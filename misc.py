import discord
from discord import app_commands
from discord.ext import commands
from ioutils import ColorEmbed


class MiscCog(commands.Cog, name="misc"):
    """Miscellaneous commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Sends a welcome message when the bot joins a guild."""
        embed = ColorEmbed(title="Ladderbot", description= "Hi! Thank you for adding me to your server!\nUse the `/ladder create` command to set up a ladder for your server.")
        await guild.system_channel.send(embed=embed)

    @app_commands.command()
    async def help(self, interaction: discord.Interaction, ephemeral: bool = True):
        """What is this bot?"""
        description = \
        "This bot lets you manage a ladder for your server, where players can join and compete against each other.\n \
        Use the `/ladder create` command to set up a ladder for your server, and `/ladder join` to join.\n \
        Then you can use `/challenge someone` to challenge someone above you.\n \
        The two of you then play a best of 5 set. Once you finish, use `/challenge report` to report the result.\n \
        Challenge lots of people and climb as high as you can!"
        embed = ColorEmbed(title="Help", description=description)
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @app_commands.command()
    async def faq(self, interaction: discord.Interaction, ephemeral: bool = True):
        """Frequently asked questions."""
        await interaction.response.send_message(view=FAQView(), ephemeral=ephemeral)

class FAQView(discord.ui.View):
    """View for the FAQ command."""
    
    @discord.ui.button(label="Who can I challenge?", style=discord.ButtonStyle.blurple)
    async def question1(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Answers the question in the label."""
        embed= ColorEmbed(title=button.label, description= \
        "This depends on your position in the ladder:\n \
        2-4: can challenge 1 above.\n \
        5-8: can challenge 2 above.\n \
        9-16: can challenge 3 above.\n \
        17+: can challenge 4 above, etc.\n \
        Inactive players can be skipped over. For example, if player 2 is inactive, player 3 can skip over them and challenge player 1.")
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="What does being inactive mean?", style=discord.ButtonStyle.blurple)
    async def question2(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Answers the question in the label."""
        embed= ColorEmbed(title=button.label, description= \
        "You become inactive if you have not sent a challenge or played a challenge in the last week.\n \
        This is to keep the ladder active and ensure that players are still engaged.\n \
        If you are inactive, players below you in the ladder can skip over you for challenges, increasing the number of people in their challenge range by 1. \n \
        You can become active again by sending a challenge, reporting a challenge, or using the `/ladder activate` command.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Why can't I send another challenge?", style=discord.ButtonStyle.blurple)
    async def question3(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Answers the question in the label."""
        embed= ColorEmbed(title=button.label, description= \
        "You can only send one challenge at a time.\n \
        If you already have a challenge sent, you must either finish it and report it with `/challenge report`, or cancel it with `/challenge cancel`.\n \
        (However, multiple people can challenge you at the same time!)")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Why can't I challenge someone again?", style=discord.ButtonStyle.blurple)
    async def question4(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Answers the question in the label."""
        embed= ColorEmbed(title=button.label, description= \
        "After finishing a challenge, you must wait one week before you can challenge the same person again. Try challenging someone else!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
