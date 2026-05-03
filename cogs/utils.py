import discord
import asyncio
from typing import List

from .database import DatabaseManager
from .log_config import get_logger

logger = get_logger("utils")


# ---------------------------------------------------------------------------
# Monitored task helper
# ---------------------------------------------------------------------------

def _create_monitored_task(coro, name=None):
    task = asyncio.create_task(coro, name=name)
    def _on_done(t):
        if not t.cancelled() and t.exception():
            logger.error("[TaskError] %s: %s", t.get_name(), t.exception())
    task.add_done_callback(_on_done)
    return task


# ---------------------------------------------------------------------------
# RTL text fix
# ---------------------------------------------------------------------------

def fix_rtl(text):
    return f"\u202B{text}\u202C"


# ---------------------------------------------------------------------------
# Error feedback helper
# ---------------------------------------------------------------------------

async def _send_embed(interaction, embed, ephemeral=True):
    """Internal helper to send an embed handling response state."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
    except Exception as e:
        logger.debug("Could not send embed: %s", e)


async def send_error(interaction, message="An error occurred.", title="\u274c Error"):
    """Send a standardized error response to the user."""
    embed = discord.Embed(title=title, description=message, color=discord.Color.red())
    await _send_embed(interaction, embed)


async def send_success(interaction, message, title="\u2705 Success"):
    """Send a standardized success response to the user."""
    embed = discord.Embed(title=title, description=message, color=discord.Color.green())
    await _send_embed(interaction, embed)


async def send_info(interaction, message, title="\u2139\ufe0f Info"):
    """Send a standardized info response to the user."""
    embed = discord.Embed(title=title, description=message, color=discord.Color.blue())
    await _send_embed(interaction, embed)


# ---------------------------------------------------------------------------
# Embed builder
# ---------------------------------------------------------------------------

_SEP = "\u2501" * 22  # ━━━━━━━━━━━━━━━━━━━━━━


def build_embed(title: str, fields: dict[str, str], *, color=None, header: str | None = None, footer: str | None = None) -> discord.Embed:
    """Build a standardized embed with key-value fields and separators.

    Args:
        title:  Embed title (e.g. "✅ Member Added")
        fields: Dict of {emoji_label: value} pairs shown as **label:** `value`
        color:  discord.Color (auto-detected from title emoji if None)
        header: Optional header text before the fields
        footer: Optional footer text

    Example::

        build_embed("✅ Gift Code Created", {
            "🎁 Gift Code": code,
            "✅ Status": "Successfully created",
        })
    """
    if color is None:
        if title.startswith("\u2705") or title.startswith("\u2714"):
            color = discord.Color.green()
        elif title.startswith("\u274c"):
            color = discord.Color.red()
        elif title.startswith("\u26a0"):
            color = discord.Color.orange()
        else:
            color = discord.Color.blue()

    lines = []
    if header:
        lines.append(f"**{header}**")
    lines.append(_SEP)
    for label, value in fields.items():
        lines.append(f"**{label}:** `{value}`")
    lines.append(_SEP)

    embed = discord.Embed(title=title, description="\n".join(lines), color=color)
    if footer:
        embed.set_footer(text=footer)
    return embed


# ---------------------------------------------------------------------------
# Input validation helper
# ---------------------------------------------------------------------------

def safe_int(value, default=None):
    """Safely convert to int, returning default on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Admin check helpers
# ---------------------------------------------------------------------------

def check_admin(user_id: int) -> bool:
    """Check if user is any admin (regular or initial)."""
    conn = DatabaseManager.instance().get("settings")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM admin WHERE id = ?", (user_id,))
    return cursor.fetchone() is not None


def check_global_admin(user_id: int) -> bool:
    """Check if user is a global/initial admin (is_initial = 1)."""
    conn = DatabaseManager.instance().get("settings")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM admin WHERE id = ? AND is_initial = 1", (user_id,))
    return cursor.fetchone() is not None


def get_admin_info(user_id: int) -> tuple | None:
    """Return (id, is_initial) tuple or None if user is not an admin."""
    conn = DatabaseManager.instance().get("settings")
    cursor = conn.cursor()
    cursor.execute("SELECT id, is_initial FROM admin WHERE id = ?", (user_id,))
    return cursor.fetchone()


def get_global_admin_ids() -> list[int]:
    """Return list of all global (is_initial=1) admin user IDs."""
    conn = DatabaseManager.instance().get("settings")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM admin WHERE is_initial = 1")
    return [row[0] for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# PaginationView
# ---------------------------------------------------------------------------

class PaginationView(discord.ui.View):
    def __init__(self, chunks: List[discord.Embed], author_id: int):
        super().__init__(timeout=180.0)
        if not chunks:
            chunks = [discord.Embed(title="No results", description="Nothing to display.")]
        self.chunks = chunks
        self.current_page = 0
        self.message = None
        self.author_id = author_id
        self.update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("You cannot use these buttons.", ephemeral=True)
            return False
        return True

    @discord.ui.button(emoji="\u2b05\ufe0f", style=discord.ButtonStyle.blurple, disabled=True)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_page_change(interaction, -1)

    @discord.ui.button(emoji="\u27a1\ufe0f", style=discord.ButtonStyle.blurple)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_page_change(interaction, 1)

    async def _handle_page_change(self, interaction: discord.Interaction, change: int):
        self.current_page = max(0, min(self.current_page + change, len(self.chunks) - 1))
        self.update_buttons()
        await self.update_page(interaction)

    def update_buttons(self):
        self.previous_page.disabled = self.current_page == 0
        self.next_page.disabled = self.current_page == len(self.chunks) - 1

    async def update_page(self, interaction: discord.Interaction):
        embed = self.chunks[self.current_page]
        embed.set_footer(text=f"Page {self.current_page + 1}/{len(self.chunks)}")
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# ---------------------------------------------------------------------------
# AllianceSelectView
# ---------------------------------------------------------------------------

class AllianceSelectView(discord.ui.View):
    def __init__(self, alliances_with_counts, cog=None, page=0):
        super().__init__(timeout=180)
        self.alliances = alliances_with_counts
        self.cog = cog
        self.page = page
        self.max_page = (len(alliances_with_counts) - 1) // 25 if alliances_with_counts else 0
        self.current_select = None
        self.callback = None
        self.member_dict = {}
        self.selected_alliance_id = None
        self.update_select_menu()

    def update_select_menu(self):
        for item in self.children[:]:
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)

        start_idx = self.page * 25
        end_idx = min(start_idx + 25, len(self.alliances))
        current_alliances = self.alliances[start_idx:end_idx]

        select = discord.ui.Select(
            placeholder=f"\U0001f3f0 Select an alliance... (Page {self.page + 1}/{self.max_page + 1})",
            options=[
                discord.SelectOption(
                    label=f"{name[:50]}",
                    value=str(alliance_id),
                    description=f"ID: {alliance_id} | Members: {count}",
                    emoji="\U0001f3f0"
                ) for alliance_id, name, count in current_alliances
            ]
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

    @discord.ui.button(label="\u25c0\ufe0f", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.update_select_menu()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="\u25b6\ufe0f", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.max_page, self.page + 1)
        self.update_select_menu()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Select by FID", emoji="\U0001f50d", style=discord.ButtonStyle.secondary)
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
            logger.error(f"FID button error: {e}")
            await interaction.response.send_message(
                "\u274c An error has occurred. Please try again.",
                ephemeral=True
            )


class FIDSearchModal(discord.ui.Modal):
    def __init__(self, selected_alliance_id=None, alliances=None, callback=None):
        super().__init__(title="Search Members with FID")
        self.selected_alliance_id = selected_alliance_id
        self.alliances = alliances
        self.callback = callback

        self.add_item(discord.ui.TextInput(
            label="Member ID",
            placeholder="Example: 12345",
            min_length=1,
            max_length=20,
            required=True
        ))

    async def on_submit(self, interaction: discord.Interaction):
        try:
            fid = self.children[0].value.strip()


            users_db = DatabaseManager.instance().get("users")
            cursor = users_db.cursor()
            cursor.execute("""
                SELECT fid, nickname, furnace_lv, alliance
                FROM users
                WHERE fid = ?
            """, (fid,))
            user_result = cursor.fetchone()

            if not user_result:
                await interaction.response.send_message(
                    "\u274c No member with this FID was found.",
                    ephemeral=True
                )
                return

            fid, nickname, furnace_lv, current_alliance_id = user_result


            alliance_db = DatabaseManager.instance().get("alliance")
            cursor = alliance_db.cursor()
            cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (current_alliance_id,))
            row = cursor.fetchone()
            current_alliance_name = row[0] if row else "Unknown"


            embed = discord.Embed(
                title="\u2705 Member Found - Transfer Process",
                description=(
                    f"**Member Information:**\n"
                    f"\U0001f464 **Name:** {nickname}\n"
                    f"\U0001f194 **FID:** {fid}\n"
                    f"\u2694\ufe0f **Level:** {furnace_lv}\n"
                    f"\U0001f3f0 **Current Alliance:** {current_alliance_name}\n\n"
                    "**Transfer Process**\n"
                    "Please select the alliance you want to transfer the member to:"
                ),
                color=discord.Color.blue()
            )


            # Filter out current alliance and limit to 25 options (Discord max)
            filtered_alliances = [
                (alliance_id, name, count)
                for alliance_id, name, count in self.alliances
                if alliance_id != current_alliance_id
            ][:25]

            select = discord.ui.Select(
                placeholder="\U0001f3af Choose the target alliance...",
                options=[
                    discord.SelectOption(
                        label=f"{name[:50]}",
                        value=str(alliance_id),
                        description=f"ID: {alliance_id}",
                        emoji="\U0001f3f0"
                    ) for alliance_id, name, _ in filtered_alliances
                ]
            )

            view = discord.ui.View()
            view.add_item(select)

            async def select_callback(select_interaction: discord.Interaction):
                target_alliance_id = int(select.values[0])

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
                        (target_alliance_id, fid)
                    )
                    users_db.commit()


                    success_embed = discord.Embed(
                        title="\u2705 Transfer Successful",
                        description=(
                            f"\U0001f464 **Member:** {nickname}\n"
                            f"\U0001f194 **FID:** {fid}\n"
                            f"\U0001f4e4 **Source:** {current_alliance_name}\n"
                            f"\U0001f4e5 **Target:** {target_alliance_name}"
                        ),
                        color=discord.Color.green()
                    )

                    await select_interaction.response.edit_message(
                        embed=success_embed,
                        view=None
                    )

                except Exception as e:
                    logger.error(f"Transfer error: {e}")
                    error_embed = discord.Embed(
                        title="\u274c Error",
                        description="An error occurred during the transfer operation.",
                        color=discord.Color.red()
                    )
                    await select_interaction.response.edit_message(
                        embed=error_embed,
                        view=None
                    )

            select.callback = select_callback
            await interaction.response.send_message(
                embed=embed,
                view=view,
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"FID search error: {e}")
            logger.error("Error details: %s", e.__class__.__name__)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "\u274c An error has occurred. Please try again.",
                    ephemeral=True
                )


# ---------------------------------------------------------------------------
# PaginatedChannelView
# ---------------------------------------------------------------------------

class PaginatedChannelView(discord.ui.View):
    def __init__(self, channels, original_callback):
        super().__init__(timeout=300)
        self.current_page = 0
        self.channels = channels
        self.original_callback = original_callback
        self.items_per_page = 25
        self.pages = [channels[i:i + self.items_per_page] for i in range(0, len(channels), self.items_per_page)]
        self.total_pages = len(self.pages)
        self.update_view()

    def update_view(self):
        self.clear_items()

        current_channels = self.pages[self.current_page]
        channel_options = [
            discord.SelectOption(
                label=channel.name[:40],
                value=str(channel.id),
                description=f"Channel ID: {channel.id}" if len(channel.name) > 40 else None,
                emoji="\U0001f4e2"
            ) for channel in current_channels
        ]

        select = discord.ui.Select(
            placeholder=f"Select channel ({self.current_page + 1}/{self.total_pages})",
            options=channel_options
        )
        select.callback = self.original_callback
        self.add_item(select)

        if self.total_pages > 1:
            previous_button = discord.ui.Button(
                label="\u25c0\ufe0f",
                style=discord.ButtonStyle.grey,
                custom_id="previous",
                disabled=(self.current_page == 0)
            )
            previous_button.callback = self.previous_callback
            self.add_item(previous_button)

            next_button = discord.ui.Button(
                label="\u25b6\ufe0f",
                style=discord.ButtonStyle.grey,
                custom_id="next",
                disabled=(self.current_page == len(self.pages) - 1)
            )
            next_button.callback = self.next_callback
            self.add_item(next_button)

    async def previous_callback(self, interaction: discord.Interaction):
        self.current_page = (self.current_page - 1) % len(self.pages)
        self.update_view()

        embed = interaction.message.embeds[0]
        embed.description = (
            f"**Page:** {self.current_page + 1}/{self.total_pages}\n"
            f"**Total Channels:** {len(self.channels)}\n\n"
            "Please select a channel from the menu below."
        )

        await interaction.response.edit_message(embed=embed, view=self)

    async def next_callback(self, interaction: discord.Interaction):
        self.current_page = (self.current_page + 1) % len(self.pages)
        self.update_view()

        embed = interaction.message.embeds[0]
        embed.description = (
            f"**Page:** {self.current_page + 1}/{self.total_pages}\n"
            f"**Total Channels:** {len(self.channels)}\n\n"
            "Please select a channel from the menu below."
        )

        await interaction.response.edit_message(embed=embed, view=self)
