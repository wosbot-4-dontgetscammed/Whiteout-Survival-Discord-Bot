import discord
from datetime import datetime

from .database import DatabaseManager
from .gift_views import GiftView, CreateGiftCodeModal
from .log_config import get_logger
from .utils import AllianceSelectView, PaginatedChannelView, _create_monitored_task, build_embed, check_admin, check_global_admin, get_admin_info as _utils_get_admin_info


logger = get_logger("gift_operations")


class GiftUI:
    def __init__(self, cog):
        self.cog = cog  # Referenz zurück zum GiftOperations Cog
        self.bot = cog.bot
        self.conn = cog.conn  # giftcode DB
        self.settings_conn = cog.settings_conn
        self.alliance_conn = cog.alliance_conn

    async def get_admin_info(self, user_id):
        return _utils_get_admin_info(user_id)

    async def get_alliance_names(self, user_id, is_global=False):
        if is_global:
            cursor = self.alliance_conn.execute("SELECT name FROM alliance_list")
            return [row[0] for row in cursor.fetchall()]
        else:
            cursor = self.settings_conn.execute("""
                SELECT alliances_id FROM adminserver WHERE admin = ?
            """, (user_id,))
            alliance_ids = [row[0] for row in cursor.fetchall()]

            if alliance_ids:
                placeholders = ','.join('?' * len(alliance_ids))
                cursor = self.alliance_conn.execute(f"""
                    SELECT name FROM alliance_list
                    WHERE alliance_id IN ({placeholders})
                """, alliance_ids)
                return [row[0] for row in cursor.fetchall()]
            return []

    async def get_available_alliances(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        guild_id = interaction.guild_id if interaction.guild else None

        admin_info = await self.get_admin_info(user_id)
        if not admin_info:
            return []

        is_global = admin_info[1] == 1

        if is_global:
            cursor = self.alliance_conn.execute("SELECT alliance_id, name FROM alliance_list")
            return cursor.fetchall()

        if guild_id:
            cursor = self.alliance_conn.execute("""
                SELECT DISTINCT alliance_id, name
                FROM alliance_list
                WHERE discord_server_id = ?
            """, (guild_id,))
            guild_alliances = cursor.fetchall()

            cursor = self.settings_conn.execute("""
                SELECT alliances_id FROM adminserver WHERE admin = ?
            """, (user_id,))
            special_alliance_ids = [row[0] for row in cursor.fetchall()]

            if special_alliance_ids:
                placeholders = ','.join('?' * len(special_alliance_ids))
                cursor = self.alliance_conn.execute(f"""
                    SELECT alliance_id, name FROM alliance_list
                    WHERE alliance_id IN ({placeholders})
                """, special_alliance_ids)
                special_alliances = cursor.fetchall()
            else:
                special_alliances = []

            all_alliances = list(set(guild_alliances + special_alliances))
            return all_alliances

        return []

    async def setup_gift_channel(self, interaction: discord.Interaction):
        admin_info = await self.get_admin_info(interaction.user.id)
        if not admin_info:
            await interaction.response.send_message(
                "❌ You are not authorized to perform this action.",
                ephemeral=True
            )
            return

        available_alliances = await self.get_available_alliances(interaction)
        if not available_alliances:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ No Available Alliances",
                    description="You don't have access to any alliances.",
                    color=discord.Color.red()
                ),
                ephemeral=True
            )
            return

        alliances_with_counts = []
        for alliance_id, name in available_alliances:
            users_db = DatabaseManager.instance().get("users")
            cursor = users_db.cursor()
            cursor.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
            row = cursor.fetchone()
            member_count = row[0] if row else 0
            alliances_with_counts.append((alliance_id, name, member_count))

        cursor = self.conn.execute("SELECT alliance_id, channel_id FROM giftcode_channel")
        current_channels = dict(cursor.fetchall())

        alliance_embed = discord.Embed(
            title="📢 Gift Code Channel Setup",
            description=(
                "Please select an alliance to set up gift code channel:\n\n"
                "**Alliance List**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Select an alliance from the list below:\n"
            ),
            color=discord.Color.blue()
        )

        view = AllianceSelectView(alliances_with_counts, self.cog)

        async def alliance_callback(select_interaction: discord.Interaction):
            try:
                alliance_id = int(view.current_select.values[0])

                channel_embed = discord.Embed(
                    title="📢 Gift Code Channel Setup",
                    description=(
                        "**Instructions:**\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        "Please select a channel for gift codes\n\n"
                        "**Page:** 1/1\n"
                        f"**Total Channels:** {len(select_interaction.guild.text_channels)}"
                    ),
                    color=discord.Color.blue()
                )

                async def channel_select_callback(channel_interaction: discord.Interaction):
                    try:
                        channel_id = int(channel_interaction.data["values"][0])

                        self.conn.execute("""
                            INSERT OR REPLACE INTO giftcode_channel (alliance_id, channel_id)
                            VALUES (?, ?)
                        """, (alliance_id, channel_id))
                        self.conn.commit()

                        alliance_name = next((name for aid, name in available_alliances if aid == alliance_id), "Unknown Alliance")

                        success_embed = discord.Embed(
                            title="✅ Gift Code Channel Set",
                            description=(
                                f"Successfully set gift code channel:\n\n"
                                f"🏰 **Alliance:** {alliance_name}\n"
                                f"📝 **Channel:** <#{channel_id}>\n"
                            ),
                            color=discord.Color.green()
                        )

                        await channel_interaction.response.edit_message(
                            embed=success_embed,
                            view=None
                        )

                    except Exception as e:
                        logger.error(f"Error setting gift code channel: {e}")
                        await channel_interaction.response.send_message(
                            "❌ An error occurred while setting the gift code channel.",
                            ephemeral=True
                        )

                channels = select_interaction.guild.text_channels
                channel_view = PaginatedChannelView(channels, channel_select_callback)

                if not select_interaction.response.is_done():
                    await select_interaction.response.edit_message(
                        embed=channel_embed,
                        view=channel_view
                    )
                else:
                    await select_interaction.message.edit(
                        embed=channel_embed,
                        view=channel_view
                    )

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
            embed=alliance_embed,
            view=view,
            ephemeral=True
        )

    async def show_gift_menu(self, interaction: discord.Interaction):
        gift_menu_embed = discord.Embed(
            title="🎁 Gift Code Operations",
            description=(
                "Please select an operation:\n\n"
                "**Available Operations**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "🎫 **Create Gift Code**\n"
                "└ Generate new gift codes\n\n"
                "📋 **List Gift Codes**\n"
                "└ View all active codes\n\n"
                "⚙️ **Auto Gift Settings**\n"
                "└ Configure automatic gift code usage\n\n"
                "❌ **Delete Gift Code**\n"
                "└ Remove existing codes\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.gold()
        )

        view = GiftView(self.cog)
        try:
            await interaction.response.edit_message(embed=gift_menu_embed, view=view)
        except discord.InteractionResponded:
            logger.debug("InteractionResponded in show_gift_menu, ignoring")
        except Exception as e:
            logger.debug("Failed to show gift menu: %s", e)

    async def create_gift_code(self, interaction: discord.Interaction):
        if not check_admin(interaction.user.id):
            await interaction.response.send_message(
                "❌ You are not authorized to create gift codes.",
                ephemeral=True
            )
            return

        modal = CreateGiftCodeModal(self.cog)
        try:
            await interaction.response.send_modal(modal)
        except Exception as e:
            logger.error(f"Error showing modal: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while showing the gift code creation form.",
                    ephemeral=True
                )

    async def list_gift_codes(self, interaction: discord.Interaction):
        cursor = self.conn.execute("""
            SELECT
                gc.giftcode,
                gc.date,
                COUNT(DISTINCT ugc.fid) as used_count
            FROM gift_codes gc
            LEFT JOIN user_giftcodes ugc ON gc.giftcode = ugc.giftcode
            GROUP BY gc.giftcode
            ORDER BY gc.date DESC
        """)

        codes = cursor.fetchall()

        if not codes:
            await interaction.response.send_message(
                "No gift codes found in the database.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🎁 Active Gift Codes",
            color=discord.Color.blue()
        )

        for code, date, used_count in codes:
            embed.add_field(
                name=f"Code: {code}",
                value=f"Created: {date}\nUsed by: {used_count} users",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def delete_gift_code(self, interaction: discord.Interaction):
        try:
            if not check_global_admin(interaction.user.id):
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="❌ Unauthorized Access",
                        description="This action requires Global Admin privileges.",
                        color=discord.Color.red()
                    ),
                    ephemeral=True
                )
                return

            cursor = self.conn.execute("""
                SELECT
                    gc.giftcode,
                    gc.date,
                    COUNT(DISTINCT ugc.fid) as used_count
                FROM gift_codes gc
                LEFT JOIN user_giftcodes ugc ON gc.giftcode = ugc.giftcode
                GROUP BY gc.giftcode
                ORDER BY gc.date DESC
            """)

            codes = cursor.fetchall()

            if not codes:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="❌ No Gift Codes",
                        description="There are no gift codes in the database to delete.",
                        color=discord.Color.red()
                    ),
                    ephemeral=True
                )
                return

            select = discord.ui.Select(
                placeholder="Select a gift code to delete",
                options=[
                    discord.SelectOption(
                        label=f"Code: {code}",
                        description=f"Created: {date} | Used by: {used_count} users",
                        value=code
                    ) for code, date, used_count in codes
                ]
            )

            async def select_callback(select_interaction):
                selected_code = select_interaction.data["values"][0]

                confirm = discord.ui.Button(
                    style=discord.ButtonStyle.danger,
                    label="Confirm Delete",
                    custom_id="confirm"
                )
                cancel = discord.ui.Button(
                    style=discord.ButtonStyle.secondary,
                    label="Cancel",
                    custom_id="cancel"
                )

                async def button_callback(button_interaction):
                    try:
                        if button_interaction.data.get('custom_id') == "confirm":
                            try:
                                self.conn.execute("DELETE FROM gift_codes WHERE giftcode = ?", (selected_code,))
                                self.conn.execute("DELETE FROM user_giftcodes WHERE giftcode = ?", (selected_code,))
                                self.conn.commit()

                                success_embed = build_embed("✅ Gift Code Deleted", {
                                    "🎁 Gift Code": selected_code,
                                    "👤 Deleted by": button_interaction.user.mention,
                                    "⏰ Time": f"<t:{int(datetime.now().timestamp())}:R>",
                                }, header="Deletion Details")

                                await button_interaction.response.edit_message(
                                    embed=success_embed,
                                    view=None
                                )

                            except Exception as e:
                                await button_interaction.response.send_message(
                                    "❌ An error occurred while deleting the gift code.",
                                    ephemeral=True
                                )

                        else:
                            cancel_embed = discord.Embed(
                                title="❌ Deletion Cancelled",
                                description="The gift code deletion was cancelled.",
                                color=discord.Color.red()
                            )
                            await button_interaction.response.edit_message(
                                embed=cancel_embed,
                                view=None
                            )

                    except Exception as e:
                        logger.error(f"Button callback error: {str(e)}")
                        try:
                            await button_interaction.response.send_message(
                                "❌ An error occurred while processing the request.",
                                ephemeral=True
                            )
                        except (discord.HTTPException, discord.NotFound):
                            await button_interaction.followup.send(
                                "❌ An error occurred while processing the request.",
                                ephemeral=True
                            )

                confirm.callback = button_callback
                cancel.callback = button_callback

                confirm_view = discord.ui.View()
                confirm_view.add_item(confirm)
                confirm_view.add_item(cancel)

                confirmation_embed = build_embed("⚠️ Confirm Deletion", {
                    "🎁 Selected Code": selected_code,
                    "⚠️ Warning": "This action cannot be undone!",
                }, header="Gift Code Details", color=discord.Color.yellow())

                await select_interaction.response.edit_message(
                    embed=confirmation_embed,
                    view=confirm_view
                )

            select.callback = select_callback
            view = discord.ui.View()
            view.add_item(select)

            initial_embed = discord.Embed(
                title="🗑️ Delete Gift Code",
                description=(
                    f"**Instructions**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"1️⃣ Select a gift code from the menu below\n"
                    f"2️⃣ Confirm your selection\n"
                    f"3️⃣ The code will be permanently deleted\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                ),
                color=discord.Color.blue()
            )

            await interaction.response.send_message(
                embed=initial_embed,
                view=view,
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Delete gift code error: {str(e)}")
            await interaction.response.send_message(
                "❌ An error occurred while processing the request.",
                ephemeral=True
            )

    async def delete_gift_channel(self, interaction: discord.Interaction):
        admin_info = await self.get_admin_info(interaction.user.id)
        if not admin_info:
            await interaction.response.send_message(
                "❌ You are not authorized to perform this action.",
                ephemeral=True
            )
            return

        available_alliances = await self.get_available_alliances(interaction)
        if not available_alliances:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ No Available Alliances",
                    description="You don't have access to any alliances.",
                    color=discord.Color.red()
                ),
                ephemeral=True
            )
            return

        cursor = self.conn.execute("SELECT alliance_id, channel_id FROM giftcode_channel")
        current_channels = dict(cursor.fetchall())

        alliances_with_counts = []
        for alliance_id, name in available_alliances:
            if alliance_id in current_channels:
                users_db = DatabaseManager.instance().get("users")
                cursor = users_db.cursor()
                cursor.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                row = cursor.fetchone()
                member_count = row[0] if row else 0
                alliances_with_counts.append((alliance_id, name, member_count))

        if not alliances_with_counts:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ No Channels Set",
                    description="There are no gift code channels set for your alliances.",
                    color=discord.Color.red()
                ),
                ephemeral=True
            )
            return

        remove_embed = discord.Embed(
            title="🗑️ Remove Gift Code Channel",
            description=(
                "Select an alliance to remove its gift code channel:\n\n"
                "**Current Log Channels**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Select an alliance from the list below:\n"
            ),
            color=discord.Color.red()
        )

        view = AllianceSelectView(alliances_with_counts, self.cog)

        async def alliance_callback(select_interaction: discord.Interaction):
            try:
                alliance_id = int(view.current_select.values[0])

                cursor = self.conn.execute("SELECT channel_id FROM giftcode_channel WHERE alliance_id = ?", (alliance_id,))
                row = cursor.fetchone()
                if not row:
                    await select_interaction.response.send_message(
                        "❌ No gift code channel found for this alliance.",
                        ephemeral=True
                    )
                    return
                channel_id = row[0]

                alliance_name = next((name for aid, name in available_alliances if aid == alliance_id), "Unknown Alliance")

                confirm_embed = discord.Embed(
                    title="⚠️ Confirm Removal",
                    description=(
                        f"Are you sure you want to remove the gift code channel for:\n\n"
                        f"🏰 **Alliance:** {alliance_name}\n"
                        f"📝 **Channel:** <#{channel_id}>\n\n"
                        "This action cannot be undone!"
                    ),
                    color=discord.Color.yellow()
                )

                confirm_view = discord.ui.View()

                async def confirm_callback(button_interaction: discord.Interaction):
                    try:
                        self.conn.execute("DELETE FROM giftcode_channel WHERE alliance_id = ?", (alliance_id,))
                        self.conn.commit()

                        success_embed = discord.Embed(
                            title="✅ Gift Code Channel Removed",
                            description=(
                                f"Successfully removed gift code channel for:\n\n"
                                f"🏰 **Alliance:** {alliance_name}\n"
                                f"📝 **Channel:** <#{channel_id}>"
                            ),
                            color=discord.Color.green()
                        )

                        await button_interaction.response.edit_message(
                            embed=success_embed,
                            view=None
                        )

                    except Exception as e:
                        logger.error(f"Error removing gift code channel: {e}")
                        await button_interaction.response.send_message(
                            "❌ An error occurred while removing the gift code channel.",
                            ephemeral=True
                        )

                async def cancel_callback(button_interaction: discord.Interaction):
                    cancel_embed = discord.Embed(
                        title="❌ Removal Cancelled",
                        description="The gift code channel removal has been cancelled.",
                        color=discord.Color.red()
                    )
                    await button_interaction.response.edit_message(
                        embed=cancel_embed,
                        view=None
                    )

                confirm_button = discord.ui.Button(
                    label="Confirm",
                    emoji="✅",
                    style=discord.ButtonStyle.danger,
                    custom_id="confirm_remove"
                )
                confirm_button.callback = confirm_callback

                cancel_button = discord.ui.Button(
                    label="Cancel",
                    emoji="❌",
                    style=discord.ButtonStyle.secondary,
                    custom_id="cancel_remove"
                )
                cancel_button.callback = cancel_callback

                confirm_view.add_item(confirm_button)
                confirm_view.add_item(cancel_button)

                if not select_interaction.response.is_done():
                    await select_interaction.response.edit_message(
                        embed=confirm_embed,
                        view=confirm_view
                    )
                else:
                    await select_interaction.message.edit(
                        embed=confirm_embed,
                        view=confirm_view
                    )

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
            embed=remove_embed,
            view=view,
            ephemeral=True
        )

    async def setup_giftcode_auto(self, interaction: discord.Interaction):
        admin_info = await self.get_admin_info(interaction.user.id)
        if not admin_info:
            await interaction.response.send_message(
                "❌ You are not authorized to perform this action.",
                ephemeral=True
            )
            return

        available_alliances = await self.get_available_alliances(interaction)
        if not available_alliances:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ No Available Alliances",
                    description="You don't have access to any alliances.",
                    color=discord.Color.red()
                ),
                ephemeral=True
            )
            return

        cursor = self.conn.execute("SELECT alliance_id, status FROM giftcodecontrol")
        current_status = dict(cursor.fetchall())

        alliances_with_counts = []
        for alliance_id, name in available_alliances:
            users_db = DatabaseManager.instance().get("users")
            cursor = users_db.cursor()
            cursor.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
            row = cursor.fetchone()
            member_count = row[0] if row else 0
            alliances_with_counts.append((alliance_id, name, member_count))

        auto_gift_embed = discord.Embed(
            title="⚙️ Auto Gift Code Settings",
            description=(
                "Select an alliance to configure auto gift code:\n\n"
                "**Alliance List**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Select an alliance from the list below:\n"
            ),
            color=discord.Color.blue()
        )

        view = AllianceSelectView(alliances_with_counts, self.cog)

        view.current_select.options.insert(0, discord.SelectOption(
            label="ENABLE ALL ALLIANCES",
            value="enable_all",
            description="Enable auto gift code for all alliances",
            emoji="✅"
        ))

        view.current_select.options.insert(1, discord.SelectOption(
            label="DISABLE ALL ALLIANCES",
            value="disable_all",
            description="Disable auto gift code for all alliances",
            emoji="❌"
        ))

        async def alliance_callback(select_interaction: discord.Interaction):
            try:
                selected_value = view.current_select.values[0]

                if selected_value in ["enable_all", "disable_all"]:
                    status = 1 if selected_value == "enable_all" else 0

                    for alliance_id, _, _ in alliances_with_counts:
                        self.conn.execute(
                            """
                            INSERT INTO giftcodecontrol (alliance_id, status)
                            VALUES (?, ?)
                            ON CONFLICT(alliance_id)
                            DO UPDATE SET status = excluded.status
                            """,
                            (alliance_id, status)
                        )
                    self.conn.commit()

                    status_text = "enabled" if status == 1 else "disabled"
                    distribute_text = ""
                    if status == 1:
                        distribute_text = "\n📬 **Note:** Distributing existing gift codes to all alliances in the background..."
                    fields = {
                        "🌐 Scope": "All Alliances",
                        "📊 Status": f"Auto gift code {status_text}",
                        "👤 Updated by": select_interaction.user.mention,
                    }
                    if distribute_text:
                        fields["📬 Note"] = "Distributing existing gift codes to all alliances in the background..."
                    success_embed = build_embed("✅ Auto Gift Code Setting Updated", fields, header="Configuration Details")

                    await select_interaction.response.edit_message(
                        embed=success_embed,
                        view=None
                    )

                    if status == 1:
                        for alliance_id, _, _ in alliances_with_counts:
                            _create_monitored_task(self.cog.distributor.distribute_pending_codes_to_alliance(alliance_id), name=f"distribute_codes_alliance_{alliance_id}")

                    return

                alliance_id = int(selected_value)
                alliance_name = next((name for aid, name in available_alliances if aid == alliance_id), "Unknown")

                current_setting = "enabled" if current_status.get(alliance_id, 0) == 1 else "disabled"

                confirm_embed = build_embed("⚙️ Auto Gift Code Configuration", {
                    "🏰 Alliance": alliance_name,
                    "📊 Current Status": f"Auto gift code is {current_setting}",
                }, header="Alliance Details", color=discord.Color.yellow())

                confirm_view = discord.ui.View()

                async def button_callback(button_interaction: discord.Interaction):
                    try:
                        status = 1 if button_interaction.data['custom_id'] == "confirm" else 0

                        self.conn.execute(
                            """
                            INSERT INTO giftcodecontrol (alliance_id, status)
                            VALUES (?, ?)
                            ON CONFLICT(alliance_id)
                            DO UPDATE SET status = excluded.status
                            """,
                            (alliance_id, status)
                        )
                        self.conn.commit()

                        status_text = "enabled" if status == 1 else "disabled"
                        distribute_text = ""
                        if status == 1:
                            distribute_text = "\n📬 **Note:** Distributing existing gift codes in the background..."
                        fields = {
                            "🏰 Alliance": alliance_name,
                            "📊 Status": f"Auto gift code {status_text}",
                            "👤 Updated by": button_interaction.user.mention,
                        }
                        if distribute_text:
                            fields["📬 Note"] = "Distributing existing gift codes in the background..."
                        success_embed = build_embed("✅ Auto Gift Code Setting Updated", fields, header="Configuration Details")

                        await button_interaction.response.edit_message(
                            embed=success_embed,
                            view=None
                        )

                        if status == 1:
                            _create_monitored_task(self.cog.distributor.distribute_pending_codes_to_alliance(alliance_id), name=f"distribute_codes_alliance_{alliance_id}")

                    except Exception as e:
                        logger.error(f"Button callback error: {str(e)}")
                        if not button_interaction.response.is_done():
                            await button_interaction.response.send_message(
                                "❌ An error occurred while updating the settings.",
                                ephemeral=True
                            )
                        else:
                            await button_interaction.followup.send(
                                "❌ An error occurred while updating the settings.",
                                ephemeral=True
                            )

                confirm_button = discord.ui.Button(
                    label="Enable",
                    emoji="✅",
                    style=discord.ButtonStyle.success,
                    custom_id="confirm"
                )
                confirm_button.callback = button_callback

                deny_button = discord.ui.Button(
                    label="Disable",
                    emoji="❌",
                    style=discord.ButtonStyle.danger,
                    custom_id="deny"
                )
                deny_button.callback = button_callback

                confirm_view.add_item(confirm_button)
                confirm_view.add_item(deny_button)

                if not select_interaction.response.is_done():
                    await select_interaction.response.edit_message(
                        embed=confirm_embed,
                        view=confirm_view
                    )
                else:
                    await select_interaction.message.edit(
                        embed=confirm_embed,
                        view=confirm_view
                    )

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
            embed=auto_gift_embed,
            view=view,
            ephemeral=True
        )
