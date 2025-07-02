from dataclasses import dataclass, field
import datetime
from ioutils import JsonSerializable

import discord
from discord.ext import commands


@dataclass(order=True)
class Player(JsonSerializable):
    user: discord.Member = field()
    last_active_date: datetime.datetime = field(compare=False)

    def is_active(self) -> bool:
        x = self.last_active_date + datetime.timedelta(weeks=1)
        return (self.last_active_date + datetime.timedelta(weeks=1)) > datetime.datetime.now()

    def to_json(self) -> dict[str, int | float | str]:
        """Convert the current player object to a JSON string."""
        return {
            "user_id": self.user.id,
            "last_active_date": self.last_active_date.timestamp()
        }

    @staticmethod
    async def from_json(bot: commands.Bot, json_obj: dict[str, int | float | str]):
        """Convert a JSON dictionary to a player object."""
        user = await bot.fetch_user(json_obj["user_id"])
        last_active_date = datetime.datetime.fromtimestamp(json_obj["last_active_date"])
        return Player(user, last_active_date)

@dataclass(order=True)
class Ladder(JsonSerializable):
    guild: discord.Guild = field(compare=False)
    players: list[Player] = field(compare=False)

    def to_json(self) -> dict[str, list[int]]:
        """Convert the current ladder object to a JSON string."""
        return {
            "guild_id": self.guild.id,
            "players": [player.to_json() for player in self.players]
        }

    @staticmethod
    async def from_json(bot: commands.Bot, json_obj: dict[str, int | float | str | dict]):
        guild = await bot.fetch_guild(int(json_obj["guild_id"]))
        players = [await Player.from_json(bot, player) for player in json_obj["players"]]
        return Ladder(guild, players)