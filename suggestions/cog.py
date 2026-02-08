import logging
from typing import TYPE_CHECKING, Optional
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from ballsdex.core.models import Player

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.suggestions")

# Configuration - Set this to your suggestions channel ID
# You can find channel ID by: Right click channel → Copy ID (requires Developer Mode enabled)
SUGGESTIONS_CHANNEL_ID = 1432561197034504223  # Replace with your channel ID, e.g., 1234567890123456789

# Optional: DM these user IDs when a suggestion is received
NOTIFY_USERS = [496296918280962049]  # List of Discord user IDs to DM, e.g., [123456789, 987654321]


class SuggestionModal(discord.ui.Modal, title="Submit a Suggestion"):
    """Modal for collecting suggestion details"""
    
    suggestion_text = discord.ui.TextInput(
        label="Your Suggestion",
        style=discord.TextStyle.paragraph,
        placeholder="Describe your suggestion here...",
        required=True,
        max_length=2000
    )
    
    def __init__(self, cog: "Suggestions"):
        super().__init__()
        self.cog = cog
    
    async def on_submit(self, interaction: discord.Interaction):
        """Handle suggestion submission"""
        await interaction.response.defer(ephemeral=True)
        
        # Get player data
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        
        # Track suggestions in player's extra_data
        suggestions_count = player.extra_data.get("suggestions_submitted", 0)
        player.extra_data["suggestions_submitted"] = suggestions_count + 1
        player.extra_data["last_suggestion_date"] = datetime.now().isoformat()
        await player.save()
        
        # Send to suggestions channel
        await self.cog.send_suggestion(
            interaction.user,
            str(self.suggestion_text),
            None,
            suggestions_count + 1
        )
        
        await interaction.followup.send(
            "✅ Thank you! Your suggestion has been submitted to the bot owners.",
            ephemeral=True
        )


class Suggestions(commands.Cog):
    """
    Submit suggestions to the bot owners
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    async def send_suggestion(
        self,
        user: discord.User,
        suggestion_text: str,
        attachment_url: Optional[str],
        suggestion_number: int
    ):
        """Send suggestion to the configured channel and optionally DM owners"""
        
        # Create embed
        embed = discord.Embed(
            title="💡 New Suggestion",
            description=suggestion_text,
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        embed.set_author(
            name=f"{user.name} (ID: {user.id})",
            icon_url=user.display_avatar.url if user.display_avatar else None
        )
        
        embed.add_field(
            name="User Info",
            value=f"**Username:** {user.mention}\n**Total Suggestions:** {suggestion_number}",
            inline=False
        )
        
        if attachment_url:
            embed.add_field(
                name="Attachment",
                value=f"[View File]({attachment_url})",
                inline=False
            )
            # Try to show image if it's an image
            if attachment_url.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                embed.set_image(url=attachment_url)
        
        embed.set_footer(text=f"Suggestion #{suggestion_number}")
        
        # Send to suggestions channel
        if SUGGESTIONS_CHANNEL_ID:
            try:
                channel = self.bot.get_channel(SUGGESTIONS_CHANNEL_ID)
                if channel:
                    await channel.send(embed=embed)
                    log.info(f"Suggestion from {user.id} sent to channel {SUGGESTIONS_CHANNEL_ID}")
                else:
                    log.error(f"Suggestions channel {SUGGESTIONS_CHANNEL_ID} not found")
            except Exception as e:
                log.error(f"Failed to send suggestion to channel: {e}")
        
        # Optionally DM bot owners
        if NOTIFY_USERS:
            for user_id in NOTIFY_USERS:
                try:
                    owner = await self.bot.fetch_user(user_id)
                    if owner:
                        dm_embed = embed.copy()
                        dm_embed.title = "💡 New Suggestion Received"
                        await owner.send(embed=dm_embed)
                        log.info(f"Sent suggestion notification DM to {user_id}")
                except Exception as e:
                    log.error(f"Failed to DM user {user_id}: {e}")

    @app_commands.command()
    async def suggest(
        self,
        interaction: discord.Interaction,
        suggestion: Optional[str] = None,
        attachment: Optional[discord.Attachment] = None
    ):
        """
        Submit a suggestion to the bot owners

        Parameters
        ----------
        suggestion: str
            Your suggestion text (optional if using attachment)
        attachment: discord.Attachment
            Optional file/image to include with your suggestion
        """
        
        # Check if SUGGESTIONS_CHANNEL_ID is configured
        if not SUGGESTIONS_CHANNEL_ID:
            await interaction.response.send_message(
                "❌ Suggestions are not currently configured. Contact a bot admin.",
                ephemeral=True
            )
            log.error("SUGGESTIONS_CHANNEL_ID not configured in suggestions package")
            return
        
        # If no text provided, show modal
        if not suggestion:
            modal = SuggestionModal(self)
            await interaction.response.send_modal(modal)
            return
        
        # If text was provided directly
        await interaction.response.defer(ephemeral=True)
        
        # Get player data
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        suggestions_count = player.extra_data.get("suggestions_submitted", 0)
        player.extra_data["suggestions_submitted"] = suggestions_count + 1
        player.extra_data["last_suggestion_date"] = datetime.now().isoformat()
        await player.save()
        
        # Get attachment URL if provided
        attachment_url = attachment.url if attachment else None
        
        # Send suggestion
        await self.send_suggestion(
            interaction.user,
            suggestion,
            attachment_url,
            suggestions_count + 1
        )
        
        await interaction.followup.send(
            "✅ Thank you! Your suggestion has been submitted to the bot owners.",
            ephemeral=True
        )
        
        log.info(f"Suggestion submitted by {interaction.user.id}: {suggestion[:50]}...")
    
    @app_commands.command()
    async def mystats(self, interaction: discord.Interaction):
        """
        View your suggestion statistics
        """
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        
        suggestions_count = player.extra_data.get("suggestions_submitted", 0)
        last_suggestion = player.extra_data.get("last_suggestion_date")
        
        embed = discord.Embed(
            title="📊 Your Suggestion Stats",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="Total Suggestions",
            value=f"**{suggestions_count}** suggestion{'s' if suggestions_count != 1 else ''}",
            inline=True
        )
        
        if last_suggestion:
            # Parse ISO format datetime
            last_date = datetime.fromisoformat(last_suggestion)
            embed.add_field(
                name="Last Suggestion",
                value=f"<t:{int(last_date.timestamp())}:R>",
                inline=True
            )
        
        embed.set_author(
            name=interaction.user.name,
            icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
