import asyncio  # Add this at the top of the file
import logging
import random
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from ballsdex.core.models import Ball, BallInstance, Player
from ballsdex.core.models import balls as countryballs
from ballsdex.settings import settings

from ballsdex.packages.battle.xe_battle_lib_v2 import (
    BattleInstance,
    TurnAction,
    MOVES,
    create_battle_from_instances
)

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.battle")

# Store active battles per guild
active_battles = {}  # guild_id: {"battle": BattleInstance, "p1_id": int, "p2_id": int, "message": Message, "expires_at": datetime}

# Store cooldowns per user
battle_cooldowns = {}  # user_id: datetime


class BattleMoveView(discord.ui.View):
    """Interactive view for selecting moves during battle"""
    
    def __init__(self, battle: BattleInstance, player_name: str):
        super().__init__(timeout=60)
        self.battle = battle
        self.player_name = player_name
        self.selected_move = None
    
    @discord.ui.button(label="Quick Attack", emoji="⚔️", style=discord.ButtonStyle.primary)
    async def attack_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_move_selection(interaction, "attack")
    
    @discord.ui.button(label="Heavy Strike", emoji="💪", style=discord.ButtonStyle.danger)
    async def heavy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_move_selection(interaction, "heavy")
    
    @discord.ui.button(label="Defend", emoji="🛡️", style=discord.ButtonStyle.secondary)
    async def defend_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_move_selection(interaction, "defend")
    
    @discord.ui.button(label="Recover", emoji="💚", style=discord.ButtonStyle.success)
    async def heal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_move_selection(interaction, "heal")
    
    async def handle_move_selection(self, interaction: discord.Interaction, move_key: str):
        """Handle when a player selects a move"""
        # Verify it's the right player
        if interaction.user.name != self.player_name:
            await interaction.response.send_message(
                "❌ This isn't your battle turn!",
                ephemeral=True
            )
            return
        
        self.selected_move = move_key
        move = MOVES[move_key]
        
        await interaction.response.send_message(
            f"✅ You selected: {move.emoji} **{move.name}**\nWaiting for opponent...",
            ephemeral=True
        )
        
        self.stop()


def create_battle_embed(battle: BattleInstance, title: str = "⚔️ Battle in Progress") -> discord.Embed:
    """Create an embed showing the current battle state"""
    embed = discord.Embed(
        title=title,
        description=f"**{battle.p1_name}** vs **{battle.p2_name}**\nTurn: {battle.current_turn}",
        color=discord.Color.red()
    )
    
    # Player 1's active ball
    p1_ball = battle.get_active_ball(battle.p1_name)
    if p1_ball:
        hp_bar = create_hp_bar(p1_ball.health, p1_ball.max_health)
        embed.add_field(
            name=f"{battle.p1_name}'s {p1_ball.name}",
            value=f"{hp_bar}\n⚔️ ATK: {p1_ball.attack} | 💚 HP: {p1_ball.health}/{p1_ball.max_health}",
            inline=True
        )
    
    # VS divider
    embed.add_field(name="\u200b", value="**VS**", inline=True)
    
    # Player 2's active ball
    p2_ball = battle.get_active_ball(battle.p2_name)
    if p2_ball:
        hp_bar = create_hp_bar(p2_ball.health, p2_ball.max_health)
        embed.add_field(
            name=f"{battle.p2_name}'s {p2_ball.name}",
            value=f"{hp_bar}\n⚔️ ATK: {p2_ball.attack} | 💚 HP: {p2_ball.health}/{p2_ball.max_health}",
            inline=True
        )
    
    # Show remaining team members
    p1_alive = sum(1 for ball in battle.p1_balls if not ball.dead)
    p2_alive = sum(1 for ball in battle.p2_balls if not ball.dead)
    
    embed.add_field(
        name=f"{battle.p1_name}'s Team",
        value=f"{'🟢' * p1_alive}{'🔴' * (3 - p1_alive)}",
        inline=True
    )
    
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    
    embed.add_field(
        name=f"{battle.p2_name}'s Team",
        value=f"{'🟢' * p2_alive}{'🔴' * (3 - p2_alive)}",
        inline=True
    )
    
    return embed


def create_hp_bar(current_hp: int, max_hp: int, length: int = 10) -> str:
    """Create a visual HP bar"""
    if max_hp <= 0:
        return "❌"
    
    percentage = current_hp / max_hp
    filled = int(length * percentage)
    empty = length - filled
    
    return f"{'🟩' * filled}{'⬜' * empty}"


def check_cooldown(user_id: int) -> Optional[timedelta]:
    """Check if user is on cooldown, return remaining time or None"""
    if user_id in battle_cooldowns:
        cooldown_end = battle_cooldowns[user_id]
        now = datetime.now()
        if now < cooldown_end:
            return cooldown_end - now
    return None


def set_cooldown(user_id: int, hours: int = 1):
    """Set a cooldown for a user"""
    battle_cooldowns[user_id] = datetime.now() + timedelta(hours=hours)


def check_expired_battles():
    """Clean up expired battles"""
    now = datetime.now()
    expired_guilds = [
        guild_id for guild_id, battle_data in active_battles.items()
        if "expires_at" in battle_data and now > battle_data["expires_at"]
    ]
    
    for guild_id in expired_guilds:
        log.info(f"Cleaning up expired battle in guild {guild_id}")
        del active_battles[guild_id]
    
    return len(expired_guilds)


class Battle(commands.GroupCog, group_name="battle"):
    """
    Interactive turn-based battles with your cards!
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @app_commands.command()
    async def challenge(self, interaction: discord.Interaction, opponent: discord.Member):
        """
        Challenge another player to a battle!
        
        Parameters
        ----------
        opponent: discord.Member
            The player you want to battle
        """
        # Clean up any expired battles first
        check_expired_battles()
        
        # Check if challenger is on cooldown
        cooldown = check_cooldown(interaction.user.id)
        if cooldown:
            minutes = int(cooldown.total_seconds() / 60)
            await interaction.response.send_message(
                f"⏰ You're on cooldown! Try again in {minutes} minutes.",
                ephemeral=True
            )
            return
        
        # Check if opponent is on cooldown
        cooldown = check_cooldown(opponent.id)
        if cooldown:
            minutes = int(cooldown.total_seconds() / 60)
            await interaction.response.send_message(
                f"⏰ {opponent.mention} is on cooldown! They can battle again in {minutes} minutes.",
                ephemeral=True
            )
            return
        
        # Can't battle yourself
        if opponent.id == interaction.user.id:
            await interaction.response.send_message(
                "❌ You can't battle yourself!",
                ephemeral=True
            )
            return
        
        # Can't battle bots
        if opponent.bot:
            await interaction.response.send_message(
                "❌ You can't battle bots!",
                ephemeral=True
            )
            return
        
        # Check if there's already an active battle in this guild
        if interaction.guild_id in active_battles:
            await interaction.response.send_message(
                "❌ There's already a battle happening in this server! Wait for it to finish.",
                ephemeral=True
            )
            return
        
        # Get both players' data
        challenger_player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        opponent_player, _ = await Player.get_or_create(discord_id=opponent.id)
        
        # Check if challenger has at least 3 balls
        challenger_balls = await BallInstance.filter(
            player=challenger_player,
            deleted=False
        ).count()
        
        if challenger_balls < 3:
            await interaction.response.send_message(
                f"❌ You need at least 3 {settings.plural_collectible_name} to battle!",
                ephemeral=True
            )
            return
        
        # Check if opponent has at least 3 balls
        opponent_balls_count = await BallInstance.filter(
            player=opponent_player,
            deleted=False
        ).count()
        
        if opponent_balls_count < 3:
            await interaction.response.send_message(
                f"❌ {opponent.mention} needs at least 3 {settings.plural_collectible_name} to battle!",
                ephemeral=True
            )
            return
        
        # Create challenge embed
        embed = discord.Embed(
            title="⚔️ Battle Challenge!",
            description=(
                f"{interaction.user.mention} has challenged {opponent.mention} to a battle!\n\n"
                f"**Rules:**\n"
                f"• Each player must select exactly 3 {settings.plural_collectible_name}\n"
                f"• Use `/battle best` to auto-fill your 3 strongest\n"
                f"• Use `/battle add <card>` to add specific cards\n"
                f"• Use `/battle remove <card>` to remove cards\n"
                f"• Once both players have 3 cards and click Ready, battle begins!\n"
                f"• Winner gets progress toward rewards!"
            ),
            color=discord.Color.gold()
        )
        
        embed.add_field(
            name=f"{interaction.user.name}'s Team",
            value="Empty (0/3)",
            inline=True
        )
        
        embed.add_field(
            name=f"{opponent.name}'s Team",
            value="Empty (0/3)",
            inline=True
        )
        
        # Create accept/decline view
        view = discord.ui.View(timeout=60)
        
        async def accept_callback(button_interaction: discord.Interaction):
            if button_interaction.user.id != opponent.id:
                await button_interaction.response.send_message(
                    "❌ Only the challenged player can accept!",
                    ephemeral=True
                )
                return
            
            # Initialize battle with 5 minute expiration for setup phase
            battle = BattleInstance(
                p1_name=interaction.user.name,
                p2_name=opponent.name,
                p1_balls=[],
                p2_balls=[]
            )
            
            # Get the message from the button interaction
            message = button_interaction.message
            
            active_battles[interaction.guild_id] = {
                "battle": battle,
                "p1_id": interaction.user.id,
                "p2_id": opponent.id,
                "message": message,
                "expires_at": datetime.now() + timedelta(minutes=5)  # 5 minute expiration
            }
            
            embed.description = (
                f"⚔️ Battle accepted! Both players, add your 3 {settings.plural_collectible_name}!\n\n"
                f"Use `/battle best` to auto-fill or `/battle add <card>` for specific cards.\n"
                f"⏰ **You have 5 minutes to add your cards!**"
            )
            embed.color = discord.Color.green()
            
            for item in view.children:
                item.disabled = True
            
            await button_interaction.response.edit_message(embed=embed, view=view)
        
        async def decline_callback(button_interaction: discord.Interaction):
            if button_interaction.user.id != opponent.id:
                await button_interaction.response.send_message(
                    "❌ Only the challenged player can decline!",
                    ephemeral=True
                )
                return
            
            embed.description = f"❌ {opponent.mention} declined the battle challenge."
            embed.color = discord.Color.red()
            
            for item in view.children:
                item.disabled = True
            
            await button_interaction.response.edit_message(embed=embed, view=view)
        
        accept_button = discord.ui.Button(label="Accept", style=discord.ButtonStyle.success, emoji="✅")
        accept_button.callback = accept_callback
        
        decline_button = discord.ui.Button(label="Decline", style=discord.ButtonStyle.danger, emoji="❌")
        decline_button.callback = decline_callback
        
        view.add_item(accept_button)
        view.add_item(decline_button)
        
        await interaction.response.send_message(
            content=f"{opponent.mention}, you've been challenged!",
            embed=embed,
            view=view
        )
    
    @app_commands.command()
    async def stats(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        """
        View battle statistics
        
        Parameters
        ----------
        user: discord.Member
            The user to check stats for (defaults to yourself)
        """
        target = user or interaction.user
        player, _ = await Player.get_or_create(discord_id=target.id)
        
        # Get stats from extra_data
        wins = player.extra_data.get("battle_wins", 0)
        losses = player.extra_data.get("battle_losses", 0)
        total_battles = wins + losses
        win_rate = (wins / total_battles * 100) if total_battles > 0 else 0
        
        # Calculate rewards progress - CHANGED: 3 wins per reward instead of 5
        rewards_claimed = player.extra_data.get("battle_rewards_claimed", 0)
        rewards_available = wins // 3
        wins_until_reward = 3 - (wins % 3)
        
        embed = discord.Embed(
            title=f"⚔️ {target.name}'s Battle Stats",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="📊 Record",
            value=f"**Wins:** {wins}\n**Losses:** {losses}\n**Win Rate:** {win_rate:.1f}%",
            inline=True
        )
        
        embed.add_field(
            name="🎁 Rewards",
            value=f"**Total Earned:** {rewards_claimed}\n**Available:** {rewards_available - rewards_claimed}\n**Progress:** {wins % 3}/3 wins\n**Next Reward:** {wins_until_reward} wins away",
            inline=True
        )
        
        # Last battle info
        last_battle = player.extra_data.get("last_battle_result")
        if last_battle:
            result_emoji = "🏆" if last_battle.get("won") else "💔"
            embed.add_field(
                name="📝 Last Battle",
                value=f"{result_emoji} vs {last_battle.get('opponent', 'Unknown')}",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command()
    async def redeem(self, interaction: discord.Interaction):
        """
        Redeem a reward card (requires 3 wins, claimable every 3 wins) - Only cards with rarity ≤55% (excluding 0%)
        """
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        
        wins = player.extra_data.get("battle_wins", 0)
        rewards_available = wins // 3  # CHANGED: 3 wins per reward instead of 5
        rewards_claimed = player.extra_data.get("battle_rewards_claimed", 0)
        
        # Check if there are any unclaimed rewards
        if rewards_claimed >= rewards_available:
            wins_needed = 3 - (wins % 3)
            await interaction.response.send_message(
                f"❌ You don't have any rewards to claim!\n"
                f"Win {wins_needed} more battle{'s' if wins_needed != 1 else ''} to earn your next reward (rewards are claimable every 3 wins).",
                ephemeral=True
            )
            return
        
        # Get all rarities and calculate 55th percentile (top 55% rarity)
        # Exclude cards with 0% rarity (limited edition cards)
        all_rarities = [ball.rarity for ball in countryballs.values() if ball.enabled and ball.rarity > 0]
        
        if not all_rarities:
            await interaction.response.send_message(
                "❌ No balls available for rewards. Contact an admin.",
                ephemeral=True
            )
            return
        
        sorted_rarities = sorted(all_rarities)
        # Top 55% = rarity values in bottom 55% of the sorted list (lower rarity = rarer)
        cutoff_index = int(len(sorted_rarities) * 0.55)
        max_rarity = sorted_rarities[cutoff_index] if cutoff_index < len(sorted_rarities) else sorted_rarities[-1]
        
        # Get eligible balls (rarity > 0 and rarity <= 55th percentile = top 55% rarest)
        eligible_balls = [
            ball for ball in countryballs.values()
            if ball.enabled and ball.rarity > 0 and ball.rarity <= max_rarity
        ]
        
        if not eligible_balls:
            await interaction.response.send_message(
                "❌ No eligible balls available for rewards. Contact an admin.",
                ephemeral=True
            )
            return
        
        random_ball = random.choice(eligible_balls)
        
        # Create ball instance
        ball_instance = await BallInstance.create(
            ball=random_ball,
            player=player,
            attack_bonus=random.randint(-settings.max_attack_bonus, settings.max_attack_bonus),
            health_bonus=random.randint(-settings.max_health_bonus, settings.max_health_bonus),
        )
        
        # Update rewards claimed
        player.extra_data["battle_rewards_claimed"] = rewards_claimed + 1
        await player.save()
        
        # Calculate remaining rewards
        remaining_rewards = rewards_available - (rewards_claimed + 1)
        
        embed = discord.Embed(
            title="🎁 Battle Reward Claimed!",
            description=f"Congratulations! You received a rare card:",
            color=discord.Color.gold()
        )
        
        embed.add_field(
            name="Card",
            value=f"**{random_ball.country}**",
            inline=False
        )
        
        embed.add_field(
            name="Stats",
            value=f"⚔️ ATK: {ball_instance.attack_bonus:+}%\n💚 HP: {ball_instance.health_bonus:+}%",
            inline=True
        )
        
        embed.add_field(
            name="Rarity",
            value=f"Top 55% rarest (rarity: {random_ball.rarity:.2%})",
            inline=True
        )
        
        if remaining_rewards > 0:
            embed.add_field(
                name="Remaining Rewards",
                value=f"🎁 {remaining_rewards} reward{'s' if remaining_rewards != 1 else ''} left to claim!",
                inline=False
            )
        else:
            wins_to_next = 3 - (wins % 3)
            embed.add_field(
                name="Next Reward",
                value=f"Win {wins_to_next} more battle{'s' if wins_to_next != 1 else ''} to earn another reward!",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed)
        log.info(f"Player {player.discord_id} redeemed battle reward: {random_ball.country} (rarity: {random_ball.rarity})")
    
    @app_commands.command()
    async def best(self, interaction: discord.Interaction):
        """
        Automatically add your 3 strongest cards to the battle
        """
        # Clean up expired battles first
        check_expired_battles()
        
        # Check if there's an active battle in this guild
        if interaction.guild_id not in active_battles:
            await interaction.response.send_message(
                "❌ There's no active battle setup! Use `/battle challenge` first.",
                ephemeral=True
            )
            return
        
        battle_data = active_battles[interaction.guild_id]
        
        # Check if battle has expired
        if "expires_at" in battle_data and datetime.now() > battle_data["expires_at"]:
            await interaction.response.send_message(
                "❌ This battle has expired! Start a new one with `/battle challenge`.",
                ephemeral=True
            )
            del active_battles[interaction.guild_id]
            return
        
        battle = battle_data["battle"]
        
        # Check if user is part of this battle
        if interaction.user.id not in (battle_data["p1_id"], battle_data["p2_id"]):
            await interaction.response.send_message(
                "❌ You're not part of this battle!",
                ephemeral=True
            )
            return
        
        # Check if already has 3 balls
        is_p1 = interaction.user.id == battle_data["p1_id"]
        current_balls = battle.p1_balls if is_p1 else battle.p2_balls
        
        if len(current_balls) >= 3:
            await interaction.response.send_message(
                "❌ You already have 3 cards selected!",
                ephemeral=True
            )
            return
        
        # Get player's strongest 3 balls
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        
        # Get balls sorted by total stats (attack + health)
        balls = await BallInstance.filter(
            player=player,
            deleted=False
        ).prefetch_related("ball")
        
        # Sort by combined stats
        sorted_balls = sorted(
            balls,
            key=lambda b: b.attack + b.health,
            reverse=True
        )[:3]
        
        if len(sorted_balls) < 3:
            await interaction.response.send_message(
                f"❌ You need at least 3 {settings.plural_collectible_name} to battle!",
                ephemeral=True
            )
            return
        
        # Add balls to battle
        from ballsdex.packages.battle.xe_battle_lib_v2 import BattleBall
        
        for ball_inst in sorted_balls:
            battle_ball = BattleBall(
                name=ball_inst.ball.country,
                owner=interaction.user.name,
                health=ball_inst.health,
                attack=ball_inst.attack,
                max_health=ball_inst.health,
                emoji=""
            )
            current_balls.append(battle_ball)
        
        # Try to update message
        try:
            await self._update_battle_setup_message(interaction, battle_data)
        except Exception as e:
            log.error(f"Failed to update battle setup message: {e}")
        
        await interaction.response.send_message(
            f"✅ Added your 3 strongest {settings.plural_collectible_name}!",
            ephemeral=True
        )
    
    @app_commands.command()
    async def add(self, interaction: discord.Interaction):
        """
        Add a card to your battle team using a dropdown menu
        """
        # Clean up expired battles first
        check_expired_battles()
        
        # Check if there's an active battle in this guild
        if interaction.guild_id not in active_battles:
            await interaction.response.send_message(
                "❌ There's no active battle setup! Use `/battle challenge` first.",
                ephemeral=True
            )
            return
        
        battle_data = active_battles[interaction.guild_id]
        
        # Check if battle has expired
        if "expires_at" in battle_data and datetime.now() > battle_data["expires_at"]:
            await interaction.response.send_message(
                "❌ This battle has expired! Start a new one with `/battle challenge`.",
                ephemeral=True
            )
            del active_battles[interaction.guild_id]
            return
        
        battle = battle_data["battle"]
        
        # Check if user is part of this battle
        if interaction.user.id not in (battle_data["p1_id"], battle_data["p2_id"]):
            await interaction.response.send_message(
                "❌ You're not part of this battle!",
                ephemeral=True
            )
            return
        
        # Check if already has 3 balls
        is_p1 = interaction.user.id == battle_data["p1_id"]
        current_balls = battle.p1_balls if is_p1 else battle.p2_balls
        
        if len(current_balls) >= 3:
            await interaction.response.send_message(
                "❌ You already have 3 cards selected! Use `/battle remove` to remove a card first.",
                ephemeral=True
            )
            return
        
        # Get player
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        
        # Get user's balls from database
        user_balls = await BallInstance.filter(player=player, deleted=False).prefetch_related("ball")
        
        if not user_balls:
            await interaction.response.send_message(
                "❌ You don't have any balls to add to your team!",
                ephemeral=True
            )
            return
        
        # Create dropdown with user's balls (limit to 25 options as per Discord API)
        class BallSelectView(discord.ui.View):
            def __init__(self, balls_list, max_options=25):
                super().__init__(timeout=60)
                self.selected_ball = None
                
                # Create select menu
                select = discord.ui.Select(
                    placeholder="Choose a ball to add to your team",
                    min_values=1,
                    max_values=1
                )
                
                # Add options (limit to 25)
                for ball in balls_list[:max_options]:
                    # Create option label with ball info
                    label = f"{ball.ball.country}"
                    if len(label) > 100:
                        label = label[:97] + "..."
                    
                    select.add_option(
                        label=label,
                        value=str(ball.pk),
                        description=f"ID: {ball.pk} | ATK: {ball.attack} | HP: {ball.health}"
                    )
                
                async def select_callback(select_interaction: discord.Interaction):
                    self.selected_ball = select.values[0]
                    await select_interaction.response.send_message(
                        f"✅ Ball added to your team!",
                        ephemeral=True
                    )
                    self.stop()
                
                select.callback = select_callback
                self.add_item(select)
        
        # Show the dropdown
        view = BallSelectView(user_balls)
        await interaction.response.send_message(
            "🎯 Select a ball to add to your battle team:",
            view=view,
            ephemeral=True
        )
        
        # Wait for selection
        await view.wait()
        
        if not view.selected_ball:
            return  # User didn't select anything (timeout)
        
        # Get the selected ball
        ball_id = int(view.selected_ball)
        ball_instance = await BallInstance.get(pk=ball_id).prefetch_related("ball")
        
        # Check if ball is already in team
        if any(b.name == ball_instance.ball.country for b in current_balls):
            await interaction.followup.send(
                f"❌ You already have **{ball_instance.ball.country}** in your team!",
                ephemeral=True
            )
            return
        
        # Add ball to battle
        from ballsdex.packages.battle.xe_battle_lib_v2 import BattleBall
        
        battle_ball = BattleBall(
            name=ball_instance.ball.country,
            owner=interaction.user.name,
            health=ball_instance.health,
            attack=ball_instance.attack,
            max_health=ball_instance.health,
            emoji=""
        )
        current_balls.append(battle_ball)
        
        # Try to update message
        try:
            await self._update_battle_setup_message(interaction, battle_data)
        except Exception as e:
            log.error(f"Failed to update battle setup message: {e}")
    
    @app_commands.command()
    async def remove(self, interaction: discord.Interaction):
        """
        Remove a card from your battle team using a dropdown menu
        """
        # Clean up expired battles first
        check_expired_battles()
        
        # Check if there's an active battle in this guild
        if interaction.guild_id not in active_battles:
            await interaction.response.send_message(
                "❌ There's no active battle setup! Use `/battle challenge` first.",
                ephemeral=True
            )
            return
        
        battle_data = active_battles[interaction.guild_id]
        
        # Check if battle has expired
        if "expires_at" in battle_data and datetime.now() > battle_data["expires_at"]:
            await interaction.response.send_message(
                "❌ This battle has expired! Start a new one with `/battle challenge`.",
                ephemeral=True
            )
            del active_battles[interaction.guild_id]
            return
        
        battle = battle_data["battle"]
        
        # Check if user is part of this battle
        if interaction.user.id not in (battle_data["p1_id"], battle_data["p2_id"]):
            await interaction.response.send_message(
                "❌ You're not part of this battle!",
                ephemeral=True
            )
            return
        
        # Get current balls
        is_p1 = interaction.user.id == battle_data["p1_id"]
        current_balls = battle.p1_balls if is_p1 else battle.p2_balls
        
        if len(current_balls) == 0:
            await interaction.response.send_message(
                "❌ You don't have any cards in your team!",
                ephemeral=True
            )
            return
        
        # Create dropdown with current team balls
        class BallRemoveView(discord.ui.View):
            def __init__(self, balls_list):
                super().__init__(timeout=60)
                self.selected_index = None
                
                # Create select menu
                select = discord.ui.Select(
                    placeholder="Choose a ball to remove from your team",
                    min_values=1,
                    max_values=1
                )
                
                # Add options for each ball in team
                for i, ball in enumerate(balls_list):
                    select.add_option(
                        label=f"{ball.name}",
                        value=str(i),
                        description=f"ATK: {ball.attack} | HP: {ball.health}/{ball.max_health}"
                    )
                
                async def select_callback(select_interaction: discord.Interaction):
                    self.selected_index = int(select.values[0])
                    await select_interaction.response.send_message(
                        f"✅ Ball removed from your team!",
                        ephemeral=True
                    )
                    self.stop()
                
                select.callback = select_callback
                self.add_item(select)
        
        # Show the dropdown
        view = BallRemoveView(current_balls)
        await interaction.response.send_message(
            "🎯 Select a ball to remove from your battle team:",
            view=view,
            ephemeral=True
        )
        
        # Wait for selection
        await view.wait()
        
        if view.selected_index is None:
            return  # User didn't select anything (timeout)
        
        # Remove the ball
        removed_ball = current_balls.pop(view.selected_index)
        
        # Try to update message
        try:
            await self._update_battle_setup_message(interaction, battle_data)
        except Exception as e:
            log.error(f"Failed to update battle setup message: {e}")
    
    async def _update_battle_setup_message(self, interaction: discord.Interaction, battle_data: dict):
        """Update the battle setup message"""
        battle = battle_data["battle"]
        message = battle_data.get("message")
        
        # Safety check - if message is None, we can't update it
        if message is None:
            log.warning("Battle setup message is None, cannot update")
            return
        
        # Get player names
        p1 = await self.bot.fetch_user(battle_data["p1_id"])
        p2 = await self.bot.fetch_user(battle_data["p2_id"])
        
        p1_count = len(battle.p1_balls)
        p2_count = len(battle.p2_balls)
        
        # Calculate time remaining
        time_remaining = ""
        if "expires_at" in battle_data:
            remaining = battle_data["expires_at"] - datetime.now()
            if remaining.total_seconds() > 0:
                minutes = int(remaining.total_seconds() / 60)
                seconds = int(remaining.total_seconds() % 60)
                time_remaining = f"\n⏰ **Time remaining: {minutes}m {seconds}s**"
        
        # Create updated embed
        embed = discord.Embed(
            title="⚔️ Battle Setup",
            description=f"Both players are selecting their teams!\nClick Ready when you have 3 cards.{time_remaining}",
            color=discord.Color.gold()
        )
        
        # Show team compositions
        p1_team_text = "\n".join([f"• {ball.name} (ATK: {ball.attack}, HP: {ball.health})" for ball in battle.p1_balls])
        if not p1_team_text:
            p1_team_text = "Empty"
        
        p2_team_text = "\n".join([f"• {ball.name} (ATK: {ball.attack}, HP: {ball.health})" for ball in battle.p2_balls])
        if not p2_team_text:
            p2_team_text = "Empty"
        
        embed.add_field(
            name=f"{p1.name}'s Team ({p1_count}/3)" + (" ✅" if p1_count == 3 and battle.p1_ready else ""),
            value=p1_team_text[:1024],
            inline=True
        )
        
        embed.add_field(
            name=f"{p2.name}'s Team ({p2_count}/3)" + (" ✅" if p2_count == 3 and battle.p2_ready else ""),
            value=p2_team_text[:1024],
            inline=True
        )
        
        # Create ready button if both have 3 balls
        if p1_count == 3 and p2_count == 3:
            view = discord.ui.View(timeout=120)
            
            async def ready_callback(button_interaction: discord.Interaction):
                # Check if user is part of battle
                if button_interaction.user.id == battle_data["p1_id"]:
                    battle.p1_ready = True
                elif button_interaction.user.id == battle_data["p2_id"]:
                    battle.p2_ready = True
                else:
                    await button_interaction.response.send_message(
                        "❌ You're not part of this battle!",
                        ephemeral=True
                    )
                    return
                
                # Check if both ready
                if battle.p1_ready and battle.p2_ready:
                    # Start battle!
                    await self._start_interactive_battle(button_interaction, battle_data)
                else:
                    await button_interaction.response.send_message(
                        "✅ You're ready! Waiting for opponent...",
                        ephemeral=True
                    )
                    await self._update_battle_setup_message(button_interaction, battle_data)
            
            ready_button = discord.ui.Button(label="Ready!", style=discord.ButtonStyle.success, emoji="✅")
            ready_button.callback = ready_callback
            view.add_item(ready_button)
            
            await message.edit(embed=embed, view=view)
        else:
            await message.edit(embed=embed, view=None)
    
    async def _start_interactive_battle(self, interaction: discord.Interaction, battle_data: dict):
        """Start the interactive turn-based battle"""
        battle = battle_data["battle"]
        
        # Remove expiration time once battle starts
        if "expires_at" in battle_data:
            del battle_data["expires_at"]
        
        # Set cooldowns for both players
        set_cooldown(battle_data["p1_id"], hours=1)
        set_cooldown(battle_data["p2_id"], hours=1)
        
        # Initial battle embed
        embed = create_battle_embed(battle, title="⚔️ Battle Started!")
        embed.description += "\n\n**Both players, select your first move!**"
        
        await interaction.response.edit_message(embed=embed, view=None)
        
        # Start turn loop
        await self._battle_turn_loop(interaction, battle_data)
    
    async def _battle_turn_loop(self, interaction: discord.Interaction, battle_data: dict):
        """Main battle loop - handle turns until battle ends"""
        battle = battle_data["battle"]
        channel = interaction.channel
        
        while not battle.is_battle_over():
            # Send turn prompt to both players
            embed = create_battle_embed(battle)
            embed.description += f"\n\n**Turn {battle.current_turn + 1} - Select your moves!**"
            
            message = await channel.send(embed=embed)
            
            # Get move selections from both players
            p1_view = BattleMoveView(battle, battle.p1_name)
            p2_view = BattleMoveView(battle, battle.p2_name)
            
            # Send DM or ephemeral messages for move selection
            p1_user = await self.bot.fetch_user(battle_data["p1_id"])
            p2_user = await self.bot.fetch_user(battle_data["p2_id"])
            
            move_embed = discord.Embed(
                title=f"🎮 Your Turn - Turn {battle.current_turn + 1}",
                description="Select your move!",
                color=discord.Color.blue()
            )
            
            # Add move descriptions
            for key, move in MOVES.items():
                move_embed.add_field(
                    name=f"{move.emoji} {move.name}",
                    value=move.description,
                    inline=False
                )
            
            try:
                # Send the main battle status message WITHOUT buttons
                await message.edit(embed=embed, view=None)
                
                # Send player-specific messages WITH buttons
                p1_msg = await channel.send(f"{p1_user.mention}, select your move!", view=p1_view)
                
                # Wait for P1 to select
                await p1_view.wait()
                await p1_msg.delete()
                
                if not p1_view.selected_move:
                    # Timeout - random move
                    p1_view.selected_move = random.choice(list(MOVES.keys()))
                
                # P2's turn
                p2_msg = await channel.send(f"{p2_user.mention}, select your move!", view=p2_view)
                
                # Wait for P2 to select
                await p2_view.wait()
                await p2_msg.delete()
                
                if not p2_view.selected_move:
                    # Timeout - random move
                    p2_view.selected_move = random.choice(list(MOVES.keys()))
                
            except Exception as e:
                log.error(f"Error in battle turn: {e}")
                await channel.send("❌ An error occurred in the battle. Battle cancelled.")
                del active_battles[interaction.guild_id]
                return
            
            # Execute turn
            p1_action = TurnAction(battle.p1_name, 0, p1_view.selected_move)
            p2_action = TurnAction(battle.p2_name, 0, p2_view.selected_move)
            
            turn_result = battle.execute_turn(p1_action, p2_action)
            
            # Display turn results
            result_embed = create_battle_embed(battle, title=f"⚔️ Turn {battle.current_turn} Results")
            
            result_text = ""
            for event in turn_result["events"]:
                result_text += event.get("message", "") + "\n"
            
            result_embed.add_field(
                name="📝 What Happened",
                value=result_text[:1024] or "Nothing happened",
                inline=False
            )
            
            await channel.send(embed=result_embed)
            
            # Small delay for readability
            await asyncio.sleep(3)
        
        # Battle over - declare winner
        await self._end_battle(channel, battle_data)
    
    async def _end_battle(self, channel, battle_data: dict):
        """End the battle and update stats"""
        battle = battle_data["battle"]
        winner_name = battle.get_winner()
        
        # Determine winner and loser IDs
        if winner_name == battle.p1_name:
            winner_id = battle_data["p1_id"]
            loser_id = battle_data["p2_id"]
        elif winner_name == battle.p2_name:
            winner_id = battle_data["p2_id"]
            loser_id = battle_data["p1_id"]
        else:
            # Draw
            winner_id = None
            loser_id = None
        
        # Update stats
        if winner_id:
            winner_player, _ = await Player.get_or_create(discord_id=winner_id)
            loser_player, _ = await Player.get_or_create(discord_id=loser_id)
            
            # Update wins/losses
            winner_player.extra_data["battle_wins"] = winner_player.extra_data.get("battle_wins", 0) + 1
            winner_player.extra_data["last_battle_result"] = {
                "won": True,
                "opponent": battle.p2_name if winner_id == battle_data["p1_id"] else battle.p1_name
            }
            await winner_player.save()
            
            loser_player.extra_data["battle_losses"] = loser_player.extra_data.get("battle_losses", 0) + 1
            loser_player.extra_data["last_battle_result"] = {
                "won": False,
                "opponent": battle.p1_name if loser_id == battle_data["p2_id"] else battle.p2_name
            }
            await loser_player.save()
        
        # Create final embed
        embed = discord.Embed(
            title="🏆 Battle Complete!",
            description=f"**Winner: {winner_name}!**" if winner_name != "Draw" else "**It's a draw!**",
            color=discord.Color.gold() if winner_name != "Draw" else discord.Color.greyple()
        )
        
        embed.add_field(
            name="📊 Battle Stats",
            value=f"**Total Turns:** {battle.current_turn}\n**Duration:** {len(battle.turn_history)} exchanges",
            inline=False
        )
        
        if winner_id:
            winner_player = await Player.get_or_create(discord_id=winner_id)
            wins = winner_player[0].extra_data.get("battle_wins", 0)
            wins_to_reward = 3 - (wins % 3)  # CHANGED: 3 wins per reward
            
            # Show rewards info
            rewards_available = wins // 3  # CHANGED: 3 wins per reward
            rewards_claimed = winner_player[0].extra_data.get("battle_rewards_claimed", 0)
            unclaimed = rewards_available - rewards_claimed
            
            reward_text = f"{wins % 3}/3 wins"  # CHANGED: show progress out of 3
            if unclaimed > 0:
                reward_text += f"\n🎁 **{unclaimed} reward{'s' if unclaimed != 1 else ''} ready to claim!** Use `/battle redeem`"
            else:
                reward_text += f"\n{wins_to_reward} win{'s' if wins_to_reward != 1 else ''} until next reward!"
            
            embed.add_field(
                name="🎁 Reward Progress",
                value=reward_text,
                inline=False
            )
        
        await channel.send(embed=embed)
        
        # Clean up
        del active_battles[interaction.guild_id]

async def setup(bot: "BallsDexBot"):
    await bot.add_cog(Battle(bot))
