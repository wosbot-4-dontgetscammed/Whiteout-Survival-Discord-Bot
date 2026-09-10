import os
import json
import aiohttp
import asyncio
import re
from datetime import datetime
import discord
import ssl
from .config import WOSLAND_API_URL, WOSLAND_API_KEY, WOS_TEST_PLAYER_ID
from .database import DatabaseManager
from .utils import _create_monitored_task, get_global_admin_ids
from .log_config import get_logger

logger = get_logger("gift_operationsapi")

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class GiftCodeAPI:
    def __init__(self, bot):
        self.bot = bot
        self.api_url = WOSLAND_API_URL
        self.api_key = WOSLAND_API_KEY
        self.check_interval = 300
        self._fail_count = 0
        self._max_backoff = 3600  # max 1h zwischen Versuchen
        self._pause_threshold = 10  # nach 10 Fehlversuchen pausieren
        self._paused = False

        db = DatabaseManager.instance()
        self.conn = db.get("giftcode")

        self.settings_conn = db.get("settings")

        self.users_conn = db.get("users")

        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

        self._connector = None

        # The community code-sync is optional. Without GIFTCODE_API_URL /
        # GIFTCODE_API_KEY every request went to an empty URL, failed with an
        # empty ClientError message and was retried forever - hundreds of
        # meaningless errors a day for a feature that was never set up.
        self.enabled = bool(self.api_url and self.api_key)
        if not self.enabled:
            logger.info(
                "Community gift-code sync disabled (set GIFTCODE_API_URL and "
                "GIFTCODE_API_KEY in .env to enable it); the local scraper is unaffected"
            )
            self._api_task = None
            return

        self._api_task = _create_monitored_task(self.start_api_check(), name="api_check_loop")

    def cancel(self):
        if self._api_task and not self._api_task.done():
            self._api_task.cancel()

    async def start_api_check(self):
        await asyncio.sleep(60)
        while True:
            if self._paused:
                await asyncio.sleep(self._max_backoff)
                # Retry-Versuch nach Pause
                success = await self._try_sync()
                if success:
                    self._fail_count = 0
                    self._paused = False
                    logger.info("Gift code API is back online, resuming sync")
                else:
                    logger.warning(f"Gift code API still unreachable, next retry in {self._max_backoff}s")
                continue

            success = await self._try_sync()
            if success:
                self._fail_count = 0
                wait = self.check_interval
            else:
                self._fail_count += 1
                if self._fail_count >= self._pause_threshold:
                    self._paused = True
                    logger.warning(f"Gift code API unreachable after {self._fail_count} attempts, pausing sync (retry every {self._max_backoff}s)")
                    continue
                wait = min(self.check_interval * (2 ** self._fail_count), self._max_backoff)
                if self._fail_count <= 3:
                    logger.warning(f"Gift code API unreachable (attempt {self._fail_count}), next retry in {wait}s")
            await asyncio.sleep(wait)

    async def _try_sync(self) -> bool:
        try:
            result = await self.sync_with_api()
            return result is True
        except (aiohttp.ClientError, OSError) as e:
            logger.error(f"Connection error during sync: {e}")
            return False
        except Exception as e:
            logger.exception(f"Unexpected error during sync: {e}")
            return False

    def _get_connector(self):
        if self._connector is None or self._connector.closed:
            self._connector = aiohttp.TCPConnector(ssl=self.ssl_context)
        return self._connector

    async def close(self):
        try:
            if self._connector and not self._connector.closed:
                await self._connector.close()
        except Exception:
            pass

    @staticmethod
    def _safe_parse_json(text: str) -> dict | None:
        if not text or not text.strip():
            return None
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None

    async def sync_with_api(self):
        if not getattr(self, "enabled", True):
            return False
        try:
            cursor = self.conn.execute("SELECT giftcode, date FROM gift_codes")
            db_codes = {row[0]: row[1] for row in cursor.fetchall()}

            async with aiohttp.ClientSession(connector=self._get_connector(), connector_owner=False) as session:
                headers = {
                    'X-API-Key': self.api_key,
                    'Content-Type': 'application/json'
                }

                async with session.get(self.api_url, headers=headers, timeout=REQUEST_TIMEOUT) as response:
                    response_text = await response.text()

                    if response.status != 200:
                        logger.error(f"sync_with_api: HTTP {response.status}")
                        return False

                    result = self._safe_parse_json(response_text)
                    if result is None:
                        logger.error("sync_with_api: Empty or invalid JSON response")
                        return False

                    if 'error' in result:
                        logger.error(f"sync_with_api: API error: {result.get('error')}")
                        return False

                    api_giftcodes = result.get('codes', [])

                    valid_codes = []
                    invalid_codes = []
                    for code_line in api_giftcodes:
                        parts = code_line.strip().split()
                        if len(parts) != 2:
                            invalid_codes.append(code_line)
                            continue

                        code, date_str = parts
                        if not re.match("^[a-zA-Z0-9]+$", code):
                            invalid_codes.append(code_line)
                            continue

                        try:
                            date_obj = datetime.strptime(date_str, "%d.%m.%Y")
                            valid_codes.append((code, date_obj))
                        except ValueError:
                            invalid_codes.append(code_line)
                            continue

                    if invalid_codes:
                        for invalid_code in invalid_codes:
                            try:
                                code = invalid_code.split()[0] if ' ' in invalid_code else invalid_code.strip()
                                data = {'code': code}

                                async with session.delete(self.api_url, json=data, headers=headers, timeout=REQUEST_TIMEOUT) as del_response:
                                    pass

                                await asyncio.sleep(1)

                            except (aiohttp.ClientError, OSError) as e:
                                logger.error(f"Failed to delete invalid code {code}: {e}")
                            except Exception as e:
                                logger.error(f"Unexpected error deleting invalid code: {e}")

                        await asyncio.sleep(2)
                        try:
                            async with session.get(self.api_url, headers=headers, timeout=REQUEST_TIMEOUT) as check_response:
                                await check_response.text()
                        except (aiohttp.ClientError, OSError):
                            pass

                    new_codes = []
                    for code, date_obj in valid_codes:
                        formatted_date = date_obj.strftime("%Y-%m-%d")
                        if code not in db_codes:
                            try:
                                self.conn.execute("INSERT OR IGNORE INTO gift_codes (giftcode, date) VALUES (?, ?)", (code, formatted_date))
                                new_codes.append((code, formatted_date))
                            except Exception as e:
                                logger.error(f"DB insert error for {code}: {e}")

                    try:
                        self.conn.commit()
                    except Exception as e:
                        logger.error(f"Error committing: {e}")
                        return False

                    try:
                        if not new_codes:
                            return True

                        for code, formatted_date in new_codes:
                            try:
                                cursor = self.conn.execute("SELECT alliance_id FROM giftcodecontrol WHERE status = 1")
                                auto_alliances = cursor.fetchall() or []

                                admin_id_list = get_global_admin_ids()
                                if admin_id_list:
                                    admin_embed = discord.Embed(
                                        title="🎁 New Gift Code Found!",
                                        description=(
                                            f"**Gift Code Details**\n"
                                            f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                            f"🎁 **Code:** `{code}`\n"
                                            f"📅 **Date:** `{formatted_date}`\n"
                                            f"📝 **Status:** `Retrieved from Reloisback API`\n"
                                            f"⏰ **Time:** <t:{int(datetime.now().timestamp())}:R>\n"
                                            f"🔄 **Auto Alliance Count:** `{len(auto_alliances)}`\n"
                                            f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                        ),
                                        color=discord.Color.green()
                                    )

                                    for admin_id in admin_id_list:
                                        try:
                                            admin_user = await self.bot.fetch_user(admin_id)
                                            if admin_user:
                                                await admin_user.send(embed=admin_embed)
                                        except Exception:
                                            pass

                                if auto_alliances:
                                    for alliance in auto_alliances:
                                        try:
                                            gift_operations = self.bot.get_cog('GiftOperations')
                                            if gift_operations:
                                                await gift_operations.distributor.use_giftcode_for_alliance(alliance[0], code)
                                                await asyncio.sleep(1)
                                            else:
                                                logger.warning("GiftOperations cog not loaded")
                                        except Exception as e:
                                            logger.error(f"Auto-redeem error for alliance {alliance[0]}: {e}")
                            except Exception as e:
                                logger.error(f"Error processing new code {code}: {e}")
                    except Exception as e:
                        logger.exception(f"Error during code distribution: {e}")

                    for db_code, db_date in db_codes.items():
                        try:
                            date_obj = datetime.strptime(db_date, "%Y-%m-%d")
                            formatted_date = date_obj.strftime("%d.%m.%Y")

                            data = {
                                'code': db_code,
                                'date': formatted_date
                            }
                            async with session.post(self.api_url, json=data, headers=headers, timeout=REQUEST_TIMEOUT) as post_response:
                                pass
                        except (aiohttp.ClientError, OSError) as e:
                            logger.error(f"Failed to push code {db_code} to API: {e}")
                        except Exception as e:
                            logger.error(f"Unexpected error pushing code {db_code}: {e}")

                    return True

        except (aiohttp.ClientError, OSError) as e:
            logger.error("Connection error in sync_with_api (%s): %s | url=%s", type(e).__name__, e, self.api_url)
        except Exception as e:
            logger.exception(f"Unexpected error in sync_with_api: {e}")

    async def add_giftcode(self, giftcode: str) -> bool:
        if not getattr(self, "enabled", True):
            return False
        try:
            cursor = self.conn.execute("SELECT 1 FROM gift_codes WHERE giftcode = ?", (giftcode,))
            already_in_db = cursor.fetchone() is not None

            async with aiohttp.ClientSession(connector=self._get_connector(), connector_owner=False) as session:
                headers = {
                    'Content-Type': 'application/json',
                    'X-API-Key': self.api_key
                }

                date_str = datetime.now().strftime("%d.%m.%Y")
                data = {
                    'code': giftcode,
                    'date': date_str
                }

                async with session.post(self.api_url, json=data, headers=headers, timeout=REQUEST_TIMEOUT) as response:
                    if response.status == 200:
                        response_text = await response.text()
                        result = self._safe_parse_json(response_text)

                        if result and result.get('success'):
                            if not already_in_db:
                                self.conn.execute("INSERT OR IGNORE INTO gift_codes (giftcode, date) VALUES (?, ?)", (giftcode, datetime.now().strftime("%Y-%m-%d")))
                                self.conn.commit()
                            return True

                        return False

        except (aiohttp.ClientError, OSError) as e:
            logger.error("Connection error in add_giftcode (%s): %s | url=%s", type(e).__name__, e, self.api_url)
            return False
        except Exception as e:
            logger.exception(f"Unexpected error in add_giftcode: {e}")
            return False

    async def remove_giftcode(self, giftcode: str, from_validation: bool = False) -> bool:
        if not getattr(self, "enabled", True):
            return False
        try:
            if not from_validation:
                return False

            async with aiohttp.ClientSession(connector=self._get_connector(), connector_owner=False) as session:
                headers = {
                    'Content-Type': 'application/json',
                    'X-API-Key': self.api_key
                }
                data = {'code': giftcode}

                async with session.delete(self.api_url, json=data, headers=headers, timeout=REQUEST_TIMEOUT) as response:
                    response_text = await response.text()

                    if response.status == 200:
                        result = self._safe_parse_json(response_text)
                        if result is None:
                            logger.error(f"remove_giftcode: Empty or invalid JSON for {giftcode}")
                            return False
                        success = 'success' in result
                        if success:
                            self.conn.execute("DELETE FROM gift_codes WHERE giftcode = ?", (giftcode,))
                            self.conn.execute("DELETE FROM user_giftcodes WHERE giftcode = ?", (giftcode,))
                            self.conn.commit()
                        return success
                    else:
                        return False
        except (aiohttp.ClientError, OSError) as e:
            logger.error("Connection error in remove_giftcode (%s): %s | url=%s", type(e).__name__, e, self.api_url)
            return False
        except Exception as e:
            logger.exception(f"Unexpected error in remove_giftcode: {e}")
            return False

    async def check_giftcode(self, giftcode: str) -> bool:
        try:
            async with aiohttp.ClientSession(connector=self._get_connector(), connector_owner=False) as session:
                async with session.get(f"{self.api_url}?action=check&giftcode={giftcode}", timeout=REQUEST_TIMEOUT) as response:
                    if response.status == 200:
                        response_text = await response.text()
                        result = self._safe_parse_json(response_text)
                        if result:
                            return result.get('exists', False)
            return False
        except (aiohttp.ClientError, OSError) as e:
            logger.error(f"Connection error in check_giftcode: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error in check_giftcode: {e}")
            return False

    async def validate_and_clean_giftcode_file(self):
        try:
            cursor = self.conn.execute("SELECT giftcode FROM gift_codes")
            codes = cursor.fetchall()

            if not codes:
                return

            for code_row in codes:
                code = code_row[0]
                gift_ops = self.bot.get_cog('GiftOperations')
                if not gift_ops:
                    logger.warning("GiftOperations cog not loaded, skipping validation")
                    return
                status = await gift_ops.claimer.claim_giftcode_rewards_wos(WOS_TEST_PLAYER_ID, code)

                if status in ["TIME_ERROR", "CDK_NOT_FOUND", "USAGE_LIMIT"]:
                    await self.remove_giftcode(code, from_validation=True)

                await asyncio.sleep(3)

        except Exception as e:
            logger.exception(f"Error in validate_and_clean: {e}")
