import datetime
from itertools import islice
import discord
import math

from discord import app_commands
from discord.ext import commands
from ioutils import write_json, initialize_from_json
from structs import Player, Ladder


class LadderRankingView(discord.ui.View):

    def __init__(self, ladder: Ladder, message: discord.Message):
        super().__init__(timeout=None)
        self.page: int = 0
        self.ladder: Ladder = ladder
        self.message: discord.Message = message
        self.PLAYERS_PER_PAGE: int = 10

    @discord.ui.button(emoji="⏪", style=discord.ButtonStyle.blurple, custom_id="back_button")
    async def back_page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0: # Lower bound
            self.page -= 1
            await self.update_view()
        return await interaction.response.defer()


    @discord.ui.button(emoji="⏩", style=discord.ButtonStyle.blurple, custom_id="forward_button")
    async def forward_page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.max_page(): # Upper bound
            self.page += 1
            await self.update_view()
        return await interaction.response.defer()

    async def update_view(self):
        players_ranking = islice(enumerate(self.ladder.players), self.page * self.PLAYERS_PER_PAGE, self.page * self.PLAYERS_PER_PAGE + self.PLAYERS_PER_PAGE)
        description  = '\n'.join([f"**{i+1}.** {player.user.mention}{"" if player.is_active() else " (INACTIVE)"}" for i, player in players_ranking])
        description += f"\n\nPage {self.page + 1}/{self.max_page() + 1}"
        embed = discord.Embed(title=f"Rankings: {self.ladder.guild.name}", description=description)
        await self.message.edit(embed=embed)

    def max_page(self) -> int:
        return math.ceil(len(self.ladder.players) / self.PLAYERS_PER_PAGE) - 1

@app_commands.guild_only()
class LadderCog(commands.GroupCog, name="ladder"):
    """Handles the state of the ladder for each server."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ladders: dict[discord.Guild, Ladder] = {} # Maps a server to its ladder

    @commands.Cog.listener()
    async def on_ready(self):
        await initialize_from_json(self.bot, Ladder, self.ladders, "ladder", is_list=False)

    @app_commands.command()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def create(self, interaction: discord.Interaction, are_you_sure: bool):
        """Create a new ladder for this server. THIS COMMAND WILL ERASE ANY EXISTING LADDER FOR THIS SERVER!"""
        if not are_you_sure:
            return await interaction.response.send_message("Are you sure?", ephemeral=True)

        ladder = Ladder(interaction.guild, [])
        self.ladders[interaction.guild] = ladder
        write_json(interaction.guild.id, "ladder", value=ladder.to_json())

        embed = discord.Embed(title="Ladder Created!", description="Use the `/ladder join` command to join this server's ladder!")
        return await interaction.response.send_message(embed=embed)
    
    @app_commands.command()
    async def join(self, interaction: discord.Interaction):
        """Join this server's ladder."""
        if not await self.verify_ladder_exists(interaction):
            return
        
        player = Player(interaction.user, datetime.datetime.now())
        if player in self.ladders[interaction.guild].players:
            return await interaction.response.send_message("You have already joined this server's ladder.", ephemeral=True)
        self.ladders[interaction.guild].players.append(player)
        write_json(interaction.guild.id, "ladder", value=self.ladders[interaction.guild].to_json())
    
        description = f"**{interaction.user.mention} has joined this server's ladder!**\nThere are now {len(self.ladders[interaction.guild].players)} players in this ladder."
        embed =  discord.Embed(title="New Player!", description=description)
        return await interaction.response.send_message(embed=embed)
    
    @app_commands.command()
    async def leave(self, interaction: discord.Interaction):
        """Leave this server's ladder."""
        if not await self.verify_ladder_exists(interaction):
            return
        
        player = Player(interaction.user, None)
        if player not in self.ladders[interaction.guild].players:
            return await interaction.response.send_message("You are not in this server's ladder.", ephemeral=True)
        self.ladders[interaction.guild].players.remove(player)
        write_json(interaction.guild.id, "ladder", value=self.ladders[interaction.guild].to_json())
    
        description = f"**{interaction.user.mention} has left this server's ladder!**\n\nThere are now {len(self.ladders[interaction.guild].players)} players in this ladder."
        embed =  discord.Embed(title="Player Left!", description=description)
        return await interaction.response.send_message(embed=embed)

    @app_commands.command()
    async def rankings(self, interaction: discord.Interaction):
        """List the current standings of this server's ladder."""
        if not await self.verify_ladder_exists(interaction):
            return
        
        if len(self.ladders[interaction.guild].players) == 0:
            embed = discord.Embed(title=f"Rankings: {interaction.guild.name}", description="This server's ladder is empty.\nUse the `/ladder join` command!")
            return await interaction.response.send_message(embed=embed)
        
        embed = discord.Embed(title="Thinking...")
        callback = await interaction.response.send_message(embed=embed) # We need an initial message in order to just edit it with update_view() later

        message = await interaction.channel.fetch_message(callback.message_id)
        view = LadderRankingView(self.ladders[interaction.guild], message)
        self.bot.add_view(view=view, message_id=callback.message_id)
        await view.update_view()
        await message.edit(view=view)

    async def verify_ladder_exists(self, interaction: discord.Interaction) -> bool:
        """Checks if a ladder exists for this interaction's guild, and if not, sends a warning message."""
        if interaction.guild not in self.ladders:
            await interaction.response.send_message("This server does not have a ladder yet. Use the `/ladder create` command!", ephemeral=True)
            return False
        return True