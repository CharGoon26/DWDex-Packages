import discord
from discord import app_commands
from discord.ext import commands
from ballsdex.settings import settings
from tortoise.functions import Count
from ballsdex.core.models import Ball, BallInstance, Player, balls, Special
from ballsdex.core.utils.paginator import FieldPageSource, Pages, TextPageSource

# This command sends the top 10 players with the most balls in ephemeral.

class Leaderboard(commands.Cog):
    """
    Custom commands for the bot.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command()
    async def leaderboard(self, interaction: discord.Interaction):
        """
        Show the leaderboard of users with the most caught countryballs.
        """
        await interaction.response.defer(ephemeral=False, thinking=True)
        
        players = await Player.annotate(ball_count=Count("balls")).order_by("-ball_count").limit(10)
        
        if not players:
            await interaction.followup.send("No players found.", ephemeral=False)
            return

        entries = []
        for i, player in enumerate(players):
            user = self.bot.get_user(player.discord_id)
            if user is None:
                user = await self.bot.fetch_user(player.discord_id)

            # Add medal for top three
            if i == 0:
                medal = "🥇"
            elif i == 1:
                medal = "🥈"
            elif i == 2:
                medal = "🥉"
            else:
                medal = ""
            
            entries.append((f"{i + 1}. {medal} {user.name}", f"Whos: {player.ball_count}"))

        source = FieldPageSource(entries, per_page=5, inline=False)
        source.embed.title = "🏆 Top 10 Doctor Who Dex Players 🏆"
        source.embed.color = discord.Color.gold()
        source.embed.set_thumbnail(url=interaction.user.display_avatar.url)
        
        pages = Pages(source=source, interaction=interaction)
        await pages.start(ephemeral=False)


async def setup(bot):
    await bot.add_cog(Leaderboard(bot))
