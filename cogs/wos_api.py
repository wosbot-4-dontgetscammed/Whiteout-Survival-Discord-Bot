"""Centralized WOS API client.

Replaces duplicated fetch logic across control.py, w.py, id_channel.py,
alliance_member_operations.py and gift_operations.py.
"""

import asyncio
import hashlib
import random
import string
import time
import aiohttp
import ssl
from aiohttp_socks import ProxyConnector

from .api_queue import game_queue
from .config import (
    WOS_ENCRYPT_KEY,
    WOS_PLAYER_INFO_URL,
    WOS_GIFTCODE_URL,
    WOS_GIFTCODE_REDEMPTION_URL,
    WOS_API_HEADERS,
)
from .log_config import get_logger

logger = get_logger("wos_api")

# Reusable SSL context that skips verification (matches legacy behaviour)
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


# --- player-info circuit breaker -------------------------------------------
# The game removed /api/player in 2026-07. Once we see it 404, stop sending
# requests for a while: every caller gets the 404 back instantly instead of
# waiting on a doomed round-trip. control.py re-probes with force=True.
PLAYER_API_BACKOFF_SECONDS = 6 * 3600
_player_api_down_until = 0.0


def player_api_is_down() -> bool:
    """True while the player-info endpoint is in its failure backoff."""
    return time.monotonic() < _player_api_down_until


def _sign_form(form: str) -> str:
    """Append MD5 signature expected by the CenturyGame API."""
    sign = hashlib.md5((form + WOS_ENCRYPT_KEY).encode("utf-8")).hexdigest()
    return f"sign={sign}&{form}"


# Module-level connector reuse (avoids creating a new connector per call)
_connector: aiohttp.TCPConnector | None = None


def _get_connector() -> aiohttp.TCPConnector:
    """Return a reusable TCPConnector, creating one if needed."""
    global _connector
    if _connector is None or _connector.closed:
        _connector = aiohttp.TCPConnector(ssl=_ssl_ctx)
    return _connector


async def fetch_player_info(fid, *, proxy: str | None = None, force: bool = False):
    """Fetch player info from the WOS API.

    Returns:
        dict  – parsed JSON on success (status 200 with valid data)
        int   – HTTP status code on non-200 responses (e.g. 429, 404)
        None  – on network / parsing errors

    While the endpoint is in its failure backoff, 404 is returned without a
    request. Pass force=True to bypass the backoff and actually re-probe.
    """
    global _player_api_down_until

    if not force and player_api_is_down():
        return 404

    current_time = int(time.time() * 1000)
    form = _sign_form(f"fid={fid}&time={current_time}")
    headers = {**WOS_API_HEADERS}

    try:
        async with game_queue().slot():
            return await _do_fetch_player_info(form, headers, proxy)
    except Exception as e:
        logger.debug("fetch_player_info fid=%s error: %s", fid, e)
        return None


async def _do_fetch_player_info(form, headers, proxy):
    try:
        if proxy:
            connector = ProxyConnector.from_url(proxy)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                    WOS_PLAYER_INFO_URL, headers=headers, data=form, ssl=False
                ) as response:
                    return _record_player_api_status(response.status, await _read_json(response))
        else:
            async with aiohttp.ClientSession(connector=_get_connector(), connector_owner=False) as session:
                async with session.post(
                    WOS_PLAYER_INFO_URL, headers=headers, data=form
                ) as response:
                    return _record_player_api_status(response.status, await _read_json(response))
    except Exception as e:
        logger.debug("fetch_player_info fid=%s error: %s", fid, e)
        return None


async def _read_json(response):
    """Parse a JSON body, tolerating error pages that are not valid JSON."""
    if response.status != 200:
        return None
    try:
        return await response.json(content_type=None)
    except Exception:
        return None


def _record_player_api_status(status: int, payload):
    """Open or close the circuit breaker based on one response."""
    global _player_api_down_until
    if status == 200:
        if payload is None:
            # 200 with an unparseable body is a broken response, not data, and
            # not evidence the endpoint works: report it as a plain error.
            logger.warning("player-info API returned 200 with an unreadable body")
            return None
        if _player_api_down_until:
            logger.info("player-info API responded again - clearing backoff")
        _player_api_down_until = 0.0
        return payload
    if status in (403, 404, 410):
        if not player_api_is_down():
            logger.info(
                "player-info API returned %s - backing off for %d hours",
                status, PLAYER_API_BACKOFF_SECONDS // 3600,
            )
        _player_api_down_until = time.monotonic() + PLAYER_API_BACKOFF_SECONDS
    return status


# Gift-code error codes used by the kingdom oracle
_KID_MISMATCH = 40020   # "USER INFO ERROR" - fid+kid does not resolve (wrong kingdom)
_THROTTLED = 40019      # per-fid rate limit

# Upstream only evaluates the code AFTER it has accepted fid+kid, so each of
# these proves the kingdom is right. Anything outside this set (a blocked
# request, an HTML error page, an empty body) proves nothing: treating it as a
# match used to write a wrong kingdom into the database as fact, which then
# broke every later redemption for that member.
_KID_CONFIRMED = frozenset({
    20000,  # SUCCESS
    40005,  # USED (usage limit reached)
    40006,  # STOVE_LV ERROR (furnace too low)
    40007,  # TIME ERROR (expired)
    40008,  # RECEIVED (already redeemed)
    40010,  # SPEND MORE
    40011,  # SAME TYPE EXCHANGE
    40014,  # CDK NOT FOUND  <- the expected answer for our bogus probe code
    40017,  # RECHARGE MONEY ERROR
    40018,  # RECHARGE MONEY VIP ERROR
})


async def resolve_kingdom(fid, candidate_kids, *, per_probe_delay: float = 2.2):
    """Detect a player's kingdom id (kid) via the gift-code oracle.

    Since 2026-07 the /api/player endpoint that used to return a player's
    kingdom was removed. The surviving /api/gift_code endpoint validates
    fid+kid BEFORE the code, so we can probe: submit an intentionally
    invalid gift code with a candidate kid and read the error code.

      err_code 40020 (USER INFO ERROR) -> wrong kingdom, try next
      anything else (40014 CDK NOT FOUND, etc.) -> fid+kid matched

    The probe is side-effect-free: the bogus code can never redeem, so no
    reward is ever consumed. Candidates are tried in order (put the most
    likely kingdom first). Returns the matching kid as a string, or None.
    """
    bogus = "ZZ" + "".join(random.choices(string.ascii_uppercase + string.digits, k=14))
    headers = {
        "accept": "application/json",
        "origin": WOS_GIFTCODE_REDEMPTION_URL,
        **WOS_API_HEADERS,
    }
    tried = set()
    for kid in candidate_kids:
        if kid in (None, ""):
            continue
        kid = str(kid).strip()
        if not kid or kid in tried:
            continue
        tried.add(kid)

        for attempt in range(2):  # one retry on throttle
            current_time = int(time.time())
            form = _sign_form(f"cdk={bogus}&fid={fid}&kid={kid}&time={current_time}")
            try:
                async with game_queue().slot():
                    async with aiohttp.ClientSession(
                        connector=_get_connector(), connector_owner=False
                    ) as session:
                        async with session.post(
                            WOS_GIFTCODE_URL, headers=headers, data=form
                        ) as response:
                            payload = await response.json(content_type=None)
            except Exception as e:
                logger.debug("resolve_kingdom fid=%s kid=%s error: %s", fid, kid, e)
                break  # network error - give up on this candidate

            if not isinstance(payload, dict):
                logger.debug(
                    "resolve_kingdom fid=%s kid=%s: unreadable response - inconclusive", fid, kid
                )
                break  # cannot judge this candidate - do not claim a match

            err_code = payload.get("err_code")
            if err_code == _THROTTLED:
                game_queue().pause(5, "40019 per-FID throttle")
                await asyncio.sleep(5)
                continue  # retry same kid once
            if err_code == _KID_MISMATCH:
                break  # wrong kingdom - next candidate
            if err_code in _KID_CONFIRMED:
                logger.info("resolve_kingdom fid=%s -> kid=%s (err_code=%s)", fid, kid, err_code)
                return kid
            # Anything else says nothing about the kingdom - stay silent rather
            # than guessing.
            logger.debug(
                "resolve_kingdom fid=%s kid=%s: inconclusive response (err_code=%r, msg=%r)",
                fid, kid, err_code, payload.get("msg"),
            )
            break

        await asyncio.sleep(per_probe_delay)

    return None
