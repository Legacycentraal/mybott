import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import asyncio
import logging
import time
import signal
from keep_alive import keep_alive

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Bot configuration
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "your-token-here")
ACCOUNTS_FILE = "data/accounts.json"
INVITES_FILE = "data/invites.json"

# Signal handler for graceful shutdown
def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}")
    logger.info("Bot will continue running...")

# Register signal handlers
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# Initialize bot with required intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Cache to store invite counts
invite_cache = {}

# Enhanced JSON handling
def load_json(filename, default):
    try:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        try:
            with open(filename, 'r') as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    raise json.JSONDecodeError("Invalid JSON structure", "", 0)
                return data
        except (json.JSONDecodeError, FileNotFoundError):
            with open(filename, 'w') as f:
                json.dump(default, f, indent=4)
            return default
    except Exception as e:
        logger.error(f"Error loading JSON file {filename}: {e}")
        return default

def save_json(filename, data):
    if not isinstance(data, dict):
        logger.error(f"Invalid data type for JSON save: {type(data)}")
        raise ValueError("Data must be a dictionary")

    temp_file = f"{filename}.tmp"
    try:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        # Write to temporary file first
        with open(temp_file, 'w') as f:
            json.dump(data, f, indent=4)
        # Then rename it to the target file (atomic operation)
        os.replace(temp_file, filename)
    except Exception as e:
        logger.error(f"Error saving JSON file {filename}: {e}")
        if os.path.exists(temp_file):
            os.remove(temp_file)
        raise

def load_accounts():
    return load_json(ACCOUNTS_FILE, {"accounts": []})["accounts"]

def save_accounts(accounts):
    save_json(ACCOUNTS_FILE, {"accounts": accounts})

def load_invites():
    return load_json(INVITES_FILE, {})

def save_invites(invites):
    save_json(INVITES_FILE, invites)

def initialize_user_invites(invites, guild_id, user_id):
    """Initialize guild and user invite data if not exists"""
    guild_id = str(guild_id)
    user_id = str(user_id)

    if guild_id not in invites:
        invites[guild_id] = {}

    if user_id not in invites[guild_id]:
        invites[guild_id][user_id] = 0

    save_invites(invites)
    return invites

# Bot events
@bot.event
async def on_ready():
    logger.info(f"Bot is ready! Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")

    # Initialize invite cache for all guilds
    for guild in bot.guilds:
        try:
            invites = await guild.invites()
            invite_cache[guild.id] = {invite.code: invite.uses for invite in invites}
            logger.info(f"Cached {len(invites)} invites for guild {guild.name}")
        except discord.Forbidden:
            logger.warning(f"Missing permissions to fetch invites in {guild.name}")

@bot.event
async def on_guild_join(guild):
    try:
        invites = await guild.invites()
        invite_cache[guild.id] = {invite.code: invite.uses for invite in invites}
        logger.info(f"Cached invites for newly joined guild: {guild.name}")
    except discord.Forbidden:
        logger.warning(f"Missing permissions to fetch invites in {guild.name}")

@bot.event
async def on_invite_create(invite):
    if invite.guild.id not in invite_cache:
        invite_cache[invite.guild.id] = {}
    invite_cache[invite.guild.id][invite.code] = invite.uses
    logger.info(f"New invite created: {invite.code} in guild {invite.guild.name}")

@bot.event
async def on_member_join(member):
    logger.info(f"New member joined: {member.name} in guild {member.guild.name}")
    invites = load_invites()
    guild_id = str(member.guild.id)

    if guild_id not in invites:
        invites[guild_id] = {}
        logger.info(f"Created new invite tracking for guild {member.guild.name}")

    try:
        new_invites = await member.guild.invites()
        for invite in new_invites:
            old_uses = invite_cache[member.guild.id].get(invite.code, 0)
            if invite.uses > old_uses:
                inviter_id = str(invite.inviter.id)
                if inviter_id not in invites[guild_id]:
                    invites[guild_id][inviter_id] = 0
                invites[guild_id][inviter_id] += 1
                invite_cache[member.guild.id][invite.code] = invite.uses
                logger.info(f"Updated invite count for user {invite.inviter.name}: {invites[guild_id][inviter_id]}")
                break

        save_invites(invites)
    except discord.Forbidden:
        logger.error(f"Missing permissions to fetch invites in {member.guild.name}")

# Commands
@bot.tree.command(name="invites", description="Check your invite count")
async def check_invites(interaction: discord.Interaction):
    try:
        invites = load_invites()
        guild_id = str(interaction.guild_id)
        user_id = str(interaction.user.id)
        logger.info(f"Invite check by user {interaction.user.name}")

        invites = initialize_user_invites(invites, guild_id, user_id)
        invite_count = invites[guild_id][user_id]

        embed = discord.Embed(
            title="Invite Count",
            description=f"You have {invite_count} invite(s)!",
            color=0x2f3136
        )
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        logger.error(f"Error in check_invites: {e}")
        if not interaction.response.is_done():
            embed = discord.Embed(
                title="Error!",
                description="An error occurred while checking your invites.",
                color=0x2f3136
            )
            await interaction.response.send_message(embed=embed)

@bot.tree.command(name="claim", description="Claim an account using your invites")
async def claim(interaction: discord.Interaction):
    try:
        logger.info(f"Claim attempt by user {interaction.user.name}")
        invites = load_invites()
        guild_id = str(interaction.guild_id)
        user_id = str(interaction.user.id)

        # Initialize invite data if needed
        invites = initialize_user_invites(invites, guild_id, user_id)
        user_invites = invites[guild_id][user_id]
        logger.info(f"User {interaction.user.name} has {user_invites} invites")

        if user_invites < 1:
            embed = discord.Embed(
                title="Error!",
                description="You need at least 1 invite to claim an account!",
                color=0x2f3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        accounts = load_accounts()
        logger.info(f"Currently {len(accounts)} accounts available")

        if not accounts:
            embed = discord.Embed(
                title="Error!",
                description="Sorry, there are no accounts available!",
                color=0x2f3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        try:
            # Try to send DM first before modifying any state
            # Create an embed for the DM
            dm_embed = discord.Embed(
                title="Here's your account:",
                description=f"`{accounts[0]}`",
                color=0x2f3136
            )
            await interaction.user.send(embed=dm_embed)

            # If DM succeeds, update the states
            account = accounts.pop(0)  # Remove the first account
            save_accounts(accounts)
            logger.info(f"Account removed from pool, {len(accounts)} remaining")

            invites[guild_id][user_id] -= 1
            save_invites(invites)
            logger.info(f"Deducted invite from user {interaction.user.name}, now has {invites[guild_id][user_id]} invites")

            # Send notification to the logging channel
            log_channel = bot.get_channel(1346662542461636629)
            if log_channel:
                log_embed = discord.Embed(
                    title="Account Claimed",
                    description=f"{interaction.user.mention} has claimed an account.",
                    color=0x2f3136
                )
                await log_channel.send(embed=log_embed)
                logger.info(f"Sent claim notification to logging channel for user {interaction.user.name}")

            # Create an embed for the success message
            embed = discord.Embed(
                title="Success!",
                description=f"A gift has been sent to {interaction.user.mention}'s DMs. Balance -1 invites.",
                color=0x2f3136
            )
            await interaction.response.send_message(embed=embed)
            logger.info(f"Successfully sent account to user {interaction.user.name}")
        except discord.Forbidden:
            logger.warning(f"Failed to send DM to user {interaction.user.name}")
            embed = discord.Embed(
                title="Error!",
                description="Couldn't send you the account via DM. Please enable DMs from server members and try again!",
                color=0x2f3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        logger.error(f"Error in claim command: {e}")
        if not interaction.response.is_done():
            embed = discord.Embed(
                title="Error!",
                description="An error occurred while processing your claim.",
                color=0x2f3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="addaccount", description="Add an account to the pool (Admin only)")
@app_commands.default_permissions(administrator=True)
async def add_account(interaction: discord.Interaction, account: str):
    try:
        accounts = load_accounts()
        accounts.append(account)
        save_accounts(accounts)
        logger.info(f"Account added by admin {interaction.user.name}")
        embed = discord.Embed(
            title="Success!",
            description="Account added successfully!",
            color=0x2f3136
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        logger.error(f"Error in add_account command: {e}")
        embed = discord.Embed(
            title="Error!",
            description="An error occurred while adding the account.",
            color=0x2f3136
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="remaining", description="Check how many accounts are left")
async def remaining(interaction: discord.Interaction):
    try:
        accounts = load_accounts()
        logger.info(f"Remaining accounts check by user {interaction.user.name}")
        embed = discord.Embed(
            title="Remaining Accounts",
            description=f"There are {len(accounts)} accounts remaining!",
            color=0x2f3136
        )
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        logger.error(f"Error in remaining command: {e}")
        embed = discord.Embed(
            title="Error!",
            description="An error occurred while checking remaining accounts.",
            color=0x2f3136
        )
        await interaction.response.send_message(embed=embed)

# Error handling
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        logger.warning(f"User {interaction.user.name} attempted to use command without permissions")
        embed = discord.Embed(
            title="Error!",
            description="You don't have permission to use this command!",
            color=0x2f3136
        )
        await interaction.response.send_message(embed=embed)
    else:
        logger.error(f"Command error: {error}")
        embed = discord.Embed(
            title="Error!",
            description="An error occurred while processing your command.",
            color=0x2f3136
        )
        await interaction.response.send_message(embed=embed)

# Run the bot
if __name__ == "__main__":
    keep_alive()  # Start the web server
    while True:
        try:
            logger.info("Starting bot...")
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            logger.error(f"Bot crashed with error: {e}")
            logger.info("Restarting bot in 5 seconds...")
            time.sleep(5)
            continue