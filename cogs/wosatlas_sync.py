"""Slash commands for the WoS Atlas data source.

CenturyGame's /api/player is gone (2026-07), so member nickname, furnace level
and kingdom are refreshed from WoS Atlas instead. The alliance control loop does
this on its own schedule; these commands are for on-demand runs — after a state
transfer, after a rename wave, or when a gift code fails with a state error.
"""

import discord
from discord import app_commands
from discord.ext import commands

from . import wosatlas_api
from .config import LEVEL_MAPPING
from .database import DatabaseManager
from .log_config import get_logger
from .utils import check_admin

logger = get_logger("wosatlas_sync")


def _furnace_display(level):
    return LEVEL_MAPPING.get(level, level)


async def _alliance_autocomplete(interaction: discord.Interaction, current: str):
    db = DatabaseManager.instance().get("alliance")
    rows = db.execute("SELECT alliance_id, name FROM alliance_list").fetchall()
    current = (current or "").lower()
    return [
        app_commands.Choice(name=f"{name} ({aid})", value=str(aid))
        for aid, name in rows
        if current in str(name).lower() or current in str(aid)
    ][:25]


class WosAtlasSync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_unload(self):
        await wosatlas_api.get_client().close()

    # -- /atlas_sync ------------------------------------------------------

    @app_commands.command(
        name="atlas_sync",
        description="Refresh nickname, furnace level and state from WoS Atlas",
    )
    @app_commands.describe(alliance="Alliance to refresh (leave empty for all)")
    @app_commands.autocomplete(alliance=_alliance_autocomplete)
    async def atlas_sync(self, interaction: discord.Interaction, alliance: str | None = None):
        if not check_admin(interaction.user.id):
            await interaction.response.send_message("You do not have permission.", ephemeral=True)
            return
        if not wosatlas_api.available():
            await interaction.response.send_message(
                "WoS Atlas is not configured — set `WOSATLAS_EMAIL` and "
                "`WOSATLAS_PASSWORD` in `.env`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        users_db = DatabaseManager.instance().get("users")
        if alliance:
            rows = users_db.execute(
                "SELECT fid, nickname, furnace_lv, kid FROM users WHERE alliance = ?",
                (alliance,),
            ).fetchall()
        else:
            rows = users_db.execute(
                "SELECT fid, nickname, furnace_lv, kid FROM users"
            ).fetchall()

        if not rows:
            await interaction.followup.send("No members found.")
            return

        name_changes, furnace_changes, kid_changes = [], [], []
        resolved = missing = 0

        for index, (fid, old_nick, old_furnace, old_kid) in enumerate(rows, start=1):
            if index % 25 == 0:
                # Keeps the user informed and the interaction token warm on
                # long runs (a full roster is ~1.1s per member).
                try:
                    await interaction.edit_original_response(
                        content=f"🛰️ Syncing… {index}/{len(rows)} members checked"
                    )
                except discord.HTTPException:
                    pass
            snapshot = await wosatlas_api.lookup_fid(fid, use_cache=False)
            if not snapshot or not snapshot.get("nickname"):
                missing += 1
                continue
            resolved += 1

            sets, values = [], []
            new_nick = snapshot["nickname"]
            new_furnace = snapshot.get("furnace_lv")
            new_kid = snapshot.get("kid")

            if wosatlas_api.is_placeholder_name(new_nick, fid):
                new_nick = old_nick  # never downgrade to a generated default

            if new_nick != old_nick:
                sets.append("nickname = ?")
                values.append(new_nick)
                name_changes.append(f"📝 `{old_nick}` ➡️ `{new_nick}`")
            if new_furnace and new_furnace != old_furnace:
                sets.append("furnace_lv = ?")
                values.append(new_furnace)
                furnace_changes.append(
                    f"👤 **{new_nick}**\n🔥 `{_furnace_display(old_furnace)}` ➡️ "
                    f"`{_furnace_display(new_furnace)}`"
                )
            if new_kid and str(new_kid) != str(old_kid or ""):
                new_kid = await wosatlas_api.confirm_kid(fid, new_kid, old_kid)
            if new_kid and str(new_kid) != str(old_kid or ""):
                sets.append("kid = ?")
                values.append(new_kid)
                kid_changes.append(
                    f"👤 **{new_nick}**\n🌍 `{old_kid}` ➡️ `{new_kid}`"
                )

            if sets:
                values.append(fid)
                users_db.execute(
                    f"UPDATE users SET {', '.join(sets)} WHERE fid = ?", values
                )
                users_db.commit()

        logger.info(
            "ATLAS_SYNC admin=%s alliance=%s resolved=%s missing=%s "
            "names=%s furnace=%s kid=%s",
            interaction.user.id, alliance or "all", resolved, missing,
            len(name_changes), len(furnace_changes), len(kid_changes),
        )

        embed = discord.Embed(
            title="🛰️ WoS Atlas Sync",
            description=(
                f"✅ Resolved **{resolved}** of **{len(rows)}** members "
                f"({missing} not in the Atlas index)"
            ),
            color=discord.Color.green(),
        )
        for title, entries in (
            ("📝 Nickname Changes", name_changes),
            ("🔥 Furnace Level Changes", furnace_changes),
            ("🌍 State Changes", kid_changes),
        ):
            if entries:
                shown = "\n".join(entries[:10])
                if len(entries) > 10:
                    shown += f"\n… and {len(entries) - 10} more"
                embed.add_field(name=f"{title} ({len(entries)})", value=shown, inline=False)
        if not (name_changes or furnace_changes or kid_changes):
            embed.add_field(name="📊 Result", value="No changes detected.", inline=False)

        try:
            await interaction.followup.send(embed=embed)
        except discord.HTTPException:
            # The interaction token expires after ~15 minutes; fall back to a
            # plain channel message so a long sync still reports its result.
            logger.warning("atlas_sync: interaction expired, posting to channel instead")
            if interaction.channel:
                await interaction.channel.send(embed=embed)

    # -- /atlas_lookup ----------------------------------------------------

    @app_commands.command(
        name="atlas_lookup",
        description="Look up one Chief ID in WoS Atlas (nickname, furnace, state, power)",
    )
    @app_commands.describe(fid="Chief ID")
    async def atlas_lookup(self, interaction: discord.Interaction, fid: str):
        # Gated like /atlas_sync: every lookup occupies the shared, rate-limited
        # Atlas session that the control loop and gift-code repair depend on.
        if not check_admin(interaction.user.id):
            await interaction.response.send_message("You do not have permission.", ephemeral=True)
            return
        if not wosatlas_api.available():
            await interaction.response.send_message(
                "WoS Atlas is not configured.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)
        snapshot = await wosatlas_api.lookup_fid(fid, use_cache=False)
        if not snapshot:
            await interaction.followup.send(
                f"No Atlas record for `{fid}` — the player is not in their index."
            )
            return

        embed = discord.Embed(
            title=f"🛰️ {snapshot['nickname']}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="🆔 FID", value=str(snapshot["fid"]), inline=True)
        embed.add_field(
            name="🔥 Furnace",
            value=str(_furnace_display(snapshot.get("furnace_lv"))),
            inline=True,
        )
        embed.add_field(name="🌍 State", value=str(snapshot.get("kid")), inline=True)
        if snapshot.get("power") is not None:
            embed.add_field(name="⚔️ Power", value=f"{snapshot['power']:,}", inline=True)
        if snapshot.get("alliance_abbr"):
            embed.add_field(name="🏰 Alliance", value=snapshot["alliance_abbr"], inline=True)
        if snapshot.get("x") is not None and snapshot.get("y") is not None:
            embed.add_field(
                name="📍 Coordinates", value=f"X:{snapshot['x']} Y:{snapshot['y']}", inline=True
            )
        embed.set_footer(text=f"Observed {snapshot.get('observed_at', 'unknown')} · WoS Atlas")

        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(WosAtlasSync(bot))
