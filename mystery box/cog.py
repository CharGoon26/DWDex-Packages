import discord
import logging
import random
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from discord import app_commands
from discord.ext import commands

from ballsdex.core.models import Player, Ball, BallInstance, balls
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.mysterybox")

# Set your timezone here - change this to whatever you want
# Examples: "UTC", "America/New_York", "Europe/London", "Asia/Tokyo", "America/Los_Angeles"
MYSTERY_BOX_TIMEZONE = "UTC"


class MysteryBox(commands.Cog):
    """
    Mystery Box system - Get a random ball every Monday at midnight
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    async def cog_load(self):
        """Called when the cog is loaded"""
        log.info(f"Mystery Box cog loaded successfully (Timezone: {MYSTERY_BOX_TIMEZONE})")

    def cog_unload(self):
        log.info("Mystery Box cog unloaded")

    async def has_claimed_this_week(self, player: Player) -> bool:
        """
        Check if player has already claimed their mystery box this week
        """
        try:
            import pytz
            tz = pytz.timezone(MYSTERY_BOX_TIMEZONE)
            now = datetime.now(tz)
            
            # Calculate Monday of this week (start of week)
            days_since_monday = now.weekday()
            monday = now - timedelta(days=days_since_monday)
            monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Check if any ball was caught today (Monday)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_balls = await BallInstance.filter(
                player=player,
                catch_date__gte=today_start
            ).count()
            
            # If they have balls caught today, they've already claimed
            if today_balls > 0:
                return True
                    
            return False
        except Exception as e:
            log.error(f"Error checking claim status for player {player.discord_id}: {e}")
            return False

    @app_commands.command()
    async def mysterybox(self, interaction: discord.Interaction):
        """
        Open your weekly mystery box (only available on Mondays)
        """
        await interaction.response.defer(ephemeral=True)
        
        player, created = await Player.get_or_create(discord_id=interaction.user.id)
        
        import pytz
        tz = pytz.timezone(MYSTERY_BOX_TIMEZONE)
        current_time = datetime.now(tz)
        
        # Check if it's Monday (0 = Monday)
        if current_time.weekday() != 0:
            # Not Monday - show when next box is available
            days_until_monday = (7 - current_time.weekday()) % 7
            if days_until_monday == 0:
                days_until_monday = 7
            
            next_monday = current_time + timedelta(days=days_until_monday)
            next_monday = next_monday.replace(hour=0, minute=0, second=0, microsecond=0)
            
            embed = discord.Embed(
                title="📅 Not Monday Yet!",
                description=f"Mystery boxes are only available on Mondays!",
                color=discord.Color.orange()
            )
            
            embed.add_field(
                name="Current Day",
                value=f"📆 {current_time.strftime('%A, %B %d')}",
                inline=True
            )
            
            embed.add_field(
                name="Next Mystery Box",
                value=f"🎁 <t:{int(next_monday.timestamp())}:R>",
                inline=True
            )
            
            embed.set_footer(text=f"Server Time: {MYSTERY_BOX_TIMEZONE}")
            
            await interaction.followup.send(embed=embed, ephemeral=False)
            return
        
        # It's Monday! Check if they already claimed
        has_claimed = await self.has_claimed_this_week(player)
        
        if has_claimed:
            # Already claimed this week
            next_monday = current_time + timedelta(days=7)
            next_monday = next_monday.replace(hour=0, minute=0, second=0, microsecond=0)
            
            embed = discord.Embed(
                title="⏰ Already Claimed!",
                description="You've already opened your mystery box this week!",
                color=discord.Color.red()
            )
            
            embed.add_field(
                name="Next Mystery Box",
                value=f"🎁 <t:{int(next_monday.timestamp())}:R>",
                inline=False
            )
            
            embed.set_footer(text="Come back next Monday for another mystery box!")
            
            await interaction.followup.send(embed=embed, ephemeral=False)
            return
        
        # They can claim! Give them a random ball
        try:
            # Get a random ball
            all_balls = list(balls.values())
            if not all_balls:
                await interaction.followup.send("❌ No balls available. Please contact an administrator.", ephemeral=True)
                return
            
            random_ball = random.choice(all_balls)
            
            # Generate random stats
            is_shiny = random.randint(1, 2048) == 1  # 1/2048 chance
            attack_bonus = random.randint(-20, 20)
            health_bonus = random.randint(-20, 20)
            
            # Create the ball instance
            ball_instance = await BallInstance.create(
                ball=random_ball,
                player=player,
                shiny=is_shiny,
                attack_bonus=attack_bonus,
                health_bonus=health_bonus,
            )
            
            # Create success embed with ball details
            embed = discord.Embed(
                title="🎁 Mystery Box Opened!",
                description=f"**Congratulations!** You received:",
                color=discord.Color.gold() if is_shiny else discord.Color.purple()
            )
            
            # Ball name with shiny indicator
            ball_name = f"✨ **{random_ball.country}** ✨" if is_shiny else f"**{random_ball.country}**"
            embed.add_field(
                name="Ball",
                value=ball_name,
                inline=False
            )
            
            # Stats
            attack_display = f"+{attack_bonus}" if attack_bonus >= 0 else str(attack_bonus)
            health_display = f"+{health_bonus}" if health_bonus >= 0 else str(health_bonus)
            
            embed.add_field(
                name="⚔️ Attack Bonus",
                value=attack_display,
                inline=True
            )
            embed.add_field(
                name="❤️ Health Bonus",
                value=health_display,
                inline=True
            )
            
            if is_shiny:
                embed.add_field(
                    name="✨ Special",
                    value="**SHINY BALL!** This is a rare find!",
                    inline=False
                )
            
            # Add ball image if available
            try:
                image_url = random_ball.get_image_url()
                if image_url:
                    embed.set_thumbnail(url=image_url)
            except:
                pass
            
            # Calculate next Monday
            next_monday = current_time + timedelta(days=7)
            next_monday = next_monday.replace(hour=0, minute=0, second=0, microsecond=0)
            
            embed.add_field(
                name="📅 Next Mystery Box",
                value=f"<t:{int(next_monday.timestamp())}:R>",
                inline=False
            )
            
            embed.set_footer(text=f"Server Time: {MYSTERY_BOX_TIMEZONE}")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            log.info(f"Player {player.discord_id} opened mystery box and received {random_ball.country} (shiny: {is_shiny})")
            
        except Exception as e:
            log.error(f"Error opening mystery box for player {player.discord_id}: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ An error occurred while opening your mystery box. Please try again.",
                ephemeral=True
            )
