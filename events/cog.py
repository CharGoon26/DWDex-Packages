import discord
from discord import app_commands
from discord.ext import commands
from typing import TYPE_CHECKING

from ballsdex.core.models import Special
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


@app_commands.guild_only()
class Events(commands.Cog):
    """
    View information about special events.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @app_commands.command()
    async def events(self, interaction: discord.Interaction["BallsDexBot"]):
        """
        List all special events with their details.
        """
        await interaction.response.defer(ephemeral=True)
        
        # Get all specials ordered by PK descending (newest first)
        specials = await Special.all().order_by('-id')
        
        if not specials:
            await interaction.followup.send(
                "No special events found in the database.",
                ephemeral=True
            )
            return
        
        # Create embed
        embed = discord.Embed(
            title="📅 Special Events",
            color=discord.Color.blue(),
            description=f"Total events: {len(specials)}"
        )
        
        # Build the list as a formatted text block
        event_list = []
        
        for special in specials:
            # Get emoji - can be either unicode emoji or Discord emoji ID
            emoji_display = "❓"
            if special.emoji:
                try:
                    # Try to convert to int (Discord emoji ID)
                    emoji_obj = self.bot.get_emoji(int(special.emoji))
                    emoji_display = str(emoji_obj) if emoji_obj else "❓"
                except ValueError:
                    # It's a unicode emoji, use it directly
                    emoji_display = special.emoji
            
            # Format rarity as percentage
            rarity_percent = special.rarity * 100
            if rarity_percent.is_integer():
                rarity_str = f"{int(rarity_percent)}%"
            else:
                rarity_str = f"{rarity_percent:.2f}%"
            
            # Format dates with Discord timestamps
            if special.start_date and special.end_date:
                # Use Discord timestamp format for automatic timezone conversion
                start_timestamp = f"<t:{int(special.start_date.timestamp())}:f>"
                end_timestamp = f"<t:{int(special.end_date.timestamp())}:f>"
                date_range = f"{start_timestamp} - {end_timestamp}"
            else:
                date_range = "Ongoing"
            
            # Count cards caught for this event
            from ballsdex.core.models import BallInstance
            card_count = await BallInstance.filter(special=special).count()
            
            # Format: Name | Emoji | Date Range | Rarity | Cards Caught
            event_line = f"**{special.name}** {emoji_display} • {date_range} • {rarity_str} • {card_count} caught"
            event_list.append(event_line)
        
        # Join all events with newlines
        embed.description = "\n\n".join(event_list)
        
        embed.set_footer(text=f"Total: {len(specials)} events")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
