"""Per-alliance reporting on gift-code redemptions.

`user_giftcodes` records one row per (member, code) with the redemption status,
but nothing in the bot ever summarised it per alliance. These commands answer
"which codes did this alliance actually get, and who is missing one".
"""

from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .database import DatabaseManager
from .log_config import get_logger
from .utils import check_admin

logger = get_logger("giftcode_report")

# Statuses that mean the member holds the reward.
REDEEMED = ("SUCCESS", "RECEIVED", "SAME TYPE EXCHANGE")


def _alliance_names() -> dict:
    rows = DatabaseManager.instance().get("alliance").execute(
        "SELECT alliance_id, name FROM alliance_list"
    ).fetchall()
    return {str(aid): name for aid, name in rows}


async def _alliance_autocomplete(interaction: discord.Interaction, current: str):
    current = (current or "").lower()
    return [
        app_commands.Choice(name=f"{name} ({aid})", value=str(aid))
        for aid, name in _alliance_names().items()
        if current in name.lower() or current in str(aid)
    ][:25]


def _members_by_alliance():
    """{alliance_id: {fid: nickname}} for every registered member."""
    rows = DatabaseManager.instance().get("users").execute(
        "SELECT fid, nickname, alliance FROM users"
    ).fetchall()
    out = {}
    for fid, nickname, alliance in rows:
        out.setdefault(str(alliance), {})[fid] = nickname
    return out


def _redemptions():
    """{fid: {giftcode: status}} from the redemption log."""
    rows = DatabaseManager.instance().get("giftcode").execute(
        "SELECT fid, giftcode, status FROM user_giftcodes"
    ).fetchall()
    out = {}
    for fid, code, status in rows:
        out.setdefault(fid, {})[code] = status
    return out


def _known_codes():
    rows = DatabaseManager.instance().get("giftcode").execute(
        "SELECT giftcode, date FROM gift_codes ORDER BY date DESC"
    ).fetchall()
    return [code for code, _date in rows]


class GiftCodeReport(commands.Cog):
    """Reports, plus a daily digest.

    The existing summaries only appear on an event: the distribution summary when
    a new code arrives, the retry summary only when a retry actually happened. So
    a quiet day produces nothing at all. This posts one digest per alliance per
    day, whether or not anything moved.
    """

    def __init__(self, bot):
        self.bot = bot
        db = DatabaseManager.instance().get("giftcode")
        db.execute("""
            CREATE TABLE IF NOT EXISTS giftcode_digest (
                alliance_id INTEGER PRIMARY KEY,
                enabled INTEGER DEFAULT 1,
                last_sent TEXT
            )
        """)
        db.commit()
        self.daily_digest.start()

    async def cog_unload(self):
        self.daily_digest.cancel()

    # -- daily digest -----------------------------------------------------

    @tasks.loop(hours=1)
    async def daily_digest(self):
        """Post one summary per alliance per day into its gift-code channel."""
        try:
            db = DatabaseManager.instance().get("giftcode")
            alliance_db = DatabaseManager.instance().get("alliance")
            members = _members_by_alliance()
            redemptions = _redemptions()
            codes = _known_codes()
            if not codes:
                return

            now = datetime.now()
            for alliance_id, roster in members.items():
                if not roster:
                    continue

                row = db.execute(
                    "SELECT enabled, last_sent FROM giftcode_digest WHERE alliance_id = ?",
                    (alliance_id,),
                ).fetchone()
                if row and not row[0]:
                    continue
                if row and row[1]:
                    try:
                        if now - datetime.fromisoformat(row[1]) < timedelta(hours=23):
                            continue
                    except ValueError:
                        pass

                ch_row = alliance_db.execute(
                    "SELECT channel_id FROM alliancesettings WHERE alliance_id = ?",
                    (alliance_id,),
                ).fetchone()
                if not ch_row:
                    continue
                channel = self.bot.get_channel(ch_row[0])
                if not channel:
                    continue

                embed = self._build_digest(alliance_id, roster, redemptions, codes)
                try:
                    await channel.send(embed=embed)
                except discord.HTTPException as e:
                    logger.warning("digest send failed for alliance %s: %s", alliance_id, e)
                    continue

                db.execute(
                    "INSERT INTO giftcode_digest (alliance_id, enabled, last_sent) "
                    "VALUES (?, 1, ?) ON CONFLICT(alliance_id) DO UPDATE SET last_sent = ?",
                    (alliance_id, now.isoformat(), now.isoformat()),
                )
                db.commit()
                logger.info("GIFTCODE_DIGEST posted for alliance %s", alliance_id)
        except Exception as e:
            logger.exception("daily_digest failed: %s", e)

    @daily_digest.before_loop
    async def before_daily_digest(self):
        await self.bot.wait_until_ready()

    def _build_digest(self, alliance_id, roster, redemptions, codes):
        names = _alliance_names()
        lines, fully, partial = [], 0, 0
        for giftcode in codes[:15]:
            got = [f for f in roster if redemptions.get(f, {}).get(giftcode) in REDEEMED]
            share = round(100 * len(got) / len(roster)) if roster else 0
            if len(got) == len(roster):
                fully += 1
                lines.append(f"✅ `{giftcode}` — all {len(roster)}")
            else:
                partial += 1
                lines.append(f"⏳ `{giftcode}` — **{len(got)}/{len(roster)}** ({share}%)")

        embed = discord.Embed(
            title=f"🎁 Daily Gift Code Summary — {names.get(str(alliance_id), alliance_id)}",
            description=(
                f"👥 {len(roster)} members · ✅ {fully} codes complete · ⏳ {partial} incomplete"
            ),
            color=discord.Color.green() if not partial else discord.Color.gold(),
        )
        embed.add_field(name="📊 Coverage", value="\n".join(lines)[:1024], inline=False)
        embed.set_footer(text="/giftcode_missing <alliance> <code> lists who is still missing one")
        return embed

    # -- toggle -----------------------------------------------------------

    @app_commands.command(
        name="giftcode_digest",
        description="Turn the daily gift-code summary for an alliance on or off",
    )
    @app_commands.describe(alliance="Alliance", enabled="Post a daily summary?")
    @app_commands.autocomplete(alliance=_alliance_autocomplete)
    async def giftcode_digest(
        self, interaction: discord.Interaction, alliance: str, enabled: bool
    ):
        if not check_admin(interaction.user.id):
            await interaction.response.send_message("You do not have permission.", ephemeral=True)
            return
        db = DatabaseManager.instance().get("giftcode")
        db.execute(
            "INSERT INTO giftcode_digest (alliance_id, enabled) VALUES (?, ?) "
            "ON CONFLICT(alliance_id) DO UPDATE SET enabled = ?",
            (alliance, int(enabled), int(enabled)),
        )
        db.commit()
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(
            f"✅ Daily gift-code summary **{state}** for this alliance.", ephemeral=True
        )

    @app_commands.command(
        name="giftcode_report",
        description="Which gift codes an alliance redeemed, and who is still missing them",
    )
    @app_commands.describe(
        alliance="Alliance to report on (leave empty for all)",
        code="Limit the report to a single gift code",
    )
    @app_commands.autocomplete(alliance=_alliance_autocomplete)
    async def giftcode_report(
        self,
        interaction: discord.Interaction,
        alliance: str | None = None,
        code: str | None = None,
    ):
        if not check_admin(interaction.user.id):
            await interaction.response.send_message("You do not have permission.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        names = _alliance_names()
        members = _members_by_alliance()
        redemptions = _redemptions()
        codes = [code] if code else _known_codes()

        if not codes:
            await interaction.followup.send("No gift codes recorded yet.")
            return

        alliances = [alliance] if alliance else sorted(members.keys())
        embeds = []

        for aid in alliances:
            roster = members.get(str(aid), {})
            if not roster:
                continue

            embed = discord.Embed(
                title=f"🎁 Gift Code Report — {names.get(str(aid), f'Alliance {aid}')}",
                description=f"👥 {len(roster)} members · 🎫 {len(codes)} codes tracked",
                color=discord.Color.gold(),
            )

            lines, untouched = [], []
            for giftcode in codes[:25]:
                got = [fid for fid in roster if redemptions.get(fid, {}).get(giftcode) in REDEEMED]
                failed = [
                    fid for fid in roster
                    if fid in redemptions
                    and giftcode in redemptions[fid]
                    and redemptions[fid][giftcode] not in REDEEMED
                ]
                pending = len(roster) - len(got) - len(failed)

                if not got and not failed:
                    untouched.append(giftcode)
                    continue

                share = round(100 * len(got) / len(roster)) if roster else 0
                mark = "✅" if pending == 0 and not failed else ("⚠️" if failed else "⏳")
                detail = f"{mark} `{giftcode}` — **{len(got)}/{len(roster)}** ({share}%)"
                if failed:
                    detail += f" · ❌ {len(failed)} failed"
                if pending:
                    detail += f" · ⏳ {pending} pending"
                lines.append(detail)

            if lines:
                # Discord caps a field at 1024 characters, so split across fields.
                chunk, size = [], 0
                part = 1
                for line in lines:
                    if size + len(line) + 1 > 1000:
                        embed.add_field(
                            name=f"📊 Redemptions ({part})", value="\n".join(chunk), inline=False
                        )
                        chunk, size, part = [], 0, part + 1
                    chunk.append(line)
                    size += len(line) + 1
                if chunk:
                    embed.add_field(
                        name=f"📊 Redemptions ({part})" if part > 1 else "📊 Redemptions",
                        value="\n".join(chunk),
                        inline=False,
                    )
            else:
                embed.add_field(
                    name="📊 Redemptions", value="No redemptions recorded.", inline=False
                )

            if untouched:
                shown = ", ".join(f"`{c}`" for c in untouched[:10])
                if len(untouched) > 10:
                    shown += f" … +{len(untouched) - 10}"
                embed.add_field(name="🚫 Never attempted", value=shown, inline=False)

            embeds.append(embed)

        if not embeds:
            await interaction.followup.send("No members found for that alliance.")
            return

        logger.info(
            "GIFTCODE_REPORT admin=%s alliance=%s codes=%s",
            interaction.user.id, alliance or "all", len(codes),
        )
        # Discord allows at most 10 embeds per message.
        await interaction.followup.send(embeds=embeds[:10])
        for extra in range(10, len(embeds), 10):
            await interaction.followup.send(embeds=embeds[extra:extra + 10])

    @app_commands.command(
        name="giftcode_missing",
        description="List members of an alliance who did not redeem a specific code",
    )
    @app_commands.describe(alliance="Alliance", code="Gift code to check")
    @app_commands.autocomplete(alliance=_alliance_autocomplete)
    async def giftcode_missing(
        self, interaction: discord.Interaction, alliance: str, code: str
    ):
        if not check_admin(interaction.user.id):
            await interaction.response.send_message("You do not have permission.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        roster = _members_by_alliance().get(str(alliance), {})
        if not roster:
            await interaction.followup.send("No members found for that alliance.")
            return

        redemptions = _redemptions()
        missing, failed = [], []
        for fid, nickname in roster.items():
            status = redemptions.get(fid, {}).get(code)
            if status in REDEEMED:
                continue
            if status:
                failed.append(f"❌ **{nickname}** (`{fid}`) — {status}")
            else:
                missing.append(f"⏳ **{nickname}** (`{fid}`)")

        names = _alliance_names()
        embed = discord.Embed(
            title=f"🎁 `{code}` — {names.get(str(alliance), alliance)}",
            description=(
                f"✅ {len(roster) - len(missing) - len(failed)}/{len(roster)} redeemed"
            ),
            color=discord.Color.orange() if (missing or failed) else discord.Color.green(),
        )
        for title, entries in (("❌ Failed", failed), ("⏳ Not attempted", missing)):
            if entries:
                shown = "\n".join(entries[:20])
                if len(entries) > 20:
                    shown += f"\n… and {len(entries) - 20} more"
                embed.add_field(name=f"{title} ({len(entries)})", value=shown[:1024], inline=False)
        if not missing and not failed:
            embed.add_field(name="📊 Result", value="Everyone has this code.", inline=False)

        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(GiftCodeReport(bot))
