"""Add a member from a profile screenshot using offline OCR (Apple Vision).

Since 2026-07 there is no API that returns a WOS player's nickname/furnace from
a FID (see project_api_change_2026_07). This command lets an admin upload a
profile screenshot; the bot OCRs it on-device (offline, free, no API key),
pre-fills the fields, and the admin confirms/edits before saving. The kingdom
is auto-detected via the gift-code oracle when left blank.
"""

import os
import tempfile

import discord
from discord import app_commands
from discord.ext import commands

from .config import LEVEL_MAPPING
from .database import DatabaseManager
from .log_config import get_logger
from .utils import check_admin, build_embed
from .wos_api import resolve_kingdom
from . import screenshot_ocr

logger = get_logger("screenshot_add")


def _furnace_display(furnace_lv: int) -> str:
    if isinstance(furnace_lv, int) and furnace_lv > 30:
        return LEVEL_MAPPING.get(furnace_lv, f"Level {furnace_lv}")
    return f"Level {furnace_lv}"


class ScreenshotEditModal(discord.ui.Modal):
    """Pre-filled, editable confirmation of the OCR result before saving."""

    def __init__(self, alliance_id, parsed: dict):
        super().__init__(title="Confirm Member Details")
        self.alliance_id = alliance_id
        self.nickname = discord.ui.TextInput(
            label="Nickname", default=(parsed.get("nickname") or ""), max_length=100)
        self.fid = discord.ui.TextInput(
            label="FID (player ID)", default=(parsed.get("fid") or ""))
        self.region = discord.ui.TextInput(
            label="Region (kingdom id) — blank = auto-detect",
            required=False, default=(parsed.get("kid") or ""))
        self.furnace = discord.ui.TextInput(
            label="Furnace level (number, optional)",
            required=False,
            default=(str(parsed.get("furnace")) if parsed.get("furnace") else ""))
        for item in (self.nickname, self.fid, self.region, self.furnace):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        fid = self.fid.value.strip()
        if not fid.isdigit():
            await interaction.followup.send("FID must be numeric.", ephemeral=True)
            return

        nickname = self.nickname.value.strip() or str(fid)
        kid = self.region.value.strip()
        furnace_raw = self.furnace.value.strip()
        # accept a bare number (e.g. 30) or FCx (fire-crystal) style; store the
        # numeric level when possible, else 0.
        furnace_lv = int(furnace_raw) if furnace_raw.isdigit() else 0

        users_db = DatabaseManager.instance().get("users")
        cur = users_db.cursor()
        cur.execute("SELECT alliance FROM users WHERE fid=?", (fid,))
        if cur.fetchone():
            await interaction.followup.send(f"FID {fid} is already registered.", ephemeral=True)
            return

        if not kid:
            common = [str(r[0]) for r in users_db.execute(
                "SELECT kid FROM users WHERE alliance=? AND kid IS NOT NULL AND kid!='' "
                "GROUP BY kid ORDER BY COUNT(*) DESC", (self.alliance_id,)).fetchall()]
            allk = [str(r[0]) for r in users_db.execute(
                "SELECT DISTINCT kid FROM users WHERE kid IS NOT NULL AND kid!=''").fetchall()]
            cand = list(dict.fromkeys([*common, *allk]))
            kid = await resolve_kingdom(fid, cand) if cand else None

        if not kid:
            await interaction.followup.send(
                f"Could not determine the region for FID {fid}. Please re-run and enter it manually.",
                ephemeral=True)
            return

        cur.execute(
            "INSERT INTO users (fid, nickname, furnace_lv, kid, stove_lv_content, alliance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (fid, nickname, furnace_lv, kid, None, self.alliance_id))
        users_db.commit()
        logger.info("SCREENSHOT_ADD admin=%s fid=%s nickname=%s kid=%s alliance=%s",
                    interaction.user.id, fid, nickname, kid, self.alliance_id)

        embed = build_embed("✅ Member Added (from screenshot)", {
            "👤 Name": nickname,
            "🆔 FID": str(fid),
            "🔥 Furnace Level": _furnace_display(furnace_lv),
            "🌍 State": str(kid),
        })
        await interaction.followup.send(embed=embed, ephemeral=True)


class ScreenshotConfirmView(discord.ui.View):
    def __init__(self, alliance_id, parsed: dict):
        super().__init__(timeout=300)
        self.alliance_id = alliance_id
        self.parsed = parsed

    @discord.ui.button(label="Confirm / Edit", emoji="✏️", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ScreenshotEditModal(self.alliance_id, self.parsed))


class ScreenshotAdd(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="add_screenshot",
        description="Add a member from a profile screenshot (offline OCR)")
    @app_commands.describe(
        alliance="Alliance to add the member to",
        screenshot="The player's in-game profile screenshot")
    async def add_screenshot(self, interaction: discord.Interaction,
                             alliance: str, screenshot: discord.Attachment):
        if not check_admin(interaction.user.id):
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True)
            return
        if not screenshot.content_type or not screenshot.content_type.startswith("image/"):
            await interaction.response.send_message("Please attach an image.", ephemeral=True)
            return
        if not screenshot_ocr.available():
            await interaction.response.send_message(
                "OCR helper is not available on this host (bin/ocr_vision missing).", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        alliance_id = int(alliance) if str(alliance).isdigit() else alliance

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "shot.png")
            await screenshot.save(path)
            lines = await screenshot_ocr.ocr_lines(path)
        parsed = screenshot_ocr.parse_profile(lines)

        preview = build_embed("🔍 Screenshot Read — Confirm", {
            "👤 Nickname": parsed.get("nickname") or "—",
            "🆔 FID": parsed.get("fid") or "—",
            "🌍 Region": parsed.get("kid") or "auto-detect",
            "🔥 Furnace": parsed.get("furnace") or "—",
        }, footer="Click Confirm / Edit to review the values and save")
        await interaction.followup.send(
            embed=preview, view=ScreenshotConfirmView(alliance_id, parsed), ephemeral=True)

    @add_screenshot.autocomplete("alliance")
    async def _alliance_autocomplete(self, interaction: discord.Interaction, current: str):
        try:
            db = DatabaseManager.instance().get("alliance")
            rows = db.execute("SELECT alliance_id, name FROM alliance_list").fetchall()
        except Exception:
            return []
        choices = []
        for aid, name in rows:
            if current.lower() in (name or "").lower() or current in str(aid):
                choices.append(app_commands.Choice(name=f"{name} ({aid})", value=str(aid)))
        return choices[:25]


class InactiveMembersView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Reactivate All", emoji="♻️", style=discord.ButtonStyle.success)
    async def reactivate(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_admin(interaction.user.id):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return
        users = DatabaseManager.instance().get("users")
        n = users.execute("UPDATE users SET inactive=0, resolve_fail_count=0 WHERE COALESCE(inactive,0)=1").rowcount
        users.commit()
        logger.info("REACTIVATE_ALL admin=%s count=%s", interaction.user.id, n)
        await interaction.response.edit_message(
            content=f"♻️ Reactivated {n} member(s). They'll be retried next gift cycle.",
            embed=None, view=None)


class ScreenshotAddInactive(commands.Cog):
    """Report + reactivate members flagged inactive by the gift retry loop."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="inactive_members",
        description="List members flagged inactive (unresolvable via the game API) and reactivate them")
    async def inactive_members(self, interaction: discord.Interaction):
        if not check_admin(interaction.user.id):
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True)
            return
        users = DatabaseManager.instance().get("users")
        try:
            rows = users.execute(
                "SELECT fid, nickname, kid, alliance FROM users "
                "WHERE COALESCE(inactive,0)=1 ORDER BY nickname").fetchall()
        except Exception:
            rows = []
        if not rows:
            await interaction.response.send_message("No inactive members. 🎉", ephemeral=True)
            return
        shown = rows[:25]
        lines = "\n".join(f"`{fid}` {nick} — kid {kid}, alliance {a}" for fid, nick, kid, a in shown)
        if len(rows) > len(shown):
            lines += f"\n… and {len(rows) - len(shown)} more"
        embed = build_embed(f"💤 Inactive Members ({len(rows)})", {"Flagged unresolvable": lines},
                            footer="Moved to an untracked kingdom or account gone — skipped in gift redemption")
        await interaction.response.send_message(embed=embed, view=InactiveMembersView(), ephemeral=True)


async def setup(bot):
    await bot.add_cog(ScreenshotAdd(bot))
    await bot.add_cog(ScreenshotAddInactive(bot))
