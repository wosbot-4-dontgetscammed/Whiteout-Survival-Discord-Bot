import discord
from discord.ext import commands
import asyncio
from .database import DatabaseManager
from .config import LEVEL_MAPPING
from .log_config import get_logger
from .utils import build_embed
from .wos_api import fetch_player_info

logger = get_logger("w")

class WCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.conn = DatabaseManager.instance().get("changes")
        self.c = self.conn.cursor()
        self.level_mapping = LEVEL_MAPPING

    @discord.app_commands.command(name='w', description='Fetches user info using fid.')
    async def w(self, interaction: discord.Interaction, fid: str):
        await self.fetch_user_info(interaction, fid)

    @w.autocomplete('fid')
    async def autocomplete_fid(self, interaction: discord.Interaction, current: str):
        try:
            users_db = DatabaseManager.instance().get("users")
            cursor = users_db.cursor()
            if current:
                cursor.execute("SELECT fid, nickname FROM users WHERE CAST(fid AS TEXT) LIKE ? OR nickname LIKE ? LIMIT 25", (f"%{current}%", f"%{current}%"))
            else:
                cursor.execute("SELECT fid, nickname FROM users LIMIT 25")
            users = cursor.fetchall()

            choices = [
                discord.app_commands.Choice(name=f"{nickname} ({fid})", value=str(fid))
                for fid, nickname in users
            ]

            return choices[:25]

        except Exception as e:
            logger.error(f"Autocomplete could not be loaded: {e}")
            return []


    async def fetch_user_info(self, interaction: discord.Interaction, fid: str):
        try:
            await interaction.response.defer(thinking=True)

            max_retries = 3
            retry_delay = 60

            for attempt in range(max_retries):
                data = await fetch_player_info(fid)

                if isinstance(data, dict):
                    user_data = data.get('data', {})
                    if not user_data:
                        await interaction.followup.send(f"User with ID {fid} not found (no data returned).")
                        return

                    nickname = user_data.get('nickname', 'Unknown')
                    fid_value = user_data.get('fid', 0)
                    stove_level = user_data.get('stove_lv', 0)
                    kid = user_data.get('kid', 0)
                    avatar_image = user_data.get('avatar_image', None)
                    stove_lv_content = user_data.get('stove_lv_content')

                    if stove_level > 30:
                        stove_level_name = self.level_mapping.get(stove_level, f"Level {stove_level}")
                    else:
                        stove_level_name = f"Level {stove_level}"

                    user_info = None
                    alliance_info = None

                    users_db = DatabaseManager.instance().get("users")
                    cursor = users_db.cursor()
                    cursor.execute("SELECT *, alliance FROM users WHERE fid=?", (fid_value,))
                    user_info = cursor.fetchone()

                    if user_info and user_info[-1]:
                        alliance_db = DatabaseManager.instance().get("alliance")
                        cursor = alliance_db.cursor()
                        cursor.execute("SELECT name FROM alliance_list WHERE alliance_id=?", (user_info[-1],))
                        alliance_info = cursor.fetchone()

                    fields = {
                        "\U0001f194 FID": str(fid_value),
                        "\U0001f525 Furnace Level": stove_level_name,
                        "\U0001f30d State": str(kid),
                    }
                    if alliance_info:
                        fields["\U0001f3f0 Alliance"] = alliance_info[0]

                    registration_status = "Registered on the List \u2705" if user_info else "Not on the List \u274c"
                    embed = build_embed(f"\U0001f464 {nickname}", fields, color=discord.Color.blue(), footer=registration_status)

                    if avatar_image:
                        embed.set_image(url=avatar_image)
                    if isinstance(stove_lv_content, str) and stove_lv_content.startswith("http"):
                        embed.set_thumbnail(url=stove_lv_content)

                    await interaction.followup.send(embed=embed)
                    return

                elif data == 429:
                    if attempt < max_retries - 1:
                        if attempt == 0:
                            await interaction.followup.send("API limit reached, your result will be displayed automatically shortly...")
                        await asyncio.sleep(retry_delay)

            await interaction.followup.send(f"User with ID {fid} not found or an error occurred after multiple attempts.")

        except Exception as e:
            logger.exception(f"An error occurred: {e}")
            await interaction.followup.send("An error occurred while fetching user info.")


async def setup(bot):
    await bot.add_cog(WCommand(bot))
