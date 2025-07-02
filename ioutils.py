import json, os
from abc import ABC, abstractmethod
from typing import Iterable
from discord import Embed, Color
import discord
from discord.ext import commands


DATA_FILE = os.getenv("DATA_FILE")

class JsonSerializable(ABC):
    @abstractmethod
    def to_json(self) -> dict:
        pass

    @staticmethod
    @abstractmethod
    def from_json(bot: commands.Bot, json_obj: dict):
        pass

def read_json(*path: list[str | int]):
    """Read JSON object data from a file."""
    with open(DATA_FILE, "r") as file:
        position = json.load(file)
    for key in path:
        if key is None or position is None:
            return None
        position = position.get(str(key), None)
    return position

def write_json(*path: any, value: any):
    """Writes JSON object data to a file."""
    with open(DATA_FILE, "r+") as file:
        file_json = json.load(file)
        position = file_json
        for key in path:
            if position.get(str(key)) is None:
                position[str(key)] = dict()
            previous_position = position
            position = position.get(str(key))

        previous_position[str(key)] = value
        file.seek(0)
        json.dump(file_json, file, indent=2)
        file.truncate()

async def initialize_from_json(bot: commands.Bot, settings_class: JsonSerializable, guild_settings: dict[discord.Guild, list[JsonSerializable]], key: str, is_list: bool = True):
    """Initializes a dictionary mapping guild ID to a set of JSON-serializable objects 
    by reading the JSON file and deserializing the objects."""
    for guild in bot.guilds:
        if read_json(guild.id, key) is None:
            write_json(guild.id, key, value={})
        try:
            if is_list:
                guild_settings[guild] = {await settings_class.from_json(bot, json_str) for json_str in read_json(guild.id, key)}
            else:
                guild_settings[guild] = await settings_class.from_json(bot, read_json(guild.id, key))
        except (TypeError, AttributeError, json.JSONDecodeError) as e:
            print(e)