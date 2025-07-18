import datetime
import discord
import logging

from discord import app_commands
from discord.ext import commands
from ioutils import ColorEmbed, write_json, initialize_from_json
from structs import PagedView, Player, Ladder, Videogame


ADMIN_ROLE_NAME = "Ladder Manager"

@app_commands.guild_only()
class LadderCog(commands.GroupCog, name="ladder"):
    """Handles the state of the ladder for each server."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ladders: dict[discord.Guild, Ladder] = {} # Maps a server to its ladder

    @commands.Cog.listener()
    async def on_ready(self):
        await initialize_from_json(self.bot, Ladder, self.ladders, "ladder", is_list=False)
        logging.info(f"Cog \"{self.__cog_name__}\" is now ready!")

    @app_commands.command()
    @app_commands.checks.has_role(ADMIN_ROLE_NAME)
    async def create(self, interaction: discord.Interaction, game: Videogame, are_you_sure: bool):
        """Create a new ladder for this server. THIS COMMAND WILL ERASE ANY EXISTING LADDER FOR THIS SERVER! Admins only!"""
        if not are_you_sure:
            return await interaction.response.send_message("Are you sure?", ephemeral=True)

        ladder = Ladder(interaction.guild, game, [], False)
        self.ladders[interaction.guild] = ladder
        write_json(interaction.guild.id, "ladder", value=ladder.to_json())

        challenges = self.bot.get_cog("challenge").challenges
        challenges[interaction.guild] = {}
        write_json(interaction.guild.id, "challenges", value=[challenge.to_json() for challenge in challenges[interaction.guild]])

        embed = ColorEmbed(title="Ladder Created!", description="Use the `/ladder join` command to join this server's ladder!")
        return await interaction.response.send_message(embed=embed)
    
    @app_commands.command()
    async def join(self, interaction: discord.Interaction):
        """Join this server's ladder."""
        if not await self.verify_ladder_exists(interaction):
            return
        if not await self.verify_ladder_is_not_frozen(interaction):
            return

        player = Player(interaction.user, datetime.datetime.now())
        if player in self.ladders[interaction.guild].players:
            return await interaction.response.send_message("You have already joined this server's ladder.", ephemeral=True)
        self.ladders[interaction.guild].players.append(player)
        write_json(interaction.guild.id, "ladder", value=self.ladders[interaction.guild].to_json())
    
        description = f"**{interaction.user.mention} has joined this server's ladder!**\nThere are now {len(self.ladders[interaction.guild].players)} players in this ladder."
        embed =  ColorEmbed(title="New Player!", description=description)
        return await interaction.response.send_message(embed=embed)
    
    @app_commands.command()
    async def leave(self, interaction: discord.Interaction):
        """Leave this server's ladder."""
        if not await self.verify_ladder_exists(interaction):
            return
        if not await self.verify_ladder_is_not_frozen(interaction):
            return

        player = Player(interaction.user, None)
        if player not in self.ladders[interaction.guild].players:
            return await interaction.response.send_message("You are not in this server's ladder.", ephemeral=True)
        self.ladders[interaction.guild].players.remove(player)
        write_json(interaction.guild.id, "ladder", value=self.ladders[interaction.guild].to_json())

        challenges = self.bot.get_cog("challenge").challenges[interaction.guild]
        active_challenges = {challenge for challenge in challenges if challenge.challenger_player.user == interaction.user or challenge.challenged_player.user == interaction.user}
        challenges -= active_challenges
        self.bot.get_cog("challenge").challenges[interaction.guild] = challenges
        write_json(interaction.guild.id, "challenges", value=[challenge.to_json() for challenge in challenges])
    
        description = f"**{interaction.user.mention} has left this server's ladder!**\nThere are now {len(self.ladders[interaction.guild].players)} players in this ladder."
        embed = ColorEmbed(title="Player left!", description=description)
        return await interaction.response.send_message(embed=embed)

    @app_commands.command()
    @app_commands.checks.has_role(ADMIN_ROLE_NAME)
    async def freeze(self, interaction: discord.Interaction, freeze: bool, ephemeral: bool = True):
        """Freeze this server's ladder. No one can join, leave, or challenge while the ladder is frozen. Admins only!"""
        if not await self.verify_ladder_exists(interaction):
            return
        
        self.ladders[interaction.guild].is_frozen = freeze
        write_json(interaction.guild.id, "ladder", value=self.ladders[interaction.guild].to_json())
        if freeze:
            embed = ColorEmbed(title="Ladder Frozen!", description="No one can join, leave, or challenge while the ladder is frozen.")
        else:
            embed = ColorEmbed(title="Ladder Unfrozen!", description="Players can now join, leave, or challenge.")
        return await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @app_commands.command()
    @app_commands.checks.has_role(ADMIN_ROLE_NAME)
    async def add(self, interaction: discord.Interaction, user: discord.Member, position: app_commands.Range[int, 1]):
        """Add a player to a ladder at a certain position. Move them to a new position if they are already in the ladder. Admins only!"""
        if not await self.verify_ladder_exists(interaction):
            return
        if not await self.verify_ladder_is_not_frozen(interaction):
            return

        player = Player(user, datetime.datetime.now())
        if player in self.ladders[interaction.guild].players:
            self.ladders[interaction.guild].players.remove(player)
        self.ladders[interaction.guild].players.insert(position-1, player)
        write_json(interaction.guild.id, "ladder", value=self.ladders[interaction.guild].to_json())
    
        description = f"**{interaction.user.mention} has joined this server's ladder at number {position-1}!**\nThere are now {len(self.ladders[interaction.guild].players)} players in this ladder."
        embed =  ColorEmbed(title="New Player!", description=description)
        return await interaction.response.send_message(embed=embed)

    @app_commands.command()
    @app_commands.checks.has_role(ADMIN_ROLE_NAME)
    async def remove(self, interaction: discord.Interaction, user: discord.Member):
        """Remove a player from this server's ladder. Admins only!"""
        if not await self.verify_ladder_exists(interaction):
            return
        if not await self.verify_ladder_is_not_frozen(interaction):
            return

        player = Player(user, None)
        if player not in self.ladders[interaction.guild].players:
            return await interaction.response.send_message(f"{user.mention} is not in this server's ladder.", ephemeral=True)
        
        self.ladders[interaction.guild].players.remove(player)
        write_json(interaction.guild.id, "ladder", value=self.ladders[interaction.guild].to_json())

        challenges = self.bot.get_cog("challenge").challenges[interaction.guild]
        removing_challenges = {challenge for challenge in challenges if challenge.challenger_player.user == user or challenge.challenged_player.user == user}
        challenges -= removing_challenges
        self.bot.get_cog("challenge").challenges[interaction.guild] = challenges
        write_json(interaction.guild.id, "challenges", value=[challenge.to_json() for challenge in challenges])

        description = f"**{user.mention} has been removed from this server's ladder!**\nThere are now {len(self.ladders[interaction.guild].players)} players in this ladder."
        embed = ColorEmbed(title="Player removed!", description=description)
        return await interaction.response.send_message(embed=embed)

    @app_commands.command()
    async def activate(self, interaction: discord.Interaction):
        """Set yourself back to active. You will be considered inactive if you do not send or play any challenges in the next week."""
        if not await self.verify_ladder_exists(interaction):
            return
        if not await self.verify_ladder_is_not_frozen(interaction):
            return

        player = Player(interaction.user, None)
        if player not in self.ladders[interaction.guild].players:
            return await interaction.response.send_message("You are not in this server's ladder.", ephemeral=True)

        player.last_active_date = datetime.datetime.now()
        self.ladders[interaction.guild].players[self.ladders[interaction.guild].players.index(player)] = player
        write_json(interaction.guild.id, "ladder", value=self.ladders[interaction.guild].to_json())
        await interaction.response.send_message(f"You are now active! You will become inactive again if you do not send or play any challenges in the next week.", ephemeral=True)

    @app_commands.command()
    async def rankings(self, interaction: discord.Interaction, ephemeral: bool = True):
        """List the current standings of this server's ladder."""
        if not await self.verify_ladder_exists(interaction):
            return
        
        if len(self.ladders[interaction.guild].players) == 0:
            embed = ColorEmbed(title=f"Rankings: {interaction.guild.name}", description="This server's ladder is empty.\nUse the `/ladder join` command!")
            return await interaction.response.send_message(embed=embed)
        
        ladder = self.ladders[interaction.guild]
        view = PagedView[Player](self.bot, f"Rankings: {ladder.guild.name}", ladder.players, lambda p: f"{p.user.mention}{"" if p.is_active() else " (INACTIVE)"}")
        await view.send(interaction, ephemeral=ephemeral)

    async def verify_ladder_exists(self, interaction: discord.Interaction) -> bool:
        """Checks if a ladder exists for this interaction's guild, and if not, sends a warning message."""
        if self.ladders[interaction.guild] is None:
            await interaction.response.send_message("This server does not have a ladder yet. Use the `/ladder create` command!", ephemeral=True)
            return False
        return True
    
    async def verify_ladder_is_not_frozen(self, interaction: discord.Interaction) -> bool:
        """Checks if the ladder is frozen for this interaction's guild, and if so, sends a warning message."""
        if self.ladders[interaction.guild].is_frozen:
            await interaction.response.send_message("This server's ladder is currently frozen. Please wait until it is unfrozen to use this command.", ephemeral=True)
            return False
        return True
