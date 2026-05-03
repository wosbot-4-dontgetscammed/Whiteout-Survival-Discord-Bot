import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from datetime import datetime

from .database import DatabaseManager
from .log_config import get_logger
from .utils import build_embed, get_admin_info, send_error, send_success, send_info

logger = get_logger("alliance")

class Alliance(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        db = DatabaseManager.instance()
        self.conn = db.get("alliance")

        self.conn_users = db.get("users")

        self.conn_settings = db.get("settings")

        self.conn_giftcode = db.get("giftcode")

        self._create_table()
        self._check_and_add_column()

    def _create_table(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS alliance_list (
                alliance_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                discord_server_id INTEGER
            )
        """)
        self.conn.commit()

    def _check_and_add_column(self):
        cursor = self.conn.execute("PRAGMA table_info(alliance_list)")
        columns = [info[1] for info in cursor.fetchall()]
        if "discord_server_id" not in columns:
            self.conn.execute("ALTER TABLE alliance_list ADD COLUMN discord_server_id INTEGER")
            self.conn.commit()

    async def view_alliances(self, interaction: discord.Interaction):

        user_id = interaction.user.id
        admin = get_admin_info(user_id)

        if admin is None:
            await interaction.response.send_message("You do not have permission to view alliances.", ephemeral=True)
            return

        is_initial = admin[1]
        guild_id = interaction.guild.id

        try:
            if is_initial == 1:
                query = """
                    SELECT a.alliance_id, a.name, COALESCE(s.interval, 0) as interval
                    FROM alliance_list a
                    LEFT JOIN alliancesettings s ON a.alliance_id = s.alliance_id
                    ORDER BY a.alliance_id ASC
                """
                cursor = self.conn.execute(query)
            else:
                query = """
                    SELECT a.alliance_id, a.name, COALESCE(s.interval, 0) as interval
                    FROM alliance_list a
                    LEFT JOIN alliancesettings s ON a.alliance_id = s.alliance_id
                    WHERE a.discord_server_id = ?
                    ORDER BY a.alliance_id ASC
                """
                cursor = self.conn.execute(query, (guild_id,))

            alliances = cursor.fetchall()

            alliance_list = ""
            for alliance_id, name, interval in alliances:
                
                cursor = self.conn_users.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                row = cursor.fetchone()
                member_count = row[0] if row else 0

                interval_text = f"{interval} minutes" if interval > 0 else "No automatic control"
                alliance_list += f"🛡️ **{alliance_id}: {name}**\n👥 Members: {member_count}\n⏱️ Control Interval: {interval_text}\n\n"

            if not alliance_list:
                alliance_list = "No alliances found."

            await send_info(interaction, alliance_list, "Existing Alliances")

        except Exception as e:
            await interaction.response.send_message(
                "An error occurred while fetching alliances.", 
                ephemeral=True
            )

    async def alliance_autocomplete(self, interaction: discord.Interaction, current: str):
        cursor = self.conn.execute("SELECT alliance_id, name FROM alliance_list")
        alliances = cursor.fetchall()
        return [
            app_commands.Choice(name=f"{name} (ID: {alliance_id})", value=str(alliance_id))
            for alliance_id, name in alliances if current.lower() in name.lower()
        ][:25]

    @app_commands.command(name="settings", description="Open settings menu.")
    async def settings(self, interaction: discord.Interaction):
        try:
            cursor = self.conn_settings.execute("SELECT COUNT(*) FROM admin")
            row = cursor.fetchone()
            admin_count = row[0] if row else 0

            user_id = interaction.user.id

            if admin_count == 0:
                self.conn_settings.execute("""
                    INSERT INTO admin (id, is_initial) 
                    VALUES (?, 1)
                """, (user_id,))
                self.conn_settings.commit()

                await send_success(
                    interaction,
                    "This command has been used for the first time and no administrators were found.\n\n"
                    f"**{interaction.user.name}** has been added as the Global Administrator.\n\n"
                    "You can now access all administrative functions.",
                    "🎉 First Time Setup"
                )
                
                await asyncio.sleep(3)
                
            admin = get_admin_info(user_id)

            if admin is None:
                await interaction.response.send_message(
                    "You do not have permission to access this menu.",
                    ephemeral=True
                )
                return

            embed = discord.Embed(
                title="⚙️ Settings Menu",
                description=(
                    "Please select a category:\n\n"
                    "**Menu Categories**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "🏰 **Alliance Operations**\n"
                    "└ Manage alliances and settings\n\n"
                    "👥 **Alliance Member Operations**\n"
                    "└ Add, remove, and view members\n\n"
                    "🤖 **Bot Operations**\n"
                    "└ Configure bot settings\n\n"
                    "🎁 **Gift Code Operations**\n"
                    "└ Manage gift codes and rewards\n\n"
                    "📜 **Alliance History**\n"
                    "└ View alliance changes and history\n\n"
                    "🆘 **Support Operations**\n"
                    "└ Access support features\n\n"
                    "🌐 **Gift Code Scraper**\n"
                    "└ Configure web scraper sources\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=discord.Color.blue()
            )

            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Alliance Operations",
                emoji="🏰",
                style=discord.ButtonStyle.primary,
                custom_id="alliance_operations",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Member Operations",
                emoji="👥",
                style=discord.ButtonStyle.primary,
                custom_id="member_operations",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Bot Operations",
                emoji="🤖",
                style=discord.ButtonStyle.primary,
                custom_id="bot_operations",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="Gift Operations",
                emoji="🎁",
                style=discord.ButtonStyle.primary,
                custom_id="gift_code_operations",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="Alliance History",
                emoji="📜",
                style=discord.ButtonStyle.primary,
                custom_id="alliance_history",
                row=2
            ))
            view.add_item(discord.ui.Button(
                label="Support Operations",
                emoji="🆘",
                style=discord.ButtonStyle.primary,
                custom_id="support_operations",
                row=2
            ))
            view.add_item(discord.ui.Button(
                label="Other Features",
                emoji="🔧",
                style=discord.ButtonStyle.primary,
                custom_id="other_features",
                row=3
            ))
            view.add_item(discord.ui.Button(
                label="Gift Scraper",
                emoji="🌐",
                style=discord.ButtonStyle.primary,
                custom_id="scraper_settings",
                row=3
            ))

            if admin_count == 0:
                await interaction.edit_original_response(embed=embed, view=view)
            else:
                await interaction.response.send_message(embed=embed, view=view)

        except Exception as e:
            if not any(error_code in str(e) for error_code in ["10062", "40060"]):
                logger.error("Settings command error: %s", e)
            error_message = "An error occurred while processing your request."
            if not interaction.response.is_done():
                await interaction.response.send_message(error_message, ephemeral=True)
            else:
                await interaction.followup.send(error_message, ephemeral=True)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type == discord.InteractionType.component:
            custom_id = interaction.data.get("custom_id")
            user_id = interaction.user.id

            # Custom IDs handled by this cog
            ADMIN_REQUIRED_IDS = {
                "alliance_operations", "add_alliance", "edit_alliance",
                "delete_alliance", "view_alliances", "check_alliance",
                "member_operations", "bot_operations", "gift_code_operations",
                "scraper_settings", "support_operations", "alliance_history",
                "other_features", "alliance_main_menu",
            }

            if custom_id not in ADMIN_REQUIRED_IDS:
                return  # Not our button, let other cog listeners handle it

            admin = get_admin_info(user_id)

            if admin is None:
                await interaction.response.send_message("You do not have permission to perform this action.", ephemeral=True)
                return

            try:
                if custom_id == "alliance_operations":
                    embed = discord.Embed(
                        title="🏰 Alliance Operations",
                        description=(
                            "Please select an operation:\n\n"
                            "**Available Operations**\n"
                            "━━━━━━━━━━━━━━━━━━━━━━\n"
                            "➕ **Add Alliance**\n"
                            "└ Create a new alliance\n\n"
                            "✏️ **Edit Alliance**\n"
                            "└ Modify existing alliance settings\n\n"
                            "🗑️ **Delete Alliance**\n"
                            "└ Remove an existing alliance\n\n"
                            "👀 **View Alliances**\n"
                            "└ List all available alliances\n"
                            "━━━━━━━━━━━━━━━━━━━━━━"
                        ),
                        color=discord.Color.blue()
                    )
                    
                    view = discord.ui.View()
                    view.add_item(discord.ui.Button(
                        label="Add Alliance", 
                        emoji="➕",
                        style=discord.ButtonStyle.success, 
                        custom_id="add_alliance", 
                        disabled=admin[1] != 1
                    ))
                    view.add_item(discord.ui.Button(
                        label="Edit Alliance", 
                        emoji="✏️",
                        style=discord.ButtonStyle.primary, 
                        custom_id="edit_alliance", 
                        disabled=admin[1] != 1
                    ))
                    view.add_item(discord.ui.Button(
                        label="Delete Alliance", 
                        emoji="🗑️",
                        style=discord.ButtonStyle.danger, 
                        custom_id="delete_alliance", 
                        disabled=admin[1] != 1
                    ))
                    view.add_item(discord.ui.Button(
                        label="View Alliances", 
                        emoji="👀",
                        style=discord.ButtonStyle.primary, 
                        custom_id="view_alliances"
                    ))
                    view.add_item(discord.ui.Button(
                        label="Check Alliance", 
                        emoji="🔍",
                        style=discord.ButtonStyle.primary, 
                        custom_id="check_alliance"
                    ))
                    view.add_item(discord.ui.Button(
                        label="Main Menu",
                        emoji="🏠",
                        style=discord.ButtonStyle.secondary,
                        custom_id="alliance_main_menu"
                    ))

                    await interaction.response.edit_message(embed=embed, view=view)

                elif custom_id == "edit_alliance":
                    if admin[1] != 1:
                        await interaction.response.send_message("You do not have permission to perform this action.", ephemeral=True)
                        return
                    await self.edit_alliance(interaction)

                elif custom_id == "check_alliance":
                    cursor = self.conn.execute("""
                        SELECT a.alliance_id, a.name, COALESCE(s.interval, 0) as interval
                        FROM alliance_list a
                        LEFT JOIN alliancesettings s ON a.alliance_id = s.alliance_id
                        ORDER BY a.name
                    """)
                    alliances = cursor.fetchall()

                    if not alliances:
                        await interaction.response.send_message("No alliances found to check.", ephemeral=True)
                        return

                    options = [
                        discord.SelectOption(
                            label="Check All Alliances",
                            value="all",
                            description="Start control process for all alliances",
                            emoji="🔄"
                        )
                    ]
                    
                    options.extend([
                        discord.SelectOption(
                            label=f"{name[:40]}",
                            value=str(alliance_id),
                            description=f"Control Interval: {interval} minutes"
                        ) for alliance_id, name, interval in alliances
                    ])

                    select = discord.ui.Select(
                        placeholder="Select an alliance to check",
                        options=options,
                        custom_id="alliance_check_select"
                    )

                    async def alliance_check_callback(select_interaction: discord.Interaction):
                        try:
                            selected_value = select_interaction.data["values"][0]
                            control_cog = self.bot.get_cog('Control')
                            
                            if not control_cog:
                                await select_interaction.response.send_message("Control module not found.", ephemeral=True)
                                return
                            
                            if not hasattr(control_cog, '_queue_processor_task') or control_cog._queue_processor_task.done():
                                control_cog._queue_processor_task = asyncio.create_task(control_cog.process_control_queue())
                            
                            if selected_value == "all":
                                progress_embed = build_embed("🔄 Alliance Control Queue", {
                                    "📊 Total Alliances": str(len(alliances)),
                                    "🔄 Status": "Adding alliances to control queue...",
                                    "⏰ Queue Start": "Now",
                                    "⚠️ Note": "Each alliance will be processed in sequence",
                                    "⏱️ Wait Time": "1 minute between each alliance control",
                                }, header="Control Queue Information", color=discord.Color.blue())
                                await select_interaction.response.send_message(embed=progress_embed)
                                
                                for index, (alliance_id, name, _) in enumerate(alliances):
                                    try:
                                        queue_status_embed = build_embed("🔄 Alliance Control Queue", {
                                            "📊 Total Alliances": str(len(alliances)),
                                            "🔄 Current Alliance": name,
                                            "📈 Progress": f"{index + 1}/{len(alliances)}",
                                            "⏰ Queue Start": f"<t:{int(datetime.now().timestamp())}:R>",
                                            "⏱️ Wait Time": "1 minute between each alliance control",
                                        }, header="Control Queue Information", color=discord.Color.blue())
                                        await select_interaction.edit_original_response(embed=queue_status_embed)
                                        
                                        cursor = self.conn.execute("""
                                            SELECT channel_id FROM alliancesettings WHERE alliance_id = ?
                                        """, (alliance_id,))
                                        channel_data = cursor.fetchone()
                                        channel = self.bot.get_channel(channel_data[0]) if channel_data else select_interaction.channel
                                        
                                        await control_cog.control_queue.put({
                                            'channel': channel,
                                            'alliance_id': alliance_id,
                                            'is_manual': True
                                        })

                                        wait_count = 0
                                        while control_cog.current_control:
                                            await asyncio.sleep(1)
                                            wait_count += 1
                                            if wait_count > 300:  # 5 min timeout
                                                logger.warning("Timed out waiting for control_cog")
                                                break

                                        if index < len(alliances) - 1:
                                            await asyncio.sleep(60)
                                    
                                    except Exception as e:
                                        logger.error("Error processing alliance %s: %s", name, e)
                                        continue
                                
                                queue_complete_embed = build_embed("✅ Alliance Control Queue Complete", {
                                    "📊 Total Alliances": str(len(alliances)),
                                    "🔄 Status": "All controls completed",
                                    "⏰ Completion Time": f"<t:{int(datetime.now().timestamp())}:R>",
                                    "📝 Note": "Control results have been shared in respective channels",
                                }, header="Queue Status Information")
                                await select_interaction.edit_original_response(embed=queue_complete_embed)
                            
                            else:
                                alliance_id = int(selected_value)
                                cursor = self.conn.execute("""
                                    SELECT a.name, s.channel_id 
                                    FROM alliance_list a
                                    LEFT JOIN alliancesettings s ON a.alliance_id = s.alliance_id
                                    WHERE a.alliance_id = ?
                                """, (alliance_id,))
                                alliance_data = cursor.fetchone()

                                if not alliance_data:
                                    await select_interaction.response.send_message("Alliance not found.", ephemeral=True)
                                    return

                                alliance_name, channel_id = alliance_data
                                channel = self.bot.get_channel(channel_id) if channel_id else select_interaction.channel
                                
                                status_embed = discord.Embed(
                                    title="🔍 Alliance Control",
                                    description=(
                                        f"Control process will start for alliance **{alliance_name}**.\n\n"
                                        "**Process Information**\n"
                                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                                        "• Status: `Queued`\n"
                                        "• Process: `Starting...`\n"
                                        "• Channel: `Results will be shared in the designated channel`\n"
                                        "━━━━━━━━━━━━━━━━━━━━━━"
                                    ),
                                    color=discord.Color.blue()
                                )
                                await select_interaction.response.send_message(embed=status_embed)
                                
                                await control_cog.control_queue.put({
                                    'channel': channel,
                                    'alliance_id': alliance_id,
                                    'is_manual': True
                                })

                        except Exception as e:
                            logger.error("Alliance check error: %s", e)
                            await select_interaction.response.send_message(
                                "An error occurred during the control process.", 
                                ephemeral=True
                            )

                    select.callback = alliance_check_callback
                    view = discord.ui.View()
                    view.add_item(select)

                    embed = discord.Embed(
                        title="🔍 Alliance Control",
                        description=(
                            "Please select an alliance to check:\n\n"
                            "**Information**\n"
                            "━━━━━━━━━━━━━━━━━━━━━━\n"
                            "• Select 'Check All Alliances' to process all alliances\n"
                            "• Control process may take a few minutes\n"
                            "• Results will be shared in the designated channel\n"
                            "• Other controls will be queued during the process\n"
                            "━━━━━━━━━━━━━━━━━━━━━━"
                        ),
                        color=discord.Color.blue()
                    )
                    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

                elif custom_id == "member_operations":
                    cog = self.bot.get_cog("AllianceMemberOperations")
                    if cog:
                        await cog.handle_member_operations(interaction)
                    else:
                        await interaction.response.send_message("Module not loaded.", ephemeral=True)

                elif custom_id == "bot_operations":
                    try:
                        bot_ops_cog = interaction.client.get_cog("BotOperations")
                        if bot_ops_cog:
                            await bot_ops_cog.show_bot_operations_menu(interaction)
                        else:
                            await interaction.response.send_message(
                                "❌ Bot Operations module not found.",
                                ephemeral=True
                            )
                    except Exception as e:
                        if not any(error_code in str(e) for error_code in ["10062", "40060"]):
                            logger.error("Bot operations error: %s", e)
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "An error occurred while loading Bot Operations.",
                                ephemeral=True
                            )
                        else:
                            await interaction.followup.send(
                                "An error occurred while loading Bot Operations.",
                                ephemeral=True
                            )

                elif custom_id == "gift_code_operations":
                    try:
                        gift_ops_cog = interaction.client.get_cog("GiftOperations")
                        if gift_ops_cog:
                            await gift_ops_cog.ui.show_gift_menu(interaction)
                        else:
                            await interaction.response.send_message(
                                "❌ Gift Operations module not found.",
                                ephemeral=True
                            )
                    except Exception as e:
                        logger.error("Gift operations error: %s", e)
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "An error occurred while loading Gift Operations.",
                                ephemeral=True
                            )
                        else:
                            await interaction.followup.send(
                                "An error occurred while loading Gift Operations.",
                                ephemeral=True
                            )

                elif custom_id == "scraper_settings":
                    try:
                        scraper_cog = interaction.client.get_cog("GiftScraper")
                        if scraper_cog:
                            await scraper_cog.show_scraper_menu(interaction)
                        else:
                            await interaction.response.send_message(
                                "❌ Gift Scraper module not found.",
                                ephemeral=True
                            )
                    except Exception as e:
                        logger.error("Gift scraper error: %s", e)
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "An error occurred while loading Gift Scraper.",
                                ephemeral=True
                            )
                        else:
                            await interaction.followup.send(
                                "An error occurred while loading Gift Scraper.",
                                ephemeral=True
                            )

                elif custom_id == "add_alliance":
                    if admin[1] != 1:
                        await interaction.response.send_message("You do not have permission to perform this action.", ephemeral=True)
                        return
                    await self.add_alliance(interaction)

                elif custom_id == "delete_alliance":
                    if admin[1] != 1:
                        await interaction.response.send_message("You do not have permission to perform this action.", ephemeral=True)
                        return
                    await self.delete_alliance(interaction)

                elif custom_id == "view_alliances":
                    await self.view_alliances(interaction)

                elif custom_id == "support_operations":
                    try:
                        support_ops_cog = interaction.client.get_cog("SupportOperations")
                        if support_ops_cog:
                            await support_ops_cog.show_support_menu(interaction)
                        else:
                            await interaction.response.send_message(
                                "❌ Support Operations module not found.",
                                ephemeral=True
                            )
                    except Exception as e:
                        if not any(error_code in str(e) for error_code in ["10062", "40060"]):
                            logger.error("Support operations error: %s", e)
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "An error occurred while loading Support Operations.", 
                                ephemeral=True
                            )
                        else:
                            await interaction.followup.send(
                                "An error occurred while loading Support Operations.",
                                ephemeral=True
                            )

                elif custom_id == "alliance_history":
                    try:
                        changes_cog = interaction.client.get_cog("Changes")
                        if changes_cog:
                            await changes_cog.show_alliance_history_menu(interaction)
                        else:
                            await interaction.response.send_message(
                                "❌ Alliance History module not found.",
                                ephemeral=True
                            )
                    except Exception as e:
                        logger.error("Alliance history error: %s", e)
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "An error occurred while loading Alliance History.",
                                ephemeral=True
                            )
                        else:
                            await interaction.followup.send(
                                "An error occurred while loading Alliance History.",
                                ephemeral=True
                            )

                elif custom_id == "other_features":
                    try:
                        other_features_cog = interaction.client.get_cog("OtherFeatures")
                        if other_features_cog:
                            await other_features_cog.show_other_features_menu(interaction)
                        else:
                            await interaction.response.send_message(
                                "❌ Other Features module not found.",
                                ephemeral=True
                            )
                    except Exception as e:
                        if not any(error_code in str(e) for error_code in ["10062", "40060"]):
                            logger.error("Other features error: %s", e)
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "An error occurred while loading Other Features menu.",
                                ephemeral=True
                            )
                        else:
                            await interaction.followup.send(
                                "An error occurred while loading Other Features menu.",
                                ephemeral=True
                            )

                elif custom_id == "alliance_main_menu":
                    await self.show_main_menu(interaction)

            except Exception as e:
                if not any(error_code in str(e) for error_code in ["10062", "40060"]):
                    logger.error("Error processing interaction with custom_id '%s': %s", custom_id, e)
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "An error occurred while processing your request. Please try again.",
                        ephemeral=True
                    )

    async def add_alliance(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("Please perform this action in a Discord channel.", ephemeral=True)
            return

        modal = AllianceModal(title="Add Alliance")
        await interaction.response.send_modal(modal)
        await modal.wait()

        try:
            alliance_name = modal.name.value.strip()
            interval = int(modal.interval.value.strip())

            embed = discord.Embed(
                title="Channel Selection",
                description=(
                    "**Instructions:**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "Please select a channel for the alliance\n\n"
                    "**Page:** 1/1\n"
                    f"**Total Channels:** {len(interaction.guild.text_channels)}"
                ),
                color=discord.Color.blue()
            )

            async def channel_select_callback(select_interaction: discord.Interaction):
                try:
                    cursor = self.conn.execute("SELECT alliance_id FROM alliance_list WHERE name = ?", (alliance_name,))
                    existing_alliance = cursor.fetchone()
                    
                    if existing_alliance:
                        error_embed = discord.Embed(
                            title="Error",
                            description="An alliance with this name already exists.",
                            color=discord.Color.red()
                        )
                        await select_interaction.response.edit_message(embed=error_embed, view=None)
                        return

                    channel_id = int(select_interaction.data["values"][0])

                    cursor = self.conn.execute("INSERT INTO alliance_list (name, discord_server_id) VALUES (?, ?)", 
                                 (alliance_name, interaction.guild.id))
                    alliance_id = cursor.lastrowid
                    self.conn.execute("INSERT INTO alliancesettings (alliance_id, channel_id, interval) VALUES (?, ?, ?)", 
                                 (alliance_id, channel_id, interval))
                    self.conn.commit()

                    self.conn_giftcode.execute("""
                        INSERT INTO giftcodecontrol (alliance_id, status) 
                        VALUES (?, 1)
                    """, (alliance_id,))
                    self.conn_giftcode.commit()

                    result_embed = discord.Embed(
                        title="✅ Alliance Successfully Created",
                        description="The alliance has been created with the following details:",
                        color=discord.Color.green()
                    )
                    
                    info_section = (
                        f"**🛡️ Alliance Name**\n{alliance_name}\n\n"
                        f"**🔢 Alliance ID**\n{alliance_id}\n\n"
                        f"**📢 Channel**\n<#{channel_id}>\n\n"
                        f"**⏱️ Control Interval**\n{interval} minutes"
                    )
                    result_embed.add_field(name="Alliance Details", value=info_section, inline=False)
                    
                    result_embed.set_footer(text="Alliance settings have been successfully saved")
                    result_embed.timestamp = discord.utils.utcnow()
                    
                    await select_interaction.response.edit_message(embed=result_embed, view=None)

                except Exception as e:
                    error_embed = discord.Embed(
                        title="Error",
                        description=f"Error creating alliance: {str(e)}",
                        color=discord.Color.red()
                    )
                    await select_interaction.response.edit_message(embed=error_embed, view=None)

            channels = interaction.guild.text_channels
            view = PaginatedChannelView(channels, channel_select_callback)
            await modal.interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        except ValueError:
            await send_error(modal.interaction, "Invalid interval value. Please enter a number.", "Error")
        except Exception as e:
            await send_error(modal.interaction, f"Error: {str(e)}", "Error")

    async def edit_alliance(self, interaction: discord.Interaction):
        cursor = self.conn.execute("""
            SELECT a.alliance_id, a.name, COALESCE(s.interval, 0) as interval, COALESCE(s.channel_id, 0) as channel_id 
            FROM alliance_list a 
            LEFT JOIN alliancesettings s ON a.alliance_id = s.alliance_id
            ORDER BY a.alliance_id ASC
        """)
        alliances = cursor.fetchall()
        
        if not alliances:
            no_alliance_embed = discord.Embed(
                title="❌ No Alliances Found",
                description=(
                    "There are no alliances registered in the database.\n"
                    "Please create an alliance first using the `/alliance create` command."
                ),
                color=discord.Color.red()
            )
            no_alliance_embed.set_footer(text="Use /alliance create to add a new alliance")
            return await interaction.response.send_message(embed=no_alliance_embed, ephemeral=True)

        alliance_options = [
            discord.SelectOption(
                label=f"{name} (ID: {alliance_id})",
                value=f"{alliance_id}",
                description=f"Interval: {interval} minutes"
            ) for alliance_id, name, interval, _ in alliances
        ]
        
        items_per_page = 25
        option_pages = [alliance_options[i:i + items_per_page] for i in range(0, len(alliance_options), items_per_page)]
        total_pages = len(option_pages)

        class PaginatedAllianceView(discord.ui.View):
            def __init__(self, pages, original_callback):
                super().__init__(timeout=300)
                self.current_page = 0
                self.pages = pages
                self.original_callback = original_callback
                self.total_pages = len(pages)
                self.update_view()

            def update_view(self):
                self.clear_items()
                
                select = discord.ui.Select(
                    placeholder=f"Select alliance ({self.current_page + 1}/{self.total_pages})",
                    options=self.pages[self.current_page]
                )
                select.callback = self.original_callback
                self.add_item(select)
                
                previous_button = discord.ui.Button(
                    label="◀️",
                    style=discord.ButtonStyle.grey,
                    custom_id="previous",
                    disabled=(self.current_page == 0)
                )
                previous_button.callback = self.previous_callback
                self.add_item(previous_button)

                next_button = discord.ui.Button(
                    label="▶️",
                    style=discord.ButtonStyle.grey,
                    custom_id="next",
                    disabled=(self.current_page == len(self.pages) - 1)
                )
                next_button.callback = self.next_callback
                self.add_item(next_button)

            async def previous_callback(self, interaction: discord.Interaction):
                self.current_page = (self.current_page - 1) % len(self.pages)
                self.update_view()
                
                embed = interaction.message.embeds[0]
                embed.description = (
                    "**Instructions:**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "1️⃣ Select an alliance from the dropdown menu\n"
                    "2️⃣ Use ◀️ ▶️ buttons to navigate between pages\n\n"
                    f"**Current Page:** {self.current_page + 1}/{self.total_pages}\n"
                    f"**Total Alliances:** {sum(len(page) for page in self.pages)}\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                )
                await interaction.response.edit_message(embed=embed, view=self)

            async def next_callback(self, interaction: discord.Interaction):
                self.current_page = (self.current_page + 1) % len(self.pages)
                self.update_view()
                
                embed = interaction.message.embeds[0]
                embed.description = (
                    "**Instructions:**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "1️⃣ Select an alliance from the dropdown menu\n"
                    "2️⃣ Use ◀️ ▶️ buttons to navigate between pages\n\n"
                    f"**Current Page:** {self.current_page + 1}/{self.total_pages}\n"
                    f"**Total Alliances:** {sum(len(page) for page in self.pages)}\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                )
                await interaction.response.edit_message(embed=embed, view=self)

        async def select_callback(select_interaction: discord.Interaction):
            try:
                alliance_id = int(select_interaction.data["values"][0])
                alliance_data = next(a for a in alliances if a[0] == alliance_id)
                
                cursor = self.conn.execute("""
                    SELECT interval, channel_id 
                    FROM alliancesettings 
                    WHERE alliance_id = ?
                """, (alliance_id,))
                settings_data = cursor.fetchone()
                
                modal = AllianceModal(
                    title="Edit Alliance",
                    default_name=alliance_data[1],
                    default_interval=str(settings_data[0] if settings_data else 0)
                )
                await select_interaction.response.send_modal(modal)
                await modal.wait()

                try:
                    alliance_name = modal.name.value.strip()
                    interval = int(modal.interval.value.strip())

                    embed = discord.Embed(
                        title="🔄 Channel Selection",
                        description=(
                            "**Current Channel Information**\n"
                            "━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"📢 Current channel: {f'<#{settings_data[1]}>' if settings_data else 'Not set'}\n"
                            "**Page:** 1/1\n"
                            f"**Total Channels:** {len(interaction.guild.text_channels)}\n"
                            "━━━━━━━━━━━━━━━━━━━━━━"
                        ),
                        color=discord.Color.blue()
                    )

                    async def channel_select_callback(channel_interaction: discord.Interaction):
                        try:
                            channel_id = int(channel_interaction.data["values"][0])

                            self.conn.execute("UPDATE alliance_list SET name = ? WHERE alliance_id = ?", 
                                          (alliance_name, alliance_id))
                            
                            if settings_data:
                                self.conn.execute("""
                                    UPDATE alliancesettings 
                                    SET channel_id = ?, interval = ? 
                                    WHERE alliance_id = ?
                                """, (channel_id, interval, alliance_id))
                            else:
                                self.conn.execute("""
                                    INSERT INTO alliancesettings (alliance_id, channel_id, interval)
                                    VALUES (?, ?, ?)
                                """, (alliance_id, channel_id, interval))
                            
                            self.conn.commit()

                            result_embed = discord.Embed(
                                title="✅ Alliance Successfully Updated",
                                description="The alliance details have been updated as follows:",
                                color=discord.Color.green()
                            )
                            
                            info_section = (
                                f"**🛡️ Alliance Name**\n{alliance_name}\n\n"
                                f"**🔢 Alliance ID**\n{alliance_id}\n\n"
                                f"**📢 Channel**\n<#{channel_id}>\n\n"
                                f"**⏱️ Control Interval**\n{interval} minutes"
                            )
                            result_embed.add_field(name="Alliance Details", value=info_section, inline=False)
                            
                            result_embed.set_footer(text="Alliance settings have been successfully saved")
                            result_embed.timestamp = discord.utils.utcnow()
                            
                            await channel_interaction.response.edit_message(embed=result_embed, view=None)

                        except Exception as e:
                            error_embed = discord.Embed(
                                title="❌ Error",
                                description=f"An error occurred while updating the alliance: {str(e)}",
                                color=discord.Color.red()
                            )
                            await channel_interaction.response.edit_message(embed=error_embed, view=None)

                    channels = interaction.guild.text_channels
                    view = PaginatedChannelView(channels, channel_select_callback)
                    await modal.interaction.response.send_message(embed=embed, view=view, ephemeral=True)

                except ValueError:
                    await send_error(modal.interaction, "Invalid interval value. Please enter a number.", "Error")
                except Exception as e:
                    await send_error(modal.interaction, f"Error: {str(e)}", "Error")

            except Exception as e:
                await send_error(select_interaction, f"An error occurred: {str(e)}")

        view = PaginatedAllianceView(option_pages, select_callback)
        embed = discord.Embed(
            title="🛡️ Alliance Edit Menu",
            description=(
                "**Instructions:**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "1️⃣ Select an alliance from the dropdown menu\n"
                "2️⃣ Use ◀️ ▶️ buttons to navigate between pages\n\n"
                f"**Current Page:** {1}/{total_pages}\n"
                f"**Total Alliances:** {len(alliances)}\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text="Use the dropdown menu below to select an alliance")
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def delete_alliance(self, interaction: discord.Interaction):
        try:
            cursor = self.conn.execute("SELECT alliance_id, name FROM alliance_list ORDER BY name")
            alliances = cursor.fetchall()
            
            if not alliances:
                await send_error(interaction, "There are no alliances to delete.", "❌ No Alliances Found")
                return

            alliance_members = {}
            for alliance_id, _ in alliances:
                cursor = self.conn_users.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                row = cursor.fetchone()
                member_count = row[0] if row else 0
                alliance_members[alliance_id] = member_count

            items_per_page = 25
            all_options = [
                discord.SelectOption(
                    label=f"{name[:40]} (ID: {alliance_id})",
                    value=f"{alliance_id}",
                    description=f"👥 Members: {alliance_members[alliance_id]} | Click to delete",
                    emoji="🗑️"
                ) for alliance_id, name in alliances
            ]
            
            option_pages = [all_options[i:i + items_per_page] for i in range(0, len(all_options), items_per_page)]
            
            embed = discord.Embed(
                title="🗑️ Delete Alliance",
                description=(
                    "**⚠️ Warning: This action cannot be undone!**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "1️⃣ Select an alliance from the dropdown menu\n"
                    "2️⃣ Use ◀️ ▶️ buttons to navigate between pages\n\n"
                    f"**Current Page:** 1/{len(option_pages)}\n"
                    f"**Total Alliances:** {len(alliances)}\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=discord.Color.red()
            )
            embed.set_footer(text="⚠️ Warning: Deleting an alliance will remove all its data!")
            embed.timestamp = discord.utils.utcnow()

            view = PaginatedDeleteView(option_pages, self.alliance_delete_callback)
            
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        except Exception as e:
            logger.error("Error in delete_alliance: %s", e)
            await send_error(interaction, "An error occurred while loading the delete menu.")

    async def alliance_delete_callback(self, interaction: discord.Interaction):
        try:
            alliance_id = int(interaction.data["values"][0])
            
            cursor = self.conn.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
            alliance_data = cursor.fetchone()
            
            if not alliance_data:
                await interaction.response.send_message("Alliance not found.", ephemeral=True)
                return
            
            alliance_name = alliance_data[0]

            cursor = self.conn.execute("SELECT COUNT(*) FROM alliancesettings WHERE alliance_id = ?", (alliance_id,))
            row = cursor.fetchone()
            settings_count = row[0] if row else 0

            cursor = self.conn_users.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
            row = cursor.fetchone()
            users_count = row[0] if row else 0

            cursor = self.conn_settings.execute("SELECT COUNT(*) FROM adminserver WHERE alliances_id = ?", (alliance_id,))
            row = cursor.fetchone()
            admin_server_count = row[0] if row else 0

            cursor = self.conn_giftcode.execute("SELECT COUNT(*) FROM giftcode_channel WHERE alliance_id = ?", (alliance_id,))
            row = cursor.fetchone()
            gift_channels_count = row[0] if row else 0

            cursor = self.conn_giftcode.execute("SELECT COUNT(*) FROM giftcodecontrol WHERE alliance_id = ?", (alliance_id,))
            row = cursor.fetchone()
            gift_code_control_count = row[0] if row else 0

            confirm_embed = discord.Embed(
                title="⚠️ Confirm Alliance Deletion",
                description=(
                    f"Are you sure you want to delete this alliance?\n\n"
                    f"**Alliance Details:**\n"
                    f"🛡️ **Name:** {alliance_name}\n"
                    f"🔢 **ID:** {alliance_id}\n"
                    f"👥 **Members:** {users_count}\n\n"
                    f"**Data to be Deleted:**\n"
                    f"⚙️ Alliance Settings: {settings_count}\n"
                    f"👥 User Records: {users_count}\n"
                    f"🏰 Admin Server Records: {admin_server_count}\n"
                    f"📢 Gift Channels: {gift_channels_count}\n"
                    f"📊 Gift Code Controls: {gift_code_control_count}\n\n"
                    "**⚠️ WARNING: This action cannot be undone!**"
                ),
                color=discord.Color.red()
            )
            
            confirm_view = discord.ui.View(timeout=60)
            
            async def confirm_callback(button_interaction: discord.Interaction):
                try:
                    alliance_count = 0
                    admin_settings_count = 0
                    users_count_deleted = 0
                    admin_server_count = 0
                    gift_channels_count = 0
                    gift_code_control_count = 0
                    errors = []

                    try:
                        cursor = self.conn.execute("DELETE FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                        alliance_count = cursor.rowcount
                        cursor = self.conn.execute("DELETE FROM alliancesettings WHERE alliance_id = ?", (alliance_id,))
                        admin_settings_count = cursor.rowcount
                        self.conn.commit()
                    except Exception as e:
                        logger.error(f"Partial alliance deletion failure (alliance db): {e}")
                        errors.append(f"alliance db: {e}")
                        try:
                            self.conn.rollback()
                        except Exception:
                            pass

                    try:
                        cursor = self.conn_users.execute("DELETE FROM users WHERE alliance = ?", (alliance_id,))
                        users_count_deleted = cursor.rowcount
                        self.conn_users.commit()
                    except Exception as e:
                        logger.error(f"Partial alliance deletion failure (users db): {e}")
                        errors.append(f"users db: {e}")
                        try:
                            self.conn_users.rollback()
                        except Exception:
                            pass

                    try:
                        cursor = self.conn_settings.execute("DELETE FROM adminserver WHERE alliances_id = ?", (alliance_id,))
                        admin_server_count = cursor.rowcount
                        self.conn_settings.commit()
                    except Exception as e:
                        logger.error(f"Partial alliance deletion failure (settings db): {e}")
                        errors.append(f"settings db: {e}")
                        try:
                            self.conn_settings.rollback()
                        except Exception:
                            pass

                    try:
                        cursor = self.conn_giftcode.execute("DELETE FROM giftcode_channel WHERE alliance_id = ?", (alliance_id,))
                        gift_channels_count = cursor.rowcount
                        cursor = self.conn_giftcode.execute("DELETE FROM giftcodecontrol WHERE alliance_id = ?", (alliance_id,))
                        gift_code_control_count = cursor.rowcount
                        self.conn_giftcode.commit()
                    except Exception as e:
                        logger.error(f"Partial alliance deletion failure (giftcode db): {e}")
                        errors.append(f"giftcode db: {e}")
                        try:
                            self.conn_giftcode.rollback()
                        except Exception:
                            pass

                    if errors:
                        logger.error(f"Alliance deletion completed with errors: {'; '.join(errors)}")

                    cleanup_embed = discord.Embed(
                        title="✅ Alliance Successfully Deleted",
                        description=(
                            f"Alliance **{alliance_name}** has been deleted.\n\n"
                            "**Cleaned Up Data:**\n"
                            f"🛡️ Alliance Records: {alliance_count}\n"
                            f"👥 Users Removed: {users_count_deleted}\n"
                            f"⚙️ Alliance Settings: {admin_settings_count}\n"
                            f"🏰 Admin Server Records: {admin_server_count}\n"
                            f"📢 Gift Channels: {gift_channels_count}\n"
                            f"📊 Gift Code Controls: {gift_code_control_count}"
                        ),
                        color=discord.Color.green()
                    )
                    cleanup_embed.set_footer(text="All related data has been successfully removed")
                    cleanup_embed.timestamp = discord.utils.utcnow()
                    
                    await button_interaction.response.edit_message(embed=cleanup_embed, view=None)
                    
                except Exception as e:
                    error_embed = discord.Embed(
                        title="❌ Error",
                        description=f"An error occurred while deleting the alliance: {str(e)}",
                        color=discord.Color.red()
                    )
                    await button_interaction.response.edit_message(embed=error_embed, view=None)

            async def cancel_callback(button_interaction: discord.Interaction):
                cancel_embed = discord.Embed(
                    title="❌ Deletion Cancelled",
                    description="Alliance deletion has been cancelled.",
                    color=discord.Color.grey()
                )
                await button_interaction.response.edit_message(embed=cancel_embed, view=None)

            confirm_button = discord.ui.Button(label="Confirm", style=discord.ButtonStyle.danger)
            cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.grey)
            confirm_button.callback = confirm_callback
            cancel_button.callback = cancel_callback
            confirm_view.add_item(confirm_button)
            confirm_view.add_item(cancel_button)

            await interaction.response.edit_message(embed=confirm_embed, view=confirm_view)

        except Exception as e:
            logger.error("Error in alliance_delete_callback: %s", e)
            await send_error(interaction, "An error occurred while processing the deletion.")

    async def show_main_menu(self, interaction: discord.Interaction):
        try:
            embed = discord.Embed(
                title="⚙️ Settings Menu",
                description=(
                    "Please select a category:\n\n"
                    "**Menu Categories**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "🏰 **Alliance Operations**\n"
                    "└ Manage alliances and settings\n\n"
                    "👥 **Alliance Member Operations**\n"
                    "└ Add, remove, and view members\n\n"
                    "🤖 **Bot Operations**\n"
                    "└ Configure bot settings\n\n"
                    "🎁 **Gift Code Operations**\n"
                    "└ Manage gift codes and rewards\n\n"
                    "📜 **Alliance History**\n"
                    "└ View alliance changes and history\n\n"
                    "🆘 **Support Operations**\n"
                    "└ Access support features\n\n"
                    "🔧 **Other Features**\n"
                    "└ Access other features\n\n"
                    "🌐 **Gift Code Scraper**\n"
                    "└ Configure web scraper sources\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=discord.Color.blue()
            )

            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Alliance Operations",
                emoji="🏰",
                style=discord.ButtonStyle.primary,
                custom_id="alliance_operations",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Member Operations",
                emoji="👥",
                style=discord.ButtonStyle.primary,
                custom_id="member_operations",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Bot Operations",
                emoji="🤖",
                style=discord.ButtonStyle.primary,
                custom_id="bot_operations",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="Gift Operations",
                emoji="🎁",
                style=discord.ButtonStyle.primary,
                custom_id="gift_code_operations",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="Alliance History",
                emoji="📜",
                style=discord.ButtonStyle.primary,
                custom_id="alliance_history",
                row=2
            ))
            view.add_item(discord.ui.Button(
                label="Support Operations",
                emoji="🆘",
                style=discord.ButtonStyle.primary,
                custom_id="support_operations",
                row=2
            ))
            view.add_item(discord.ui.Button(
                label="Other Features",
                emoji="🔧",
                style=discord.ButtonStyle.primary,
                custom_id="other_features",
                row=3
            ))
            view.add_item(discord.ui.Button(
                label="Gift Scraper",
                emoji="🌐",
                style=discord.ButtonStyle.primary,
                custom_id="scraper_settings",
                row=3
            ))

            try:
                await interaction.response.edit_message(embed=embed, view=view)
            except discord.InteractionResponded:
                logger.debug("InteractionResponded in show_main_menu, ignoring")

        except Exception as e:
            logger.exception("Error in show_main_menu: %s", e)

class AllianceModal(discord.ui.Modal):
    def __init__(self, title: str, default_name: str = "", default_interval: str = "0"):
        super().__init__(title=title)
        
        self.name = discord.ui.TextInput(
            label="Alliance Name",
            placeholder="Enter alliance name",
            default=default_name,
            required=True
        )
        self.add_item(self.name)
        
        self.interval = discord.ui.TextInput(
            label="Control Interval (minutes)",
            placeholder="Enter interval (0 to disable)",
            default=default_interval,
            required=True
        )
        self.add_item(self.interval)

    async def on_submit(self, interaction: discord.Interaction):
        self.interaction = interaction

class AllianceView(discord.ui.View):
    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    @discord.ui.button(
        label="Main Menu",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="alliance_main_menu"
    )
    async def main_menu_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_main_menu(interaction)

class PaginatedDeleteView(discord.ui.View):
    def __init__(self, pages, original_callback):
        super().__init__(timeout=300)
        self.current_page = 0
        self.pages = pages
        self.original_callback = original_callback
        self.total_pages = len(pages)
        self.update_view()

    def update_view(self):
        self.clear_items()
        
        select = discord.ui.Select(
            placeholder=f"Select alliance to delete ({self.current_page + 1}/{self.total_pages})",
            options=self.pages[self.current_page]
        )
        select.callback = self.original_callback
        self.add_item(select)
        
        previous_button = discord.ui.Button(
            label="◀️",
            style=discord.ButtonStyle.grey,
            custom_id="previous",
            disabled=(self.current_page == 0)
        )
        previous_button.callback = self.previous_callback
        self.add_item(previous_button)

        next_button = discord.ui.Button(
            label="▶️",
            style=discord.ButtonStyle.grey,
            custom_id="next",
            disabled=(self.current_page == len(self.pages) - 1)
        )
        next_button.callback = self.next_callback
        self.add_item(next_button)

    async def previous_callback(self, interaction: discord.Interaction):
        self.current_page = (self.current_page - 1) % len(self.pages)
        self.update_view()
        
        embed = discord.Embed(
            title="🗑️ Delete Alliance",
            description=(
                "**⚠️ Warning: This action cannot be undone!**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "1️⃣ Select an alliance from the dropdown menu\n"
                "2️⃣ Use ◀️ ▶️ buttons to navigate between pages\n\n"
                f"**Current Page:** {self.current_page + 1}/{self.total_pages}\n"
                f"**Total Alliances:** {sum(len(page) for page in self.pages)}\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.red()
        )
        embed.set_footer(text="⚠️ Warning: Deleting an alliance will remove all its data!")
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.response.edit_message(embed=embed, view=self)

    async def next_callback(self, interaction: discord.Interaction):
        self.current_page = (self.current_page + 1) % len(self.pages)
        self.update_view()
        
        embed = discord.Embed(
            title="🗑️ Delete Alliance",
            description=(
                "**⚠️ Warning: This action cannot be undone!**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "1️⃣ Select an alliance from the dropdown menu\n"
                "2️⃣ Use ◀️ ▶️ buttons to navigate between pages\n\n"
                f"**Current Page:** {self.current_page + 1}/{self.total_pages}\n"
                f"**Total Alliances:** {sum(len(page) for page in self.pages)}\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.red()
        )
        embed.set_footer(text="⚠️ Warning: Deleting an alliance will remove all its data!")
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.response.edit_message(embed=embed, view=self)

from .utils import PaginatedChannelView  # re-export

async def setup(bot):
    await bot.add_cog(Alliance(bot))