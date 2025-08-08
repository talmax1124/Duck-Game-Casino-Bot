import discord
from discord.ui import View, Button
from discord.ext import commands
from discord.ext.commands import Context
from utils.image_generator import generate_duck_game_image
import random
import io
import json
import os


def load_bank():
    if not os.path.exists("data/bank.json"):
        return {}
    with open("data/bank.json", "r") as f:
        data = json.load(f)
    # Ensure all users have wallet, bank, and game_active keys
    for user_id, balances in data.items():
        if not isinstance(balances, dict):
            data[user_id] = {"wallet": float(balances), "bank": 0.0, "game_active": False}
        else:
            if "wallet" not in balances:
                balances["wallet"] = 1000.0
            if "bank" not in balances:
                balances["bank"] = 0.0
            if "game_active" not in balances:
                balances["game_active"] = False
    return data

def update_bank(data):
    with open("data/bank.json", "w") as f:
        json.dump(data, f, indent=4)


class DuckGameView(View):
    def __init__(self, user, amount, wallet, multiplier, username):
        super().__init__(timeout=None)
        self.user = user
        self.amount = float(amount)
        self.position = -1  # Start duck in the grass
        self.hazard_pos = None
        self.started = False
        self.session_wallet = self.amount  # session wallet to track current winnings
        self.wallet = wallet  # user's real wallet (static during game)
        self.multiplier = multiplier
        self.username = username
        self.multipliers = [1.2, 1.5, 1.7, 2.0, 2.4]

        # Only show the start button initially
        self.start_button = Button(label="Start", style=discord.ButtonStyle.primary)
        self.start_button.callback = self.start_button_callback
        self.add_item(self.start_button)

        # Generate the initial image for the game
        self.initial_image = generate_duck_game_image(self.position, -1, [])
        print("[DEBUG] Initialized DuckGameView with position -1")

    async def start_button_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("You cannot control this game.", ephemeral=True)
            return

        self.clear_items()
        print("[DEBUG] Start button clicked. Game starting...")
        self.hazard_pos = random.randint(0, 4)
        print(f"[DEBUG] Hazard position set to {self.hazard_pos}")
        self.forward_button = Button(label="Forward", style=discord.ButtonStyle.success)
        self.stop_button = Button(label="Stop", style=discord.ButtonStyle.danger)
        self.forward_button.callback = self.forward_button_callback
        self.stop_button.callback = self.stop_button_callback
        self.add_item(self.forward_button)
        self.add_item(self.stop_button)

        self.multiplier = 0.0
        self.session_wallet = 0.0

        image = generate_duck_game_image(self.position, -1, [])
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        file = discord.File(fp=buffer, filename="start.png")

        bank_data = load_bank()
        user_id = str(self.user.id)
        wallet = bank_data[user_id]["wallet"]
        remaining_multipliers = self.multipliers[self.position+1:]
        remaining_text = ", ".join([f"x{m:.1f}" for m in remaining_multipliers]) if remaining_multipliers else "None"
        await interaction.response.edit_message(content=f"🎮 Player: {self.username}\n🦆 The duck moved forward safely!\nCurrent Winnings: ${self.session_wallet:.2f} | Multiplier: x{self.multiplier:.2f}\n💼 Wallet: ${wallet:.2f}\nRemaining Multipliers: {remaining_text}", attachments=[file], view=self)

    async def forward_button_callback(self, interaction: discord.Interaction):
        print(f"[DEBUG] Forward clicked. Current position: {self.position}")
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("You cannot control this game.", ephemeral=True)
            return

        self.position += 1
        print(f"[DEBUG] Duck moved to position: {self.position}")

        if self.position == len(self.multipliers) - 1:
            self.multiplier = self.multipliers[self.position]
            self.session_wallet = self.amount * self.multiplier

        if self.position > len(self.multipliers) - 1:
            self.clear_items()
            image = generate_duck_game_image(self.position, -1, [])
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            buffer.seek(0)
            file = discord.File(fp=buffer, filename="finish.png")
            bank_data = load_bank()
            user_id = str(self.user.id)
            bank_data[user_id]["wallet"] += self.session_wallet
            bank_data[user_id]["game_active"] = False
            update_bank(bank_data)
            wallet = bank_data[user_id]["wallet"]
            bank = bank_data[user_id]["bank"]
            remaining_multipliers = self.multipliers[self.position+1:]
            remaining_text = ", ".join([f"x{m:.1f}" for m in remaining_multipliers]) if remaining_multipliers else "None"
            await interaction.response.edit_message(
                content=f"🎮 Player: {self.username}\n🏁 The duck made it safely across all lanes!\nFinal Winnings: ${self.session_wallet:.2f} | Final Multiplier: x{self.multiplier:.2f}\n💼 Wallet: ${wallet:.2f} | 🏦 Bank: ${bank:.2f}\nRemaining Multipliers: {remaining_text}",
                attachments=[file],
                view=None
            )
            self.view.bot.get_cog("DuckGame").active_sessions.discard(self.user.id)
            return

        if self.position == self.hazard_pos:
            self.clear_items()
            image = generate_duck_game_image(self.position, self.hazard_pos, [])
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            buffer.seek(0)
            file = discord.File(fp=buffer, filename="crash.png")
            bank_data = load_bank()
            user_id = str(self.user.id)
            bank_data[user_id]["wallet"] -= self.amount  # Deduct bet on loss
            if bank_data[user_id]["wallet"] < 0:
                bank_data[user_id]["wallet"] = 0.0
            bank_data[user_id]["game_active"] = False
            update_bank(bank_data)
            wallet = bank_data[user_id]["wallet"]
            bank = bank_data[user_id]["bank"]
            self.session_wallet = 0.0
            remaining_multipliers = self.multipliers[self.position+1:]
            remaining_text = ", ".join([f"x{m:.1f}" for m in remaining_multipliers]) if remaining_multipliers else "None"
            await interaction.response.edit_message(content=f"🎮 Player: {self.username}\n💥 The duck got hit by a car!\nCurrent Winnings: ${self.session_wallet:.2f} | Multiplier: x{self.multiplier:.2f}\n💼 Wallet: ${wallet:.2f} | 🏦 Bank: ${bank:.2f}\nRemaining Multipliers: {remaining_text}", attachments=[file], view=None)
            self.view.bot.get_cog("DuckGame").active_sessions.discard(self.user.id)
            return
        else:
            image = generate_duck_game_image(self.position, -1, [])
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            buffer.seek(0)
            file = discord.File(fp=buffer, filename="safe.png")
            if self.position < len(self.multipliers):
                self.multiplier = self.multipliers[self.position]
                self.session_wallet = self.amount * self.multiplier

            bank_data = load_bank()
            user_id = str(self.user.id)
            wallet = bank_data[user_id]["wallet"]
            bank = bank_data[user_id]["bank"]
            remaining_multipliers = self.multipliers[self.position+1:]
            remaining_text = ", ".join([f"x{m:.1f}" for m in remaining_multipliers]) if remaining_multipliers else "None"
            await interaction.response.edit_message(content=f"🎮 Player: {self.username}\n🦆 The duck moved forward safely!\nCurrent Winnings: ${self.session_wallet:.2f} | Multiplier: x{self.multiplier:.2f}\n💼 Wallet: ${wallet:.2f} | 🏦 Bank: ${bank:.2f}\nRemaining Multipliers: {remaining_text}", attachments=[file], view=self)

    async def stop_button_callback(self, interaction: discord.Interaction):
        print(f"[DEBUG] Stop clicked. Final position: {self.position}")
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("You cannot control this game.", ephemeral=True)
            return

        # Add session_wallet to real wallet
        bank_data = load_bank()
        user_id = str(self.user.id)
        if user_id not in bank_data:
            bank_data[user_id] = {"wallet": 1000.0, "bank": 0.0}
        bank_data[user_id]["wallet"] += self.session_wallet
        bank_data[user_id]["game_active"] = False
        update_bank(bank_data)

        self.clear_items()
        image = generate_duck_game_image(self.position, -1, [])
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        file = discord.File(fp=buffer, filename="cashout.png")

        wallet = bank_data[user_id]["wallet"]
        bank = bank_data[user_id]["bank"]
        remaining_multipliers = self.multipliers[self.position+1:]
        remaining_text = ", ".join([f"x{m:.1f}" for m in remaining_multipliers]) if remaining_multipliers else "None"
        await interaction.response.edit_message(content=f"🎮 Player: {self.username}\n💰 You stopped the game and cashed out!\nFinal Winnings: ${self.session_wallet:.2f} | Final Multiplier: x{self.multiplier:.2f}\n💼 Wallet: ${wallet:.2f} | 🏦 Bank: ${bank:.2f}\nRemaining Multipliers: {remaining_text}", attachments=[file], view=None)
        self.view.bot.get_cog("DuckGame").active_sessions.discard(self.user.id)


class DuckGame(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_sessions = set()

    @commands.hybrid_command(name="duckgame", description="Start the Duck Game!")
    async def duckgame_command(self, ctx: Context, amount: str):
        print(f"[DEBUG] /duckgame command called by {ctx.author.name} with amount: {amount}")
        bank_data = load_bank()
        user_id = str(ctx.author.id)
        if user_id not in bank_data:
            bank_data[user_id] = {"wallet": 1000.0, "bank": 0.0, "game_active": False}
        if bank_data[user_id].get("game_active", False):
            await ctx.send("❌ You already have an active game session.")
            return
        bank_data[user_id]["game_active"] = True
        update_bank(bank_data)

        self.active_sessions.add(ctx.author.id)

        user_wallet = bank_data[user_id].get("wallet", 1000.0)

        if amount.lower() == "a":
            amount = user_wallet
        elif amount.lower() == "h":
            amount = user_wallet / 2
        else:
            try:
                amount = float(amount)
            except ValueError:
                await ctx.send("❌ Invalid bet amount. Please enter a number, or use 'A' for all or 'H' for half.")
                return

        if amount > user_wallet:
            await ctx.send("❌ You don't have enough funds to bet that amount.")
            return

        # Removed deduction of amount from wallet
        # user_wallet -= amount
        # bank_data[user_id]["wallet"] = user_wallet
        # update_bank(bank_data)

        bank = bank_data[user_id].get("bank", 0.0)
        multiplier = 1.0
        view = DuckGameView(ctx.author, amount, user_wallet, multiplier, ctx.author.name)
        print("[DEBUG] Created DuckGameView instance")
        buffer = io.BytesIO()
        image = generate_duck_game_image(-1, -1, [])
        image.save(buffer, format="PNG")
        buffer.seek(0)
        file = discord.File(fp=buffer, filename="start.png")
        print("[DEBUG] Sending initial game message with image")
        await ctx.send(
            f"🎮 Player: {ctx.author.name}\n🦆 Duck Game Started!\nYou bet ${amount:.2f}. Click 'Start' to begin.\nCurrent Winnings: ${amount:.2f} | Multiplier: x{multiplier:.2f}\n💼 Wallet: ${user_wallet:.2f} | 🏦 Bank: ${bank:.2f}",
            view=view,
            file=file
        )

    @commands.command(name="balance", description="Check your balance.")
    async def balance_command(self, ctx: Context):
        bank_data = load_bank()
        user_id = str(ctx.author.id)
        if user_id not in bank_data:
            bank_data[user_id] = {"wallet": 1000.0, "bank": 0.0}
        wallet = bank_data[user_id].get("wallet", 1000.0)
        bank = bank_data[user_id].get("bank", 0.0)
        await ctx.send(f"💰 {ctx.author.name}, your current wallet balance is: ${wallet:.2f}\n🏦 Your bank balance is: ${bank:.2f}")

    @commands.command(name="withdraw", description="Withdraw from your bank to your wallet.")
    async def withdraw_command(self, ctx: Context, amount: str):
        bank_data = load_bank()
        user_id = str(ctx.author.id)
        if user_id not in bank_data:
            bank_data[user_id] = {"wallet": 1000.0, "bank": 0.0}

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

        await ctx.send(f"🏦 {ctx.author.name} withdrew ${amount:.2f} from their bank to their wallet.")
        await ctx.send(f"✅ Successfully withdrew ${amount:.2f} from your bank.")

    @commands.command(name="deposit", description="Deposit from your wallet to your bank.")
    async def deposit_command(self, ctx: Context, amount: str):
        bank_data = load_bank()
        user_id = str(ctx.author.id)
        if user_id not in bank_data:
            bank_data[user_id] = {"wallet": 1000.0, "bank": 0.0}

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

        await ctx.send(f"🏦 {ctx.author.name} deposited ${amount:.2f} from their wallet to their bank.")

    @commands.command(name="setmoney", description="Admin command to set a user's wallet and/or bank.")
    async def set_money_command(self, ctx: Context, member: discord.Member, wallet: float = None, bank: float = None):
        if not any(role.name.lower() == "admin" for role in ctx.author.roles):
            await ctx.send("❌ You need the 'admin' role to use this command.")
            return
        bank_data = load_bank()
        user_id = str(member.id)
        if user_id not in bank_data:
            bank_data[user_id] = {"wallet": 1000.0, "bank": 0.0}

        if wallet is not None:
            bank_data[user_id]["wallet"] = wallet
        if bank is not None:
            bank_data[user_id]["bank"] = bank

        update_bank(bank_data)
        await ctx.send(f"✅ Updated {member.name}'s balances. Wallet: ${bank_data[user_id]['wallet']:.2f}, Bank: ${bank_data[user_id]['bank']:.2f}")

    @commands.command(name="release", description="Release a user's stuck game session.")
    async def release_command(self, ctx: Context, member_or_all: str):
        if not any(role.name.lower() == "admin" for role in ctx.author.roles):
            await ctx.send("❌ You need the 'admin' role to use this command.")
            return

        bank_data = load_bank()

        if member_or_all.lower() == "a":
            for user_id in bank_data:
                bank_data[user_id]["game_active"] = False
            self.active_sessions.clear()
            update_bank(bank_data)
            await ctx.send("✅ Released all users from any stuck game sessions.")
        else:
            try:
                member = await commands.MemberConverter().convert(ctx, member_or_all)
            except commands.BadArgument:
                await ctx.send("❌ Invalid user or ID.")
                return

            user_id = str(member.id)
            if user_id in bank_data:
                bank_data[user_id]["game_active"] = False
                update_bank(bank_data)

            self.active_sessions.discard(member.id)
            await ctx.send(f"✅ Released {member.name} from any stuck game session.")


# Expose setup for bot integration
async def setup(bot: commands.Bot):
    await bot.add_cog(DuckGame(bot))