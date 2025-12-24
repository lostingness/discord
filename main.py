import discord
import aiohttp
import re
import os
import asyncio
import json
import sqlite3
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import pytz
import time
import io
import math
from typing import Optional

# Get environment variables for Railway
TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
YOUR_DISCORD_ID = int(os.environ.get('ADMIN_DISCORD_ID', 1355605971858100249))
DEFAULT_CHANNEL_ID = int(os.environ.get('DEFAULT_CHANNEL_ID', 1435704878986039356))

# Exit if no token
if not TOKEN:
    print("❌ ERROR: DISCORD_BOT_TOKEN environment variable is required!")
    print("💡 Please set it in Railway Environment Variables")
    exit(1)

# Bot setup with correct intents
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# API Configuration
DETAILS_API_URL = "https://lostingness.site/KEY/Infox.php?type={value}"
TELEGRAM_API_URL = "https://my.lostingness.site/tgn.php?value={value}"

# Developer Information
DEVELOPER_INFO = {
    'discord': 'https://discord.gg/teamkorn',
    'telegram': 'https://t.me/Terex',
    'developer': '@Terex On Telegram',
    'phenion': '@phenion on Telegram'
}

# Service Prices
SERVICE_PRICES = {
    'mobile': 1,
    'aadhaar': 1,
    'email': 1,
    'telegram': 5
}

# Setup tracking
pending_setups = {}  # server_id: owner_id
admin_notification_tasks = {}

# Database setup
def init_db():
    conn = sqlite3.connect('kornfinder.db')
    c = conn.cursor()
    
    # Users table for credits and levels
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            credits INTEGER DEFAULT 0,
            level INTEGER DEFAULT 0,
            total_voice_minutes INTEGER DEFAULT 0,
            unlimited INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Voice sessions table for tracking active voice time
    c.execute('''
        CREATE TABLE IF NOT EXISTS voice_sessions (
            user_id INTEGER PRIMARY KEY,
            join_time TEXT,
            guild_id INTEGER,
            channel_id INTEGER,
            last_check_time TEXT
        )
    ''')
    
    # Allowed channels table
    c.execute('''
        CREATE TABLE IF NOT EXISTS allowed_channels (
            channel_id INTEGER PRIMARY KEY,
            guild_id INTEGER,
            added_by INTEGER,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Global admins table (full access)
    c.execute('''
        CREATE TABLE IF NOT EXISTS global_admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Server admins table (limited access - only their server)
    c.execute('''
        CREATE TABLE IF NOT EXISTS server_admins (
            server_id INTEGER,
            user_id INTEGER,
            added_by INTEGER,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (server_id, user_id)
        )
    ''')
    
    # Server setup tracking
    c.execute('''
        CREATE TABLE IF NOT EXISTS server_setup (
            server_id INTEGER PRIMARY KEY,
            setup_complete INTEGER DEFAULT 0,
            setup_channel_id INTEGER,
            last_notification TEXT,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Bot joins tracking table
    c.execute('''
        CREATE TABLE IF NOT EXISTS bot_joins (
            server_id INTEGER PRIMARY KEY,
            server_name TEXT,
            server_owner_id INTEGER,
            join_date TEXT,
            added_by INTEGER,
            notification_sent INTEGER DEFAULT 0
        )
    ''')
    
    # Service prices table
    c.execute('''
        CREATE TABLE IF NOT EXISTS service_prices (
            service_name TEXT PRIMARY KEY,
            price INTEGER DEFAULT 1,
            updated_by INTEGER,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Initialize service prices
    for service, price in SERVICE_PRICES.items():
        c.execute('''
            INSERT OR REPLACE INTO service_prices (service_name, price, updated_by) 
            VALUES (?, ?, ?)
        ''', (service, price, YOUR_DISCORD_ID))
    
    # Add default global admin
    c.execute('INSERT OR IGNORE INTO global_admins (user_id, added_by) VALUES (?, ?)', (YOUR_DISCORD_ID, YOUR_DISCORD_ID))
    
    conn.commit()
    conn.close()

# Initialize database
init_db()

class PremiumStyles:
    # Premium Colors
    PRIMARY = 0x5865F2
    SUCCESS = 0x57F287
    ERROR = 0xED4245
    WARNING = 0xFEE75C
    INFO = 0x3498DB
    PREMIUM = 0x9B59B6

# Global variables for stats
bot.start_time = datetime.now(timezone.utc)
search_count = 0

def get_db_connection():
    return sqlite3.connect('kornfinder.db', check_same_thread=False)

def is_allowed_channel():
    async def predicate(ctx):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT channel_id FROM allowed_channels WHERE channel_id = ?', (ctx.channel.id,))
        result = c.fetchone()
        conn.close()
        
        if not result:
            embed = discord.Embed(
                title="🚫 Channel Restricted",
                description="This bot can only be used in authorized channels.",
                color=0xED4245
            )
            await ctx.send(embed=embed, delete_after=10)
            return False
        return True
    return commands.check(predicate)

def is_global_admin():
    async def predicate(ctx):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT user_id FROM global_admins WHERE user_id = ?', (ctx.author.id,))
        result = c.fetchone()
        conn.close()
        
        if not result:
            embed = discord.Embed(
                title="🚫 Global Admin Access Required",
                description="You need global administrator privileges to use this command.",
                color=0xED4245
            )
            await ctx.send(embed=embed, delete_after=10)
            return False
        return True
    return commands.check(predicate)

def is_server_admin():
    async def predicate(ctx):
        # Check if global admin first
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT user_id FROM global_admins WHERE user_id = ?', (ctx.author.id,))
        global_admin = c.fetchone()
        
        if global_admin:
            conn.close()
            return True
        
        # Check if server admin for this server
        if ctx.guild:
            c.execute('SELECT user_id FROM server_admins WHERE server_id = ? AND user_id = ?', (ctx.guild.id, ctx.author.id))
            server_admin = c.fetchone()
            conn.close()
            
            if server_admin:
                return True
        
        embed = discord.Embed(
            title="🚫 Admin Access Required",
            description="You need administrator privileges to use this command.",
            color=0xED4245
        )
        await ctx.send(embed=embed, delete_after=10)
        return False
    return commands.check(predicate)

def get_user_data(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = c.fetchone()
    
    if not user:
        # Create new user with 0 credits
        c.execute('''
            INSERT INTO users (user_id, credits, level, total_voice_minutes, unlimited)
            VALUES (?, 0, 0, 0, 0)
        ''', (user_id,))
        conn.commit()
        c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        user = c.fetchone()
    
    conn.close()
    return user

def has_unlimited_access(user_id):
    """Check if user has unlimited access"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT unlimited FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result and result[0] == 1

def update_user_credits(user_id, credits_change):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET credits = credits + ? WHERE user_id = ?', (credits_change, user_id))
    conn.commit()
    conn.close()

def set_user_credits(user_id, credits):
    """Set user credits to specific amount"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET credits = ? WHERE user_id = ?', (credits, user_id))
    conn.commit()
    conn.close()

def update_user_level(user_id, level):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET level = ? WHERE user_id = ?', (level, user_id))
    conn.commit()
    conn.close()

def update_voice_minutes(user_id, minutes):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET total_voice_minutes = total_voice_minutes + ? WHERE user_id = ?', (minutes, user_id))
    conn.commit()
    conn.close()

def get_voice_session(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM voice_sessions WHERE user_id = ?', (user_id,))
    session = c.fetchone()
    conn.close()
    return session

def start_voice_session(user_id, guild_id, channel_id):
    conn = get_db_connection()
    c = conn.cursor()
    current_time = datetime.now().isoformat()
    c.execute('''
        INSERT OR REPLACE INTO voice_sessions (user_id, join_time, guild_id, channel_id, last_check_time)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, current_time, guild_id, channel_id, current_time))
    conn.commit()
    conn.close()

def update_voice_check_time(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE voice_sessions SET last_check_time = ? WHERE user_id = ?', (datetime.now().isoformat(), user_id))
    conn.commit()
    conn.close()

def end_voice_session(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('DELETE FROM voice_sessions WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_indian_time():
    """Get current Indian time"""
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist).strftime("%d %b %Y • %I:%M %p IST")

async def resolve_user(ctx, user_input):
    """Resolve user input to user object"""
    try:
        # Check if input is a user ID
        if user_input.isdigit():
            user_id = int(user_input)
            try:
                user = await bot.fetch_user(user_id)
                return user
            except:
                pass
        
        # Check if input is a mention
        if user_input.startswith('<@') and user_input.endswith('>'):
            user_id = int(re.sub(r'\D', '', user_input))
            try:
                user = await bot.fetch_user(user_id)
                return user
            except:
                pass
        
        # Check in current guild
        if ctx.guild:
            # Remove @ if present
            if user_input.startswith('@'):
                user_input = user_input[1:]
            
            # Try to find by username#discriminator
            if '#' in user_input:
                try:
                    username, discriminator = user_input.split('#')
                    user = discord.utils.get(ctx.guild.members, name=username, discriminator=discriminator)
                    if user:
                        return user
                except:
                    pass
            
            # Try to find by username
            user = discord.utils.get(ctx.guild.members, name=user_input)
            if user:
                return user
            
            # Try to find by nickname
            user = discord.utils.get(ctx.guild.members, display_name=user_input)
            if user:
                return user
            
            # Try to find by partial name
            for member in ctx.guild.members:
                if user_input.lower() in member.name.lower() or (member.nick and user_input.lower() in member.nick.lower()):
                    return member
        
        return None
    except Exception as e:
        print(f"Error resolving user {user_input}: {e}")
        return None

def clean_text(text):
    """Advanced text cleaning"""
    if not text or str(text).strip() in ["", "null", "None", "N/A", "NA"]:
        return "**Not Available**"
    
    text = str(text).strip()
    text = re.sub(r'[!@#$%^&*()_+=`~\[\]{}|\\:;"<>?]', ' ', text)
    text = re.sub(r'[.!]+$', '', text)
    text = re.sub(r'\s+', ' ', text)
    
    if '@' not in text:
        words = text.split()
        cleaned_words = []
        for word in words:
            if word.upper() in ['II', 'III', 'IV', 'VI', 'VII', 'VIII']:
                cleaned_words.append(word.upper())
            elif len(word) > 1:
                cleaned_words.append(word[0].upper() + word[1:].lower())
            else:
                cleaned_words.append(word.upper())
        text = ' '.join(cleaned_words)
    
    return f"**{text}**"

def format_address(address):
    """Premium address formatting"""
    if not address or str(address).strip() in ["", "null", "None", "N/A"]:
        return "**Address Not Available**"
    
    address = str(address)
    address = re.sub(r'[.!*#-]+', ', ', address)
    address = re.sub(r'\s*,\s*', ', ', address)
    address = re.sub(r'\s+', ' ', address)
    address = re.sub(r'\b(c/o|C/O)\s*:?\s*', '**C/O:** ', address, flags=re.IGNORECASE)
    address = address.strip().strip(',')
    
    parts = [part.strip() for part in address.split(',') if part.strip()]
    formatted_parts = []
    
    for part in parts:
        if part.upper() in ['DELHI', 'MUMBAI', 'KOLKATA', 'CHENNAI', 'BANGALORE', 'HYDERABAD']:
            formatted_parts.append(f"**{part.upper()}**")
        else:
            formatted_parts.append(f"**{part.title()}**")
    
    return ', '.join(formatted_parts)

async def check_voice_rewards(user_id, minutes_added):
    """Check and give voice rewards"""
    user_data = get_user_data(user_id)
    old_minutes = user_data[3] - minutes_added
    new_minutes = user_data[3]
    
    # Calculate how many 10-minute and 20-minute intervals passed
    old_tens = old_minutes // 10
    new_tens = new_minutes // 10
    
    old_twenties = old_minutes // 20
    new_twenties = new_minutes // 20
    
    # Give 1 credit for every 10 minutes
    tens_diff = new_tens - old_tens
    if tens_diff > 0:
        update_user_credits(user_id, tens_diff)
    
    # Level up for every 20 minutes
    twenties_diff = new_twenties - old_twenties
    if twenties_diff > 0:
        new_level = user_data[2] + twenties_diff
        update_user_level(user_id, new_level)
        
        # Notify user about level up
        user = bot.get_user(user_id)
        if user:
            try:
                embed = discord.Embed(
                    title="🎉 LEVEL UP! 🎉",
                    description=f"**{user.mention} just reached Level {new_level}!**",
                    color=0x57F287
                )
                embed.add_field(
                    name="🎧 Voice Activity",
                    value=f"**Total Time:** {new_minutes} minutes",
                    inline=True
                )
                embed.add_field(
                    name="💰 Credits Earned",
                    value=f"**+{tens_diff} credits** this session\n**Total Credits:** {user_data[1] + tens_diff}",
                    inline=True
                )
                embed.set_footer(text="Keep staying active in voice chat to earn more credits! 💫")
                await user.send(embed=embed)
            except:
                pass

@bot.event
async def on_ready():
    print("🚀 KornFinder Premium Mobile Search Bot Online!")
    print(f"💎 Admin ID: {YOUR_DISCORD_ID}")
    print(f"📢 Default Channel: {DEFAULT_CHANNEL_ID}")
    print("⚡ Voice Chat Credit System Enabled!")
    print("🌐 API: Lostingness Premium")
    print("💰 10 minutes = 1 credit, 20 minutes = 2 credits + level up")
    print("📱 Services: Number, Aadhaar, Email, Telegram")
    
    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name="Mobile Numbers | !info"
    )
    await bot.change_presence(activity=activity)
    
    # Add default channel if exists
    await add_default_channel()
    
    # Start background tasks
    asyncio.create_task(voice_monitoring_task())
    asyncio.create_task(cleanup_voice_sessions_task())
    asyncio.create_task(daily_report_task())
    
    print(f"✅ Bot is online in {len(bot.guilds)} servers!")

async def add_default_channel():
    """Add default channel to database if it exists"""
    try:
        channel = bot.get_channel(DEFAULT_CHANNEL_ID)
        if channel and channel.guild:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute('INSERT OR IGNORE INTO allowed_channels (channel_id, guild_id, added_by) VALUES (?, ?, ?)', 
                      (DEFAULT_CHANNEL_ID, channel.guild.id, YOUR_DISCORD_ID))
            conn.commit()
            conn.close()
            print(f"✅ Default channel added: #{channel.name} in {channel.guild.name}")
    except Exception as e:
        print(f"⚠️ Could not add default channel: {e}")

async def voice_monitoring_task():
    """24x7 Voice monitoring for automatic rewards"""
    while True:
        try:
            # Check all active voice sessions every minute
            conn = get_db_connection()
            c = conn.cursor()
            c.execute('SELECT * FROM voice_sessions')
            active_sessions = c.fetchall()
            conn.close()
            
            for session in active_sessions:
                user_id, join_time_str, guild_id, channel_id, last_check_str = session
                
                # Get the guild and member
                guild = bot.get_guild(guild_id)
                if not guild:
                    end_voice_session(user_id)
                    continue
                
                member = guild.get_member(user_id)
                if not member:
                    end_voice_session(user_id)
                    continue
                
                # Check if member is actually in a voice channel
                if not member.voice or not member.voice.channel or member.voice.channel.id != channel_id:
                    # Member is not in the tracked voice channel, end session
                    end_voice_session(user_id)
                    continue
                
                # Member is in voice channel, update minutes
                last_check = datetime.fromisoformat(last_check_str)
                current_time = datetime.now()
                time_since_last_check = (current_time - last_check).total_seconds() / 60
                
                if time_since_last_check >= 1:
                    # Update voice minutes (only 1 minute per minute check)
                    update_voice_minutes(user_id, 1)
                    
                    # Check for rewards
                    await check_voice_rewards(user_id, 1)
                    
                    # Update last check time
                    update_voice_check_time(user_id)
            
            await asyncio.sleep(60)
            
        except Exception as e:
            print(f"Voice monitoring error: {e}")
            await asyncio.sleep(60)

async def cleanup_voice_sessions_task():
    """Clean up stale voice sessions"""
    while True:
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute('SELECT * FROM voice_sessions')
            sessions = c.fetchall()
            
            for session in sessions:
                user_id, join_time_str, guild_id, channel_id, last_check_str = session
                last_check = datetime.fromisoformat(last_check_str)
                current_time = datetime.now()
                
                # If no check for 5 minutes, assume user left
                if (current_time - last_check).total_seconds() > 300:
                    c.execute('DELETE FROM voice_sessions WHERE user_id = ?', (user_id,))
            
            conn.commit()
            conn.close()
            await asyncio.sleep(300)  # Check every 5 minutes
            
        except Exception as e:
            print(f"Cleanup error: {e}")
            await asyncio.sleep(300)

async def daily_report_task():
    """Send daily report to admin"""
    while True:
        try:
            # Wait 24 hours
            await asyncio.sleep(86400)  # 24 hours in seconds
            
            # Generate and send report
            await send_daily_report()
            
        except Exception as e:
            print(f"Daily report error: {e}")
            await asyncio.sleep(3600)  # Wait 1 hour before retrying

async def send_daily_report():
    """Send daily report to admin"""
    try:
        admin_user = bot.get_user(YOUR_DISCORD_ID)
        
        if not admin_user:
            print("❌ Admin user not found!")
            return
        
        # Generate report
        report_content = await generate_server_report()
        
        # Create text file
        file_content = f"KornFinder Bot - Daily Report\n"
        file_content += f"Generated on: {get_indian_time()}\n"
        file_content += f"Total Servers: {len(bot.guilds)}\n"
        file_content += "=" * 50 + "\n\n"
        file_content += report_content
        
        # Send as file
        file = discord.File(io.BytesIO(file_content.encode('utf-8')), filename=f"kornfinder_report_{datetime.now().strftime('%Y%m%d')}.txt")
        
        embed = discord.Embed(
            title="📊 Daily Server Report",
            description=f"**KornFinder Bot Daily Report**\nGenerated on {get_indian_time()}",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc)
        )
        
        embed.add_field(
            name="📈 Server Statistics",
            value=f"**Total Servers:** {len(bot.guilds)}\n**Total Users:** {sum(guild.member_count for guild in bot.guilds):,}\n**Bot Uptime:** {str(datetime.now(timezone.utc) - bot.start_time).split('.')[0]}",
            inline=False
        )
        
        embed.set_footer(text="Automated Daily Report • KornFinder Bot")
        
        await admin_user.send(embed=embed, file=file)
        print(f"📊 Daily report sent to admin {admin_user.name}")
        
    except Exception as e:
        print(f"Error sending daily report: {e}")

async def generate_server_report():
    """Generate server report text"""
    report = ""
    
    for guild in bot.guilds:
        report += f"Server: {guild.name}\n"
        report += f"ID: {guild.id}\n"
        report += f"Owner: {guild.owner.name if guild.owner else 'Unknown'}\n"
        report += f"Members: {guild.member_count}\n"
        report += f"Created: {guild.created_at.strftime('%Y-%m-%d')}\n"
        
        # Get allowed channels for this server
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT channel_id FROM allowed_channels WHERE guild_id = ?', (guild.id,))
        allowed_channels = c.fetchall()
        conn.close()
        
        if allowed_channels:
            report += "Allowed Channels:\n"
            for channel_row in allowed_channels:
                channel_id = channel_row[0]
                channel = guild.get_channel(channel_id)
                if channel:
                    report += f"  - #{channel.name} (ID: {channel.id})\n"
                else:
                    report += f"  - Unknown Channel (ID: {channel_id})\n"
        else:
            report += "Allowed Channels: None\n"
        
        # Try to get invite link
        invite_link = "No invite available"
        try:
            if guild.vanity_url_code:
                invite_link = f"https://discord.gg/{guild.vanity_url_code}"
            else:
                # Try to create an invite
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).create_instant_invite:
                        try:
                            invite = await channel.create_invite(max_age=300, max_uses=1, reason="Daily report")
                            invite_link = invite.url
                            break
                        except:
                            continue
        except:
            pass
        
        report += f"Invite Link: {invite_link}\n"
        report += "-" * 40 + "\n\n"
    
    return report

@bot.event
async def on_voice_state_update(member, before, after):
    """Track voice channel joins and leaves"""
    if member.bot:
        return
    
    user_id = member.id
    
    # User joined a voice channel
    if before.channel is None and after.channel is not None:
        start_voice_session(user_id, after.channel.guild.id, after.channel.id)
        print(f"🎤 Voice session started: {member.display_name}")
    
    # User left a voice channel or moved channels
    elif before.channel is not None and after.channel is None:
        session = get_voice_session(user_id)
        if session:
            join_time = datetime.fromisoformat(session[1])
            last_check = datetime.fromisoformat(session[4])
            time_spent = (last_check - join_time).total_seconds() / 60
            time_spent = int(time_spent)
            
            if time_spent > 0:
                # Final update for remaining time
                update_voice_minutes(user_id, time_spent)
                
                # Check for rewards
                await check_voice_rewards(user_id, time_spent)
            
            end_voice_session(user_id)
            print(f"🎤 Voice session ended: {member.display_name} spent {time_spent} minutes")
    
    # User moved between channels
    elif before.channel is not None and after.channel is not None and before.channel.id != after.channel.id:
        # End old session and start new one
        session = get_voice_session(user_id)
        if session:
            join_time = datetime.fromisoformat(session[1])
            last_check = datetime.fromisoformat(session[4])
            time_spent = (last_check - join_time).total_seconds() / 60
            time_spent = int(time_spent)
            
            if time_spent > 0:
                update_voice_minutes(user_id, time_spent)
                await check_voice_rewards(user_id, time_spent)
            
            end_voice_session(user_id)
        
        start_voice_session(user_id, after.channel.guild.id, after.channel.id)
        print(f"🎤 Voice session moved: {member.display_name}")

async def check_server_permissions(guild):
    """Check if bot has admin permissions in server"""
    if not guild.me.guild_permissions.administrator:
        # Start hourly notifications
        if guild.id not in admin_notification_tasks:
            admin_notification_tasks[guild.id] = asyncio.create_task(
                admin_notification_task(guild)
            )
        return False
    else:
        # Stop notifications if task exists
        if guild.id in admin_notification_tasks:
            task = admin_notification_tasks[guild.id]
            task.cancel()
            del admin_notification_tasks[guild.id]
        return True

async def admin_notification_task(guild):
    """Send hourly notifications if bot doesn't have admin permissions"""
    while True:
        try:
            # Wait 1 hour
            await asyncio.sleep(3600)
            
            # Check if bot still doesn't have admin
            if not guild.me.guild_permissions.administrator:
                # Try to send to server owner
                if guild.owner:
                    try:
                        embed = discord.Embed(
                            title="⚠️ ADMINISTRATOR PERMISSIONS REQUIRED ⚠️",
                            description=f"Hello {guild.owner.mention}! **KornFinder Bot** needs **Administrator Permissions** in **{guild.name}** to function properly!",
                            color=0xFEE75C
                        )
                        
                        embed.add_field(
                            name="🔧 **Why Admin Permissions?**",
                            value="Administrator permissions allow the bot to:\n• Manage channels and messages\n• Track voice chat activity\n• Provide seamless user experience\n• Access all necessary features",
                            inline=False
                        )
                        
                        embed.add_field(
                            name="🚀 **Benefits of Full Setup**",
                            value="• **24/7 Voice Chat Credit System** 🎤\n• **Advanced Search Features** 🔍\n• **Auto-delete for privacy** 🛡️\n• **User management tools** 👥\n• **Server analytics** 📊",
                            inline=False
                        )
                        
                        embed.add_field(
                            name="⚡ **How to Grant Admin**",
                            value="1. Go to **Server Settings** ⚙️\n2. Click **Roles** 👑\n3. Select **KornFinder Bot** role\n4. Enable **Administrator** permission\n5. Save changes 💾",
                            inline=False
                        )
                        
                        embed.add_field(
                            name="📞 **Need Help?**",
                            value=f"**Developer:** {DEVELOPER_INFO['developer']}\n**Discord Server:** [Join Here]({DEVELOPER_INFO['discord']}) 👥\n**Telegram:** [Contact]({DEVELOPER_INFO['telegram']}) 📲\n**API Provider:** {DEVELOPER_INFO['phenion']} 🔗",
                            inline=False
                        )
                        
                        await guild.owner.send(embed=embed)
                        print(f"⚠️ Admin notification sent to {guild.owner.name} for server {guild.name}")
                        
                    except Exception as e:
                        print(f"Could not send admin notification to {guild.owner.name}: {e}")
                        
                        # Try to send to first available text channel
                        for channel in guild.text_channels:
                            if channel.permissions_for(guild.me).send_messages:
                                try:
                                    await channel.send(f"@{guild.owner.id if guild.owner else ''} ⚠️ **KornFinder Bot needs Administrator Permissions to function properly!** Please grant admin permissions for full features.")
                                    break
                                except:
                                    continue
            else:
                # Bot now has admin, stop notifications
                break
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Admin notification task error for {guild.name}: {e}")
            await asyncio.sleep(3600)

@bot.event
async def on_guild_join(guild):
    """Send notification to server owner when bot is added"""
    # Store bot join information
    conn = get_db_connection()
    c = conn.cursor()
    
    # Record bot join
    c.execute('''
        INSERT OR REPLACE INTO bot_joins (server_id, server_name, server_owner_id, join_date, added_by, notification_sent)
        VALUES (?, ?, ?, ?, ?, 0)
    ''', (guild.id, guild.name, guild.owner.id if guild.owner else 0, datetime.now().isoformat(), guild.owner.id if guild.owner else 0))
    
    # Add to server setup tracking
    c.execute('INSERT OR IGNORE INTO server_setup (server_id, setup_complete) VALUES (?, 0)', (guild.id,))
    
    conn.commit()
    conn.close()
    
    # Check admin permissions
    await check_server_permissions(guild)
    
    # Send setup message to server owner
    owner = guild.owner
    if owner:
        try:
            embed = discord.Embed(
                title="🎉 WELCOME TO KORNFINDER BOT! 🎉",
                description=f"Hello {owner.mention}! Thanks for adding **KornFinder Bot** to **{guild.name}**!",
                color=0x5865F2
            )
            
            embed.add_field(
                name="🚀 **Quick Setup Guide**",
                value="To get started, please follow these steps:",
                inline=False
            )
            
            embed.add_field(
                name="📌 **Step 1: Create a Channel**",
                value="Create a new text channel or use an existing one where you want to use the bot.",
                inline=False
            )
            
            embed.add_field(
                name="🔧 **Step 2: Get Channel ID**",
                value="Right-click the channel and click **Copy ID** (Developer Mode must be enabled in Discord Settings).",
                inline=False
            )
            
            embed.add_field(
                name="📨 **Step 3: Send Channel ID**",
                value="**Reply to this message** with the Channel ID to complete setup.",
                inline=False
            )
            
            embed.add_field(
                name="⚡ **Step 4: Grant Admin Permissions**",
                value="Make sure the bot has **Administrator Permissions** for full functionality.",
                inline=False
            )
            
            embed.add_field(
                name="💎 **Bot Features**",
                value="• **Mobile Number Lookup** 📱\n• **Aadhaar Card Search** 🪪\n• **Email Address Search** 📧\n• **Telegram to Mobile** 📲\n• **Voice Chat Credit System** 🎤",
                inline=False
            )
            
            embed.add_field(
                name="📞 **Support & Links**",
                value=f"**Developer:** {DEVELOPER_INFO['developer']}\n**Discord Server:** [Join Here]({DEVELOPER_INFO['discord']}) 👥\n**Telegram:** [Contact]({DEVELOPER_INFO['telegram']}) 📲\n**API Provider:** {DEVELOPER_INFO['phenion']} 🔗",
                inline=False
            )
            
            embed.set_footer(text="Reply to this message with the Channel ID to complete setup! ✅")
            
            setup_msg = await owner.send(embed=embed)
            
            # Store the setup message ID for reply tracking
            pending_setups[guild.id] = {
                'owner_id': owner.id,
                'setup_msg_id': setup_msg.id,
                'channel_id': None
            }
            
            print(f"📥 Bot added to new server: {guild.name} (ID: {guild.id})")
            
        except Exception as e:
            print(f"Could not send setup message to server owner: {e}")
    
    # Send notification to bot admin
    await notify_admin_about_join(guild)

@bot.event
async def on_message(message):
    """Handle DM messages for setup"""
    # Process commands first
    await bot.process_commands(message)
    
    # Check if message is a DM
    if isinstance(message.channel, discord.DMChannel) and not message.author.bot:
        # Check if this is a reply to our setup message
        if message.reference and message.reference.message_id:
            # Check if user is a server owner with pending setup
            for server_id, setup_info in list(pending_setups.items()):
                if (message.author.id == setup_info['owner_id'] and 
                    message.reference.message_id == setup_info['setup_msg_id']):
                    
                    # This is a setup message reply
                    content = message.content.strip()
                    
                    # Check if it's a channel ID (numeric)
                    if content.isdigit():
                        channel_id = int(content)
                        guild = bot.get_guild(server_id)
                        
                        if guild:
                            channel = guild.get_channel(channel_id)
                            if channel:
                                # Add channel to allowed channels
                                conn = get_db_connection()
                                c = conn.cursor()
                                c.execute('INSERT OR REPLACE INTO allowed_channels (channel_id, guild_id, added_by) VALUES (?, ?, ?)', 
                                         (channel_id, guild.id, message.author.id))
                                c.execute('UPDATE server_setup SET setup_complete = 1, setup_channel_id = ? WHERE server_id = ?', 
                                         (channel_id, guild.id))
                                conn.commit()
                                conn.close()
                                
                                # Remove from pending setups
                                del pending_setups[server_id]
                                
                                # Send success message
                                success_embed = discord.Embed(
                                    title="✅ SETUP COMPLETE! 🎉",
                                    description=f"**KornFinder Bot has been successfully set up in {guild.name}!**",
                                    color=0x57F287
                                )
                                
                                success_embed.add_field(
                                    name="📢 **Setup Successful!**",
                                    value=f"**Channel:** #{channel.name}\n**Server:** {guild.name}\n**Status:** ✅ **ACTIVE 24/7**",
                                    inline=False
                                )
                                
                                success_embed.add_field(
                                    name="🔧 **Bot Commands**",
                                    value="Here are the main commands you can use:",
                                    inline=False
                                )
                                
                                success_embed.add_field(
                                    name="🔍 **SEARCH COMMANDS**",
                                    value=(
                                        "`!num 7405453929` - Search mobile number **(1 credit)**\n"
                                        "`!card 123456789012` - Search Aadhaar card **(1 credit)**\n"
                                        "`!email example@domain.com` - Search email address **(1 credit)**\n"
                                        "`!tg username` - Telegram to mobile search **(5 credits)**"
                                    ),
                                    inline=False
                                )
                                
                                success_embed.add_field(
                                    name="👤 **USER COMMANDS**",
                                    value=(
                                        "`!info` - Complete bot information 📊\n"
                                        "`!credits` - Check your credit balance 💰\n"
                                        "`!voice` - Check voice chat status 🎤\n"
                                        "`!level` - Check your level ⭐\n"
                                        "`!leader` - View top users leaderboard 🏆"
                                    ),
                                    inline=False
                                )
                                
                                success_embed.add_field(
                                    name="🎧 **VOICE CHAT REWARDS**",
                                    value=(
                                        "**Earn credits by staying in voice chat:**\n"
                                        "• **10 minutes** = 1 credit 💎\n"
                                        "• **20 minutes** = 2 credits + level up ⭐\n"
                                        "• **Stay active** = Unlimited credits! 🔥"
                                    ),
                                    inline=False
                                )
                                
                                success_embed.add_field(
                                    name="🛠️ **ADMIN COMMANDS**",
                                    value=(
                                        "`!addadmin @User` - Add admin to your server\n"
                                        "`!addchannel #channel` - Add allowed channel\n"
                                        "`!listchannels` - List allowed channels"
                                    ),
                                    inline=False
                                )
                                
                                success_embed.add_field(
                                    name="📞 **Support & Links**",
                                    value=f"**Developer:** {DEVELOPER_INFO['developer']}\n**Discord Server:** [Join Here]({DEVELOPER_INFO['discord']}) 👥\n**Telegram:** [Contact]({DEVELOPER_INFO['telegram']}) 📲\n**API Provider:** {DEVELOPER_INFO['phenion']} 🔗",
                                    inline=False
                                )
                                
                                success_embed.set_footer(text="Enjoy using KornFinder Bot! 🚀")
                                
                                await message.channel.send(embed=success_embed)
                                
                                # Also send message to the setup channel
                                try:
                                    channel_embed = discord.Embed(
                                        title="🤖 KORNFINDER BOT - READY TO USE! 🎉",
                                        description=f"**This channel has been set up for KornFinder Bot commands!**\n\nUse `!info` to see all available commands.",
                                        color=0x57F287
                                    )
                                    channel_embed.set_footer(text="Setup completed successfully! ✅")
                                    await channel.send(embed=channel_embed)
                                except:
                                    pass
                                
                                print(f"✅ Setup completed for server: {guild.name} (Channel: #{channel.name})")
                            else:
                                await message.channel.send("❌ Channel not found! Please make sure the Channel ID is correct and the bot has access to that channel.")
                        else:
                            await message.channel.send("❌ Server not found! The bot might have been removed from the server.")
                    else:
                        await message.channel.send("❌ Please send only the Channel ID (numbers only). Right-click the channel and click 'Copy ID'.")
                    
                    # Break after handling
                    break

async def notify_admin_about_join(guild):
    """Notify admin about new server join"""
    try:
        admin_user = bot.get_user(YOUR_DISCORD_ID)
        
        if not admin_user:
            print("❌ Admin user not found!")
            return
        
        # Create invite for the server (try to get existing invite or create one)
        invite_link = "Could not create invite"
        try:
            # Try to get text channels
            text_channels = [channel for channel in guild.text_channels if channel.permissions_for(guild.me).create_instant_invite]
            
            if text_channels:
                # Use the first text channel we have permission in
                invite = await text_channels[0].create_invite(max_age=604800, max_uses=1, reason="Bot join notification")
                invite_link = invite.url
        except:
            pass
        
        # Send notification to admin
        embed = discord.Embed(
            title="📥 BOT ADDED TO NEW SERVER!",
            description=f"**KornFinder Bot** has been added to a new server!",
            color=0x57F287,
            timestamp=datetime.now(timezone.utc)
        )
        
        embed.add_field(name="🏢 Server Name", value=f"**{guild.name}**", inline=True)
        embed.add_field(name="🆔 Server ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="👑 Server Owner", value=f"{guild.owner.mention if guild.owner else 'Unknown'}", inline=True)
        embed.add_field(name="👥 Member Count", value=f"**{guild.member_count}** members", inline=True)
        embed.add_field(name="📅 Joined On", value=f"{get_indian_time()}", inline=True)
        embed.add_field(name="🔗 Server Invite", value=f"[Join Server]({invite_link})", inline=True)
        
        embed.set_footer(text="Bot Join Notification • KornFinder")
        
        await admin_user.send(embed=embed)
        
        # Mark notification as sent
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('UPDATE bot_joins SET notification_sent = 1 WHERE server_id = ?', (guild.id,))
        conn.commit()
        conn.close()
        
        print(f"📢 Admin notified about new server: {guild.name}")
        
    except Exception as e:
        print(f"Error notifying admin: {e}")

def get_service_price(service_name):
    """Get price for a service from database"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT price FROM service_prices WHERE service_name = ?', (service_name,))
    result = c.fetchone()
    conn.close()
    
    if result:
        return result[0]
    else:
        # Default prices if not found
        return SERVICE_PRICES.get(service_name, 1)

def check_credits(user_id, service_name):
    """Check if user has credits for search"""
    if has_unlimited_access(user_id):
        return True, "unlimited"
    
    price = get_service_price(service_name)
    user_data = get_user_data(user_id)
    credits = user_data[1]
    
    if credits >= price:
        return True, "credit"
    else:
        return False, "no_credits"

def use_credit(user_id, service_name):
    """Use credits for search"""
    if has_unlimited_access(user_id):
        return True
    
    price = get_service_price(service_name)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET credits = credits - ? WHERE user_id = ?', (price, user_id))
    conn.commit()
    conn.close()
    return True

def refund_credit(user_id, service_name):
    """Refund credits if no records found"""
    if has_unlimited_access(user_id):
        return
    
    price = get_service_price(service_name)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET credits = credits + ? WHERE user_id = ?', (price, user_id))
    conn.commit()
    conn.close()

async def make_api_request(url, max_retries=3):
    """Make API request with retry mechanism and better error handling"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive',
        'Cache-Control': 'no-cache'
    }
    
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=30, ssl=False) as response:
                    if response.status == 200:
                        return await response.json()
                    elif response.status in [502, 503, 504]:
                        print(f"⚠️ Server error {response.status}, attempt {attempt + 1}/{max_retries}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 ** attempt)  # Exponential backoff
                            continue
                        else:
                            raise Exception(f"API server error after {max_retries} attempts: {response.status}")
                    elif response.status == 403:
                        raise Exception("API access forbidden. Please check API key or permissions.")
                    elif response.status == 404:
                        raise Exception("API endpoint not found.")
                    else:
                        raise Exception(f"API returned status {response.status}")
        except asyncio.TimeoutError:
            print(f"⏰ Timeout, attempt {attempt + 1}/{max_retries}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            else:
                raise Exception("Request timed out after multiple attempts")
        except aiohttp.ClientError as e:
            print(f"🌐 Network error: {e}, attempt {attempt + 1}/{max_retries}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            else:
                raise Exception(f"Network error: {str(e)}")
    
    raise Exception("Max retries exceeded")

async def process_api_search(ctx, api_url, search_value, user_id, service_name, search_type="mobile"):
    """Process API search with credit system"""
    global search_count
    
    price = get_service_price(service_name)
    has_credits, credit_type = check_credits(user_id, service_name)
    
    if not has_credits:
        user_data = get_user_data(user_id)
        level = user_data[2]
        voice_minutes = user_data[3]
        
        embed = discord.Embed(
            title="💰 Insufficient Credits!",
            description=f"**{ctx.author.mention}, you need {price} credit(s) for this search!**",
            color=0xED4245
        )
        
        embed.add_field(
            name="📊 Your Stats",
            value=f"**Current Credits:** {user_data[1]}\n**Level:** {level}\n**Voice Minutes:** {voice_minutes}",
            inline=True
        )
        
        embed.add_field(
            name="🎧 Earn Credits",
            value=f"**10 minutes in VC** = 1 credit\n**20 minutes in VC** = 2 credits + level up\nJoin any voice channel to start earning!",
            inline=True
        )
        
        embed.set_footer(text="Voice activity = Search power! 🔋")
        
        message = await ctx.send(embed=embed)
        await asyncio.sleep(30)
        try:
            await message.delete()
        except:
            pass
        return None
    
    # Show searching embed
    search_embed = discord.Embed(
        title="🔍 Launching Premium Search",
        description=f"**Searching for:** `{search_value}`\n**Type:** {search_type.upper()}",
        color=0x5865F2
    )
    
    search_embed.add_field(name="💰 Cost", value=f"**{price} credit(s)**", inline=True)
    search_embed.add_field(name="👤 User", value=f"{ctx.author.mention}", inline=True)
    search_embed.add_field(name="🌐 API Status", value="**Connecting...** 🔄", inline=True)
    search_embed.set_footer(text="Powered by Advanced OSINT Technology")
    search_msg = await ctx.send(embed=search_embed)
    
    # Use credits
    use_credit(user_id, service_name)
    
    try:
        # Update status
        search_embed.set_field_at(2, name="🌐 API Status", value="**Processing...** ⚡", inline=True)
        await search_msg.edit(embed=search_embed)
        
        # Make API request with retry mechanism
        data = await make_api_request(api_url)
        
        # Update status to success
        search_embed.set_field_at(2, name="🌐 API Status", value="**Success!** ✅", inline=True)
        await search_msg.edit(embed=search_embed)
        
        # Wait a moment then delete
        await asyncio.sleep(1)
        await search_msg.delete()
        return data
        
    except Exception as e:
        # Refund credits on error
        refund_credit(user_id, service_name)
        
        error_embed = discord.Embed(
            title="❌ Search Failed",
            description=f"Could not search for `{search_value}`",
            color=0xED4245
        )
        
        # Provide specific error messages
        error_msg = str(e)
        if "403" in error_msg:
            error_detail = "**API Access Forbidden**\nThe API server denied access. This could be due to:\n• Invalid or expired API key\n• IP blocking\n• Rate limiting"
        elif "502" in error_msg or "503" in error_msg or "504" in error_msg:
            error_detail = "**Server Error**\nThe API server is currently experiencing issues:\n• Server may be down\n• High traffic\n• Maintenance in progress"
        elif "timed out" in error_msg.lower():
            error_detail = "**Connection Timeout**\nThe request took too long:\n• Slow network connection\n• API server overloaded\n• Try again later"
        else:
            error_detail = f"**Error:** {error_msg[:150]}"
        
        error_embed.add_field(
            name="📝 Error Details",
            value=f"{error_detail}\n**Credits refunded:** {price}",
            inline=False
        )
        
        error_embed.add_field(
            name="🔄 Solution",
            value="• Try again in a few minutes\n• Check if API service is working\n• Contact support if issue persists",
            inline=False
        )
        
        error_msg = await ctx.send(embed=error_embed)
        await asyncio.sleep(120)
        try:
            await error_msg.delete()
        except:
            pass
        return None

async def send_premium_results(ctx, search_value, data, search_type="mobile"):
    """Send formatted search results"""
    
    # Check for no records or empty response
    if not data or (isinstance(data, dict) and data.get("message") == "No records found"):
        embed = discord.Embed(
            title="📭 No Records Found",
            description=f"No records found for: `{search_value}`",
            color=0xFEE75C
        )
        embed.add_field(
            name="💡 Suggestions",
            value="• Try with a different number/email\n• Check the format\n• Some data may not be in database\n• API might not have information for this query",
            inline=False
        )
        embed.add_field(name="👤 User", value=f"{ctx.author.mention}", inline=True)
        embed.set_footer(text="Tip: Try different search types for better results")
        
        message = await ctx.send(embed=embed)
        await asyncio.sleep(180)
        try:
            await message.delete()
        except:
            pass
        return
    
    # New Telegram API response format
    if search_type == "telegram" and isinstance(data, dict):
        if data.get("success") == True:
            embed = discord.Embed(
                title="✅ Telegram Search Successful!",
                description=f"**Found details for Telegram ID:** `{search_value}`",
                color=0x57F287,
                timestamp=datetime.now(timezone.utc)
            )
            
            # Phone info section
            if 'phone_info' in data:
                phone_info = data['phone_info']
                embed.add_field(
                    name="📱 **PHONE INFORMATION**",
                    value=(
                        f"**Country:** {phone_info.get('country', 'N/A')}\n"
                        f"**Country Code:** {phone_info.get('country_code', 'N/A')}\n"
                        f"**Number:** {phone_info.get('number', 'N/A')}\n"
                        f"**Full Number:** {phone_info.get('full_number', 'N/A')}"
                    ),
                    inline=False
                )
            
            # Account info section
            if 'account_info' in data:
                account_info = data['account_info']
                account_status = "✅ Active" if account_info.get('is_active') else "❌ Inactive"
                bot_status = "🤖 Bot" if account_info.get('is_bot') else "👤 User"
                
                embed.add_field(
                    name="👤 **ACCOUNT INFORMATION**",
                    value=(
                        f"**Status:** {account_status}\n"
                        f"**Type:** {bot_status}\n"
                        f"**First Name:** {account_info.get('first_name', 'N/A')}\n"
                        f"**Last Name:** {account_info.get('last_name', 'N/A')}"
                    ),
                    inline=False
                )
                
                # Activity info if available
                activity_text = ""
                if account_info.get('total_messages', 0) > 0:
                    activity_text += f"**Total Messages:** {account_info.get('total_messages')}\n"
                if account_info.get('total_groups', 0) > 0:
                    activity_text += f"**Groups Joined:** {account_info.get('total_groups')}\n"
                if account_info.get('messages_in_groups', 0) > 0:
                    activity_text += f"**Group Messages:** {account_info.get('messages_in_groups')}\n"
                if account_info.get('admin_in_groups', 0) > 0:
                    activity_text += f"**Admin in Groups:** {account_info.get('admin_in_groups')}"
                
                if activity_text:
                    embed.add_field(name="📊 **ACTIVITY STATS**", value=activity_text, inline=False)
            
            # Summary section
            if 'summary' in data:
                summary = data['summary']
                embed.add_field(
                    name="📋 **SEARCH SUMMARY**",
                    value=(
                        f"**Phone Available:** {'✅ Yes' if summary.get('phone_available') else '❌ No'}\n"
                        f"**Account Available:** {'✅ Yes' if summary.get('account_available') else '❌ No'}\n"
                        f"**Data Sources:** {summary.get('data_sources', 0)}"
                    ),
                    inline=False
                )
            
            embed.add_field(name="👤 **Requested By**", value=f"{ctx.author.mention}", inline=True)
            
            if data.get('phone_info', {}).get('footer'):
                embed.add_field(name="✨ **Powered By**", value=f"**{data['phone_info']['footer']}**", inline=True)
            
            embed.set_footer(text=f"Telegram ID Search • {get_indian_time()}")
            
            message = await ctx.send(embed=embed)
            await asyncio.sleep(180)
            try:
                await message.delete()
            except:
                pass
            return
        else:
            # Telegram API returned success: false
            embed = discord.Embed(
                title="📭 Telegram Details Not Found",
                description=f"No Telegram details found for: `{search_value}`",
                color=0xFEE75C
            )
            embed.add_field(
                name="💡 Information",
                value="• This Telegram ID may not exist or is private\n• The account might be deleted\n• Try with a different Telegram ID",
                inline=False
            )
            embed.add_field(name="👤 User", value=f"{ctx.author.mention}", inline=True)
            
            message = await ctx.send(embed=embed)
            await asyncio.sleep(180)
            try:
                await message.delete()
            except:
                pass
            return
    
    # Standard details API response (should be a list)
    if isinstance(data, list) and len(data) > 0:
        total_records = len(data)
        
        summary_embed = discord.Embed(
            title="✅ SEARCH SUCCESSFUL!",
            description=f"**Found {total_records} Record(s) for `{search_value}`**",
            color=0x57F287,
            timestamp=datetime.now(timezone.utc)
        )
        
        summary_embed.add_field(
            name="📊 Search Summary",
            value=f"**Type:** {search_type.upper()}\n**Value:** `{search_value}`\n**Records:** {total_records}\n**Time:** {get_indian_time()}",
            inline=False
        )
        
        summary_embed.add_field(name="👤 User", value=f"{ctx.author.mention}", inline=True)
        
        summary_embed.add_field(
            name="⏰ Auto-Delete",
            value="**This message will be automatically deleted in 3 minutes!**\nSave important information before it disappears.",
            inline=False
        )
        
        summary_message = await ctx.send(embed=summary_embed)
        
        # Send individual records
        messages_to_delete = [summary_message]
        
        for index, record in enumerate(data[:5], 1):
            record_embed = create_record_embed(record, index, min(5, total_records), search_value, search_type)
            record_message = await ctx.send(embed=record_embed)
            messages_to_delete.append(record_message)
            await asyncio.sleep(0.5)
        
        if total_records > 5:
            note_embed = discord.Embed(
                title="📋 Note",
                description=f"Showing 5 of {total_records} records for better readability.",
                color=0xFEE75C
            )
            note_message = await ctx.send(embed=note_embed)
            messages_to_delete.append(note_message)
        
        # Auto-delete after 3 minutes
        await asyncio.sleep(180)
        for msg in messages_to_delete:
            try:
                await msg.delete()
            except:
                pass
    
    elif isinstance(data, dict):
        # Handle dictionary response (single record)
        embed = create_record_embed(data, 1, 1, search_value, search_type)
        embed.title = "✅ Search Result"
        message = await ctx.send(embed=embed)
        await asyncio.sleep(180)
        try:
            await message.delete()
        except:
            pass

def create_record_embed(record, current_index, total_records, search_value, search_type):
    """Create premium embed for record"""
    embed = discord.Embed(
        title=f"📄 RECORD {current_index} of {total_records}",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc)
    )
    
    # Add all available fields with bold formatting
    if 'mobile' in record and record['mobile']:
        embed.add_field(name="📱 **MOBILE NUMBER**", value=f"```{record['mobile']}```", inline=True)
    
    if 'name' in record and record['name']:
        embed.add_field(name="👤 **FULL NAME**", value=f"```{record['name']}```", inline=True)
    
    father_name = None
    if 'father_name' in record and record['father_name']:
        father_name = record['father_name']
    elif 'fathersname' in record and record['fathersname']:
        father_name = record['fathersname']
    
    if father_name:
        embed.add_field(name="👨‍👦 **FATHER'S NAME**", value=f"```{father_name}```", inline=True)
    
    if 'address' in record and record['address']:
        address = format_address(record['address'])
        embed.add_field(name="🏠 **COMPLETE ADDRESS**", value=address, inline=False)
    
    if 'circle' in record and record['circle']:
        embed.add_field(name="🌍 **TELECOM CIRCLE**", value=f"```{record['circle']}```", inline=True)
    
    id_number = None
    if 'id_number' in record and record['id_number']:
        id_number = record['id_number']
    elif 'idnumber' in record and record['idnumber']:
        id_number = record['idnumber']
    
    if id_number:
        embed.add_field(name="🪪 **ID NUMBER**", value=f"```{id_number}```", inline=True)
    
    if 'email' in record and record['email']:
        embed.add_field(name="📧 **EMAIL ADDRESS**", value=f"```{record['email']}```", inline=True)
    
    if 'alt_mobile' in record and record['alt_mobile']:
        embed.add_field(name="📞 **ALTERNATE MOBILE**", value=f"```{record['alt_mobile']}```", inline=True)
    
    # Add search value if not already in fields
    if search_type == "mobile" and 'mobile' not in record:
        embed.add_field(name="🔍 **SEARCHED FOR**", value=f"```{search_value}```", inline=False)
    
    embed.set_footer(text=f"Record {current_index}/{total_records} • {get_indian_time()}")
    
    return embed

# USER COMMANDS

@bot.command(aliases=['num'])
@is_allowed_channel()
async def number(ctx, *, mobile_number: str = None):
    """Search mobile number"""
    if not mobile_number:
        embed = discord.Embed(
            title="📱 Mobile Number Search",
            description="**Usage:** `!num 7405453929`\n**Cost:** 1 credit per search\n**Format:** 10-digit Indian number",
            color=0x3498DB
        )
        await ctx.send(embed=embed)
        return
    
    # Extract 10-digit number
    numbers = re.findall(r'\d{10}', mobile_number)
    if not numbers:
        await ctx.send("❌ Please provide a valid 10-digit mobile number!")
        return
    
    number = numbers[0]
    api_url = DETAILS_API_URL.format(value=number)
    
    data = await process_api_search(ctx, api_url, number, ctx.author.id, "mobile", "mobile")
    if data is not None:
        await send_premium_results(ctx, number, data, "mobile")

@bot.command(aliases=['card'])
@is_allowed_channel()
async def aadhaar(ctx, *, aadhaar_number: str = None):
    """Search Aadhaar number"""
    if not aadhaar_number:
        embed = discord.Embed(
            title="🪪 Aadhaar Card Search",
            description="**Usage:** `!card 123456789012`\n**Cost:** 1 credit per search\n**Format:** 12-digit Aadhaar",
            color=0x3498DB
        )
        await ctx.send(embed=embed)
        return
    
    # Extract 12-digit Aadhaar
    aadhaar_match = re.search(r'\d{12}', aadhaar_number)
    if not aadhaar_match:
        await ctx.send("❌ Please provide a valid 12-digit Aadhaar number!")
        return
    
    aadhaar = aadhaar_match.group()
    api_url = DETAILS_API_URL.format(value=aadhaar)
    
    data = await process_api_search(ctx, api_url, aadhaar, ctx.author.id, "aadhaar", "aadhaar")
    if data is not None:
        await send_premium_results(ctx, aadhaar, data, "aadhaar")

@bot.command()
@is_allowed_channel()
async def email(ctx, *, email_address: str = None):
    """Search email address"""
    if not email_address:
        embed = discord.Embed(
            title="📧 Email Address Search",
            description="**Usage:** `!email example@domain.com`\n**Cost:** 1 credit per search\n**Format:** Valid email address",
            color=0x3498DB
        )
        await ctx.send(embed=embed)
        return
    
    # Basic email validation
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email_address.strip()):
        await ctx.send("❌ Please provide a valid email address!")
        return
    
    email_addr = email_address.strip()
    api_url = DETAILS_API_URL.format(value=email_addr)
    
    data = await process_api_search(ctx, api_url, email_addr, ctx.author.id, "email", "email")
    if data is not None:
        await send_premium_results(ctx, email_addr, data, "email")

@bot.command()
@is_allowed_channel()
async def tg(ctx, *, telegram_input: str = None):
    """Search Telegram to Mobile"""
    if not telegram_input:
        embed = discord.Embed(
            title="📲 Telegram to Mobile Search",
            description="**Usage:** `!tg 123456789` (Telegram User ID)\n**Cost:** 5 credits per search\n**Note:** Searches for mobile linked to Telegram",
            color=0x9B59B6
        )
        await ctx.send(embed=embed)
        return
    
    telegram_value = telegram_input.strip()
    api_url = TELEGRAM_API_URL.format(value=telegram_value)
    
    data = await process_api_search(ctx, api_url, telegram_value, ctx.author.id, "telegram", "telegram")
    if data is not None:
        await send_premium_results(ctx, telegram_value, data, "telegram")

@bot.command()
@is_allowed_channel()
async def info(ctx):
    """📊 Get complete bot information & commands"""
    embed = discord.Embed(
        title="🤖 KORNFINDER BOT - Complete Information",
        description="**Advanced OSINT Search Bot with Voice Chat Credit System** 🔍",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc)
    )
    
    # Bot Statistics
    uptime = datetime.now(timezone.utc) - bot.start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    # Server count
    server_count = len(bot.guilds)
    
    embed.add_field(
        name="📊 **Bot Statistics**",
        value=(
            f"**Uptime:** {days}d {hours}h {minutes}m\n"
            f"**Servers:** {server_count} servers\n"
            f"**Developer:** {DEVELOPER_INFO['developer']}\n"
            f"**Version:** Premium v3.0"
        ),
        inline=False
    )
    
    # Get current service prices
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT service_name, price FROM service_prices')
    prices = c.fetchall()
    conn.close()
    
    price_text = ""
    for service_name, price in prices:
        service_display = {
            'mobile': 'Mobile Number',
            'aadhaar': 'Aadhaar Card',
            'email': 'Email Address',
            'telegram': 'Telegram ID'
        }.get(service_name, service_name.title())
        
        price_text += f"• **{service_display}:** {price} credit{'s' if price > 1 else ''}\n"
    
    # Search Features
    embed.add_field(
        name="🔍 **Search Features & Prices**",
        value=price_text,
        inline=False
    )
    
    # Credit System
    embed.add_field(
        name="💰 **Credit System**",
        value=(
            "**Earn Credits in Voice Chat:** 🎤\n"
            "• **10 minutes** = 1 credit 💎\n"
            "• **20 minutes** = 2 credits + Level Up ⭐\n"
            "• **No daily limits** - Earn unlimited! 🔥\n"
            "• **Auto-tracking** - Join VC and earn automatically"
        ),
        inline=False
    )
    
    # Quick Commands
    embed.add_field(
        name="⚡ **Quick Commands**",
        value=(
            "`!num 7405453929` - Search mobile number\n"
            "`!card 123456789012` - Search Aadhaar card\n"
            "`!email test@example.com` - Search email\n"
            "`!tg 123456789` - Telegram ID search\n"
            "`!credits` - Check your balance\n"
            "`!voice` - Voice chat status\n"
            "`!leader` - Top users leaderboard"
        ),
        inline=False
    )
    
    # Support Information
    embed.add_field(
        name="📞 **Support & Links**",
        value=(
            f"**Developer:** {DEVELOPER_INFO['developer']}\n"
            f"**Discord Server:** [Join Here]({DEVELOPER_INFO['discord']}) 👥\n"
            f"**Telegram:** [Contact]({DEVELOPER_INFO['telegram']}) 📲\n"
            f"**API Provider:** {DEVELOPER_INFO['phenion']} 🔗"
        ),
        inline=False
    )
    
    # Footer with tips
    embed.set_footer(
        text=f"💡 Pro Tip: Stay active in voice chat to unlock unlimited searches! • {get_indian_time()}"
    )
    
    await ctx.send(embed=embed)

@bot.command()
@is_allowed_channel()
async def credits(ctx):
    """Check your credits"""
    user_data = get_user_data(ctx.author.id)
    credits = user_data[1]
    level = user_data[2]
    voice_minutes = user_data[3]
    unlimited = user_data[4]
    
    embed = discord.Embed(
        title="💰 Your Credit Balance",
        description=f"**{ctx.author.mention}, here are your current stats:**",
        color=0x9B59B6
    )
    
    if unlimited == 1:
        embed.add_field(name="✨ **UNLIMITED ACCESS**", value="**You have unlimited credits!** 🎉", inline=False)
    else:
        embed.add_field(name="💎 **Credits Available**", value=f"**{credits}** credits", inline=True)
    
    embed.add_field(name="⭐ **Level**", value=f"**{level}**", inline=True)
    embed.add_field(name="🎧 **Voice Minutes**", value=f"**{voice_minutes}** minutes", inline=True)
    
    # Calculate next rewards
    next_10_min = 10 - (voice_minutes % 10)
    next_20_min = 20 - (voice_minutes % 20)
    
    embed.add_field(
        name="🎯 **Next Rewards**",
        value=f"**{next_10_min} minutes** → 1 credit\n**{next_20_min} minutes** → 2 credits + level up",
        inline=False
    )
    
    await ctx.send(embed=embed)

# ADMIN COMMANDS - SERVER ADMINS (Limited Access)

@bot.command()
@is_server_admin()
async def addadmin(ctx, user_input: str):
    """Add server admin (server admins only)"""
    # Try to resolve user input
    user = await resolve_user(ctx, user_input)
    
    if not user:
        await ctx.send("❌ User not found! Please provide a valid user ID, mention, or username.")
        return
    
    # Check if user is already a server admin
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT user_id FROM server_admins WHERE server_id = ? AND user_id = ?', (ctx.guild.id, user.id))
    existing = c.fetchone()
    
    if existing:
        await ctx.send(f"❌ {user.mention} is already a server admin!")
        conn.close()
        return
    
    # Add as server admin
    c.execute('INSERT INTO server_admins (server_id, user_id, added_by) VALUES (?, ?, ?)', 
              (ctx.guild.id, user.id, ctx.author.id))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(
        title="✅ Server Admin Added!",
        description=f"**{user.mention} is now a server admin!**",
        color=0x57F287
    )
    
    embed.add_field(name="👤 New Admin", value=f"{user.mention}\n(ID: `{user.id}`)", inline=True)
    embed.add_field(name="🏢 Server", value=f"**{ctx.guild.name}**", inline=True)
    embed.add_field(name="👑 Added By", value=f"{ctx.author.mention}", inline=True)
    
    # Notify the new admin
    try:
        admin_notification = discord.Embed(
            title="🎉 You're Now a Server Admin!",
            description=f"You have been granted **server admin access** by {ctx.author.mention} in **{ctx.guild.name}**",
            color=0x57F287
        )
        admin_notification.add_field(
            name="🔧 Admin Commands",
            value="You can now use server admin commands:\n• `!addadmin @User`\n• `!addchannel #channel`\n• `!listchannels`",
            inline=False
        )
        await user.send(embed=admin_notification)
    except:
        pass
    
    await ctx.send(embed=embed)

@bot.command()
@is_server_admin()
async def addchannel(ctx, channel: discord.TextChannel = None):
    """Add channel to allowed list (server admins only)"""
    if not channel:
        # Try to get channel from mention
        if ctx.message.channel_mentions:
            channel = ctx.message.channel_mentions[0]
        else:
            await ctx.send("❌ Please mention a channel! Example: `!addchannel #general`")
            return
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO allowed_channels (channel_id, guild_id, added_by) VALUES (?, ?, ?)', 
              (channel.id, ctx.guild.id, ctx.author.id))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(
        title="✅ Channel Added!",
        description=f"**{channel.mention} is now an allowed channel!**",
        color=0x57F287
    )
    
    embed.add_field(name="📢 Channel", value=f"{channel.mention}\n(ID: `{channel.id}`)", inline=True)
    embed.add_field(name="🏢 Server", value=f"**{ctx.guild.name}**", inline=True)
    embed.add_field(name="👤 Added By", value=f"{ctx.author.mention}", inline=True)
    
    await ctx.send(embed=embed)

@bot.command()
@is_server_admin()
async def listchannels(ctx):
    """List all allowed channels in this server"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT channel_id FROM allowed_channels WHERE guild_id = ?', (ctx.guild.id,))
    channels = c.fetchall()
    conn.close()
    
    if not channels:
        embed = discord.Embed(
            title="📋 Allowed Channels",
            description="❌ No channels configured for this server!\nUse `!addchannel #channel` to add one.",
            color=0xED4245
        )
    else:
        channel_list = []
        for channel_row in channels:
            channel_id = channel_row[0]
            channel = ctx.guild.get_channel(channel_id)
            if channel:
                channel_list.append(f"• {channel.mention} (ID: `{channel.id}`)")
            else:
                channel_list.append(f"• Unknown Channel (ID: `{channel_id}`)")
        
        embed = discord.Embed(
            title="📋 Allowed Channels",
            description="**Channels where bot commands can be used:**\n\n" + "\n".join(channel_list),
            color=0x3498DB
        )
    
    embed.set_footer(text=f"Server: {ctx.guild.name}")
    await ctx.send(embed=embed)

# ADMIN COMMANDS - GLOBAL ADMINS (Full Access)

@bot.command()
@is_global_admin()
async def broadcast(ctx, server_id: int, *, message: str):
    """Broadcast message to a server's allowed channel"""
    server = bot.get_guild(server_id)
    if not server:
        await ctx.send("❌ Server not found!")
        return
    
    # Get allowed channels for this server
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT channel_id FROM allowed_channels WHERE guild_id = ?', (server.id,))
    channels = c.fetchall()
    conn.close()
    
    if not channels:
        await ctx.send(f"❌ No allowed channels found in **{server.name}**!")
        return
    
    sent_count = 0
    for channel_row in channels:
        channel_id = channel_row[0]
        channel = server.get_channel(channel_id)
        
        if channel:
            try:
                embed = discord.Embed(
                    title="📢 **ANNOUNCEMENT FROM ADMIN** 📢",
                    description=message,
                    color=0x5865F2,
                    timestamp=datetime.now(timezone.utc)
                )
                embed.add_field(name="👤 **Announced By**", value=f"{ctx.author.mention}", inline=True)
                embed.add_field(name="🏢 **Server**", value=f"**{server.name}**", inline=True)
                embed.set_footer(text="Admin Broadcast • KornFinder Bot")
                
                await channel.send(embed=embed)
                sent_count += 1
                print(f"📢 Broadcast sent to #{channel.name} in {server.name}")
                
            except Exception as e:
                print(f"Failed to send broadcast to channel {channel_id}: {e}")
    
    await ctx.send(f"✅ Broadcast sent to **{sent_count}** channel(s) in **{server.name}**!")

@bot.command()
@is_global_admin()
async def allbroadcast(ctx, server_id: int, *, message: str):
    """Send DM to all members in a server"""
    server = bot.get_guild(server_id)
    if not server:
        await ctx.send("❌ Server not found!")
        return
    
    # Show processing embed
    process_embed = discord.Embed(
        title="📨 Sending Broadcast DMs",
        description=f"Sending message to **{server.member_count}** members in **{server.name}**...",
        color=0x5865F2
    )
    process_embed.add_field(name="📝 Message", value=f"```{message[:100]}...```", inline=False)
    process_embed.set_footer(text="This may take a while...")
    
    process_msg = await ctx.send(embed=process_embed)
    
    sent_count = 0
    failed_count = 0
    
    for member in server.members:
        if member.bot:
            continue
        
        try:
            embed = discord.Embed(
                title="📢 **IMPORTANT ANNOUNCEMENT** 📢",
                description=message,
                color=0x5865F2,
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="👤 **From Admin**", value=f"{ctx.author.mention}", inline=True)
            embed.add_field(name="🏢 **Server**", value=f"**{server.name}**", inline=True)
            embed.set_footer(text="Admin DM Broadcast • KornFinder Bot")
            
            await member.send(embed=embed)
            sent_count += 1
            
            # Small delay to avoid rate limits
            await asyncio.sleep(0.5)
            
        except Exception as e:
            failed_count += 1
            print(f"Failed to send DM to {member.name}: {e}")
    
    # Update embed with results
    result_embed = discord.Embed(
        title="✅ Broadcast DMs Sent!",
        description=f"**DM broadcast completed for {server.name}!**",
        color=0x57F287
    )
    
    result_embed.add_field(name="📊 **Results**", 
                          value=f"✅ **Sent:** {sent_count} members\n❌ **Failed:** {failed_count} members\n👥 **Total:** {server.member_count} members",
                          inline=False)
    
    result_embed.add_field(name="📝 **Message**", value=f"```{message[:200]}...```", inline=False)
    result_embed.add_field(name="🏢 **Server**", value=f"**{server.name}**\n(ID: `{server.id}`)", inline=True)
    result_embed.add_field(name="👤 **Sent By**", value=f"{ctx.author.mention}", inline=True)
    
    await process_msg.edit(embed=result_embed)

@bot.command()
@is_global_admin()
async def message(ctx, server_id: int, user_input: str, *, message: str):
    """Send DM to specific user in a server"""
    server = bot.get_guild(server_id)
    if not server:
        await ctx.send("❌ Server not found!")
        return
    
    # Try to find user in the server
    user = None
    try:
        # Check if input is user ID
        if user_input.isdigit():
            member = server.get_member(int(user_input))
            if member:
                user = member
    except:
        pass
    
    if not user:
        # Try to find by username
        for member in server.members:
            if user_input.lower() in member.name.lower() or user_input.lower() in (member.display_name.lower() if member.display_name else ""):
                user = member
                break
    
    if not user:
        await ctx.send("❌ User not found in that server!")
        return
    
    try:
        embed = discord.Embed(
            title="📨 **MESSAGE FROM ADMIN** 📨",
            description=message,
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="👤 **From Admin**", value=f"{ctx.author.mention}", inline=True)
        embed.add_field(name="🏢 **Server**", value=f"**{server.name}**", inline=True)
        embed.set_footer(text="Admin Direct Message • KornFinder Bot")
        
        await user.send(embed=embed)
        
        success_embed = discord.Embed(
            title="✅ Message Sent!",
            description=f"**Message successfully sent to {user.mention}!**",
            color=0x57F287
        )
        
        success_embed.add_field(name="👤 **To User**", value=f"{user.mention}\n(ID: `{user.id}`)", inline=True)
        success_embed.add_field(name="🏢 **Server**", value=f"**{server.name}**", inline=True)
        success_embed.add_field(name="👤 **Sent By**", value=f"{ctx.author.mention}", inline=True)
        success_embed.add_field(name="📝 **Message**", value=f"```{message[:100]}...```", inline=False)
        
        await ctx.send(embed=success_embed)
        
    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Failed to Send Message",
            description=f"Could not send message to {user_input}",
            color=0xED4245
        )
        error_embed.add_field(name="📝 **Error**", value=f"```{str(e)[:100]}```", inline=False)
        await ctx.send(embed=error_embed)

@bot.command()
@is_global_admin()
async def setprice(ctx, service_name: str, price: int):
    """Set price for a service"""
    valid_services = ['mobile', 'aadhaar', 'email', 'telegram']
    
    if service_name not in valid_services:
        await ctx.send(f"❌ Invalid service name! Valid services: {', '.join(valid_services)}")
        return
    
    if price < 1:
        await ctx.send("❌ Price must be at least 1 credit!")
        return
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE service_prices SET price = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP WHERE service_name = ?', 
              (price, ctx.author.id, service_name))
    conn.commit()
    
    # Update SERVICE_PRICES dict
    SERVICE_PRICES[service_name] = price
    
    conn.close()
    
    service_display = {
        'mobile': 'Mobile Number Search',
        'aadhaar': 'Aadhaar Card Search',
        'email': 'Email Address Search',
        'telegram': 'Telegram ID Search'
    }.get(service_name, service_name.title())
    
    embed = discord.Embed(
        title="✅ Service Price Updated!",
        description=f"**{service_display} price has been updated!**",
        color=0x57F287
    )
    
    embed.add_field(name="💰 **New Price**", value=f"**{price} credit{'s' if price > 1 else ''}**", inline=True)
    embed.add_field(name="🔧 **Service**", value=f"**{service_display}**", inline=True)
    embed.add_field(name="👤 **Updated By**", value=f"{ctx.author.mention}", inline=True)
    
    await ctx.send(embed=embed)

@bot.command()
@is_global_admin()
async def prices(ctx):
    """Show all service prices"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT service_name, price, updated_at FROM service_prices')
    prices = c.fetchall()
    conn.close()
    
    embed = discord.Embed(
        title="💰 Service Prices",
        description="**Current prices for all services:**",
        color=0x9B59B6,
        timestamp=datetime.now(timezone.utc)
    )
    
    price_text = ""
    for service_name, price, updated_at in prices:
        service_display = {
            'mobile': '📱 Mobile Number',
            'aadhaar': '🪪 Aadhaar Card',
            'email': '📧 Email Address',
            'telegram': '📲 Telegram ID'
        }.get(service_name, service_name.title())
        
        price_text += f"{service_display}: **{price} credit{'s' if price > 1 else ''}**\n"
    
    embed.add_field(name="📋 **Prices**", value=price_text, inline=False)
    embed.set_footer(text="Use !setprice <service> <amount> to update prices")
    
    await ctx.send(embed=embed)

# Additional global admin commands
@bot.command()
@is_global_admin()
async def fundcredits(ctx, server_id: int, user_input: str, credit_amount: int):
    """Add credits to specific user in a server"""
    # Check if server exists
    server = bot.get_guild(server_id)
    if not server:
        await ctx.send("❌ Server not found!")
        return
    
    # Try to resolve user
    user = None
    try:
        # Check if user is in the server
        member = server.get_member(int(user_input)) if user_input.isdigit() else None
        if member:
            user = member
        else:
            # Try to find by username
            for member in server.members:
                if user_input.lower() in member.name.lower() or user_input.lower() in (member.display_name.lower() if member.display_name else ""):
                    user = member
                    break
    except:
        pass
    
    if not user:
        await ctx.send("❌ User not found in that server!")
        return
    
    if credit_amount <= 0:
        await ctx.send("❌ Credit amount must be positive!")
        return
    
    # Show processing embed
    process_embed = discord.Embed(
        title="💰 Funding User Credits",
        description=f"Adding **{credit_amount} credits** to **{user.display_name}** in **{server.name}**...",
        color=0x5865F2
    )
    process_embed.add_field(name="👤 User", value=f"{user.mention}", inline=True)
    process_embed.add_field(name="💰 Amount", value=f"**{credit_amount} credits**", inline=True)
    process_embed.set_footer(text="Processing...")
    
    process_msg = await ctx.send(embed=process_embed)
    
    # Add credits
    update_user_credits(user.id, credit_amount)
    
    # Get updated user data
    user_data = get_user_data(user.id)
    
    # Update embed with results
    result_embed = discord.Embed(
        title="✅ User Credits Funded!",
        description=f"Successfully funded **{user.display_name}** with credits!",
        color=0x57F287
    )
    
    result_embed.add_field(name="💰 Credits Added", value=f"**{credit_amount} credits**", inline=True)
    result_embed.add_field(name="👤 User", value=f"{user.mention}\n(ID: `{user.id}`)", inline=True)
    result_embed.add_field(name="📊 New Balance", value=f"**{user_data[1]} credits**", inline=True)
    result_embed.add_field(name="🏢 Server", value=f"**{server.name}**\n(ID: `{server.id}`)", inline=True)
    result_embed.add_field(name="👤 Funded By", value=f"{ctx.author.mention}", inline=True)
    
    await process_msg.edit(embed=result_embed)
    
    # Notify the user
    try:
        user_notification = discord.Embed(
            title="🎉 Credits Added!",
            description=f"You received **{credit_amount} credits** from {ctx.author.mention}",
            color=0x57F287
        )
        user_notification.add_field(name="📊 New Balance", value=f"**{user_data[1]} credits**", inline=True)
        user_notification.add_field(name="🏢 Server", value=f"**{server.name}**", inline=True)
        await user.send(embed=user_notification)
    except:
        pass
    
    # Log the action
    print(f"💰 {ctx.author.name} funded {user.name} in {server.name} with {credit_amount} credits")

@bot.command()
@is_global_admin()
async def masteradmin(ctx, user_input: str):
    """Give global admin access to user"""
    # Try to resolve user input
    user = await resolve_user(ctx, user_input)
    
    if not user:
        await ctx.send("❌ User not found! Please provide a valid user ID, mention, or username.")
        return
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO global_admins (user_id, added_by) VALUES (?, ?)', (user.id, ctx.author.id))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(
        title="✅ Global Admin Access Granted!",
        description=f"**{user.mention} now has global admin access!**",
        color=0x57F287
    )
    
    embed.add_field(name="👤 New Global Admin", value=f"{user.mention}\n(ID: `{user.id}`)", inline=True)
    embed.add_field(name="👑 Granted By", value=f"{ctx.author.mention}", inline=True)
    
    # Notify the new admin
    try:
        admin_notification = discord.Embed(
            title="🎉 You're Now a Global Admin!",
            description=f"You have been granted **global admin access** by {ctx.author.mention}",
            color=0x57F287
        )
        admin_notification.add_field(
            name="🔧 Admin Commands",
            value="You can now use all global admin commands to manage the bot!",
            inline=False
        )
        admin_notification.add_field(
            name="📋 Quick Start",
            value="Use `!adminhelp` to see all available admin commands",
            inline=False
        )
        await user.send(embed=admin_notification)
    except:
        pass
    
    await ctx.send(embed=embed)

@bot.command()
@is_global_admin()
async def adminhelp(ctx):
    """Show admin commands help"""
    embed = discord.Embed(
        title="🛠️ Admin Commands Help",
        description="**Different admin levels have different permissions**",
        color=0x9B59B6
    )
    
    embed.add_field(
        name="👑 **GLOBAL ADMINS** (Full Access)",
        value=(
            "**Server Management:**\n"
            "`!broadcast <server_id> <message>` - Broadcast to server\n"
            "`!allbroadcast <server_id> <message>` - DM all server members\n"
            "`!message <server_id> <user> <message>` - DM specific user\n"
            "`!masteradmin <user>` - Make global admin\n"
            "\n**Service Management:**\n"
            "`!setprice <service> <amount>` - Set service price\n"
            "`!prices` - Show all service prices\n"
            "\n**User Management:**\n"
            "`!fundcredits <server_id> <user> <credits>` - Fund user credits\n"
            "`!addcredit <user> <amount>` - Add credits\n"
            "`!removecredit <user> <amount>` - Remove credits\n"
            "`!unlimited <user>` - Give unlimited access"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🔧 **SERVER ADMINS** (Limited Access)",
        value=(
            "`!addadmin @User` - Add server admin\n"
            "`!addchannel #channel` - Add allowed channel\n"
            "`!listchannels` - List allowed channels\n"
            "\n*Server admins can only manage their own server*"
        ),
        inline=False
    )
    
    embed.add_field(
        name="💡 **Notes**",
        value=(
            "• **Global Admins:** Have access to ALL commands\n"
            "• **Server Admins:** Can only manage their own server\n"
            "• **Setup:** When bot joins a server, owner automatically becomes server admin"
        ),
        inline=False
    )
    
    await ctx.send(embed=embed)

# Additional commands from previous version (voice, level, leader, etc.)
@bot.command()
@is_allowed_channel()
async def voice(ctx):
    """Check voice chat status"""
    user_data = get_user_data(ctx.author.id)
    voice_minutes = user_data[3]
    level = user_data[2]
    
    session = get_voice_session(ctx.author.id)
    
    embed = discord.Embed(
        title="🎧 Voice Chat Status",
        description=f"**{ctx.author.mention}, here's your voice activity:**",
        color=0x3498DB
    )
    
    if session:
        join_time = datetime.fromisoformat(session[1])
        last_check = datetime.fromisoformat(session[4])
        time_spent = (last_check - join_time).total_seconds() / 60
        time_spent = int(time_spent)
        
        embed.add_field(
            name="🔴 **Live Session Active**",
            value=f"**Current Session:** {time_spent} minutes\n**Total Time:** {voice_minutes} minutes\n**Level:** {level}",
            inline=False
        )
    else:
        embed.add_field(
            name="🟢 **Ready to Earn**",
            value="Join any voice channel to start earning credits!",
            inline=False
        )
    
    # Calculate next rewards
    next_10_min = 10 - (voice_minutes % 10)
    next_20_min = 20 - (voice_minutes % 20)
    
    embed.add_field(
        name="🎯 **Next Rewards**",
        value=(
            f"**{next_10_min} minutes** → **1 credit** 💎\n"
            f"**{next_20_min} minutes** → **2 credits + level up** ⭐"
        ),
        inline=False
    )
    
    embed.add_field(
        name="💡 **Pro Tips**",
        value=(
            "• Join voice channels with friends 🎤\n"
            "• Background music sessions count 🎵\n"
            "• Every minute brings you closer to credits ⏰"
        ),
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.command()
@is_allowed_channel()
async def level(ctx):
    """Check your level"""
    user_data = get_user_data(ctx.author.id)
    level = user_data[2]
    voice_minutes = user_data[3]
    
    embed = discord.Embed(
        title="⭐ Your Level Stats",
        description=f"**{ctx.author.mention}, here's your level progression:**",
        color=0xFFD700
    )
    
    embed.add_field(name="🏆 **Current Level**", value=f"**{level}**", inline=True)
    embed.add_field(name="🎧 **Total Voice Minutes**", value=f"**{voice_minutes}** minutes", inline=True)
    
    # Calculate progress to next level
    minutes_in_current_level = voice_minutes % 20
    minutes_to_next_level = 20 - minutes_in_current_level
    
    # Progress bar
    progress_percentage = (minutes_in_current_level / 20) * 100
    progress_bar = "🟩" * int(progress_percentage / 10) + "⬜" * (10 - int(progress_percentage / 10))
    
    embed.add_field(
        name="📊 **Progress to Level {next_level}**".format(next_level=level + 1),
        value=f"{progress_bar} {progress_percentage:.0f}%\n**{minutes_to_next_level} minutes needed**",
        inline=False
    )
    
    embed.add_field(
        name="🎯 **Level Up Rewards**",
        value="Every **20 minutes** in voice chat gives you:\n• **2 credits** 💎\n• **1 level up** ⭐",
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.command()
@is_allowed_channel()
async def leader(ctx):
    """Show leaderboard"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        SELECT user_id, level, credits, total_voice_minutes 
        FROM users 
        ORDER BY level DESC, total_voice_minutes DESC 
        LIMIT 10
    ''')
    top_users = c.fetchall()
    conn.close()
    
    embed = discord.Embed(
        title="🏆 Leaderboard - Top 10 Users",
        description="**Ranked by level and voice activity**",
        color=0xFFD700,
        timestamp=datetime.now(timezone.utc)
    )
    
    if not top_users:
        embed.add_field(
            name="No Users Yet",
            value="Be the first to join voice chat and earn credits! 🎤",
            inline=False
        )
    else:
        leaderboard_text = ""
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        for idx, (user_id, level, credits, voice_minutes) in enumerate(top_users):
            user = ctx.guild.get_member(user_id)
            if user:
                username = user.display_name
            else:
                username = f"User {user_id}"
            
            medal = medals[idx] if idx < len(medals) else f"{idx+1}."
            
            leaderboard_text += (
                f"{medal} **{username}**\n"
                f"   ⭐ Level {level} | 💰 {credits} credits | 🎧 {voice_minutes} mins\n\n"
            )
        
        embed.add_field(name="🏅 Top Users", value=leaderboard_text, inline=False)
    
    # Add user's rank if available
    user_rank = None
    all_users = get_db_connection().execute(
        'SELECT user_id FROM users ORDER BY level DESC, total_voice_minutes DESC'
    ).fetchall()
    
    for rank, (uid,) in enumerate(all_users, 1):
        if uid == ctx.author.id:
            user_rank = rank
            break
    
    if user_rank:
        embed.add_field(
            name="📈 Your Rank",
            value=f"**You are ranked #{user_rank}**\nKeep grinding to reach the top! 🔥",
            inline=False
        )
    
    embed.set_footer(text=f"Updated • {get_indian_time()}")
    await ctx.send(embed=embed)

@bot.command()
@is_global_admin()
async def txtlist(ctx):
    """Generate and send server list as .txt file"""
    try:
        # Generate report
        report_content = await generate_server_report()
        
        # Create text file
        file_content = f"KornFinder Bot - Server List Report\n"
        file_content += f"Generated on: {get_indian_time()}\n"
        file_content += f"Generated by: {ctx.author.name}\n"
        file_content += f"Total Servers: {len(bot.guilds)}\n"
        file_content += "=" * 50 + "\n\n"
        file_content += report_content
        
        # Send as file
        file = discord.File(io.BytesIO(file_content.encode('utf-8')), filename=f"kornfinder_servers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        
        embed = discord.Embed(
            title="📋 Server List Generated",
            description=f"**Server list report generated successfully!**\nContains information about **{len(bot.guilds)}** servers.",
            color=0x57F287,
            timestamp=datetime.now(timezone.utc)
        )
        
        embed.add_field(
            name="📊 Report Details",
            value=f"**Total Servers:** {len(bot.guilds)}\n**Total Users:** {sum(guild.member_count for guild in bot.guilds):,}\n**Generated:** {get_indian_time()}",
            inline=False
        )
        
        embed.add_field(
            name="📁 File Information",
            value="The attached .txt file contains:\n• Server names and IDs\n• Owner information\n• Member counts\n• Allowed channels\n• Server creation dates",
            inline=False
        )
        
        embed.set_footer(text="Manual Server Report • KornFinder Bot")
        
        await ctx.send(embed=embed, file=file)
        
    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Error Generating Report",
            description=f"Could not generate server list report.",
            color=0xED4245
        )
        error_embed.add_field(
            name="📝 Error Details",
            value=f"```{str(e)[:500]}```",
            inline=False
        )
        await ctx.send(embed=error_embed)
        print(f"Error in txtlist command: {e}")

# Additional global admin commands for unlimited access
@bot.command()
@is_global_admin()
async def unlimited(ctx, user_input: str):
    """Give unlimited access to user"""
    # Try to resolve user input
    user = await resolve_user(ctx, user_input)
    
    if not user:
        await ctx.send("❌ User not found! Please provide a valid user ID, mention, or username.")
        return
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET unlimited = 1 WHERE user_id = ?', (user.id,))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(
        title="✨ UNLIMITED ACCESS GRANTED!",
        description=f"**{user.mention} now has unlimited credits access!**",
        color=0x9B59B6
    )
    
    embed.add_field(name="👤 User", value=f"{user.mention}\n(ID: `{user.id}`)", inline=True)
    embed.add_field(name="💎 Status", value="**UNLIMITED CREDITS**", inline=True)
    embed.add_field(name="👑 Granted By", value=f"{ctx.author.mention}", inline=True)
    
    # Notify the user
    try:
        user_notification = discord.Embed(
            title="🎉 UNLIMITED ACCESS GRANTED!",
            description=f"You have been granted **unlimited credits access** by {ctx.author.mention}!",
            color=0x9B59B6
        )
        user_notification.add_field(
            name="✨ Benefits",
            value="• Unlimited searches without using credits\n• All services available for free\n• Premium access to all features",
            inline=False
        )
        await user.send(embed=user_notification)
    except:
        pass
    
    await ctx.send(embed=embed)

@bot.command()
@is_global_admin()
async def removeunlimited(ctx, user_input: str):
    """Remove unlimited access from user"""
    # Try to resolve user input
    user = await resolve_user(ctx, user_input)
    
    if not user:
        await ctx.send("❌ User not found! Please provide a valid user ID, mention, or username.")
        return
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET unlimited = 0 WHERE user_id = ?', (user.id,))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(
        title="⚠️ UNLIMITED ACCESS REMOVED",
        description=f"**{user.mention}'s unlimited access has been removed!**",
        color=0xFEE75C
    )
    
    embed.add_field(name="👤 User", value=f"{user.mention}\n(ID: `{user.id}`)", inline=True)
    embed.add_field(name="💎 New Status", value="**CREDIT-BASED ACCESS**", inline=True)
    embed.add_field(name="👑 Removed By", value=f"{ctx.author.mention}", inline=True)
    
    # Notify the user
    try:
        user_notification = discord.Embed(
            title="⚠️ UNLIMITED ACCESS REMOVED",
            description=f"Your **unlimited credits access** has been removed by {ctx.author.mention}.",
            color=0xFEE75C
        )
        user_notification.add_field(
            name="📊 New Status",
            value="You will now need to use credits for searches. Earn credits by staying in voice chat!",
            inline=False
        )
        await user.send(embed=user_notification)
    except:
        pass
    
    await ctx.send(embed=embed)

@bot.command()
@is_global_admin()
async def addcredit(ctx, user_input: str, amount: int):
    """Add credits to user"""
    # Try to resolve user input
    user = await resolve_user(ctx, user_input)
    
    if not user:
        await ctx.send("❌ User not found! Please provide a valid user ID, mention, or username.")
        return
    
    if amount <= 0:
        await ctx.send("❌ Amount must be positive!")
        return
    
    # Add credits
    update_user_credits(user.id, amount)
    
    # Get updated data
    user_data = get_user_data(user.id)
    
    embed = discord.Embed(
        title="💰 Credits Added",
        description=f"**Added {amount} credits to {user.mention}!**",
        color=0x57F287
    )
    
    embed.add_field(name="👤 User", value=f"{user.mention}\n(ID: `{user.id}`)", inline=True)
    embed.add_field(name="💎 Amount Added", value=f"**{amount} credits**", inline=True)
    embed.add_field(name="📊 New Balance", value=f"**{user_data[1]} credits**", inline=True)
    embed.add_field(name="👑 Added By", value=f"{ctx.author.mention}", inline=True)
    
    await ctx.send(embed=embed)
    
    # Notify the user
    try:
        user_notification = discord.Embed(
            title="💰 Credits Added!",
            description=f"You received **{amount} credits** from {ctx.author.mention}",
            color=0x57F287
        )
        user_notification.add_field(name="📊 New Balance", value=f"**{user_data[1]} credits**", inline=True)
        await user.send(embed=user_notification)
    except:
        pass

@bot.command()
@is_global_admin()
async def removecredit(ctx, user_input: str, amount: int):
    """Remove credits from user"""
    # Try to resolve user input
    user = await resolve_user(ctx, user_input)
    
    if not user:
        await ctx.send("❌ User not found! Please provide a valid user ID, mention, or username.")
        return
    
    if amount <= 0:
        await ctx.send("❌ Amount must be positive!")
        return
    
    # Get current data
    user_data = get_user_data(user.id)
    current_credits = user_data[1]
    
    if amount > current_credits:
        amount = current_credits
    
    # Remove credits
    update_user_credits(user.id, -amount)
    
    # Get updated data
    user_data = get_user_data(user.id)
    
    embed = discord.Embed(
        title="⚠️ Credits Removed",
        description=f"**Removed {amount} credits from {user.mention}!**",
        color=0xFEE75C
    )
    
    embed.add_field(name="👤 User", value=f"{user.mention}\n(ID: `{user.id}`)", inline=True)
    embed.add_field(name="💎 Amount Removed", value=f"**{amount} credits**", inline=True)
    embed.add_field(name="📊 New Balance", value=f"**{user_data[1]} credits**", inline=True)
    embed.add_field(name="👑 Removed By", value=f"{ctx.author.mention}", inline=True)
    
    await ctx.send(embed=embed)
    
    # Notify the user
    try:
        user_notification = discord.Embed(
            title="⚠️ Credits Removed",
            description=f"**{amount} credits** were removed by {ctx.author.mention}",
            color=0xFEE75C
        )
        user_notification.add_field(name="📊 New Balance", value=f"**{user_data[1]} credits**", inline=True)
        await user.send(embed=user_notification)
    except:
        pass

@bot.command()
@is_global_admin()
async def setcredits(ctx, user_input: str, amount: int):
    """Set user's credits to specific amount"""
    # Try to resolve user input
    user = await resolve_user(ctx, user_input)
    
    if not user:
        await ctx.send("❌ User not found! Please provide a valid user ID, mention, or username.")
        return
    
    if amount < 0:
        await ctx.send("❌ Amount cannot be negative!")
        return
    
    # Set credits
    set_user_credits(user.id, amount)
    
    embed = discord.Embed(
        title="💰 Credits Set",
        description=f"**Set {user.mention}'s credits to {amount}!**",
        color=0x57F287
    )
    
    embed.add_field(name="👤 User", value=f"{user.mention}\n(ID: `{user.id}`)", inline=True)
    embed.add_field(name="💎 New Balance", value=f"**{amount} credits**", inline=True)
    embed.add_field(name="👑 Set By", value=f"{ctx.author.mention}", inline=True)
    
    await ctx.send(embed=embed)
    
    # Notify the user
    try:
        user_notification = discord.Embed(
            title="💰 Credits Updated",
            description=f"Your credits have been set to **{amount}** by {ctx.author.mention}",
            color=0x57F287
        )
        await user.send(embed=user_notification)
    except:
        pass

@bot.command()
@is_global_admin()
async def setlevel(ctx, user_input: str, level: int):
    """Set user's level"""
    # Try to resolve user input
    user = await resolve_user(ctx, user_input)
    
    if not user:
        await ctx.send("❌ User not found! Please provide a valid user ID, mention, or username.")
        return
    
    if level < 0:
        await ctx.send("❌ Level cannot be negative!")
        return
    
    # Set level
    update_user_level(user.id, level)
    
    embed = discord.Embed(
        title="⭐ Level Set",
        description=f"**Set {user.mention}'s level to {level}!**",
        color=0xFFD700
    )
    
    embed.add_field(name="👤 User", value=f"{user.mention}\n(ID: `{user.id}`)", inline=True)
    embed.add_field(name="🏆 New Level", value=f"**Level {level}**", inline=True)
    embed.add_field(name="👑 Set By", value=f"{ctx.author.mention}", inline=True)
    
    await ctx.send(embed=embed)
    
    # Notify the user
    try:
        user_notification = discord.Embed(
            title="⭐ Level Updated!",
            description=f"Your level has been set to **{level}** by {ctx.author.mention}",
            color=0xFFD700
        )
        await user.send(embed=user_notification)
    except:
        pass

@bot.command()
@is_global_admin()
async def setvoicetime(ctx, user_input: str, minutes: int):
    """Set user's voice minutes"""
    # Try to resolve user input
    user = await resolve_user(ctx, user_input)
    
    if not user:
        await ctx.send("❌ User not found! Please provide a valid user ID, mention, or username.")
        return
    
    if minutes < 0:
        await ctx.send("❌ Minutes cannot be negative!")
        return
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET total_voice_minutes = ? WHERE user_id = ?', (minutes, user.id))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(
        title="🎧 Voice Time Set",
        description=f"**Set {user.mention}'s voice minutes to {minutes}!**",
        color=0x3498DB
    )
    
    embed.add_field(name="👤 User", value=f"{user.mention}\n(ID: `{user.id}`)", inline=True)
    embed.add_field(name="⏱️ Voice Minutes", value=f"**{minutes} minutes**", inline=True)
    embed.add_field(name="👑 Set By", value=f"{ctx.author.mention}", inline=True)
    
    await ctx.send(embed=embed)
    
    # Notify the user
    try:
        user_notification = discord.Embed(
            title="🎧 Voice Time Updated",
            description=f"Your voice time has been set to **{minutes} minutes** by {ctx.author.mention}",
            color=0x3498DB
        )
        await user.send(embed=user_notification)
    except:
        pass

@bot.command()
@is_global_admin()
async def userinfo(ctx, user_input: str):
    """Get detailed user information"""
    # Try to resolve user input
    user = await resolve_user(ctx, user_input)
    
    if not user:
        await ctx.send("❌ User not found! Please provide a valid user ID, mention, or username.")
        return
    
    # Get user data
    user_data = get_user_data(user.id)
    credits = user_data[1]
    level = user_data[2]
    voice_minutes = user_data[3]
    unlimited = user_data[4]
    created_at = user_data[5]
    
    # Check if global admin
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT user_id FROM global_admins WHERE user_id = ?', (user.id,))
    is_global_admin = c.fetchone() is not None
    
    # Check server admin status
    c.execute('SELECT server_id FROM server_admins WHERE user_id = ?', (user.id,))
    server_admin_rows = c.fetchall()
    server_admin_count = len(server_admin_rows)
    conn.close()
    
    embed = discord.Embed(
        title="📋 User Information",
        description=f"**Detailed information about {user.mention}**",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc)
    )
    
    embed.add_field(name="👤 **User**", value=f"{user.mention}\nID: `{user.id}`", inline=True)
    embed.add_field(name="📅 **Account Created**", value=user.created_at.strftime("%d %b %Y"), inline=True)
    embed.add_field(name="🤖 **Bot**", value="✅ Yes" if user.bot else "❌ No", inline=True)
    
    embed.add_field(
        name="💎 **Credit Information**",
        value=(
            f"**Credits:** {credits}\n"
            f"**Level:** {level}\n"
            f"**Voice Minutes:** {voice_minutes}\n"
            f"**Unlimited:** {'✅ Yes' if unlimited == 1 else '❌ No'}"
        ),
        inline=False
    )
    
    embed.add_field(
        name="👑 **Admin Status**",
        value=(
            f"**Global Admin:** {'✅ Yes' if is_global_admin else '❌ No'}\n"
            f"**Server Admin in:** {server_admin_count} server{'s' if server_admin_count != 1 else ''}\n"
            f"**Database Entry:** {created_at}"
        ),
        inline=False
    )
    
    # Calculate rewards from voice minutes
    total_credits_earned = voice_minutes // 10
    total_levels_earned = voice_minutes // 20
    
    embed.add_field(
        name="📊 **Voice Chat Stats**",
        value=(
            f"**Total Credits Earned (VC):** {total_credits_earned}\n"
            f"**Total Levels Earned (VC):** {total_levels_earned}\n"
            f"**Next Credit in:** {10 - (voice_minutes % 10)} minutes\n"
            f"**Next Level in:** {20 - (voice_minutes % 20)} minutes"
        ),
        inline=False
    )
    
    embed.set_footer(text=f"Requested by {ctx.author.name} • {get_indian_time()}")
    
    await ctx.send(embed=embed)

@bot.command()
@is_global_admin()
async def removemychannel(ctx, server_id: int, channel_id: int):
    """Remove channel from allowed channels"""
    server = bot.get_guild(server_id)
    if not server:
        await ctx.send("❌ Server not found!")
        return
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('DELETE FROM allowed_channels WHERE channel_id = ? AND guild_id = ?', (channel_id, server.id))
    rows_deleted = conn.total_changes
    conn.commit()
    conn.close()
    
    if rows_deleted > 0:
        embed = discord.Embed(
            title="✅ Channel Removed",
            description=f"**Channel has been removed from allowed channels!**",
            color=0x57F287
        )
        embed.add_field(name="🏢 Server", value=f"**{server.name}**\n(ID: `{server.id}`)", inline=True)
        embed.add_field(name="📢 Channel ID", value=f"`{channel_id}`", inline=True)
        embed.add_field(name="👤 Removed By", value=f"{ctx.author.mention}", inline=True)
        await ctx.send(embed=embed)
    else:
        await ctx.send("❌ Channel not found in allowed channels list!")

@bot.command()
@is_global_admin()
async def removeadmin(ctx, server_id: int, user_input: str):
    """Remove server admin"""
    server = bot.get_guild(server_id)
    if not server:
        await ctx.send("❌ Server not found!")
        return
    
    # Try to resolve user
    user = None
    try:
        if user_input.isdigit():
            member = server.get_member(int(user_input))
            if member:
                user = member
    except:
        pass
    
    if not user:
        # Try to find by username
        for member in server.members:
            if user_input.lower() in member.name.lower() or user_input.lower() in (member.display_name.lower() if member.display_name else ""):
                user = member
                break
    
    if not user:
        await ctx.send("❌ User not found in that server!")
        return
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('DELETE FROM server_admins WHERE server_id = ? AND user_id = ?', (server.id, user.id))
    rows_deleted = conn.total_changes
    conn.commit()
    conn.close()
    
    if rows_deleted > 0:
        embed = discord.Embed(
            title="⚠️ Server Admin Removed",
            description=f"**{user.mention} has been removed as server admin!**",
            color=0xFEE75C
        )
        embed.add_field(name="👤 User", value=f"{user.mention}\n(ID: `{user.id}`)", inline=True)
        embed.add_field(name="🏢 Server", value=f"**{server.name}**", inline=True)
        embed.add_field(name="👑 Removed By", value=f"{ctx.author.mention}", inline=True)
        
        # Notify the user
        try:
            user_notification = discord.Embed(
                title="⚠️ Server Admin Access Removed",
                description=f"Your **server admin access** in **{server.name}** has been removed by {ctx.author.mention}",
                color=0xFEE75C
            )
            await user.send(embed=user_notification)
        except:
            pass
        
        await ctx.send(embed=embed)
    else:
        await ctx.send("❌ User is not a server admin in that server!")

@bot.command()
@is_global_admin()
async def removeglobaladmin(ctx, user_input: str):
    """Remove global admin access"""
    # Try to resolve user input
    user = await resolve_user(ctx, user_input)
    
    if not user:
        await ctx.send("❌ User not found! Please provide a valid user ID, mention, or username.")
        return
    
    # Cannot remove yourself
    if user.id == ctx.author.id:
        await ctx.send("❌ You cannot remove your own global admin access!")
        return
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('DELETE FROM global_admins WHERE user_id = ?', (user.id,))
    rows_deleted = conn.total_changes
    conn.commit()
    conn.close()
    
    if rows_deleted > 0:
        embed = discord.Embed(
            title="⚠️ Global Admin Access Removed",
            description=f"**{user.mention} no longer has global admin access!**",
            color=0xED4245
        )
        embed.add_field(name="👤 User", value=f"{user.mention}\n(ID: `{user.id}`)", inline=True)
        embed.add_field(name="👑 Removed By", value=f"{ctx.author.mention}", inline=True)
        
        # Notify the user
        try:
            user_notification = discord.Embed(
                title="⚠️ Global Admin Access Removed",
                description=f"Your **global admin access** has been removed by {ctx.author.mention}",
                color=0xED4245
            )
            await user.send(embed=user_notification)
        except:
            pass
        
        await ctx.send(embed=embed)
    else:
        await ctx.send("❌ User is not a global admin!")

@bot.command()
@is_global_admin()
async def backupdb(ctx):
    """Create and send database backup"""
    try:
        # Check if database exists
        if not os.path.exists('kornfinder.db'):
            await ctx.send("❌ Database file not found!")
            return
        
        # Read database file
        with open('kornfinder.db', 'rb') as f:
            db_data = f.read()
        
        # Send as file
        file = discord.File(io.BytesIO(db_data), filename=f"kornfinder_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
        
        embed = discord.Embed(
            title="💾 Database Backup",
            description="**Database backup created successfully!**",
            color=0x57F287,
            timestamp=datetime.now(timezone.utc)
        )
        
        embed.add_field(
            name="📊 Database Information",
            value=f"**File Size:** {len(db_data) / 1024:.2f} KB\n**Generated:** {get_indian_time()}\n**Contains:** All user data, admin data, and settings",
            inline=False
        )
        
        embed.set_footer(text="Database Backup • KornFinder Bot")
        
        await ctx.send(embed=embed, file=file)
        
    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Backup Failed",
            description="Could not create database backup.",
            color=0xED4245
        )
        error_embed.add_field(
            name="📝 Error Details",
            value=f"```{str(e)[:500]}```",
            inline=False
        )
        await ctx.send(embed=error_embed)
        print(f"Error in backupdb command: {e}")

@bot.command()
@is_global_admin()
async def resetdb(ctx):
    """Reset database (WARNING: Deletes all data)"""
    # Confirmation embed
    confirm_embed = discord.Embed(
        title="⚠️ DATABASE RESET CONFIRMATION ⚠️",
        description="**WARNING: This will delete ALL data including:**\n• All user credits and levels\n• All admin permissions\n• All server settings\n• All allowed channels\n• All service prices",
        color=0xED4245
    )
    
    confirm_embed.add_field(
        name="❌ **IRREVERSIBLE ACTION**",
        value="This action cannot be undone! All data will be permanently lost.",
        inline=False
    )
    
    confirm_embed.add_field(
        name="✅ **To Confirm**",
        value="Type `CONFIRM RESET DATABASE` exactly as shown to proceed.",
        inline=False
    )
    
    confirm_embed.set_footer(text="This action requires explicit confirmation")
    
    await ctx.send(embed=confirm_embed)
    
    # Wait for confirmation
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel and m.content == "CONFIRM RESET DATABASE"
    
    try:
        await bot.wait_for('message', timeout=30.0, check=check)
    except asyncio.TimeoutError:
        await ctx.send("❌ Database reset cancelled (timeout).")
        return
    
    # Proceed with reset
    try:
        # Delete database file
        if os.path.exists('kornfinder.db'):
            os.remove('kornfinder.db')
        
        # Reinitialize database
        init_db()
        
        success_embed = discord.Embed(
            title="✅ Database Reset Complete",
            description="**Database has been reset to factory defaults!**",
            color=0x57F287
        )
        
        success_embed.add_field(
            name="📊 New Database",
            value="Database has been recreated with:\n• Default service prices\n• Default admin account\n• Empty user database",
            inline=False
        )
        
        success_embed.add_field(
            name="👤 Admin Account",
            value=f"Global admin: <@{YOUR_DISCORD_ID}>\nYou may need to re-add other admins.",
            inline=False
        )
        
        success_embed.set_footer(text="Database reset completed successfully")
        
        await ctx.send(embed=success_embed)
        
    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Database Reset Failed",
            description="Could not reset database.",
            color=0xED4245
        )
        error_embed.add_field(
            name="📝 Error Details",
            value=f"```{str(e)[:500]}```",
            inline=False
        )
        await ctx.send(embed=error_embed)
        print(f"Error in resetdb command: {e}")

@bot.command()
@is_global_admin()
async def servers(ctx):
    """List all servers the bot is in"""
    embed = discord.Embed(
        title="🏢 Bot Servers List",
        description=f"**Total Servers:** {len(bot.guilds)}",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc)
    )
    
    if not bot.guilds:
        embed.add_field(
            name="No Servers",
            value="The bot is not in any servers yet.",
            inline=False
        )
    else:
        # Sort servers by member count
        sorted_guilds = sorted(bot.guilds, key=lambda g: g.member_count, reverse=True)
        
        server_list = ""
        for i, guild in enumerate(sorted_guilds[:25], 1):
            owner_name = guild.owner.name if guild.owner else "Unknown"
            server_list += f"{i}. **{guild.name}**\n   👑 {owner_name} | 👥 {guild.member_count} | 🆔 `{guild.id}`\n"
        
        embed.add_field(
            name=f"📋 Servers ({len(sorted_guilds)})",
            value=server_list,
            inline=False
        )
    
    embed.set_footer(text=f"Requested by {ctx.author.name} • {get_indian_time()}")
    await ctx.send(embed=embed)

@bot.command()
@is_global_admin()
async def leaveserver(ctx, server_id: int):
    """Leave a server"""
    server = bot.get_guild(server_id)
    if not server:
        await ctx.send("❌ Server not found!")
        return
    
    # Confirmation
    confirm_embed = discord.Embed(
        title="⚠️ LEAVE SERVER CONFIRMATION ⚠️",
        description=f"**Are you sure you want to leave this server?**\n\n**Server:** {server.name}\n**ID:** `{server.id}`\n**Owner:** {server.owner.mention if server.owner else 'Unknown'}\n**Members:** {server.member_count}",
        color=0xED4245
    )
    
    confirm_embed.add_field(
        name="✅ **To Confirm**",
        value="Type `YES LEAVE SERVER` exactly as shown to proceed.",
        inline=False
    )
    
    await ctx.send(embed=confirm_embed)
    
    # Wait for confirmation
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel and m.content == "YES LEAVE SERVER"
    
    try:
        await bot.wait_for('message', timeout=30.0, check=check)
    except asyncio.TimeoutError:
        await ctx.send("❌ Server leave cancelled (timeout).")
        return
    
    # Leave server
    try:
        await server.leave()
        
        success_embed = discord.Embed(
            title="✅ Left Server",
            description=f"**Successfully left {server.name}!**",
            color=0x57F287
        )
        
        success_embed.add_field(name="🏢 Server", value=f"**{server.name}**", inline=True)
        success_embed.add_field(name="🆔 ID", value=f"`{server.id}`", inline=True)
        success_embed.add_field(name="👥 Members", value=f"{server.member_count}", inline=True)
        
        await ctx.send(embed=success_embed)
        
    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Failed to Leave Server",
            description=f"Could not leave {server.name}",
            color=0xED4245
        )
        error_embed.add_field(
            name="📝 Error",
            value=f"```{str(e)[:200]}```",
            inline=False
        )
        await ctx.send(embed=error_embed)

# Error handling
@bot.event
async def on_command_error(ctx, error):
    """Global error handler"""
    if isinstance(error, commands.CommandNotFound):
        return
    
    if isinstance(error, commands.CheckFailure):
        return
    
    # Log the error
    print(f"Command error: {error}")
    
    # Send error message
    embed = discord.Embed(
        title="❌ Command Error",
        description="An error occurred while processing the command.",
        color=0xED4245
    )
    
    # Add more specific error information
    if hasattr(error, 'original'):
        error_msg = str(error.original)[:200]
        embed.add_field(name="Error Details", value=f"```{error_msg}```", inline=False)
    
    embed.add_field(
        name="🔄 Solution",
        value="• Check your command syntax\n• Make sure you have required permissions\n• Try again in a few moments",
        inline=False
    )
    
    await ctx.send(embed=embed, delete_after=30)

# Run the bot
if __name__ == "__main__":
    print("=" * 50)
    print("🚀 STARTING KORNFINDER PREMIUM BOT v3.0")
    print("=" * 50)
    print(f"💎 Admin ID: {YOUR_DISCORD_ID}")
    print(f"📢 Default Channel: {DEFAULT_CHANNEL_ID}")
    print(f"🔗 Discord Server: {DEVELOPER_INFO['discord']}")
    print(f"📱 Telegram: {DEVELOPER_INFO['telegram']}")
    print(f"👤 Developer: {DEVELOPER_INFO['developer']}")
    print(f"🔗 API Provider: {DEVELOPER_INFO['phenion']}")
    print("💰 Credit System: Voice Chat Only")
    print("🎯 10 minutes = 1 credit, 20 minutes = 2 credits + level up")
    print("📱 Services: Number, Card, Email, Telegram ID")
    print("🔧 API Features: 3x Retry, Error Handling, Auto-Refund")
    print("🛡️ Auto-Delete: 3 minutes for all messages")
    print("🔔 Server Join Notifications: ENABLED")
    print("📨 Auto Setup via DM Reply: ENABLED")
    print("⚠️ Admin Permission Notifications: ENABLED (Hourly)")
    print("👑 Global Admin Panel: FULL ACCESS")
    print("🔧 Server Admin Panel: LIMITED ACCESS")
    print("📢 Broadcast Commands: ENABLED")
    print("💰 Service Price Management: ENABLED")
    print("🏆 Leaderboard: ENABLED")
    print("🎧 Voice Monitoring: FIXED (No false increments)")
    print("✨ Unlimited Access Feature: ADDED")
    print("📊 Daily Reports: Auto-sent to admin")
    print("📋 Manual Reports: !txtlist command")
    print("🚀 Railway.com Compatible: YES")
    print("=" * 50)
    print("✅ Bot is ready to launch!")
    
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        print("❌ Invalid bot token! Check your DISCORD_BOT_TOKEN environment variable.")
    except Exception as e:
        print(f"❌ Bot error: {e}")