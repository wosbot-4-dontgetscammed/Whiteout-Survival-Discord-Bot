"""Gift-code distribution logic (no Cog, pure service class)."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING

import discord

from .config import WOS_TEST_PLAYER_ID
from .gift_api import RECHECK_STATUSES
from .database import DatabaseManager
from .log_config import get_logger
from .utils import build_embed

if TYPE_CHECKING:
    from .gift_api import GiftCodeClaimer

logger = get_logger("gift_distribution")


class GiftDistributor:
    """Distributes gift codes to alliance members via GiftCodeClaimer."""

    def __init__(self, bot, claimer: GiftCodeClaimer):
        self.bot = bot
        self.claimer = claimer

    # ------------------------------------------------------------------
    # Distribute pending codes to a single member
    # ------------------------------------------------------------------

    async def distribute_pending_codes_to_member(self, player_id, alliance_id):
        """Distribute all pending gift codes to a single player."""
        results = {}
        conn = DatabaseManager.instance().get("giftcode")
        try:
            cursor = conn.execute("SELECT giftcode FROM gift_codes")
            all_codes = [row[0] for row in cursor.fetchall()]

            if not all_codes:
                return results

            placeholders = ','.join('?' * len(all_codes))
            cursor = conn.execute(f"""
                SELECT giftcode FROM user_giftcodes
                WHERE fid = ? AND giftcode IN ({placeholders})
            """, (player_id, *all_codes))
            already_claimed = {row[0] for row in cursor.fetchall()}

            pending_codes = [c for c in all_codes if c not in already_claimed]

            for code in pending_codes:
                try:
                    status = await self.claimer.claim_giftcode_rewards_wos(player_id, code)
                    results[code] = status

                    if status in ("TIME_ERROR", "CDK_NOT_FOUND", "USAGE_LIMIT"):
                        logger.info(f"DISTRIBUTE_PENDING - Skipping code {code} for {player_id}: {status}")
                        continue

                    logger.info(f"DISTRIBUTE_PENDING - Player {player_id}, Code {code}: {status}")

                    await asyncio.sleep(2)
                except Exception as e:
                    results[code] = f"ERROR_{type(e).__name__}"
                    logger.error(f"DISTRIBUTE_PENDING ERROR - Player {player_id}, Code {code}: {e}")

        except Exception as e:
            logger.error(f"DISTRIBUTE_PENDING CRITICAL ERROR - Player {player_id}: {e}")

        return results

    # ------------------------------------------------------------------
    # Distribute pending codes to all alliance members
    # ------------------------------------------------------------------

    async def distribute_pending_codes_to_alliance(self, alliance_id, channel=None):
        """Distribute all pending gift codes to all alliance members who don't have them yet."""
        conn = DatabaseManager.instance().get("giftcode")
        try:
            cursor = conn.execute("SELECT giftcode FROM gift_codes")
            all_codes = [row[0] for row in cursor.fetchall()]

            if not all_codes:
                if channel:
                    await channel.send(embed=discord.Embed(
                        title="Info",
                        description="No gift codes in database to distribute.",
                        color=discord.Color.blue()
                    ))
                return

            logger.info(f"DISTRIBUTE_ALL - Alliance {alliance_id}, {len(all_codes)} codes to process")

            if channel:
                await channel.send(embed=discord.Embed(
                    title="Gift Code Distribution",
                    description=f"Processing {len(all_codes)} gift code(s) for alliance...",
                    color=discord.Color.blue()
                ))

            for code in all_codes:
                try:
                    await self.use_giftcode_for_alliance(alliance_id, code)
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"DISTRIBUTE_ALL ERROR - Alliance {alliance_id}, Code {code}: {e}")

        except Exception as e:
            logger.error(f"DISTRIBUTE_ALL CRITICAL ERROR - Alliance {alliance_id}: {e}")

    # ------------------------------------------------------------------
    # Use a single gift code for an entire alliance
    # ------------------------------------------------------------------

    async def use_giftcode_for_alliance(self, alliance_id, giftcode):
        try:
            conn = DatabaseManager.instance().get("giftcode")
            alliance_conn = DatabaseManager.instance().get("alliance")

            operation_counter = 0

            successful_users = []
            already_used_users = []
            failed_users = []
            ineligible_users = []   # recharge/VIP-gated - not a failure, not retryable now

            cursor = alliance_conn.execute(
                "SELECT channel_id FROM alliancesettings WHERE alliance_id = ?",
                (alliance_id,)
            )
            channel_result = cursor.fetchone()
            if not channel_result:
                logger.warning("No alliancesettings for alliance %s - skipping gift distribution", alliance_id)
                return False

            cursor = alliance_conn.execute(
                "SELECT name FROM alliance_list WHERE alliance_id = ?",
                (alliance_id,)
            )
            name_result = cursor.fetchone()
            if not name_result:
                logger.warning("Alliance %s not found in alliance_list", alliance_id)
                return False

            channel_id = channel_result[0]
            alliance_name = name_result[0]

            channel = self.bot.get_channel(channel_id)
            if not channel:
                logger.warning("Channel %s for alliance %s (%s) not accessible - bot may not be in server",
                               channel_id, alliance_name, alliance_id)
                return False

            logger.info("Starting gift distribution for alliance %s (%s), code %s", alliance_name, alliance_id, giftcode)

            await asyncio.sleep(0)

            initial_check = await self.claimer.claim_giftcode_rewards_wos(WOS_TEST_PLAYER_ID, giftcode)
            if initial_check == "USAGE_LIMIT":
                usage_limit_embed = build_embed("Gift Code Usage Limit Reached", {
                    "Alliance": alliance_name,
                    "Gift Code": giftcode,
                    "Status": "Usage limit has been reached for this code",
                }, header="Gift Code Details", color=discord.Color.red())
                await channel.send(embed=usage_limit_embed)
                return False

            await asyncio.sleep(0)

            users_conn = DatabaseManager.instance().get("users")
            users_cursor = users_conn.cursor()

            users_cursor.execute(
                "SELECT fid FROM users WHERE alliance = ?",
                (str(alliance_id),)
            )
            members = users_cursor.fetchall()

            if not members:
                return True

            total_members = len(members)
            processed = 0
            success = 0
            received = 0
            failed = 0

            embed = build_embed("Auto Gift Code Progress", {
                "Alliance": alliance_name,
                "Gift Code": giftcode,
                "Total Members": str(total_members),
                "Success": str(success),
                "Already Used": str(received),
                "Failed": str(failed),
                "Progress": f"{processed}/{total_members}",
            }, header="Gift Code Distribution Started", color=discord.Color.blue())
            status_message = await channel.send(embed=embed)

            await asyncio.sleep(0)

            logger.info(f"GIFT CODE: {giftcode} | USAGE TIME: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Alliance Member List")

            member_ids = [member[0] for member in members]
            placeholders = ','.join('?' * len(member_ids))
            cursor = conn.execute(f"""
                SELECT fid, status FROM user_giftcodes
                WHERE giftcode = ? AND fid IN ({placeholders})
            """, (giftcode, *member_ids))
            previous_users = {row[0]: row[1] for row in cursor.fetchall()}

            # A stored recharge/VIP block is not a redemption — count it apart
            # so the report does not claim those members already got the code.
            previously_ineligible = {f for f, s in previous_users.items() if s in RECHECK_STATUSES}
            received = len(previous_users) - len(previously_ineligible)
            processed = len(previous_users)

            embed = build_embed("Auto Gift Code Progress", {
                "Alliance": alliance_name,
                "Gift Code": giftcode,
                "Total Members": str(total_members),
                "Success": str(success),
                "Already Used": str(received),
                "Failed": str(failed),
                "Progress": f"{processed}/{total_members}",
            }, header="Processing Gift Code", color=discord.Color.blue())
            await status_message.edit(embed=embed)

            await asyncio.sleep(0)

            timeout_retry_users = []

            for member in members:
                operation_counter += 1
                if operation_counter % 10 == 0:
                    await asyncio.sleep(0)

                player_id = member[0]
                nickname = "Unknown"
                try:
                    users_db = DatabaseManager.instance().get("users")
                    cursor = users_db.cursor()
                    cursor.execute("SELECT nickname FROM users WHERE fid = ?", (player_id,))
                    row = cursor.fetchone()
                    nickname = row[0] if row else "Unknown"

                    if player_id in previous_users:
                        if player_id in previously_ineligible:
                            ineligible_users.append(nickname)
                        else:
                            already_used_users.append(nickname)
                        continue

                    response_status = await self.claimer.claim_giftcode_rewards_wos(player_id, giftcode)

                    logger.info(f"{nickname} - {response_status}")

                    if response_status == "SUCCESS":
                        success += 1
                        processed += 1
                        successful_users.append(nickname)
                        try:
                            conn.execute("""
                                INSERT INTO user_giftcodes (fid, giftcode, status)
                                VALUES (?, ?, ?)
                            """, (player_id, giftcode, response_status))
                            conn.commit()
                        except sqlite3.IntegrityError:
                            logger.debug("Duplicate giftcode record for fid=%s code=%s, skipping", player_id, giftcode)
                    elif response_status in ["RECEIVED", "SAME TYPE EXCHANGE"]:
                        received += 1
                        processed += 1
                        already_used_users.append(nickname)
                        try:
                            conn.execute("""
                                INSERT INTO user_giftcodes (fid, giftcode, status)
                                VALUES (?, ?, ?)
                            """, (player_id, giftcode, response_status))
                            conn.commit()
                        except sqlite3.IntegrityError:
                            logger.debug("Duplicate giftcode record for fid=%s code=%s, skipping", player_id, giftcode)
                    elif response_status in RECHECK_STATUSES:
                        # Code demands a recharge/VIP tier this member lacks.
                        # Already cached by the claimer; the retry loop looks at
                        # them again once a day.
                        processed += 1
                        ineligible_users.append(nickname)
                    elif response_status == "TIMEOUT_RETRY":
                        timeout_retry_users.append((player_id, nickname))
                    else:
                        failed += 1
                        processed += 1
                        failed_users.append((player_id, nickname))

                    embed = build_embed("Auto Gift Code Progress", {
                        "Alliance": alliance_name,
                        "Gift Code": giftcode,
                        "Total Members": str(total_members),
                        "Success": str(success),
                        "Already Used": str(received),
                        "Failed": str(failed),
                        "Progress": f"{processed}/{total_members}",
                    }, header="Processing Gift Code", color=discord.Color.blue())
                    await status_message.edit(embed=embed)

                except Exception as e:
                    logger.error(f"Error processing member {player_id} ({nickname}): {e}")
                    failed += 1
                    processed += 1
                    failed_users.append((player_id, nickname))
                    await status_message.edit(embed=embed)

            await asyncio.sleep(0)

            if timeout_retry_users:
                embed = build_embed("Auto Gift Code Progress", {
                    "Alliance": alliance_name,
                    "Gift Code": giftcode,
                    "Total Members": str(total_members),
                    "Success": str(success),
                    "Already Used": str(received),
                    "Failed": str(failed),
                    "Progress": f"{processed}/{total_members}",
                    "Remaining Retry": str(len(timeout_retry_users)),
                }, header="Processing Timeout Retry Users", color=discord.Color.blue())
                await status_message.edit(embed=embed)

                for player_id, nickname in timeout_retry_users[:]:
                    operation_counter += 1
                    if operation_counter % 10 == 0:
                        await asyncio.sleep(0)

                    max_retries = 5
                    retry_attempt = 0
                    while retry_attempt < max_retries:
                        retry_attempt += 1
                        response_status = await self.claimer.claim_giftcode_rewards_wos(player_id, giftcode)

                        if response_status != "TIMEOUT_RETRY":
                            if response_status == "SUCCESS":
                                success += 1
                                processed += 1
                                try:
                                    conn.execute("""
                                        INSERT OR REPLACE INTO user_giftcodes (fid, giftcode, status)
                                        VALUES (?, ?, ?)
                                    """, (player_id, giftcode, response_status))
                                    conn.commit()
                                except Exception as e:
                                    logger.error(f"DATABASE ERROR: {e}")
                            elif response_status in ["RECEIVED", "SAME TYPE EXCHANGE"]:
                                received += 1
                                processed += 1
                                try:
                                    conn.execute("""
                                        INSERT OR REPLACE INTO user_giftcodes (fid, giftcode, status)
                                        VALUES (?, ?, ?)
                                    """, (player_id, giftcode, response_status))
                                    conn.commit()
                                except Exception as e:
                                    logger.error(f"DATABASE ERROR: {e}")
                            else:
                                failed += 1
                                processed += 1

                            timeout_retry_users.remove((player_id, nickname))
                            break

                        embed = build_embed("Auto Gift Code Progress", {
                            "Alliance": alliance_name,
                            "Gift Code": giftcode,
                            "Total Members": str(total_members),
                            "Success": str(success),
                            "Already Used": str(received),
                            "Failed": str(failed),
                            "Progress": f"{processed}/{total_members}",
                            "Remaining Retry": str(len(timeout_retry_users)),
                        }, header="Processing Timeout Retry Users", color=discord.Color.blue())
                        await status_message.edit(embed=embed)
                        await asyncio.sleep(5)
                    else:
                        # Max retries exceeded -- mark as failed
                        logger.warning(f"Max retries exceeded for player {player_id} on giftcode {giftcode}")
                        failed += 1
                        processed += 1
                        timeout_retry_users.remove((player_id, nickname))
                        failed_users.append((player_id, nickname))
                        logger.warning(f"{nickname} - FAILED: Max timeout retries exceeded")

            log_dir = 'giftcode_logs'
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)

            log_filename = f"{giftcode}-{alliance_name}.txt"
            log_path = os.path.join(log_dir, log_filename)

            with open(log_path, 'w', encoding='utf-8') as log_file:
                log_file.write(f"Alliance: {alliance_name}\n")
                log_file.write(f"Total Members: {total_members}\n")
                log_file.write(f"Successful: {success}\n")
                log_file.write(f"Already Used: {received}\n")
                log_file.write(f"Not Eligible (recharge/VIP): {len(ineligible_users)}\n")
                log_file.write(f"Failed: {failed}\n\n")

                log_file.write(f"Successful Users ({len(successful_users)})\n")
                log_file.write("------------------------\n")
                for user in successful_users:
                    log_file.write(f"{user}\n")
                log_file.write("\n")

                log_file.write(f"Already Used ({len(already_used_users)})\n")
                log_file.write("------------------------\n")
                for user in already_used_users:
                    log_file.write(f"{user}\n")
                log_file.write("\n")

                log_file.write(f"Failed Users ({len(failed_users)})\n")
                log_file.write("------------------------\n")
                for _, user in failed_users:
                    log_file.write(f"{user}\n")

            failed_list = ""
            if failed_users:
                failed_list = "\n**Failed Users:**\n" + "\n".join(f"  - `{name}`" for _, name in failed_users) + "\n"

            summary = {
                "Alliance": alliance_name,
                "Gift Code": giftcode,
                "Total Members": str(total_members),
                "Success": str(success),
                "Already Used": str(received),
                "Failed": str(failed),
                "Progress": f"{processed}/{total_members}",
            }
            if ineligible_users:
                summary["Not Eligible (recharge/VIP)"] = str(len(ineligible_users))
            embed = build_embed("Gift Code Process Complete", summary,
                                header="Gift Code Distribution Complete", color=discord.Color.green())
            if failed_list:
                # Insert failed user list before the closing separator
                lines = embed.description.rsplit("\n", 2)
                embed.description = lines[0] + "\n" + failed_list + "\n" + lines[-1]

            if failed_users:
                # Import here to avoid circular import
                from .gift_views import RetryFailedView
                view = RetryFailedView(self, failed_users, giftcode, alliance_name, channel)
                await status_message.edit(embed=embed, view=view)
            else:
                await status_message.edit(embed=embed)

            return True

        except Exception as e:
            logger.error(f"Error in use_giftcode_for_alliance: {str(e)}")
            return False
