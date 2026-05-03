import asyncio
import os
import re
from datetime import datetime

import discord
from discord.ext import commands, tasks

from .config import WOS_TEST_PLAYER_ID
from .database import DatabaseManager
from .gift_api import GiftCodeClaimer
from .gift_distribution import GiftDistributor
from .gift_operationsapi import GiftCodeAPI
from .gift_ui import GiftUI
from .gift_views import GiftView, RetryFailedView, CreateGiftCodeModal, DeleteGiftCodeModal
from .log_config import get_logger
from .utils import AllianceSelectView, PaginatedChannelView, _create_monitored_task, build_embed, check_admin, check_global_admin, get_admin_info as _utils_get_admin_info, get_global_admin_ids


logger = get_logger("gift_operations")


class GiftOperations(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.conn = DatabaseManager.instance().get("giftcode")

        self.api = GiftCodeAPI(bot)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS giftcodecontrol (
                alliance_id INTEGER PRIMARY KEY,
                status INTEGER DEFAULT 0
            )
        """)
        self.conn.commit()

        self.settings_conn = DatabaseManager.instance().get("settings")

        self.alliance_conn = DatabaseManager.instance().get("alliance")

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS giftcode_channel (
                alliance_id INTEGER,
                channel_id INTEGER,
                PRIMARY KEY (alliance_id)
            )
        """)
        self.conn.commit()

        self.claimer = GiftCodeClaimer()
        self.distributor = GiftDistributor(bot, self.claimer)

        self.ui = GiftUI(self)

        self.retry_missing_codes.start()

    async def cog_unload(self):
        self.retry_missing_codes.cancel()
        self.check_channels_loop.cancel()
        try:
            if hasattr(self, 'api') and self.api:
                self.api.cancel()
                await self.api.close()
        except Exception as e:
            logger.debug("Error closing API client during cog unload: %s", e)
        try:
            await self.claimer.close()
        except Exception as e:
            logger.debug("Error closing claimer during cog unload: %s", e)

    # ------------------------------------------------------------------
    # Background retry loop for missing redemptions
    # ------------------------------------------------------------------

    @tasks.loop(minutes=30)
    async def retry_missing_codes(self):
        """Retry redeeming gift codes for members who haven't received them yet."""
        try:
            cursor = self.conn.execute("SELECT alliance_id FROM giftcodecontrol WHERE status = 1")
            auto_alliances = cursor.fetchall()
            if not auto_alliances:
                return

            cursor = self.conn.execute("SELECT giftcode FROM gift_codes")
            all_codes = [row[0] for row in cursor.fetchall()]
            if not all_codes:
                return

            for giftcode in all_codes:
                # Validate code is still active with test account
                try:
                    status = await self.claimer.claim_giftcode_rewards_wos(WOS_TEST_PLAYER_ID, giftcode)
                except Exception as e:
                    logger.error(f"[RETRY] Validation error for {giftcode}: {e}")
                    continue

                if status in ("TIME_ERROR", "CDK_NOT_FOUND", "USAGE_LIMIT"):
                    continue  # Code expired/invalid, skip

                for alliance_row in auto_alliances:
                    alliance_id = alliance_row[0]

                    # Get alliance members
                    try:
                        users_conn = DatabaseManager.instance().get("users")
                        users_cursor = users_conn.cursor()
                        users_cursor.execute("SELECT fid FROM users WHERE alliance = ?", (str(alliance_id),))
                        members = [row[0] for row in users_cursor.fetchall()]
                    except Exception as e:
                        logger.error(f"[RETRY] DB error reading users for alliance {alliance_id}: {e}")
                        continue

                    if not members:
                        continue

                    # Find members who haven't redeemed this code yet
                    placeholders = ','.join('?' * len(members))
                    cursor = self.conn.execute(f"""
                        SELECT fid FROM user_giftcodes
                        WHERE giftcode = ? AND fid IN ({placeholders})
                    """, (giftcode, *members))
                    redeemed_fids = {row[0] for row in cursor.fetchall()}

                    missing = [fid for fid in members if fid not in redeemed_fids]
                    if not missing:
                        continue

                    logger.info(f"[RETRY] {giftcode}: {len(missing)} members missing in alliance {alliance_id}, retrying...")

                    retried_success = 0
                    failed_details = []  # (nickname, reason)
                    code_dead = False

                    # Resolve FID → nickname for better reporting
                    users_conn = DatabaseManager.instance().get("users")
                    fid_names = {}
                    for fid in missing:
                        cursor = users_conn.cursor()
                        cursor.execute("SELECT nickname FROM users WHERE fid = ?", (fid,))
                        row = cursor.fetchone()
                        fid_names[fid] = row[0] if row else str(fid)

                    for fid in missing:
                        nickname = fid_names.get(fid, str(fid))
                        try:
                            result = await self.claimer.claim_giftcode_rewards_wos(fid, giftcode)

                            if result in ("USAGE_LIMIT", "TIME_ERROR", "CDK_NOT_FOUND"):
                                code_dead = True
                                break

                            if result in ("SUCCESS", "RECEIVED", "SAME TYPE EXCHANGE"):
                                retried_success += 1
                            elif result == "STOVE_LV_ERROR":
                                logger.info("[RETRY] %s - %s (%s) - skipped (furnace level too low)", giftcode, nickname, fid)
                            else:
                                failed_details.append((nickname, result))

                            logger.info("[RETRY] %s - %s (%s) - %s", giftcode, nickname, fid, result)

                            await asyncio.sleep(2)
                        except Exception as e:
                            logger.error("[RETRY] Error for %s (%s), code %s: %s", nickname, fid, giftcode, e)
                            failed_details.append((nickname, "ERROR"))
                            await asyncio.sleep(2)

                    if retried_success > 0 or failed_details:
                        logger.info("[RETRY] %s alliance %s: %d success, %d failed",
                                    giftcode, alliance_id, retried_success, len(failed_details))

                        # Post summary to alliance channel
                        cursor = self.alliance_conn.execute(
                            "SELECT channel_id FROM alliancesettings WHERE alliance_id = ?",
                            (alliance_id,)
                        )
                        ch_row = cursor.fetchone()
                        if ch_row:
                            channel = self.bot.get_channel(ch_row[0])
                            if channel:
                                try:
                                    desc = (
                                        f"**Code:** `{giftcode}`\n"
                                        f"**Retried:** `{retried_success + len(failed_details)}`\n"
                                        f"**Success:** `{retried_success}`\n"
                                        f"**Failed:** `{len(failed_details)}`\n"
                                    )
                                    if failed_details:
                                        desc += "\n**Failed Users:**\n"
                                        for name, reason in failed_details:
                                            desc += f"  - `{name}` ({reason})\n"

                                    embed = discord.Embed(
                                        title="Retry Summary",
                                        description=desc,
                                        color=discord.Color.green() if not failed_details else discord.Color.orange()
                                    )
                                    await channel.send(embed=embed)
                                except Exception as e:
                                    logger.debug("Failed to send retry summary to channel: %s", e)

                    if code_dead:
                        break  # Skip this code for remaining alliances

                await asyncio.sleep(5)

        except Exception as e:
            logger.exception(f"[RETRY] Error in retry loop: {e}")

    @retry_missing_codes.before_loop
    async def before_retry_loop(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(120)  # Wait 2 minutes before first run

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            cursor = self.conn.execute("SELECT channel_id FROM giftcode_channel")
            channel_ids = [row[0] for row in cursor.fetchall()]

            invalid_channels = []
            for channel_id in channel_ids:
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    invalid_channels.append(channel_id)

            if invalid_channels:
                placeholders = ','.join('?' * len(invalid_channels))
                self.conn.execute(f"""
                    DELETE FROM giftcode_channel 
                    WHERE channel_id IN ({placeholders})
                """, invalid_channels)
                self.conn.commit()
                logger.info(f"Startup: Removed {len(invalid_channels)} invalid channels from database.")

            if not self.check_channels_loop.is_running():
                self.check_channels_loop.start()

        except Exception as e:
            logger.error(f"Error in on_ready: {str(e)}")

    @discord.ext.commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        try:
            if message.author.bot or not message.guild:
                return

            cursor = self.conn.execute("SELECT alliance_id FROM giftcode_channel WHERE channel_id = ?", (message.channel.id,))
            channel_info = cursor.fetchone()
            
            if not channel_info:
                return

            content = message.content.strip()
            if not content:
                return

            giftcode = None
            if len(content.split()) == 1:
                giftcode = content
            else:
                code_match = re.search(r'Code:\s*(\S+)', content)
                if code_match:
                    giftcode = code_match.group(1)

            if not giftcode:
                await message.add_reaction("❌")
                error_embed = build_embed("❌ Invalid Format", {
                    "👤 Sender": message.author.mention,
                    "❌ Status": "Invalid gift code format",
                }, header="Gift Code Details")
                await message.reply(embed=error_embed, mention_author=False)
                return

            initial_check = await self.claimer.claim_giftcode_rewards_wos(WOS_TEST_PLAYER_ID, giftcode)
            
            if initial_check == "USAGE_LIMIT":
                await message.add_reaction("❌")
                usage_limit_embed = build_embed("❌ Gift Code Usage Limit", {
                    "👤 Sender": message.author.mention,
                    "🎁 Gift Code": giftcode,
                    "❌ Status": "Usage limit has been reached for this code",
                }, header="Gift Code Details")
                await message.reply(embed=usage_limit_embed, mention_author=False)
                return

            if initial_check == "TIME_ERROR":
                await message.add_reaction("❌")
                expired_embed = build_embed("❌ Gift Code Expired", {
                    "👤 Sender": message.author.mention,
                    "🎁 Gift Code": giftcode,
                }, header="Gift Code Details")
                await message.reply(embed=expired_embed, mention_author=False)
                return

            if initial_check == "CDK_NOT_FOUND":
                await message.add_reaction("❌")
                invalid_embed = build_embed("❌ Invalid Gift Code", {
                    "👤 Sender": message.author.mention,
                    "🎁 Gift Code": giftcode,
                }, header="Gift Code Details")
                await message.reply(embed=invalid_embed, mention_author=False)
                return

            if initial_check in ["SUCCESS", "RECEIVED", "SAME TYPE EXCHANGE"]:
                cursor = self.conn.execute("SELECT 1 FROM gift_codes WHERE giftcode = ?", (giftcode,))
                if not cursor.fetchone():
                    self.conn.execute(
                        "INSERT INTO gift_codes (giftcode, date) VALUES (?, ?)",
                        (giftcode, datetime.now().strftime("%Y-%m-%d"))
                    )
                    self.conn.commit()

                    _create_monitored_task(self.api.add_giftcode(giftcode), name="api_add_giftcode")

                    cursor = self.conn.execute("SELECT alliance_id FROM giftcodecontrol WHERE status = 1")
                    auto_alliances = cursor.fetchall()

                    success_embed = build_embed("\u2705 Gift Code Successfully Added", {
                        "\U0001f464 Sender": message.author.mention,
                        "\U0001f381 Gift Code": giftcode,
                    }, header="Gift Code Details")
                    await message.add_reaction("\u2705")
                    await message.reply(embed=success_embed, mention_author=False)

                    for alliance in auto_alliances:
                        _create_monitored_task(
                            self.distributor.use_giftcode_for_alliance(alliance[0], giftcode),
                            name=f"distribute_{giftcode}_{alliance[0]}"
                        )
                else:
                    already_exists_embed = build_embed("ℹ️ Gift Code Status", {
                        "👤 Sender": message.author.mention,
                        "🎁 Gift Code": giftcode,
                        "📝 Status": "Already in database",
                    }, header="Gift Code Details", color=discord.Color.blue())
                    await message.add_reaction("✅")
                    await message.reply(embed=already_exists_embed, mention_author=False)
            else:
                await message.add_reaction("⚠️")
                await message.reply("⚠️ Temporary error occurred. Please try again later.", delete_after=10)

        except Exception as e:
            logger.error(f"Error in on_message: {str(e)}")

    @tasks.loop(seconds=300)
    async def check_channels_loop(self):
        try:
            cursor = self.conn.execute("SELECT channel_id FROM giftcode_channel")
            channel_ids = [row[0] for row in cursor.fetchall()]

            invalid_channels = []

            for channel_id in channel_ids:
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    logger.warning(f"Channel {channel_id} not found. It will be removed from database.")
                    invalid_channels.append(channel_id)
                    continue

                last_reaction_time = None
                async for message in channel.history(limit=100):
                    if message.reactions:
                        for reaction in message.reactions:
                            async for user in reaction.users():
                                if user == self.bot.user:
                                    last_reaction_time = message.created_at
                                    break
                            if last_reaction_time:
                                break
                    if last_reaction_time:
                        break

                if not last_reaction_time:
                    messages_to_check = [msg async for msg in channel.history(limit=10, oldest_first=True)]
                else:
                    messages_to_check = [msg async for msg in channel.history(limit=50, after=last_reaction_time, oldest_first=True)]

                for message in messages_to_check:
                    if message.author == self.bot.user:
                        continue

                    content = message.content.strip()
                    if not content:
                        continue

                    has_bot_reaction = False
                    for reaction in message.reactions:
                        async for user in reaction.users():
                            if user == self.bot.user:
                                has_bot_reaction = True
                                break
                        if has_bot_reaction:
                            break
                    
                    if has_bot_reaction:
                        continue

                    giftcode = None
                    
                    if len(content.split()) == 1:
                        giftcode = content
                    else:
                        code_match = re.search(r'Code:\s*(\S+)', content)
                        if code_match:
                            giftcode = code_match.group(1)

                    if not giftcode:
                        await message.add_reaction("❌")
                        continue

                    try:
                        response_status = await self.claimer.claim_giftcode_rewards_wos(WOS_TEST_PLAYER_ID, giftcode)

                        if response_status == "USAGE_LIMIT":
                            usage_limit_embed = build_embed("❌ Gift Code Usage Limit", {
                                "👤 Sender": message.author.mention,
                                "🎁 Gift Code": giftcode,
                                "❌ Status": "Usage limit has been reached for this code",
                            }, header="Gift Code Details")
                            await message.add_reaction("❌")
                            await message.reply(embed=usage_limit_embed, mention_author=False)
                            continue

                        if response_status == "TIME_ERROR":
                            expired_embed = build_embed("❌ Gift Code Expired", {
                                "👤 Sender": message.author.mention,
                                "🎁 Gift Code": giftcode,
                            }, header="Gift Code Details")
                            await message.add_reaction("❌")
                            await message.reply(embed=expired_embed, mention_author=False)
                            continue

                        if response_status in ["SUCCESS", "RECEIVED", "SAME TYPE EXCHANGE"]:
                            cursor = self.conn.execute("SELECT 1 FROM gift_codes WHERE giftcode = ?", (giftcode,))
                            if not cursor.fetchone():
                                self.conn.execute(
                                    "INSERT INTO gift_codes (giftcode, date) VALUES (?, ?)",
                                    (giftcode, datetime.now().strftime("%Y-%m-%d"))
                                )
                                self.conn.commit()

                                cursor = self.conn.execute("SELECT alliance_id FROM giftcodecontrol WHERE status = 1")
                                auto_alliances = cursor.fetchall()

                                _fields = {}
                                if isinstance(message.author, (discord.Member, discord.User)):
                                    _fields["\U0001f464 Sender"] = message.author.mention
                                _fields["\U0001f381 Gift Code"] = giftcode
                                success_embed = build_embed("\u2705 Gift Code Successfully Added", _fields, header="Gift Code Details")
                                await message.add_reaction("\u2705")
                                await message.reply(embed=success_embed, mention_author=False)

                                for alliance in auto_alliances:
                                    _create_monitored_task(
                                        self.distributor.use_giftcode_for_alliance(alliance[0], giftcode),
                                        name=f"distribute_{giftcode}_{alliance[0]}"
                                    )

                            else:
                                _fields = {}
                                if isinstance(message.author, (discord.Member, discord.User)):
                                    _fields["👤 Sender"] = message.author.mention
                                _fields["🎁 Gift Code"] = giftcode
                                _fields["📝 Status"] = "Already in database"
                                already_exists_embed = build_embed("ℹ️ Gift Code Status", _fields, header="Gift Code Details", color=discord.Color.blue())
                                await message.add_reaction("✅")
                                await message.reply(embed=already_exists_embed, mention_author=False)

                        elif response_status == "CDK_NOT_FOUND":
                            _fields = {}
                            if isinstance(message.author, (discord.Member, discord.User)):
                                _fields["👤 Sender"] = message.author.mention
                            _fields["🎁 Gift Code"] = giftcode
                            error_embed = build_embed("❌ Invalid Gift Code", _fields, header="Gift Code Details")
                            await message.add_reaction("❌")
                            await message.reply(embed=error_embed, mention_author=False)

                        elif response_status == "TIMEOUT_RETRY":
                            await message.add_reaction("⏳")
                            await asyncio.sleep(60)
                            retry_response = await self.claimer.claim_giftcode_rewards_wos(WOS_TEST_PLAYER_ID, giftcode)
                            await message.remove_reaction("⏳", self.bot.user)
                            if retry_response in ["SUCCESS", "RECEIVED", "SAME TYPE EXCHANGE"]:
                                cursor = self.conn.execute("SELECT 1 FROM gift_codes WHERE giftcode = ?", (giftcode,))
                                if not cursor.fetchone():
                                    self.conn.execute(
                                        "INSERT INTO gift_codes (giftcode, date) VALUES (?, ?)",
                                        (giftcode, datetime.now().strftime("%Y-%m-%d"))
                                    )
                                    self.conn.commit()

                                    success_embed = build_embed("✅ Gift Code Successfully Added", {
                                        "👤 Sender": message.author.mention,
                                        "🎁 Gift Code": giftcode,
                                    }, header="Gift Code Details")
                                    await message.add_reaction("✅")
                                    await message.reply(embed=success_embed, mention_author=False)

                    except Exception as e:
                        logger.error(f"Error processing gift code {giftcode}: {str(e)}")
                        continue

            if invalid_channels:
                placeholders = ','.join('?' * len(invalid_channels))
                self.conn.execute(f"""
                    DELETE FROM giftcode_channel 
                    WHERE channel_id IN ({placeholders})
                """, invalid_channels)
                self.conn.commit()
                logger.info(f"Removed {len(invalid_channels)} invalid channels from database.")

            await self.validate_gift_codes()

        except Exception as e:
            logger.error(f"Error in check_channels_loop: {str(e)}")

    async def validate_gift_codes(self):
        try:
            cursor = self.conn.execute("SELECT giftcode FROM gift_codes")
            all_codes = cursor.fetchall()
            
            admin_ids = get_global_admin_ids()
            
            for code in all_codes:
                giftcode = code[0]
                status = await self.claimer.claim_giftcode_rewards_wos(WOS_TEST_PLAYER_ID, giftcode)
                
                if status in ["TIME_ERROR", "CDK_NOT_FOUND", "USAGE_LIMIT"]:
                    await self.api.remove_giftcode(giftcode, from_validation=True)
                    self.conn.execute("DELETE FROM user_giftcodes WHERE giftcode = ?", (giftcode,))
                    self.conn.execute("DELETE FROM gift_codes WHERE giftcode = ?", (giftcode,))
                    self.conn.commit()
                    
                    reason = "expired" if status == "TIME_ERROR" else "invalid" if status == "CDK_NOT_FOUND" else "usage limit reached"
                    admin_embed = build_embed("🎁 Gift Code Removed", {
                        "🎁 Gift Code": giftcode,
                        "❌ Reason": f"Code {reason}",
                        "⏰ Time": f"<t:{int(datetime.now().timestamp())}:R>",
                    }, header="Gift Code Details", color=discord.Color.red())
                    
                    for admin_id in admin_ids:
                        try:
                            admin_user = await self.bot.fetch_user(admin_id)
                            if admin_user:
                                await admin_user.send(embed=admin_embed)
                        except Exception as e:
                            logger.error(f"Error sending message to admin {admin_id}: {str(e)}")
                
                await asyncio.sleep(60)
                
        except Exception as e:
            logger.error(f"Error in validate_gift_codes: {str(e)}")

    async def handle_success(self, message, giftcode):
        status = await self.claimer.claim_giftcode_rewards_wos(WOS_TEST_PLAYER_ID, giftcode)

        if status in ["SUCCESS", "RECEIVED", "SAME TYPE EXCHANGE"]:
            cursor = self.conn.execute("SELECT 1 FROM gift_codes WHERE giftcode = ?", (giftcode,))
            if not cursor.fetchone():
                self.conn.execute("INSERT INTO gift_codes (giftcode, date) VALUES (?, ?)", (giftcode, datetime.now().strftime("%Y-%m-%d")))
                self.conn.commit()

                _create_monitored_task(self.api.add_giftcode(giftcode), name="api_add_giftcode")

                await message.add_reaction("✅")
                await message.reply("Gift code successfully added.", mention_author=False)
        elif status == "TIME_ERROR":
            await self.handle_time_error(message)
        elif status == "CDK_NOT_FOUND":
            await self.handle_cdk_not_found(message)
        elif status == "USAGE_LIMIT":
            await message.add_reaction("❌")
            await message.reply("Usage limit has been reached for this code.", mention_author=False)

    async def handle_already_received(self, message, giftcode):
        status = await self.claimer.claim_giftcode_rewards_wos(WOS_TEST_PLAYER_ID, giftcode)

        if status in ["SUCCESS", "RECEIVED", "SAME TYPE EXCHANGE"]:
            cursor = self.conn.execute("SELECT 1 FROM gift_codes WHERE giftcode = ?", (giftcode,))
            if not cursor.fetchone():
                self.conn.execute("INSERT INTO gift_codes (giftcode, date) VALUES (?, ?)", (giftcode, datetime.now().strftime("%Y-%m-%d")))
                self.conn.commit()

                _create_monitored_task(self.api.add_giftcode(giftcode), name="api_add_giftcode")

                await message.add_reaction("✅")
                await message.reply("Gift code successfully added.", mention_author=False)
        elif status == "TIME_ERROR":
            await self.handle_time_error(message)
        elif status == "CDK_NOT_FOUND":
            await self.handle_cdk_not_found(message)
        elif status == "USAGE_LIMIT":
            await message.add_reaction("❌")
            await message.reply("Usage limit has been reached for this code.", mention_author=False)

    async def handle_cdk_not_found(self, message):
        await message.add_reaction("❌")
        await message.reply("The gift code is incorrect.", mention_author=False)

    async def handle_time_error(self, message):
        await message.add_reaction("❌")
        await message.reply("Gift code expired.", mention_author=False)

    async def handle_timeout_retry(self, message, giftcode):
        cursor = self.conn.execute("SELECT 1 FROM gift_codes WHERE giftcode = ?", (giftcode,))
        if not cursor.fetchone():
            await message.add_reaction("⏳")




async def setup(bot):
    await bot.add_cog(GiftOperations(bot))