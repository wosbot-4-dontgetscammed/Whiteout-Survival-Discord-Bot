from __future__ import annotations

import discord
from discord.ext import commands
from datetime import datetime, timedelta
import pytz
import json
from typing import TYPE_CHECKING

from .log_config import get_logger
from .bear_trap_modals import (
    TimeSelectModal,
    NotificationTypeView,
)

if TYPE_CHECKING:
    from .bear_trap import BearTrap

logger = get_logger("bear_trap_views")


class BearTrapView(discord.ui.View):
    def __init__(self, cog: BearTrap):
        super().__init__(timeout=300)
        self.cog = cog

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(
        label="Set Time",
        emoji="⏰",
        style=discord.ButtonStyle.primary,
        custom_id="set_time",
        row=0
    )
    async def set_time_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.check_admin(interaction):
            return

        try:
            embed = discord.Embed(
                title="⏰ Notification Creation Method",
                description=(
                    "Please select how you want to create the notification:\n\n"
                    "**⚙️ Create in Discord**\n"
                    "• For simple notifications\n"
                    "• Quick setup\n"
                    "• Basic features\n\n"
                    "**🌐 Create on Website (Recommended)**\n"
                    "• For advanced notifications\n"
                    "• Customizable embeds\n"
                    "• Rich text formatting\n"
                    "• Custom color selection\n"
                    "• Add images and thumbnails\n"
                    "• Footer and author fields"
                ),
                color=discord.Color.blue()
            )

            view = discord.ui.View(timeout=300)

            discord_button = discord.ui.Button(
                label="Create in Discord",
                emoji="⚙️",
                style=discord.ButtonStyle.primary,
                custom_id="create_in_discord"
            )

            async def discord_button_callback(discord_interaction):
                modal = TimeSelectModal(self.cog)
                await discord_interaction.response.send_modal(modal)

            discord_button.callback = discord_button_callback

            web_button = discord.ui.Button(
                label="Create on Website",
                emoji="🌐",
                style=discord.ButtonStyle.success,
                custom_id="create_in_web"
            )

            async def web_button_callback(web_interaction):
                try:
                    editor_cog = self.cog.bot.get_cog('BearTrapEditor')
                    if not editor_cog:
                        await web_interaction.response.send_message(
                            "❌ BearTrapEditor module not found!",
                            ephemeral=True
                        )
                        return

                    view = editor_cog.TimeSelectOptionsView(editor_cog)
                    await view.start_setup(web_interaction)

                except Exception as e:
                    logger.error(f"Error in web button: {e}")
                    await web_interaction.response.send_message(
                        "❌ An error occurred while starting the website process!",
                        ephemeral=True
                    )

            web_button.callback = web_button_callback

            view.add_item(discord_button)
            view.add_item(web_button)

            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        except Exception as e:
            logger.exception(f"Error in set time button: {e} (type={type(e)})")

            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred!",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "❌ An error occurred!",
                        ephemeral=True
                    )
            except Exception as notify_error:
                logger.error(f"Failed to notify user about error: {notify_error}")

    @discord.ui.button(
        label="Remove Notification",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="remove_notification",
        row=0
    )
    async def remove_notification_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.check_admin(interaction):
            return
        try:
            notifications = await self.cog.get_notifications(interaction.guild_id)
            if not notifications:
                await interaction.response.send_message(
                    "❌ No notifications found for this server.",
                    ephemeral=True
                )
                return

            options = []
            for notif in notifications:
                display_description = notif[6].split('|')[-1] if '|' in notif[6] else notif[6]
                options.append(
                    discord.SelectOption(
                        label=f"{notif[3]:02d}:{notif[4]:02d} - {display_description[:30]}",
                        description=f"ID: {notif[0]}",
                        value=str(notif[0])
                    )
                )
            select = discord.ui.Select(
                placeholder="Select a notification to remove",
                options=options[:25]
            )

            async def select_callback(select_interaction):
                try:
                    notification_id = int(select_interaction.data["values"][0])
                    selected_notif = next(n for n in notifications if n[0] == notification_id)

                    notification_types = {
                        1: "Sends notifications at 30 minutes, 10 minutes, 5 minutes before and when time's up",
                        2: "Sends notifications at 10 minutes, 5 minutes before and when time's up",
                        3: "Sends notifications at 5 minutes before and when time's up",
                        4: "Sends notification only 5 minutes before",
                        5: "Sends notification only when time's up",
                        6: "Sends notifications at custom times"
                    }

                    mention_type = selected_notif[8]
                    if mention_type == "everyone":
                        mention_display = "@everyone"
                    elif mention_type.startswith("role_"):
                        role_id = int(mention_type.split('_')[1])
                        role = select_interaction.guild.get_role(role_id)
                        mention_display = f"@{role.name}" if role else f"Role: {role_id}"
                    elif mention_type.startswith("member_"):
                        member_id = int(mention_type.split('_')[1])
                        member = select_interaction.guild.get_member(member_id)
                        mention_display = f"@{member.display_name}" if member else f"Member: {member_id}"
                    else:
                        mention_display = "Unknown"

                    if not selected_notif[9]:
                        repeat_text = "❌ No repeat"
                    else:
                        minutes = selected_notif[10]
                        if minutes == 1:
                            repeat_text = "🔄 Repeats every minute"
                        elif minutes == 60:
                            repeat_text = "🔄 Repeats every hour"
                        elif minutes == 1440:
                            repeat_text = "🔄 Repeats daily"
                        elif minutes == 2880:
                            repeat_text = "🔄 Repeats every 2 days"
                        elif minutes == 4320:
                            repeat_text = "🔄 Repeats every 3 days"
                        elif minutes == 10080:
                            repeat_text = "🔄 Repeats weekly"
                        else:
                            repeat_text = f"🔄 Repeats every {minutes} minutes"

                    if selected_notif[15] is not None:
                        next_time_str = datetime.fromisoformat(selected_notif[15]).strftime('%d/%m/%Y')
                    else:
                        next_time_str = "Not scheduled"

                    embed = discord.Embed(
                        title="🗑️ Remove Notification",
                        description=(
                            f"**📅 Date:** {next_time_str}\n"
                            f"**⏰ Time:** {selected_notif[3]:02d}:{selected_notif[4]:02d} {selected_notif[5]}\n"
                            f"**📢 Channel:** <#{selected_notif[2]}>\n"
                            f"**📝 Description:** {selected_notif[6].split('|')[-1] if '|' in selected_notif[6] else selected_notif[6]}\n\n"
                            f"**⚙️ Notification Type**\n{notification_types[selected_notif[7]]}\n\n"
                            f"**👥 Mentions:** {mention_display}\n"
                            f"**🔄 Repeat:** {repeat_text}\n\n"
                            "Are you sure you want to remove this notification?"
                        ),
                        color=discord.Color.red()
                    )

                    confirm_view = discord.ui.View()

                    async def confirm_callback(confirm_interaction):
                        try:
                            self.cog.conn.execute("DELETE FROM bear_notification_embeds WHERE notification_id = ?", (notification_id,))

                            self.cog.conn.execute("DELETE FROM notification_history WHERE notification_id = ?", (notification_id,))

                            self.cog.conn.execute("DELETE FROM bear_notifications WHERE id = ?", (notification_id,))

                            self.cog.conn.commit()
                            await confirm_interaction.response.edit_message(
                                content="✅ Notification and its history have been removed successfully.",
                                embed=None,
                                view=None
                            )
                        except Exception as e:
                            logger.error(f"Error removing notification: {e}")
                            await confirm_interaction.response.send_message(
                                "❌ An error occurred while removing the notification.",
                                ephemeral=True
                            )

                    async def cancel_callback(cancel_interaction):
                        await cancel_interaction.response.edit_message(
                            content="❌ Removal cancelled.",
                            embed=None,
                            view=None
                        )

                    confirm_button = discord.ui.Button(label="Confirm", style=discord.ButtonStyle.danger)
                    cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)

                    confirm_button.callback = confirm_callback
                    cancel_button.callback = cancel_callback

                    confirm_view.add_item(confirm_button)
                    confirm_view.add_item(cancel_button)

                    await select_interaction.response.edit_message(embed=embed, view=confirm_view)
                except Exception as e:
                    logger.error(f"Error in remove notification callback: {e}")
                    await select_interaction.response.send_message(
                        "❌ An error occurred while removing the notification.",
                        ephemeral=True
                    )

            select.callback = select_callback
            view = discord.ui.View()
            view.add_item(select)

            await interaction.response.send_message(
                "Select a notification to remove:",
                view=view,
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Error in remove notification: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while loading notifications.",
                ephemeral=True
            )

    @discord.ui.button(
        label="View Notifications",
        emoji="📋",
        style=discord.ButtonStyle.primary,
        custom_id="view_notifications",
        row=1
    )
    async def view_notifications_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.check_admin(interaction):
            return
        try:
            notifications = await self.cog.get_notifications(interaction.guild_id)
            if not notifications:
                await interaction.response.send_message(
                    "❌ No notifications found for this server.",
                    ephemeral=True
                )
                return

            options = []
            for notif in notifications:
                status = "🟢 Enabled" if notif[11] else "🔴 Disabled"

                if "EMBED_MESSAGE:" in notif[6]:
                    cursor = self.cog.conn.execute("""
                        SELECT title, description
                        FROM bear_notification_embeds
                        WHERE notification_id = ?
                    """, (notif[0],))
                    embed_data = cursor.fetchone()

                    if embed_data and embed_data[0]:
                        display_description = f"📝 Embed: {embed_data[0]}"
                    else:
                        display_description = "📝 Embed Message"
                else:
                    display_description = notif[6].split('|')[-1] if '|' in notif[6] else notif[6]
                    if display_description.startswith("PLAIN_MESSAGE:"):
                        display_description = display_description.replace("PLAIN_MESSAGE:", "✍️ ")

                options.append(
                    discord.SelectOption(
                        label=f"{notif[3]:02d}:{notif[4]:02d} - {display_description[:30]}",
                        description=f"ID: {notif[0]} | {status}",
                        value=str(notif[0])
                    )
                )
            select = discord.ui.Select(
                placeholder="Select a notification to view details",
                options=options[:25]
            )

            view = discord.ui.View()

            async def select_callback(select_interaction):
                try:
                    notification_id = int(select_interaction.data["values"][0])
                    selected_notif = next(n for n in notifications if n[0] == notification_id)

                    notification_types = {
                        1: "Sends notifications at 30 minutes, 10 minutes, 5 minutes before and when time's up",
                        2: "Sends notifications at 10 minutes, 5 minutes before and when time's up",
                        3: "Sends notifications at 5 minutes before and when time's up",
                        4: "Sends notification only 5 minutes before",
                        5: "Sends notification only when time's up",
                        6: "Sends notifications at custom times"
                    }

                    mention_type = selected_notif[8]
                    if mention_type == "everyone":
                        mention_display = "@everyone"
                    elif mention_type.startswith("role_"):
                        role_id = int(mention_type.split('_')[1])
                        role = select_interaction.guild.get_role(role_id)
                        mention_display = f"@{role.name}" if role else f"Role: {role_id}"
                    elif mention_type.startswith("member_"):
                        member_id = int(mention_type.split('_')[1])
                        member = select_interaction.guild.get_member(member_id)
                        mention_display = f"@{member.display_name}" if member else f"Member: {member_id}"
                    else:
                        mention_display = "Unknown"

                    if not selected_notif[9]:
                        repeat_text = "❌ No repeat"
                    else:
                        minutes = selected_notif[10]
                        if minutes == 1:
                            repeat_text = "🔄 Repeats every minute"
                        elif minutes == 60:
                            repeat_text = "🔄 Repeats every hour"
                        elif minutes == 1440:
                            repeat_text = "🔄 Repeats daily"
                        elif minutes == 2880:
                            repeat_text = "🔄 Repeats every 2 days"
                        elif minutes == 4320:
                            repeat_text = "🔄 Repeats every 3 days"
                        elif minutes == 10080:
                            repeat_text = "🔄 Repeats weekly"
                        else:
                            repeat_text = f"🔄 Repeats every {minutes} minutes"

                    embed_data = None
                    if "EMBED_MESSAGE:" in selected_notif[6]:
                        cursor = self.cog.conn.execute("""
                            SELECT title, description, color, image_url, thumbnail_url, footer, author, mention_message
                            FROM bear_notification_embeds
                            WHERE notification_id = ?
                        """, (notification_id,))
                        embed_result = cursor.fetchone()
                        if embed_result:
                            embed_data = {
                                'title': embed_result[0],
                                'description': embed_result[1],
                                'color': embed_result[2],
                                'image_url': embed_result[3],
                                'thumbnail_url': embed_result[4],
                                'footer': embed_result[5],
                                'author': embed_result[6],
                                'mention_message': embed_result[7]
                            }

                    if selected_notif[15] is not None:
                        next_time_str = datetime.fromisoformat(selected_notif[15]).strftime('%d/%m/%Y')
                    else:
                        next_time_str = "Not scheduled"

                    details_embed = discord.Embed(
                        title="📋 Notification Details",
                        description=(
                            f"**📅 Date:** {next_time_str}\n"
                            f"**⏰ Time:** {selected_notif[3]:02d}:{selected_notif[4]:02d} {selected_notif[5]}\n"
                            f"**📢 Channel:** <#{selected_notif[2]}>\n"
                            f"**📝 Description:** {selected_notif[6].split('|')[-1] if '|' in selected_notif[6] else selected_notif[6]}\n\n"
                            f"**⚙️ Notification Type**\n{notification_types[selected_notif[7]]}\n\n"
                            f"**👥 Mentions:** {mention_display}\n"
                            f"**🔄 Repeat:** {repeat_text}"
                        ),
                        color=discord.Color.blue()
                    )

                    if embed_data:
                        preview_embed = discord.Embed(
                            title=embed_data['title'] if embed_data['title'] else "No Title",
                            description=embed_data['description'] if embed_data['description'] else "No Description",
                            color=embed_data['color'] if embed_data['color'] else discord.Color.blue()
                        )

                        if embed_data['image_url']:
                            preview_embed.set_image(url=embed_data['image_url'])
                        if embed_data['thumbnail_url']:
                            preview_embed.set_thumbnail(url=embed_data['thumbnail_url'])
                        if embed_data['footer']:
                            preview_embed.set_footer(text=embed_data['footer'])
                        if embed_data['author']:
                            preview_embed.set_author(name=embed_data['author'])

                        mention_preview = ""
                        if embed_data['mention_message']:
                            mention_preview = embed_data['mention_message']
                            example_time = "30 minutes"
                            if "%t" in mention_preview:
                                mention_preview = mention_preview.replace("%t", example_time)
                            if "{time}" in mention_preview:
                                mention_preview = mention_preview.replace("{time}", example_time)

                        copyable_data = {
                            'title': embed_data['title'],
                            'description': embed_data['description'],
                            'color': embed_data['color'],
                            'footer': embed_data['footer'],
                            'author': embed_data['author'],
                            'image_url': embed_data['image_url'],
                            'thumbnail_url': embed_data['thumbnail_url'],
                            'mention_message': embed_data['mention_message']
                        }

                        embed_json = json.dumps(copyable_data, indent=2)

                        view = discord.ui.View()
                        view.add_item(select)

                        content = "**📋 Notification Details**\n\n"
                        content += f"**Embed Code:**\n```json\n{embed_json}\n```\n"
                        if mention_preview:
                            content += f"**Message Preview:**\n{mention_preview}"

                        await select_interaction.response.edit_message(
                            content=content,
                            embeds=[details_embed, preview_embed],
                            view=view
                        )
                    else:
                        view = discord.ui.View()
                        view.add_item(select)

                        message_preview = None
                        if "PLAIN_MESSAGE:" in selected_notif[6]:
                            message_preview = selected_notif[6].replace("PLAIN_MESSAGE:", "")

                        await select_interaction.response.edit_message(
                            content="**📋 Notification Details**" +
                                  (f"\n\n**Message Preview:**\n{message_preview}" if message_preview else ""),
                            embed=details_embed,
                            view=view
                        )

                except Exception as e:
                    logger.error(f"Error in select callback: {e}")
                    await select_interaction.response.send_message(
                        "❌ An error occurred while processing your selection.",
                        ephemeral=True
                    )

            select.callback = select_callback
            view.add_item(select)

            await interaction.response.send_message(
                "Select a notification to view details:",
                view=view,
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Error viewing notifications: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while loading notifications.",
                ephemeral=True
            )

    @discord.ui.button(
        label="Enable/Disable",
        emoji="✅",
        style=discord.ButtonStyle.primary,
        custom_id="toggle_notifications",
        row=1
    )
    async def toggle_notifications_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.check_admin(interaction):
            return
        try:
            notifications = await self.cog.get_notifications(interaction.guild_id)
            if not notifications:
                await interaction.response.send_message(
                    "❌ No notifications found for this server.",
                    ephemeral=True
                )
                return

            options = []
            for notif in notifications:
                status = "🟢 Enabled" if notif[11] else "🔴 Disabled"
                display_description = notif[6].split('|')[-1] if '|' in notif[6] else notif[6]
                options.append(
                    discord.SelectOption(
                        label=f"{notif[3]:02d}:{notif[4]:02d} - {display_description[:30]}",
                        description=f"ID: {notif[0]} | {status}",
                        value=str(notif[0])
                    )
                )
            select = discord.ui.Select(
                placeholder="Select a notification to toggle",
                options=options[:25]
            )

            async def select_callback(select_interaction):
                notification_id = int(select_interaction.data["values"][0])
                current_status = next(n[11] for n in notifications if n[0] == notification_id)
                new_status = not current_status

                if await self.cog.toggle_notification(notification_id, new_status):
                    status_text = "enabled" if new_status else "disabled"
                    embed = discord.Embed(
                        title="✅ Notification Status Updated",
                        description=f"The notification has been {status_text}.",
                        color=discord.Color.green() if new_status else discord.Color.red()
                    )
                    await select_interaction.response.edit_message(embed=embed, view=None)
                else:
                    await select_interaction.response.send_message(
                        "❌ Failed to update notification status.",
                        ephemeral=True
                    )

            select.callback = select_callback
            view = discord.ui.View()
            view.add_item(select)

            await interaction.response.send_message(
                "Select a notification to toggle:",
                view=view,
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Error in toggle notifications: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while loading notifications.",
                ephemeral=True
            )

    @discord.ui.button(
        label="Back",
        emoji="◀️",
        style=discord.ButtonStyle.secondary,
        custom_id="bear_trap_back",
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
            logger.error(f"Error returning to Other Features menu: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while returning to Other Features menu.",
                ephemeral=True
            )

    @discord.ui.button(
        label="Main Menu",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="bear_trap_main_menu",
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
            logger.error(f"Error returning to main menu: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while returning to main menu.",
                ephemeral=True
            )

    @discord.ui.button(
        label="Edit",
        emoji="✏️",
        style=discord.ButtonStyle.primary,
        custom_id="edit_notification",
        row=1
    )
    async def edit_notification_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.check_admin(interaction):
            return
        try:
            notifications = await self.cog.get_notifications(interaction.guild_id)
            if not notifications:
                await interaction.response.send_message(
                    "❌ No notifications found to edit in this server.",
                    ephemeral=True
                )
                return

            options = []
            for notif in notifications:
                status = "🟢 Enabled" if notif[11] else "🔴 Disabled"

                if "EMBED_MESSAGE:" in notif[6]:
                    cursor = self.cog.conn.execute("""
                        SELECT title, description
                        FROM bear_notification_embeds
                        WHERE notification_id = ?
                    """, (notif[0],))
                    embed_data = cursor.fetchone()

                    if embed_data and embed_data[0]:
                        display_description = f"📝 Embed: {embed_data[0]}"
                    else:
                        display_description = "📝 Embed Message"
                else:
                    display_description = notif[6].split('|')[-1] if '|' in notif[6] else notif[6]
                    if display_description.startswith("PLAIN_MESSAGE:"):
                        display_description = display_description.replace("PLAIN_MESSAGE:", "✍️ ")

                options.append(
                    discord.SelectOption(
                        label=f"{notif[3]:02d}:{notif[4]:02d} - {display_description[:30]}",
                        description=f"ID: {notif[0]} | {status}",
                        value=str(notif[0])
                    )
                )

            select = discord.ui.Select(
                placeholder="Select a notification to edit",
                options=options[:25]
            )

            async def select_callback(select_interaction):
                try:
                    notification_id = int(select_interaction.data["values"][0])
                    editor_cog = self.cog.bot.get_cog('BearTrapEditor')
                    if editor_cog:
                        await editor_cog.start_edit_process(select_interaction, notification_id)
                    else:
                        await select_interaction.response.send_message(
                            "❌ Editor module not found!",
                            ephemeral=True
                        )
                except Exception as e:
                    logger.error(f"Error in edit notification callback: {e}")
                    await select_interaction.response.send_message(
                        "❌ An error occurred during editing!",
                        ephemeral=True
                    )

            select.callback = select_callback
            view = discord.ui.View()
            view.add_item(select)

            await interaction.response.send_message(
                "Select the notification you want to edit:",
                view=view,
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Error in edit button: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while starting the edit process!",
                ephemeral=True
            )


class ChannelSelectView(discord.ui.View):
    def __init__(self, cog: BearTrap, start_date, hour, minute, timezone, message_data, original_message):
        super().__init__(timeout=300)
        self.cog = cog
        self.start_date = start_date
        self.hour = hour
        self.minute = minute
        self.timezone = timezone
        self.message_data = message_data
        self.original_message = original_message

        self.add_item(ChannelSelectMenu(self))


class ChannelSelectMenu(discord.ui.ChannelSelect):
    def __init__(self, view):
        self.parent_view = view
        super().__init__(
            placeholder="Select a channel for notifications",
            channel_types=[
                discord.ChannelType.text,
                discord.ChannelType.news,
                discord.ChannelType.forum,
                discord.ChannelType.news_thread,
                discord.ChannelType.public_thread,
                discord.ChannelType.private_thread,
                discord.ChannelType.stage_voice
            ],
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            channel = self.values[0]
            actual_channel = interaction.guild.get_channel(channel.id)
            if actual_channel is None:
                await interaction.response.send_message(
                    "❌ Could not find the selected channel!",
                    ephemeral=True
                )
                return
            if not actual_channel.permissions_for(interaction.guild.me).send_messages:
                await interaction.response.send_message(
                    "❌ I don't have permission to send messages in this channel!",
                    ephemeral=True
                )
                return

            embed = discord.Embed(
                title="⏰ Select Notification Type",
                description=(
                    "Choose when to send notifications:\n\n"
                    "**30m, 10m, 5m & Time**\n"
                    "• 30 minutes before\n"
                    "• 10 minutes before\n"
                    "• 5 minutes before\n"
                    "• When time's up\n\n"
                    "**10m, 5m & Time**\n"
                    "• 10 minutes before\n"
                    "• 5 minutes before\n"
                    "• When time's up\n\n"
                    "**5m & Time**\n"
                    "• 5 minutes before\n"
                    "• When time's up\n\n"
                    "**Only 5m**\n"
                    "• Only 5 minutes before\n\n"
                    "**Only Time**\n"
                    "• Only when time's up\n\n"
                    "**Custom Times**\n"
                    "• Set your own notification times"
                ),
                color=discord.Color.blue()
            )

            view = NotificationTypeView(
                self.parent_view.cog,
                self.parent_view.start_date,
                self.parent_view.hour,
                self.parent_view.minute,
                self.parent_view.timezone,
                self.parent_view.message_data,
                channel.id,
                self.parent_view.original_message
            )

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=view
            )

        except Exception as e:
            logger.error(f"Error in channel select callback: {e}")
            try:
                await interaction.response.send_message(
                    "❌ An error occurred while processing your selection!",
                    ephemeral=True
                )
            except discord.InteractionResponded:
                await interaction.followup.send(
                    "❌ An error occurred while processing your selection!",
                    ephemeral=True
                )


class ImportEmbedModal(discord.ui.Modal):
    def __init__(self, embed_view):
        super().__init__(title="Import Embed")
        self.embed_view = embed_view

        self.embed_code = discord.ui.TextInput(
            label="Embed Code",
            placeholder="Paste the embed code here...",
            style=discord.TextStyle.paragraph,
            required=True
        )
        self.add_item(self.embed_code)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            embed_data = json.loads(self.embed_code.value)

            self.embed_view.embed_data.update({
                'title': embed_data.get('title') or '',
                'description': embed_data.get('description') or '',
                'color': embed_data.get('color', discord.Color.blue().value),
                'footer': embed_data.get('footer') or '',
                'author': embed_data.get('author') or '',
                'image_url': embed_data.get('image_url') or '',
                'thumbnail_url': embed_data.get('thumbnail_url') or '',
                'mention_message': embed_data.get('mention_message') or '@tag'
            })

            await self.embed_view.update_embed(interaction)
            await interaction.followup.send(
                "✅ Embed imported successfully!",
                ephemeral=True
            )

        except json.JSONDecodeError:
            await interaction.response.send_message(
                "❌ Invalid embed code format. Please make sure you copied the entire code correctly.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error importing embed: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred while importing the embed.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "❌ An error occurred while importing the embed.",
                        ephemeral=True
                    )
            except (discord.HTTPException, discord.NotFound):
                pass
