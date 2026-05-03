import discord
from discord.ext import commands
import sqlite3
from datetime import datetime, timedelta
import pytz
import os
import asyncio
import json
import urllib.parse
from .database import DatabaseManager
from .log_config import get_logger
from .bear_trap_views import BearTrapView, ChannelSelectView
from .utils import check_admin as _check_admin

logger = get_logger("bear_trap")

class BearTrap(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        self.conn = DatabaseManager.instance().get("beartime")
# Per-user embed data keyed by user ID to avoid race conditions
        # when multiple users create notifications concurrently.
        self._pending_embed_data = {}

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS bear_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                hour INTEGER NOT NULL,
                minute INTEGER NOT NULL,
                timezone TEXT NOT NULL,
                description TEXT NOT NULL,
                notification_type INTEGER NOT NULL,
                mention_type TEXT NOT NULL,
                repeat_enabled INTEGER NOT NULL DEFAULT 0,
                repeat_minutes INTEGER DEFAULT 0,
                is_enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by INTEGER NOT NULL,
                last_notification TIMESTAMP,
                next_notification TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS notification_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_id INTEGER NOT NULL,
                notification_time INTEGER NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (notification_id) REFERENCES bear_notifications(id) ON DELETE CASCADE
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS bear_notification_embeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_id INTEGER NOT NULL,
                title TEXT,
                description TEXT,
                color INTEGER,
                image_url TEXT,
                thumbnail_url TEXT,
                footer TEXT,
                author TEXT,
                mention_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (notification_id) REFERENCES bear_notifications(id) ON DELETE CASCADE
            )
        """)

        try:
            self.conn.execute("SELECT mention_message FROM bear_notification_embeds LIMIT 1")
        except sqlite3.OperationalError:
            self.conn.execute("ALTER TABLE bear_notification_embeds ADD COLUMN mention_message TEXT")

        self.conn.commit()

    async def cog_load(self):
        self.notification_task = asyncio.create_task(self.check_notifications())

    async def cog_unload(self):
        if hasattr(self, 'notification_task'):
            self.notification_task.cancel()
        self._pending_embed_data.clear()

    async def save_notification(self, guild_id: int, channel_id: int, start_date: datetime,
                              hour: int, minute: int, timezone: str, description: str,
                              created_by: int, notification_type: int, mention_type: str,
                              repeat_48h: bool, repeat_minutes: int = 0) -> int:
        try:
            embed_data = None
            notification_description = description

            if description.startswith("CUSTOM_TIMES:"):
                parts = description.split("|", 1)
                notification_description = description

                if len(parts) > 1 and "EMBED_MESSAGE:" in parts[1]:
                    embed_data = self._pending_embed_data.pop(created_by, None)
            elif "EMBED_MESSAGE:" in description:
                notification_description = "EMBED_MESSAGE:true"
                embed_data = self._pending_embed_data.pop(created_by, None)

            tz = pytz.timezone(timezone)
            naive_dt = start_date.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
                tzinfo=None
            )
            next_notification = tz.localize(naive_dt)

            cursor = self.conn.execute("""
                INSERT INTO bear_notifications
                (guild_id, channel_id, hour, minute, timezone, description, notification_type,
                mention_type, repeat_enabled, repeat_minutes, created_by, next_notification)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (guild_id, channel_id, hour, minute, timezone, notification_description, notification_type,
                  mention_type, 1 if repeat_48h else 0, repeat_minutes, created_by,
                  next_notification.isoformat()))

            notification_id = cursor.lastrowid

            if embed_data:
                await self.save_notification_embed(notification_id, embed_data)

            self.conn.commit()
            return notification_id
        except Exception as e:
            logger.error(f"Error saving notification: {e}")
            raise

    async def save_notification_embed(self, notification_id: int, embed_data: dict) -> bool:
        try:
            self.conn.execute("""
                INSERT INTO bear_notification_embeds
                (notification_id, title, description, color, image_url, thumbnail_url, footer, author, mention_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                notification_id,
                embed_data.get('title'),
                embed_data.get('description'),
                int(embed_data.get('color', discord.Color.blue().value)),
                embed_data.get('image_url'),
                embed_data.get('thumbnail_url'),
                embed_data.get('footer'),
                embed_data.get('author'),
                embed_data.get('mention_message')
            ))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error saving embed: {e}")
            return False

    async def get_notification_embed(self, notification_id: int) -> dict:
        try:
            cursor = self.conn.execute("""
                SELECT title, description, color, image_url, thumbnail_url, footer, author, mention_message
                FROM bear_notification_embeds
                WHERE notification_id = ?
            """, (notification_id,))

            result = cursor.fetchone()
            if result:
                return {
                    'title': result[0],
                    'description': result[1],
                    'color': result[2],
                    'image_url': result[3],
                    'thumbnail_url': result[4],
                    'footer': result[5],
                    'author': result[6],
                    'mention_message': result[7]
                }
            return None
        except Exception as e:
            logger.error(f"Error getting embed: {e}")
            return None

    async def check_notifications(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                # Clean stale entries to prevent memory leak
                if len(self._pending_embed_data) > 100:
                    self._pending_embed_data.clear()

                cursor = self.conn.execute("""
                    SELECT * FROM bear_notifications
                    WHERE is_enabled = 1 AND next_notification IS NOT NULL
                """)
                notifications = cursor.fetchall()

                now = datetime.now(pytz.UTC)
                for notification in notifications:
                    try:
                        await self.process_notification(notification)
                    except Exception as e:
                        logger.error(f"Error processing notification {notification[0]}: {e}")
                        continue

            except Exception as e:
                logger.error(f"Error in notification checker: {e}")

            await asyncio.sleep(5)

    async def process_notification(self, notification):
        try:
            (id, guild_id, channel_id, hour, minute, timezone, description,
             notification_type, mention_type, repeat_enabled, repeat_minutes,
             is_enabled, created_at, created_by, last_notification,
             next_notification) = notification

            if not is_enabled:
                return

            channel = self.bot.get_channel(channel_id)
            if not channel:
                self.conn.execute("""
                    UPDATE bear_notifications
                    SET is_enabled = 0
                    WHERE id = ?
                """, (id,))
                self.conn.commit()
                return

            tz = pytz.timezone(timezone)
            now = datetime.now(tz)
            next_time = datetime.fromisoformat(next_notification)
            if next_time.tzinfo is None:
                next_time = next_time.replace(tzinfo=tz)

            if next_time < now and repeat_enabled and repeat_minutes > 0:
                time_diff = (now - next_time).total_seconds() / 60
                periods_passed = int(time_diff / repeat_minutes) + 1
                next_time = next_time + timedelta(minutes=repeat_minutes * periods_passed)

                self.conn.execute("""
                    UPDATE bear_notifications
                    SET next_notification = ?
                    WHERE id = ?
                """, (next_time.isoformat(), id))
                self.conn.commit()
                return

            time_until = next_time - now
            minutes_until = time_until.total_seconds() / 60

            if time_until.total_seconds() < -0.1:
                if not repeat_enabled:
                    # Disable expired non-repeating notification so it is not polled forever
                    self.conn.execute("""
                        UPDATE bear_notifications
                        SET is_enabled = 0
                        WHERE id = ?
                    """, (id,))
                    self.conn.commit()
                return

            notification_times = []

            if notification_type == 1:
                notification_times = [30, 10, 5, 0]
            elif notification_type == 2:
                notification_times = [10, 5, 0]
            elif notification_type == 3:
                notification_times = [5, 0]
            elif notification_type == 4:
                notification_times = [5]
            elif notification_type == 5:
                notification_times = [0]
            elif notification_type == 6:
                if description.startswith("CUSTOM_TIMES:"):
                    times_str = description.split("CUSTOM_TIMES:")[1].split("|")[0]
                    if ',' in times_str:
                        notification_times = [int(t.strip()) for t in times_str.split(',') if t.strip()]
                    else:
                        notification_times = [int(t.strip()) for t in times_str.split('-') if t.strip()]
                    description = description.split("|")[1]

            should_notify = False
            current_time = None

            for notify_time in notification_times:
                time_diff = abs(minutes_until - notify_time)
                if time_diff < 0.1:
                    thirty_seconds_ago = (now - timedelta(seconds=30)).strftime('%Y-%m-%d %H:%M:%S')

                    cursor = self.conn.execute("""
                        SELECT COUNT(*) FROM notification_history
                        WHERE notification_id = ?
                        AND notification_time = ?
                        AND sent_at >= ?
                    """, (id, notify_time, thirty_seconds_ago))

                    row = cursor.fetchone()
                    count = row[0] if row else 0
                    if count == 0:
                        should_notify = True
                        current_time = notify_time
                    break

            if should_notify:
                # Record notification history and commit BEFORE sending Discord
                # messages to prevent duplicate sends if the commit were to fail later.
                current_time_str = now.strftime('%Y-%m-%d %H:%M:%S')
                self.conn.execute("""
                    INSERT INTO notification_history (notification_id, notification_time, sent_at)
                    VALUES (?, ?, ?)
                """, (id, current_time, current_time_str))

                self.conn.execute("""
                    UPDATE bear_notifications
                    SET last_notification = ?
                    WHERE id = ?
                """, (now.isoformat(), id))

                if current_time == 0:
                    if repeat_enabled and repeat_minutes > 0:
                        current_next = datetime.fromisoformat(next_notification)
                        next_time_val = current_next + timedelta(minutes=repeat_minutes)

                        self.conn.execute("""
                            UPDATE bear_notifications
                            SET next_notification = ?
                            WHERE id = ?
                        """, (next_time_val.isoformat(), id))
                    else:
                        self.conn.execute("""
                            UPDATE bear_notifications
                            SET is_enabled = 0
                            WHERE id = ?
                        """, (id,))

                self.conn.commit()

                mention_text = ""
                if mention_type == "everyone":
                    mention_text = "@everyone"
                elif mention_type.startswith("role_"):
                    role_id = int(mention_type.split("_")[1])
                    role = channel.guild.get_role(role_id)
                    if role:
                        mention_text = role.mention
                    else:
                        mention_text = f"Role {role_id}"
                elif mention_type.startswith("member_"):
                    member_id = int(mention_type.split("_")[1])
                    try:
                        member = channel.guild.get_member(member_id) or await channel.guild.fetch_member(member_id)
                    except (discord.NotFound, discord.HTTPException):
                        member = None
                    if member:
                        mention_text = member.mention
                    else:
                        mention_text = f"Member {member_id}"

                rounded_time = round(minutes_until)

                if rounded_time == 1:
                    time_unit = "minute"
                elif rounded_time < 60:
                    time_unit = "minutes"
                elif rounded_time == 60:
                    rounded_time = 1
                    time_unit = "hour"
                elif rounded_time < 1440:
                    rounded_time = round(rounded_time / 60)
                    time_unit = "hours"
                elif rounded_time == 1440:
                    rounded_time = 1
                    time_unit = "day"
                else:
                    rounded_time = round(rounded_time / 1440)
                    time_unit = "days"

                time_text = f"{rounded_time} {time_unit}"

                if "EMBED_MESSAGE:" in description:
                    try:
                        embed_data = await self.get_notification_embed(id)

                        if embed_data:
                            try:
                                embed = discord.Embed()

                                try:
                                    color_value = embed_data.get("color")
                                    if color_value is not None:
                                        embed.color = int(color_value)
                                    else:
                                        embed.color = discord.Color.blue()
                                except (ValueError, TypeError):
                                    embed.color = discord.Color.blue()

                                title = embed_data.get("title", "")
                                if title and isinstance(title, str):
                                    title = title.replace("%t", time_text)
                                    title = title.replace("{time}", time_text)
                                    if "@tag" in title:
                                        title = title.replace("@tag", mention_text)
                                    embed.title = title

                                embed_desc = embed_data.get("description", "")
                                if embed_desc and isinstance(embed_desc, str):
                                    embed_desc = embed_desc.replace("%t", time_text)
                                    embed_desc = embed_desc.replace("{time}", time_text)
                                    if "@tag" in embed_desc:
                                        embed_desc = embed_desc.replace("@tag", mention_text)
                                    embed.description = embed_desc

                                image_url = embed_data.get("image_url", "")
                                if image_url and isinstance(image_url, str) and image_url.strip() and image_url.startswith(('http://', 'https://')):
                                    embed.set_image(url=image_url)

                                thumbnail_url = embed_data.get("thumbnail_url", "")
                                if thumbnail_url and isinstance(thumbnail_url, str) and thumbnail_url.strip() and thumbnail_url.startswith(('http://', 'https://')):
                                    embed.set_thumbnail(url=thumbnail_url)

                                footer_text = embed_data.get("footer", "")
                                if footer_text and isinstance(footer_text, str):
                                    footer_text = footer_text.replace("%t", time_text)
                                    footer_text = footer_text.replace("{time}", time_text)
                                    if "@tag" in footer_text:
                                        footer_text = footer_text.replace("@tag", mention_text)
                                    embed.set_footer(text=footer_text)

                                author_text = embed_data.get("author", "")
                                if author_text and isinstance(author_text, str):
                                    author_text = author_text.replace("%t", time_text)
                                    author_text = author_text.replace("{time}", time_text)
                                    if "@tag" in author_text:
                                        author_text = author_text.replace("@tag", mention_text)
                                    embed.set_author(name=author_text)

                                if embed.to_dict():
                                    if mention_text:
                                        mention_message = embed_data.get("mention_message", "")
                                        if mention_message and "@tag" in mention_message:
                                            mention_message = mention_message.replace("@tag", mention_text)
                                            mention_message = mention_message.replace("%t", time_text)
                                            mention_message = mention_message.replace("{time}", time_text)
                                            await channel.send(mention_message)
                                        else:
                                            mention_text = mention_text.replace("%t", time_text)
                                            mention_text = mention_text.replace("{time}", time_text)
                                            await channel.send(mention_text)
                                    await channel.send(embed=embed)
                                else:
                                    if rounded_time > 0:
                                        await channel.send(f"{mention_text} ⏰ **Notification** will start in **{time_text}**!")
                                    else:
                                        await channel.send(f"{mention_text} ⏰ **Notification**")
                            except Exception as e:
                                logger.error(f"Error creating embed: {e}")
                                if rounded_time > 0:
                                    await channel.send(f"{mention_text} ⏰ **Error sending embed notification** will start in **{time_text}**!")
                                else:
                                    await channel.send(f"{mention_text} ⏰ **Error sending embed notification**")
                    except Exception as e:
                        logger.error(f"Error creating embed: {e}")
                        if rounded_time > 0:
                            await channel.send(f"{mention_text} ⏰ **Error sending embed notification** will start in **{time_text}**!")
                        else:
                            await channel.send(f"{mention_text} ⏰ **Error sending embed notification**")
                else:
                    actual_description = description
                    if description.startswith("CUSTOM_TIMES:"):
                        parts = description.split("|", 1)
                        if len(parts) > 1:
                            actual_description = parts[1]

                    if actual_description.startswith("PLAIN_MESSAGE:"):
                        actual_description = actual_description.replace("PLAIN_MESSAGE:", "", 1)

                    if "@tag" in actual_description or "%t" in actual_description or "{time}" in actual_description:
                        message = actual_description
                        if "@tag" in message:
                            message = message.replace("@tag", mention_text)
                        if "%t" in message:
                            message = message.replace("%t", time_text)
                        if "{time}" in message:
                            message = message.replace("{time}", time_text)
                        await channel.send(message)
                    else:
                        if rounded_time > 0:
                            await channel.send(f"{mention_text} ⏰ **{actual_description}** will start in **{time_text}**!")
                        else:
                            await channel.send(f"{mention_text} ⏰ **{actual_description}**")

        except Exception as e:
            logger.exception(f"Error processing notification: {e}")

    async def get_notifications(self, guild_id: int) -> list:
        try:
            cursor = self.conn.execute("""
                SELECT * FROM bear_notifications
                WHERE guild_id = ?
                ORDER BY next_notification
            """, (guild_id,))
            return cursor.fetchall()
        except Exception as e:
            logger.error(f"Error getting notifications: {e}")
            return []

    async def toggle_notification(self, notification_id: int, enabled: bool) -> bool:
        try:

            cursor = self.conn.execute("""
                SELECT is_enabled FROM bear_notifications WHERE id = ?
            """, (notification_id,))
            result = cursor.fetchone()
            if not result:
                return False

            self.conn.execute("""
                UPDATE bear_notifications
                SET is_enabled = ?
                WHERE id = ?
            """, (1 if enabled else 0, notification_id))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error toggling notification: {e}")
            return False

    def get_world_times(self):
        current_utc = datetime.now(pytz.UTC)
        times = {
            "UTC": current_utc,
            "US/Pacific": current_utc.astimezone(pytz.timezone('US/Pacific')),
            "US/Eastern": current_utc.astimezone(pytz.timezone('US/Eastern')),
            "Europe/London": current_utc.astimezone(pytz.timezone('Europe/London')),
            "Europe/Istanbul": current_utc.astimezone(pytz.timezone('Europe/Istanbul')),
            "Asia/Tokyo": current_utc.astimezone(pytz.timezone('Asia/Tokyo')),
        }
        return times
    async def show_bear_trap_menu(self, interaction: discord.Interaction):
        try:
            times = self.get_world_times()
            time_display = "\n".join([
                f"🌍 **{zone}:** {time.strftime('%H:%M:%S')}"
                for zone, time in times.items()
            ])

            embed = discord.Embed(
                title="🐻 Bear Trap System",
                description=(
                    "Configure time notification settings:\n\n"
                    "**Current World Times**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{time_display}\n\n"
                    "**Available Operations**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "⏰ **Set Time**\n"
                    "└ Configure notification time\n"
                    "└ Not just for Bear! Use it for any event:\n"
                    "   Bear - KE - Forst - CJ and everything else\n"
                    "└ Add unlimited notifications\n\n"
                    "🗑️ **Remove Notification**\n"
                    "└ Delete unwanted notifications\n\n"
                    "✅ **Enable/Disable**\n"
                    "└ Toggle notifications\n\n"
                    "📋 **View Settings**\n"
                    "└ Check current configuration\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=discord.Color.gold()
            )

            embed.set_footer(text="Last Updated")
            embed.timestamp = datetime.now()

            view = BearTrapView(self)

            try:
                await interaction.response.edit_message(embed=embed, view=view)
            except discord.InteractionResponded:
                logger.debug("InteractionResponded in show_bear_trap_menu, ignoring")

        except Exception as e:
            logger.error(f"Error in show_bear_trap_menu: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred. Please try again.",
                    ephemeral=True
                )

    async def check_admin(self, interaction: discord.Interaction) -> bool:
        try:
            if not _check_admin(interaction.user.id):
                await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
                return False
            return True
        except Exception as e:
            logger.error(f"Error in admin check: {e}")
            return False

    async def show_channel_selection(self, interaction: discord.Interaction, start_date, hour, minute, timezone, message_data, channels):
        try:
            embed = discord.Embed(
                title="📢 Select Channel",
                description=(
                    "Choose a channel to send notifications:\n\n"
                    "Select a text channel from the dropdown menu below.\n"
                    "Make sure the bot has permission to send messages in the selected channel."
                ),
                color=discord.Color.blue()
            )

            view = ChannelSelectView(
                self,
                start_date,
                hour,
                minute,
                timezone,
                message_data,
                interaction.message
            )

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=view
            )

        except Exception as e:
            logger.error(f"Error in show_channel_selection: {e}")
            await interaction.followup.send(
                "❌ An error occurred while showing channel selection!",
                ephemeral=True
            )

async def setup(bot):
    await bot.add_cog(BearTrap(bot))
