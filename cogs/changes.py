import discord
from discord.ext import commands
from datetime import datetime
from .utils import AllianceSelectView, get_admin_info as _utils_get_admin_info, check_global_admin
from .config import LEVEL_MAPPING
from .database import DatabaseManager
from .log_config import get_logger

logger = get_logger("changes")

class Changes(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        db = DatabaseManager.instance()
        self.conn_settings = db.get("settings")

        self.conn = db.get("changes")

        self._create_tables()

        self.level_mapping = LEVEL_MAPPING

    def _create_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS furnace_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fid INTEGER,
                old_furnace_lv INTEGER,
                new_furnace_lv INTEGER,
                change_date TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS nickname_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fid INTEGER,
                old_nickname TEXT,
                new_nickname TEXT,
                change_date TEXT
            )
        """)
        self.conn.commit()

    async def show_alliance_history_menu(self, interaction: discord.Interaction):
        try:
            embed = discord.Embed(
                title="📜 Alliance History Menu",
                description=(
                    "**Available Operations**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "🔥 **Furnace Changes**\n"
                    "└ View furnace level changes\n\n"
                    "📝 **Nickname Changes**\n"
                    "└ View nickname history\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=discord.Color.blue()
            )

            view = HistoryView(self)
            await interaction.response.edit_message(embed=embed, view=view)
            
        except Exception as e:
            if not any(error_code in str(e) for error_code in ["10062", "40060"]):
                logger.error(f"Show alliance history menu error: {e}")

    async def get_admin_info(self, user_id: int):
        try:
            return _utils_get_admin_info(user_id)
        except Exception as e:
            logger.error(f"Error in get_admin_info: {e}")
            return None

    async def get_admin_alliances(self, user_id: int, guild_id: int):
        try:
            admin_result = _utils_get_admin_info(user_id)

            if not admin_result:
                logger.info(f"User {user_id} is not an admin")
                return [], [], False

            is_initial = admin_result[1]

            if is_initial == 1:
                db = DatabaseManager.instance()
                alliance_db = db.get("alliance")
                cursor = alliance_db.cursor()
                cursor.execute("SELECT alliance_id, name FROM alliance_list ORDER BY name")
                alliances = cursor.fetchall()
                return alliances, [], True

            db = DatabaseManager.instance()
            settings_db = db.get("settings")
            server_alliances = []
            special_alliances = []

            alliance_db = db.get("alliance")
            cursor = alliance_db.cursor()
            cursor.execute("""
                SELECT DISTINCT alliance_id, name
                FROM alliance_list
                WHERE discord_server_id = ?
                ORDER BY name
            """, (guild_id,))
            server_alliances = cursor.fetchall()

            cursor = settings_db.cursor()
            cursor.execute("""
                SELECT alliances_id
                FROM adminserver
                WHERE admin = ?
            """, (user_id,))
            special_alliance_ids = cursor.fetchall()

            if special_alliance_ids:
                cursor = alliance_db.cursor()
                placeholders = ','.join('?' * len(special_alliance_ids))
                cursor.execute(f"""
                    SELECT DISTINCT alliance_id, name
                    FROM alliance_list
                    WHERE alliance_id IN ({placeholders})
                    ORDER BY name
                """, [aid[0] for aid in special_alliance_ids])
                special_alliances = cursor.fetchall()
            
            all_alliances = list({(aid, name) for aid, name in (server_alliances + special_alliances)})
            
            if not all_alliances and not special_alliances:
                return [], [], False
            
            return all_alliances, special_alliances, False
                
        except Exception as e:
            logger.error(f"Error in get_admin_alliances: {e}")
            return [], [], False

    async def show_furnace_history(self, interaction: discord.Interaction, fid: int):
        try:
            cursor = self.conn.execute("""
                SELECT old_furnace_lv, new_furnace_lv, change_date 
                FROM furnace_changes 
                WHERE fid = ? 
                ORDER BY change_date DESC
            """, (fid,))
            
            changes = cursor.fetchall()
            
            if not changes:
                await interaction.followup.send(
                    "No furnace changes found for this player.",
                    ephemeral=True
                )
                return

            users_db = DatabaseManager.instance().get("users")
            cursor = users_db.cursor()
            cursor.execute("SELECT nickname, furnace_lv FROM users WHERE fid = ?", (fid,))
            user_info = cursor.fetchone()
            nickname = user_info[0] if user_info else "Unknown"
            current_level = user_info[1] if user_info else 0

            embed = discord.Embed(
                title=f"🔥 Furnace Level History",
                description=(
                    f"**Player:** `{nickname}`\n"
                    f"**FID:** `{fid}`\n"
                    f"**Current Level:** `{self.level_mapping.get(current_level, str(current_level))}`\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                ),
                color=discord.Color.blue()
            )

            for old_level, new_level, change_date in changes:
                old_level_str = self.level_mapping.get(int(old_level), str(old_level))
                new_level_str = self.level_mapping.get(int(new_level), str(new_level))
                embed.add_field(
                    name=f"Level Change at {change_date}",
                    value=f"```{old_level_str} ➜ {new_level_str}```",
                    inline=False
                )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in show_furnace_history: {e}")
            await interaction.followup.send(
                "❌ An error occurred while displaying the furnace history.",
                ephemeral=True
            )

    async def show_nickname_history(self, interaction: discord.Interaction, fid: int):
        try:
            cursor = self.conn.execute("""
                SELECT old_nickname, new_nickname, change_date 
                FROM nickname_changes 
                WHERE fid = ? 
                ORDER BY change_date DESC
            """, (fid,))
            
            changes = cursor.fetchall()
            
            if not changes:
                await interaction.followup.send(
                    "No nickname changes found for this player.",
                    ephemeral=True
                )
                return

            users_db = DatabaseManager.instance().get("users")
            cursor = users_db.cursor()
            cursor.execute("SELECT nickname, furnace_lv FROM users WHERE fid = ?", (fid,))
            user_info = cursor.fetchone()
            nickname = user_info[0] if user_info else "Unknown"
            current_level = user_info[1] if user_info else 0

            embed = discord.Embed(
                title=f"📝 Nickname History",
                description=(
                    f"**Player:** `{nickname}`\n"
                    f"**FID:** `{fid}`\n"
                    f"**Current Level:** `{self.level_mapping.get(current_level, str(current_level))}`\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                ),
                color=discord.Color.blue()
            )

            for old_name, new_name, change_date in changes:
                embed.add_field(
                    name=f"Nickname Change at {change_date}",
                    value=f"```{old_name} ➜ {new_name}```",
                    inline=False
                )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in show_nickname_history: {e}")
            await interaction.followup.send(
                "❌ An error occurred while displaying the nickname history.",
                ephemeral=True
            )

    async def show_member_list_nickname(self, interaction: discord.Interaction, alliance_id: int):
        try:
            db = DatabaseManager.instance()
            alliance_db = db.get("alliance")
            cursor = alliance_db.cursor()
            cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
            row = cursor.fetchone()
            alliance_name = row[0] if row else "Unknown"

            users_db = db.get("users")
            cursor = users_db.cursor()
            cursor.execute("""
                SELECT fid, nickname, furnace_lv
                FROM users
                WHERE alliance = ?
                ORDER BY furnace_lv DESC, nickname
            """, (alliance_id,))
            members = cursor.fetchall()

            if not members:
                await interaction.response.send_message(
                    "❌ No members found in this alliance.",
                    ephemeral=True
                )
                return

            embed = discord.Embed(
                title=f"📝 {alliance_name} - Member List",
                description=(
                    "Select a member to view nickname history:\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Total Members: {len(members)}\n"
                    f"Current Page: 1/{(len(members) + 24) // 25}\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=discord.Color.blue()
            )

            view = MemberListViewNickname(self, members, alliance_name)
            
            await interaction.response.edit_message(
                embed=embed,
                view=view
            )

        except Exception as e:
            logger.error(f"Error in show_member_list_nickname: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while displaying the member list.",
                ephemeral=True
            )

    async def show_recent_changes(self, interaction: discord.Interaction, alliance_name: str, hours: int):
        try:
            db = DatabaseManager.instance()
            alliance_db = db.get("alliance")
            cursor = alliance_db.cursor()
            cursor.execute("SELECT alliance_id FROM alliance_list WHERE name = ?", (alliance_name,))
            row = cursor.fetchone()
            if not row:
                await interaction.followup.send(
                    f"❌ Alliance '{alliance_name}' not found.",
                    ephemeral=True
                )
                return
            alliance_id = row[0]

            users_db = db.get("users")
            cursor = users_db.cursor()
            cursor.execute("""
                SELECT fid, nickname
                FROM users
                WHERE alliance = ?
            """, (alliance_id,))
            members = {fid: name for fid, name in cursor.fetchall()}

            if not members:
                await interaction.followup.send(
                    f"No members found in alliance '{alliance_name}'.",
                    ephemeral=True
                )
                return

            cursor = self.conn.execute("""
                SELECT fid, old_furnace_lv, new_furnace_lv, change_date
                FROM furnace_changes
                WHERE fid IN ({})
                AND change_date >= datetime('now', '-' || ? || ' hours')
                ORDER BY change_date DESC
            """.format(','.join('?' * len(members))), (*members.keys(), hours))
            
            changes = cursor.fetchall()

            if not changes:
                await interaction.followup.send(
                    f"No level changes found in the last {hours} hour(s) for {alliance_name}.",
                    ephemeral=True
                )
                return

            chunks = [changes[i:i + 25] for i in range(0, len(changes), 25)]
            
            view = RecentChangesView(chunks, members, self.level_mapping, alliance_name, hours)
            await interaction.followup.send(embed=view.get_embed(), view=view)

        except Exception as e:
            logger.error(f"Error in show_recent_changes: {e}")
            await interaction.followup.send(
                "❌ An error occurred while showing recent changes.",
                ephemeral=True
            )

    async def show_recent_nickname_changes(self, interaction: discord.Interaction, alliance_name: str, hours: int):
        try:
            db = DatabaseManager.instance()
            alliance_db = db.get("alliance")
            cursor = alliance_db.cursor()
            cursor.execute("SELECT alliance_id FROM alliance_list WHERE name = ?", (alliance_name,))
            row = cursor.fetchone()
            if not row:
                await interaction.followup.send(
                    f"❌ Alliance '{alliance_name}' not found.",
                    ephemeral=True
                )
                return
            alliance_id = row[0]

            users_db = db.get("users")
            cursor = users_db.cursor()
            cursor.execute("""
                SELECT fid, nickname
                FROM users
                WHERE alliance = ?
            """, (alliance_id,))
            members = {fid: name for fid, name in cursor.fetchall()}

            if not members:
                await interaction.followup.send(
                    f"No members found in alliance '{alliance_name}'.",
                    ephemeral=True
                )
                return

            cursor = self.conn.execute("""
                SELECT fid, old_nickname, new_nickname, change_date
                FROM nickname_changes
                WHERE fid IN ({})
                AND change_date >= datetime('now', '-' || ? || ' hours')
                ORDER BY change_date DESC
            """.format(','.join('?' * len(members))), (*members.keys(), hours))
            
            changes = cursor.fetchall()

            if not changes:
                await interaction.followup.send(
                    f"No nickname changes found in the last {hours} hour(s) for {alliance_name}.",
                    ephemeral=True
                )
                return

            chunks = [changes[i:i + 25] for i in range(0, len(changes), 25)]
            
            view = RecentNicknameChangesView(chunks, members, alliance_name, hours)
            await interaction.followup.send(embed=view.get_embed(), view=view)

        except Exception as e:
            logger.error(f"Error in show_recent_nickname_changes: {e}")
            await interaction.followup.send(
                "❌ An error occurred while showing recent changes.",
                ephemeral=True
            )

class HistoryView(discord.ui.View):
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
        self.current_page = 0
        self.members_per_page = 25
        self.level_mapping = cog.level_mapping

    @discord.ui.button(
        label="Furnace Changes",
        emoji="🔥",
        style=discord.ButtonStyle.primary,
        custom_id="furnace_changes",
        row=0
    )
    async def furnace_changes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            admin_info = await self.cog.get_admin_info(interaction.user.id)
            if not admin_info:
                await interaction.response.send_message(
                    "❌ You don't have permission to perform this action.",
                    ephemeral=True
                )
                return

            available_alliances = await self.cog.get_admin_alliances(interaction.user.id, interaction.guild_id)
            if not available_alliances[0]:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="❌ No Available Alliance",
                        description="No alliance found that you have access to.",
                        color=discord.Color.red()
                    ),
                    ephemeral=True
                )
                return

            alliances, special_alliances, is_global = available_alliances

            alliances_with_counts = []
            users_db = DatabaseManager.instance().get("users")
            for alliance_id, name in alliances:
                cursor = users_db.cursor()
                cursor.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                row = cursor.fetchone()
                member_count = row[0] if row else 0
                alliances_with_counts.append((alliance_id, name, member_count))

            special_alliance_text = ""
            if special_alliances:
                special_alliance_text = "\n\n**Special Access Alliances**\n"
                special_alliance_text += "━━━━━━━━━━━━━━━━━━━━━━\n"
                for _, name in special_alliances:
                    special_alliance_text += f"🔸 {name}\n"
                special_alliance_text += "━━━━━━━━━━━━━━━━━━━━━━"

            select_embed = discord.Embed(
                title="🔥 Furnace Changes",
                description=(
                    "Select an alliance to view furnace changes:\n\n"
                    "**Permission Details**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 **Access Level:** `{'Global Admin' if admin_info[1] == 1 else 'Server Admin'}`\n"
                    f"🔍 **Access Type:** `{'All Alliances' if admin_info[1] == 1 else 'Server + Special Access'}`\n"
                    f"📊 **Available Alliances:** `{len(alliances)}`\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                    f"{special_alliance_text}"
                ),
                color=discord.Color.blue()
            )

            view = AllianceSelectView(alliances_with_counts, self.cog)

            async def alliance_callback(select_interaction: discord.Interaction):
                try:
                    alliance_id = int(view.current_select.values[0])
                    await self.member_callback(select_interaction, alliance_id)
                except Exception as e:
                    logger.error(f"Error in alliance selection: {e}")
                    if not select_interaction.response.is_done():
                        await select_interaction.response.send_message(
                            "❌ An error occurred while processing your selection.",
                            ephemeral=True
                        )
                    else:
                        await select_interaction.followup.send(
                            "❌ An error occurred while processing your selection.",
                            ephemeral=True
                        )

            view.callback = alliance_callback
            
            await interaction.response.send_message(
                embed=select_embed,
                view=view,
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Error in furnace_changes_button: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while processing the request.",
                ephemeral=True
            )

    async def member_callback(self, interaction: discord.Interaction, alliance_id: int):
        try:
            db = DatabaseManager.instance()
            users_db = db.get("users")
            cursor = users_db.cursor()
            cursor.execute("""
                SELECT fid, nickname, furnace_lv
                FROM users
                WHERE alliance = ?
                ORDER BY furnace_lv DESC, nickname
            """, (alliance_id,))
            members = cursor.fetchall()

            if not members:
                await interaction.response.send_message(
                    "❌ No members found in this alliance.",
                    ephemeral=True
                )
                return

            alliance_db = db.get("alliance")
            cursor = alliance_db.cursor()
            cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
            row = cursor.fetchone()
            alliance_name = row[0] if row else "Unknown"

            view = MemberListView(self.cog, members, alliance_name)
            
            embed = discord.Embed(
                title=f"🔥 {alliance_name} - Member List",
                description=(
                    "Select a member to view furnace history:\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Total Members: {len(members)}\n"
                    f"Current Page: 1/{view.total_pages}\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=discord.Color.blue()
            )

            await interaction.response.edit_message(embed=embed, view=view)

        except Exception as e:
            logger.error(f"Error in member_callback: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while showing member list.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ An error occurred while showing member list.",
                    ephemeral=True
                )

    @discord.ui.button(
        label="Nickname Changes",
        emoji="📝",
        style=discord.ButtonStyle.primary,
        custom_id="nickname_changes",
        row=0
    )
    async def nickname_changes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            admin_info = await self.cog.get_admin_info(interaction.user.id)
            if not admin_info:
                await interaction.response.send_message(
                    "❌ You don't have permission to perform this action.",
                    ephemeral=True
                )
                return

            available_alliances = await self.cog.get_admin_alliances(interaction.user.id, interaction.guild_id)
            if not available_alliances[0]:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="❌ No Available Alliance",
                        description="No alliance found that you have access to.",
                        color=discord.Color.red()
                    ),
                    ephemeral=True
                )
                return

            alliances, special_alliances, is_global = available_alliances

            alliances_with_counts = []
            users_db = DatabaseManager.instance().get("users")
            for alliance_id, name in alliances:
                cursor = users_db.cursor()
                cursor.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                row = cursor.fetchone()
                member_count = row[0] if row else 0
                alliances_with_counts.append((alliance_id, name, member_count))

            special_alliance_text = ""
            if special_alliances:
                special_alliance_text = "\n\n**Special Access Alliances**\n"
                special_alliance_text += "━━━━━━━━━━━━━━━━━━━━━━\n"
                for _, name in special_alliances:
                    special_alliance_text += f"🔸 {name}\n"
                special_alliance_text += "━━━━━━━━━━━━━━━━━━━━━━"

            select_embed = discord.Embed(
                title="📝 Alliance Selection - Nickname Changes",
                description=(
                    "Select an alliance to view nickname changes:\n\n"
                    "**Permission Details**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 **Access Level:** `{'Global Admin' if admin_info[1] == 1 else 'Server Admin'}`\n"
                    f"🔍 **Access Type:** `{'All Alliances' if admin_info[1] == 1 else 'Server + Special Access'}`\n"
                    f"📊 **Available Alliances:** `{len(alliances)}`\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                    f"{special_alliance_text}"
                ),
                color=discord.Color.blue()
            )

            view = AllianceSelectView(alliances_with_counts, self.cog)

            async def alliance_callback(select_interaction: discord.Interaction):
                try:
                    alliance_id = int(view.current_select.values[0])
                    await self.cog.show_member_list_nickname(select_interaction, alliance_id)
                except Exception as e:
                    logger.error(f"Error in alliance selection: {e}")
                    if not select_interaction.response.is_done():
                        await select_interaction.response.send_message(
                            "❌ An error occurred while processing your selection.",
                            ephemeral=True
                        )
                    else:
                        await select_interaction.followup.send(
                            "❌ An error occurred while processing your selection.",
                            ephemeral=True
                        )

            view.callback = alliance_callback
            
            await interaction.response.send_message(
                embed=select_embed,
                view=view,
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Error in nickname_changes_button: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while processing the request.",
                ephemeral=True
            )

    @discord.ui.button(
        label="Main Menu",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="changes_main_menu",
        row=1
    )
    async def main_menu_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_main_menu(interaction)

    async def show_main_menu(self, interaction: discord.Interaction):
        try:
            alliance_cog = self.cog.bot.get_cog("Alliance")
            if alliance_cog:
                await alliance_cog.show_main_menu(interaction)
            else:
                await interaction.response.send_message(
                    "❌ An error occurred while returning to the main menu.",
                    ephemeral=True
                )
        except Exception as e:
            logger.error(f"Main Menu error in changes: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while returning to the main menu.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "An error occurred while returning to the main menu.",
                    ephemeral=True
                )

    async def last_hour_callback(self, interaction: discord.Interaction):
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
            await self.cog.show_recent_changes(interaction, self.alliance_name, hours=1)
        except Exception as e:
            logger.error(f"Error in last_hour_callback: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while showing recent changes.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ An error occurred while showing recent changes.",
                    ephemeral=True
                )

    async def last_day_callback(self, interaction: discord.Interaction):
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
            await self.cog.show_recent_changes(interaction, self.alliance_name, hours=24)
        except Exception as e:
            logger.error(f"Error in last_day_callback: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while showing recent changes.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ An error occurred while showing recent changes.",
                    ephemeral=True
                )

    async def custom_time_callback(self, interaction: discord.Interaction):
        try:
            modal = CustomTimeModal(self.cog, self.alliance_name)
            await interaction.response.send_modal(modal)
        except Exception as e:
            logger.error(f"Error in custom_time_callback: {e}")
            await interaction.followup.send(
                "❌ An error occurred while showing the time input.",
                ephemeral=True
            )

class MemberListView(discord.ui.View):
    def __init__(self, cog, members, alliance_name):
        super().__init__()
        self.cog = cog
        self.members = members
        self.alliance_name = alliance_name
        self.current_page = 0
        self.total_pages = (len(members) + 24) // 25
        self.update_view()

    def update_view(self):
        self.clear_items()
        
        start_idx = self.current_page * 25
        end_idx = min(start_idx + 25, len(self.members))
        current_members = self.members[start_idx:end_idx]

        select = discord.ui.Select(
            placeholder=f"Select a member (Page {self.current_page + 1}/{self.total_pages})",
            options=[
                discord.SelectOption(
                    label=f"{name}",
                    value=str(fid),
                    description=f"FID: {fid} | Level: {self.cog.level_mapping.get(furnace_lv, str(furnace_lv))}"
                ) for fid, name, furnace_lv in current_members
            ],
            row=0
        )

        async def member_callback(interaction):
            try:
                fid = int(select.values[0])
                await interaction.response.defer()
                await self.cog.show_furnace_history(interaction, fid)
            except Exception as e:
                logger.error(f"Error in member_callback: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred while showing furnace history.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "❌ An error occurred while showing furnace history.",
                        ephemeral=True
                    )

        select.callback = member_callback
        self.add_item(select)

        last_hour_button = discord.ui.Button(
            label="Last Hour Changes",
            emoji="⏰",
            style=discord.ButtonStyle.primary,
            custom_id="last_hour",
            row=1
        )
        last_hour_button.callback = self.last_hour_callback
        self.add_item(last_hour_button)

        last_day_button = discord.ui.Button(
            label="Last 24h Changes",
            emoji="📅",
            style=discord.ButtonStyle.primary,
            custom_id="last_day",
            row=1
        )
        last_day_button.callback = self.last_day_callback
        self.add_item(last_day_button)

        custom_time_button = discord.ui.Button(
            label="Custom Time",
            emoji="⚙️",
            style=discord.ButtonStyle.primary,
            custom_id="custom_time",
            row=1
        )
        custom_time_button.callback = self.custom_time_callback
        self.add_item(custom_time_button)

        if self.total_pages > 1:
            previous_button = discord.ui.Button(
                label="Previous",
                emoji="⬅️",
                style=discord.ButtonStyle.secondary,
                custom_id="previous",
                disabled=self.current_page == 0,
                row=2
            )
            previous_button.callback = self.previous_callback
            self.add_item(previous_button)

            next_button = discord.ui.Button(
                label="Next",
                emoji="➡️",
                style=discord.ButtonStyle.secondary,
                custom_id="next",
                disabled=self.current_page == self.total_pages - 1,
                row=2
            )
            next_button.callback = self.next_callback
            self.add_item(next_button)

        search_button = discord.ui.Button(
            label="Search by FID",
            emoji="🔍",
            style=discord.ButtonStyle.primary,
            custom_id="search_fid",
            row=2
        )
        search_button.callback = self.search_callback
        self.add_item(search_button)

    async def last_hour_callback(self, interaction: discord.Interaction):
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
            await self.cog.show_recent_changes(interaction, self.alliance_name, hours=1)
        except Exception as e:
            logger.error(f"Error in last_hour_callback: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while showing recent changes.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ An error occurred while showing recent changes.",
                    ephemeral=True
                )

    async def last_day_callback(self, interaction: discord.Interaction):
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
            await self.cog.show_recent_changes(interaction, self.alliance_name, hours=24)
        except Exception as e:
            logger.error(f"Error in last_day_callback: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while showing recent changes.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ An error occurred while showing recent changes.",
                    ephemeral=True
                )

    async def custom_time_callback(self, interaction: discord.Interaction):
        try:
            modal = CustomTimeModal(self.cog, self.alliance_name)
            await interaction.response.send_modal(modal)
        except Exception as e:
            logger.error(f"Error in custom_time_callback: {e}")
            await interaction.followup.send(
                "❌ An error occurred while showing the time input.",
                ephemeral=True
            )

    async def previous_callback(self, interaction: discord.Interaction):
        self.current_page = max(0, self.current_page - 1)
        await self.update_page(interaction)

    async def next_callback(self, interaction: discord.Interaction):
        self.current_page = min(self.total_pages - 1, self.current_page + 1)
        await self.update_page(interaction)

    async def search_callback(self, interaction: discord.Interaction):
        modal = FIDSearchModal(self.cog)
        await interaction.response.send_modal(modal)

    async def update_page(self, interaction: discord.Interaction):
        self.update_view()
        
        embed = discord.Embed(
            title=f"🔥 {self.alliance_name} - Member List",
            description=(
                "Select a member to view furnace history:\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Total Members: {len(self.members)}\n"
                f"Current Page: {self.current_page + 1}/{self.total_pages}\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.blue()
        )

        await interaction.response.edit_message(embed=embed, view=self)

class FIDSearchModal(discord.ui.Modal, title="Search by FID"):
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
        self.fid = discord.ui.TextInput(
            label="FID",
            placeholder="Enter FID number...",
            required=True,
            min_length=1,
            max_length=20
        )
        self.add_item(self.fid)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            fid = int(self.fid.value)
            await interaction.response.defer()
            await self.cog.show_furnace_history(interaction, fid)
                
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid FID format. Please enter a valid number.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in FIDSearchModal on_submit: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while searching for the player.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ An error occurred while searching for the player.",
                    ephemeral=True
                )

class MemberListViewNickname(discord.ui.View):
    def __init__(self, cog, members, alliance_name):
        super().__init__()
        self.cog = cog
        self.members = members
        self.alliance_name = alliance_name
        self.current_page = 0
        self.total_pages = (len(members) + 24) // 25
        self.update_view()

    def update_view(self):
        self.clear_items()
        
        start_idx = self.current_page * 25
        end_idx = min(start_idx + 25, len(self.members))
        current_members = self.members[start_idx:end_idx]

        select = discord.ui.Select(
            placeholder=f"Select a member (Page {self.current_page + 1}/{self.total_pages})",
            options=[
                discord.SelectOption(
                    label=f"{name}",
                    value=str(fid),
                    description=f"FID: {fid} | Level: {self.cog.level_mapping.get(furnace_lv, str(furnace_lv))}"
                ) for fid, name, furnace_lv in current_members
            ],
            row=0
        )

        async def member_callback(interaction):
            try:
                fid = int(select.values[0])
                await interaction.response.defer()
                await self.cog.show_nickname_history(interaction, fid)
            except Exception as e:
                logger.error(f"Error in member_callback: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred while showing nickname history.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "❌ An error occurred while showing nickname history.",
                        ephemeral=True
                    )

        select.callback = member_callback
        self.add_item(select)

        last_hour_button = discord.ui.Button(
            label="Last Hour Changes",
            emoji="⏰",
            style=discord.ButtonStyle.primary,
            custom_id="last_hour_nick",
            row=1
        )
        last_hour_button.callback = self.last_hour_callback
        self.add_item(last_hour_button)

        last_day_button = discord.ui.Button(
            label="Last 24h Changes",
            emoji="📅",
            style=discord.ButtonStyle.primary,
            custom_id="last_day_nick",
            row=1
        )
        last_day_button.callback = self.last_day_callback
        self.add_item(last_day_button)

        custom_time_button = discord.ui.Button(
            label="Custom Time",
            emoji="⚙️",
            style=discord.ButtonStyle.primary,
            custom_id="custom_time_nick",
            row=1
        )
        custom_time_button.callback = self.custom_time_callback
        self.add_item(custom_time_button)

        if self.total_pages > 1:
            previous_button = discord.ui.Button(
                label="Previous",
                emoji="⬅️",
                style=discord.ButtonStyle.secondary,
                custom_id="previous_nick",
                disabled=self.current_page == 0,
                row=2
            )
            previous_button.callback = self.previous_callback
            self.add_item(previous_button)

            next_button = discord.ui.Button(
                label="Next",
                emoji="➡️",
                style=discord.ButtonStyle.secondary,
                custom_id="next_nick",
                disabled=self.current_page == self.total_pages - 1,
                row=2
            )
            next_button.callback = self.next_callback
            self.add_item(next_button)

        search_button = discord.ui.Button(
            label="Search by FID",
            emoji="🔍",
            style=discord.ButtonStyle.primary,
            custom_id="search_fid_nick",
            row=2
        )
        search_button.callback = self.search_callback
        self.add_item(search_button)

    async def last_hour_callback(self, interaction: discord.Interaction):
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
            await self.cog.show_recent_nickname_changes(interaction, self.alliance_name, hours=1)
        except Exception as e:
            logger.error(f"Error in last_hour_callback: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while showing recent changes.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ An error occurred while showing recent changes.",
                    ephemeral=True
                )

    async def last_day_callback(self, interaction: discord.Interaction):
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
            await self.cog.show_recent_nickname_changes(interaction, self.alliance_name, hours=24)
        except Exception as e:
            logger.error(f"Error in last_day_callback: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while showing recent changes.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ An error occurred while showing recent changes.",
                    ephemeral=True
                )

    async def custom_time_callback(self, interaction: discord.Interaction):
        try:
            modal = CustomTimeModalNickname(self.cog, self.alliance_name)
            await interaction.response.send_modal(modal)
        except Exception as e:
            logger.error(f"Error in custom_time_callback: {e}")
            await interaction.followup.send(
                "❌ An error occurred while showing the time input.",
                ephemeral=True
            )

    async def previous_callback(self, interaction: discord.Interaction):
        self.current_page = max(0, self.current_page - 1)
        await self.update_page(interaction)

    async def next_callback(self, interaction: discord.Interaction):
        self.current_page = min(self.total_pages - 1, self.current_page + 1)
        await self.update_page(interaction)

    async def search_callback(self, interaction: discord.Interaction):
        modal = FIDSearchModalNickname(self.cog)
        await interaction.response.send_modal(modal)

    async def update_page(self, interaction: discord.Interaction):
        self.update_view()
        
        embed = discord.Embed(
            title=f"📝 {self.alliance_name} - Member List",
            description=(
                "Select a member to view nickname history:\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Total Members: {len(self.members)}\n"
                f"Current Page: {self.current_page + 1}/{self.total_pages}\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.blue()
        )

        await interaction.response.edit_message(embed=embed, view=self)

class FIDSearchModalNickname(discord.ui.Modal, title="Search by FID"):
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
        self.fid = discord.ui.TextInput(
            label="FID",
            placeholder="Enter FID number...",
            required=True,
            min_length=1,
            max_length=20
        )
        self.add_item(self.fid)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            fid = int(self.fid.value)
            await interaction.response.defer()
            await self.cog.show_nickname_history(interaction, fid)
                
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid FID format. Please enter a valid number.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in FIDSearchModalNickname on_submit: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while searching for the player.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ An error occurred while searching for the player.",
                    ephemeral=True
                )

class CustomTimeModal(discord.ui.Modal, title="Custom Time Range"):
    def __init__(self, cog, alliance_name):
        super().__init__()
        self.cog = cog
        self.alliance_name = alliance_name
        self.hours = discord.ui.TextInput(
            label="Hours (1-24)",
            placeholder="Enter number of hours (max 24)...",
            required=True,
            min_length=1,
            max_length=2
        )
        self.add_item(self.hours)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            hours = int(self.hours.value)
            if hours < 1 or hours > 24:
                await interaction.response.send_message(
                    "❌ Please enter a number between 1 and 24.",
                    ephemeral=True
                )
                return

            await interaction.response.defer()
            await self.cog.show_recent_changes(interaction, self.alliance_name, hours)

        except ValueError:
            await interaction.response.send_message(
                "❌ Please enter a valid number.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in CustomTimeModal on_submit: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while processing your request.",
                ephemeral=True
            )

class RecentChangesView(discord.ui.View):
    def __init__(self, chunks, members, level_mapping, alliance_name, hours):
        super().__init__()
        self.chunks = chunks
        self.members = members
        self.level_mapping = level_mapping
        self.alliance_name = alliance_name
        self.hours = hours
        self.current_page = 0
        self.total_pages = len(chunks)
        
        self.update_buttons()

    def get_embed(self):
        embed = discord.Embed(
            title=f"🔥 Recent Level Changes - {self.alliance_name}",
            description=(
                f"Showing changes in the last {self.hours} hour(s)\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Total Changes: {sum(len(chunk) for chunk in self.chunks)}\n"
                f"Page {self.current_page + 1}/{self.total_pages}\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
            ),
            color=discord.Color.blue()
        )

        for fid, old_value, new_value, timestamp in self.chunks[self.current_page]:
            old_level = self.level_mapping.get(int(old_value), str(old_value))
            new_level = self.level_mapping.get(int(new_value), str(new_value))
            embed.add_field(
                name=f"{self.members.get(fid, f'Unknown ({fid})')} (FID: {fid})",
                value=f"```{old_level} ➜ {new_level}\nTime: {timestamp}```",
                inline=False
            )

        if self.total_pages > 1:
            embed.set_footer(text=f"Page {self.current_page + 1} of {self.total_pages}")

        return embed

    def update_buttons(self):
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == self.total_pages - 1

    @discord.ui.button(label="Previous", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="previous")
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="Next", emoji="➡️", style=discord.ButtonStyle.secondary, custom_id="next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(self.total_pages - 1, self.current_page + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

class RecentNicknameChangesView(discord.ui.View):
    def __init__(self, chunks, members, alliance_name, hours):
        super().__init__()
        self.chunks = chunks
        self.members = members
        self.alliance_name = alliance_name
        self.hours = hours
        self.current_page = 0
        self.total_pages = len(chunks)
        
        self.update_buttons()

    def get_embed(self):
        embed = discord.Embed(
            title=f"📝 Recent Nickname Changes - {self.alliance_name}",
            description=(
                f"Showing changes in the last {self.hours} hour(s)\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Total Changes: {sum(len(chunk) for chunk in self.chunks)}\n"
                f"Page {self.current_page + 1}/{self.total_pages}\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
            ),
            color=discord.Color.blue()
        )

        for fid, old_name, new_name, timestamp in self.chunks[self.current_page]:
            embed.add_field(
                name=f"{self.members.get(fid, f'Unknown ({fid})')} (FID: {fid})",
                value=f"```{old_name} ➜ {new_name}\nTime: {timestamp}```",
                inline=False
            )

        if self.total_pages > 1:
            embed.set_footer(text=f"Page {self.current_page + 1} of {self.total_pages}")

        return embed

    def update_buttons(self):
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == self.total_pages - 1

    @discord.ui.button(label="Previous", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="previous_nick_recent")
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="Next", emoji="➡️", style=discord.ButtonStyle.secondary, custom_id="next_nick_recent")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(self.total_pages - 1, self.current_page + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

class CustomTimeModalNickname(discord.ui.Modal, title="Custom Time Range"):
    def __init__(self, cog, alliance_name):
        super().__init__()
        self.cog = cog
        self.alliance_name = alliance_name
        self.hours = discord.ui.TextInput(
            label="Hours (1-24)",
            placeholder="Enter number of hours (max 24)...",
            required=True,
            min_length=1,
            max_length=2
        )
        self.add_item(self.hours)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            hours = int(self.hours.value)
            if hours < 1 or hours > 24:
                await interaction.response.send_message(
                    "❌ Please enter a number between 1 and 24.",
                    ephemeral=True
                )
                return
            
            await interaction.response.defer()
            await self.cog.show_recent_nickname_changes(interaction, self.alliance_name, hours)
                
        except ValueError:
            await interaction.response.send_message(
                "❌ Please enter a valid number.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in CustomTimeModalNickname on_submit: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while processing your request.",
                ephemeral=True
            )

async def setup(bot):
    await bot.add_cog(Changes(bot)) 