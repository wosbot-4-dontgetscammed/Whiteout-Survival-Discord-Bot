import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from typing import List
from datetime import datetime

from .config import LEVEL_MAPPING, FL_EMOJIS
from .database import DatabaseManager
from .utils import _create_monitored_task, PaginationView, build_embed, fix_rtl, AllianceSelectView, FIDSearchModal, check_admin as _utils_check_admin, get_admin_info as _utils_get_admin_info
from .log_config import get_logger
from .wos_api import fetch_player_info, resolve_kingdom

logger = get_logger("alliance_member_operations")

class AllianceMemberOperations(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        db = DatabaseManager.instance()
        self.conn_alliance = db.get("alliance")

        self.conn_users = db.get("users")
        
        self.level_mapping = LEVEL_MAPPING

        self.fl_emojis = FL_EMOJIS





    def get_fl_emoji(self, fl_level: int) -> str:
        for level_range, emoji in self.fl_emojis.items():
            if fl_level in level_range:
                return emoji
        return "🔥"

    async def handle_member_operations(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="👥 Alliance Member Operations",
            description=(
                "Please select an operation from below:\n\n"
                "**Available Operations:**\n"
                "➕ `Add Member` - Add new members to alliance\n"
                "➖ `Remove Member` - Remove members from alliance\n"
                "📋 `View Members` - View alliance member list\n"
                "🔄 `Transfer Member` - Transfer members to another alliance\n"
                "🏠 `Main Menu` - Return to main menu"
            ),
            color=discord.Color.blue()
        )
        
        embed.set_footer(text="Select an option to continue")

        class MemberOperationsView(discord.ui.View):
            def __init__(self, cog):
                super().__init__()
                self.cog = cog
                self.bot = cog.bot

            @discord.ui.button(
                label="Add Member",
                emoji="➕",
                style=discord.ButtonStyle.success,
                custom_id="add_member",
                row=0
            )
            async def add_member_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                try:
                    admin_info = _utils_get_admin_info(button_interaction.user.id)
                    is_admin = admin_info is not None
                    is_initial = admin_info[1] if admin_info and admin_info[1] is not None else 0

                    if not is_admin:
                        await button_interaction.response.send_message(
                            "❌ You don't have permission to use this command.", 
                            ephemeral=True
                        )
                        return

                    alliances, special_alliances, is_global = await self.cog.get_admin_alliances(
                        button_interaction.user.id, 
                        button_interaction.guild_id
                    )
                    
                    if not alliances:
                        await button_interaction.response.send_message(
                            "❌ No alliances found for your permissions.", 
                            ephemeral=True
                        )
                        return

                    special_alliance_text = ""
                    if special_alliances:
                        special_alliance_text = "\n\n**Special Access Alliances**\n"
                        special_alliance_text += "━━━━━━━━━━━━━━━━━━━━━━\n"
                        for _, name in special_alliances:
                            special_alliance_text += f"🔸 {name}\n"
                        special_alliance_text += "━━━━━━━━━━━━━━━━━━━━━━"

                    select_embed = discord.Embed(
                        title="📋 Alliance Selection",
                        description=(
                            "Please select an alliance to add members:\n\n"
                            "**Permission Details**\n"
                            "━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"👤 **Access Level:** `{'Global Admin' if is_initial == 1 else 'Server Admin'}`\n"
                            f"🔍 **Access Type:** `{'All Alliances' if is_initial == 1 else 'Server + Special Access'}`\n"
                            f"📊 **Available Alliances:** `{len(alliances)}`\n"
                            "━━━━━━━━━━━━━━━━━━━━━━"
                            f"{special_alliance_text}"
                        ),
                        color=discord.Color.green()
                    )

                    alliances_with_counts = []
                    for alliance_id, name in alliances:
                        users_db = DatabaseManager.instance().get("users")
                        cursor = users_db.cursor()
                        cursor.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                        row = cursor.fetchone()
                        member_count = row[0] if row else 0
                        alliances_with_counts.append((alliance_id, name, member_count))

                    view = AllianceSelectView(alliances_with_counts, self.cog)

                    async def select_callback(interaction: discord.Interaction):
                        alliance_id = int(view.current_select.values[0])
                        await interaction.response.send_modal(AddMemberModal(alliance_id))

                    view.callback = select_callback
                    await button_interaction.response.send_message(
                        embed=select_embed,
                        view=view,
                        ephemeral=True
                    )

                except Exception as e:
                    logger.info(f"Error in add_member_button: {e}")
                    await button_interaction.response.send_message(
                        "An error occurred while processing your request.",
                        ephemeral=True
                    )

            @discord.ui.button(
                label="Manage Regions",
                emoji="🌍",
                style=discord.ButtonStyle.secondary,
                custom_id="manage_regions",
                row=1
            )
            async def manage_regions_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                try:
                    if not _utils_get_admin_info(button_interaction.user.id):
                        await button_interaction.response.send_message(
                            "❌ You don't have permission to use this command.", ephemeral=True)
                        return
                    alliances, special_alliances, is_global = await self.cog.get_admin_alliances(
                        button_interaction.user.id, button_interaction.guild_id)
                    if not alliances:
                        await button_interaction.response.send_message(
                            "❌ No alliances found for your permissions.", ephemeral=True)
                        return
                    alliances_with_counts = []
                    for alliance_id, name in alliances:
                        users_db = DatabaseManager.instance().get("users")
                        cnt = users_db.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,)).fetchone()
                        alliances_with_counts.append((alliance_id, name, cnt[0] if cnt else 0))
                    view = AllianceSelectView(alliances_with_counts, self.cog)

                    async def select_callback(interaction: discord.Interaction):
                        alliance_id = int(view.current_select.values[0])
                        aname = next((n for aid, n in alliances if str(aid) == str(alliance_id)), str(alliance_id))
                        from .regions import RegionManageView
                        rview = RegionManageView(alliance_id, aname)
                        await interaction.response.edit_message(content=None, embed=rview.embed(), view=rview)

                    view.callback = select_callback
                    await button_interaction.response.send_message(
                        embed=discord.Embed(title="🌍 Select an alliance to manage its regions",
                                            color=discord.Color.blurple()),
                        view=view, ephemeral=True)
                except Exception as e:
                    logger.info(f"Error in manage_regions_button: {e}")
                    await button_interaction.response.send_message(
                        "An error occurred while processing your request.", ephemeral=True)

            @discord.ui.button(
                label="Remove Member",
                emoji="➖",
                style=discord.ButtonStyle.danger,
                custom_id="remove_member",
                row=0
            )
            async def remove_member_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                try:
                    admin_result = _utils_get_admin_info(button_interaction.user.id)

                    if not admin_result:
                        await button_interaction.response.send_message(
                            "❌ You are not authorized to use this command.",
                            ephemeral=True
                        )
                        return

                    is_initial = admin_result[1]

                    alliances, special_alliances, is_global = await self.cog.get_admin_alliances(
                        button_interaction.user.id, 
                        button_interaction.guild_id
                    )
                    
                    if not alliances:
                        await button_interaction.response.send_message(
                            "❌ Your authorized alliance was not found.", 
                            ephemeral=True
                        )
                        return

                    special_alliance_text = ""
                    if special_alliances:
                        special_alliance_text = "\n\n**Special Access Alliances**\n"
                        special_alliance_text += "━━━━━━━━━━━━━━━━━━━━━━\n"
                        for _, name in special_alliances:
                            special_alliance_text += f"🔸 {name}\n"
                        special_alliance_text += "━━━━━━━━━━━━━━━━━━━━━━"

                    select_embed = discord.Embed(
                        title="🗑️ Alliance Selection - Member Deletion",
                        description=(
                            "Please select an alliance to remove members:\n\n"
                            "**Permission Details**\n"
                            "━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"👤 **Access Level:** `{'Global Admin' if is_initial == 1 else 'Server Admin'}`\n"
                            f"🔍 **Access Type:** `{'All Alliances' if is_initial == 1 else 'Server + Special Access'}`\n"
                            f"📊 **Available Alliances:** `{len(alliances)}`\n"
                            "━━━━━━━━━━━━━━━━━━━━━━"
                            f"{special_alliance_text}"
                        ),
                        color=discord.Color.red()
                    )

                    alliances_with_counts = []
                    for alliance_id, name in alliances:
                        users_db = DatabaseManager.instance().get("users")
                        cursor = users_db.cursor()
                        cursor.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                        row = cursor.fetchone()
                        member_count = row[0] if row else 0
                        alliances_with_counts.append((alliance_id, name, member_count))

                    view = AllianceSelectView(alliances_with_counts, self.cog)

                    async def select_callback(interaction: discord.Interaction):
                        alliance_id = int(view.current_select.values[0])

                        alliance_db = DatabaseManager.instance().get("alliance")
                        cursor = alliance_db.cursor()
                        cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                        row = cursor.fetchone()
                        alliance_name = row[0] if row else "Unknown"
                        
                        users_db = DatabaseManager.instance().get("users")
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

                        max_fl = max(member[2] for member in members)
                        avg_fl = sum(member[2] for member in members) / len(members)

                        member_embed = discord.Embed(
                            title=f"👥 {alliance_name} -  Member Selection",
                            description=(
                                "```ml\n"
                                "Alliance Statistics\n"
                                "══════════════════════════\n"
                                f"📊 Total Member     : {len(members)}\n"
                                f"⚔️ Highest Level    : {self.cog.level_mapping.get(max_fl, str(max_fl))}\n"
                                f"📈 Average Level    : {self.cog.level_mapping.get(int(avg_fl), str(int(avg_fl)))}\n"
                                "══════════════════════════\n"
                                "```\n"
                                "Select the member you want to delete:"
                            ),
                            color=discord.Color.red()
                        )

                        member_view = MemberSelectView(members, alliance_name, self.cog)
                        
                        async def member_callback(member_interaction: discord.Interaction):
                            selected_value = member_view.current_select.values[0]
                            
                            if selected_value == "all":
                                confirm_embed = discord.Embed(
                                    title="⚠️ Confirmation Required",
                                    description=f"A total of **{len(members)}** members will be deleted.\nDo you confirm?",
                                    color=discord.Color.red()
                                )
                                
                                confirm_view = discord.ui.View()
                                confirm_button = discord.ui.Button(
                                    label="✅ Confirm", 
                                    style=discord.ButtonStyle.danger, 
                                    custom_id="confirm_all"
                                )
                                cancel_button = discord.ui.Button(
                                    label="❌ Cancel", 
                                    style=discord.ButtonStyle.secondary, 
                                    custom_id="cancel_all"
                                )
                                
                                confirm_view.add_item(confirm_button)
                                confirm_view.add_item(cancel_button)

                                async def confirm_callback(confirm_interaction: discord.Interaction):
                                    if confirm_interaction.data["custom_id"] == "confirm_all":
                                        users_db = DatabaseManager.instance().get("users")
                                        cursor = users_db.cursor()
                                        cursor.execute("SELECT fid, nickname FROM users WHERE alliance = ?", (alliance_id,))
                                        removed_members = cursor.fetchall()
                                        cursor.execute("DELETE FROM users WHERE alliance = ?", (alliance_id,))
                                        users_db.commit()
                                        
                                        try:
                                            settings_db = DatabaseManager.instance().get("settings")
                                            cursor = settings_db.cursor()
                                            cursor.execute("""
                                                SELECT channel_id 
                                                FROM alliance_logs 
                                                WHERE alliance_id = ?
                                            """, (alliance_id,))
                                            alliance_log_result = cursor.fetchone()
                                                
                                            if alliance_log_result and alliance_log_result[0]:
                                                log_embed = discord.Embed(
                                                    title="🗑️ Mass Member Removal",
                                                    description=(
                                                        f"**Alliance:** {alliance_name}\n"
                                                        f"**Administrator:** {confirm_interaction.user.name} (`{confirm_interaction.user.id}`)\n"
                                                        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                                                        f"**Total Members Removed:** {len(removed_members)}\n\n"
                                                        "**Removed Members:**\n"
                                                        "```\n" + 
                                                        "\n".join([f"FID{idx+1}: {fid}" for idx, (fid, _) in enumerate(removed_members[:20])]) +
                                                        (f"\n... ve {len(removed_members) - 20} FID more" if len(removed_members) > 20 else "") +
                                                        "\n```"
                                                    ),
                                                    color=discord.Color.red()
                                                )
                                                    
                                                try:
                                                    alliance_channel_id = int(alliance_log_result[0])
                                                    alliance_log_channel = self.bot.get_channel(alliance_channel_id)
                                                    if alliance_log_channel:
                                                        await alliance_log_channel.send(embed=log_embed)
                                                except Exception as e:
                                                    logger.info(f"Alliance Log Sending Error: {e}")
                                        except Exception as e:
                                            logger.info(f"Log record error: {e}")
                                        
                                        success_embed = discord.Embed(
                                            title="✅ Members Deleted",
                                            description=f"A total of **{len(removed_members)}** members have been successfully deleted.",
                                            color=discord.Color.green()
                                        )
                                        await confirm_interaction.response.edit_message(embed=success_embed, view=None)
                                    else:
                                        cancel_embed = discord.Embed(
                                            title="❌ Operation Cancelled",
                                            description="Member deletion operation has been cancelled.",
                                            color=discord.Color.orange()
                                        )
                                        await confirm_interaction.response.edit_message(embed=cancel_embed, view=None)

                                confirm_button.callback = confirm_callback
                                cancel_button.callback = confirm_callback
                                
                                await member_interaction.response.edit_message(
                                    embed=confirm_embed,
                                    view=confirm_view
                                )
                            
                            else:
                                try:
                                    selected_fid = selected_value
                                    users_db = DatabaseManager.instance().get("users")
                                    cursor = users_db.cursor()
                                    cursor.execute("SELECT nickname FROM users WHERE fid = ?", (selected_fid,))
                                    row = cursor.fetchone()
                                    nickname = row[0] if row else "Unknown"

                                    cursor.execute("DELETE FROM users WHERE fid = ?", (selected_fid,))
                                    users_db.commit()
                                    
                                    try:
                                        settings_db = DatabaseManager.instance().get("settings")
                                        cursor = settings_db.cursor()
                                        cursor.execute("""
                                            SELECT channel_id 
                                            FROM alliance_logs 
                                            WHERE alliance_id = ?
                                        """, (alliance_id,))
                                        alliance_log_result = cursor.fetchone()
                                            
                                        if alliance_log_result and alliance_log_result[0]:
                                            log_embed = discord.Embed(
                                                title="🗑️ Member Removed",
                                                description=(
                                                    f"**Alliance:** {alliance_name}\n"
                                                    f"**Administrator:** {member_interaction.user.name} (`{member_interaction.user.id}`)\n"
                                                    f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                                                    f"**Removed Member:**\n"
                                                    f"👤 **Name:** {nickname}\n"
                                                    f"🆔 **FID:** {selected_fid}"
                                                ),
                                                color=discord.Color.red()
                                            )
                                                
                                            try:
                                                alliance_channel_id = int(alliance_log_result[0])
                                                alliance_log_channel = self.bot.get_channel(alliance_channel_id)
                                                if alliance_log_channel:
                                                    await alliance_log_channel.send(embed=log_embed)
                                            except Exception as e:
                                                logger.info(f"Alliance Log Sending Error: {e}")
                                    except Exception as e:
                                        logger.info(f"Log record error: {e}")
                                    
                                    success_embed = discord.Embed(
                                        title="✅ Member Deleted",
                                        description=f"**{nickname}** has been successfully deleted.",
                                        color=discord.Color.green()
                                    )
                                    await member_interaction.response.edit_message(embed=success_embed, view=None)
                                    
                                except Exception as e:
                                    logger.info(f"Error in member removal: {e}")
                                    await member_interaction.response.send_message(
                                        "❌ An error occurred during member removal.",
                                        ephemeral=True
                                    )

                        member_view.callback = member_callback
                        await interaction.response.edit_message(
                            embed=member_embed,
                            view=member_view
                        )

                    view.callback = select_callback
                    await button_interaction.response.send_message(
                        embed=select_embed,
                        view=view,
                        ephemeral=True
                    )

                except Exception as e:
                    logger.info(f"Error in remove_member_button: {e}")
                    await button_interaction.response.send_message(
                        "❌ An error occurred during the member deletion process.",
                        ephemeral=True
                    )

            @discord.ui.button(
                label="View Members",
                emoji="👥",
                style=discord.ButtonStyle.primary,
                custom_id="view_members",
                row=0
            )
            async def view_members_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                try:
                    admin_result = _utils_get_admin_info(button_interaction.user.id)

                    if not admin_result:
                        await button_interaction.response.send_message(
                            "❌ You do not have permission to use this command.",
                            ephemeral=True
                        )
                        return

                    is_initial = admin_result[1]

                    alliances, special_alliances, is_global = await self.cog.get_admin_alliances(
                        button_interaction.user.id, 
                        button_interaction.guild_id
                    )
                    
                    if not alliances:
                        await button_interaction.response.send_message(
                            "❌ No alliance found that you have permission for.", 
                            ephemeral=True
                        )
                        return

                    special_alliance_text = ""
                    if special_alliances:
                        special_alliance_text = "\n\n**Special Access Alliances**\n"
                        special_alliance_text += "━━━━━━━━━━━━━━━━━━━━━━\n"
                        for _, name in special_alliances:
                            special_alliance_text += f"🔸 {name}\n"
                        special_alliance_text += "━━━━━━━━━━━━━━━━━━━━━━"

                    select_embed = discord.Embed(
                        title="👥 Alliance Selection",
                        description=(
                            "Please select an alliance to view members:\n\n"
                            "**Permission Details**\n"
                            "━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"👤 **Access Level:** `{'Global Admin' if is_initial == 1 else 'Server Admin'}`\n"
                            f"🔍 **Access Type:** `{'All Alliances' if is_initial == 1 else 'Server + Special Access'}`\n"
                            f"📊 **Available Alliances:** `{len(alliances)}`\n"
                            "━━━━━━━━━━━━━━━━━━━━━━"
                            f"{special_alliance_text}"
                        ),
                        color=discord.Color.blue()
                    )

                    alliances_with_counts = []
                    for alliance_id, name in alliances:
                        users_db = DatabaseManager.instance().get("users")
                        cursor = users_db.cursor()
                        cursor.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                        row = cursor.fetchone()
                        member_count = row[0] if row else 0
                        alliances_with_counts.append((alliance_id, name, member_count))

                    view = AllianceSelectView(alliances_with_counts, self.cog)

                    async def select_callback(interaction: discord.Interaction):
                        alliance_id = int(view.current_select.values[0])

                        alliance_db = DatabaseManager.instance().get("alliance")
                        cursor = alliance_db.cursor()
                        cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                        row = cursor.fetchone()
                        alliance_name = row[0] if row else "Unknown"
                        
                        users_db = DatabaseManager.instance().get("users")
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

                        max_fl = max(member[2] for member in members)
                        avg_fl = sum(member[2] for member in members) / len(members)

                        public_embed = discord.Embed(
                            title=f"👥 {alliance_name} - Member List",
                            description=(
                                "```ml\n"
                                "Alliance Statistics\n"
                                "══════════════════════════\n"
                                f"📊 Total Members    : {len(members)}\n"
                                f"⚔️ Highest Level    : {self.cog.level_mapping.get(max_fl, str(max_fl))}\n"
                                f"📈 Average Level    : {self.cog.level_mapping.get(int(avg_fl), str(int(avg_fl)))}\n"
                                "══════════════════════════\n"
                                "```\n"
                                "**Member List**\n"
                                "━━━━━━━━━━━━━━━━━━━━━━\n"
                            ),
                            color=discord.Color.blue()
                        )

                        members_per_page = 15
                        member_chunks = [members[i:i + members_per_page] for i in range(0, len(members), members_per_page)]
                        embeds = []

                        for page, chunk in enumerate(member_chunks):
                            embed = public_embed.copy()
                            
                            member_list = ""
                            for idx, (fid, nickname, furnace_lv) in enumerate(chunk, start=page * members_per_page + 1):
                                level = self.cog.level_mapping.get(furnace_lv, str(furnace_lv))
                                member_list += f"**{idx:02d}.** 👤 {nickname}\n└ ⚔️ `FC: {level}` | `FID: {fid}`\n\n"

                            embed.description += member_list
                            
                            if len(member_chunks) > 1:
                                embed.set_footer(text=f"Page {page + 1}/{len(member_chunks)}")
                            
                            embeds.append(embed)

                        pagination_view = PaginationView(embeds, interaction.user.id)
                        
                        await interaction.response.edit_message(
                            content="✅ Member list has been generated and posted below.",
                            embed=None,
                            view=None
                        )
                        
                        message = await interaction.channel.send(
                            embed=embeds[0],
                            view=pagination_view if len(embeds) > 1 else None
                        )
                        
                        if pagination_view:
                            pagination_view.message = message

                    view.callback = select_callback
                    await button_interaction.response.send_message(
                        embed=select_embed,
                        view=view,
                        ephemeral=True
                    )

                except Exception as e:
                    logger.info(f"Error in view_members_button: {e}")
                    if not button_interaction.response.is_done():
                        await button_interaction.response.send_message(
                            "❌ An error occurred while displaying the member list.",
                            ephemeral=True
                        )

            @discord.ui.button(
                label="Main Menu",
                emoji="🏠",
                style=discord.ButtonStyle.secondary,
                custom_id="member_ops_main_menu",
                row=2
            )
            async def main_menu_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                await self.cog.show_main_menu(interaction)

            @discord.ui.button(label="Transfer Member", emoji="🔄", style=discord.ButtonStyle.primary)
            async def transfer_member_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                try:
                    admin_result = _utils_get_admin_info(button_interaction.user.id)

                    if not admin_result:
                        await button_interaction.response.send_message(
                            "❌ You do not have permission to use this command.",
                            ephemeral=True
                        )
                        return

                    is_initial = admin_result[1]

                    
                    alliances, special_alliances, is_global = await self.cog.get_admin_alliances(
                        button_interaction.user.id, 
                        button_interaction.guild_id
                    )
                    
                    if not alliances:
                        await button_interaction.response.send_message(
                            "❌ No alliance found with your permissions.", 
                            ephemeral=True
                        )
                        return

                    
                    special_alliance_text = ""
                    if special_alliances:
                        special_alliance_text = "\n\n**Special Access Alliances**\n"
                        special_alliance_text += "━━━━━━━━━━━━━━━━━━━━━━\n"
                        for _, name in special_alliances:
                            special_alliance_text += f"🔸 {name}\n"
                        special_alliance_text += "━━━━━━━━━━━━━━━━━━━━━━"

                    
                    select_embed = discord.Embed(
                        title="🔄 Alliance Selection - Member Transfer",
                        description=(
                            "Select the **source** alliance from which you want to transfer members:\n\n"
                            "**Permission Details**\n"
                            "━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"👤 **Access Level:** `{'Global Admin' if is_initial == 1 else 'Server Admin'}`\n"
                            f"🔍 **Access Type:** `{'All Alliances' if is_initial == 1 else 'Server + Special Access'}`\n"
                            f"📊 **Available Alliances:** `{len(alliances)}`\n"
                            "━━━━━━━━━━━━━━━━━━━━━━"
                            f"{special_alliance_text}"
                        ),
                        color=discord.Color.blue()
                    )

                    
                    alliances_with_counts = []
                    for alliance_id, name in alliances:
                        users_db = DatabaseManager.instance().get("users")
                        cursor = users_db.cursor()
                        cursor.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                        row = cursor.fetchone()
                        member_count = row[0] if row else 0
                        alliances_with_counts.append((alliance_id, name, member_count))


                    view = AllianceSelectView(alliances_with_counts, self.cog)

                    async def source_callback(interaction: discord.Interaction):
                        try:
                            source_alliance_id = int(view.current_select.values[0])


                            alliance_db = DatabaseManager.instance().get("alliance")
                            cursor = alliance_db.cursor()
                            cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (source_alliance_id,))
                            row = cursor.fetchone()
                            source_alliance_name = row[0] if row else "Unknown"
                            
                            
                            users_db = DatabaseManager.instance().get("users")
                            cursor = users_db.cursor()
                            cursor.execute("""
                                SELECT fid, nickname, furnace_lv 
                                FROM users 
                                WHERE alliance = ? 
                                ORDER BY furnace_lv DESC, nickname
                            """, (source_alliance_id,))
                            members = cursor.fetchall()

                            if not members:
                                await interaction.response.send_message(
                                    "❌ No members found in this alliance.", 
                                    ephemeral=True
                                )
                                return

                            
                            max_fl = max(member[2] for member in members)
                            avg_fl = sum(member[2] for member in members) / len(members)

                            
                            member_embed = discord.Embed(
                                title=f"👥 {source_alliance_name} - Member Selection",
                                description=(
                                    "```ml\n"
                                    "Alliance Statistics\n"
                                    "══════════════════════════\n"
                                    f"📊 Total Members    : {len(members)}\n"
                                    f"⚔️ Highest Level    : {self.cog.level_mapping.get(max_fl, str(max_fl))}\n"
                                    f"📈 Average Level    : {self.cog.level_mapping.get(int(avg_fl), str(int(avg_fl)))}\n"
                                    "══════════════════════════\n"
                                    "```\n"
                                    "Select the member to transfer:\n\n"
                                    "**Selection Methods**\n"
                                    "1️⃣ Select member from menu below\n"
                                    "2️⃣ Click 'Select by FID' button and enter FID\n"
                                    "━━━━━━━━━━━━━━━━━━━━━━"
                                ),
                                color=discord.Color.blue()
                            )

                            
                            member_view = MemberSelectView(members, source_alliance_name, self.cog)
                            
                            async def member_callback(member_interaction: discord.Interaction):
                                selected_value = member_view.current_select.values[0]
                                if selected_value == "all":
                                    await member_interaction.response.send_message("❌ Cannot transfer all members at once. Please select a specific member.", ephemeral=True)
                                    return
                                selected_fid = int(selected_value)
                                
                                
                                users_db = DatabaseManager.instance().get("users")
                                cursor = users_db.cursor()
                                cursor.execute("SELECT nickname FROM users WHERE fid = ?", (selected_fid,))
                                row = cursor.fetchone()
                                selected_member_name = row[0] if row else "Unknown"


                                target_embed = discord.Embed(
                                    title="🎯 Target Alliance Selection",
                                    description=(
                                        f"Select target alliance to transfer "
                                        f"member **{selected_member_name}**:"
                                    ),
                                    color=discord.Color.blue()
                                )

                                
                                target_options = [
                                    discord.SelectOption(
                                        label=f"{name[:50]}",
                                        value=str(alliance_id),
                                        description=f"ID: {alliance_id} | Members: {count}",
                                        emoji="🏰"
                                    ) for alliance_id, name, count in alliances_with_counts
                                    if alliance_id != source_alliance_id
                                ]

                                target_select = discord.ui.Select(
                                    placeholder="🎯 Select target alliance...",
                                    options=target_options
                                )
                                
                                target_view = discord.ui.View()
                                target_view.add_item(target_select)

                                async def target_callback(target_interaction: discord.Interaction):
                                    target_alliance_id = int(target_select.values[0])

                                    try:

                                        alliance_db = DatabaseManager.instance().get("alliance")
                                        cursor = alliance_db.cursor()
                                        cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (target_alliance_id,))
                                        row = cursor.fetchone()
                                        target_alliance_name = row[0] if row else "Unknown"

                                        
                                        users_db = DatabaseManager.instance().get("users")
                                        cursor = users_db.cursor()
                                        cursor.execute(
                                            "UPDATE users SET alliance = ? WHERE fid = ?",
                                            (target_alliance_id, selected_fid)
                                        )
                                        users_db.commit()

                                        
                                        success_embed = discord.Embed(
                                            title="✅ Transfer Successful",
                                            description=(
                                                f"👤 **Member:** {selected_member_name}\n"
                                                f"🆔 **FID:** {selected_fid}\n"
                                                f"📤 **Source:** {source_alliance_name}\n"
                                                f"📥 **Target:** {target_alliance_name}"
                                            ),
                                            color=discord.Color.green()
                                        )
                                        
                                        await target_interaction.response.edit_message(
                                            embed=success_embed,
                                            view=None
                                        )
                                        
                                    except Exception as e:
                                        logger.info(f"Transfer error: {e}")
                                        error_embed = discord.Embed(
                                            title="❌ Error",
                                            description="An error occurred during the transfer operation.",
                                            color=discord.Color.red()
                                        )
                                        await target_interaction.response.edit_message(
                                            embed=error_embed,
                                            view=None
                                        )

                                target_select.callback = target_callback
                                await member_interaction.response.edit_message(
                                    embed=target_embed,
                                    view=target_view
                                )

                            member_view.callback = member_callback
                            await interaction.response.edit_message(
                                embed=member_embed,
                                view=member_view
                            )

                        except Exception as e:
                            logger.info(f"Source callback error: {e}")
                            await interaction.response.send_message(
                                "❌ An error occurred. Please try again.",
                                ephemeral=True
                            )

                    view.callback = source_callback
                    await button_interaction.response.send_message(
                        embed=select_embed,
                        view=view,
                        ephemeral=True
                    )

                except Exception as e:
                    logger.info(f"Error in transfer_member_button: {e}")
                    await button_interaction.response.send_message(
                        "❌ An error occurred during the transfer operation.",
                        ephemeral=True
                    )


        view = MemberOperationsView(self)
        await interaction.response.edit_message(embed=embed, view=view)

    async def add_member(self, interaction: discord.Interaction):
        cursor = self.conn_alliance.cursor()
        cursor.execute("SELECT alliance_id, name FROM alliance_list")
        alliances = cursor.fetchall()
        alliance_options = [discord.SelectOption(label=name, value=str(alliance_id)) for alliance_id, name in alliances]

        select = discord.ui.Select(placeholder="Select an alliance", options=alliance_options)
        view = discord.ui.View()
        view.add_item(select)

        async def select_callback(select_interaction: discord.Interaction):
            alliance_id = select.values[0]
            await select_interaction.response.send_modal(AddMemberModal(alliance_id))

        select.callback = select_callback
        await interaction.response.send_message("Please select an alliance:", view=view, ephemeral=True)

    async def remove_member(self, interaction: discord.Interaction):
        cursor = self.conn_alliance.cursor()
        cursor.execute("SELECT alliance_id, name FROM alliance_list")
        alliances = cursor.fetchall()
        alliance_options = [discord.SelectOption(label=name, value=str(alliance_id)) for alliance_id, name in alliances]

        select = discord.ui.Select(placeholder="Select an alliance", options=alliance_options)
        view = discord.ui.View()
        view.add_item(select)

        async def select_callback(select_interaction: discord.Interaction):
            alliance_id = select.values[0]

            cursor = self.conn_users.cursor()
            cursor.execute("SELECT fid, nickname FROM users WHERE alliance = ?", (alliance_id,))
            members = cursor.fetchall()
            
            if not members:
                await select_interaction.response.send_message("No members found in this alliance.", ephemeral=True)
                return

            
            member_options = [
                discord.SelectOption(
                    label=f"{nickname[:80]}",
                    value=str(fid),
                    description=f"FID: {fid}"
                ) for fid, nickname in members
            ]

            member_options = member_options[:24]  # Discord limit is 25, save 1 for ALL MEMBERS

            member_options.insert(0, discord.SelectOption(
                label="ALL MEMBERS",
                value="all",
                description="⚠️ Selecting this will remove all members!"
            ))

            member_select = discord.ui.Select(
                placeholder="Select member to remove",
                options=member_options
            )
            member_view = discord.ui.View()
            member_view.add_item(member_select)

            async def member_select_callback(member_interaction: discord.Interaction):
                selected_value = member_select.values[0]
                
                if selected_value == "all":
                    
                    embed = discord.Embed(
                        title="⚠️ Confirmation Required",
                        description=f"Total **{len(members)}** members will be removed.\nDo you confirm?",
                        color=discord.Color.red()
                    )
                    
                    confirm_view = discord.ui.View()
                    confirm_view.add_item(discord.ui.Button(label="✅ Confirm", style=discord.ButtonStyle.success, custom_id="confirm_all"))
                    confirm_view.add_item(discord.ui.Button(label="❌ Cancel", style=discord.ButtonStyle.danger, custom_id="cancel_all"))

                    async def button_callback(button_interaction: discord.Interaction):
                        try:
                            if button_interaction.data["custom_id"] == "confirm_all":
                                
                                fid_list = [str(fid) for fid, _ in members]
                                cursor = self.conn_users.cursor()
                                cursor.execute("DELETE FROM users WHERE alliance = ?", (alliance_id,))
                                self.conn_users.commit()
                                
                                result_embed = discord.Embed(
                                    title="✅ Members Removed",
                                    description=f"Total **{len(members)}** members removed.\n\n**Removed FIDs:**\n{', '.join(fid_list)}",
                                    color=discord.Color.green()
                                )
                                await button_interaction.response.edit_message(embed=result_embed, view=None)
                            else:
                                
                                cancel_embed = discord.Embed(
                                    title="❌ Operation Cancelled",
                                    description="Member removal operation has been cancelled.",
                                    color=discord.Color.orange()
                                )
                                await button_interaction.response.edit_message(embed=cancel_embed, view=None)
                        except Exception as e:
                            logger.info(f"Error in button operation: {e}")

                    
                    for button in confirm_view.children:
                        button.callback = button_callback

                    await member_interaction.response.edit_message(embed=embed, view=confirm_view)
                
                else:
                    try:
                        
                        selected_fid = selected_value
                        cursor = self.conn_users.cursor()
                        cursor.execute("SELECT nickname FROM users WHERE fid = ?", (selected_fid,))
                        row = cursor.fetchone()
                        nickname = row[0] if row else "Unknown"

                        cursor.execute("DELETE FROM users WHERE fid = ?", (selected_fid,))
                        self.conn_users.commit()
                        
                        result_embed = discord.Embed(
                            title="✅ Member Removed",
                            description=f"**{nickname}** (FID: {selected_fid}) has been successfully removed.",
                            color=discord.Color.green()
                        )
                        await member_interaction.response.edit_message(embed=result_embed, view=None)
                    except Exception as e:
                        logger.info(f"Error in member removal: {e}")

            member_select.callback = member_select_callback
            await select_interaction.response.edit_message(content=None, view=member_view)

        select.callback = select_callback
        await interaction.response.send_message("Please select an alliance:", view=view, ephemeral=True)

    async def add_user(self, interaction: discord.Interaction, alliance_id: str, ids: str, kid: str = None):
        cursor = self.conn_alliance.cursor()
        cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
        alliance_name = cursor.fetchone()
        if alliance_name:
            alliance_name = alliance_name[0]
        else:
            await interaction.response.send_message("Alliance not found.", ephemeral=True)
            return

        if not await self.is_admin(interaction.user.id):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        # Parse the FID list. Each entry may carry its own region as "fid:kid";
        # otherwise the batch-level `kid` (Region field) applies. Region is
        # required because the WOS API no longer offers player-info-by-FID
        # (see project_api_change_2026_07): without it we can neither fetch the
        # nickname nor redeem gift codes for the member.
        batch_kid = (str(kid).strip() if kid else "") or None
        ids_list = []
        kid_map = {}
        for entry in ids.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry:
                fid_part, kid_part = entry.split(":", 1)
                fid_part = fid_part.strip()
                kid_part = kid_part.strip()
            else:
                fid_part, kid_part = entry, batch_kid
            ids_list.append(fid_part)
            if kid_part:
                kid_map[fid_part] = kid_part

        
        total_users = len(ids_list)
        embed = discord.Embed(
            title="👥 User Addition Progress", 
            description=f"Processing {total_users} members...\n\n**Progress:** `0/{total_users}`", 
            color=discord.Color.blue()
        )
        embed.add_field(
            name=f"✅ Successfully Added (0/{total_users})", 
            value="-", 
            inline=False
        )
        embed.add_field(
            name=f"❌ Failed (0/{total_users})", 
            value="-", 
            inline=False
        )
        embed.add_field(
            name=f"⚠️ Already Exists (0/{total_users})", 
            value="-", 
            inline=False
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)
        message = await interaction.original_response()

        added_count = 0
        error_count = 0 
        already_exists_count = 0
        added_users = []
        error_users = []
        already_exists_users = []

        try:
            logger.info("ADD_MEMBERS admin=%s alliance=%s(%s) fids=%s count=%d",
                        interaction.user.id, alliance_name, alliance_id, ids, total_users)

            # Candidate kingdoms for auto-detecting a FID's region via the
            # gift-code oracle (used only when no region was supplied). Try the
            # kingdoms this alliance's existing members live in first (most
            # common first), then any other known kingdom.
            try:
                common_kids = [str(r[0]) for r in self.conn_users.execute(
                    "SELECT kid FROM users WHERE alliance=? AND kid IS NOT NULL AND kid!='' "
                    "GROUP BY kid ORDER BY COUNT(*) DESC", (alliance_id,)).fetchall()]
                all_kids = [str(r[0]) for r in self.conn_users.execute(
                    "SELECT DISTINCT kid FROM users WHERE kid IS NOT NULL AND kid!=''").fetchall()]
            except Exception as e:
                logger.warning("Could not build kid candidate list: %s", e)
                common_kids, all_kids = [], []
            try:
                from .regions import get_regions
                configured_kids = [k for k, _ in get_regions(alliance_id)]
            except Exception:
                configured_kids = []
            auto_candidates = list(dict.fromkeys([*configured_kids, *common_kids, *all_kids]))

            index = 0
            while index < len(ids_list):
                fid = ids_list[index]
                try:
                    embed.description = f"Processing {total_users} members...\n\n**Progress:** `{index + 1}/{total_users}`"

                    data = await fetch_player_info(fid)

                    if data == 429:
                        logger.warning("Rate limit for FID %s, waiting 60s", fid)
                        embed.description = "\u26a0\ufe0f API rate limit reached. Waiting for 60 seconds..."
                        embed.color = discord.Color.orange()
                        await message.edit(embed=embed)
                        await asyncio.sleep(60)
                        embed.description = f"Processing {total_users} members...\n\n**Progress:** `{index + 1}/{total_users}`"
                        embed.color = discord.Color.blue()
                        await message.edit(embed=embed)
                        continue

                    # 2026-07: the WOS player-info endpoint (/api/player) was
                    # removed upstream, so fetch_player_info can no longer return
                    # a nickname/furnace for a FID. When it fails, fall back to a
                    # placeholder record built from the admin-supplied region so
                    # the member is still registered and eligible for gift codes.
                    # If the endpoint ever returns, real data is used instead and
                    # this branch is skipped automatically.
                    if not (isinstance(data, dict) and data.get('data')):
                        pkid = kid_map.get(fid)
                        if not pkid and auto_candidates:
                            # No region supplied - auto-detect it via the oracle.
                            pkid = await resolve_kingdom(fid, auto_candidates)
                            if pkid:
                                logger.info("Auto-detected kingdom kid=%s for FID %s via gift-code oracle", pkid, fid)
                        if pkid:
                            logger.info("Player API unavailable for FID %s - adding placeholder with kid=%s", fid, pkid)
                            data = {"data": {
                                "nickname": str(fid),
                                "stove_lv": 0,
                                "stove_lv_content": None,
                                "kid": pkid,
                            }}

                    if isinstance(data, dict) and data.get('data'):
                        nickname = data['data'].get('nickname')
                        furnace_lv = data['data'].get('stove_lv', 0)
                        stove_lv_content = data['data'].get('stove_lv_content', None)
                        kid = data['data'].get('kid', None)

                        if nickname:
                            user_cursor = self.conn_users.cursor()
                            user_cursor.execute("SELECT * FROM users WHERE fid=?", (fid,))
                            result = user_cursor.fetchone()

                            if result is None:
                                try:
                                    user_cursor.execute("""
                                        INSERT INTO users (fid, nickname, furnace_lv, kid, stove_lv_content, alliance)
                                        VALUES (?, ?, ?, ?, ?, ?)
                                    """, (fid, nickname, furnace_lv, kid, stove_lv_content, alliance_id))
                                    self.conn_users.commit()

                                    logger.info("Added member FID=%s nickname=%s level=%s alliance=%s", fid, nickname, furnace_lv, alliance_id)

                                    added_count += 1
                                    added_users.append((fid, nickname))

                                    # Distribute pending gift codes to newly added member
                                    try:
                                        gc_db = DatabaseManager.instance().get("giftcode")
                                        gc_cursor = gc_db.cursor()
                                        gc_cursor.execute("SELECT status FROM giftcodecontrol WHERE alliance_id = ? AND status = 1", (alliance_id,))
                                        if gc_cursor.fetchone():
                                            gift_cog = self.bot.get_cog('GiftOperations')
                                            if gift_cog:
                                                _create_monitored_task(gift_cog.distributor.distribute_pending_codes_to_member(fid, alliance_id), name=f"distribute_codes_member_{fid}")
                                    except Exception as gift_e:
                                        logger.warning("Could not distribute gift codes to %s: %s", fid, gift_e)

                                    embed.set_field_at(
                                        0,
                                        name=f"\u2705 Successfully Added ({added_count}/{total_users})",
                                        value="User list cannot be displayed due to exceeding 70 users" if len(added_users) > 70
                                        else ", ".join([n for _, n in added_users]) or "-",
                                        inline=False
                                    )
                                    await message.edit(embed=embed)

                                except Exception as e:
                                    logger.error("DB error for FID %s: %s", fid, e)
                                    error_count += 1
                                    error_users.append(fid)

                                    embed.set_field_at(
                                        1,
                                        name=f"\u274c Failed ({error_count}/{total_users})",
                                        value="Error list cannot be displayed due to exceeding 70 users" if len(error_users) > 70
                                        else ", ".join(error_users) or "-",
                                        inline=False
                                    )
                                    await message.edit(embed=embed)
                            else:
                                logger.info("Member already exists: %s (FID: %s)", nickname, fid)
                                already_exists_count += 1
                                already_exists_users.append((fid, nickname))

                                embed.set_field_at(
                                    2,
                                    name=f"\u26a0\ufe0f Already Exists ({already_exists_count}/{total_users})",
                                    value="Existing user list cannot be displayed due to exceeding 70 users" if len(already_exists_users) > 70
                                    else ", ".join([n for _, n in already_exists_users]) or "-",
                                    inline=False
                                )
                                await message.edit(embed=embed)
                        else:
                            error_count += 1
                            error_users.append(fid)
                    else:
                        logger.warning("No data for FID %s, response=%s", fid, data)
                        error_count += 1
                        if fid not in error_users:
                            error_users.append(fid)

                        embed.set_field_at(
                            1,
                            name=f"\u274c Failed ({error_count}/{total_users})",
                            value="Error list cannot be displayed due to exceeding 70 users" if len(error_users) > 70
                            else ", ".join(error_users) or "-",
                            inline=False
                        )
                        await message.edit(embed=embed)

                    index += 1

                except Exception as e:
                    logger.error("Request failed for FID %s: %s", fid, e)
                    error_count += 1
                    error_users.append(fid)
                    await message.edit(embed=embed)
                    index += 1

            embed.set_field_at(0, name=f"✅ Successfully Added ({added_count}/{total_users})",
                value="User list cannot be displayed due to exceeding 70 users" if len(added_users) > 70 
                else ", ".join([nickname for _, nickname in added_users]) or "-",
                inline=False
            )
            
            embed.set_field_at(1, name=f"❌ Failed ({error_count}/{total_users})",
                value="Error list cannot be displayed due to exceeding 70 users" if len(error_users) > 70 
                else ", ".join(error_users) or "-",
                inline=False
            )
            
            embed.set_field_at(2, name=f"⚠️ Already Exists ({already_exists_count}/{total_users})",
                value="Existing user list cannot be displayed due to exceeding 70 users" if len(already_exists_users) > 70 
                else ", ".join([nickname for _, nickname in already_exists_users]) or "-",
                inline=False
            )

            await message.edit(embed=embed)

            try:
                settings_db = DatabaseManager.instance().get("settings")
                cursor = settings_db.cursor()
                cursor.execute("""
                    SELECT channel_id 
                    FROM alliance_logs 
                    WHERE alliance_id = ?
                """, (alliance_id,))
                alliance_log_result = cursor.fetchone()
                    
                if alliance_log_result and alliance_log_result[0]:
                    log_embed = discord.Embed(
                        title="👥 Members Added to Alliance",
                        description=(
                            f"**Alliance:** {alliance_name}\n"
                            f"**Administrator:** {interaction.user.name} (`{interaction.user.id}`)\n"
                            f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                            f"**Results:**\n"
                            f"✅ Successfully Added: {added_count}\n"
                            f"❌ Failed: {error_count}\n"
                            f"⚠️ Already Exists: {already_exists_count}\n\n"
                            "**Added FIDs:**\n"
                            f"```\n{','.join(ids_list)}\n```"
                        ),
                        color=discord.Color.green()
                    )

                    try:
                        alliance_channel_id = int(alliance_log_result[0])
                        alliance_log_channel = self.bot.get_channel(alliance_channel_id)
                        if alliance_log_channel:
                            await alliance_log_channel.send(embed=log_embed)
                    except Exception as e:
                        logger.error("Alliance log send error: %s", e)

            except Exception as e:
                logger.error("Log record error: %s", e)

            logger.info("ADD_MEMBERS results: added=%d failed=%d exists=%d", added_count, error_count, already_exists_count)

        except Exception as e:
            logger.exception("Critical error in add members: %s", e)

        embed.title = "✅ User Addition Completed"
        embed.description = f"Process completed for {total_users} members."
        embed.color = discord.Color.green()
        await message.edit(embed=embed)

    async def is_admin(self, user_id):
        try:
            return _utils_check_admin(user_id)
        except Exception as e:
            logger.info(f"Error in admin check: {str(e)}")
            logger.info(f"Error details: {str(e.__class__.__name__)}")
            return False

    def cog_unload(self):
        pass

    async def get_admin_alliances(self, user_id: int, guild_id: int):
        try:
            admin_result = _utils_get_admin_info(user_id)

            if not admin_result:
                logger.info(f"User {user_id} is not an admin")
                return [], [], False

            is_initial = admin_result[1]

            if is_initial == 1:
                alliance_db = DatabaseManager.instance().get("alliance")
                cursor = alliance_db.cursor()
                cursor.execute("SELECT alliance_id, name FROM alliance_list ORDER BY name")
                alliances = cursor.fetchall()
                return alliances, [], True

            server_alliances = []
            special_alliances = []

            alliance_db = DatabaseManager.instance().get("alliance")
            cursor = alliance_db.cursor()
            cursor.execute("""
                SELECT DISTINCT alliance_id, name
                FROM alliance_list
                WHERE discord_server_id = ?
                ORDER BY name
            """, (guild_id,))
            server_alliances = cursor.fetchall()
            
            
            settings_db = DatabaseManager.instance().get("settings")
            cursor = settings_db.cursor()
            cursor.execute("""
                SELECT alliances_id 
                FROM adminserver 
                WHERE admin = ?
            """, (user_id,))
            special_alliance_ids = cursor.fetchall()
                
            
            if special_alliance_ids:
                alliance_db = DatabaseManager.instance().get("alliance")
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
            return [], [], False

    async def show_main_menu(self, interaction: discord.Interaction):
        try:
            alliance_cog = self.bot.get_cog("Alliance")
            if alliance_cog:
                await alliance_cog.show_main_menu(interaction)
            else:
                await interaction.response.send_message(
                    "❌ An error occurred while returning to the main menu.",
                    ephemeral=True
                )
        except Exception as e:
            logger.info(f"[ERROR] Main Menu error in member operations: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while returning to main menu.", 
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "An error occurred while returning to main menu.",
                    ephemeral=True
                )

class AddMemberModal(discord.ui.Modal):
    def __init__(self, alliance_id):
        super().__init__(title="Add Member")
        self.alliance_id = alliance_id
        self.add_item(discord.ui.TextInput(
            label="Enter IDs (comma-separated)",
            placeholder="Example: 12345,67890  (or 12345:1587 per-region)",
            style=discord.TextStyle.paragraph
        ))
        # Region (kingdom id): since 2026-07 the WOS API no longer exposes a
        # player-info-by-FID endpoint, so the bot cannot look up a player's
        # kingdom itself. Pre-filled with the alliance's default region (see
        # /region_default); one region applies to every FID above, unless
        # overridden per-FID with "fid:kid". Blank falls back to auto-detect.
        try:
            from .regions import get_default_region
            default_region = get_default_region(alliance_id) or ""
        except Exception:
            default_region = ""
        self.add_item(discord.ui.TextInput(
            label="Region (kingdom id) - optional",
            placeholder="Leave empty to auto-detect (e.g. 1587)",
            required=False,
            default=default_region,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        try:


            ids = self.children[0].value
            kid = self.children[1].value
            await interaction.client.get_cog("AllianceMemberOperations").add_user(
                interaction,
                self.alliance_id,
                ids,
                kid,
            )
        except Exception as e:
            logger.info(f"ERROR: Modal submit error - {str(e)}")
            await interaction.response.send_message(
                "An error occurred. Please try again.", 
                ephemeral=True
            )

class RemoveMemberModal(discord.ui.Modal):
    def __init__(self, alliance_id):
        super().__init__(title="Remove Member")
        self.alliance_id = alliance_id
        self.add_item(discord.ui.TextInput(label="Enter IDs (comma-separated)", placeholder="e.g., 12345,67890"))

    async def on_submit(self, interaction: discord.Interaction):
        ids = self.children[0].value
        await interaction.client.get_cog("AllianceMemberOperations").remove_user(interaction, self.alliance_id, ids)


class MemberSelectView(discord.ui.View):
    def __init__(self, members, source_alliance_name, cog, page=0):
        super().__init__(timeout=180)
        self.members = members
        self.source_alliance_name = source_alliance_name
        self.cog = cog
        self.page = page
        self.max_page = (len(members) - 1) // 25
        self.current_select = None
        self.callback = None
        self.member_dict = {str(fid): nickname for fid, nickname, _ in members}
        self.selected_alliance_id = None
        self.alliances = None
        self.update_select_menu()

    def update_select_menu(self):
        for item in self.children[:]:
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)

        start_idx = self.page * 25
        end_idx = min(start_idx + 25, len(self.members))
        current_members = self.members[start_idx:end_idx]

        options = []
        
        if self.page == 0:
            options.append(discord.SelectOption(
                label="ALL MEMBERS",
                value="all",
                description=f"⚠️ Delete all {len(self.members)} members!",
                emoji="⚠️"
            ))

        remaining_slots = 25 - len(options)
        member_options = [
            discord.SelectOption(
                label=f"{nickname[:50]}",
                value=str(fid),
                description=f"FID: {fid} | FC: {self.cog.level_mapping.get(furnace_lv, str(furnace_lv))}",
                emoji="👤"
            ) for fid, nickname, furnace_lv in current_members[:remaining_slots]
        ]
        options.extend(member_options)

        select = discord.ui.Select(
            placeholder=f"👤 Select member to transfer... (Page {self.page + 1}/{self.max_page + 1})",
            options=options
        )
        
        async def select_callback(interaction: discord.Interaction):
            self.current_select = select
            if self.callback:
                await self.callback(interaction)
        
        select.callback = select_callback
        self.add_item(select)
        self.current_select = select

        if hasattr(self, 'prev_button'):
            self.prev_button.disabled = self.page == 0
        if hasattr(self, 'next_button'):
            self.next_button.disabled = self.page == self.max_page

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.update_select_menu()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.max_page, self.page + 1)
        self.update_select_menu()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Select by FID", emoji="🔍", style=discord.ButtonStyle.secondary)
    async def fid_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            
            if self.current_select and self.current_select.values:
                self.selected_alliance_id = self.current_select.values[0]
            
            modal = FIDSearchModal(
                selected_alliance_id=self.selected_alliance_id,
                alliances=self.alliances,
                callback=self.callback
            )
            await interaction.response.send_modal(modal)
        except Exception as e:
            logger.info(f"FID button error: {e}")
            await interaction.response.send_message(
                "❌ An error has occurred. Please try again.",
                ephemeral=True
            )

async def setup(bot):
    await bot.add_cog(AllianceMemberOperations(bot))