from __future__ import annotations

import discord
from discord.ext import commands
from datetime import datetime, timedelta
import pytz
import json
from typing import TYPE_CHECKING

from .log_config import get_logger

if TYPE_CHECKING:
    from .bear_trap import BearTrap

logger = get_logger("bear_trap_modals")


class RepeatOptionView(discord.ui.View):
    def __init__(self, cog: BearTrap, start_date, hour, minute, timezone, description, channel_id, notification_type, mention_type, original_message):
        super().__init__(timeout=300)
        self.cog = cog
        self.start_date = start_date
        self.hour = hour
        self.minute = minute
        self.timezone = timezone
        self.description = description
        self.channel_id = channel_id
        self.notification_type = notification_type
        self.mention_type = mention_type
        self.original_message = original_message

    @discord.ui.button(label="No Repeat", style=discord.ButtonStyle.danger, custom_id="no_repeat")
    async def no_repeat_button(self, interaction, button):
        await self.save_notification(interaction, False)

    @discord.ui.button(label="Custom Interval", style=discord.ButtonStyle.primary, custom_id="custom_interval")
    async def custom_interval_button(self, interaction, button):
        modal = RepeatIntervalModal(self)
        await interaction.response.send_modal(modal)

    async def save_notification(self, interaction, repeat, repeat_minutes=0, interval_text=None):
        try:
            notification_id = await self.cog.save_notification(
                guild_id=interaction.guild_id,
                channel_id=self.channel_id,
                start_date=self.start_date,
                hour=self.hour,
                minute=self.minute,
                timezone=self.timezone,
                description=self.description,
                created_by=interaction.user.id,
                notification_type=self.notification_type,
                mention_type=self.mention_type,
                repeat_48h=repeat,
                repeat_minutes=repeat_minutes
            )

            notification_types = {
                1: "Sends notifications at 30 minutes, 10 minutes, 5 minutes before and when time's up",
                2: "Sends notifications at 10 minutes, 5 minutes before and when time's up",
                3: "Sends notifications at 5 minutes before and when time's up",
                4: "Sends notification only 5 minutes before",
                5: "Sends notification only when time's up",
                6: "Sends notifications at custom times"
            }

            if self.mention_type == "everyone":
                mention_display = "@everyone"
            elif self.mention_type.startswith("role_"):
                role_id = int(self.mention_type.split('_')[1])
                role = interaction.guild.get_role(role_id)
                mention_display = f"@{role.name}" if role else f"Role: {role_id}"
            elif self.mention_type.startswith("member_"):
                member_id = int(self.mention_type.split('_')[1])
                member = interaction.guild.get_member(member_id)
                mention_display = f"@{member.display_name}" if member else f"Member: {member_id}"
            else:
                mention_display = "No Mention"

            if not repeat:
                repeat_text = "❌ No repeat"
            elif interval_text:
                repeat_text = f"🔄 Repeats every {interval_text}"
            else:
                minutes = repeat_minutes
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

            embed = discord.Embed(
                title="✅ Notification Set Successfully",
                description=(
                    f"**📅 Date:** {self.start_date.strftime('%d/%m/%Y')}\n"
                    f"**⏰ Time:** {self.hour:02d}:{self.minute:02d} {self.timezone}\n"
                    f"**📢 Channel:** <#{self.channel_id}>\n"
                    f"**📝 Description:** {self.description.split('|')[-1] if '|' in self.description else self.description}\n\n"
                    f"**⚙️ Notification Type**\n{notification_types[self.notification_type]}\n\n"
                    f"**👥 Mentions:** {mention_display}\n"
                    f"**🔄 Repeat:** {repeat_text}"
                ),
                color=discord.Color.green()
            )

            embed.set_footer(text="Created at")
            embed.timestamp = datetime.now()

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=None
            )

        except Exception as e:
            logger.error(f"Error saving notification: {e}")
            await interaction.followup.send(
                "❌ An error occurred while saving the notification.",
                ephemeral=True
            )


class RepeatIntervalModal(discord.ui.Modal):
    def __init__(self, repeat_view: RepeatOptionView):
        super().__init__(title="Set Repeat Interval")
        self.repeat_view = repeat_view

        self.months = discord.ui.TextInput(
            label="Months",
            placeholder="Enter number of months (e.g., 1)",
            min_length=0,
            max_length=2,
            required=False,
            default="0",
            style=discord.TextStyle.short
        )

        self.weeks = discord.ui.TextInput(
            label="Weeks",
            placeholder="Enter number of weeks (e.g., 2)",
            min_length=0,
            max_length=2,
            required=False,
            default="0",
            style=discord.TextStyle.short
        )

        self.days = discord.ui.TextInput(
            label="Days",
            placeholder="Enter number of days (e.g., 3)",
            min_length=0,
            max_length=2,
            required=False,
            default="0",
            style=discord.TextStyle.short
        )

        self.hours = discord.ui.TextInput(
            label="Hours",
            placeholder="Enter number of hours (e.g., 12)",
            min_length=0,
            max_length=2,
            required=False,
            default="0",
            style=discord.TextStyle.short
        )

        self.minutes = discord.ui.TextInput(
            label="Minutes",
            placeholder="Enter number of minutes (e.g., 30)",
            min_length=0,
            max_length=2,
            required=False,
            default="0",
            style=discord.TextStyle.short
        )

        for item in [self.months, self.weeks, self.days, self.hours, self.minutes]:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            try:
                months = int(self.months.value)
                weeks = int(self.weeks.value)
                days = int(self.days.value)
                hours = int(self.hours.value)
                minutes = int(self.minutes.value)
            except ValueError:
                await interaction.response.send_message(
                    "❌ Please enter valid numbers for all fields!",
                    ephemeral=True
                )
                return

            if not any([months > 0, weeks > 0, days > 0, hours > 0, minutes > 0]):
                await interaction.response.send_message(
                    "❌ Please enter at least one time interval greater than 0!",
                    ephemeral=True
                )
                return

            total_minutes = (months * 30 * 24 * 60) + (weeks * 7 * 24 * 60) + (days * 24 * 60) + (hours * 60) + minutes

            interval_parts = []
            if months > 0:
                interval_parts.append(f"{months} month{'s' if months > 1 else ''}")
            if weeks > 0:
                interval_parts.append(f"{weeks} week{'s' if weeks > 1 else ''}")
            if days > 0:
                interval_parts.append(f"{days} day{'s' if days > 1 else ''}")
            if hours > 0:
                interval_parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
            if minutes > 0:
                interval_parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")

            if len(interval_parts) > 1:
                interval_text = ", ".join(interval_parts[:-1]) + " and " + interval_parts[-1]
            else:
                interval_text = interval_parts[0]

            await self.repeat_view.save_notification(interaction, True, total_minutes, interval_text)

        except Exception as e:
            logger.error(f"Error in repeat interval modal: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while setting the repeat interval.",
                ephemeral=True
            )


class TextInputModal(discord.ui.Modal):
    def __init__(self, title, label, placeholder, default_value="", max_length=None, style=discord.TextStyle.short):
        super().__init__(title=title)
        self.value = None
        self.input = discord.ui.TextInput(
            label=label,
            placeholder=placeholder,
            default=default_value,
            max_length=max_length,
            style=style
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        self.value = self.input.value
        await interaction.response.defer()


class EmbedEditorView(discord.ui.View):
    def __init__(self, cog: BearTrap, start_date, hour, minute, timezone, original_message):
        super().__init__(timeout=300)
        self.cog = cog
        self.start_date = start_date
        self.hour = hour
        self.minute = minute
        self.timezone = timezone
        self.original_message = original_message
        self.embed_data = {
            "title": "⏰ Bear Trap",
            "description": "Add a description...",
            "color": discord.Color.blue().value,
            "footer": "Bear Trap Notification System",
            "author": "Bear Trap",
            "mention_message": ""
        }

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except Exception:
            pass

    async def update_embed(self, interaction: discord.Interaction):
        try:
            example_time = "30 minutes"
            embed = discord.Embed(color=self.embed_data.get("color", discord.Color.blue().value))

            if "title" in self.embed_data:
                title = self.embed_data["title"].replace("%t", example_time).replace("{time}", example_time)
                embed.title = title
            if "description" in self.embed_data:
                description = self.embed_data["description"].replace("%t", example_time).replace("{time}", example_time)
                embed.description = description
            if "footer" in self.embed_data:
                footer = self.embed_data["footer"].replace("%t", example_time).replace("{time}", example_time)
                embed.set_footer(text=footer)
            if "author" in self.embed_data:
                author = self.embed_data["author"].replace("%t", example_time).replace("{time}", example_time)
                embed.set_author(name=author)
            if "image_url" in self.embed_data and self.embed_data["image_url"]:
                embed.set_image(url=self.embed_data["image_url"])
            if "thumbnail_url" in self.embed_data and self.embed_data["thumbnail_url"]:
                embed.set_thumbnail(url=self.embed_data["thumbnail_url"])

            mention_preview = self.embed_data.get('mention_message', '@tag')
            if mention_preview:
                mention_preview = mention_preview.replace("%t", example_time)
                mention_preview = mention_preview.replace("{time}", example_time)

            content = (
                "📝 **Embed Editor**\n\n"
                "**Note:** \n"
                "• Use `%t` or `{time}` to show the remaining time\n"
                "• Use `@tag` for mentions (will be replaced with the actual mention)\n"
                "• You can use these in title, description, footer, and author fields\n"
                "• Time will automatically show with appropriate units (minutes/hours/days)\n\n"
                f"Currently showing '{example_time}' as an example.\n\n"
                f"**Current Mention Message Preview:**\n"
                f"{mention_preview}\n\n"
            )

            if not interaction.response.is_done():
                await interaction.response.edit_message(content=content, embed=embed, view=self)
            else:
                await interaction.followup.edit_message(message_id=interaction.message.id, content=content, embed=embed, view=self)

        except Exception as e:
            logger.error(f"Error updating embed: {e}")
            try:
                await interaction.followup.send("❌ An error occurred while updating the embed!", ephemeral=True)
            except (discord.HTTPException, discord.NotFound):
                pass

    @discord.ui.button(label="Mention Message", style=discord.ButtonStyle.secondary, row=1)
    async def edit_mention_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            modal = TextInputModal(
                title="Edit Mention Message",
                label="Mention Message",
                placeholder="Example: Hey @tag time! (@tag will be replaced with the actual mention)",
                default_value=self.embed_data.get("mention_message", ""),
                max_length=2000
            )
            await interaction.response.send_modal(modal)
            await modal.wait()

            if modal.value:
                self.embed_data["mention_message"] = modal.value
                await self.update_embed(interaction)

        except Exception as e:
            logger.error(f"Error in edit_mention_message: {e}")
            await interaction.followup.send("❌ An error occurred while editing the mention message!", ephemeral=True)

    @discord.ui.button(label="Title", style=discord.ButtonStyle.primary, row=0)
    async def edit_title(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            modal = TextInputModal(
                title="Edit Title",
                label="New Title",
                placeholder="Example: ⏰ Bear starts in {time}!",
                default_value=self.embed_data.get("title", ""),
                max_length=256
            )
            await interaction.response.send_modal(modal)
            await modal.wait()

            if modal.value:
                self.embed_data["title"] = modal.value
                await self.update_embed(interaction)

        except Exception as e:
            logger.error(f"Error in edit_title: {e}")
            await interaction.followup.send("❌ An error occurred while editing the title!", ephemeral=True)

    @discord.ui.button(label="Description", style=discord.ButtonStyle.primary, row=0)
    async def edit_description(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            modal = TextInputModal(
                title="Edit Description",
                label="New Description",
                placeholder="Example: Get ready for Bear! Only {time} remaining.",
                default_value=self.embed_data.get("description", ""),
                max_length=4000,
                style=discord.TextStyle.paragraph
            )
            await interaction.response.send_modal(modal)
            await modal.wait()

            if modal.value:
                self.embed_data["description"] = modal.value
                await self.update_embed(interaction)

        except Exception as e:
            logger.error(f"Error in edit_description: {e}")
            await interaction.followup.send("❌ An error occurred while editing the description!", ephemeral=True)

    @discord.ui.button(label="Color", style=discord.ButtonStyle.success, row=0)
    async def edit_color(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            current_color = self.embed_data.get('color', discord.Color.blue().value)
            current_hex = f"#{hex(current_color)[2:].zfill(6)}"

            modal = TextInputModal(
                title="Color Code",
                label="Hex Color Code",
                placeholder="#FF0000",
                default_value=current_hex,
                max_length=7
            )
            await interaction.response.send_modal(modal)
            await modal.wait()

            if modal.value:
                try:
                    hex_value = modal.value.strip('#')
                    color_value = int(hex_value, 16)
                    self.embed_data["color"] = color_value
                    await self.update_embed(interaction)
                except ValueError:
                    await interaction.followup.send("❌ Invalid color code! Example: #FF0000", ephemeral=True)

        except Exception as e:
            logger.error(f"Error in edit_color: {e}")
            await interaction.followup.send("❌ An error occurred while editing the color!", ephemeral=True)

    @discord.ui.button(label="Footer", style=discord.ButtonStyle.secondary, row=1)
    async def edit_footer(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            modal = TextInputModal(
                title="Edit Footer",
                label="Footer Text",
                placeholder="Example: Bear Trap Notification System",
                default_value=self.embed_data.get("footer", ""),
                max_length=2048
            )
            await interaction.response.send_modal(modal)
            await modal.wait()

            if modal.value:
                self.embed_data["footer"] = modal.value
                await self.update_embed(interaction)

        except Exception as e:
            logger.error(f"Error in edit_footer: {e}")
            await interaction.followup.send("❌ An error occurred while editing the footer!", ephemeral=True)

    @discord.ui.button(label="Author", style=discord.ButtonStyle.secondary, row=1)
    async def edit_author(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            modal = TextInputModal(
                title="Edit Author",
                label="Author Text",
                placeholder="Example: Bear Trap",
                default_value=self.embed_data.get("author", ""),
                max_length=256
            )
            await interaction.response.send_modal(modal)
            await modal.wait()

            if modal.value:
                self.embed_data["author"] = modal.value
                await self.update_embed(interaction)

        except Exception as e:
            logger.error(f"Error in edit_author: {e}")
            await interaction.followup.send("❌ An error occurred while editing the author!", ephemeral=True)

    @discord.ui.button(label="Add Image", style=discord.ButtonStyle.secondary, row=2)
    async def add_image(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            modal = TextInputModal(
                title="Image URL",
                label="Image URL",
                placeholder="https://example.com/image.png",
                default_value=self.embed_data.get("image_url", ""),
                max_length=1000
            )
            await interaction.response.send_modal(modal)
            await modal.wait()

            if modal.value:
                if not modal.value.startswith(('http://', 'https://')):
                    await interaction.followup.send("❌ Invalid URL! URL must start with 'http://' or 'https://'.", ephemeral=True)
                    return

                self.embed_data["image_url"] = modal.value
                await self.update_embed(interaction)

        except Exception as e:
            logger.error(f"Error in add_image: {e}")
            await interaction.followup.send("❌ An error occurred while adding the image!", ephemeral=True)

    @discord.ui.button(label="Add Thumbnail", style=discord.ButtonStyle.secondary, row=2)
    async def add_thumbnail(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            modal = TextInputModal(
                title="Thumbnail URL",
                label="Thumbnail URL",
                placeholder="https://example.com/thumbnail.png",
                default_value=self.embed_data.get("thumbnail_url", ""),
                max_length=1000
            )
            await interaction.response.send_modal(modal)
            await modal.wait()

            if modal.value:
                if not modal.value.startswith(('http://', 'https://')):
                    await interaction.followup.send("❌ Invalid URL! URL must start with 'http://' or 'https://'.", ephemeral=True)
                    return

                self.embed_data["thumbnail_url"] = modal.value
                await self.update_embed(interaction)

        except Exception as e:
            logger.error(f"Error in add_thumbnail: {e}")
            await interaction.followup.send("❌ An error occurred while adding the thumbnail!", ephemeral=True)

    @discord.ui.button(label="Confirm ✅", style=discord.ButtonStyle.green, row=3)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            self.cog._pending_embed_data[interaction.user.id] = self.embed_data

            embed_data = "EMBED_MESSAGE:true"

            await self.cog.show_channel_selection(
                interaction,
                self.start_date,
                self.hour,
                self.minute,
                self.timezone,
                embed_data,
                interaction.guild.text_channels
            )

        except Exception as e:
            logger.error(f"Error in confirm button: {e}")
            try:
                await interaction.followup.send(
                    "❌ An error occurred while confirming the embed! Please try again.",
                    ephemeral=True
                )
            except (discord.HTTPException, discord.NotFound):
                pass

    @discord.ui.button(label="Import Embed", style=discord.ButtonStyle.secondary, emoji="📥", row=2)
    async def import_embed(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            from .bear_trap_views import ImportEmbedModal
            modal = ImportEmbedModal(self)
            await interaction.response.send_modal(modal)
        except Exception as e:
            logger.error(f"Error showing import modal: {e}")
            await interaction.followup.send(
                "❌ An error occurred while importing the embed.",
                ephemeral=True
            )


class MessageTypeView(discord.ui.View):
    def __init__(self, cog: BearTrap, start_date, hour, minute, timezone):
        super().__init__(timeout=300)
        self.cog = cog
        self.start_date = start_date
        self.hour = hour
        self.minute = minute
        self.timezone = timezone
        self.original_message = None

    @discord.ui.button(label="Embed Message", style=discord.ButtonStyle.primary, emoji="📝", row=0)
    async def embed_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            example_time = "30 minutes"

            embed = discord.Embed(
                title="Bear Trap Notification",
                description="Get ready for Bear! Only %t remaining.",
                color=discord.Color.blue()
            )
            embed.set_footer(text="Bear Trap Notification System")

            content = (
                "📝 **Embed Editor**\n\n"
                "**Note:** \n"
                "• Use `%t` or `{time}` to show the remaining time\n"
                "• Use `@tag` for mentions (will be replaced with the actual mention)\n"
                "• You can use these in title, description, footer, and author fields\n"
                "• Time will automatically show with appropriate units (minutes/hours/days)\n\n"
                f"Currently showing '{example_time}' as an example."
            )

            view = EmbedEditorView(
                self.cog,
                self.start_date,
                self.hour,
                self.minute,
                self.timezone,
                interaction.message
            )
            view.embed_data = {
                "title": embed.title,
                "description": embed.description,
                "color": embed.color.value,
                "footer": "Bear Trap Notification System"
            }

            await interaction.response.edit_message(
                content=content,
                embed=embed,
                view=view
            )

        except Exception as e:
            logger.error(f"Error in embed_message: {e}")
            await interaction.followup.send(
                "❌ An error occurred while starting the embed editor!",
                ephemeral=True
            )

    @discord.ui.button(label="Plain Message", style=discord.ButtonStyle.secondary, emoji="✍️", row=0)
    async def plain_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = discord.ui.Modal(title="Message Content")
        message_content = discord.ui.TextInput(
            label="Message",
            placeholder="Enter notification message... You can use @tag for mentions and %t or {time} for time",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000
        )
        modal.add_item(message_content)

        async def modal_submit(modal_interaction):
            channels = interaction.guild.text_channels
            await self.cog.show_channel_selection(
                modal_interaction,
                self.start_date,
                self.hour,
                self.minute,
                self.timezone,
                f"PLAIN_MESSAGE:{message_content.value}",
                channels
            )

        modal.on_submit = modal_submit
        await interaction.response.send_modal(modal)


class TimeSelectModal(discord.ui.Modal):
    def __init__(self, cog: BearTrap):
        super().__init__(title="Set Notification Time")
        self.cog = cog

        current_utc = datetime.now(pytz.UTC)

        self.start_date = discord.ui.TextInput(
            label="Start Date (DD/MM/YYYY)",
            placeholder="Enter start date (e.g., 25/03/2024)",
            min_length=8,
            max_length=10,
            required=True,
            default=current_utc.strftime("%d/%m/%Y")
        )

        self.hour = discord.ui.TextInput(
            label="Hour (0-23)",
            placeholder="Enter hour (e.g., 14)",
            min_length=1,
            max_length=2,
            required=True,
            default=current_utc.strftime("%H")
        )

        self.minute = discord.ui.TextInput(
            label="Minute (0-59)",
            placeholder="Enter minute (e.g., 30)",
            min_length=1,
            max_length=2,
            required=True,
            default=current_utc.strftime("%M")
        )

        self.timezone = discord.ui.TextInput(
            label="Timezone",
            placeholder="Enter timezone (e.g., UTC, Europe/Istanbul)",
            min_length=1,
            max_length=50,
            required=True,
            default="UTC"
        )

        for item in [self.start_date, self.hour, self.minute, self.timezone]:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            try:
                timezone = pytz.timezone(self.timezone.value)
            except pytz.exceptions.UnknownTimeZoneError:
                await interaction.response.send_message(
                    "❌ Invalid timezone! Please use a valid timezone (e.g., UTC, Europe/Istanbul).",
                    ephemeral=True
                )
                return

            try:
                start_date = datetime.strptime(self.start_date.value, "%d/%m/%Y")
                now = datetime.now(timezone)
                start_date = timezone.localize(start_date)

                if start_date.date() < now.date():
                    await interaction.response.send_message(
                        "❌ Start date cannot be in the past for the selected timezone!",
                        ephemeral=True
                    )
                    return
            except ValueError:
                await interaction.response.send_message(
                    "❌ Invalid date format! Please use DD/MM/YYYY format.",
                    ephemeral=True
                )
                return

            hour = int(self.hour.value)
            minute = int(self.minute.value)

            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("Invalid time format")

            view = MessageTypeView(
                self.cog,
                start_date,
                hour,
                minute,
                self.timezone.value
            )

            embed = discord.Embed(
                title="📝 Select Message Type",
                description=(
                    "How should your notification message look?\n\n"
                    "**📝 Embed Message**\n"
                    "• Customizable title\n"
                    "• Rich text format\n"
                    "• Custom color selection\n"
                    "• Footer and author\n\n"
                    "**✍️ Plain Message**\n"
                    "• Simple text format\n"
                    "• Quick creation"
                ),
                color=discord.Color.blue()
            )

            await interaction.response.send_message(
                embed=embed,
                view=view,
                ephemeral=True
            )

        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid time format! Please use numbers for hour (0-23) and minute (0-59).",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in time modal: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while setting the time.",
                ephemeral=True
            )


class NotificationTypeView(discord.ui.View):
    def __init__(self, cog: BearTrap, start_date, hour, minute, timezone, message_data, channel_id, original_message):
        super().__init__(timeout=300)
        self.cog = cog
        self.start_date = start_date
        self.hour = hour
        self.minute = minute
        self.timezone = timezone
        self.message_data = message_data
        self.channel_id = channel_id
        self.original_message = original_message

    @discord.ui.button(label="30m, 10m, 5m & Time", style=discord.ButtonStyle.primary, custom_id="type_1", row=0)
    async def type_1(self, interaction, button):
        await self.show_mention_type_menu(interaction, 1)

    @discord.ui.button(label="10m, 5m & Time", style=discord.ButtonStyle.primary, custom_id="type_2", row=0)
    async def type_2(self, interaction, button):
        await self.show_mention_type_menu(interaction, 2)

    @discord.ui.button(label="5m & Time", style=discord.ButtonStyle.primary, custom_id="type_3", row=1)
    async def type_3(self, interaction, button):
        await self.show_mention_type_menu(interaction, 3)

    @discord.ui.button(label="Only 5m", style=discord.ButtonStyle.primary, custom_id="type_4", row=1)
    async def type_4(self, interaction, button):
        await self.show_mention_type_menu(interaction, 4)

    @discord.ui.button(label="Only Time", style=discord.ButtonStyle.primary, custom_id="type_5", row=1)
    async def type_5(self, interaction, button):
        await self.show_mention_type_menu(interaction, 5)

    @discord.ui.button(label="Custom Times", style=discord.ButtonStyle.success, custom_id="type_6", row=2)
    async def type_6(self, interaction, button):
        modal = CustomTimesModal(self.cog, self.start_date, self.hour, self.minute, self.timezone, self.message_data, self.channel_id, self.original_message)
        await interaction.response.send_modal(modal)

    async def show_mention_type_menu(self, interaction, notification_type):
        try:
            embed = discord.Embed(
                title="📢 Select Mention Type",
                description=(
                    "Choose how to mention users:\n\n"
                    "1️⃣ @everyone\n"
                    "2️⃣ Specific Role\n"
                    "3️⃣ Specific Member\n"
                    "4️⃣ No Mention"
                ),
                color=discord.Color.blue()
            )

            view = MentionTypeView(
                self.cog,
                self.start_date,
                self.hour,
                self.minute,
                self.timezone,
                self.message_data,
                self.channel_id,
                notification_type,
                self.original_message
            )

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=view
            )
        except Exception as e:
            logger.error(f"Error in show_mention_type_menu: {e}")
            await interaction.followup.send(
                "❌ An error occurred while showing mention options!",
                ephemeral=True
            )


class CustomTimesModal(discord.ui.Modal):
    def __init__(self, cog: BearTrap, start_date, hour, minute, timezone, message_data, channel_id, original_message):
        super().__init__(title="Set Custom Notification Times")
        self.cog = cog
        self.start_date = start_date
        self.hour = hour
        self.minute = minute
        self.timezone = timezone
        self.message_data = message_data
        self.channel_id = channel_id
        self.original_message = original_message

        self.custom_times = discord.ui.TextInput(
            label="Custom Notification Times",
            placeholder="Enter times in minutes (e.g., 60-20-15-4-2 or 60-20-15-4-2-0)",
            min_length=1,
            max_length=50,
            required=True,
            style=discord.TextStyle.short
        )
        self.add_item(self.custom_times)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            times_str = self.custom_times.value.strip()
            times = [int(t) for t in times_str.split('-')]

            if not all(isinstance(t, int) and t >= 0 for t in times):
                raise ValueError("All times must be non-negative integers")

            if not times:
                raise ValueError("At least one time must be specified")

            if not all(times[i] > times[i+1] for i in range(len(times)-1)):
                raise ValueError("Times must be in descending order")

            embed = discord.Embed(
                title="📢 Select Mention Type",
                description=(
                    "Choose how to mention users:\n\n"
                    "1️⃣ @everyone\n"
                    "2️⃣ Specific Role\n"
                    "3️⃣ Specific Member\n"
                    "4️⃣ No Mention"
                ),
                color=discord.Color.blue()
            )

            view = MentionTypeView(
                self.cog,
                self.start_date,
                self.hour,
                self.minute,
                self.timezone,
                f"CUSTOM_TIMES:{'-'.join(map(str, times))}|{self.message_data}",
                self.channel_id,
                6,
                self.original_message
            )

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=view
            )

        except ValueError as e:
            await interaction.response.send_message(
                f"❌ Invalid input: {str(e)}",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in custom times modal: {e}")
            await interaction.followup.send(
                "❌ An error occurred while processing custom times.",
                ephemeral=True
            )


class MentionTypeView(discord.ui.View):
    def __init__(self, cog: BearTrap, start_date, hour, minute, timezone, message_data, channel_id, notification_type, original_message):
        super().__init__(timeout=300)
        self.cog = cog
        self.start_date = start_date
        self.hour = hour
        self.minute = minute
        self.timezone = timezone
        self.message_data = message_data
        self.channel_id = channel_id
        self.notification_type = notification_type
        self.original_message = original_message

    async def show_mention_type_menu(self, interaction, mention_type):
        try:
            embed = discord.Embed(
                title="🔄 Repeat Settings",
                description=(
                    "**Configure Notification Repeat**\n\n"
                    "Choose how often you want this notification to repeat:\n\n"
                    "• No Repeat: Notification will be sent only once\n"
                    "• Custom Interval: Set a custom repeat interval (minutes/hours/days/weeks/months)"
                ),
                color=discord.Color.blue()
            )

            view = RepeatOptionView(
                self.cog,
                self.start_date,
                self.hour,
                self.minute,
                self.timezone,
                self.message_data,
                self.channel_id,
                self.notification_type,
                mention_type,
                self.original_message
            )

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=view
            )
        except Exception as e:
            logger.error(f"Error in show_mention_type_menu: {e}")
            await interaction.followup.send(
                "❌ An error occurred while showing mention options!",
                ephemeral=True
            )

    @discord.ui.button(label="@everyone", style=discord.ButtonStyle.danger, emoji="📢", row=0)
    async def everyone_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.show_mention_type_menu(interaction, "everyone")
        except Exception as e:
            logger.error(f"Error in everyone button: {e}")
            await interaction.followup.send(
                "❌ An error occurred while setting @everyone mention!",
                ephemeral=True
            )

    @discord.ui.button(label="Select Member", style=discord.ButtonStyle.primary, emoji="👤", row=0)
    async def member_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            select = discord.ui.UserSelect(
                placeholder="Select a member to mention",
                min_values=1,
                max_values=1
            )

            async def user_select_callback(select_interaction):
                try:
                    selected_user_id = select_interaction.data["values"][0]
                    await self.show_mention_type_menu(select_interaction, f"member_{selected_user_id}")
                except Exception as e:
                    logger.error(f"Error in user selection: {e}")
                    await select_interaction.followup.send(
                        "❌ An error occurred while selecting the member!",
                        ephemeral=True
                    )

            select.callback = user_select_callback
            view = discord.ui.View(timeout=300)
            view.add_item(select)

            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="👤 Select Member",
                    description="Choose a member to mention:",
                    color=discord.Color.blue()
                ),
                view=view
            )
        except Exception as e:
            logger.error(f"Error in member button: {e}")
            await interaction.followup.send(
                "❌ An error occurred while showing member selection!",
                ephemeral=True
            )

    @discord.ui.button(label="Select Role", style=discord.ButtonStyle.success, emoji="👥", row=0)
    async def role_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            select = discord.ui.RoleSelect(
                placeholder="Select a role to mention",
                min_values=1,
                max_values=1
            )

            async def role_select_callback(select_interaction):
                try:
                    selected_role_id = select_interaction.data["values"][0]
                    await self.show_mention_type_menu(select_interaction, f"role_{selected_role_id}")
                except Exception as e:
                    logger.error(f"Error in role selection: {e}")
                    await select_interaction.followup.send(
                        "❌ An error occurred while selecting the role!",
                        ephemeral=True
                    )

            select.callback = role_select_callback
            view = discord.ui.View(timeout=300)
            view.add_item(select)

            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="👥 Select Role",
                    description="Choose a role to mention:",
                    color=discord.Color.blue()
                ),
                view=view
            )
        except Exception as e:
            logger.error(f"Error in role button: {e}")
            await interaction.followup.send(
                "❌ An error occurred while showing role selection!",
                ephemeral=True
            )

    @discord.ui.button(label="No Mention", style=discord.ButtonStyle.secondary, emoji="🔕", row=0)
    async def no_mention_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.show_mention_type_menu(interaction, "none")
        except Exception as e:
            logger.error(f"Error in no mention button: {e}")
            await interaction.followup.send(
                "❌ An error occurred while setting no mention!",
                ephemeral=True
            )


class MentionSelectMenu(discord.ui.Select):
    MAX_OPTIONS = 25

    def __init__(self, view):
        self.parent_view = view

        options = []

        options.append(
            discord.SelectOption(
                label="@everyone",
                value="everyone",
                description="Mention everyone in the server",
                emoji="📢"
            )
        )

        options.append(
            discord.SelectOption(
                label="No Mention",
                value="none",
                description="Don't mention anyone",
                emoji="🔕"
            )
        )

        remaining_slots = self.MAX_OPTIONS - len(options)

        guild = view.original_message.guild
        roles = sorted(
            [role for role in guild.roles if not role.is_default() and not role.managed],
            key=lambda r: r.position,
            reverse=True
        )

        roles_truncated = False
        for role in roles:
            if remaining_slots <= 0:
                roles_truncated = True
                break
            options.append(
                discord.SelectOption(
                    label=role.name,
                    value=f"role_{role.id}",
                    description=f"Role with {len(role.members)} members",
                    emoji="👥"
                )
            )
            remaining_slots -= 1

        members = sorted(
            [member for member in guild.members if not member.bot],
            key=lambda m: m.display_name.lower()
        )

        members_truncated = False
        for member in members:
            if remaining_slots <= 0:
                members_truncated = True
                break
            options.append(
                discord.SelectOption(
                    label=member.display_name,
                    value=f"member_{member.id}",
                    description=f"@{member.name}",
                    emoji="👤"
                )
            )
            remaining_slots -= 1

        if roles_truncated or members_truncated:
            logger.warning(
                "MentionSelectMenu truncated: roles_truncated=%s, members_truncated=%s (guild=%s)",
                roles_truncated, members_truncated, guild.id
            )

        super().__init__(
            placeholder="🔍 Search and select who to mention...",
            min_values=1,
            max_values=1,
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            selected_value = self.values[0]

            await self.parent_view.show_mention_type_menu(interaction, selected_value)

        except Exception as e:
            logger.error(f"Error in mention selection: {e}")
            await interaction.followup.send(
                "❌ An error occurred while processing your selection!",
                ephemeral=True
            )
