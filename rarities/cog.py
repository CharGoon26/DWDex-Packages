import discord
from discord import app_commands
from discord.ext import commands
from ballsdex.settings import settings
from collections import defaultdict
from ballsdex.core.models import Ball
from ballsdex.core.utils.paginator import Pages, TextPageSource

# Rarities command: Tiered rarity list for all players.

class Rarities(commands.Cog):
    """
    Commands for viewing rarity lists.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command()
    async def rarities(
        self,
        interaction: discord.Interaction,
        chunked: bool = True,
    ):
        """
        Generate a tiered list of countryballs ranked by rarity.

        Parameters
        ----------
        chunked: bool
            Group together countryballs by rarity tier.
        """
        await interaction.response.defer(ephemeral=False)
        text = ""
        balls_queryset = Ball.all().order_by("rarity").filter(rarity__gt=0, enabled=True)
        sorted_balls = await balls_queryset

        if chunked:
            # Group by rarity value, then assign tier numbers
            rarity_groups: dict[float, list[Ball]] = defaultdict(list)
            for ball in sorted_balls:
                rarity_groups[ball.rarity].append(ball)
            
            # Sort rarity values ascending (rarest first), assign tiers
            sorted_rarities = sorted(rarity_groups.keys())
            tier = 1
            for rarity in sorted_rarities:
                balls_in_tier = rarity_groups[rarity]
                text += f"Tier {tier}:\n"
                for ball in balls_in_tier:
                    text += f"{tier}. {ball.country}\n"
                tier += 1
        else:
            # Non-chunked: Sequential numbering, sorted by rarity
            for i, ball in enumerate(sorted_balls, start=1):
                text += f"{i}. {ball.country}\n"

        source = TextPageSource(text, prefix="```md\n", suffix="```")
        pages = Pages(source=source, interaction=interaction, compact=True)
        await pages.start(ephemeral=False)


async def setup(bot):
    await bot.add_cog(Rarities(bot))