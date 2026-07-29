from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING

import discord

from .config import WOS_TEST_PLAYER_ID
from .database import DatabaseManager
from .log_config import get_logger
from .utils import AllianceSelectView, _create_monitored_task, build_embed

if TYPE_CHECKING:
    from .gift_distribution import GiftDistributor
    from .gift_operations import GiftOperations

logger = get_logger("gift_operations")


class RetryFailedView(discord.ui.View):
    def __init__(self, distributor_or_cog, failed_users, giftcode, alliance_name, channel):
        super().__init__(timeout=3600)  # 1 hour
        from .gift_distribution import GiftDistributor
        if isinstance(distributor_or_cog, GiftDistributor):
            self.claimer = distributor_or_cog.claimer
        else:
            self.claimer = distributor_or_cog.claimer
        self.failed_users = failed_users
        self.giftcode = giftcode
        self.alliance_name = alliance_name
        self.channel = channel

    @discord.ui.button(label="Retry Failed", style=discord.ButtonStyle.danger, emoji="\U0001f504")
    async def retry_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            button.disabled = True
            await interaction.response.edit_message(view=self)

            conn = DatabaseManager.instance().get("giftcode")
            retry_success = []
            retry_still_failed = []

            for player_id, nickname in self.failed_users:
                try:
                    status = await self.claimer.claim_giftcode_rewards_wos(player_id, self.giftcode)
                except Exception as e:
                    logger.error("Retry claim error for %s (%s): %s", nickname, player_id, e)
                    status = f"ERROR_{type(e).__name__}"

                if status in ["SUCCESS", "RECEIVED", "SAME TYPE EXCHANGE"]:
                    retry_success.append(nickname)
                    try:
                        conn.execute("""
                            INSERT OR REPLACE INTO user_giftcodes (fid, giftcode, status)
                            VALUES (?, ?, ?)
                        """, (player_id, self.giftcode, status))
                        conn.commit()
                    except Exception as e:
                        logger.error("Retry DB error for %s: %s", player_id, e)
                else:
                    retry_still_failed.append((player_id, nickname, status))
                await asyncio.sleep(2)

            failed_list = ""
            if retry_still_failed:
                failed_list = "\n\U0001f464 **Still Failed:**\n" + "\n".join(
                    f"  - `{name}` ({reason})" for _, name, reason in retry_still_failed
                ) + "\n"

            success_list = ""
            if retry_success:
                success_list = "\n\u2705 **Retry Success:**\n" + "\n".join(f"  - `{name}`" for name in retry_success) + "\n"

            embed = build_embed("\U0001f504 Retry Results", {
                "\U0001f3f0 Alliance": self.alliance_name,
                "\U0001f381 Gift Code": self.giftcode,
                "\u2705 Retry Success": str(len(retry_success)),
                "\u274c Still Failed": str(len(retry_still_failed)),
            }, header="Retry for Failed Users", color=discord.Color.green() if not retry_still_failed else discord.Color.orange())
            if success_list or failed_list:
                lines = embed.description.rsplit("\n", 2)
                embed.description = lines[0] + "\n" + success_list + failed_list + "\n" + lines[-1]

            if retry_still_failed:
                self.failed_users = [(pid, name) for pid, name, _ in retry_still_failed]
                button.disabled = False
                await interaction.message.edit(embed=embed, view=self)
            else:
                await interaction.message.edit(embed=embed, view=None)

        except Exception as e:
            logger.exception("Error in RetryFailedView retry_button: %s", e)
            try:
                button.disabled = False
                await interaction.message.edit(
                    content=f"\u274c Retry failed: {e}",
                    view=self
                )
            except Exception:
                pass


class CreateGiftCodeModal(discord.ui.Modal):
    def __init__(self, cog: GiftOperations):
        super().__init__(title="Create Gift Code")
        self.cog = cog

        self.giftcode = discord.ui.TextInput(
            label="Gift Code",
            placeholder="Enter the gift code",
            required=True,
            min_length=4,
            max_length=20
        )
        self.add_item(self.giftcode)

    async def on_submit(self, interaction: discord.Interaction):
        code = self.giftcode.value

        await interaction.response.defer(ephemeral=True)

        try:
            status = await self.cog.claimer.claim_giftcode_rewards_wos(WOS_TEST_PLAYER_ID, code)

            if status in ["SUCCESS", "RECEIVED", "SAME TYPE EXCHANGE"]:
                cursor = self.cog.conn.execute("SELECT 1 FROM gift_codes WHERE giftcode = ?", (code,))
                if not cursor.fetchone():
                    date = datetime.now().strftime("%Y-%m-%d")

                    self.cog.conn.execute(
                        "INSERT INTO gift_codes (giftcode, date) VALUES (?, ?)",
                        (code, date)
                    )
                    self.cog.conn.commit()

                    _create_monitored_task(self.cog.api.add_giftcode(code), name="api_add_giftcode")

                    embed = build_embed("✅ Gift Code Created", {
                        "🎁 Gift Code": code,
                        "✅ Status": "Successfully created",
                    }, header="Gift Code Details")

                    await interaction.followup.send(embed=embed, ephemeral=True)

                else:
                    embed = build_embed("❌ Gift Code Error", {
                        "🎁 Gift Code": code,
                        "❌ Status": "Already exists in database",
                    }, header="Gift Code Details")
                    await interaction.followup.send(embed=embed, ephemeral=True)

            elif status == "TIME_ERROR":
                embed = build_embed("❌ Gift Code Error", {
                    "🎁 Gift Code": code,
                    "❌ Status": "Gift code has expired",
                }, header="Gift Code Details")
                await interaction.followup.send(embed=embed, ephemeral=True)

            elif status == "CDK_NOT_FOUND":
                embed = build_embed("❌ Gift Code Error", {
                    "🎁 Gift Code": code,
                    "❌ Status": "Invalid gift code",
                }, header="Gift Code Details")
                await interaction.followup.send(embed=embed, ephemeral=True)

            elif status == "USAGE_LIMIT":
                embed = build_embed("❌ Gift Code Error", {
                    "🎁 Gift Code": code,
                    "❌ Status": "Usage limit has been reached",
                }, header="Gift Code Details")
                await interaction.followup.send(embed=embed, ephemeral=True)

        except sqlite3.IntegrityError:
            await interaction.followup.send(
                "❌ This gift code already exists!",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error creating gift code: {e}")
            await interaction.followup.send(
                "❌ An error occurred while creating the gift code.",
                ephemeral=True
            )

class DeleteGiftCodeModal(discord.ui.Modal, title="Delete Gift Code"):
    def __init__(self, cog: GiftOperations):
        super().__init__()
        self.cog = cog

    giftcode = discord.ui.TextInput(
        label="Gift Code",
        placeholder="Enter the gift code to delete",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        code = self.giftcode.value

        cursor = self.cog.conn.execute("SELECT 1 FROM gift_codes WHERE giftcode = ?", (code,))
        if not cursor.fetchone():
            await interaction.followup.send(
                "❌ Gift code not found!",
                ephemeral=True
            )
            return

        self.cog.conn.execute("DELETE FROM gift_codes WHERE giftcode = ?", (code,))
        self.cog.conn.execute("DELETE FROM user_giftcodes WHERE giftcode = ?", (code,))
        self.cog.conn.commit()

        embed = discord.Embed(
            title="✅ Gift Code Deleted",
            description=f"Gift code `{code}` has been deleted successfully.",
            color=discord.Color.green()
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

class GiftView(discord.ui.View):
    def __init__(self, cog: GiftOperations):
        super().__init__(timeout=300)
        self.cog = cog

    async def on_timeout(self):
        if hasattr(self, 'message') and self.message:
            for item in self.children:
                item.disabled = True
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    @discord.ui.button(
        label="Create Gift Code",
        style=discord.ButtonStyle.success,
        custom_id="create_gift",
        emoji="➕",
        row=0
    )
    async def create_gift(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.ui.create_gift_code(interaction)

    @discord.ui.button(
        label="List Gift Codes",
        style=discord.ButtonStyle.primary,
        custom_id="list_gift",
        emoji="📋",
        row=0
    )
    async def list_gift(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.ui.list_gift_codes(interaction)

    @discord.ui.button(
        label="Auto Gift Settings",
        style=discord.ButtonStyle.secondary,
        custom_id="auto_gift_settings",
        emoji="⚙️",
        row=1
    )
    async def auto_gift_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.ui.setup_giftcode_auto(interaction)

    @discord.ui.button(
        label="Delete Gift Code",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="delete_gift"
    )
    async def delete_gift_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.cog.ui.delete_gift_code(interaction)
        except Exception as e:
            logger.error(f"Delete gift button error: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while processing delete request.",
                ephemeral=True
            )

    @discord.ui.button(
        label="Gift Code Channel",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id="gift_channel"
    )
    async def gift_channel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.cog.ui.setup_gift_channel(interaction)
        except Exception as e:
            logger.error(f"Gift channel button error: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while setting up gift channel.",
                    ephemeral=True
                )

    @discord.ui.button(
        label="Delete Gift Channel",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="delete_gift_channel"
    )
    async def delete_gift_channel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.cog.ui.delete_gift_channel(interaction)
        except Exception as e:
            logger.error(f"Delete gift channel button error: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while deleting gift channel.",
                    ephemeral=True
                )

    @discord.ui.button(
        label="Use Gift Code for Alliance",
        emoji="▶️",
        style=discord.ButtonStyle.success,
        custom_id="use_gift_alliance"
    )
    async def use_gift_alliance_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            admin_info = await self.cog.ui.get_admin_info(interaction.user.id)
            if not admin_info:
                await interaction.response.send_message(
                    "❌ You are not authorized to perform this action.",
                    ephemeral=True
                )
                return

            available_alliances = await self.cog.ui.get_available_alliances(interaction)
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

            alliance_embed = discord.Embed(
                title="🎯 Use Gift Code for Alliance",
                description=(
                    "Select an alliance to use gift code:\n\n"
                    "**Alliance List**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "Select an alliance from the list below:\n"
                ),
                color=discord.Color.blue()
            )

            view = AllianceSelectView(alliances_with_counts, self.cog)

            view.current_select.options.insert(0, discord.SelectOption(
                label="ALL ALLIANCES",
                value="all",
                description=f"Apply to all {len(alliances_with_counts)} alliances",
                emoji="🌐"
            ))

            async def alliance_callback(select_interaction: discord.Interaction):
                try:
                    selected_value = view.current_select.values[0]

                    if selected_value == "all":
                        all_alliances = [aid for aid, name, _ in alliances_with_counts]
                    else:
                        alliance_id = int(selected_value)
                        all_alliances = [alliance_id]

                    cursor = self.cog.conn.execute("""
                        SELECT giftcode, date FROM gift_codes
                        ORDER BY date DESC
                    """)
                    gift_codes = cursor.fetchall()

                    if not gift_codes:
                        await select_interaction.response.edit_message(
                            content="No gift codes available.",
                            view=None
                        )
                        return

                    # Discord select menus support max 25 options; show most recent codes
                    gift_codes = gift_codes[:25]

                    giftcode_embed = discord.Embed(
                        title="🎁 Select Gift Code",
                        description=(
                            "Select a gift code to use:\n\n"
                            "**Gift Code List**\n"
                            "━━━━━━━━━━━━━━━━━━━━━━\n"
                            "Select a gift code from the list below:\n"
                        ),
                        color=discord.Color.blue()
                    )

                    select_giftcode = discord.ui.Select(
                        placeholder="Select a gift code",
                        options=[
                            discord.SelectOption(
                                label=f"Code: {code}",
                                value=code,
                                description=f"Created: {date}",
                                emoji="🎁"
                            ) for code, date in gift_codes
                        ]
                    )

                    async def giftcode_callback(giftcode_interaction: discord.Interaction):
                        try:
                            selected_code = giftcode_interaction.data["values"][0]

                            confirm_embed = build_embed("⚠️ Confirm Gift Code Usage", {
                                "🎁 Gift Code": selected_code,
                                "🏰 Alliances": 'ALL' if selected_value == 'all' else next((name for aid, name, _ in alliances_with_counts if aid == alliance_id), 'Unknown'),
                            }, header="Details", color=discord.Color.yellow())

                            confirm_view = discord.ui.View()

                            async def confirm_callback(button_interaction: discord.Interaction):
                                try:
                                    progress_embed = build_embed("🎁 Gift Code Distribution Progress", {
                                        "🎁 Gift Code": selected_code,
                                        "🏰 Total Alliances": str(len(all_alliances)),
                                        "⏳ Current Alliance": "Starting...",
                                    }, header="Overall Progress", color=discord.Color.blue())

                                    await button_interaction.response.edit_message(
                                        content=None,
                                        embed=progress_embed,
                                        view=None
                                    )

                                    completed = 0
                                    for aid in all_alliances:
                                        alliance_name = next((name for a_id, name, _ in alliances_with_counts if a_id == aid), 'Unknown')

                                        progress_embed = build_embed("🎁 Gift Code Distribution Progress", {
                                            "🎁 Gift Code": selected_code,
                                            "🏰 Total Alliances": str(len(all_alliances)),
                                            "⏳ Current Alliance": alliance_name,
                                            "📊 Progress": f"{completed}/{len(all_alliances)}",
                                        }, header="Overall Progress", color=discord.Color.blue())
                                        await button_interaction.edit_original_response(embed=progress_embed)

                                        result = await self.cog.distributor.use_giftcode_for_alliance(aid, selected_code)
                                        if result:
                                            completed += 1

                                        await asyncio.sleep(5)

                                    final_embed = build_embed("✅ Gift Code Distribution Complete", {
                                        "🎁 Gift Code": selected_code,
                                        "🏰 Total Alliances": str(len(all_alliances)),
                                        "✅ Completed": f"{completed}/{len(all_alliances)}",
                                        "⏰ Time": f"<t:{int(datetime.now().timestamp())}:R>",
                                    }, header="Final Status")

                                    await button_interaction.edit_original_response(embed=final_embed)

                                except Exception as e:
                                    logger.error(f"Error using gift code: {e}")
                                    await button_interaction.followup.send(
                                        "❌ An error occurred while using the gift code.",
                                        ephemeral=True
                                    )

                            async def cancel_callback(button_interaction: discord.Interaction):
                                cancel_embed = discord.Embed(
                                    title="❌ Operation Cancelled",
                                    description="The gift code usage has been cancelled.",
                                    color=discord.Color.red()
                                )
                                await button_interaction.response.edit_message(
                                    embed=cancel_embed,
                                    view=None
                                )

                            confirm_button = discord.ui.Button(
                                label="Confirm",
                                emoji="✅",
                                style=discord.ButtonStyle.success,
                                custom_id="confirm"
                            )
                            confirm_button.callback = confirm_callback

                            cancel_button = discord.ui.Button(
                                label="Cancel",
                                emoji="❌",
                                style=discord.ButtonStyle.danger,
                                custom_id="cancel"
                            )
                            cancel_button.callback = cancel_callback

                            confirm_view.add_item(confirm_button)
                            confirm_view.add_item(cancel_button)

                            await giftcode_interaction.response.edit_message(
                                embed=confirm_embed,
                                view=confirm_view
                            )

                        except Exception as e:
                            logger.error(f"Error in gift code selection: {e}")
                            if not giftcode_interaction.response.is_done():
                                await giftcode_interaction.response.send_message(
                                    "❌ An error occurred while processing your selection.",
                                    ephemeral=True
                                )
                            else:
                                await giftcode_interaction.followup.send(
                                    "❌ An error occurred while processing your selection.",
                                    ephemeral=True
                                )

                    select_giftcode.callback = giftcode_callback
                    giftcode_view = discord.ui.View()
                    giftcode_view.add_item(select_giftcode)

                    if not select_interaction.response.is_done():
                        await select_interaction.response.edit_message(
                            embed=giftcode_embed,
                            view=giftcode_view
                        )
                    else:
                        await select_interaction.message.edit(
                            embed=giftcode_embed,
                            view=giftcode_view
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

        except Exception as e:
            logger.error(f"Error in use_gift_alliance_button: {str(e)}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred while processing the request.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "❌ An error occurred while processing the request.",
                        ephemeral=True
                    )
            except Exception:
                pass

    @discord.ui.button(
        label="Main Menu",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="gift_main_menu"
    )
    async def main_menu_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            alliance_cog = self.cog.bot.get_cog("Alliance")
            if alliance_cog:
                try:
                    await interaction.message.edit(content=None, embed=None, view=None)
                except (discord.HTTPException, discord.NotFound):
                    pass
                await alliance_cog.show_main_menu(interaction)
            else:
                await interaction.response.send_message(
                    "❌ Alliance module not found.",
                    ephemeral=True
                )
        except Exception as e:
            logger.error(f"Error returning to main menu: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while returning to main menu.",
                    ephemeral=True
                )
