import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiohttp
import asyncio
import re
import json
import ssl
from datetime import datetime
from .config import WOS_TEST_PLAYER_ID
from .database import DatabaseManager
from .utils import _create_monitored_task, check_admin, get_global_admin_ids, send_error, send_success
from .log_config import get_logger

logger = get_logger("gift_scraper")


COMMON_WORDS = {
    "about", "after", "again", "along", "also", "other", "always", "being",
    "below", "between", "bonus", "break", "build", "check", "claim", "class",
    "click", "close", "codes", "could", "daily", "enter", "error", "event",
    "every", "extra", "first", "found", "frost", "games", "gifts", "great",
    "guide", "happy", "house", "human", "items", "known", "later", "leave",
    "level", "light", "limit", "links", "login", "lucky", "march", "might",
    "month", "never", "night", "notes", "offer", "order", "other", "party",
    "patch", "place", "plain", "plant", "plays", "point", "power", "prize",
    "quest", "quick", "realm", "redeem", "refer", "reply", "reset", "right",
    "round", "royal", "rules", "saved", "share", "shell", "short", "since",
    "skill", "small", "snowy", "solve", "sorry", "south", "speed", "staff",
    "stage", "start", "state", "still", "stone", "store", "storm", "story",
    "super", "table", "thank", "their", "there", "these", "thing", "those",
    "three", "times", "today", "token", "total", "tower", "trade", "trial",
    "under", "unite", "until", "using", "valid", "value", "video", "watch",
    "water", "which", "while", "white", "whole", "world", "would", "write",
    "active", "battle", "before", "broken", "button", "cancel", "change",
    "copied", "create", "delete", "detail", "double", "easily", "effect",
    "enable", "energy", "enough", "expire", "finish", "follow", "frozen",
    "future", "gaming", "global", "golden", "island", "launch", "latest",
    "listed", "manage", "master", "mobile", "normal", "number", "obtain",
    "online", "option", "origin", "output", "player", "please", "policy",
    "random", "rating", "reason", "record", "reduce", "region", "reload",
    "remove", "render", "repair", "repeat", "report", "result", "return",
    "reveal", "review", "reward", "scroll", "search", "second", "select",
    "server", "shield", "simple", "single", "social", "source", "spring",
    "status", "strong", "submit", "summer", "supply", "system", "target",
    "toggle", "troops", "unique", "update", "useful", "weekly", "whiteout",
    "winter", "survival", "expired", "invalid", "giftcode", "facebook",
    "youtube", "twitter", "discord", "instagram", "android", "working",
    "updated", "january", "february", "april", "august", "september",
    "october", "november", "december", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday", "reddit", "comment",
    "removed", "deleted", "content", "website", "article", "special",
    "limited", "premium", "download", "account", "setting", "general",
    "release", "version", "chapter", "million", "billion", "hundred",
    "thousand", "nothing", "already", "because", "however", "without",
    "through", "another", "between", "against", "available", "community",
    "resource", "exchange", "official", "continue", "complete", "progress",
    "everyone", "together", "possible", "received", "remember", "strength",
    "standard", "includes", "requires", "provided", "contains", "featured",
    "upcoming", "previous", "favorite", "bookmark", "navigate", "homepage",
}


SCRAPER_SOURCES = [
    ("wosrewards", "https://wosrewards.com", True),
    ("woswiki", "https://whiteoutsurvival.wiki/giftcodes/", True),
    ("lootbar", "https://lootbar.gg/blog/en/whiteout-survival-newest-codes.html", True),
    ("gamesradar", "https://www.gamesradar.com/games/survival/whiteout-survival-codes-gift/", True),
    ("beebom", "https://beebom.com/whiteout-survival-codes/", True),
    ("dexerto", "https://www.dexerto.com/codes/whiteout-survival-codes-3295120/", True),
    # Reddit requires OAuth for its .json API since 2023 and returns 403 to
    # unauthenticated/server requests, so it never yielded codes - disabled.
    ("reddit", "https://www.reddit.com/r/whiteoutsurvival/new.json", False),
]


class GiftScraper(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        db = DatabaseManager.instance()
        self.conn = db.get("giftcode")

        self.settings_conn = db.get("settings")

        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

        self._connector: aiohttp.TCPConnector | None = None
        self._running = False
        self._init_tables()
        self.scrape_loop.start()

    def _init_tables(self):
        self.conn.execute('''CREATE TABLE IF NOT EXISTS scraper_sources (
            name TEXT PRIMARY KEY,
            url TEXT,
            enabled INTEGER DEFAULT 1,
            last_run TEXT,
            last_error TEXT,
            codes_found_last INTEGER DEFAULT 0,
            consecutive_failures INTEGER DEFAULT 0
        )''')

        self.conn.execute('''CREATE TABLE IF NOT EXISTS scraper_code_provenance (
            giftcode TEXT,
            source TEXT,
            discovered_at TEXT,
            PRIMARY KEY (giftcode, source)
        )''')
        self.conn.commit()

        for name, url, enabled in SCRAPER_SOURCES:
            self.conn.execute(
                "INSERT OR IGNORE INTO scraper_sources (name, url, enabled) VALUES (?, ?, ?)",
                (name, url, int(enabled))
            )
        self.conn.commit()

    def _get_connector(self) -> aiohttp.TCPConnector:
        if self._connector is None or self._connector.closed:
            self._connector = aiohttp.TCPConnector(ssl=self.ssl_context)
        return self._connector

    async def cog_unload(self):
        self.scrape_loop.cancel()
        if self._connector and not self._connector.closed:
            await self._connector.close()

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    async def _fetch_html(self, url: str) -> str | None:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            async with aiohttp.ClientSession(connector=self._get_connector(), connector_owner=False) as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        return await resp.text()
                    logger.info(f"HTTP {resp.status} for {url}")
        except (aiohttp.ClientError, OSError) as e:
            logger.error(f"Connection error for {url}: {e}")
        except Exception as e:
            logger.error(f"Unexpected fetch error for {url}: {e}")
        return None

    async def _fetch_json(self, url: str) -> dict | None:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        try:
            async with aiohttp.ClientSession(connector=self._get_connector(), connector_owner=False) as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        if not text or not text.strip():
                            logger.warning(f"Empty JSON response from {url}")
                            return None
                        try:
                            return json.loads(text)
                        except (json.JSONDecodeError, ValueError):
                            logger.warning(f"Invalid JSON from {url}")
                            return None
                    logger.info(f"HTTP {resp.status} for {url}")
        except (aiohttp.ClientError, OSError) as e:
            logger.error(f"Connection error for {url}: {e}")
        except Exception as e:
            logger.error(f"Unexpected fetch error for {url}: {e}")
        return None

    # ------------------------------------------------------------------
    # Code filtering
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_candidates(html: str, patterns: list[str] | None = None) -> list[str]:
        if not html:
            return []
        if patterns is None:
            patterns = [
                r'<strong[^>]*>([A-Za-z0-9]{5,25})</strong>',
                r'<code[^>]*>([A-Za-z0-9]{5,25})</code>',
                r'data-code=["\']([A-Za-z0-9]{5,25})["\']',
                r'copy["\'][^>]*>([A-Za-z0-9]{5,25})<',
            ]
        candidates = []
        for pattern in patterns:
            candidates.extend(re.findall(pattern, html, re.IGNORECASE))
        return candidates

    @staticmethod
    def _is_likely_code(text: str) -> bool:
        if not re.fullmatch(r'[A-Za-z0-9]{5,25}', text):
            return False
        if text.isdigit():
            return False
        if text.isalpha() and text.islower():
            return False
        if text.lower() in COMMON_WORDS:
            return False
        if len(text) < 6:
            return False
        has_letter = bool(re.search(r'[A-Za-z]', text))
        has_digit = bool(re.search(r'[0-9]', text))
        if has_letter and has_digit:
            return True
        if text.isalpha() and any(c.isupper() for c in text) and any(c.islower() for c in text):
            return True
        if len(text) >= 8 and text.isupper():
            return True
        return False

    def _filter_codes(self, candidates: list[str]) -> list[str]:
        seen = set()
        result = []
        for c in candidates:
            c = c.strip()
            if c in seen:
                continue
            seen.add(c)
            if self._is_likely_code(c):
                result.append(c)
        return result

    # ------------------------------------------------------------------
    # 7 Scraper methods
    # ------------------------------------------------------------------

    async def scrape_wosrewards(self) -> list[str]:
        html = await self._fetch_html("https://wosrewards.com")
        if not html:
            return []
        patterns = [
            r'<strong[^>]*>([A-Za-z0-9]{5,25})</strong>',
            r'<b[^>]*>([A-Za-z0-9]{5,25})</b>',
            r'<span[^>]*class="[^"]*code[^"]*"[^>]*>([A-Za-z0-9]{5,25})</span>',
            r'data-code=["\']([A-Za-z0-9]{5,25})["\']',
            r'<code[^>]*>([A-Za-z0-9]{5,25})</code>',
            r'<td[^>]*>([A-Za-z0-9]{5,25})</td>',
            r'copy[^>]*>([A-Za-z0-9]{5,25})<',
        ]
        return self._filter_codes(self._extract_candidates(html, patterns))

    async def scrape_woswiki(self) -> list[str]:
        html = await self._fetch_html("https://whiteoutsurvival.wiki/giftcodes/")
        if not html:
            return []
        patterns = [
            r'<strong[^>]*>([A-Za-z0-9]{5,25})</strong>',
            r'data-code=["\']([A-Za-z0-9]{5,25})["\']',
            r'copy[^>]*>([A-Za-z0-9]{5,25})<',
            r'<code[^>]*>([A-Za-z0-9]{5,25})</code>',
            r'<button[^>]*data-clipboard-text=["\']([A-Za-z0-9]{5,25})["\']',
            r'class="[^"]*code[^"]*"[^>]*>([A-Za-z0-9]{5,25})<',
        ]
        return self._filter_codes(self._extract_candidates(html, patterns))

    async def scrape_lootbar(self) -> list[str]:
        html = await self._fetch_html("https://lootbar.gg/blog/en/whiteout-survival-newest-codes.html")
        if not html:
            return []
        patterns = [
            r'<strong[^>]*>([A-Za-z0-9]{5,25})</strong>',
            r'<b[^>]*>([A-Za-z0-9]{5,25})</b>',
        ]
        return self._filter_codes(self._extract_candidates(html, patterns))

    async def scrape_gamesradar(self) -> list[str]:
        html = await self._fetch_html("https://www.gamesradar.com/games/survival/whiteout-survival-codes-gift/")
        if not html:
            return []
        patterns = [
            r'<strong[^>]*>([A-Za-z0-9]{5,25})</strong>',
            r'<b[^>]*>([A-Za-z0-9]{5,25})</b>',
        ]
        return self._filter_codes(self._extract_candidates(html, patterns))

    async def scrape_beebom(self) -> list[str]:
        html = await self._fetch_html("https://beebom.com/whiteout-survival-codes/")
        if not html:
            return []
        patterns = [
            r'<strong[^>]*>([A-Za-z0-9]{5,25})</strong>',
            r'<li[^>]*>([A-Za-z0-9]{5,25})\s*[\u2013\u2014\-–—:]',
            r'<li[^>]*><strong>([A-Za-z0-9]{5,25})</strong>',
        ]
        return self._filter_codes(self._extract_candidates(html, patterns))

    async def scrape_dexerto(self) -> list[str]:
        html = await self._fetch_html("https://www.dexerto.com/codes/whiteout-survival-codes-3295120/")
        if not html:
            return []
        patterns = [
            r'<td[^>]*>([A-Za-z0-9]{5,25})</td>',
            r'<strong[^>]*>([A-Za-z0-9]{5,25})</strong>',
        ]
        return self._filter_codes(self._extract_candidates(html, patterns))

    async def scrape_reddit(self) -> list[str]:
        data = await self._fetch_json("https://www.reddit.com/r/whiteoutsurvival/new.json?limit=50")
        if not data:
            return []
        codes = []
        keywords = {"code", "gift", "redeem", "giftcode", "coupon", "reward"}
        try:
            posts = data.get("data", {}).get("children", [])
            for post in posts:
                post_data = post.get("data", {})
                title = (post_data.get("title") or "").lower()
                selftext = post_data.get("selftext") or ""
                if not any(kw in title for kw in keywords):
                    continue
                candidates = re.findall(r'[A-Za-z0-9]{5,25}', selftext)
                candidates += re.findall(r'[A-Za-z0-9]{5,25}', post_data.get("title", ""))
                codes.extend(candidates)
        except Exception as e:
            logger.error(f"Reddit parse error: {e}")
        return self._filter_codes(codes)

    # ------------------------------------------------------------------
    # Core processing: validate + distribute new codes
    # ------------------------------------------------------------------

    async def _process_new_codes(self, codes: list[str], source: str) -> int:
        new_count = 0
        gift_ops = self.bot.get_cog('GiftOperations')
        if not gift_ops:
            logger.warning("GiftOperations cog not loaded, skipping processing")
            return 0

        for code in codes:
            cursor = self.conn.execute("SELECT 1 FROM gift_codes WHERE giftcode = ?", (code,))
            if cursor.fetchone():
                # Already known — just record provenance
                self.conn.execute(
                    "INSERT OR IGNORE INTO scraper_code_provenance (giftcode, source, discovered_at) VALUES (?, ?, ?)",
                    (code, source, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                )
                self.conn.commit()
                continue

            # Validate via WOS API (test account)
            logger.info(f"Validating new candidate from {source}: {code}")
            try:
                status = await gift_ops.claimer.claim_giftcode_rewards_wos(WOS_TEST_PLAYER_ID, code)
            except Exception as e:
                logger.error(f"Validation error for {code}: {e}")
                continue

            if status not in ("SUCCESS", "RECEIVED", "SAME TYPE EXCHANGE"):
                logger.info(f"Code {code} invalid (status: {status})")
                continue

            # Valid code — insert into DB
            logger.info(f"New valid code found from {source}: {code}")
            now = datetime.now()
            date_str = now.strftime("%Y-%m-%d")

            self.conn.execute(
                "INSERT OR IGNORE INTO gift_codes (giftcode, date) VALUES (?, ?)",
                (code, date_str)
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO scraper_code_provenance (giftcode, source, discovered_at) VALUES (?, ?, ?)",
                (code, source, now.strftime("%Y-%m-%d %H:%M:%S"))
            )
            self.conn.commit()
            new_count += 1

            # Sync with community gift code API
            api = getattr(gift_ops, 'api', None)
            if api:
                _create_monitored_task(api.add_giftcode(code), name="api_add_giftcode")

            # Auto-redeem for alliances with giftcodecontrol.status = 1
            cursor = self.conn.execute("SELECT alliance_id FROM giftcodecontrol WHERE status = 1")
            auto_alliances = cursor.fetchall() or []

            # Notify admins
            admin_id_list = get_global_admin_ids()
            if admin_id_list:
                admin_embed = discord.Embed(
                    title="New Gift Code Found!",
                    description=(
                        f"**Gift Code Details**\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"**Code:** `{code}`\n"
                        f"**Date:** `{date_str}`\n"
                        f"**Status:** `Scraped from {source}`\n"
                        f"**Time:** <t:{int(now.timestamp())}:R>\n"
                        f"**Auto Alliance Count:** `{len(auto_alliances)}`\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    ),
                    color=discord.Color.green()
                )
                for admin_id in admin_id_list:
                    try:
                        admin_user = await self.bot.fetch_user(admin_id)
                        if admin_user:
                            await admin_user.send(embed=admin_embed)
                    except Exception as e:
                        logger.debug("Failed to send new code notification to admin %s: %s", admin_id, e)

            # Distribute to auto-enabled alliances
            for alliance in auto_alliances:
                try:
                    await gift_ops.distributor.use_giftcode_for_alliance(alliance[0], code)
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f"Auto-redeem error for alliance {alliance[0]}: {e}")

            await asyncio.sleep(3)

        return new_count

    # ------------------------------------------------------------------
    # Background task
    # ------------------------------------------------------------------

    async def _run_scrape_cycle(self):
        if self._running:
            logger.info("Cycle already running, skipping")
            return
        self._running = True
        try:
            logger.info("Starting scrape cycle...")
            scraper_map = {
                "wosrewards": self.scrape_wosrewards,
                "woswiki": self.scrape_woswiki,
                "lootbar": self.scrape_lootbar,
                "gamesradar": self.scrape_gamesradar,
                "beebom": self.scrape_beebom,
                "dexerto": self.scrape_dexerto,
                "reddit": self.scrape_reddit,
            }

            cursor = self.conn.execute("SELECT name, enabled, consecutive_failures FROM scraper_sources")
            sources = cursor.fetchall()

            total_new = 0
            for name, enabled, failures in sources:
                if not enabled:
                    continue
                if failures >= 5:
                    logger.warning(f"Skipping {name} (consecutive failures: {failures})")
                    continue

                scraper_fn = scraper_map.get(name)
                if not scraper_fn:
                    continue

                try:
                    codes = await scraper_fn()
                    new_count = await self._process_new_codes(codes, name)
                    total_new += new_count

                    self.conn.execute("""
                        UPDATE scraper_sources
                        SET last_run = ?, last_error = NULL, codes_found_last = ?, consecutive_failures = 0
                        WHERE name = ?
                    """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(codes), name))
                    self.conn.commit()

                    logger.info(f"{name}: found {len(codes)} candidates, {new_count} new valid codes")

                except Exception as e:
                    error_msg = str(e)[:200]
                    logger.exception(f"Error scraping {name}: {e}")

                    self.conn.execute("""
                        UPDATE scraper_sources
                        SET last_run = ?, last_error = ?, consecutive_failures = consecutive_failures + 1
                        WHERE name = ?
                    """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), error_msg, name))
                    self.conn.commit()

                await asyncio.sleep(5)

            logger.info(f"Cycle complete. Total new codes: {total_new}")
        finally:
            self._running = False

    @tasks.loop(minutes=60)
    async def scrape_loop(self):
        await self._run_scrape_cycle()

    @scrape_loop.before_loop
    async def before_scrape_loop(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)

    # ------------------------------------------------------------------
    # Settings menu integration
    # ------------------------------------------------------------------

    async def show_scraper_menu(self, interaction: discord.Interaction):
        cursor = self.conn.execute("SELECT COUNT(*) FROM scraper_sources WHERE enabled = 1")
        row = cursor.fetchone()
        enabled_count = row[0] if row else 0
        cursor = self.conn.execute("SELECT COUNT(*) FROM scraper_sources")
        row = cursor.fetchone()
        total_count = row[0] if row else 0

        embed = discord.Embed(
            title="🌐 Gift Code Scraper",
            description=(
                "Please select an operation:\n\n"
                "**Scraper Overview**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"**Sources:** `{enabled_count}/{total_count}` enabled\n"
                f"**Loop Interval:** `60 minutes`\n"
                f"**Status:** `{'Running' if self._running else 'Idle'}`\n\n"
                "**Available Operations**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "📊 **Scraper Status**\n"
                "└ View status of all sources\n\n"
                "▶️ **Run Now**\n"
                "└ Manually trigger a scrape cycle\n\n"
                "⚙️ **Toggle Sources**\n"
                "└ Enable or disable individual sources\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.teal()
        )
        view = ScraperView(self)
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except discord.InteractionResponded:
            logger.debug("InteractionResponded in scraper menu, ignoring")

    async def show_scraper_status(self, interaction: discord.Interaction):
        cursor = self.conn.execute(
            "SELECT name, enabled, last_run, last_error, codes_found_last, consecutive_failures FROM scraper_sources ORDER BY name"
        )
        rows = cursor.fetchall()

        lines = []
        for name, enabled, last_run, last_error, codes_found, failures in rows:
            status_icon = "✅" if enabled else "❌"
            last_run_str = last_run or "Never"
            fail_str = f" ⚠️{failures}" if failures > 0 else ""
            lines.append(f"{status_icon} `{name:<12}` Last: `{last_run_str}` | Codes: `{codes_found}`{fail_str}")

        embed = discord.Embed(
            title="📊 Scraper Source Status",
            description="\n".join(lines) if lines else "No sources configured.",
            color=discord.Color.blue()
        )
        embed.set_footer(text="Use Toggle Sources to enable/disable")

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Back",
            emoji="◀️",
            style=discord.ButtonStyle.secondary,
            custom_id="scraper_settings"
        ))
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except discord.InteractionResponded:
            logger.debug("InteractionResponded in scraper menu, ignoring")

    async def show_toggle_menu(self, interaction: discord.Interaction):
        cursor = self.conn.execute("SELECT name, enabled FROM scraper_sources ORDER BY name")
        sources = cursor.fetchall()

        if not sources:
            await interaction.response.send_message("No sources configured.", ephemeral=True)
            return

        options = [
            discord.SelectOption(
                label=name,
                description=f"Currently: {'Enabled' if enabled else 'Disabled'}",
                value=name,
                emoji="✅" if enabled else "❌"
            )
            for name, enabled in sources
        ]

        select = discord.ui.Select(
            placeholder="Select a source to toggle...",
            options=options,
            custom_id="scraper_toggle_select"
        )

        async def select_callback(select_interaction: discord.Interaction):
            selected = select_interaction.data["values"][0]
            cursor = self.conn.execute("SELECT enabled FROM scraper_sources WHERE name = ?", (selected,))
            row = cursor.fetchone()
            if row:
                new_status = 0 if row[0] else 1
                self.conn.execute(
                    "UPDATE scraper_sources SET enabled = ?, consecutive_failures = 0 WHERE name = ?",
                    (new_status, selected)
                )
                self.conn.commit()
            await self.show_toggle_menu(select_interaction)

        select.callback = select_callback

        embed = discord.Embed(
            title="⚙️ Toggle Scraper Sources",
            description="Select a source to toggle on/off:",
            color=discord.Color.teal()
        )

        view = discord.ui.View()
        view.add_item(select)
        view.add_item(discord.ui.Button(
            label="Back",
            emoji="◀️",
            style=discord.ButtonStyle.secondary,
            custom_id="scraper_settings",
            row=1
        ))
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except discord.InteractionResponded:
            logger.debug("InteractionResponded in scraper menu, ignoring")

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    def _is_admin(self, user_id: int) -> bool:
        return check_admin(user_id)

    @app_commands.command(name="scraper_status", description="Show status of all scraper sources")
    async def scraper_status(self, interaction: discord.Interaction):
        if not self._is_admin(interaction.user.id):
            await send_error(interaction, "This action requires admin privileges.", "Unauthorized")
            return
        try:
            cursor = self.conn.execute(
                "SELECT name, enabled, last_run, last_error, codes_found_last, consecutive_failures FROM scraper_sources ORDER BY name"
            )
            rows = cursor.fetchall()

            if not rows:
                await interaction.response.send_message("No scraper sources configured.", ephemeral=True)
                return

            lines = []
            for name, enabled, last_run, last_error, codes_found, failures in rows:
                status_icon = "ON" if enabled else "OFF"
                last_run_str = last_run or "Never"
                error_str = f" | Err: {last_error[:40]}" if last_error else ""
                fail_str = f" | Fails: {failures}" if failures > 0 else ""
                lines.append(
                    f"`{name:<12}` {status_icon} | Last: `{last_run_str}` | Codes: `{codes_found}`{fail_str}{error_str}"
                )

            embed = discord.Embed(
                title="Scraper Status",
                description="\n".join(lines),
                color=discord.Color.blue()
            )
            embed.set_footer(text="Loop interval: 60 minutes")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.exception(f"Error in scraper_status command: {e}")
            await send_error(interaction, str(e), "Error")

    @app_commands.command(name="scraper_run", description="Manually trigger a scrape cycle")
    async def scraper_run(self, interaction: discord.Interaction):
        if not self._is_admin(interaction.user.id):
            await send_error(interaction, "This action requires admin privileges.", "Unauthorized")
            return
        try:
            if self._running:
                await interaction.response.send_message(
                    embed=discord.Embed(title="Already Running", description="A scrape cycle is already in progress.", color=discord.Color.orange()),
                    ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True)
            await self._run_scrape_cycle()
            await send_success(interaction, "Cycle finished. Use `/scraper_status` for results.", "Scrape Complete")
        except Exception as e:
            logger.exception(f"Error in scraper_run command: {e}")
            await send_error(interaction, str(e), "Error")

    @app_commands.command(name="scraper_toggle", description="Enable or disable a scraper source")
    @app_commands.describe(source="The scraper source to toggle")
    async def scraper_toggle(self, interaction: discord.Interaction, source: str):
        if not self._is_admin(interaction.user.id):
            await send_error(interaction, "This action requires admin privileges.", "Unauthorized")
            return
        try:
            cursor = self.conn.execute("SELECT enabled FROM scraper_sources WHERE name = ?", (source,))
            row = cursor.fetchone()
            if not row:
                await send_error(interaction, f"Source `{source}` not found.", "Unknown Source")
                return

            new_status = 0 if row[0] else 1
            self.conn.execute(
                "UPDATE scraper_sources SET enabled = ?, consecutive_failures = 0 WHERE name = ?",
                (new_status, source)
            )
            self.conn.commit()

            status_text = "enabled" if new_status else "disabled"
            embed = discord.Embed(
                title="Scraper Source Updated",
                description=(
                    f"**Source:** `{source}`\n"
                    f"**Status:** `{status_text}`\n"
                ),
                color=discord.Color.green() if new_status else discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.exception(f"Error in scraper_toggle command: {e}")
            await send_error(interaction, str(e), "Error")

    @scraper_toggle.autocomplete("source")
    async def source_autocomplete(self, interaction: discord.Interaction, current: str):
        cursor = self.conn.execute("SELECT name, enabled FROM scraper_sources")
        sources = cursor.fetchall()
        return [
            app_commands.Choice(
                name=f"{name} ({'ON' if enabled else 'OFF'})",
                value=name
            )
            for name, enabled in sources
            if current.lower() in name.lower()
        ][:25]


class ScraperView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=300)
        self.cog = cog

    async def on_timeout(self):
        if hasattr(self, 'message') and self.message:
            for item in self.children:
                item.disabled = True
            try:
                await self.message.edit(view=self)
            except Exception as e:
                logger.debug("Failed to edit message on timeout: %s", e)

    @discord.ui.button(
        label="Scraper Status",
        style=discord.ButtonStyle.blurple,
        custom_id="scraper_view_status",
        emoji="📊",
        row=0
    )
    async def view_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_scraper_status(interaction)

    @discord.ui.button(
        label="Run Now",
        style=discord.ButtonStyle.green,
        custom_id="scraper_run_now",
        emoji="▶️",
        row=0
    )
    async def run_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.cog._running:
            await interaction.response.send_message("A scrape cycle is already running.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.cog._run_scrape_cycle()
        await interaction.followup.send("Scrape cycle completed.", ephemeral=True)

    @discord.ui.button(
        label="Toggle Sources",
        style=discord.ButtonStyle.grey,
        custom_id="scraper_toggle_sources",
        emoji="⚙️",
        row=0
    )
    async def toggle_sources(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_toggle_menu(interaction)

    @discord.ui.button(
        label="Main Menu",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="scraper_main_menu",
        row=1
    )
    async def main_menu_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            alliance_cog = self.cog.bot.get_cog("Alliance")
            if alliance_cog:
                try:
                    await interaction.message.edit(content=None, embed=None, view=None)
                except Exception as e:
                    logger.debug("Failed to clear message before main menu: %s", e)
                await alliance_cog.show_main_menu(interaction)
        except Exception as e:
            logger.debug("Error returning to main menu from scraper: %s", e)


async def setup(bot):
    await bot.add_cog(GiftScraper(bot))
