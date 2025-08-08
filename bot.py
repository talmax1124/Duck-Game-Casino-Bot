import discord
from discord.ext import commands
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command("help")

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    channel = bot.get_channel(1403244656845787170)
    if channel:
        await channel.send("🟢 Duck Game Bot is now online!")

async def setup():
    from commands import duckgame
    await duckgame.setup(bot)
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(setup())