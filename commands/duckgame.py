

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
from utils.image_generator import generate_duck_game_image
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
    """Edit a message in place. Supports attachments via `attachments=[discord.File(...)]`."""
    files = kwargs.pop("files", None)
    if files:
        kwargs["attachments"] = files
    await msg.edit(**kwargs)

# Global async lock for bank.json to prevent race conditions across commands
BANK_LOCK = asyncio.Lock()


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
            }
        else:
            balances.setdefault("wallet", 1000.0)
            balances.setdefault("bank", 0.0)
            balances.setdefault("game_active", False)
            balances.setdefault("last_earn_ts", 0.0)
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
            "Easy": (7, [1.10, 1.20, 1.35, 1.50, 1.70, 2.00, 2.40]),
            "Medium": (5, [1.20, 1.50, 1.70, 2.00, 2.40]),
            "Hard": (3, [1.50, 2.00, 3.00]),
        }

        self.easy_btn = Button(label="Easy", style=discord.ButtonStyle.primary)
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
        buffer = io.BytesIO(); image.save(buffer, format="PNG"); buffer.seek(0)
        file = discord.File(fp=buffer, filename="mode_start.png")

        # Use the selector message as the live game message; no extra sends/deletes
        msg = interaction.message
        view.live_message = msg
        self.live_message = msg

        remaining_text = ", ".join([f"x{m:.2f}" for m in multipliers])
        await _edit_message(
            msg,
            content=(
                f"🎮 Player: {self.username}\n"
                f"🦆 Mode selected: **{label}** ({total_lanes} lanes)\n"
                f"Current Winnings: {fmt(self.amount)} | Multiplier: x1.00\n"
                f"Remaining Multipliers: {remaining_text}\n"
                f"💼 Wallet before bet: {fmt(self.wallet_before)} | After bet: {fmt(self.wallet_after)}"
            ),
            files=[file],
            view=view,
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
        self.session_wallet = self.amount  # session winnings seed (x1.0 at start); this is the staked amount
        self.wallet_before = float(wallet_before)
        self.wallet_after = float(wallet_after)
        self.multiplier = multiplier
        self.username = username
        self.multipliers = multipliers
        self.total_lanes = total_lanes
        self.live_message = live_message
        self.ended = False  # prevent double payout or multiple endings

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
            buffer = io.BytesIO(); image.save(buffer, format="PNG"); buffer.seek(0)
            file = discord.File(fp=buffer, filename="finish.png")
            async with BANK_LOCK:
                bank_data = load_bank()
                user_id = str(self.user.id)
                bank_data.setdefault(user_id, {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0})
                bank_data[user_id]["wallet"] += self.session_wallet
                bank_data[user_id]["game_active"] = False
                update_bank(bank_data)
            wallet = bank_data[user_id]["wallet"]; bank = bank_data[user_id]["bank"]
            await _edit_message(
                self.live_message or interaction.message,
                content=(
                    f"🎮 Player: {self.username}\n"
                    f"🏁 You reached the finish!\n"
                    f"Final Winnings: {fmt(self.session_wallet)} | Final Multiplier: x{self.multiplier:.2f}\n"
                    f"💼 Wallet before bet: {fmt(self.wallet_before)} | After result: {fmt(wallet)} {fmt_delta_colored(wallet, self.wallet_before)} | 🏦 Bank: {fmt(bank)}"
                ),
                files=[file],
                view=None,
            )
            interaction.client.get_cog("DuckGame").active_sessions.discard(self.user.id)
            return

        # Crash if we hit the hazard lane
        if self.position == self.hazard_pos:
            image = generate_duck_game_image(self.position, self.hazard_pos, [], total_slots=self.total_lanes)
            buffer = io.BytesIO(); image.save(buffer, format="PNG"); buffer.seek(0)
            file = discord.File(fp=buffer, filename="crash.png")
            async with BANK_LOCK:
                bank_data = load_bank()
                user_id = str(self.user.id)
                bank_data.setdefault(user_id, {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0})
                bank_data[user_id]["game_active"] = False
                update_bank(bank_data)
            wallet = bank_data[user_id]["wallet"]; bank = bank_data[user_id]["bank"]
            self.session_wallet = 0.0
            self.ended = True
            remaining = self.multipliers[self.position + 1:]
            remain_txt = ", ".join([f"x{m:.2f}" for m in remaining]) if remaining else "None"
            await _edit_message(
                self.live_message or interaction.message,
                content=(
                    f"🎮 Player: {self.username}\n"
                    f"💥 The duck got hit by a car! You lost your stake.\n"
                    f"Current Winnings: {fmt(self.session_wallet)} | Multiplier: x{self.multiplier:.2f}\n"
                    f"💼 Wallet before bet: {fmt(self.wallet_before)} | After result: {fmt(wallet)} {fmt_delta_colored(wallet, self.wallet_before)} | 🏦 Bank: {fmt(bank)}\n"
                    f"Remaining Multipliers: {remain_txt}"
                ),
                files=[file],
                view=None,
            )
            interaction.client.get_cog("DuckGame").active_sessions.discard(self.user.id)
            return

        # Safe move within lanes
        image = generate_duck_game_image(self.position, -1, [], total_slots=self.total_lanes)
        buffer = io.BytesIO(); image.save(buffer, format="PNG"); buffer.seek(0)
        file = discord.File(fp=buffer, filename="safe.png")
        bank_data = load_bank(); user_id = str(self.user.id)
        wallet = bank_data[user_id]["wallet"]; bank = bank_data[user_id]["bank"]
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

        # If the user just left grass (moved to lane 0), make sure Stop button appears in the next view
        # (handled by DuckGameView.__init__ via position >= 0)
        await _edit_message(
            self.live_message or interaction.message,
            content=(
                f"🎮 Player: {self.username}\n"
                f"🦆 The duck moved forward safely!\n"
                f"Current Winnings: {fmt(self.session_wallet)} | Multiplier: x{self.multiplier:.2f}\n"
                f"💼 Wallet before bet: {fmt(self.wallet_before)} | After bet: {fmt(wallet)} {fmt_delta_colored(wallet, self.wallet_before)} | 🏦 Bank: {fmt(bank)}\n"
                f"Remaining Multipliers: {remain_txt}"
            ),
            files=[file],
            view=new_view,
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
        async with BANK_LOCK:
            bank_data = load_bank()
            user_id = str(self.user.id)
            if user_id not in bank_data:
                bank_data[user_id] = {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0}
            bank_data[user_id]["wallet"] += self.session_wallet
            bank_data[user_id]["game_active"] = False
            update_bank(bank_data)

        self.clear_items()
        image = generate_duck_game_image(self.position, -1, [], total_slots=self.total_lanes)
        buffer = io.BytesIO(); image.save(buffer, format="PNG"); buffer.seek(0)
        file = discord.File(fp=buffer, filename="cashout.png")

        wallet = bank_data[user_id]["wallet"]; bank = bank_data[user_id]["bank"]
        remaining = self.multipliers[self.position + 1:]
        remain_txt = ", ".join([f"x{m:.2f}" for m in remaining]) if remaining else "None"

        await _edit_message(
            self.live_message or interaction.message,
            content=(
                f"🎮 Player: {self.username}\n"
                f"💰 You stopped the game and cashed out!\n"
                f"Final Winnings: {fmt(self.session_wallet)} | Final Multiplier: x{self.multiplier:.2f}\n"
                f"💼 Wallet before bet: {fmt(self.wallet_before)} | After result: {fmt(wallet)} {fmt_delta_colored(wallet, self.wallet_before)} | 🏦 Bank: {fmt(bank)}\n"
                f"Remaining Multipliers: {remain_txt}"
            ),
            files=[file],
            view=None,
        )
        interaction.client.get_cog("DuckGame").active_sessions.discard(self.user.id)


# -------------------- COG --------------------
class DuckGame(Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_sessions = set()

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
        async with BANK_LOCK:
            fresh = load_bank()
            # ensure record exists
            fresh.setdefault(user_id, {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0})
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
        async with BANK_LOCK:
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
        )
        await ctx.send(help_text)

    @commands.command(name="balance", description="Check your balance.")
    async def balance_command(self, ctx: Context):
        async with BANK_LOCK:
            bank_data = load_bank()
            user_id = str(ctx.author.id)
            if user_id not in bank_data:
                bank_data[user_id] = {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0}
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

        async with BANK_LOCK:
            bank_data = load_bank()
            sender_id = str(ctx.author.id)
            receiver_id = str(member.id)

            bank_data.setdefault(sender_id, {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0})
            bank_data.setdefault(receiver_id, {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0})

            if bank_data[sender_id]["wallet"] < amount:
                await ctx.send("❌ You don't have enough funds to send that amount.")
                return

            bank_data[sender_id]["wallet"] -= amount
            bank_data[receiver_id]["wallet"] += amount
            update_bank(bank_data)

        await ctx.send(f"💸 {ctx.author.name} sent {fmt(amount)} to {member.name}.")

    @commands.command(name="earn", description="Earn $20,000 with a 1-hour cooldown.")
    async def earn_command(self, ctx: Context):
        async with BANK_LOCK:
            bank_data = load_bank()
            user_id = str(ctx.author.id)
            user = bank_data.setdefault(
                user_id,
                {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0},
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
        async with BANK_LOCK:
            bank_data = load_bank()
            user_id = str(ctx.author.id)
            if user_id not in bank_data:
                bank_data[user_id] = {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0}

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
        async with BANK_LOCK:
            bank_data = load_bank()
            user_id = str(ctx.author.id)
            if user_id not in bank_data:
                bank_data[user_id] = {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0}

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
        async with BANK_LOCK:
            bank_data = load_bank()
            user_id = str(member.id)
            if user_id not in bank_data:
                bank_data[user_id] = {"wallet": 1000.0, "bank": 0.0, "game_active": False, "last_earn_ts": 0.0}

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
            async with BANK_LOCK:
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

            async with BANK_LOCK:
                bank_data = load_bank()
                user_id = str(member.id)
                if user_id in bank_data:
                    bank_data[user_id]["game_active"] = False
                    update_bank(bank_data)

            self.active_sessions.discard(member.id)
            await ctx.send(f"✅ Released {member.name} from any stuck game session.")


# Expose setup for bot integration
async def setup(bot):
    cog = DuckGame(bot)
    add_cog = getattr(bot, "add_cog")
    if inspect.iscoroutinefunction(add_cog):
        await add_cog(cog)
    else:
        add_cog(cog)