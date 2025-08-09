import os
import asyncio
import logging
import discord
from discord.ext import commands
from dotenv import load_dotenv

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("duckbot")

# Load token from .env (expects DISCORD_TOKEN=...)
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
ANNOUNCE_CHANNEL_ID = 1403244656845787170  # optional announce channel

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Put it in a .env file or environment variable.")

# Intents
intents = discord.Intents.default()
intents.message_content = True  # needs to be enabled in Dev Portal for your bot
intents.members = True

# Bot instance
bot = commands.Bot(command_prefix="!", intents=intents)
# If you override help elsewhere
bot.remove_command("help")

_has_announced = False

@bot.event
async def on_ready():
    global _has_announced
    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)

    # Avoid announcing multiple times on reconnects
    if _has_announced:
        return

    channel = bot.get_channel(ANNOUNCE_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(ANNOUNCE_CHANNEL_ID)
        except Exception as e:
            log.warning("Could not fetch announce channel %s: %s", ANNOUNCE_CHANNEL_ID, e)
            channel = None

    if channel:
        try:
            await channel.send("🟢 Duck Game Bot is now online!")
            _has_announced = True
        except Exception as e:
            log.warning("Failed to send online message: %s", e)

async def main():
    # Load your DuckGame cog (which must define `async def setup(bot): ...`)
    try:
        from commands import duckgame
        await duckgame.setup(bot)
    except Exception as e:
        log.exception("Failed to load DuckGame cog: %s", e)
        raise

    # Start the bot
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())