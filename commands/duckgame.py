

"""Duck Game commands and views.
Single-message UI, bank/wallet accounting, and CSPRNG hazard logic.
"""

EARN_AMOUNT = 20_000.0
COOLDOWN_SECONDS = 3600.0

import discord
from discord.ui import View, Button
from discord.ext import commands
from discord.ext.commands import Cog
from discord.ext.commands import Context
from utils.image_generator import generate_duck_game_image, upload_frame_and_get_url
from utils.rng import get_secure_hazard

import io
import json
import os
import inspect
import asyncio
import time

# --- Admin role check helper -------------------------------------------------
def _has_admin_role(member: discord.Member) -> bool:
    return any(role.name.lower() == "admin" for role in getattr(member, "roles", []))

# --- Money formatting ---------------------------------------------------------
def fmt(amount: float) -> str:
    try:
        return f"${float(amount):,.2f}"
    except Exception:
        return f"${amount}"

def fmt_delta(after: float, before: float) -> str:
    try:
        delta = float(after) - float(before)
        sign = "+" if delta >= 0 else "-"
        return f"({sign}{abs(delta):,.2f})"
    except Exception:
        return ""

# --- Colored delta formatting -------------------------------------------------
def fmt_delta_colored(after: float, before: float) -> str:
    try:
        delta = float(after) - float(before)
        if delta >= 0:
            return f"🟢 (+${delta:,.2f})"
        else:
            return f"🔴 (-${abs(delta):,.2f})"
    except Exception:
        return ""

# --- Interaction ACK helper --------------------------------------------------
async def _ack(interaction: discord.Interaction):
    """Acknowledge the interaction quickly to avoid 'This interaction failed'.
    Prefer defer_update() for component interactions (buttons); fallback to defer().
    Safe to call more than once.
    """
    try:
        if not interaction.response.is_done():
            # Buttons come through as component interactions; update avoids the pending UI state
            if hasattr(interaction.response, 'defer_update'):
                await interaction.response.defer_update()
            else:
                await interaction.response.defer()
    except Exception:
        # If it's already acknowledged or network hiccup, ignore
        pass

# --- Single-message edit helper ---------------------------------------------
async def _edit_message(msg: discord.Message, **kwargs):
    """Edit a message in place.
    Tries to replace attachments; if the library doesn't support passing files
    during edit on this environment, gracefully falls back to editing only
    text/components so the interaction doesn't crash.
    """
    files = kwargs.pop("files", None)
    try:
        if files is not None:
            try:
                # Some py-cord builds accept attachments=[discord.File, ...]
                await msg.edit(attachments=files, **kwargs)
                return
            except TypeError:
                # Fallback: edit without attachments (keeps existing image)
                pass
        await msg.edit(**kwargs)
    except Exception:
        # Last-resort: swallow to prevent "This interaction failed" banners
        pass

# --- Helper: upload PIL image to CDN and edit message with embed ------------
async def _swap_with_embed(bot: commands.Bot, msg: discord.Message, pil_img, filename: str, content: str, view: discord.ui.View | None):
    """
    Upload PIL image to a CDN channel and edit `msg` with an embed that shows it.
    This avoids passing attachments to Message.edit (which breaks on some py-cord builds).
    Requires IMAGE_CDN_CHANNEL_ID env var and proper channel permissions.
    """
    try:
        url, _ = await upload_frame_and_get_url(bot, pil_img, filename)
        embed = discord.Embed()
        embed.set_image(url=url)
        await msg.edit(content=content, embed=embed, view=view)
    except Exception:
        # As a last resort, try to at least update text/view so the UI doesn't hang.
        try:
            await msg.edit(content=content, view=view)
        except Exception:
            pass

#
# Global async lock for bank.json to prevent race conditions across commands
# Create the lock lazily to bind it to the active loop (prevents macOS loop errors)
BANK_LOCK: asyncio.Lock | None = None
async def _get_bank_lock() -> asyncio.Lock:
    global BANK_LOCK
    if BANK_LOCK is None:
        BANK_LOCK = asyncio.Lock()
    return BANK_LOCK


def _atomic_write_json(path: str, data: dict):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=4)
    # os.replace is atomic on POSIX; it overwrites the target
    os.replace(tmp_path, path)


def load_bank():
    if not os.path.exists("data/bank.json"):
        return {}
    try:
        with open("data/bank.json", "r") as f:
            data = json.load(f)
    except Exception:
        # If file is corrupt/empty, fall back to empty
        return {}
    # Ensure all users have wallet, bank, game_active, last_earn_ts keys
    for user_id, balances in list(data.items()):
        if not isinstance(balances, dict):
            data[user_id] = {
                "wallet": float(balances),
                "bank": 0.0,
                "game_active": False,
                "last_earn_ts": 0.0,
                "wins": 0,
                "losses": 0,
                "last_rob_ts": 0.0,
            }
        else:
            balances.setdefault("wallet", 1000.0)
            balances.setdefault("bank", 0.0)
            balances.setdefault("game_active", False)
            balances.setdefault("last_earn_ts", 0.0)
            balances.setdefault("wins", 0)
            balances.setdefault("losses", 0)
            balances.setdefault("last_rob_ts", 0.0)
    return data


def update_bank(data):
    # Atomic write to avoid corruption on crashes
    os.makedirs("data", exist_ok=True)
    _atomic_write_json("data/bank.json", data)


# -------------------- MODE SELECTION VIEW --------------------
class ModeSelectView(View):
    """Mode picker for Easy/Medium/Hard; transitions to the live game view."""

    def __init__(self, user, amount, wallet_after, username):
        super().__init__(timeout=None)
        self.user = user
        self.amount = float(amount)
        self.wallet_after = wallet_after
        self.wallet_before = float(wallet_after) + float(amount)
        self.username = username
        self.started = False  # prevent multiple sessions from the same mode panel
        self.live_message: discord.Message | None = None

        # mode_name -> (lanes, multipliers)
        self.modes = {
            "Easy": (7, [1.00, 1.05, 1.15, 1.80, 2.10, 2.15, 2.30]),
            "Medium": (5, [1.05, 1.25, 1.70, 2.00, 2.40]),
            "Hard": (3, [1.50, 2.25, 3.00]),
        }

        self.easy_btn = Button(label="Easy", style=discord.ButtonStyle.success)
        self.med_btn = Button(label="Medium", style=discord.ButtonStyle.primary)
        self.hard_btn = Button(label="Hard", style=discord.ButtonStyle.danger)

        self.easy_btn.callback = self._choose_easy
        self.med_btn.callback = self._choose_med
        self.hard_btn.callback = self._choose_hard

        self.add_item(self.easy_btn)
        self.add_item(self.med_btn)
        self.add_item(self.hard_btn)

    async def _launch_mode(self, interaction: discord.Interaction, label: str):
        await _ack(interaction)
        # Prevent duplicate session creation if the user double-clicks
        if self.started:
            # If someone tries again after start, politely tell them
            if interaction.user.id == self.user.id:
                if not interaction.response.is_done():
                    await interaction.response.send_message("This game has already started.", ephemeral=True)
                else:
                    await interaction.followup.send("This game has already started.", ephemeral=True)
            else:
                if not interaction.response.is_done():
                    await interaction.response.send_message("You cannot choose a mode for someone else's game.", ephemeral=True)
                else:
                    await interaction.followup.send("You cannot choose a mode for someone else's game.", ephemeral=True)
            return

        if interaction.user.id != self.user.id:
            if not interaction.response.is_done():
                await interaction.response.send_message("You cannot choose a mode for someone else's game.", ephemeral=True)
            else:
                await interaction.followup.send("You cannot choose a mode for someone else's game.", ephemeral=True)
            return

        # Mark this view as started so additional clicks do nothing
        self.started = True

        # Disable all three mode buttons before any awaits
        for item in self.children:
            try:
                item.disabled = True
            except Exception:
                pass
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

        total_lanes, multipliers = self.modes[label]
        # Exclude the last regular lane from hazards; finish (lane N) is always clear
        hazard_pos = get_secure_hazard(total_lanes - 1)
        view = DuckGameView(
            user=self.user,
            amount=self.amount,
            wallet_before=self.wallet_before,
            wallet_after=self.wallet_after,
            multiplier=1.0,
            username=self.username,
            multipliers=multipliers,
            total_lanes=total_lanes,
            hazard_pos=hazard_pos,
            live_message=interaction.message,
        )

        # Initial board: duck in grass (-1), no hazard shown yet
        image = generate_duck_game_image(-1, -1, [], total_slots=total_lanes)

        # Use the selector message as the live game message; no extra sends/deletes
        msg = interaction.message
        view.live_message = msg
        self.live_message = msg

        remaining_text = ", ".join([f"x{m:.2f}" for m in multipliers])
        content = (
            f"🎮 Player: {self.username}\n"
            f"🦆 Mode selected: **{label}** ({total_lanes} lanes)\n"
            f"Current Winnings: {fmt(self.amount)} | Multiplier: x1.00\n"
            f"Remaining Multipliers: {remaining_text}\n"
            f"💼 Wallet before bet: {fmt(self.wallet_before)} | After bet: {fmt(self.wallet_after)}"
        )
        await _swap_with_embed(
            interaction.client,
            msg,
            image,
            "mode_start.png",
            content,
            view,
        )

    async def _choose_easy(self, interaction: discord.Interaction):
        await self._launch_mode(interaction, "Easy")

    async def _choose_med(self, interaction: discord.Interaction):
        await self._launch_mode(interaction, "Medium")

    async def _choose_hard(self, interaction: discord.Interaction):
        await self._launch_mode(interaction, "Hard")


# -------------------- GAME VIEW --------------------
class DuckGameView(View):
    """Live game view with Forward/Stop; edits a single message in place."""
    def __init__(
        self,
        user,
        amount,
        wallet_before,
        wallet_after,
        multiplier,
        username,
        multipliers,
        total_lanes,
        hazard_pos,
        start_position: int = -1,
        live_message: discord.Message | None = None,
    ):
        super().__init__(timeout=None)
        self.user = user
        self.amount = float(amount)
        self.position = start_position  # Start position (default grass = -1)
        self.hazard_pos = hazard_pos

        self.wallet_before = float(wallet_before)
        self.wallet_after = float(wallet_after)
        self.multiplier = float(multiplier)
        self.username = username
        self.multipliers = multipliers
        self.total_lanes = total_lanes
        self.live_message = live_message
        self.ended = False  # prevent double payout or multiple endings

        # Compute session winnings correctly based on current position/multiplier.
        # If this view was rebuilt while already on a lane, sync to that lane's multiplier.
        if self.position >= 0:
            if 0 <= self.position < len(self.multipliers):
                self.multiplier = float(self.multipliers[self.position])
            self.session_wallet = float(self.amount) * float(self.multiplier)
        else:
            # In grass, stake equals current session value (x1.0)
            self.session_wallet = float(self.amount)

        # Controls: show only Forward while on grass (-1). Add Stop once on lanes.
        self.forward_button = Button(label="Forward", style=discord.ButtonStyle.success)
        self.forward_button.callback = self.forward_button_callback
        self.add_item(self.forward_button)

        self.stop_button = Button(label="Stop", style=discord.ButtonStyle.danger)
        self.stop_button.callback = self.stop_button_callback
        if self.position >= 0:
            self.add_item(self.stop_button)

    def _disable_view(self):
        for item in self.children:
            try:
                item.disabled = True
            except Exception:
                pass

    async def _freeze_message(self, interaction: discord.Interaction):
        """Disable buttons on the current message to prevent double-clicks."""
        self._disable_view()
        try:
            target = self.live_message or interaction.message
            await _edit_message(target, view=self)
        except Exception:
            pass

    async def forward_button_callback(self, interaction: discord.Interaction):
        await _ack(interaction)
        if self.ended:
            # Already ended; ignore duplicate clicks gracefully
            if not interaction.response.is_done():
                await interaction.response.send_message("This game is already finished.", ephemeral=True)
            else:
                await interaction.followup.send("This game is already finished.", ephemeral=True)
            return
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("You cannot control this game.", ephemeral=True)
            return
        # Disable buttons on the old message to avoid duplicate clicks
        await self._freeze_message(interaction)

        # Step forward
        self.position += 1

        # Update multiplier/winnings for a valid lane index
        if 0 <= self.position < len(self.multipliers):
            self.multiplier = self.multipliers[self.position]
            self.session_wallet = self.amount * self.multiplier

        # If we moved past last playable lane, place duck on finish and pay out
        if self.position > self.total_lanes - 1:
            # ensure final multiplier is applied on finish
            if self.multipliers:
                self.multiplier = float(self.multipliers[-1])
            self.session_wallet = float(self.amount) * self.multiplier
            self.ended = True

            image = generate_duck_game_image(self.total_lanes, -1, [], total_slots=self.total_lanes)

            before_wallet = float(self.wallet_before)
            after_wallet  = before_wallet - float(self.amount) + float(self.session_wallet)

            lock = await _get_bank_lock()
            async with lock:
                bank_data = load_bank()
                user_id = str(self.user.id)
                bank_data.setdefault(user_id, {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0, "wins": 0, "losses": 0, "last_rob_ts": 0.0})

                # Credit winnings and mark win exactly once
                bank_data[user_id]["wallet"] = after_wallet
                bank_data[user_id]["game_active"] = False
                bank_data[user_id]["wins"] = int(bank_data[user_id].get("wins", 0)) + 1
                update_bank(bank_data)

                bank = float(bank_data[user_id]["bank"])

            content = (
                f"🎮 Player: {self.username}\n"
                f"🏁 You reached the finish!\n"
                f"Final Winnings: {fmt(self.session_wallet)} | Final Multiplier: x{self.multiplier:.2f}\n"
                f"💼 Wallet before bet: {fmt(before_wallet)} | After result: {fmt(after_wallet)} "
                f"{fmt_delta_colored(after_wallet, before_wallet)} | 🏦 Bank: {fmt(bank)}"
            )
            await _swap_with_embed(
                interaction.client,
                self.live_message or interaction.message,
                image,
                "finish.png",
                content,
                None,
            )
            interaction.client.get_cog("DuckGame").active_sessions.discard(self.user.id)
            return

        # Crash if we hit the hazard lane
        if self.position == self.hazard_pos:
            image = generate_duck_game_image(self.position, self.hazard_pos, [], total_slots=self.total_lanes)

            before_wallet = float(self.wallet_before)
            after_wallet  = before_wallet - float(self.amount)

            lock = await _get_bank_lock()
            async with lock:
                bank_data = load_bank()
                user_id = str(self.user.id)
                bank_data.setdefault(user_id, {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0, "wins": 0, "losses": 0, "last_rob_ts": 0.0})

                # Record loss exactly once and clear session
                bank_data[user_id]["wallet"] = after_wallet
                bank_data[user_id]["game_active"] = False
                bank_data[user_id]["losses"] = int(bank_data[user_id].get("losses", 0)) + 1
                update_bank(bank_data)

                bank = float(bank_data[user_id]["bank"])

            self.session_wallet = 0.0
            self.ended = True

            remaining = self.multipliers[self.position + 1:]
            remain_txt = ", ".join([f"x{m:.2f}" for m in remaining]) if remaining else "None"

            content = (
                f"🎮 Player: {self.username}\n"
                f"💥 The duck got hit by a car! You lost your stake.\n"
                f"Current Winnings: {fmt(self.session_wallet)} | Multiplier: x{self.multiplier:.2f}\n"
                f"💼 Wallet before bet: {fmt(before_wallet)} | After result: {fmt(after_wallet)} "
                f"{fmt_delta_colored(after_wallet, before_wallet)} | 🏦 Bank: {fmt(bank)}\n"
                f"Remaining Multipliers: {remain_txt}"
            )
            await _swap_with_embed(
                interaction.client,
                self.live_message or interaction.message,
                image,
                "crash.png",
                content,
                None,
            )
            interaction.client.get_cog("DuckGame").active_sessions.discard(self.user.id)
            return

        # Safe move within lanes
        image = generate_duck_game_image(self.position, -1, [], total_slots=self.total_lanes)

        # Show the post-bet wallet we already computed at start, and correct delta vs before
        before_wallet = float(self.wallet_before)
        after_bet_wallet = float(self.wallet_after)

        # Bank value (purely for display)
        bank_data = load_bank()
        bank = float(bank_data.get(str(self.user.id), {}).get("bank", 0.0))

        remaining = self.multipliers[self.position + 1:]
        remain_txt = ", ".join([f"x{m:.2f}" for m in remaining]) if remaining else "None"

        new_view = DuckGameView(
            user=self.user,
            amount=self.amount,
            wallet_before=self.wallet_before,
            wallet_after=self.wallet_after,
            multiplier=self.multiplier,
            username=self.username,
            multipliers=self.multipliers,
            total_lanes=self.total_lanes,
            hazard_pos=self.hazard_pos,
            start_position=self.position,
            live_message=self.live_message or interaction.message,
        )

        content = (
            f"🎮 Player: {self.username}\n"
            f"🦆 The duck moved forward safely!\n"
            f"Current Winnings: {fmt(self.session_wallet)} | Multiplier: x{self.multiplier:.2f}\n"
            f"💼 Wallet before bet: {fmt(before_wallet)} | After bet: {fmt(after_bet_wallet)} "
            f"{fmt_delta_colored(after_bet_wallet, before_wallet)} | 🏦 Bank: {fmt(bank)}\n"
            f"Remaining Multipliers: {remain_txt}"
        )
        await _swap_with_embed(
            interaction.client,
            self.live_message or interaction.message,
            image,
            "safe.png",
            content,
            new_view,
        )
        new_view.live_message = self.live_message or interaction.message

    async def stop_button_callback(self, interaction: discord.Interaction):
        await _ack(interaction)
        if self.ended:
            if not interaction.response.is_done():
                await interaction.response.send_message("This game is already finished.", ephemeral=True)
            else:
                await interaction.followup.send("This game is already finished.", ephemeral=True)
            return
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("You cannot control this game.", ephemeral=True)
            return
        # Disable buttons on the old message to avoid duplicate clicks
        await self._freeze_message(interaction)
        self.ended = True

        # Ensure we apply the multiplier for the lane we're currently on.
        if 0 <= self.position < len(self.multipliers):
            self.multiplier = float(self.multipliers[self.position])
        self.session_wallet = float(self.amount) * float(self.multiplier)

        before_wallet = float(self.wallet_before)
        after_wallet  = before_wallet - float(self.amount) + float(self.session_wallet)

        lock = await _get_bank_lock()
        async with lock:
            bank_data = load_bank()
            user_id = str(self.user.id)
            bank_data.setdefault(user_id, {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0, "wins": 0, "losses": 0, "last_rob_ts": 0.0})

            bank_data[user_id]["wallet"] = after_wallet
            bank_data[user_id]["wins"] = int(bank_data[user_id].get("wins", 0)) + 1
            bank_data[user_id]["game_active"] = False
            update_bank(bank_data)

            bank = float(bank_data[user_id]["bank"])

        self.clear_items()
        image = generate_duck_game_image(self.position, -1, [], total_slots=self.total_lanes)

        remaining = self.multipliers[self.position + 1:]
        remain_txt = ", ".join([f"x{m:.2f}" for m in remaining]) if remaining else "None"

        content = (
            f"🎮 Player: {self.username}\n"
            f"💰 You stopped the game and cashed out!\n"
            f"Final Winnings: {fmt(self.session_wallet)} | Final Multiplier: x{self.multiplier:.2f}\n"
            f"💼 Wallet before bet: {fmt(before_wallet)} | After result: {fmt(after_wallet)} "
            f"{fmt_delta_colored(after_wallet, before_wallet)} | 🏦 Bank: {fmt(bank)}\n"
            f"Remaining Multipliers: {remain_txt}"
        )
        await _swap_with_embed(
            interaction.client,
            self.live_message or interaction.message,
            image,
            "cashout.png",
            content,
            None,
        )
        interaction.client.get_cog("DuckGame").active_sessions.discard(self.user.id)


# -------------------- LEADERBOARD PAGINATION VIEW --------------------
class LeaderboardView(View):
    def __init__(self, ctx: Context, pages: list[list[tuple[int, int, str]]]):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.pages = pages
        self.index = 0
        self.prev_btn = Button(label="Prev", style=discord.ButtonStyle.secondary)
        self.next_btn = Button(label="Next", style=discord.ButtonStyle.secondary)
        self.prev_btn.callback = self._prev
        self.next_btn.callback = self._next
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        self._sync_buttons()

    def _sync_buttons(self):
        self.prev_btn.disabled = self.index <= 0
        self.next_btn.disabled = self.index >= (len(self.pages) - 1)

    def _make_embed(self, guild: discord.Guild | None) -> discord.Embed:
        entries = self.pages[self.index]
        start_rank = self.index * 10 + 1
        lines = []
        for i, (wins, losses, uid) in enumerate(entries, start=start_rank):
            member = guild.get_member(int(uid)) if guild else None
            name = member.name if member else f"User {uid}"
            lines.append(f"**{i}.** {name} — 🏆 {wins} wins | 💀 {losses} losses")
        total_pages = max(1, len(self.pages))
        embed = discord.Embed(
            title=f"🧮 Duck Game Leaderboard (Page {self.index+1}/{total_pages})",
            description="\n".join(lines) if lines else "No stats yet.",
            color=discord.Color.gold(),
        )
        return embed

    async def _prev(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only the command invoker can change pages.", ephemeral=True)
            return
        await _ack(interaction)
        if self.index > 0:
            self.index -= 1
            self._sync_buttons()
            await _edit_message(interaction.message, embed=self._make_embed(self.ctx.guild), view=self)

    async def _next(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only the command invoker can change pages.", ephemeral=True)
            return
        await _ack(interaction)
        if self.index < len(self.pages) - 1:
            self.index += 1
            self._sync_buttons()
            await _edit_message(interaction.message, embed=self._make_embed(self.ctx.guild), view=self)

# -------------------- COG --------------------
class DuckGame(Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_sessions = set()

    @commands.command(name="testduck", help="Generate a sample Duck Game board image for testing.")
    async def testduck_command(self, ctx: Context, lanes: int = 5):
        """Quick sanity check: renders a board with the duck in grass and no hazards.
        Usage: !testduck [lanes]
        """
        try:
            image = generate_duck_game_image(-1, -1, [], total_slots=int(lanes))
            buf = io.BytesIO(); image.save(buf, format="PNG"); buf.seek(0)
            await ctx.send("🧪 Test board generated.", file=discord.File(buf, filename="test_board.png"))
        except Exception as e:
            await ctx.send(f"❌ Failed to generate test board: {e}")

    @commands.command(name="testimage", help="Render a custom test board. Usage: !testimage [lanes] [pos] [hazard]")
    async def testimage_command(self, ctx: Context, lanes: int = 5, pos: int = -1, hazard: int = -1):
        """
        Render a board with custom lane count, duck position, and hazard index.
        lanes: total playable lanes (>=1)
        pos: duck position (-1 for grass, 0..lanes for finish)
        hazard: hazard lane index (-1 for none, 0..lanes-1 for a car on that lane)
        """
        try:
            lanes = max(1, int(lanes))
            # clamp pos into [-1, lanes] so finish is allowed
            pos = max(-1, min(int(pos), lanes))
            # clamp hazard into [-1, lanes-1]
            hazard = int(hazard)
            if hazard < -1 or hazard > lanes - 1:
                hazard = -1

            image = generate_duck_game_image(pos, hazard, [], total_slots=lanes)
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            buf.seek(0)
            await ctx.send(
                f"🧪 Test image generated. lanes={lanes}, pos={pos}, hazard={hazard}",
                file=discord.File(buf, filename="test_board.png"),
            )
        except Exception as e:
            await ctx.send(f"❌ Failed to generate test image: {e}")

    @commands.command(name="testwin", help="DEV: Credit winnings to your wallet to verify accounting. Usage: !testwin <amount> <multiplier>")
    async def testwin_command(self, ctx: Context, amount: float, multiplier: float):
        """
        DEV utility: Adds amount*multiplier to your wallet and shows before/after.
        This does NOT require/affect an active game session; it's for verifying wallet crediting.
        """
        try:
            amount = float(amount)
            multiplier = float(multiplier)
            if amount <= 0 or multiplier <= 0:
                await ctx.send("❌ Amount and multiplier must be greater than 0.")
                return
        except Exception:
            await ctx.send("❌ Usage: !testwin <amount> <multiplier>. Example: !testwin 100 2.4")
            return

        lock = await _get_bank_lock()
        async with lock:
            data = load_bank()
            uid = str(ctx.author.id)
            rec = data.setdefault(uid, {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0, "wins": 0, "losses": 0, "last_rob_ts": 0.0})
            before = float(rec.get("wallet", 0.0))
            winnings = float(amount) * float(multiplier)
            rec["wallet"] = before + winnings
            update_bank(data)
            after = rec["wallet"]

        await ctx.send(
            f"🧪 Test win credited to {ctx.author.name}\n"
            f"Amount: {fmt(amount)} × x{multiplier:.2f} = **{fmt(winnings)}**\n"
            f"Wallet before: {fmt(before)} → after: {fmt(after)} {fmt_delta_colored(after, before)}"
        )

    @commands.command(name="ping", help="Bot latency check.")
    async def ping_command(self, ctx: Context):
        await ctx.send(f"🏓 Pong! Latency: {round(self.bot.latency*1000)}ms")

    @commands.command(name="duck", help="Start the Duck Game (choose a mode). Usage: !duck [amount|A|H]")
    async def duck_command(self, ctx: Context, amount: str):
        bank_data = load_bank()
        user_id = str(ctx.author.id)
        if user_id not in bank_data:
            bank_data[user_id] = {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0}
        if bank_data[user_id].get("game_active", False):
            await ctx.send("❌ You already have an active game session.")
            return

        user_wallet = bank_data[user_id].get("wallet", 1000.0)

        if amount.lower() == "a":
            amount = user_wallet
        elif amount.lower() == "h":
            amount = user_wallet / 2
        else:
            try:
                amount = float(amount)
                if amount <= 0:
                    await ctx.send("❌ Bet must be greater than 0.")
                    return
            except ValueError:
                await ctx.send("❌ Invalid bet amount. Please enter a number, or use 'A' for all or 'H' for half.")
                return

        if amount <= 0:
            await ctx.send("❌ Bet must be greater than 0.")
            return

        if amount > user_wallet:
            await ctx.send("❌ You don't have enough funds to bet that amount.")
            return

        # Deduct stake and mark session active atomically
        lock = await _get_bank_lock()
        async with lock:
            fresh = load_bank()
            # ensure record exists
            fresh.setdefault(user_id, {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0, "wins": 0, "losses": 0, "last_rob_ts": 0.0})
            if float(fresh[user_id]["wallet"]) < float(amount):
                await ctx.send("❌ You no longer have enough funds to place that bet.")
                return
            fresh[user_id]["wallet"] = float(fresh[user_id]["wallet"]) - float(amount)
            fresh[user_id]["game_active"] = True
            update_bank(fresh)
            bank_data = fresh  # use updated snapshot

        self.active_sessions.add(ctx.author.id)

        user_wallet = bank_data[user_id].get("wallet", 0.0)  # updated after deduction
        bank = bank_data[user_id].get("bank", 0.0)
        view = ModeSelectView(ctx.author, amount, user_wallet, ctx.author.name)

        easy = ", ".join([f"x{m:.2f}" for m in view.modes["Easy"][1]])
        med = ", ".join([f"x{m:.2f}" for m in view.modes["Medium"][1]])
        hard = ", ".join([f"x{m:.2f}" for m in view.modes["Hard"][1]])
        await ctx.send(
            f"🎮 Player: {ctx.author.name}\n"
            f"🦆 Bet: {fmt(amount)}\n"
            f"Choose a mode to begin:\n"
            f"• **Easy** (7 lanes): {easy}\n"
            f"• **Medium** (5 lanes): {med}\n"
            f"• **Hard** (3 lanes): {hard}\n\n"
            f"💼 Wallet: {fmt(user_wallet)} | 🏦 Bank: {fmt(bank)}",
            view=view,
        )

    @commands.command(name="release_me", description="Release yourself from a stuck game session.")
    async def self_release_command(self, ctx: Context):
        lock = await _get_bank_lock()
        async with lock:
            bank_data = load_bank()
            user_id = str(ctx.author.id)
            if user_id in bank_data:
                bank_data[user_id]["game_active"] = False
                update_bank(bank_data)
        self.active_sessions.discard(ctx.author.id)
        await ctx.send("✅ You have been released from any stuck game session.")

    @commands.command(name="helpduck", description="Show Duck Game bot commands.")
    async def help_command(self, ctx: Context):
        help_text = (
            "**🦆 Duck Game Bot Commands:**\n"
            "`!duck [amount|A|H]` - Start the Duck Game with a bet and choose a mode.\n"
            "`!balance` - Check your wallet and bank balance.\n"
            "`!deposit [amount|A|H]` - Deposit funds into your bank.\n"
            "`!withdraw [amount|A|H]` - Withdraw funds from your bank.\n"
            "`!release_me` - Release yourself if the game is stuck.\n"
            "`!sendmoney @user amount` - Send money to another user.\n"
            "`!earn` - Earn $20,000 with a 1-hour cooldown.\n"
            "`!rob @user` - Try to rob another player (50% lose 5% fine, 50% steal up to 10%; 1h30m cooldown).\n"
            "`!leaderboard` or `!lb` - Show top players with pagination (10 per page).\n"
            "`!mystats` - Show your personal wins, losses, and win rate.\n"
            "Tip: For amounts, you can use `A` for all and `H` for half of your wallet/bank.\n"
        )
        await ctx.send(help_text)

    @commands.command(name="leaderboard", aliases=["lb"], description="Show top players by wins and losses.")
    async def leaderboard_command(self, ctx: Context):
        data = load_bank()
        if not data:
            await ctx.send("No stats yet.")
            return

        entries: list[tuple[int, int, str]] = []
        for uid, rec in data.items():
            if not isinstance(rec, dict):
                continue
            wins = int(rec.get("wins", 0))
            losses = int(rec.get("losses", 0))
            if wins > 0 or losses > 0:
                entries.append((wins, losses, uid))

        if not entries:
            await ctx.send("No stats yet.")
            return

        entries.sort(key=lambda t: (-t[0], t[1], int(t[2])))
        pages = [entries[i:i+10] for i in range(0, len(entries), 10)]

        view = LeaderboardView(ctx, pages)
        embed = view._make_embed(ctx.guild)
        await ctx.send(embed=embed, view=view)

    @commands.command(name="mystats", description="Show your personal stats (wins, losses, win rate).")
    async def mystats_command(self, ctx: Context):
        data = load_bank()
        user = data.get(str(ctx.author.id), {})
        wins = int(user.get("wins", 0))
        losses = int(user.get("losses", 0))
        games = wins + losses
        win_rate = (wins / games * 100.0) if games > 0 else 0.0
        embed = discord.Embed(
            title=f"📊 {ctx.author.name}'s Stats",
            description=(
                f"🏆 Wins: **{wins}**\n"
                f"💀 Losses: **{losses}**\n"
                f"📈 Win Rate: **{win_rate:.1f}%** ({games} game{'s' if games!=1 else ''})"
            ),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)

    @commands.command(name="balance", description="Check your balance.")
    async def balance_command(self, ctx: Context):
        lock = await _get_bank_lock()
        async with lock:
            bank_data = load_bank()
            user_id = str(ctx.author.id)
            if user_id not in bank_data:
                bank_data[user_id] = {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0, "wins": 0, "losses": 0, "last_rob_ts": 0.0}
                update_bank(bank_data)
            wallet = bank_data[user_id].get("wallet", 1000.0)
            bank = bank_data[user_id].get("bank", 0.0)
        await ctx.send(
            f"💰 {ctx.author.name}, your current wallet balance is: {fmt(wallet)}\n"
            f"🏦 Your bank balance is: {fmt(bank)}"
        )

    @commands.command(name="sendmoney", description="Send money to another user.")
    async def sendmoney_command(self, ctx: Context, member: discord.Member, amount: float):
        if member == ctx.author:
            await ctx.send("❌ You cannot send money to yourself.")
            return

        if amount <= 0:
            await ctx.send("❌ Amount must be greater than 0.")
            return

        lock = await _get_bank_lock()
        async with lock:
            bank_data = load_bank()
            sender_id = str(ctx.author.id)
            receiver_id = str(member.id)

            bank_data.setdefault(sender_id, {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0, "wins": 0, "losses": 0, "last_rob_ts": 0.0})
            bank_data.setdefault(receiver_id, {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0, "wins": 0, "losses": 0, "last_rob_ts": 0.0})

            if bank_data[sender_id]["wallet"] < amount:
                await ctx.send("❌ You don't have enough funds to send that amount.")
                return

            bank_data[sender_id]["wallet"] -= amount
            bank_data[receiver_id]["wallet"] += amount
            update_bank(bank_data)

        await ctx.send(f"💸 {ctx.author.name} sent {fmt(amount)} to {member.name}.")

    @commands.command(name="earn", description="Earn $20,000 with a 1-hour cooldown.")
    async def earn_command(self, ctx: Context):
        lock = await _get_bank_lock()
        async with lock:
            bank_data = load_bank()
            user_id = str(ctx.author.id)
            user = bank_data.setdefault(
                user_id,
                {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0, "wins": 0, "losses": 0, "last_rob_ts": 0.0},
            )
            now = time.time()
            last_ts = float(user.get("last_earn_ts", 0.0))
            remaining = last_ts + COOLDOWN_SECONDS - now
            if remaining > 0:
                mins = int(remaining // 60)
                secs = int(remaining % 60)
                await ctx.send(f"⏳ You need to wait {mins}m {secs}s before using !earn again.")
                return

            user["wallet"] = float(user.get("wallet", 0.0)) + EARN_AMOUNT
            user["last_earn_ts"] = now
            update_bank(bank_data)

        await ctx.send(f"💰 {ctx.author.name}, you earned {fmt(EARN_AMOUNT)}! The money has been added to your wallet.")

    @commands.command(name="withdraw", description="Withdraw from your bank to your wallet.")
    async def withdraw_command(self, ctx: Context, amount: str):
        lock = await _get_bank_lock()
        async with lock:
            bank_data = load_bank()
            user_id = str(ctx.author.id)
            if user_id not in bank_data:
                bank_data[user_id] = {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0, "wins": 0, "losses": 0, "last_rob_ts": 0.0}

            bank_balance = bank_data[user_id]["bank"]
            if amount.lower() == "a":
                amount = bank_balance
            elif amount.lower() == "h":
                amount = bank_balance / 2
            else:
                try:
                    amount = float(amount)
                except ValueError:
                    await ctx.send("❌ Invalid amount. Use a number, 'A' for all, or 'H' for half.")
                    return

            if amount <= 0:
                await ctx.send("❌ Please enter a valid amount to withdraw.")
                return

            if amount > bank_balance:
                await ctx.send("❌ You do not have enough funds in your bank to withdraw that amount.")
                return

            bank_data[user_id]["bank"] -= amount
            bank_data[user_id]["wallet"] += amount
            update_bank(bank_data)

        await ctx.send(f"🏦 {ctx.author.name} withdrew {fmt(amount)} from their bank to their wallet.")
        await ctx.send(f"✅ Successfully withdrew {fmt(amount)} from your bank.")

    @commands.command(name="deposit", description="Deposit from your wallet to your bank.")
    async def deposit_command(self, ctx: Context, amount: str):
        lock = await _get_bank_lock()
        async with lock:
            bank_data = load_bank()
            user_id = str(ctx.author.id)
            if user_id not in bank_data:
                bank_data[user_id] = {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0, "wins": 0, "losses": 0, "last_rob_ts": 0.0}

            wallet_balance = bank_data[user_id]["wallet"]
            if amount.lower() == "a":
                amount = wallet_balance
            elif amount.lower() == "h":
                amount = wallet_balance / 2
            else:
                try:
                    amount = float(amount)
                except ValueError:
                    await ctx.send("❌ Invalid amount. Use a number, 'A' for all, or 'H' for half.")
                    return

            if amount <= 0:
                await ctx.send("❌ Please enter a valid amount to deposit.")
                return

            if amount > wallet_balance:
                await ctx.send("❌ You do not have enough funds in your wallet to deposit that amount.")
                return

            bank_data[user_id]["wallet"] -= amount
            bank_data[user_id]["bank"] += amount
            update_bank(bank_data)

        await ctx.send(f"🏦 {ctx.author.name} deposited {fmt(amount)} from their wallet to their bank.")

    @commands.command(name="setmoney", description="Admin command to set a user's wallet and/or bank.")
    async def set_money_command(self, ctx: Context, member: discord.Member, wallet: float = None, bank: float = None):
        if not _has_admin_role(ctx.author):
            await ctx.send("❌ You need the 'admin' role to use this command.")
            return
        lock = await _get_bank_lock()
        async with lock:
            bank_data = load_bank()
            user_id = str(member.id)
            if user_id not in bank_data:
                bank_data[user_id] = {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0, "wins": 0, "losses": 0, "last_rob_ts": 0.0}

            if wallet is not None:
                if wallet < 0:
                    await ctx.send("❌ Wallet cannot be negative.")
                    return
                bank_data[user_id]["wallet"] = float(wallet)
            if bank is not None:
                if bank < 0:
                    await ctx.send("❌ Bank cannot be negative.")
                    return
                bank_data[user_id]["bank"] = float(bank)

            update_bank(bank_data)
        await ctx.send(
            f"✅ Updated {member.name}'s balances. Wallet: {fmt(bank_data[user_id]['wallet'])}, Bank: {fmt(bank_data[user_id]['bank'])}"
        )

    @commands.command(name="release", description="Release a user's stuck game session.")
    async def release_command(self, ctx: Context, member_or_all: str):
        if not _has_admin_role(ctx.author):
            await ctx.send("❌ You need the 'admin' role to use this command.")
            return

        if member_or_all.lower() == "a":
            lock = await _get_bank_lock()
            async with lock:
                bank_data = load_bank()
                for user_id in bank_data:
                    bank_data[user_id]["game_active"] = False
                self.active_sessions.clear()
                update_bank(bank_data)
            await ctx.send("✅ Released all users from any stuck game sessions.")
        else:
            try:
                member = await commands.MemberConverter().convert(ctx, member_or_all)
            except commands.BadArgument:
                await ctx.send("❌ Invalid user mention or ID.")
                return

            lock = await _get_bank_lock()
            async with lock:
                bank_data = load_bank()
                user_id = str(member.id)
                if user_id in bank_data:
                    bank_data[user_id]["game_active"] = False
                    update_bank(bank_data)

            self.active_sessions.discard(member.id)
            await ctx.send(f"✅ Released {member.name} from any stuck game session.")

    @commands.command(name="rob", description="Attempt to rob someone with risk.")
    async def rob_command(self, ctx: Context, member: discord.Member):
        if member.bot:
            await ctx.send("❌ You can't rob bots.")
            return
        if member == ctx.author:
            await ctx.send("❌ You can't rob yourself.")
            return

        now = time.time()
        lock = await _get_bank_lock()
        async with lock:
            data = load_bank()
            robber_id = str(ctx.author.id)
            victim_id = str(member.id)
            # Ensure defaults
            for uid in (robber_id, victim_id):
                data.setdefault(uid, {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0, "wins": 0, "losses": 0, "last_rob_ts": 0.0})

            last = float(data[robber_id].get("last_rob_ts", 0.0))
            remaining = last + (90 * 60) - now
            if remaining > 0:
                mins = int(remaining // 60)
                secs = int(remaining % 60)
                await ctx.send(f"⏳ You need to wait {mins}m {secs}s before trying to rob again.")
                return

            import secrets
            success = bool(secrets.randbits(1))
            robber_wallet = float(data[robber_id]["wallet"])
            victim_wallet = float(data[victim_id]["wallet"])

            if success:
                if victim_wallet <= 0:
                    data[robber_id]["last_rob_ts"] = now
                    update_bank(data)
                    await ctx.send(f"😐 {member.name} has no money to steal.")
                    return
                steal_pct = (secrets.randbelow(10) + 1) / 100.0  # 1%..10%
                amount = max(1.0, victim_wallet * steal_pct)
                amount = min(amount, victim_wallet)
                data[victim_id]["wallet"] = victim_wallet - amount
                data[robber_id]["wallet"] = robber_wallet + amount
                data[robber_id]["last_rob_ts"] = now
                update_bank(data)
                await ctx.send(f"🕵️ {ctx.author.name} successfully robbed {member.name} for {fmt(amount)}!")
            else:
                if robber_wallet <= 0:
                    data[robber_id]["last_rob_ts"] = now
                    update_bank(data)
                    await ctx.send("🚓 You got caught, but you had no money to fine.")
                    return
                fine = max(1.0, robber_wallet * 0.05)  # 5%
                data[robber_id]["wallet"] = robber_wallet - fine
                data[robber_id]["last_rob_ts"] = now
                update_bank(data)
                await ctx.send(f"🚨 {ctx.author.name} was caught trying to rob {member.name} and was fined {fmt(fine)}!")


# Expose setup for bot integration
async def setup(bot):
    cog = DuckGame(bot)
    add_cog = getattr(bot, "add_cog")
    if inspect.iscoroutinefunction(add_cog):
        await add_cog(cog)
    else:
        add_cog(cog)