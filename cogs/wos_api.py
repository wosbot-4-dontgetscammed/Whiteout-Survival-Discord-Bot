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


async def fetch_player_info(fid, *, proxy: str | None = None):
    """Fetch player info from the WOS API.

    Returns:
        dict  – parsed JSON on success (status 200 with valid data)
        int   – HTTP status code on non-200 responses (e.g. 429)
        None  – on network / parsing errors
    """
    current_time = int(time.time() * 1000)
    form = _sign_form(f"fid={fid}&time={current_time}")
    headers = {**WOS_API_HEADERS}

    try:
        if proxy:
            connector = ProxyConnector.from_url(proxy)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                    WOS_PLAYER_INFO_URL, headers=headers, data=form, ssl=False
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    return response.status
        else:
            async with aiohttp.ClientSession(connector=_get_connector(), connector_owner=False) as session:
                async with session.post(
                    WOS_PLAYER_INFO_URL, headers=headers, data=form
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    return response.status
    except Exception as e:
        logger.debug("fetch_player_info fid=%s error: %s", fid, e)
        return None


# Gift-code error codes used by the kingdom oracle
_KID_MISMATCH = 40020   # "USER INFO ERROR" - fid+kid does not resolve (wrong kingdom)
_THROTTLED = 40019      # per-fid rate limit


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

            err_code = payload.get("err_code", 0)
            if err_code == _THROTTLED:
                await asyncio.sleep(5)
                continue  # retry same kid once
            if err_code == _KID_MISMATCH:
                break  # wrong kingdom - next candidate
            # Any other response means fid+kid was accepted -> match found.
            logger.info("resolve_kingdom fid=%s -> kid=%s (err_code=%s)", fid, kid, err_code)
            return kid

        await asyncio.sleep(per_probe_delay)

    return None
