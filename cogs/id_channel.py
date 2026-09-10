import discord
from discord.ext import commands
import sqlite3
from datetime import datetime, timedelta, timezone
import asyncio
from discord.ext import tasks

from .config import LEVEL_MAPPING
from .database import DatabaseManager
from .log_config import get_logger
from .utils import build_embed, check_admin
from .wos_api import fetch_player_info, resolve_kingdom

logger = get_logger("id_channel")

class IDChannel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.setup_database()
        self.message_listeners = {}
        self.level_mapping = LEVEL_MAPPING

    def setup_database(self):
        conn = DatabaseManager.instance().get("id_channel")
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS id_channels
                     (guild_id INTEGER,
                      alliance_id INTEGER,
                      channel_id INTEGER,
                      created_at TEXT,
                      created_by INTEGER,
                      UNIQUE(guild_id, channel_id))''')
        conn.commit()

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            db = DatabaseManager.instance().get("id_channel")
            cursor = db.cursor()
            cursor.execute("SELECT channel_id, alliance_id FROM id_channels")
            channels = cursor.fetchall()

            invalid_channels = []
            for channel_id, alliance_id in channels:
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    invalid_channels.append(channel_id)
                    continue

                async for message in channel.history(limit=200, after=datetime.now(timezone.utc) - timedelta(days=1)):
                    if message.author.bot:
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

                    content = message.content.strip()
                    if not content or not content.isdigit():
                        continue

                    fid = int(content)
                    await self.process_fid(message, fid, alliance_id)

            if invalid_channels:
                db = DatabaseManager.instance().get("id_channel")
                cursor = db.cursor()
                placeholders = ','.join('?' * len(invalid_channels))
                cursor.execute(f"""
                    DELETE FROM id_channels
                    WHERE channel_id IN ({placeholders})
                """, invalid_channels)
                db.commit()

            if not self.check_channels_loop.is_running():
                self.check_channels_loop.start()

        except Exception as e:
            logger.exception("Error in IDChannel on_ready: %s", e)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        try:
            if message.author.bot or not message.guild:
                return

            db = DatabaseManager.instance().get("id_channel")
            cursor = db.cursor()
            cursor.execute("SELECT alliance_id FROM id_channels WHERE channel_id = ?", (message.channel.id,))
            channel_info = cursor.fetchone()

            if not channel_info:
                return

            alliance_id = channel_info[0]
            content = message.content.strip()

            if not content.isdigit():
                await message.add_reaction('❌')
                return

            fid = int(content)
            await self.process_fid(message, fid, alliance_id)

        except Exception as e:
            await message.add_reaction('❌')

    async def process_fid(self, message, fid, alliance_id):
        try:
            users_db = DatabaseManager.instance().get("users")
            cursor = users_db.cursor()
            cursor.execute("SELECT alliance FROM users WHERE fid = ?", (fid,))
            existing_alliance = cursor.fetchone()

            if existing_alliance:
                if existing_alliance[0] == alliance_id:
                    await message.add_reaction('⚠️')
                    await message.reply(f"This FID ({fid}) is already registered in this alliance!", delete_after=10)
                    return
                else:
                    alliance_db = DatabaseManager.instance().get("alliance")
                    alliance_cursor = alliance_db.cursor()
                    alliance_cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (existing_alliance[0],))
                    alliance_name = alliance_cursor.fetchone()

                    await message.add_reaction('⚠️')
                    await message.reply(
                        f"This FID ({fid}) is already registered in another alliance: `{alliance_name[0] if alliance_name else 'Unknown Alliance'}`",
                        delete_after=10
                    )
                    return

            max_retries = 3
            retry_delay = 60

            for attempt in range(max_retries):
                try:
                    data = await fetch_player_info(fid)

                    if data == 429:
                        if attempt < max_retries - 1:
                            warning_embed = discord.Embed(
                                title="\u26a0\ufe0f API Rate Limit Reached",
                                description=(
                                    f"Operation is on hold due to API rate limit.\n"
                                    f"**Remaining Attempts:** `{max_retries - attempt - 1}`\n"
                                    f"**Wait Time:** `60 seconds`\n\n"
                                    f"Operation will continue automatically, please wait..."
                                ),
                                color=discord.Color.orange()
                            )
                            await message.reply(embed=warning_embed)
                            await asyncio.sleep(retry_delay)
                            continue
                        else:
                            await message.add_reaction('\u274c')
                            await message.reply("Operation failed due to API rate limit. Please try again later.", delete_after=10)
                            return

                    # /api/player was removed upstream (2026-07): if no live data,
                    # auto-detect the player's kingdom via the gift-code oracle and
                    # register a placeholder (nickname=FID) so the member is still
                    # added and eligible for gift codes. Real data is used if the
                    # endpoint ever returns.
                    if not (isinstance(data, dict) and data.get('data')):
                        from .regions import candidate_kids, add_region_if_missing
                        cand = candidate_kids(alliance_id)
                        pkid = await resolve_kingdom(fid, cand) if cand else None
                        if pkid:
                            add_region_if_missing(alliance_id, pkid)
                            logger.info("id_channel: auto-detected kid=%s for FID %s (player API down)", pkid, fid)
                            data = {"data": {"nickname": str(fid), "stove_lv": 0,
                                             "stove_lv_content": None, "kid": pkid,
                                             "avatar_image": None}}

                    if isinstance(data, dict) and data.get('data'):
                        nickname = data['data'].get('nickname')
                        furnace_lv = data['data'].get('stove_lv', 0)
                        stove_lv_content = data['data'].get('stove_lv_content', None)
                        kid = data['data'].get('kid', None)
                        avatar_image = data['data'].get('avatar_image', None)

                        try:
                            users_db = DatabaseManager.instance().get("users")
                            users_cur = users_db.cursor()
                            users_cur.execute("SELECT alliance FROM users WHERE fid = ?", (fid,))
                            if users_cur.fetchone():
                                await message.add_reaction('\u26a0\ufe0f')
                                await message.reply(f"This FID ({fid}) was added by another process!", delete_after=10)
                                return

                            users_cur.execute("""
                                INSERT INTO users (fid, nickname, furnace_lv, kid, stove_lv_content, alliance)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (fid, nickname, furnace_lv, kid, stove_lv_content, alliance_id))
                            users_db.commit()
                        except sqlite3.IntegrityError:
                            await message.add_reaction('\u26a0\ufe0f')
                            await message.reply(f"This FID ({fid}) was added by another process!", delete_after=10)
                            return

                        await message.add_reaction('\u2705')

                        if furnace_lv > 30:
                            furnace_level_name = self.level_mapping.get(furnace_lv, f"Level {furnace_lv}")
                        else:
                            furnace_level_name = f"Level {furnace_lv}"

                        success_embed = build_embed("\u2705 Member Successfully Added", {
                            "\U0001f464 Name": nickname,
                            "\U0001f194 FID": str(fid),
                            "\U0001f525 Furnace Level": furnace_level_name,
                            "\U0001f30d State": str(kid),
                        })

                        if avatar_image:
                            success_embed.set_image(url=avatar_image)
                        if isinstance(stove_lv_content, str) and stove_lv_content.startswith("http"):
                            success_embed.set_thumbnail(url=stove_lv_content)

                        await message.reply(embed=success_embed)

                        logger.info("ADD_MEMBER user=%s guild=%s fid=%s nickname=%s alliance=%s furnace=%s",
                                    message.author.id, message.guild.id, fid, nickname, alliance_id, furnace_level_name)
                        return
                    else:
                        await message.add_reaction('\u274c')
                        await message.reply(
                            "Could not add this FID \u2014 its kingdom could not be detected "
                            "(the FID may be wrong, or in a kingdom none of your members are in). "
                            "Add it manually with a region via Add Member.",
                            delete_after=15,
                        )
                        return

                except Exception as e:
                    if attempt < max_retries - 1:
                        continue
                    else:
                        await message.add_reaction('❌')
                        await message.reply("An error occurred during the process!", delete_after=10)
                        return

        except Exception as e:
            await message.add_reaction('❌')
            await message.reply("An error occurred during the process!", delete_after=10)

    @tasks.loop(seconds=300)
    async def check_channels_loop(self):
        try:
            db = DatabaseManager.instance().get("id_channel")
            cursor = db.cursor()
            cursor.execute("SELECT channel_id, alliance_id FROM id_channels")
            channels = cursor.fetchall()

            current_time = datetime.now(timezone.utc)
            five_minutes_ago = current_time.timestamp() - 300

            for channel_id, alliance_id in channels:
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    continue

                messages_to_check = []
                async for message in channel.history(limit=50):
                    if message.created_at.timestamp() < five_minutes_ago:
                        has_bot_reaction = False
                        for reaction in message.reactions:
                            async for user in reaction.users():
                                if user == self.bot.user:
                                    has_bot_reaction = True
                                    break
                            if has_bot_reaction:
                                break

                        if not has_bot_reaction:
                            messages_to_check.append(message)

                for message in messages_to_check:
                    if message.author.bot:
                        continue

                    content = message.content.strip()
                    if not content:
                        continue

                    if not content.isdigit():
                        await message.add_reaction('❌')
                        continue

                    fid = int(content)
                    await self.process_fid(message, fid, alliance_id)

        except Exception as e:
            logger.exception("Error in check_channels_loop: %s", e)

    async def show_id_channel_menu(self, interaction: discord.Interaction):
        try:
            if not check_admin(interaction.user.id):
                await interaction.response.send_message(
                    "❌ You don't have permission to use this feature.", 
                    ephemeral=True
                )
                return

            embed = discord.Embed(
                title="🆔 ID Channel Management",
                description=(
                    "Manage your alliance ID channels here:\n\n"
                    "**Available Operations**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "➕ Create new ID channel\n"
                    "🗑️ Delete existing ID channel\n"
                    "📋 View active ID channels\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=discord.Color.blue()
            )
            
            view = IDChannelView(self)
            
            try:
                await interaction.response.edit_message(embed=embed, view=view)
            except discord.InteractionResponded:
                logger.debug("InteractionResponded in show_id_channel_menu, ignoring")

        except Exception as e:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred. Please try again.",
                    ephemeral=True
                )

    async def start_channel_listener(self, channel_id: int, alliance_id: int):
        if channel_id in self.message_listeners:
            self.bot.remove_listener(self.message_listeners[channel_id])
            del self.message_listeners[channel_id]

    async def stop_channel_listener(self, channel_id: int):
        if channel_id in self.message_listeners:
            self.bot.remove_listener(self.message_listeners[channel_id])
            del self.message_listeners[channel_id]

class IDChannelView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=300)
        self.cog = cog

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except Exception as e:
            logger.debug("Failed to edit message on timeout: %s", e)

    @discord.ui.button(
        label="View Channels",
        emoji="📋",
        style=discord.ButtonStyle.secondary,
        custom_id="view_id_channels",
        row=1
    )
    async def view_channels_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            channels = []
            db = DatabaseManager.instance().get("id_channel")
            cursor = db.cursor()
            cursor.execute("""
                SELECT channel_id, alliance_id, created_at, created_by
                FROM id_channels
                WHERE guild_id = ?
            """, (interaction.guild_id,))
            id_channels = cursor.fetchall()

            alliance_db = DatabaseManager.instance().get("alliance")
            alliance_cursor = alliance_db.cursor()
            for channel_id, alliance_id, created_at, created_by in id_channels:
                alliance_cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                alliance_name = alliance_cursor.fetchone()
                if alliance_name:
                    channels.append((channel_id, alliance_name[0], created_at, created_by))

            if not channels:
                await interaction.response.send_message(
                    "❌ No active ID channels found in this server.",
                    ephemeral=True
                )
                return

            embed = discord.Embed(
                title="📋 Active ID Channels",
                color=discord.Color.blue()
            )

            for channel_id, alliance_name, created_at, created_by in channels:
                channel = interaction.guild.get_channel(channel_id)
                if channel:
                    creator = None
                    try:
                        creator = await interaction.guild.fetch_member(created_by)
                    except (discord.HTTPException, discord.NotFound):
                        try:
                            creator = await interaction.client.fetch_user(created_by)
                        except (discord.HTTPException, discord.NotFound):
                            logger.debug("Could not fetch creator user %s", created_by)

                    creator_text = creator.mention if creator else f"Unknown (ID: {created_by})"
                    
                    embed.add_field(
                        name=f"#{channel.name}",
                        value=f"**Alliance:** {alliance_name}\n"
                              f"**Created At:** {created_at}\n"
                              f"**Created By:** {creator_text}",
                        inline=False
                    )

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(
                "❌ An error occurred. Please try again.",
                ephemeral=True
            )

    @discord.ui.button(
        label="Delete Channel",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="delete_id_channel",
        row=0
    )
    async def delete_channel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            channels = []
            db = DatabaseManager.instance().get("id_channel")
            cursor = db.cursor()
            cursor.execute("SELECT channel_id, alliance_id FROM id_channels WHERE guild_id = ?", (interaction.guild_id,))
            id_channels = cursor.fetchall()

            alliance_db = DatabaseManager.instance().get("alliance")
            alliance_cursor = alliance_db.cursor()
            for channel_id, alliance_id in id_channels:
                alliance_cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                alliance_name = alliance_cursor.fetchone()
                if alliance_name:
                    channels.append((channel_id, alliance_name[0]))

            if not channels:
                await interaction.response.send_message(
                    "❌ No active ID channels found in this server.",
                    ephemeral=True
                )
                return

            options = []
            for channel_id, alliance_name in channels:
                channel = interaction.guild.get_channel(channel_id)
                if channel:
                    options.append(
                        discord.SelectOption(
                            label=f"#{channel.name}",
                            value=str(channel_id),
                            description=f"Alliance: {alliance_name}"
                        )
                    )

            class ChannelSelect(discord.ui.Select):
                def __init__(self):
                    super().__init__(
                        placeholder="Select ID channel to delete",
                        options=options,
                        custom_id="delete_channel_select"
                    )

                async def callback(self, select_interaction: discord.Interaction):
                    try:
                        channel_id = int(self.values[0])

                        await self.view.cog.stop_channel_listener(channel_id)

                        db = DatabaseManager.instance().get("id_channel")
                        cursor = db.cursor()
                        cursor.execute("DELETE FROM id_channels WHERE channel_id = ?", (channel_id,))
                        db.commit()

                        channel = select_interaction.guild.get_channel(channel_id)
                        
                        logger.info("DELETE_CHANNEL user=%s guild=%s channel=%s(%s)",
                                    select_interaction.user.id, select_interaction.guild_id,
                                    channel.name if channel else "Unknown", channel_id)

                        success_embed = discord.Embed(
                            title="✅ ID Channel Deleted",
                            description=f"**Channel:** {channel.mention if channel else 'Deleted Channel'}\n\n"
                                      f"This channel will no longer be used as an ID channel.",
                            color=discord.Color.green()
                        )
                        
                        if not select_interaction.response.is_done():
                            await select_interaction.response.edit_message(embed=success_embed, view=None)
                        else:
                            await select_interaction.message.edit(embed=success_embed, view=None)
                            
                    except Exception as e:
                        error_embed = discord.Embed(
                            title="❌ Error",
                            description="An error occurred while deleting the channel.",
                            color=discord.Color.red()
                        )
                        if not select_interaction.response.is_done():
                            await select_interaction.response.edit_message(embed=error_embed, view=None)
                        else:
                            await select_interaction.message.edit(embed=error_embed, view=None)

            view = discord.ui.View()
            view.cog = self.cog
            view.add_item(ChannelSelect())
            
            select_embed = discord.Embed(
                title="🗑️ Delete ID Channel",
                description="Select the ID channel you want to delete:",
                color=discord.Color.red()
            )
            
            await interaction.response.send_message(
                embed=select_embed,
                view=view,
                ephemeral=True
            )

        except Exception as e:
            await interaction.response.send_message(
                "❌ An error occurred. Please try again.",
                ephemeral=True
            )

    @discord.ui.button(
        label="Create Channel",
        emoji="➕",
        style=discord.ButtonStyle.success,
        custom_id="create_id_channel",
        row=0
    )
    async def create_channel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            alliance_db = DatabaseManager.instance().get("alliance")
            cursor = alliance_db.cursor()
            cursor.execute("SELECT alliance_id, name FROM alliance_list")
            alliances = cursor.fetchall()

            if not alliances:
                await interaction.response.send_message(
                    "❌ No alliances found.", 
                    ephemeral=True
                )
                return

            options = [
                discord.SelectOption(
                    label=name,
                    value=str(alliance_id),
                    description=f"Alliance ID: {alliance_id}"
                ) for alliance_id, name in alliances
            ]

            class AllianceSelect(discord.ui.Select):
                def __init__(self):
                    super().__init__(
                        placeholder="Select an alliance",
                        options=options,
                        custom_id="alliance_select"
                    )

                async def callback(self, select_interaction: discord.Interaction):
                    alliance_id = int(self.values[0])
                    
                    class ChannelSelect(discord.ui.ChannelSelect):
                        def __init__(self):
                            super().__init__(
                                placeholder="Select a channel to use as ID channel",
                                channel_types=[discord.ChannelType.text]
                            )

                        async def callback(self, channel_interaction: discord.Interaction):
                            selected_channel = self.values[0]
                            
                            try:
                                db = DatabaseManager.instance().get("id_channel")
                                cursor = db.cursor()
                                cursor.execute("""
                                    INSERT INTO id_channels
                                    (guild_id, alliance_id, channel_id, created_at, created_by)
                                    VALUES (?, ?, ?, ?, ?)
                                """, (
                                    channel_interaction.guild_id,
                                    alliance_id,
                                    selected_channel.id,
                                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                    channel_interaction.user.id
                                ))
                                db.commit()

                                await self.view.cog.start_channel_listener(selected_channel.id, alliance_id)

                                logger.info("CREATE_CHANNEL user=%s guild=%s alliance=%s channel=%s(%s)",
                                            channel_interaction.user.id, channel_interaction.guild_id,
                                            alliance_id, selected_channel.name, selected_channel.id)

                                success_embed = discord.Embed(
                                    title="✅ ID Channel Created",
                                    description=f"**Channel:** {selected_channel.mention}\n"
                                              f"**Alliance:** {dict(alliances)[alliance_id]}\n\n"
                                              f"This channel will now automatically check and add FIDs to the alliance.",
                                    color=discord.Color.green()
                                )
                                await channel_interaction.response.edit_message(embed=success_embed, view=None)

                            except sqlite3.IntegrityError:
                                error_embed = discord.Embed(
                                    title="❌ Error",
                                    description="This channel is already being used as an ID channel!",
                                    color=discord.Color.red()
                                )
                                await channel_interaction.response.edit_message(embed=error_embed, view=None)
                            except Exception as e:
                                error_embed = discord.Embed(
                                    title="❌ Error",
                                    description="An error occurred while creating the channel.",
                                    color=discord.Color.red()
                                )
                                await channel_interaction.response.edit_message(embed=error_embed, view=None)

                    channel_view = discord.ui.View()
                    channel_view.cog = self.view.cog
                    channel_view.add_item(ChannelSelect())
                    
                    select_embed = discord.Embed(
                        title="🔧 ID Channel Setup",
                        description="Select a channel to use as ID channel:",
                        color=discord.Color.blue()
                    )
                    await select_interaction.response.edit_message(embed=select_embed, view=channel_view)

            alliance_view = discord.ui.View()
            alliance_view.cog = self.cog
            alliance_view.add_item(AllianceSelect())
            
            initial_embed = discord.Embed(
                title="🔧 ID Channel Setup",
                description="Select an alliance for the ID channel:",
                color=discord.Color.blue()
            )
            await interaction.response.send_message(
                embed=initial_embed,
                view=alliance_view,
                ephemeral=True
            )

        except Exception as e:
            await interaction.response.send_message(
                "❌ An error occurred. Please try again.",
                ephemeral=True
            )

    @discord.ui.button(
        label="Back",
        emoji="◀️",
        style=discord.ButtonStyle.secondary,
        custom_id="id_channel_back",
        row=2
    )
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            other_features_cog = self.cog.bot.get_cog("OtherFeatures")
            if other_features_cog:
                await other_features_cog.show_other_features_menu(interaction)
            else:
                await interaction.response.send_message(
                    "❌ Other Features module not found.",
                    ephemeral=True
                )
        except Exception as e:
            await interaction.response.send_message(
                "❌ An error occurred while returning to Other Features menu.",
                ephemeral=True
            )

    @discord.ui.button(
        label="Main Menu",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="id_channel_main_menu",
        row=2
    )
    async def main_menu_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            alliance_cog = self.cog.bot.get_cog("Alliance")
            if alliance_cog:
                await alliance_cog.show_main_menu(interaction)
            else:
                await interaction.response.send_message(
                    "❌ Alliance module not found.",
                    ephemeral=True
                )
        except Exception as e:
            await interaction.response.send_message(
                "❌ An error occurred while returning to main menu.",
                ephemeral=True
            )

async def setup(bot):
    await bot.add_cog(IDChannel(bot)) 