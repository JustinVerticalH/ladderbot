import asyncio
import os

import discord
from discord.ext import commands

from ladder import LadderCog
from challenge import ChallengeCog

def main():
    token = os.getenv("TOKEN")
    command_prefix = os.getenv("PREFIX", default="!")
    
    activity = discord.Game(name="Melee!")
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix=command_prefix, activity=activity, intents=intents, enable_debug_events=True)

    asyncio.run(bot.add_cog(LadderCog(bot)))
    asyncio.run(bot.add_cog(ChallengeCog(bot)))

    bot.run(token)

if __name__ == "__main__":
    main()