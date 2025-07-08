import asyncio
import datetime
import discord
import re

from discord import app_commands
from discord.ext import commands
from discord.utils import format_dt
from ioutils import ColorEmbed, write_json, initialize_from_json
from structs import PagedView, Player, Challenge, Ladder


HOURS_UNTIL_AUTO_VERIFY = 12

class ChallengeSendSelect(discord.ui.Select):
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
    
class ChallengeVerifyButton(discord.ui.Button):
    def __init__(self, bot: commands.Bot, challenge: Challenge, user_to_verify: discord.Member, winner: discord.Member, score: str, message: discord.Message):
        self.bot: commands.Bot = bot
        self.challenge: Challenge = challenge
        self.user_to_verify: discord.Member = user_to_verify
        self.winner: discord.Member = winner
        self.score: str = score
        self.message: discord.Message = message

        super().__init__(style=discord.ButtonStyle.blurple, label=f"@{user_to_verify.name}: Click here to confirm!", emoji = "✅")

    async def interaction_check(self, interaction: discord.Interaction):
        return interaction.user == self.user_to_verify

    async def callback(self, interaction: discord.Interaction):
        """The callback associated with this UI item."""
        await self.bot.get_cog("challenge").complete_challenge(interaction, self.challenge, self.winner, self.score, self.message)

@app_commands.guild_only()
class ChallengeCog(commands.GroupCog, name="challenge"):
    """Handles issuing challenges for a ladder."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.challenges: dict[discord.Guild, set[Challenge]] = {} # Maps a server to its list of challenges

    @commands.Cog.listener()
    async def on_ready(self):
        await initialize_from_json(self.bot, Challenge, self.challenges, "challenges", is_list=True)
        synced = await self.bot.tree.sync()
        print(f"Synced {len(synced)} commands.")

    @app_commands.command()
    async def someone(self, interaction: discord.Interaction, user: discord.Member = None):
        """Sends a challenge to another user in the ladder."""
        if not await self.verify_user_in_ladder(interaction):
            return
        if interaction.guild not in self.challenges:
            self.challenges[interaction.guild] = set()

        existing_challenge = next((challenge for challenge in self.challenges[interaction.guild] if challenge.challenger_player.user == interaction.user and challenge.completed_at is None), None)
        if existing_challenge is not None:
            return await interaction.response.send_message(f"You have already challenged {existing_challenge.challenged_player.user.mention}. Finish this challenge first!", ephemeral=True)

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
    async def report(self, interaction: discord.Interaction, versus: discord.Member, winner: discord.Member, score: app_commands.Range[str, 3, 3]):
        """Report the results of a finished challenge. If the challenger wins, they swap places!"""
        if winner != interaction.user and winner != versus:
            return await interaction.response.send_message("The winner must be one of the two players playing.", ephemeral=True)

        challenge = next((challenge for challenge in self.challenges[interaction.guild] if challenge.is_match(interaction.user, versus) and challenge.completed_at is None), None)
        if challenge is None:
            return await interaction.response.send_message("Could not find a challenge for that user!", ephemeral=True)
        
        confirmation_time = datetime.datetime.now() + datetime.timedelta(hours=HOURS_UNTIL_AUTO_VERIFY)
        description= \
            f"{interaction.user.mention} has reported: {winner.mention} {score} {interaction.user.mention if versus == winner else versus.mention}.\n \
            Click the button below to confirm, or run this command again to report a different score.\n \
            This challenge will automatically confirm {format_dt(confirmation_time, style='R')}.\n"
        embed = ColorEmbed(title="Winner!", description=description)
        response = await interaction.response.send_message(embed=embed)
        message = await interaction.channel.fetch_message(response.message_id)
        view = discord.ui.View().add_item(ChallengeVerifyButton(self.bot, challenge, versus, winner, score, message))
        await message.edit(embed=embed, view=view)
        await asyncio.sleep(HOURS_UNTIL_AUTO_VERIFY * 60 * 60) # If this report has not been verified by the other user after X hours, auto-verify
        await self.complete_challenge(interaction, challenge, winner, score, message)

    @app_commands.command()
    async def list(self, interaction: discord.Interaction, ephemeral: bool = True):
        "List all your outstanding challenges in this server."
        if interaction.guild not in self.challenges:
            return await interaction.response.send_message("No challenges!", ephemeral=True)
        if not await self.verify_user_in_ladder(interaction):
            return

        active_challenges = [challenge for challenge in self.challenges[interaction.guild] if challenge.completed_at is None]
        challenger_challenges = [f"{challenge.challenged_player.user.mention} - {format_dt(challenge.issued_at, style='R')}" for challenge in active_challenges if challenge.challenger_player.user == interaction.user]
        challenged_challenges = [f"{challenge.challenger_player.user.mention} - {format_dt(challenge.issued_at, style='R')}" for challenge in active_challenges if challenge.challenged_player.user == interaction.user]
        embed = ColorEmbed(title="Challenges")
        embed.add_field(name="Challenging:", value='\n'.join(challenger_challenges))
        embed.add_field(name="Challenged by:", value='\n'.join(challenged_challenges))
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        
    @app_commands.command()
    async def history(self, interaction: discord.Interaction, ephemeral: bool = True):
        """View your past challenges."""
        if not await self.verify_user_in_ladder(interaction):
            return
        past_challenges = [challenge for challenge in self.challenges[interaction.guild] if (challenge.challenger_player.user == interaction.user or challenge.challenged_player.user == interaction.user) and challenge.completed_at is not None]
        past_challenges.sort(key=lambda challenge: challenge.completed_at, reverse=True)
        challenge_to_str = lambda challenge: f"{challenge.challenger_player.user.mention} {challenge.challenger_player_score}-{challenge.challenged_player_score} {challenge.challenged_player.user.mention} - {format_dt(challenge.completed_at, style='R')}"
        view = PagedView(self.bot, "Past challenges", past_challenges, challenge_to_str)
        await view.send(interaction, ephemeral=ephemeral)

    async def create_and_send_challenge(self, interaction: discord.Interaction, challenger_player: Player, challenged_player: Player):
        matching_challenges = [challenge for challenge in self.challenges[interaction.guild] if challenge.is_match(challenger_player.user, challenged_player.user)]
        existing_challenges = [challenge for challenge in matching_challenges if challenge.completed_at is None]
        if len(existing_challenges) > 0:
            return await interaction.response.send_message(f"You have already challenged this user {format_dt(existing_challenges[0].issued_at, style='R')}!", ephemeral=True)
        recent_challenges = [challenge for challenge in matching_challenges if (challenge.issued_at + datetime.timedelta(weeks=1)) > datetime.datetime.now()]
        if len(recent_challenges) > 0:
            return await interaction.response.send_message(f"You have already played this user in the past week. You can challenge this user again {format_dt(recent_challenges[0].issued_at + datetime.timedelta(weeks=1), style='R')}!", ephemeral=True)
        
        challenge = Challenge(challenger_player, challenged_player, datetime.datetime.now())
        self.challenges[interaction.guild].add(challenge)
        write_json(interaction.guild.id, "challenges", value=[challenge.to_json() for challenge in self.challenges[interaction.guild]])
        challenger_player.last_active_date = datetime.datetime.now()
        write_json(interaction.guild.id, "players", value=[player.to_json() for player in self.bot.get_cog("ladder").ladders[interaction.guild].players])
        embed = ColorEmbed(title="Challenge!", description=f"You have been challenged by {challenger_player.user.mention}!")
        await interaction.response.send_message(challenged_player.user.mention, embed=embed)

    async def complete_challenge(self, interaction: discord.Interaction, challenge: Challenge, winner: discord.Member, score: str, message: discord.Message):
        """Update a challenge's status and edit the confirmation message."""
        if challenge.completed_at is not None:
            embed = ColorEmbed(title="Winner!", description="This challenge has already been reported!")
            return await message.edit(embed=embed, view=None)
        challenge.completed_at = datetime.datetime.now()
        scores = [int(s) for s in re.compile("(\d)-(\d)").match(score).groups()]
        ladder = self.bot.get_cog("ladder").ladders[interaction.guild]
        lower_position = ladder.players.index(challenge.challenger_player)
        higher_position = ladder.players.index(challenge.challenged_player)
        if winner == challenge.challenger_player.user:
            # Swap the players' positions
            temp = challenge.challenger_player
            ladder.players[lower_position] = challenge.challenged_player
            ladder.players[higher_position] = temp
            self.bot.get_cog("ladder").ladders[interaction.guild] = ladder
            write_json(interaction.guild.id, "ladder", value=ladder.to_json())

            challenge.challenger_player_score = max(scores)
            challenge.challenged_player_score = min(scores)

            description=f"{challenge.challenger_player.user.mention} has defeated {challenge.challenged_player.user.mention}!\nThey have climbed from {ordinal(lower_position+1)} to {ordinal(higher_position+1)}."
            embed = ColorEmbed(title="Winner!", description=description)
        else:
            challenge.challenger_player_score = min(scores)
            challenge.challenged_player_score = max(scores)

            description=f"{challenge.challenged_player.user.mention} has defended their spot against {challenge.challenger_player.user.mention}!\nThey remain at {ordinal(higher_position+1)}."
            embed = ColorEmbed(title="Winner!", description=description)

        challenge.challenger_player.last_active_date = datetime.datetime.now()
        challenge.challenged_player.last_active_date = datetime.datetime.now()
        write_json(interaction.guild.id, "players", value=[player.to_json() for player in ladder.players])
        await message.edit(embed=embed, view=None)
        write_json(interaction.guild.id, "challenges", value=[challenge.to_json() for challenge in self.challenges[interaction.guild]])

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