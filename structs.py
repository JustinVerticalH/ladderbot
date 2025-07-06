from dataclasses import dataclass, field
import datetime
from itertools import islice
import math
import sys
from typing import Callable
from ioutils import ColorEmbed, JsonSerializable

import discord
from discord.ext import commands


@dataclass(order=True)
class Player(JsonSerializable):
    """A player in a ladder. A player becomes inactive if they have not played in the past two weeks."""
    user: discord.Member = field()
    last_active_date: datetime.datetime = field(compare=False, hash=False)

    def __eq__(self, other):
        return self.user.id == other.user.id

    def is_active(self) -> bool:
        """Whether or not the player is active. A player is active if they have completed a challenge (as challenger or challenged) in the past two weeks."""
        return (self.last_active_date + datetime.timedelta(weeks=2)) > datetime.datetime.now()

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
class Challenge:
    """A challenge between two players. Once the two players have played a set, the challenge is complete."""
    challenger_player: Player = field()
    challenged_player: Player = field()
    issued_at: datetime.datetime = field()
    completed_at: datetime.datetime | None = field(default=None, compare=False, hash=False)
    challenger_player_score: int | None = field(default=None, compare=False, hash=False)
    challenged_player_score: int | None = field(default=None, compare=False, hash=False)

    def __hash__(self):
        return hash((self.challenger_player.user.id, self.challenged_player.user.id, self.issued_at))

    def to_json(self) -> dict[str, int | float | str]:
        """Convert the current challenge object to a JSON string."""
        return {
            "challenger_player": self.challenger_player.to_json(),
            "challenged_player": self.challenged_player.to_json(),
            "issued_at": self.issued_at.timestamp(),
            "completed_at": self.completed_at.timestamp() if self.completed_at is not None else -1,
            "challenger_player_score": self.challenger_player_score if self.challenger_player_score is not None else -1,
            "challenged_player_score": self.challenged_player_score if self.challenged_player_score is not None else -1
        }

    @staticmethod
    async def from_json(bot: commands.Bot, json_obj: dict[str, int | float | str]):
        """Convert a JSON dictionary to a challenge object."""
        challenger_player = await Player.from_json(bot, json_obj["challenger_player"])
        challenged_player = await Player.from_json(bot, json_obj["challenged_player"])
        issued_at = datetime.datetime.fromtimestamp(json_obj["issued_at"])
        completed_at = datetime.datetime.fromtimestamp(json_obj["completed_at"]) if json_obj["completed_at"] >= 0 else None
        challenger_player_score = json_obj["challenger_player_score"] if json_obj["challenger_player_score"] >= 0 else None
        challenged_player_score = json_obj["challenged_player_score"] if json_obj["challenged_player_score"] >= 0 else None

        return Challenge(challenger_player, challenged_player, issued_at, completed_at, challenger_player_score, challenged_player_score)
    
    def is_match(self, player1: discord.Member, player2: discord.Member) -> bool:
            """Whether or not this challenge involves both of these players (true if player1 challenged player2 or player2 challenged player1)."""
            return (self.challenger_player.user == player1 and self.challenged_player.user == player2) or (self.challenger_player.user == player2 and self.challenged_player.user == player1)

@dataclass(order=True)
class Ladder(JsonSerializable):
    """A ladder for a server. A ladder contains an ordered list of players."""
    guild: discord.Guild = field()
    players: list[Player] = field(compare=False, hash=False)

    def __eq__(self, other):
        return self.guild.id == other.guild.id

    def challengeable_players(self, challenger: Player) -> list[Player]:
        """All players that can be challenged by a player. This is based on each player's position on the ladder:\n
        2-4: can challenge 1 above\n
        5-8: can challenge 2 above\n
        9-16: can challenge 3 above\n
        17+: can challenge 4 above, etc.\n
        Inactive players are skipped over, but still included in the final list, i.e. 2-4 can challenge any players up to the next active player."""
        number_of_challengeable_players = math.ceil(math.log2(self.players.index(challenger) + 0.0001))
        number_of_active_players_found = 0
        players = []
        challenger_index = self.players.index(challenger)
        for i in range(number_of_challengeable_players):
            try:
                player = self.players[challenger_index-i-1]
            except IndexError:
                break
            if player.is_active():
                number_of_active_players_found += 1
            players.append(player)
            if number_of_active_players_found == number_of_challengeable_players:
                break
        return players

    def to_json(self) -> dict[str, list[int]]:
        """Convert the current ladder object to a JSON string."""
        return {
            "guild_id": self.guild.id,
            "players": [player.to_json() for player in self.players]
        }

    @staticmethod
    async def from_json(bot: commands.Bot, json_obj: dict[str, int | float | str | dict]):
        """Convert a JSON dictionary to a ladder object."""
        guild = await bot.fetch_guild(int(json_obj["guild_id"]))
        players = [await Player.from_json(bot, player) for player in json_obj["players"]]
        return Ladder(guild, players)
    
class PagedView[T](discord.ui.View):
    """A subclass of :class:`discord.ui.View` that presents an ordered list of entries, with a given number of entries per page."""
    def __init__(self, bot: commands.Bot, title: str, entries: list[T], func_entry_to_str: Callable[[T], str]):
        super().__init__(timeout=None)
        self.bot = bot
        self.title = title
        self.entries: list = entries
        self.func_entry_to_str: Callable[[T], str] = func_entry_to_str
        self.interaction: discord.Interaction = None
        self.page: int = 0
        self.ENTRIES_PER_PAGE: int = 10

    @discord.ui.button(emoji="⏪", style=discord.ButtonStyle.blurple, custom_id="back_button")
    async def back_page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Goes back one page on click. Does nothing if already on the first page."""
        if self.page > 0: # Lower bound
            self.page -= 1
            await self.update_view()
        await interaction.response.defer()

    @discord.ui.button(emoji="⏩", style=discord.ButtonStyle.blurple, custom_id="forward_button")
    async def forward_page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Goes forward one page on click. Does nothing if already on the last page."""
        if self.page < self.max_page(): # Upper bound
            self.page += 1
            await self.update_view()
        await interaction.response.defer()

    async def send(self, interaction: discord.Interaction, ephemeral: bool = True) -> discord.InteractionCallbackResponse:
        """Send a message containing this view and register the view for persistent listening."""
        self.interaction = interaction
        callback = await interaction.response.send_message(embed=self.populate_embed(), view=self, ephemeral=ephemeral)
        self.timeout = None # send_message() changes the timeout to 900.0. Why? The world may never know
        self.bot.add_view(view=self, message_id=callback.message_id)

    def populate_embed(self) -> ColorEmbed:
        """Write the new contents of the view."""
        ranking = islice(enumerate(self.entries), self.page * self.ENTRIES_PER_PAGE, self.page * self.ENTRIES_PER_PAGE + self.ENTRIES_PER_PAGE)
        description  = '\n'.join([f"**{i+1}.** {self.func_entry_to_str(player)}" for i, player in ranking])
        description += f"\n\nPage {self.page + 1}/{self.max_page() + 1}"
        return ColorEmbed(title=self.title, description=description)
    
    async def update_view(self):
        """Write the new contents of the view and update the original message."""
        await self.interaction.edit_original_response(embed=self.populate_embed())

    def max_page(self) -> int:
        """The number of pages needed to show all entries. Note that this is zero-indexed, i.e. a view with 11 entries (two pages) will have a max_page of 1."""
        return max(math.ceil(len(self.entries) / self.ENTRIES_PER_PAGE) - 1, 0)