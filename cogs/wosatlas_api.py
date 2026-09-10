"""WoS Atlas API client - replacement source for player profile data.

CenturyGame removed /api/player in 2026-07, so nickname / furnace level /
kingdom can no longer be read from the game's own API (see
project_api_change_2026_07). WoS Atlas (https://wosatlas.com) maintains its own
live index of the game world and exposes it at https://api.wosatlas.com/v1.

A free account is required for the useful fields: anonymous callers only get
nickname + kid, while a signed-in caller also gets fid, furnace level, power,
alliance tag and map coordinates.

Lookup is by Chief ID: the search endpoint treats an all-digit ``playerName``
as an ID and answers with ``idOutcome: MATCHED``.

Rate limiting: their robots.txt asks for ``Crawl-delay: 1`` and their terms
forbid automation "in a way that degrades it for others", so every request is
serialised behind a lock with MIN_INTERVAL seconds between calls, and results
are cached for CACHE_TTL so a re-run inside one interval costs nothing.
"""

import asyncio
import time

import aiohttp

from .api_queue import atlas_queue
from .config import WOSATLAS_BASE_URL, WOSATLAS_EMAIL, WOSATLAS_PASSWORD, WOSATLAS_USER_AGENT
from .log_config import get_logger

logger = get_logger("wosatlas")

MIN_INTERVAL = 1.1      # seconds between requests (their robots.txt: Crawl-delay 1)
CACHE_TTL = 30 * 60     # seconds a player snapshot stays fresh
NEGATIVE_TTL = 5 * 60   # shorter reuse for a failed lookup, so a rate-limited
                        # or briefly missing member is retried soon
REQUEST_TIMEOUT = 20
MAX_RETRIES = 2         # attempts after a 429 before giving up on a member
LOW_QUOTA = 5           # remaining requests at which we wait for the window reset

# The API advertises its budget in X-RateLimit-* headers (currently 120 requests
# per 60s window). Exceeding it returns 429 - which must never be mistaken for
# "player not found", or members would silently be treated as unresolvable.


async def confirm_kid(fid, atlas_kid, stored_kid):
    """Confirm a kingdom change against the game itself before storing it.

    Atlas is an index, not the authority: a wrong `kid` makes every later gift
    redemption fail with 40020. The game's own API is asked which kingdom it
    accepts for this Chief ID; only a confirmed answer is written, otherwise the
    stored value stays.
    """
    from .wos_api import resolve_kingdom

    if not atlas_kid or str(atlas_kid) == str(stored_kid or ""):
        return stored_kid

    candidates = [str(atlas_kid)]
    if stored_kid:
        candidates.append(str(stored_kid))

    verdict = await resolve_kingdom(fid, candidates)
    if verdict is None:
        logger.info(
            "kid change for %s (%s -> %s) not confirmed by the game - keeping %s",
            fid, stored_kid, atlas_kid, stored_kid,
        )
        return stored_kid
    if str(verdict) != str(atlas_kid):
        logger.warning(
            "Atlas reported kid=%s for %s but the game says %s - using the game",
            atlas_kid, fid, verdict,
        )
    return verdict


def is_placeholder_name(name, fid) -> bool:
    """True if `name` is a generated default rather than a chosen nickname.

    Players who never set a name show up as the bare Chief ID or as "Lord<fid>"
    (the game's own default). Atlas reports those verbatim, so they must never
    overwrite a real nickname we already know — that would silently downgrade
    the roster.
    """
    if not name:
        return True
    plain = str(name).replace("\xa0", " ").strip().lower()
    digits = str(fid).strip()
    return plain in (digits, f"lord{digits}", f"lord {digits}")


def available() -> bool:
    """True if credentials are configured."""
    return bool(WOSATLAS_EMAIL and WOSATLAS_PASSWORD)


class WosAtlasClient:
    """Small authenticated client for the WoS Atlas v1 API."""

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._logged_in = False
        self._lock = asyncio.Lock()
        self._cache: dict[int, tuple[float, dict | None]] = {}

    # -- plumbing ---------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "accept": "application/json",
                    "origin": "https://wosatlas.com",
                    "referer": "https://wosatlas.com/",
                    "user-agent": WOSATLAS_USER_AGENT,
                },
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                cookie_jar=aiohttp.CookieJar(),
            )
            self._logged_in = False
        return self._session

    # Pacing lives in the shared queue (see cogs/api_queue.py) so that the
    # control loop, the slash commands and the gift-code state repair cannot
    # add up to more than one client's worth of traffic.

    async def _login(self) -> bool:
        if not available():
            return False
        session = await self._get_session()
        async with atlas_queue().slot():
            return await self._do_login(session)

    async def _do_login(self, session) -> bool:
        try:
            async with session.post(
                f"{WOSATLAS_BASE_URL}/auth/login",
                json={"email": WOSATLAS_EMAIL, "password": WOSATLAS_PASSWORD},
            ) as response:
                if response.status == 200:
                    self._logged_in = True
                    logger.info("logged in to WoS Atlas")
                    return True
                logger.warning("WoS Atlas login failed: HTTP %s", response.status)
        except Exception as e:
            logger.warning("WoS Atlas login error: %s", e)
        self._logged_in = False
        return False

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # -- public API -------------------------------------------------------

    async def lookup_fid(self, fid, *, use_cache: bool = True) -> dict | None:
        """Return a player snapshot for a Chief ID, or None if unavailable.

        Snapshot keys: fid, nickname, furnace_lv, kid, power, alliance_abbr,
        x, y, observed_at.
        """
        if not available():
            return None

        try:
            fid = int(fid)
        except (TypeError, ValueError):
            return None

        cached = self._cache.get(fid)
        if use_cache and cached:
            ttl = CACHE_TTL if cached[1] else NEGATIVE_TTL
            if time.monotonic() - cached[0] < ttl:
                return cached[1]

        async with self._lock:
            snapshot, authoritative = await self._lookup_locked(fid)
        # A transient failure must not be cached as "this player has no record",
        # or every other caller sees the gap until the TTL expires.
        if authoritative:
            self._cache[fid] = (time.monotonic(), snapshot)
        return snapshot

    async def _lookup_locked(self, fid: int) -> tuple[dict | None, bool]:
        """Return (snapshot, authoritative).

        ``authoritative`` is False when the lookup failed for a reason that says
        nothing about the player (login failure, rate limit, network error), so
        the result must not be cached.
        """
        if not self._logged_in and not await self._login():
            return None, False

        payload = await self._search(fid)
        if payload is None:  # session expired - log in once and retry
            self._logged_in = False
            if not await self._login():
                return None, False
            payload = await self._search(fid)

        if payload is None:
            return None, False
        if payload == {}:  # non-200 / network / parse failure
            return None, False

        for player in payload.get("players") or []:
            if str(player.get("fid")) == str(fid):
                return self._normalise(player), True
        return None, True  # answered cleanly: this Chief ID is not indexed

    async def _search(self, fid: int) -> dict | None:
        """One search request.

        Returns the JSON body, ``None`` if unauthorised (caller re-logs in), or
        ``{}`` when the lookup failed for any other reason. A 429 is retried
        with the server's own Retry-After before giving up.
        """
        session = await self._get_session()

        for attempt in range(MAX_RETRIES + 1):
            try:
                async with atlas_queue().slot(), session.get(
                    f"{WOSATLAS_BASE_URL}/players/search", params={"playerName": str(fid)}
                ) as response:
                    if response.status in (401, 403):
                        return None

                    if response.status == 429:
                        wait = self._retry_after(response)
                        atlas_queue().pause(wait, "HTTP 429")
                        if attempt >= MAX_RETRIES:
                            logger.warning(
                                "WoS Atlas rate limit hit for fid=%s - giving up on this member",
                                fid,
                            )
                            return {}
                        logger.info(
                            "WoS Atlas rate limited - waiting %.1fs before retrying fid=%s",
                            wait, fid,
                        )
                        await asyncio.sleep(wait)
                        continue

                    if response.status != 200:
                        logger.warning("WoS Atlas search fid=%s -> HTTP %s", fid, response.status)
                        return {}

                    self._note_quota(response)
                    return await response.json(content_type=None)
            except Exception as e:
                logger.warning("WoS Atlas search fid=%s error: %s", fid, e)
                return {}

        return {}

    async def _search_name(self, name: str) -> dict | None:
        """Search by in-game name instead of Chief ID (diagnostics/recovery)."""
        session = await self._get_session()
        if not self._logged_in and not await self._login():
            return None
        try:
            async with atlas_queue().slot(), session.get(
                f"{WOSATLAS_BASE_URL}/players/search", params={"playerName": name}
            ) as response:
                if response.status != 200:
                    logger.warning("WoS Atlas name search %r -> HTTP %s", name, response.status)
                    return {}
                self._note_quota(response)
                return await response.json(content_type=None)
        except Exception as e:
            logger.warning("WoS Atlas name search %r error: %s", name, e)
            return {}

    @staticmethod
    def _retry_after(response) -> float:
        """Seconds to wait after a 429, from Retry-After or the reset stamp."""
        header = response.headers.get("Retry-After")
        if header:
            try:
                return max(1.0, min(float(header), 120.0))
            except ValueError:
                pass
        reset = response.headers.get("X-RateLimit-Reset")
        if reset:
            try:
                return max(1.0, min(float(reset) - time.time(), 120.0))
            except ValueError:
                pass
        return 10.0

    def _note_quota(self, response):
        """Pause before the budget runs out rather than earning a 429."""
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        if remaining is None:
            return
        try:
            remaining = int(remaining)
        except ValueError:
            return
        if remaining > LOW_QUOTA:
            return
        try:
            wait = max(0.0, min(float(reset) - time.time(), 120.0)) if reset else 5.0
        except ValueError:
            wait = 5.0
        if wait > 0:
            logger.info(
                "WoS Atlas quota nearly spent (%s left) - pausing %.1fs for the window reset",
                remaining, wait,
            )
            atlas_queue().pause(wait, "quota nearly spent")

    @staticmethod
    def _normalise(player: dict) -> dict:
        return {
            "fid": player.get("fid"),
            "nickname": (player.get("nick_name") or "").strip(),
            "furnace_lv": player.get("stove_lv") or player.get("furnace_lv"),
            "kid": player.get("kid"),
            "power": player.get("current_power"),
            "alliance_abbr": player.get("abbr"),
            "x": player.get("x"),
            "y": player.get("y"),
            "observed_at": player.get("observed_at"),
        }


_client: WosAtlasClient | None = None


def get_client() -> WosAtlasClient:
    """Return the shared client instance."""
    global _client
    if _client is None:
        _client = WosAtlasClient()
    return _client


async def lookup_fid(fid, *, use_cache: bool = True) -> dict | None:
    """Convenience wrapper around the shared client."""
    return await get_client().lookup_fid(fid, use_cache=use_cache)
