"""Per-alliance region (kingdom) management.

Lets admins define which kingdoms an alliance spans and pick a default. The
default pre-fills the region when adding members — necessary because the WOS
API no longer exposes a player's kingdom from a FID (see
project_api_change_2026_07), so it must be supplied.
"""

import discord
from discord import app_commands
from discord.ext import commands

from .database import DatabaseManager
from .log_config import get_logger
from .utils import check_admin, build_embed

logger = get_logger("regions")


def _db():
    return DatabaseManager.instance().get("alliance")


def ensure_schema():
    db = _db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS alliance_regions (
            alliance_id INTEGER,
            kid TEXT,
            is_default INTEGER DEFAULT 0,
            PRIMARY KEY (alliance_id, kid)
        )
    """)
    db.commit()


def get_default_region(alliance_id) -> str | None:
    """The alliance's default kingdom id (str), or None."""
    try:
        row = _db().execute(
            "SELECT kid FROM alliance_regions WHERE alliance_id=? AND is_default=1 LIMIT 1",
            (alliance_id,)).fetchone()
        return str(row[0]) if row else None
    except Exception:
        return None


def seed_from_members():
    """One-time: populate regions from existing member kingdoms for any
    alliance that has members but no regions yet. Most-common kingdom becomes
    the default. Safe to call on every startup — it skips already-seeded
    alliances and never overrides an existing configuration."""
    try:
        adb = _db()
        udb = DatabaseManager.instance().get("users")
        alliances = adb.execute("SELECT alliance_id FROM alliance_list").fetchall()
    except Exception as e:
        logger.debug("region seed skipped: %s", e)
        return
    for (aid,) in alliances:
        try:
            if adb.execute("SELECT COUNT(*) FROM alliance_regions WHERE alliance_id=?", (aid,)).fetchone()[0]:
                continue
            kids = udb.execute(
                "SELECT kid, COUNT(*) c FROM users WHERE alliance=? AND kid IS NOT NULL AND kid!='' "
                "GROUP BY kid ORDER BY c DESC", (str(aid),)).fetchall()
            for i, (kid, _c) in enumerate(kids):
                adb.execute("INSERT OR IGNORE INTO alliance_regions (alliance_id, kid, is_default) VALUES (?,?,?)",
                            (aid, str(kid), 1 if i == 0 else 0))
            if kids:
                adb.commit()
                logger.info("Seeded %d regions for alliance %s (default=%s)", len(kids), aid, kids[0][0])
        except Exception as e:
            logger.debug("region seed for alliance %s failed: %s", aid, e)


def get_regions(alliance_id) -> list:
    """(kid, is_default) for an alliance, default first."""
    try:
        return [(str(k), d) for k, d in _db().execute(
            "SELECT kid, is_default FROM alliance_regions WHERE alliance_id=? "
            "ORDER BY is_default DESC, kid", (alliance_id,)).fetchall()]
    except Exception:
        return []


async def _alliance_autocomplete(interaction: discord.Interaction, current: str):
    try:
        rows = _db().execute("SELECT alliance_id, name FROM alliance_list").fetchall()
    except Exception:
        return []
    out = []
    for aid, name in rows:
        if current.lower() in (name or "").lower() or current in str(aid):
            out.append(app_commands.Choice(name=f"{name} ({aid})", value=str(aid)))
    return out[:25]


def _norm_region(region: str) -> str:
    return (region or "").strip().lstrip("#")


# ----------------------------------------------------------------------
# Interactive region management (button/menu UI, used from the alliance menu)
# ----------------------------------------------------------------------

class AddRegionModal(discord.ui.Modal):
    def __init__(self, mgr_view):
        super().__init__(title="Add Region")
        self.mgr_view = mgr_view
        self.kid_input = discord.ui.TextInput(label="Kingdom id", placeholder="e.g. 1587", max_length=6)
        self.add_item(self.kid_input)

    async def on_submit(self, interaction: discord.Interaction):
        region = _norm_region(self.kid_input.value)
        if not region.isdigit():
            await interaction.response.send_message("Region must be a number (e.g. 1587).", ephemeral=True)
            return
        db = _db()
        count = db.execute("SELECT COUNT(*) FROM alliance_regions WHERE alliance_id=?",
                           (self.mgr_view.alliance_id,)).fetchone()[0]
        db.execute("INSERT OR IGNORE INTO alliance_regions (alliance_id, kid, is_default) VALUES (?,?,?)",
                   (self.mgr_view.alliance_id, region, 1 if count == 0 else 0))
        db.commit()
        await self.mgr_view.refresh(interaction)


class _AddRegionButton(discord.ui.Button):
    def __init__(self, mgr_view):
        super().__init__(label="Add Region", emoji="➕", style=discord.ButtonStyle.success, row=0)
        self.mgr_view = mgr_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AddRegionModal(self.mgr_view))


class _DefaultSelect(discord.ui.Select):
    def __init__(self, mgr_view, regs):
        options = [discord.SelectOption(label=k, value=k, default=bool(d)) for k, d in regs][:25]
        super().__init__(placeholder="⭐ Set default region…", options=options, row=1)
        self.mgr_view = mgr_view

    async def callback(self, interaction: discord.Interaction):
        kid = self.values[0]
        db = _db()
        db.execute("UPDATE alliance_regions SET is_default=0 WHERE alliance_id=?", (self.mgr_view.alliance_id,))
        db.execute("UPDATE alliance_regions SET is_default=1 WHERE alliance_id=? AND kid=?",
                   (self.mgr_view.alliance_id, kid))
        db.commit()
        await self.mgr_view.refresh(interaction)


class _RemoveSelect(discord.ui.Select):
    def __init__(self, mgr_view, regs):
        options = [discord.SelectOption(label=k, value=k) for k, d in regs][:25]
        super().__init__(placeholder="🗑️ Remove region…", options=options, row=2)
        self.mgr_view = mgr_view

    async def callback(self, interaction: discord.Interaction):
        kid = self.values[0]
        db = _db()
        was = db.execute("SELECT is_default FROM alliance_regions WHERE alliance_id=? AND kid=?",
                         (self.mgr_view.alliance_id, kid)).fetchone()
        db.execute("DELETE FROM alliance_regions WHERE alliance_id=? AND kid=?", (self.mgr_view.alliance_id, kid))
        if was and was[0]:
            nxt = db.execute("SELECT kid FROM alliance_regions WHERE alliance_id=? ORDER BY kid LIMIT 1",
                             (self.mgr_view.alliance_id,)).fetchone()
            if nxt:
                db.execute("UPDATE alliance_regions SET is_default=1 WHERE alliance_id=? AND kid=?",
                           (self.mgr_view.alliance_id, nxt[0]))
        db.commit()
        await self.mgr_view.refresh(interaction)


class RegionManageView(discord.ui.View):
    """Add/remove/set-default regions for one alliance, self-refreshing."""

    def __init__(self, alliance_id, alliance_name):
        super().__init__(timeout=300)
        self.alliance_id = alliance_id
        self.alliance_name = alliance_name
        self._sync()

    def _sync(self):
        self.clear_items()
        self.add_item(_AddRegionButton(self))
        regs = get_regions(self.alliance_id)
        if regs:
            self.add_item(_DefaultSelect(self, regs))
            self.add_item(_RemoveSelect(self, regs))

    def embed(self):
        regs = get_regions(self.alliance_id)
        lines = "\n".join(f"{'⭐' if d else '•'} `{k}`" + ("  (default)" if d else "") for k, d in regs) or "— none yet —"
        return build_embed(f"🌍 Regions — {self.alliance_name}", {"Kingdoms": lines},
                           footer="⭐ default pre-fills the region when adding members")

    async def refresh(self, interaction: discord.Interaction):
        self._sync()
        await interaction.response.edit_message(embed=self.embed(), view=self)


class Regions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        ensure_schema()
        seed_from_members()

    @app_commands.command(name="region_add", description="Add a region (kingdom id) to an alliance")
    @app_commands.describe(alliance="Alliance", region="Kingdom id, e.g. 1587")
    @app_commands.autocomplete(alliance=_alliance_autocomplete)
    async def region_add(self, interaction: discord.Interaction, alliance: str, region: str):
        if not check_admin(interaction.user.id):
            await interaction.response.send_message("You do not have permission.", ephemeral=True)
            return
        aid = int(alliance) if str(alliance).isdigit() else alliance
        region = _norm_region(region)
        if not region.isdigit():
            await interaction.response.send_message("Region must be a number (e.g. 1587).", ephemeral=True)
            return
        db = _db()
        count = db.execute("SELECT COUNT(*) FROM alliance_regions WHERE alliance_id=?", (aid,)).fetchone()[0]
        db.execute(
            "INSERT OR IGNORE INTO alliance_regions (alliance_id, kid, is_default) VALUES (?,?,?)",
            (aid, region, 1 if count == 0 else 0))
        db.commit()
        note = " and set as default (first region)" if count == 0 else ""
        logger.info("REGION_ADD admin=%s alliance=%s kid=%s", interaction.user.id, aid, region)
        await interaction.response.send_message(f"✅ Region `{region}` added{note}.", ephemeral=True)

    @app_commands.command(name="region_remove", description="Remove a region from an alliance")
    @app_commands.describe(alliance="Alliance", region="Kingdom id to remove")
    @app_commands.autocomplete(alliance=_alliance_autocomplete)
    async def region_remove(self, interaction: discord.Interaction, alliance: str, region: str):
        if not check_admin(interaction.user.id):
            await interaction.response.send_message("You do not have permission.", ephemeral=True)
            return
        aid = int(alliance) if str(alliance).isdigit() else alliance
        region = _norm_region(region)
        db = _db()
        was_default = db.execute(
            "SELECT is_default FROM alliance_regions WHERE alliance_id=? AND kid=?", (aid, region)).fetchone()
        if not was_default:
            await interaction.response.send_message(f"Region `{region}` is not set for this alliance.", ephemeral=True)
            return
        db.execute("DELETE FROM alliance_regions WHERE alliance_id=? AND kid=?", (aid, region))
        # If we removed the default, promote another region to default.
        if was_default[0]:
            nxt = db.execute(
                "SELECT kid FROM alliance_regions WHERE alliance_id=? ORDER BY kid LIMIT 1", (aid,)).fetchone()
            if nxt:
                db.execute("UPDATE alliance_regions SET is_default=1 WHERE alliance_id=? AND kid=?", (aid, nxt[0]))
        db.commit()
        logger.info("REGION_REMOVE admin=%s alliance=%s kid=%s", interaction.user.id, aid, region)
        await interaction.response.send_message(f"🗑️ Region `{region}` removed.", ephemeral=True)

    @app_commands.command(name="region_default", description="Set an alliance's default region")
    @app_commands.describe(alliance="Alliance", region="Kingdom id to make default")
    @app_commands.autocomplete(alliance=_alliance_autocomplete)
    async def region_default(self, interaction: discord.Interaction, alliance: str, region: str):
        if not check_admin(interaction.user.id):
            await interaction.response.send_message("You do not have permission.", ephemeral=True)
            return
        aid = int(alliance) if str(alliance).isdigit() else alliance
        region = _norm_region(region)
        db = _db()
        # Auto-add the region if it isn't there yet, then make it the sole default.
        db.execute("INSERT OR IGNORE INTO alliance_regions (alliance_id, kid, is_default) VALUES (?,?,0)", (aid, region))
        db.execute("UPDATE alliance_regions SET is_default=0 WHERE alliance_id=?", (aid,))
        db.execute("UPDATE alliance_regions SET is_default=1 WHERE alliance_id=? AND kid=?", (aid, region))
        db.commit()
        logger.info("REGION_DEFAULT admin=%s alliance=%s kid=%s", interaction.user.id, aid, region)
        await interaction.response.send_message(f"⭐ Default region set to `{region}`.", ephemeral=True)

    @app_commands.command(name="region_list", description="List an alliance's regions")
    @app_commands.describe(alliance="Alliance")
    @app_commands.autocomplete(alliance=_alliance_autocomplete)
    async def region_list(self, interaction: discord.Interaction, alliance: str):
        if not check_admin(interaction.user.id):
            await interaction.response.send_message("You do not have permission.", ephemeral=True)
            return
        aid = int(alliance) if str(alliance).isdigit() else alliance
        regions = get_regions(aid)
        if not regions:
            await interaction.response.send_message(
                "No regions configured for this alliance yet. Add one with `/region_add`.", ephemeral=True)
            return
        lines = "\n".join(f"{'⭐' if d else '•'} `{k}`" + ("  (default)" if d else "") for k, d in regions)
        embed = build_embed("🌍 Alliance Regions", {"Kingdoms": lines},
                            footer="⭐ default — pre-fills the region when adding members")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Regions(bot))
