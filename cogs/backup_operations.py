import discord
from discord.ext import commands, tasks
import os
import zipfile
import aiohttp
import json
from datetime import datetime, timedelta
import asyncio
import tempfile
import shutil
import pyzipper
import ssl
from .database import DatabaseManager
from .log_config import get_logger
from .utils import check_global_admin, get_global_admin_ids, send_success

logger = get_logger("backup_operations")

class BackupOperations(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = DatabaseManager.instance()
        # NOTE: wosland.com backup API is defunct (site permanently down since 2026-03)
        # Backup upload/list features are disabled until a replacement is configured
        self.api_url = os.getenv("BACKUP_API_URL", "")
        self.api_key = os.getenv("BACKUP_API_KEY", "")
        self._ssl_context = ssl.create_default_context()
        self._ssl_context.check_hostname = False
        self._ssl_context.verify_mode = ssl.CERT_NONE
        self._connector: aiohttp.TCPConnector | None = None
        os.makedirs("log", exist_ok=True)
        self.setup_database()
        self.automatic_backup_loop.start()

    def _get_connector(self) -> aiohttp.TCPConnector:
        if self._connector is None or self._connector.closed:
            self._connector = aiohttp.TCPConnector(ssl=self._ssl_context)
        return self._connector

    def setup_database(self):
        conn = self.db.get("backup")
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backup_passwords (
                discord_id TEXT PRIMARY KEY,
                backup_password TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()

    def cog_unload(self):
        self.automatic_backup_loop.cancel()

    def log_backup(self, admin_id: str, success: bool, backup_type: str, backup_url: str = None, error_message: str = None):
        status = "Success" if success else "Failed"
        msg = f"Type: {backup_type} | Admin ID: {admin_id} | Status: {status}"
        if backup_url:
            msg += f" | Download Link: {backup_url}"
        if error_message:
            msg += f" | Error: {error_message}"
        if success:
            logger.info(msg)
        else:
            logger.warning(msg)

    @tasks.loop(hours=3)
    async def automatic_backup_loop(self):
        try:
            global_admin_ids = get_global_admin_ids()

            for admin_id in global_admin_ids:
                try:
                    backup_url = await self.create_backup(admin_id)
                    if backup_url:
                        self.log_backup(admin_id, True, "Automatic Backup", backup_url)
                    else:
                        self.log_backup(admin_id, False, "Automatic Backup", None, "Backup creation failed")
                except Exception as e:
                    self.log_backup(admin_id, False, "Automatic Backup", None, str(e))

        except Exception as e:
            logger.error(f"Automatic backup error: {e}")

    @automatic_backup_loop.before_loop
    async def before_automatic_backup(self):
        await self.bot.wait_until_ready()
        try:
            global_admin_ids = get_global_admin_ids()

            for admin_id in global_admin_ids:
                try:
                    backup_url = await self.create_backup(admin_id)
                    if backup_url:
                        self.log_backup(admin_id, True, "Startup Backup", backup_url)
                    else:
                        self.log_backup(admin_id, False, "Startup Backup", None, "Backup creation failed")
                except Exception as e:
                    self.log_backup(admin_id, False, "Startup Backup", None, str(e))

        except Exception as e:
            logger.error(f"Startup backup error: {e}")

    async def is_global_admin(self, discord_id):
        return check_global_admin(int(discord_id))

    async def show_backup_menu(self, interaction: discord.Interaction):
        if not await self.is_global_admin(interaction.user.id):
            await interaction.response.send_message("❌ This menu is only available for Global Admins!", ephemeral=True)
            return

        embed = discord.Embed(
            title="💾 Backup System",
            description=(
                "**Backup Operations**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "🔐 Create/Change backup password\n"
                "📋 View backup list\n"
                "💾 Create manual backup\n"
                "🔍 Get backup download link\n"
                "ℹ️ System Info: Automatic backup every 3 hours\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.blue()
        )

        view = BackupView(self)
        await interaction.response.edit_message(embed=embed, view=view)

    async def create_backup(self, user_id: str):
        try:
            conn = self.db.get("backup")
            cursor = conn.cursor()
            cursor.execute("SELECT backup_password FROM backup_passwords WHERE discord_id = ?", (user_id,))
            result = cursor.fetchone()
            backup_password = result[0] if result else None

            if not backup_password:
                return None

            with tempfile.TemporaryDirectory() as temp_dir:
                timestamp = datetime.now()
                zip_filename = f"backup_{timestamp.strftime('%Y%m%d_%H%M%S')}.zip"
                zip_path = os.path.join(temp_dir, zip_filename)

                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for file in os.listdir("db"):
                        if file.endswith(".sqlite"):
                            file_path = os.path.join("db", file)
                            zipf.write(file_path, os.path.basename(file_path))

                    readme_content = f"""Backup Information
----------------
Created at: {timestamp}
Discord ID: {user_id}
Contains: All SQLite database files
----------------
🤖 WOS Discord Bot by Reloisback

📱 Social Links:
• GitHub: https://github.com/Reloisback/Whiteout-Survival-Discord-Bot
• Discord: https://discord.gg/h8w6N6my4a
• Support: https://buymeacoffee.com/reloisback

Thank you for using our bot! ❤️
"""
                    zipf.writestr("README.txt", readme_content)

                    comment = f"""📦 WOS Discord Bot Backup File
━━━━━━━━━━━━━━━━━━━━━━
📅 Creation Date: {timestamp.strftime('%d.%m.%Y %H:%M:%S')}
👤 Discord ID: {user_id}
📂 Content: SQLite Database Files
🔐 Password Protected: Yes
━━━━━━━━━━━━━━━━━━━━━━
ℹ️ This backup was created by WOS Discord Bot developed by Reloisback.
⚠️ Use your backup password from the Backup menu to open this file.

📱 Social Media:
• GitHub: https://github.com/Reloisback/Whiteout-Survival-Discord-Bot
• Discord: https://discord.gg/h8w6N6my4a
• Support: https://buymeacoffee.com/reloisback

💡 Join our Discord server if you need help!""".encode('utf-8')
                    zipf.comment = comment

                secured_zip = os.path.join(temp_dir, f"secured_{zip_filename}")
                with pyzipper.AESZipFile(secured_zip, 'w', compression=pyzipper.ZIP_LZMA, encryption=pyzipper.WZ_AES) as zf:
                    zf.setpassword(backup_password.encode())
                    with zipfile.ZipFile(zip_path, 'r') as normal_zip:
                        zf.comment = normal_zip.comment
                        for file in normal_zip.namelist():
                            zf.writestr(file, normal_zip.read(file))

                os.remove(zip_path)

                if os.path.getsize(secured_zip) > 2 * 1024 * 1024:
                    logger.warning(f"Backup file size exceeds 2MB limit for user {user_id}")
                    return None

                if not self.api_url or not self.api_key:
                    logger.info("Backup created but no backup API configured; cannot upload.")
                    return None

                async with aiohttp.ClientSession(connector=self._get_connector(), connector_owner=False) as session:
                    with open(secured_zip, 'rb') as f:
                        data = aiohttp.FormData()
                        data.add_field('file', f)
                        data.add_field('discord_id', str(user_id))
                        data.add_field('timestamp', timestamp.strftime('%Y-%m-%d %H:%M:%S'))

                        headers = {'X-API-Key': self.api_key}
                        async with session.post(f"{self.api_url}?action=upload", data=data, headers=headers) as response:
                            if response.status == 200:
                                result = await response.json()
                                file_url = result.get('file_url')
                                if file_url:
                                    file_url = f"{file_url}&api_key={self.api_key}"
                                return file_url
                            else:
                                error_text = await response.text()
                                logger.error(f"API Error: {error_text}")
                                return None

        except Exception as e:
            logger.exception(f"Backup creation error: {e}")
            return None

    async def get_backup_list(self, user_id: str):
        if not self.api_url or not self.api_key:
            logger.info("Backup list unavailable (no backup API configured)")
            return None
        try:
            async with aiohttp.ClientSession(connector=self._get_connector(), connector_owner=False) as session:
                headers = {'X-API-Key': self.api_key}
                params = {'discord_id': user_id, 'action': 'list'}
                async with session.get(self.api_url, params=params, headers=headers) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result
                    return None
        except Exception as e:
            logger.exception("Failed to get backup list")
            return None

class BackupView(discord.ui.View):
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

    @discord.ui.button(label="Create/Change Password", emoji="⚙️", style=discord.ButtonStyle.secondary, row=0)
    async def create_password(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = BackupPasswordModal(self.cog)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Backup List", emoji="📋", style=discord.ButtonStyle.primary, row=0)
    async def list_backups(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        backup_list = await self.cog.get_backup_list(str(interaction.user.id))

        if not backup_list or len(backup_list) == 0:
            await interaction.followup.send("❌ No backups found! Please create a backup first.", ephemeral=True)
            return

        backup_by_date = {}
        for backup in backup_list:
            date = backup['timestamp'].split()[0]
            if date not in backup_by_date:
                backup_by_date[date] = []
            backup_by_date[date].append(backup)

        sorted_dates = sorted(backup_by_date.keys(), reverse=True)

        weekly_groups = []
        current_week = []
        for date in sorted_dates:
            current_week.append(date)
            if len(current_week) == 7:
                weekly_groups.append(current_week)
                current_week = []
        if current_week:
            weekly_groups.append(current_week)

        self.backup_pages = []
        for week in weekly_groups:
            embed = discord.Embed(
                title="📋 Weekly Backup Records",
                color=discord.Color.blue()
            )
            for date in week:
                backup_count = len(backup_by_date[date])
                embed.add_field(
                    name=f"📅 {date}",
                    value=f"🔄 {backup_count} backup(s)\nClick the button below to view",
                    inline=True
                )
            self.backup_pages.append((embed, week, backup_by_date))

        if self.backup_pages:
            view = BackupListView(self.backup_pages, interaction.user.id, self.cog)
            try:
                await interaction.followup.send(embed=self.backup_pages[0][0], view=view, ephemeral=True)
            except Exception as e:
                logger.exception("Error processing backup list")
                await interaction.followup.send("❌ Error processing backup list!", ephemeral=True)
        else:
            await interaction.followup.send("❌ Error processing backup list!", ephemeral=True)

    @discord.ui.button(label="Create Backup", emoji="▶️", style=discord.ButtonStyle.success, row=0)
    async def manual_backup(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        backup_url = await self.cog.create_backup(str(interaction.user.id))
        
        if backup_url:
            embed = discord.Embed(
                title="💾 Manual Backup",
                description="Backup created successfully!",
                color=discord.Color.green()
            )
            embed.add_field(
                name="📥 Download Link",
                value=f"[Click here to download]({backup_url})",
                inline=False
            )
            embed.add_field(
                name="🔐 File Password",
                value="Use your backup password",
                inline=False
            )
            self.cog.log_backup(str(interaction.user.id), True, "Manual Backup", backup_url)
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            conn = self.cog.db.get("backup")
            cursor = conn.cursor()
            cursor.execute("SELECT backup_password FROM backup_passwords WHERE discord_id = ?", (str(interaction.user.id),))
            has_password = cursor.fetchone() is not None

            embed = discord.Embed(
                title="❌ Backup Error",
                color=discord.Color.red()
            )
            error_message = ""
            if not has_password:
                error_message = "Please set a backup password first"
                embed.description = "Failed to create backup! Please set a backup password first."
            else:
                error_message = "Backup file size exceeds 2MB limit"
                embed.description = "Failed to create backup! Backup file size exceeds 2MB limit."

            self.cog.log_backup(str(interaction.user.id), False, "Manual Backup", None, error_message)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, custom_id="backup_back", row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        other_features_cog = self.cog.bot.get_cog("OtherFeatures")
        if other_features_cog:
            await other_features_cog.show_other_features_menu(interaction)

    @discord.ui.button(label="Main Menu", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="backup_main_menu", row=1)
    async def main_menu(self, interaction: discord.Interaction, button: discord.ui.Button):
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
            logger.error(f"Error returning to main menu: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while returning to main menu.",
                ephemeral=True
            )

class BackupListView(discord.ui.View):
    def __init__(self, pages, user_id, cog):
        super().__init__(timeout=300)
        self.pages = pages
        self.current_page = 0
        self.user_id = user_id
        self.cog = cog
        self.update_buttons()
        
        if self.pages and len(self.pages) > 0:
            current_page_data = self.pages[self.current_page]
            dates = current_page_data[1]
            self.select_date.options = [
                discord.SelectOption(
                    label=date,
                    value=date,
                    description=f"View backups from {date}"
                ) for date in dates
            ]

    def update_buttons(self):
        self.previous_page.disabled = self.current_page == 0
        self.next_page.disabled = self.current_page == len(self.pages) - 1

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.secondary, row=0)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return
        
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        
        current_page_data = self.pages[self.current_page]
        dates = current_page_data[1]
        self.select_date.options = [
            discord.SelectOption(
                label=date,
                value=date,
                description=f"View backups from {date}"
            ) for date in dates
        ]
        
        await interaction.response.edit_message(embed=self.pages[self.current_page][0], view=self)

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.secondary, row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return
            
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        self.update_buttons()
        
        current_page_data = self.pages[self.current_page]
        dates = current_page_data[1]
        self.select_date.options = [
            discord.SelectOption(
                label=date,
                value=date,
                description=f"View backups from {date}"
            ) for date in dates
        ]
        
        await interaction.response.edit_message(embed=self.pages[self.current_page][0], view=self)

    @discord.ui.select(
        placeholder="Select a date to view backups",
        row=1,
        min_values=1,
        max_values=1,
        options=[]
    )
    async def select_date(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.user_id:
            return

        selected_date = select.values[0]
        current_page_data = self.pages[self.current_page]
        backup_by_date = current_page_data[2]
        backups = backup_by_date[selected_date]

        backup_pages = []
        for i in range(0, len(backups), 5):
            page_backups = backups[i:i+5]
            embed = discord.Embed(
                title=f"📋 Backup List for {selected_date}",
                color=discord.Color.blue()
            )
            for backup in page_backups:
                url = f"{backup['url']}&api_key={self.cog.api_key}"
                embed.add_field(
                    name=f"⏰ {backup['timestamp'].split()[1]}",
                    value=f"🔗 [Click here to download]({url})\n🔐 Password: Use your backup password",
                    inline=False
                )
            backup_pages.append(embed)

        view = BackupDetailView(backup_pages, self.user_id, self)
        await interaction.response.edit_message(embed=backup_pages[0], view=view)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This menu is not for you!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

class BackupDetailView(discord.ui.View):
    def __init__(self, pages, user_id, parent_view):
        super().__init__(timeout=300)
        self.pages = pages
        self.current_page = 0
        self.user_id = user_id
        self.parent_view = parent_view
        self.update_buttons()

    def update_buttons(self):
        self.previous_page.disabled = self.current_page == 0
        self.next_page.disabled = self.current_page == len(self.pages) - 1

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.secondary, row=0)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return
        
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.secondary, row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return
            
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="Back to Weekly View", emoji="🔙", style=discord.ButtonStyle.secondary, row=1)
    async def back_to_weekly(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return
            
        await interaction.response.edit_message(
            embed=self.parent_view.pages[self.parent_view.current_page][0],
            view=self.parent_view
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This menu is not for you!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

class BackupPasswordModal(discord.ui.Modal, title="Create Backup Password"):
    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    password = discord.ui.TextInput(
        label="Backup Password",
        placeholder="Enter a secure password...",
        min_length=5,
        max_length=50,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        conn = self.cog.db.get("backup")
        cursor = conn.cursor()

        cursor.execute(
            "INSERT OR REPLACE INTO backup_passwords (discord_id, backup_password) VALUES (?, ?)",
            (str(interaction.user.id), self.password.value)
        )

        conn.commit()

        await send_success(interaction, "Your backup password has been saved successfully!", "✅ Password Saved")

        try:
            admin_id_list = get_global_admin_ids()

            admin_embed = discord.Embed(
                title="🔐 Backup Password Change",
                description=f"A user has changed their backup password.",
                color=discord.Color.blue()
            )
            admin_embed.add_field(
                name="👤 User",
                value=f"<@{interaction.user.id}> (ID: {interaction.user.id})",
                inline=False
            )
            admin_embed.add_field(
                name="⏰ Time",
                value=f"<t:{int(datetime.now().timestamp())}:F>",
                inline=False
            )

            for admin_id in admin_id_list:
                try:
                    admin_user = await interaction.client.fetch_user(admin_id)
                    if admin_user and admin_user.id != interaction.user.id:
                        await admin_user.send(embed=admin_embed)
                except Exception as e:
                    logger.error(f"Error sending notification to admin {admin_id}: {e}")

        except Exception as e:
            logger.error(f"Error sending admin notifications: {e}")

async def setup(bot):
    await bot.add_cog(BackupOperations(bot))