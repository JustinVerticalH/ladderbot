import datetime
import discord
import math

from dataclasses import dataclass, field
from discord.ext import commands
from enum import Enum
from itertools import islice
from ioutils import ColorEmbed, JsonSerializable
from typing import Callable


class Videogame(Enum):
    """The videogame that the players in the ladder play."""
    SSBM = "Super Smash Bros. Melee"
    RoA = "Rivals of Aether"

@dataclass(order=True)
class Player(JsonSerializable):
    """A player in a ladder. A player becomes inactive if they have not played in the past week."""
    user: discord.Member = field()
    last_active_date: datetime.datetime = field(compare=False, hash=False)

    def __eq__(self, other):
        return self.user.id == other.user.id
    
    def __hash__(self):
        return hash(self.user.id)

    def is_active(self) -> bool:
        """Whether or not the player is active. A player is active if they have completed a challenge (as challenger or challenged) in the past week."""
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
        user = bot.get_user(int(json_obj["user_id"]))
        last_active_date = datetime.datetime.fromtimestamp(json_obj["last_active_date"])
        return Player(user, last_active_date)

@dataclass(order=True)
class Challenge:
    """A challenge between two players. Once the two players have played a set, the challenge is complete."""
    challenger_player: Player = field()
    challenged_player: Player = field()
    issued_at: datetime.datetime = field()

    def __hash__(self):
        return hash((self.challenger_player.user.id, self.challenged_player.user.id, self.issued_at))

    def to_json(self) -> dict[str, int | float | str]:
        """Convert the current challenge object to a JSON string."""
        return {
            "challenger_player": self.challenger_player.to_json(),
            "challenged_player": self.challenged_player.to_json(),
            "issued_at": self.issued_at.timestamp()
        }

    @staticmethod
    async def from_json(bot: commands.Bot, json_obj: dict[str, int | float | str]):
        """Convert a JSON dictionary to a challenge object."""
        challenger_player = await Player.from_json(bot, json_obj["challenger_player"])
        challenged_player = await Player.from_json(bot, json_obj["challenged_player"])
        issued_at = datetime.datetime.fromtimestamp(json_obj["issued_at"])

        return Challenge(challenger_player, challenged_player, issued_at)
    
    def is_match(self, player1: discord.Member, player2: discord.Member) -> bool:
            """Whether or not this challenge involves both of these players (true if player1 challenged player2 or player2 challenged player1)."""
            return (self.challenger_player.user == player1 and self.challenged_player.user == player2) or (self.challenger_player.user == player2 and self.challenged_player.user == player1)

@dataclass(order=True)
class Ladder(JsonSerializable):
    """A ladder for a server. A ladder contains an ordered list of players."""
    guild: discord.Guild = field()
    game: Videogame = field()
    players: list[Player] = field(compare=False, hash=False)
    is_frozen: bool = field(default=False, compare=False, hash=False)  # If true, no new challenges can be made


    def __eq__(self, other):
        return self.guild.id == other.guild.id

    def challengeable_players(self, challenger: Player) -> list[Player]:
        """All players that can be challenged by a player. This is based on each player's position on the ladder:\n
        2-4: can challenge 1 above\n
        5-8: can challenge 2 above\n
        9-16: can challenge 3 above\n
        17+: can challenge 4 above, etc.\n
        Inactive players are skipped over, but still included in the final list, i.e. 2-4 can challenge any players up to the next active player."""
        index = self.players.index(challenger)
        if index == 0:
            number_of_challengeable_players = 0
        elif index <= 3:
            number_of_challengeable_players = 1
        elif index <= 7:
            number_of_challengeable_players = 2
        elif index <= 15:
            number_of_challengeable_players = 3
        else:
            number_of_challengeable_players = 4
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

    def to_json(self) -> dict[str, int | float | str | dict]:
        """Convert the current ladder object to a JSON string."""
        return {
            "guild_id": self.guild.id,
            "game": self.game.value,
            "players": [player.to_json() for player in self.players],
            "is_frozen": self.is_frozen
        }

    @staticmethod
    async def from_json(bot: commands.Bot, json_obj: dict[str, int | float | str | dict]):
        """Convert a JSON dictionary to a ladder object."""
        guild = await bot.fetch_guild(int(json_obj["guild_id"]))
        game = Videogame(json_obj["game"])
        players = [await Player.from_json(bot, player) for player in json_obj["players"]]
        is_frozen = json_obj.get("is_frozen", False)
        return Ladder(guild, game, players, is_frozen)
    
@dataclass(order=True)
class ResultPlayer(JsonSerializable):
    """A player after finishing a challenge."""

    user: discord.Member = field()
    characters: set[str] = field(compare=False, hash=False)
    score: int = field()

    def __hash__(self):
        return hash(self.user.id)

    def to_json(self) -> dict[str, int | float | str]:
        """Convert the current result player object to a JSON string."""
        return {
            "user_id": self.user.id,
            "characters": [character for character in self.characters],
            "score": self.score
        }

    @staticmethod
    async def from_json(bot: commands.Bot, json_obj: dict[str, int | float | str]):
        """Convert a JSON dictionary to a result player object."""
        user = await bot.fetch_user(json_obj["user_id"])
        characters = set(json_obj.get("characters", {}))
        score = json_obj.get("score", 0)
        return ResultPlayer(user, characters, score)

@dataclass(frozen=True, order=True)
class Result(JsonSerializable):
    """The results of a challenge."""

    winner: ResultPlayer = field()
    loser: ResultPlayer = field()
    completed_at: datetime.datetime = field()
    is_upset: bool = field(default=False, compare=False, hash=False) # Whether the player who was originally lower ranked won
    notes: str = field(default="", compare=False, hash=False)

    def to_json(self) -> dict[str, int | float | str]:
        """Convert the current results object to a JSON string."""
        return {
            "winner": self.winner.to_json(),
            "loser": self.loser.to_json(),
            "completed_at": self.completed_at.timestamp(),
            "is_upset": self.is_upset,
            "notes": self.notes
        }

    @staticmethod
    async def from_json(bot: commands.Bot, json_obj: dict[str, int | float | str]):
        """Convert a JSON dictionary to a results object."""
        winner = await ResultPlayer.from_json(bot, json_obj["winner"])
        loser = await ResultPlayer.from_json(bot, json_obj["loser"])
        completed_at = datetime.datetime.fromtimestamp(json_obj["completed_at"])
        is_upset = json_obj.get("is_upset", False)
        notes = json_obj.get("notes", "")
        return Result(winner, loser, completed_at, is_upset, notes)
    
    def is_match(self, player1: discord.Member, player2: discord.Member) -> bool:
        """Whether or not this result involves both of these players (true if player1 beat player2 or player2 beat player1)."""
        return (self.winner.user == player1 and self.loser.user == player2) or (self.winner.user == player2 and self.loser.user == player1)
    
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
