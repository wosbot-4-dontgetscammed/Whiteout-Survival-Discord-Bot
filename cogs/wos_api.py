"""Centralized WOS API client.

Replaces duplicated fetch logic across control.py, w.py, id_channel.py,
alliance_member_operations.py and gift_operations.py.
"""

import hashlib
import time
import aiohttp
import ssl
from aiohttp_socks import ProxyConnector

from .config import WOS_ENCRYPT_KEY, WOS_PLAYER_INFO_URL, WOS_API_HEADERS
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
