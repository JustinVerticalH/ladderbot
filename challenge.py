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
ADMIN_ROLE_NAME = "Ladder Manager"

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

class CharacterReportView(discord.ui.View):
    """A view that lets users select a player and the characters they used in a match. Used af"""
    def __init__(self, interaction: discord.Interaction, result: Result, characters: set[str]):
        self.bot: commands.Bot = interaction.client
        self.result: Result = result
        self.user_select = self.UserSelect(result)
        self.character_select = self.CharacterSelect(result, characters)
        super().__init__(timeout=None)
        self.add_item(self.user_select)
        self.add_item(self.character_select)

    async def listen_for_user_and_characters(self, interaction: discord.Interaction):
        """Wait until both the user select and character select have been used, then update the result."""
        while True:
            # If the user has selected a player and characters, replace the existing result with this updated one
            if self.user_select.player is not None and self.character_select.characters is not None:
                # Create a new result with the updated characters
                is_winner = self.user_select.player.user == self.result.winner.user
                if is_winner:
                    self.result.winner.characters = self.character_select.characters
                else:
                    self.result.loser.characters = self.character_select.characters

                # Replace the existing result with this updated one
                if self.result in self.bot.get_cog("challenge").results[interaction.guild]:
                    self.bot.get_cog("challenge").results[interaction.guild].remove(self.result)
                self.bot.get_cog("challenge").results[interaction.guild].add(self.result)
                write_json(interaction.guild.id, "results", value=[result.to_json() for result in self.bot.get_cog("challenge").results[interaction.guild]])
                return await interaction.followup.send(content="Characters updated!", ephemeral=True)
            await asyncio.sleep(1)

    class UserSelect(discord.ui.Select):
        """Allows a user to select which user they are reporting characters for."""
        def __init__(self, result: Result):
            """Allows a user to select the characters they used in a match."""
            self.result: Result = result
            self.player: ResultPlayer = None

            options = [discord.SelectOption(label=player.user.name) for player in [self.result.winner, self.result.loser]]
            super().__init__(placeholder="Select user", options=options)

        async def callback(self, interaction: discord.Interaction):
            """The callback associated with this UI item."""
            self.player = next(player for player in [self.result.winner, self.result.loser] if player.user.name == self.values[0])
            return await interaction.response.defer()

    class CharacterSelect(discord.ui.Select):
        """Allows a user to select the characters used by someone in a match."""
        def __init__(self, result: Result, characters: set[str]):
            """Allows a user to select the characters they used in a match."""
            self.result: Result = result
            self.characters: set[str] = None

            options = [discord.SelectOption(label=character) for character in sorted(characters)]
            super().__init__(placeholder="Select character(s)", max_values=5, options=options)

        async def callback(self, interaction: discord.Interaction):
            """The callback associated with this UI item."""
            # Create a new result with the updated characters
            self.characters = set(self.values)
            return await interaction.response.defer()
    
class ChallengeReportView(discord.ui.View):
    """A view for reporting the results of a challenge. Contains buttons to confirm results and report characters."""
    def __init__(self, interaction: discord.Interaction, challenge: Challenge, users_to_verify: set[discord.Member], result: Result, ladder: Ladder, message: discord.Message, is_edit: bool):
        self.bot: commands.Bot = interaction.client
        self.challenge: Challenge = challenge
        self.users_to_verify: set[discord.Member] = users_to_verify
        self.result: Result = result
        self.ladder: Ladder = ladder
        self.message: discord.Message = message
        self.is_edit: bool = is_edit
        super().__init__(timeout=None)

    @discord.ui.button(emoji="👥", style=discord.ButtonStyle.blurple, label="Report characters")
    async def report_characters(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Saves the characters used by this player in this match and updates the result."""
        with open("characters.json", "r") as file:
            characters = json.load(file)[self.ladder.game.value]
        view = CharacterReportView(interaction, self.result, set(characters))
        await interaction.response.send_message(view=view, ephemeral=True)
        await view.listen_for_user_and_characters(interaction)

    @discord.ui.button(emoji="✅", style=discord.ButtonStyle.green, label="Confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Confirm the results of a match. This verifies that both players acknowledge that the results are accurate."""
        admin_role = discord.utils.find(lambda role: role.name == ADMIN_ROLE_NAME, interaction.guild.roles)
        if not (interaction.user in self.users_to_verify or admin_role in interaction.user.roles):
            return
        await self.bot.get_cog("challenge").complete_challenge(interaction, self.challenge, self.result, self.message, self.is_edit)

@app_commands.guild_only()
class ChallengeCog(commands.GroupCog, name="challenge"):
    """Handles issuing challenges for a ladder."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(app_commands.ContextMenu(name="Challenge", callback=self.send_challenge)) # You can't use @app_commands.context_menu() in a cog
        self.challenges: dict[discord.Guild, set[Challenge]] = {} # Maps a server to its list of challenges
        self.results: dict[discord.Guild, set[Result]] = {} # Maps a server to its list of results

    @commands.Cog.listener()
    async def on_ready(self):
        await initialize_from_json(self.bot, Challenge, self.challenges, "challenges", is_list=True)
        await initialize_from_json(self.bot, Result, self.results, "results", is_list=True)

        synced = await self.bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
        print(f"Cog \"{self.__cog_name__}\" is now ready!")

    @app_commands.command()
    async def someone(self, interaction: discord.Interaction, user: discord.Member = None):
        """Send a challenge to another user in the ladder."""
        await self.send_challenge(interaction, user)

    @app_commands.command()
    async def cancel(self, interaction: discord.Interaction, versus: discord.Member):
        """Cancel a challenge you have sent to another user."""
        if not await self.verify_user_in_ladder(interaction):
            return
        if not await self.bot.get_cog("ladder").verify_ladder_is_not_frozen(interaction):
            return
        if interaction.guild not in self.challenges:
            return await interaction.response.send_message("No challenges!", ephemeral=True)

        challenge = next((challenge for challenge in self.challenges[interaction.guild] if challenge.is_match(interaction.user, versus)), None)
        if challenge is None:
            return await interaction.response.send_message("Could not find a challenge for that user!", ephemeral=True)

        self.challenges[interaction.guild].discard(challenge)
        write_json(interaction.guild.id, "challenges", value=[challenge.to_json() for challenge in self.challenges[interaction.guild]])
        await interaction.response.send_message(embed=ColorEmbed(title="Challenge!", description=f"Cancelled challenge vs. {versus.mention}."))

    @app_commands.command()
    async def report(self, interaction: discord.Interaction, winner: discord.Member, score: app_commands.Range[str, 3, 3], loser: discord.Member, notes: str = ""):
        """Report the results of a finished challenge. If the challenger wins, they swap places!"""
        await self.report_challenge(interaction, winner, loser, score, notes, is_edit=False)

    @app_commands.command()
    async def edit(self, interaction: discord.Interaction, winner: discord.Member, score: app_commands.Range[str, 3, 3], loser: discord.Member, notes: str = ""):
        """Edit the results of a challenge that has already been reported."""
        await self.report_challenge(interaction, winner, loser, score, notes if notes != "" else None, is_edit=True)

    @app_commands.command()
    async def undo(self, interaction: discord.Interaction, winner: discord.Member, loser: discord.Member):
        """Undo results that have already been reported and confirmed."""
        if not await self.verify_user_in_ladder(interaction):
            return
        if not await self.verify_ladder_is_not_frozen(interaction):
            return
        if interaction.guild not in self.challenges:
            return await interaction.response.send_message("No challenges!", ephemeral=True)

        result = next((result for result in self.results[interaction.guild] if result.is_match(winner, loser)), None)
        if result is None:
            return await interaction.response.send_message("Could not find a completed challenge for those players!", ephemeral=True)

        if result.is_upset:
            # Swap the players' positions back
            ladder = self.bot.get_cog("ladder").ladders[interaction.guild]
            winner_player = next((player for player in self.bot.get_cog("ladder").ladders[interaction.guild].players if player.user == result.winner.user), None)
            loser_player = next((player for player in self.bot.get_cog("ladder").ladders[interaction.guild].players if player.user == result.loser.user), None)
            lower_position = ladder.players.index(winner_player)
            higher_position = ladder.players.index(loser_player)
            temp = winner_player
            ladder.players[lower_position] = loser_player
            ladder.players[higher_position] = temp
            self.bot.get_cog("ladder").ladders[interaction.guild] = ladder
            write_json(interaction.guild.id, "ladder", value=ladder.to_json())
        self.results[interaction.guild].discard(result)
        write_json(interaction.guild.id, "results", value=[result.to_json() for result in self.challenges[interaction.guild]])
        await interaction.response.send_message(embed=ColorEmbed(title="Challenge!", description=f"Undid the results of {interaction.user.mention} vs. {versus.mention}. \
                                                                 {"\nThey have swapped positions back." if result.is_upset else ""}"))

    @app_commands.command()
    async def inprogress(self, interaction: discord.Interaction, only_mine: bool = True, ephemeral: bool = True):
        "List all your outstanding challenges in this server."
        if interaction.guild not in self.challenges:
            return await interaction.response.send_message("No challenges!", ephemeral=True)
        if not await self.verify_user_in_ladder(interaction):
            return

        embed = ColorEmbed(title="Challenges")
        if only_mine:
            challenger_challenges = [f"{challenge.challenged_player.user.mention} - {format_dt(challenge.issued_at, style='R')}" for challenge in self.challenges[interaction.guild] if challenge.challenger_player.user == interaction.user]
            challenged_challenges = [f"{challenge.challenger_player.user.mention} - {format_dt(challenge.issued_at, style='R')}" for challenge in self.challenges[interaction.guild] if challenge.challenged_player.user == interaction.user]
            embed.add_field(name="Challenging:", value='\n'.join(challenger_challenges))
            embed.add_field(name="Challenged by:", value='\n'.join(challenged_challenges))
        else:
            challenges = [f"{challenge.challenger_player.user.mention}-{challenge.challenged_player.user.mention} - {format_dt(challenge.issued_at, style='R')}" for challenge in self.challenges[interaction.guild]]
            embed.description = '\n'.join(challenges)
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        
    @app_commands.command()
    async def history(self, interaction: discord.Interaction, only_mine: bool = True, ephemeral: bool = True):
        """View your past challenges."""
        if not await self.verify_user_in_ladder(interaction):
            return
        results = [result for result in self.results[interaction.guild] if (result.winner.user == interaction.user or result.loser.user == interaction.user or not only_mine)]
        results.sort(key=lambda result: result.completed_at, reverse=True)
        result_to_str = lambda result: \
            f"{result.winner.user.mention} {"("+','.join(result.winner.characters)+")" if len(result.winner.characters) > 0 else ""} \
            {result.winner.score}-{result.loser.score} \
            {result.loser.user.mention} {"("+','.join(result.loser.characters)+")" if len(result.loser.characters) > 0 else ""} - {format_dt(result.completed_at, style='R')}\n \
            {"Notes: " + result.notes if result.notes else ""}"
        view = PagedView(self.bot, "Past challenges", results, result_to_str)
        await view.send(interaction, ephemeral=ephemeral)

    async def send_challenge(self, interaction: discord.Interaction, user: discord.Member):
        """Sends a challenge to another user in the ladder."""
        if not await self.verify_user_in_ladder(interaction):
            return
        if not await self.bot.get_cog("ladder").verify_ladder_is_not_frozen(interaction):
            return
        if interaction.guild not in self.challenges:
            self.challenges[interaction.guild] = set() # Initialize the set of challenges for this guild if necessary

        recent_result = next((result for result in self.results[interaction.guild] if result.is_match(interaction.user, user) and result.completed_at + TIME_UNTIL_CHALLENGEABLE_AGAIN > datetime.datetime.now()), None)
        if recent_result is not None:
            return await interaction.response.send_message(f"You have already played {user.mention} recently! You can play them again {format_dt(recent_result.completed_at + TIME_UNTIL_CHALLENGEABLE_AGAIN, style='R')}.", ephemeral=True)
        
        existing_challenge = next((challenge for challenge in self.challenges[interaction.guild] if challenge.is_match(interaction.user, user)), None)
        if existing_challenge is not None:
            return await interaction.response.send_message(f"You have already challenged {user.mention} {format_dt(existing_challenge.issued_at, style='R')}. Finish this challenge first!", ephemeral=True)

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

    async def create_and_send_challenge(self, interaction: discord.Interaction, challenger_player: Player, challenged_player: Player):
        """Create a challenge and send a message to the challenged player."""
        if not await self.bot.get_cog("ladder").verify_ladder_is_not_frozen(interaction):
            return
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

    async def report_challenge(self, interaction: discord.Interaction, winner: discord.Member, loser: discord.Member, score: str, notes: str, is_edit: bool):
        """Report the results of a challenge."""
        if not await self.bot.get_cog("ladder").verify_ladder_is_not_frozen(interaction):
            return

        if is_edit:
            result = next((result for result in self.results[interaction.guild] if result.is_match(winner, loser)), None)
            challenge = None
        else:
            challenge = next((challenge for challenge in self.challenges[interaction.guild] if challenge.is_match(winner, loser)), None)
            result = None
        if result is None and challenge is None:
            return await interaction.response.send_message(f"Could not find a {"completed" if result is None else ""} challenge for those players!", ephemeral=True)

        users_to_confirm = {user for user in [winner, loser] if user != interaction.user}
        confirmation_time = datetime.datetime.now() + datetime.timedelta(hours=HOURS_UNTIL_AUTO_VERIFY)
        notes = result.notes if (notes == None and result != None) else notes
        description= \
            f"{interaction.user.mention} has reported a set!\n \
            {"New score:" if is_edit else"Score:"} {winner.mention} {score} {loser.mention}.\n \
            {" or ".join((user.mention for user in users_to_confirm))}: Click the button below to confirm, or run this command again to report a different score.\n \
            This challenge will automatically confirm {format_dt(confirmation_time, style='R')}.\n \
            {f"Notes: {notes}" if notes else ""}"
        embed = ColorEmbed(title="Winner!", description=description)
        response = await interaction.response.send_message(embed=embed)

        message = await interaction.channel.fetch_message(response.message_id)
        winner_player_results = ResultPlayer(winner, [], self.str_to_scores(score)[0])
        loser_player_results = ResultPlayer(loser, [], self.str_to_scores(score)[1])
        winner_player = next((player for player in self.bot.get_cog("ladder").ladders[interaction.guild].players if player.user == winner_player_results.user), None)
        loser_player = next((player for player in self.bot.get_cog("ladder").ladders[interaction.guild].players if player.user == loser_player_results.user), None)

        ladder = self.bot.get_cog("ladder").ladders[interaction.guild]
        if is_edit:
            existing_result = next((result for result in self.results[interaction.guild] if result.is_match(winner_player_results.user, loser_player_results.user)), None)
            is_same_winner = existing_result.winner.user == winner
            is_upset = (is_same_winner and existing_result.is_upset) or (not is_same_winner and not existing_result.is_upset)
        else:
            is_upset = ladder.players.index(winner_player) > ladder.players.index(loser_player) # True if the winner was lower on the ladder than the loser
        result = Result(winner_player_results, loser_player_results, datetime.datetime.now(), is_upset, notes)
        ladder = self.bot.get_cog("ladder").ladders[interaction.guild]
        view = ChallengeReportView(interaction, challenge, users_to_confirm, result, ladder, message, is_edit)
        await message.edit(embed=embed, view=view)
        await asyncio.sleep(HOURS_UNTIL_AUTO_VERIFY * 60 * 60) # If this report has not been verified by the other user after X hours, auto-verify
        await self.complete_challenge(interaction, challenge, result, message)

    async def complete_challenge(self, interaction: discord.Interaction, challenge: Challenge, result: Result, message: discord.Message, is_edit: bool):
        """Update a challenge's status and edit the confirmation message."""
        if not await self.bot.get_cog("ladder").verify_ladder_is_not_frozen(interaction):
            return
        if not is_edit and challenge not in self.challenges[interaction.guild]:
            embed = ColorEmbed(title="Winner!", description="This challenge has already been reported!")
            return await message.edit(embed=embed, view=None)

        ladder = self.bot.get_cog("ladder").ladders[interaction.guild]
        if challenge is not None:
            player1 = challenge.challenger_player
            player2 = challenge.challenged_player
        else:
            player1 = next((player for player in ladder.players if player.user == result.winner.user), None)
            player2 = next((player for player in ladder.players if player.user == result.loser.user), None)

        player1.last_active_date = datetime.datetime.now()
        player2.last_active_date = datetime.datetime.now()

        lower_position = max(ladder.players.index(player1), ladder.players.index(player2))
        higher_position = min(ladder.players.index(player1), ladder.players.index(player2))
        if (result.is_upset) or (challenge is not None and result.winner.user == challenge.challenger_player.user):
            # Swap the players' positions
            temp = player1
            ladder.players[lower_position] = player2
            ladder.players[higher_position] = temp
            self.bot.get_cog("ladder").ladders[interaction.guild] = ladder
            write_json(interaction.guild.id, "ladder", value=ladder.to_json())

            description=f"{player1.user.mention} has defeated {player2.user.mention} {result.winner.score}-{result.loser.score}!\nThey have climbed from {ordinal(lower_position+1)} to {ordinal(higher_position+1)}."
            embed = ColorEmbed(title="Winner!", description=description)
        else:
            description=f"{player1.user.mention} has defended their spot against {player2.user.mention} {result.winner.score}-{result.loser.score}!\nThey remain at {ordinal(higher_position+1)}."
            embed = ColorEmbed(title="Winner!", description=description)

        # Write everything to storage file
        ladder.players = [player1 if player == player1 else player2 if player == player2 else player for player in ladder.players]
        self.bot.get_cog("ladder").ladders[interaction.guild] = ladder
        write_json(interaction.guild.id, "ladder", value=ladder.to_json())
        await message.edit(embed=embed, view=None)
        self.challenges[interaction.guild].discard(challenge)
        write_json(interaction.guild.id, "challenges", value=[challenge.to_json() for challenge in self.challenges[interaction.guild]])
        self.results[interaction.guild].discard(result) # In case an old version of the result already exists (if the other player has already reported the result)
        if is_edit:
            existing_result = next((result for result in self.results[interaction.guild] if result.is_match(result.winner.user, result.loser.user)), None)
            self.results[interaction.guild].discard(existing_result)
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