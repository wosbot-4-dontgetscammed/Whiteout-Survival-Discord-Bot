"""Low-level WOS gift-code claiming logic (no Cog, pure service class)."""

import aiohttp
import asyncio
import base64
import hashlib
import json
import re
from datetime import datetime

import ddddocr

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

    async def claim_giftcode_rewards_wos(self, player_id, giftcode):
        try:
            conn = DatabaseManager.instance().get("giftcode")

            if player_id != WOS_TEST_PLAYER_ID:
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
                    async with session.post(self.wos_giftcode_url, headers=self._api_headers, data=data) as response:
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
                elif err_code == 40019:
                    status = "RATE_LIMITED"      # per-FID throttle (retries exhausted)
                elif err_code == 40001:
                    status = "ROLE_NOT_EXIST"    # legacy captcha flow only
                elif err_code == 40020:
                    # USER INFO ERROR - upstream could not resolve this fid/kid.
                    # Transient during the 2026-07 backend migration; retryable.
                    # Quiet for the periodic test-player ping (recurring noise);
                    # keep it visible for real members (may signal a wrong kid).
                    if player_id == WOS_TEST_PLAYER_ID:
                        logger.debug("USER INFO ERROR (40020) for test player %s kid=%s - upstream transient", player_id, kid)
                    else:
                        logger.warning("USER INFO ERROR (40020) for %s kid=%s - upstream transient", player_id, kid)
                    # Distinct status so the retry loop can track persistently
                    # unresolvable members (wrong kid / gone) and flag them.
                    status = "USER_INFO_ERROR"
                else:
                    # Surface the real API reason instead of a bare "ERROR"
                    # (e.g. captcha errors, unknown codes) so failure reports
                    # are diagnostic. Format: ERROR_<code>_<MSG>.
                    msg_slug = re.sub(r'[^A-Z0-9]+', '_', (msg or 'UNKNOWN').upper()).strip('_')
                    status = f"ERROR_{err_code}_{msg_slug}" if err_code else f"ERROR_{msg_slug}"

                if player_id != WOS_TEST_PLAYER_ID and status in ["SUCCESS", "RECEIVED", "SAME TYPE EXCHANGE", "STOVE_LV_ERROR"]:
                    try:
                        conn.execute("""
                            INSERT OR REPLACE INTO user_giftcodes (fid, giftcode, status)
                            VALUES (?, ?, ?)
                        """, (player_id, giftcode, status))
                        conn.commit()
                        logger.info("DATABASE - Updated: User %s, Code %s, Status %s", player_id, giftcode, status)
                    except Exception as e:
                        logger.error("DATABASE ERROR: %s", e, exc_info=True)

                return status

        except Exception as e:
            logger.error("ERROR in claim_giftcode_rewards_wos: %s", e, exc_info=True)
            return f"ERROR_{type(e).__name__}"
