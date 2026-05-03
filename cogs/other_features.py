import discord
from discord.ext import commands
from .log_config import get_logger

logger = get_logger("other_features")

class OtherFeatures(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
    async def show_other_features_menu(self, interaction: discord.Interaction):
        try:
            embed = discord.Embed(
                title="🔧 Other Features",
                description=(
                    "This section was created according to users' requests:\n\n"
                    "**Available Operations**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "🐻 **Bear Trap**\n"
                    "└ Time notification system\n"
                    "└ Not just for Bear! Use it for any event:\n"
                    "   Bear - KE - Forst - CJ and everything else\n"
                    "└ Add unlimited notifications\n\n"
                    "🆔 **ID Channel**\n"
                    "└ Create and manage ID channels\n"
                    "└ Automatic ID verification system\n"
                    "└ Custom channel settings\n\n"
                    "💾 **Backup System**\n"
                    "└ Automatic database backup\n"
                    "└ Secure backup storage\n"
                    "└ Only for Global Admins\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=discord.Color.blue()
            )
            
            view = OtherFeaturesView(self)
            
            if not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=view)
            else:
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                
        except Exception as e:
            logger.error("Error in show_other_features_menu: %s", e)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred. Please try again.",
                    ephemeral=True
                )

class OtherFeaturesView(discord.ui.View):
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
        label="Bear Trap",
        emoji="🐻",
        style=discord.ButtonStyle.primary,
        custom_id="bear_trap",
        row=0
    )
    async def bear_trap_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            bear_trap_cog = self.cog.bot.get_cog("BearTrap")
            if bear_trap_cog:
                await bear_trap_cog.show_bear_trap_menu(interaction)
            else:
                await interaction.response.send_message(
                    "❌ Bear Trap module not found.",
                    ephemeral=True
                )
        except Exception as e:
            logger.error("Error loading Bear Trap menu: %s", e)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while loading Bear Trap menu.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ An error occurred while loading Bear Trap menu.",
                    ephemeral=True
                )

    @discord.ui.button(
        label="ID Channel",
        emoji="🆔",
        style=discord.ButtonStyle.primary,
        custom_id="id_channel",
        row=0
    )
    async def id_channel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            id_channel_cog = self.cog.bot.get_cog("IDChannel")
            if id_channel_cog:
                await id_channel_cog.show_id_channel_menu(interaction)
            else:
                await interaction.response.send_message(
                    "❌ ID Channel module not found.",
                    ephemeral=True
                )
        except Exception as e:
            logger.error("Error loading ID Channel menu: %s", e)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while loading ID Channel menu.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ An error occurred while loading ID Channel menu.",
                    ephemeral=True
                )

    @discord.ui.button(
        label="Backup System",
        emoji="💾",
        style=discord.ButtonStyle.primary,
        custom_id="backup_system",
        row=1
    )
    async def backup_system_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            backup_cog = self.cog.bot.get_cog("BackupOperations")
            if backup_cog:
                await backup_cog.show_backup_menu(interaction)
            else:
                await interaction.response.send_message(
                    "❌ Backup System module not found.",
                    ephemeral=True
                )
        except Exception as e:
            logger.error("Error loading Backup System menu: %s", e)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while loading Backup System menu.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ An error occurred while loading Backup System menu.",
                    ephemeral=True
                )

    @discord.ui.button(
        label="Main Menu",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="other_main_menu",
        row=2
    )
    async def main_menu_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            alliance_cog = self.cog.bot.get_cog("Alliance")
            if alliance_cog:
                await alliance_cog.show_main_menu(interaction)
        except Exception as e:
            logger.error("Error returning to main menu: %s", e)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while returning to main menu.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ An error occurred while returning to main menu.",
                    ephemeral=True
                )

async def setup(bot):
    await bot.add_cog(OtherFeatures(bot)) 