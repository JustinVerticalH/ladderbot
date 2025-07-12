import asyncio
import datetime
import json
import discord

from discord import app_commands
from discord.ext import commands
from discord.utils import format_dt
from ioutils import ColorEmbed, write_json, initialize_from_json
from structs import PagedView, Player, Challenge, Ladder, Result, ResultPlayer


TIME_UNTIL_CHALLENGEABLE_AGAIN = datetime.timedelta(weeks=1) # Time until one player can challenge another player after their last match
HOURS_UNTIL_AUTO_VERIFY = 12 # Time until a challenge result is automatically verified if not confirmed by the other player

class ChallengeSendSelect(discord.ui.Select):
    """A select menu for choosing a player to challenge. Only contains the names of players that can be challenged by the user."""
    def __init__(self, interaction: discord.Interaction, player: Player, ladder: Ladder):
        self.bot: commands.Bot = interaction.client
        self.player: Player = player
        self.ladder: Ladder = ladder

        options = [discord.SelectOption(label=other_player.user.name) for other_player in ladder.challengeable_players(player)]
        super().__init__(placeholder="Select player to challenge", options=options)
    
    async def callback(self, interaction: discord.Interaction):
        """The callback associated with this UI item."""
        challenged_player_name = self.values[0]
        challenged_player = next(player for player in self.ladder.players if player.user.name == challenged_player_name)
        return await self.bot.get_cog("challenge").create_and_send_challenge(interaction, self.player, challenged_player)

class CharacterSelect(discord.ui.Select):
    """Allows a user to select the characters they used in a match."""
    def __init__(self, interaction: discord.Interaction, result: Result, characters: set[str]):
        self.bot: commands.Bot = interaction.client
        self.result: Result = result
        self.winner_characters: set[str] = result.winner.characters
        self.loser_characters: set[str] = result.loser.characters

        options = [discord.SelectOption(label=character) for character in sorted(characters)]
        super().__init__(placeholder="Select character(s)", max_values=5, options=options)

    async def interaction_check(self, interaction):
        """A callback that is called when an interaction happens within this item that checks whether the callback should be processed."""
        return interaction.user == self.result.winner.user or interaction.user == self.result.loser.user

    async def callback(self, interaction: discord.Interaction):
        """The callback associated with this UI item."""
        # Create a new result with the updated characters
        is_winner = interaction.user == self.result.winner.user
        if is_winner:
            self.result.winner.characters = set(self.values)
            self.result.loser.characters = self.loser_characters
        else:
            self.result.winner.characters = self.winner_characters
            self.result.loser.characters = set(self.values)

        # Replace the existing result with this updated one
        if self.result in self.bot.get_cog("challenge").results[interaction.guild]:
            self.bot.get_cog("challenge").results[interaction.guild].remove(self.result)
        self.bot.get_cog("challenge").results[interaction.guild].add(self.result)
        write_json(interaction.guild.id, "results", value=[result.to_json() for result in self.bot.get_cog("challenge").results[interaction.guild]])
        return await interaction.response.send_message("Characters updated!", ephemeral=True)
    
class ChallengeReportView(discord.ui.View):
    """A view for reporting the results of a challenge. Contains buttons to confirm results and report characters."""
    def __init__(self, interaction: discord.Interaction, challenge: Challenge, user_to_verify: discord.Member, result: Result, ladder: Ladder, message: discord.Message):
        super().__init__(timeout=None)
        self.bot: commands.Bot = interaction.client
        self.challenge: Challenge = challenge
        self.user_to_verify: discord.Member = user_to_verify
        self.result: Result = result
        self.ladder: Ladder = ladder
        self.message: discord.Message = message

    @discord.ui.button(emoji="👥", style=discord.ButtonStyle.blurple, label="Report characters")
    async def report_characters(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Saves the characters used by this player in this match and updates the result."""
        with open("characters.json", "r") as file:
            characters = json.load(file)[self.ladder.game.value]
        view = discord.ui.View(timeout=None)
        view.add_item(CharacterSelect(interaction, self.result, characters))
        await interaction.response.send_message(view=view, ephemeral=True)

    @discord.ui.button(emoji="✅", style=discord.ButtonStyle.blurple, label="Confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Confirm the results of a match. This verifies that both players acknowledge that the results are accurate."""
        if not (interaction.user == self.user_to_verify or interaction.user.guild_permissions.manage_guild):
            return
        await self.bot.get_cog("challenge").complete_challenge(interaction, self.challenge, self.result, self.message)

@app_commands.guild_only()
class ChallengeCog(commands.GroupCog, name="challenge"):
    """Handles issuing challenges for a ladder."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.challenges: dict[discord.Guild, set[Challenge]] = {} # Maps a server to its list of challenges
        self.results: dict[discord.Guild, set[Result]] = {} # Maps a server to its list of results

    @commands.Cog.listener()
    async def on_ready(self):
        await initialize_from_json(self.bot, Challenge, self.challenges, "challenges", is_list=True)
        await initialize_from_json(self.bot, Result, self.results, "results", is_list=True)

        synced = await self.bot.tree.sync()
        print(f"Synced {len(synced)} commands.")

    @app_commands.command()
    async def someone(self, interaction: discord.Interaction, user: discord.Member = None):
        """Sends a challenge to another user in the ladder."""
        if not await self.verify_user_in_ladder(interaction):
            return
        if interaction.guild not in self.challenges:
            self.challenges[interaction.guild] = set() # Initialize the set of challenges for this guild if necessary

        recent_result = next((result for result in self.results[interaction.guild] if result.is_match(interaction.user, user) and result.completed_at + TIME_UNTIL_CHALLENGEABLE_AGAIN > datetime.datetime.now()), None)
        if recent_result is not None:
            return await interaction.response.send_message(f"You have already challenged {user.mention}. Finish this challenge first!", ephemeral=True)

        ladder = self.bot.get_cog("ladder").ladders[interaction.guild]
        challenger_player = next((player for player in ladder.players if player.user == interaction.user), None)
        challengeable_players = ladder.challengeable_players(challenger_player)
        if len(challengeable_players) == 0:
            return await interaction.response.send_message("There are no users for you to challenge!", ephemeral=True)

        if user is None:
            view = discord.ui.View().add_item(ChallengeSendSelect(interaction, challenger_player, ladder))
            return await interaction.response.send_message(view=view, ephemeral=True)
        else:
            challenged_player = next((player for player in ladder.players if player.user == user), None)
            if challenged_player is None:
                return await interaction.response.send_message("This user is not in this server's ladder!", ephemeral=True)
            return await self.create_and_send_challenge(interaction, challenger_player, challenged_player)

    @app_commands.command()
    async def report(self, interaction: discord.Interaction, versus: discord.Member, winner: discord.Member, score: app_commands.Range[str, 3, 3], notes: str = ""):
        """Report the results of a finished challenge. If the challenger wins, they swap places!"""
        if winner != interaction.user and winner != versus:
            return await interaction.response.send_message("The winner must be one of the two players playing.", ephemeral=True)

        challenge = next((challenge for challenge in self.challenges[interaction.guild] if challenge.is_match(interaction.user, versus)), None)
        if challenge is None:
            return await interaction.response.send_message("Could not find a challenge for that user!", ephemeral=True)
        
        confirmation_time = datetime.datetime.now() + datetime.timedelta(hours=HOURS_UNTIL_AUTO_VERIFY)
        description= \
            f"{interaction.user.mention} has reported: {winner.mention} {score} {interaction.user.mention if versus == winner else versus.mention}.\n \
            Click the button below to confirm, or run this command again to report a different score.\n \
            This challenge will automatically confirm {format_dt(confirmation_time, style='R')}.\n \
            {f"Notes: {notes}" if notes else ""}"
        embed = ColorEmbed(title="Winner!", description=description)
        response = await interaction.response.send_message(embed=embed)

        message = await interaction.channel.fetch_message(response.message_id)
        winner_player = ResultPlayer(winner, [], self.str_to_scores(score)[0])
        loser_player = ResultPlayer(versus if interaction.user == winner else interaction.user, [], self.str_to_scores(score)[1])
        result = Result(winner_player, loser_player, datetime.datetime.now(), notes)
        ladder = self.bot.get_cog("ladder").ladders[interaction.guild]
        view = ChallengeReportView(interaction, challenge, versus, result, ladder, message)
        await message.edit(embed=embed, view=view)
        await asyncio.sleep(HOURS_UNTIL_AUTO_VERIFY * 60 * 60) # If this report has not been verified by the other user after X hours, auto-verify
        await self.complete_challenge(interaction, challenge, result, message)

    @app_commands.command()
    async def inprogress(self, interaction: discord.Interaction, ephemeral: bool = True):
        "List all your outstanding challenges in this server."
        if interaction.guild not in self.challenges:
            return await interaction.response.send_message("No challenges!", ephemeral=True)
        if not await self.verify_user_in_ladder(interaction):
            return

        challenger_challenges = [f"{challenge.challenged_player.user.mention} - {format_dt(challenge.issued_at, style='R')}" for challenge in self.challenges[interaction.guild] if challenge.challenger_player.user == interaction.user]
        challenged_challenges = [f"{challenge.challenger_player.user.mention} - {format_dt(challenge.issued_at, style='R')}" for challenge in self.challenges[interaction.guild] if challenge.challenged_player.user == interaction.user]
        embed = ColorEmbed(title="Challenges")
        embed.add_field(name="Challenging:", value='\n'.join(challenger_challenges))
        embed.add_field(name="Challenged by:", value='\n'.join(challenged_challenges))
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        
    @app_commands.command()
    async def history(self, interaction: discord.Interaction, ephemeral: bool = True):
        """View your past challenges."""
        if not await self.verify_user_in_ladder(interaction):
            return
        results = [result for result in self.results[interaction.guild] if result.winner.user == interaction.user or result.loser.user == interaction.user]
        results.sort(key=lambda result: result.completed_at, reverse=True)
        result_to_str = lambda result: \
            f"{result.winner.user.mention} {"("+','.join(result.winner.characters)+")" if len(result.winner.characters) > 0 else ""} \
            {result.winner.score}-{result.loser.score} \
            {result.loser.user.mention} {"("+','.join(result.loser.characters)+")" if len(result.loser.characters) > 0 else ""} - {format_dt(result.completed_at, style='R')}\n \
            {"Notes: " + result.notes if result.notes else ""}"
        view = PagedView(self.bot, "Past challenges", results, result_to_str)
        await view.send(interaction, ephemeral=ephemeral)

    async def create_and_send_challenge(self, interaction: discord.Interaction, challenger_player: Player, challenged_player: Player):
        """Create a challenge and send a message to the challenged player."""
        existing_challenge = next((challenge for challenge in self.challenges[interaction.guild] if challenge.is_match(challenger_player.user, challenged_player.user)), None)
        if existing_challenge is not None:
            return await interaction.response.send_message(f"You have already challenged this user {format_dt(existing_challenge.issued_at, style='R')}!", ephemeral=True)
        recent_results = [result for result in self.results[interaction.guild] if (result.completed_at + TIME_UNTIL_CHALLENGEABLE_AGAIN) > datetime.datetime.now()]
        if len(recent_results) > 0:
            return await interaction.response.send_message(f"You have already played this user recently. You can challenge this user again {format_dt(recent_results[0].completed_at + TIME_UNTIL_CHALLENGEABLE_AGAIN, style='R')}!", ephemeral=True)
        
        challenge = Challenge(challenger_player, challenged_player, datetime.datetime.now())
        self.challenges[interaction.guild].add(challenge)
        write_json(interaction.guild.id, "challenges", value=[challenge.to_json() for challenge in self.challenges[interaction.guild]])
        challenger_player.last_active_date = datetime.datetime.now()
        write_json(interaction.guild.id, "ladder", value=self.bot.get_cog("ladder").ladders[interaction.guild].to_json())
        embed = ColorEmbed(title="Challenge!", description=f"You have been challenged by {challenger_player.user.mention}!")
        await interaction.response.send_message(challenged_player.user.mention, embed=embed)

    async def complete_challenge(self, interaction: discord.Interaction, challenge: Challenge, result: Result, message: discord.Message):
        """Update a challenge's status and edit the confirmation message."""
        if challenge not in self.challenges[interaction.guild]:
            embed = ColorEmbed(title="Winner!", description="This challenge has already been reported!")
            return await message.edit(embed=embed, view=None)
        
        challenge.challenger_player.last_active_date = datetime.datetime.now()
        challenge.challenged_player.last_active_date = datetime.datetime.now()

        ladder = self.bot.get_cog("ladder").ladders[interaction.guild]
        lower_position = ladder.players.index(challenge.challenger_player)
        higher_position = ladder.players.index(challenge.challenged_player)
        if result.winner.user == challenge.challenger_player.user:
            # Swap the players' positions
            temp = challenge.challenger_player
            ladder.players[lower_position] = challenge.challenged_player
            ladder.players[higher_position] = temp
            self.bot.get_cog("ladder").ladders[interaction.guild] = ladder
            write_json(interaction.guild.id, "ladder", value=ladder.to_json())

            description=f"{challenge.challenger_player.user.mention} has defeated {challenge.challenged_player.user.mention}!\nThey have climbed from {ordinal(lower_position+1)} to {ordinal(higher_position+1)}."
            embed = ColorEmbed(title="Winner!", description=description)
        else:
            description=f"{challenge.challenged_player.user.mention} has defended their spot against {challenge.challenger_player.user.mention}!\nThey remain at {ordinal(higher_position+1)}."
            embed = ColorEmbed(title="Winner!", description=description)

        # Write everything to storage file
        ladder.players = [challenge.challenger_player if player == challenge.challenger_player else challenge.challenged_player if player == challenge.challenger_player else player for player in ladder.players]
        self.bot.get_cog("ladder").ladders[interaction.guild] = ladder
        write_json(interaction.guild.id, "ladder", value=ladder.to_json())
        await message.edit(embed=embed, view=None)
        self.challenges[interaction.guild].discard(challenge)
        write_json(interaction.guild.id, "challenges", value=[challenge.to_json() for challenge in self.challenges[interaction.guild]])
        self.results[interaction.guild].discard(result) # In case an old version of the result already exists (if the other player has already reported the result)
        self.results[interaction.guild].add(result)
        write_json(interaction.guild.id, "results", value=[result.to_json() for result in self.results[interaction.guild]])

    async def verify_user_in_ladder(self, interaction: discord.Interaction) -> bool:
        """Checks if a user has joined this guild's ladder, and if not, sends a warning message."""
        laddercog = self.bot.get_cog("ladder")
        if not await laddercog.verify_ladder_exists(interaction):
            return False
        ladder = laddercog.ladders[interaction.guild]
        challenger_player = next((player for player in ladder.players if player.user == interaction.user), None)
        if challenger_player is None:
            await interaction.response.send_message("You have not joined this server's ladder yet. Use the `/ladder join` command!", ephemeral=True)
            return False
        return True

    @staticmethod
    def str_to_scores(score: str) -> tuple[int, int]:
        """Converts a score string ("3-0", "3-2", etc) to a tuple of integers (3, 0)."""
        if not (str.isdigit(score[0]) and score[1] == '-' and str.isdigit(score[2])):
            raise ValueError("Score must be in the format `_-_`.")
        return tuple((int(score[0]), int(score[2])))

def ordinal(n: int) -> str:
    """Converts a number to a string representation of the number in ordinal form (1st, 2nd, 3rd, etc)."""
    if n % 100 in [11, 12, 13]:
        return f"{n}th"
    if n % 10 == 1:
        return f"{n}st"
    if n % 10 == 2:
        return f"{n}nd"
    if n % 10 == 3:
        return f"{n}rd"
    return f"{n}th"

# TODO: Clear the finished challenges (once a month?)