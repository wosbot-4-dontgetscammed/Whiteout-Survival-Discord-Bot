"""Low-level WOS gift-code claiming logic (no Cog, pure service class)."""

import aiohttp
import asyncio
import base64
import hashlib
import json
import re
from datetime import datetime

import ddddocr

from .api_queue import game_queue
from .config import (
    WOS_ENCRYPT_KEY,
    WOS_PLAYER_INFO_URL,
    WOS_GIFTCODE_URL,
    WOS_CAPTCHA_URL,
    WOS_GIFTCODE_REDEMPTION_URL,
    WOS_TEST_PLAYER_ID,
    WOS_API_HEADERS,
)
from .database import DatabaseManager
from .log_config import get_logger
from .wos_api import _ssl_ctx as _shared_ssl_ctx

logger = get_logger("gift_api")

# Statuses that are final for the moment but can flip later: the player does
# not (yet) meet the code's recharge/VIP requirement. They are cached like any
# other terminal status so the retry loop stops hammering the API for them —
# the retry loop re-checks them once a day (see RECHECK_AFTER_HOURS).
RECHECK_STATUSES = ("RECHARGE_REQUIRED", "VIP_REQUIRED")
RECHECK_AFTER_HOURS = 24

# Statuses worth remembering per (fid, code); anything else stays retryable.
PERSISTED_STATUSES = ("SUCCESS", "RECEIVED", "SAME TYPE EXCHANGE",
                      "STOVE_LV_ERROR", *RECHECK_STATUSES)


class GiftCodeClaimer:
    """Handles captcha solving and gift-code redemption against the WOS API."""

    def __init__(self):
        self.ocr = ddddocr.DdddOcr(show_ad=False)

        self.wos_player_info_url = WOS_PLAYER_INFO_URL
        self.wos_giftcode_url = WOS_GIFTCODE_URL
        self.wos_captcha_url = WOS_CAPTCHA_URL
        self.wos_giftcode_redemption_url = WOS_GIFTCODE_REDEMPTION_URL
        self.wos_encrypt_key = WOS_ENCRYPT_KEY

        self._api_headers = {
            "accept": "application/json, text/plain, */*",
            "origin": self.wos_giftcode_redemption_url,
            **WOS_API_HEADERS,
        }

        self._connector: aiohttp.TCPConnector | None = None

        # FIDs whose stored kingdom was already re-probed after a USER INFO
        # ERROR this process — one repair attempt each, so a dead FID cannot
        # turn into an endless probe loop.
        self._kid_repair_attempted: set = set()

    # ------------------------------------------------------------------
    # Connector reuse
    # ------------------------------------------------------------------

    def _get_connector(self) -> aiohttp.TCPConnector:
        if self._connector is None or self._connector.closed:
            self._connector = aiohttp.TCPConnector(ssl=_shared_ssl_ctx)
        return self._connector

    async def close(self):
        try:
            if self._connector and not self._connector.closed:
                await self._connector.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def encode_data(self, data: dict) -> dict:
        secret = self.wos_encrypt_key
        sorted_keys = sorted(data.keys())
        encoded_data = "&".join(
            [
                f"{key}={json.dumps(data[key]) if isinstance(data[key], dict) else data[key]}"
                for key in sorted_keys
            ]
        )
        sign = hashlib.md5(f"{encoded_data}{secret}".encode()).hexdigest()
        return {"sign": sign, **data}

    # ------------------------------------------------------------------
    # Captcha
    # ------------------------------------------------------------------

    async def solve_captcha(self, fid, max_retries=3):
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(connector=self._get_connector(), connector_owner=False, timeout=timeout) as session:
            for attempt in range(max_retries):
                try:
                    data_to_encode = {
                        "fid": f"{fid}",
                        "time": f"{int(datetime.now().timestamp())}",
                        "init": "1",
                    }
                    data = self.encode_data(data_to_encode)

                    async with session.post(self.wos_captcha_url, headers=self._api_headers, data=data) as response:
                        try:
                            response_json = await response.json(content_type=None)
                        except (json.JSONDecodeError, ValueError):
                            text = await response.text()
                            logger.warning("[GiftAPI] Invalid JSON from captcha API: %s", text[:200])
                            response_json = None

                    if response_json is None:
                        await asyncio.sleep(3)
                        continue

                    logger.debug("CAPTCHA RESPONSE (attempt %d): %s", attempt + 1, json.dumps(response_json, indent=2, default=str))

                    err_code = response_json.get("err_code", 0)
                    if err_code in (40100, 40101):
                        await asyncio.sleep(3)
                        continue

                    raw_data = response_json.get("data", "")
                    if isinstance(raw_data, dict):
                        img_base64 = raw_data.get("img", "") or raw_data.get("image", "") or raw_data.get("captcha", "")
                    else:
                        img_base64 = raw_data

                    if not img_base64:
                        await asyncio.sleep(2)
                        continue

                    if isinstance(img_base64, str) and "," in img_base64:
                        img_base64 = img_base64.split(",", 1)[1]

                    img_bytes = base64.b64decode(img_base64)
                    captcha_text = await asyncio.to_thread(self.ocr.classification, img_bytes)
                    return captcha_text

                except Exception as e:
                    logger.error("CAPTCHA ERROR (attempt %d): %s", attempt + 1, e, exc_info=True)
                    await asyncio.sleep(2)

        return None

    # ------------------------------------------------------------------
    # Player info
    # ------------------------------------------------------------------

    async def get_stove_info_wos(self, player_id):
        """Fetch player info and return parsed JSON (or None on failure)."""
        data_to_encode = {
            "fid": f"{player_id}",
            "time": f"{int(datetime.now().timestamp())}",
        }
        data = self.encode_data(data_to_encode)

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(connector=self._get_connector(), connector_owner=False, timeout=timeout) as session:
            async with session.post(self.wos_player_info_url, headers=self._api_headers, data=data) as response:
                try:
                    return await response.json(content_type=None)
                except (json.JSONDecodeError, ValueError):
                    text = await response.text()
                    logger.warning("[GiftAPI] Invalid JSON from player info API: %s", text[:200])
                    return None

    # ------------------------------------------------------------------
    # Gift-code redemption
    # ------------------------------------------------------------------

    async def _repair_kid(self, player_id, current_kid):
        """Re-detect a member's kingdom after a USER INFO ERROR (40020).

        Probes the member's alliance regions with the gift-code oracle and
        stores the kingdom that the API accepts. Returns the confirmed kid
        (unchanged value = the stored region is fine, so the 40020 really was
        transient) or None when nothing matched / already attempted.
        """
        if player_id == WOS_TEST_PLAYER_ID or player_id in self._kid_repair_attempted:
            return None
        self._kid_repair_attempted.add(player_id)
        try:
            # Fast path: WoS Atlas knows the member's current state outright, so
            # ask it before falling back to probing kingdoms one by one.
            from . import wosatlas_api
            if wosatlas_api.available():
                snapshot = await wosatlas_api.lookup_fid(player_id, use_cache=False)
                atlas_kid = snapshot.get("kid") if snapshot else None
                if atlas_kid:
                    if str(atlas_kid) != str(current_kid or ""):
                        users_db = DatabaseManager.instance().get("users")
                        users_db.execute(
                            "UPDATE users SET kid = ? WHERE fid = ?", (atlas_kid, player_id)
                        )
                        users_db.commit()
                        logger.info(
                            "[GiftAPI] state repaired via WoS Atlas for %s: %s -> %s",
                            player_id, current_kid, atlas_kid,
                        )
                    return atlas_kid

            row = DatabaseManager.instance().get("users").execute(
                "SELECT alliance FROM users WHERE fid = ?", (player_id,)).fetchone()
            if not row or row[0] in (None, ""):
                return None
            from .regions import verify_member_kid
            kid, _changed = await verify_member_kid(player_id, row[0], stored=current_kid)
            return kid
        except Exception as e:
            logger.warning("[GiftAPI] region repair failed for %s: %s", player_id, e)
            return None

    async def claim_giftcode_rewards_wos(self, player_id, giftcode, *, force=False):
        """Redeem `giftcode` for `player_id`.

        `force=True` ignores the cached result for this (fid, code) and asks
        upstream again — used by the daily re-check of members who did not meet
        a code's recharge/VIP requirement earlier.
        """
        try:
            conn = DatabaseManager.instance().get("giftcode")

            if player_id != WOS_TEST_PLAYER_ID and not force:
                cursor = conn.execute("""
                    SELECT status FROM user_giftcodes
                    WHERE fid = ? AND giftcode = ?
                """, (player_id, giftcode))

                existing_record = cursor.fetchone()
                if existing_record:
                    logger.debug("CACHE HIT - User %s already processed with status: %s", player_id, existing_record[0])
                    return existing_record[0]

            # --- 2026-07 CenturyGame API change -------------------------------
            # The standalone player-info endpoint (/api/player) and the captcha
            # endpoint (/api/captcha) were removed server-side. The web redeemer
            # now performs a SINGLE POST /api/gift_code that carries the player's
            # kingdom id (kid); there is no separate player pre-lookup and no
            # captcha step. We reproduce that exact request here.
            #
            # kid is required and comes from our stored users table (populated
            # when the member was added). Without it the API returns
            # "USER INFO ERROR" (err_code 40020).
            kid = None
            try:
                urow = DatabaseManager.instance().get("users").execute(
                    "SELECT kid FROM users WHERE fid = ?", (player_id,)
                ).fetchone()
                if urow:
                    kid = urow[0]
            except Exception as e:
                logger.warning("Could not read kid for %s: %s", player_id, e)

            if not kid and player_id == WOS_TEST_PLAYER_ID:
                kid = 1587  # test player's kingdom

            if not kid:
                logger.warning("No kid stored for %s - cannot redeem (kingdom id required by API)", player_id)
                return "NO_KID"

            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(connector=self._get_connector(), connector_owner=False, timeout=timeout) as session:
                response_json = None
                for attempt in range(3):
                    data = self.encode_data({
                        "fid": f"{player_id}",
                        "cdk": giftcode,
                        "kid": f"{kid}",
                        "time": f"{int(datetime.now().timestamp())}",
                    })
                    async with game_queue().slot(), session.post(
                        self.wos_giftcode_url, headers=self._api_headers, data=data
                    ) as response:
                        try:
                            response_json = await response.json(content_type=None)
                        except (json.JSONDecodeError, ValueError):
                            text = await response.text()
                            logger.warning("[GiftAPI] Invalid JSON from gift code API: %s", text[:200])
                            return "ERROR_INVALID_JSON"

                    # 40019 = CenturyGame's per-FID throttle (this player redeemed
                    # too frequently). Transient — wait 5s and retry. Must NEVER be
                    # conflated with 40020/KID_MISMATCH.
                    if response_json.get("err_code") == 40019 and attempt < 2:
                        logger.debug("[GiftAPI] per-FID throttle (40019) for %s - waiting 5s", player_id)
                        # Slow every feature down, not just this redemption.
                        game_queue().pause(5, "40019 per-FID throttle")
                        await asyncio.sleep(5)
                        continue
                    break

                logger.debug("API REQUEST - Gift Code | Player ID: %s | kid: %s | Gift Code: %s | Response: %s",
                             player_id, kid, giftcode, json.dumps(response_json, indent=2))

                err_code = response_json.get("err_code", 0)
                msg = response_json.get("msg")

                if msg == "SUCCESS":
                    status = "SUCCESS"
                elif msg == "RECEIVED." and err_code == 40008:
                    status = "RECEIVED"
                elif msg == "CDK NOT FOUND." and err_code == 40014:
                    status = "CDK_NOT_FOUND"
                elif msg == "SAME TYPE EXCHANGE." and err_code == 40011:
                    status = "SAME TYPE EXCHANGE"
                elif msg == "TIME ERROR." and err_code == 40007:
                    status = "TIME_ERROR"
                elif msg == "TIMEOUT RETRY." and err_code == 40004:
                    status = "TIMEOUT_RETRY"
                elif msg == "USED." and err_code == 40005:
                    status = "USAGE_LIMIT"
                elif msg == "STOVE_LV ERROR." and err_code == 40006:
                    status = "STOVE_LV_ERROR"
                elif err_code == 40010:
                    status = "SPEND_MORE"        # player level/spend too low for this code
                elif err_code == 40017:
                    # "RECHARGE MONEY ERROR." - the code is tied to a purchase
                    # the player has not made. Nothing the bot can do now, but
                    # it can change once the player recharges.
                    status = "RECHARGE_REQUIRED"
                elif err_code == 40018:
                    status = "VIP_REQUIRED"      # "RECHARGE MONEY VIP ERROR."
                elif err_code == 40019:
                    status = "RATE_LIMITED"      # per-FID throttle (retries exhausted)
                elif err_code == 40001:
                    status = "ROLE_NOT_EXIST"    # legacy captcha flow only
                elif err_code == 40020:
                    # USER INFO ERROR - upstream could not resolve this fid+kid.
                    # Two causes: a transient backend hiccup, or a stale kingdom
                    # (the player transferred states), which would otherwise fail
                    # every future redemption for him. Re-probe the alliance's
                    # regions once: if another kingdom answers, it is stored and
                    # the redemption is retried with it.
                    if player_id == WOS_TEST_PLAYER_ID:
                        logger.debug("USER INFO ERROR (40020) for test player %s kid=%s - upstream transient", player_id, kid)
                    elif player_id in self._kid_repair_attempted:
                        # Already probed this run and nothing resolved: keep the
                        # log readable instead of repeating the same warning for
                        # every code in every cycle (this used to fill the log
                        # with hundreds of identical lines for one member).
                        logger.debug(
                            "USER INFO ERROR (40020) for %s kid=%s - already probed, still unresolved",
                            player_id, kid,
                        )
                        repaired = None
                    else:
                        logger.warning("USER INFO ERROR (40020) for %s kid=%s - re-checking region", player_id, kid)
                        repaired = await self._repair_kid(player_id, kid)
                        if repaired and str(repaired) != str(kid):
                            logger.info("[GiftAPI] region repaired for %s: %s -> %s, retrying redemption",
                                        player_id, kid, repaired)
                            return await self.claim_giftcode_rewards_wos(player_id, giftcode)
                    # Distinct status so the retry loop can track persistently
                    # unresolvable members (wrong kid / gone) and flag them.
                    status = "USER_INFO_ERROR"
                else:
                    # Surface the real API reason instead of a bare "ERROR"
                    # (e.g. captcha errors, unknown codes) so failure reports
                    # are diagnostic. Format: ERROR_<code>_<MSG>.
                    # `msg` is whatever upstream sent - occasionally a number,
                    # which used to crash here with "'int' object has no
                    # attribute 'upper'" and abort the whole redemption.
                    msg_slug = re.sub(
                        r'[^A-Z0-9]+', '_', str(msg or 'UNKNOWN').upper()
                    ).strip('_')
                    status = f"ERROR_{err_code}_{msg_slug}" if err_code else f"ERROR_{msg_slug}"

                if player_id != WOS_TEST_PLAYER_ID and status in PERSISTED_STATUSES:
                    try:
                        conn.execute("""
                            INSERT OR REPLACE INTO user_giftcodes (fid, giftcode, status, updated_at)
                            VALUES (?, ?, ?, ?)
                        """, (player_id, giftcode, status,
                              datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                        conn.commit()
                        logger.info("DATABASE - Updated: User %s, Code %s, Status %s", player_id, giftcode, status)
                    except Exception as e:
                        logger.error("DATABASE ERROR: %s", e, exc_info=True)

                return status

        except Exception as e:
            logger.error("ERROR in claim_giftcode_rewards_wos: %s", e, exc_info=True)
            return f"ERROR_{type(e).__name__}"
