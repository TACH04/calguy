import discord
from discord.ext import commands, tasks
import os
import json
import time
import asyncio
import logging
import datetime
import tempfile
import re
from collections import Counter
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
import aiohttp
import hashlib
from agents.agent import GeneralAgent
from bot.text_chunking import DISCORD_MAX_MESSAGE_LENGTH, split_text
from bot.reminder_manager import reminder_manager
from integrations.google_calendar import get_upcoming_events_data
from bot.image_generator import render_event_dashboard

def load_contacts():
    contacts_file = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'contacts.json')
    try:
        with open(contacts_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error(f"Error loading contacts: {e}")
        return {}

def save_contacts(contacts):
    contacts_file = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'contacts.json')
    try:
        os.makedirs(os.path.dirname(contacts_file), exist_ok=True)
        with open(contacts_file, 'w', encoding='utf-8') as f:
            json.dump(contacts, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving contacts: {e}")
        return False


def get_initials(name):
    parts = str(name).strip().split()
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()

def generate_color(user_id):
    hash_val = int(hashlib.md5(str(user_id).encode()).hexdigest(), 16)
    r = (hash_val & 0xFF0000) >> 16
    g = (hash_val & 0x00FF00) >> 8
    b = hash_val & 0x0000FF
    
    # Mix with white to make pastels/brighter colors readable against dark bg
    r = (r + 255) // 2
    g = (g + 255) // 2
    b = (b + 255) // 2
    
    return f"#{r:02x}{g:02x}{b:02x}"

# Configure logging
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_file = 'discord_bot.log'

# Set up Rotating File Handler (5 MB max size, 5 backup files)
file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5)
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)

# Set up Console Handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

# Get the root logger and add handlers
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

logger = logging.getLogger('bot.discord_bot')

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

if not DISCORD_TOKEN or DISCORD_TOKEN == "your_bot_token_here":
    logger.error("DISCORD_TOKEN is not set in .env")
    exit(1)

# Enforce a single instance of the bot per workspace
LOCK_FILE = os.path.join(os.path.dirname(__file__), '..', '..', '.bot.lock')
try:
    if os.path.exists(LOCK_FILE):
        # Check if the process is actually running (Mac/Linux specific)
        with open(LOCK_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            logger.error(f"❌ Another bot instance is already running (PID: {old_pid}). Exiting.")
            exit(1)
        except (ProcessLookupError, ValueError):
            # Process is dead, we can overwrite the lock
            pass
    
    with open(LOCK_FILE, 'w') as f:
        f.write(str(os.getpid()))
    
except Exception as e:
    logger.warning(f"Could not enforce process lock: {e}")

# Channel Configurations for Reminders
def _parse_channel_id(val):
    if not val:
        return None
    try:
        # Handle cases where the user might paste a URL or 'guild_id/channel_id'
        if '/' in val:
            val = val.split('/')[-1]
        return int(val)
    except ValueError:
        logger.error(f"Invalid channel ID format in .env: '{val}'")
        return None

ANNOUNCEMENT_CHANNEL_ID = _parse_channel_id(os.getenv("ANNOUNCEMENT_CHANNEL_ID"))
REMINDERS_CHANNEL_ID = _parse_channel_id(os.getenv("REMINDERS_CHANNEL_ID"))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)
poll_lock = asyncio.Lock()

# Session Management
SESSION_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'sessions')
SESSION_TTL_DAYS = int(os.getenv("SESSION_TTL_DAYS", "7"))
# Max number of recent messages whose image data is persisted to disk
SESSION_IMAGE_TURNS_KEPT = int(os.getenv("SESSION_IMAGE_TURNS_KEPT", "3"))


class SessionManager:
    def __init__(self):
        self.sessions = {}
        self.tasks = {}
        self.http_session = None
        self._init_lock = asyncio.Lock()
        os.makedirs(SESSION_DIR, exist_ok=True)

    # ------------------------------------------------------------------
    # Session access
    # ------------------------------------------------------------------

    async def get_session(self, channel_id: int):
        """Returns the (GeneralAgent, asyncio.Lock) for a given channel."""
        if channel_id in self.sessions:
            session = self.sessions[channel_id]
            session['last_access'] = asyncio.get_event_loop().time()
            return session['agent'], session['lock']

        async with self._init_lock:
            # Double-check after acquiring the lock to avoid redundant loads
            if channel_id not in self.sessions:
                agent = GeneralAgent()
                loaded = await self._load_session(channel_id, agent)
                if loaded:
                    logger.info(f"Restored persisted session for channel {channel_id}.")
                else:
                    logger.info(f"Creating new session for channel {channel_id}.")
                self.sessions[channel_id] = {
                    'agent': agent,
                    'lock': asyncio.Lock(),
                    'last_access': asyncio.get_event_loop().time()
                }

        session = self.sessions[channel_id]
        session['last_access'] = asyncio.get_event_loop().time()
        return session['agent'], session['lock']

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _session_path(self, channel_id: int) -> str:
        return os.path.join(SESSION_DIR, f"{channel_id}.json")

    async def save_session(self, channel_id: int):
        """Serialize and save the current session to disk."""
        if channel_id not in self.sessions:
            return
        agent = self.sessions[channel_id]['agent']
        try:
            messages = self._prune_images_for_storage(agent.get_history())
            payload = {
                "channel_id": channel_id,
                "saved_at": time.time(),
                "messages": messages,
            }
            path = self._session_path(channel_id)
            
            def do_save():
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(payload, f, ensure_ascii=False)
            
            await asyncio.to_thread(do_save)
            logger.debug(f"Session saved for channel {channel_id} ({len(messages)} messages).")
        except Exception as e:
            logger.error(f"Failed to save session for channel {channel_id}: {e}")

    async def _load_session(self, channel_id: int, agent: GeneralAgent) -> bool:
        """Load a persisted session from disk into the given agent. Returns True on success."""
        path = self._session_path(channel_id)
        
        exists = await asyncio.to_thread(os.path.exists, path)
        if not exists:
            return False
            
        try:
            def do_load():
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            
            payload = await asyncio.to_thread(do_load)
            messages = payload.get("messages", [])
            if not messages:
                return False
            agent.load_history(messages)
            return True
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Session file corrupted for channel {channel_id}, deleting: {e}")
            await self.delete_session_file(channel_id)
            return False
        except Exception as e:
            logger.error(f"Failed to load session for channel {channel_id}: {e}")
            return False

    async def delete_session_file(self, channel_id: int):
        """Remove the persisted session file for a channel."""
        path = self._session_path(channel_id)
        exists = await asyncio.to_thread(os.path.exists, path)
        if exists:
            try:
                await asyncio.to_thread(os.remove, path)
                logger.info(f"Deleted session file for channel {channel_id}.")
            except Exception as e:
                logger.error(f"Failed to delete session file for channel {channel_id}: {e}")

    # ------------------------------------------------------------------
    # Storage management
    # ------------------------------------------------------------------

    def _prune_images_for_storage(self, messages: list[dict]) -> list[dict]:
        """
        Strip image data from all but the most recent SESSION_IMAGE_TURNS_KEPT
        user messages. This keeps disk usage manageable while preserving recent
        visual context.
        """
        # Find indices of user messages that have images, newest first
        image_msg_indices = [
            i for i, m in enumerate(messages)
            if m.get("role") == "user" and m.get("images")
        ]
        # Keep images only for the last N turns
        keep_indices = set(image_msg_indices[-SESSION_IMAGE_TURNS_KEPT:])

        pruned = []
        for i, msg in enumerate(messages):
            m = dict(msg)
            if m.get("images") and i not in keep_indices:
                m = {k: v for k, v in m.items() if k != "images"}
            pruned.append(m)
        return pruned

    async def _cleanup_old_sessions(self):
        """Delete session files that haven't been updated within SESSION_TTL_DAYS."""
        cutoff = time.time() - SESSION_TTL_DAYS * 86400
        removed = 0
        try:
            def get_old_files():
                to_remove = []
                for fname in os.listdir(SESSION_DIR):
                    if not fname.endswith('.json'):
                        continue
                    fpath = os.path.join(SESSION_DIR, fname)
                    if os.path.getmtime(fpath) < cutoff:
                        to_remove.append(fpath)
                return to_remove

            old_files = await asyncio.to_thread(get_old_files)
            for fpath in old_files:
                await asyncio.to_thread(os.remove, fpath)
                removed += 1
                
            if removed:
                logger.info(f"Cleaned up {removed} expired session file(s) (TTL={SESSION_TTL_DAYS}d).")
        except Exception as e:
            logger.error(f"Error during session cleanup: {e}")

    async def close(self):
        """Close the shared HTTP session."""
        if self.http_session:
            await self.http_session.close()
            logger.info("Shared HTTP session closed.")

session_manager = SessionManager()

# Supported image MIME types for vision
IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
MAX_IMAGES_PER_MESSAGE = 5

async def download_images(attachments) -> list[bytes]:
    """Download image attachments from a Discord message and return them as a list of bytes."""
    image_bytes_list = []
    
    # MIME types to filenames extension fallback
    valid_exts = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
    
    image_attachments = []
    for a in attachments:
        is_image = False
        if a.content_type:
            mime = a.content_type.split(';')[0].strip().lower()
            if mime in IMAGE_MIME_TYPES:
                is_image = True
        
        # Fallback to extension if content_type is missing or generic
        if not is_image and a.filename:
            ext = os.path.splitext(a.filename.lower())[1]
            if ext in valid_exts:
                is_image = True
                
        if is_image:
            image_attachments.append(a)

    image_attachments = image_attachments[:MAX_IMAGES_PER_MESSAGE]

    if not image_attachments:
        if attachments:
            logger.info(f"Skipped {len(attachments)} attachments (none matched image types).")
        return []

    if not session_manager.http_session:
        session_manager.http_session = aiohttp.ClientSession()
        logger.info("Initialized shared HTTP session for image downloads.")

    session = session_manager.http_session
    for attachment in image_attachments:
        try:
            async with session.get(attachment.url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    image_bytes_list.append(data)
                    logger.info(f"Downloaded image attachment: {attachment.filename} ({len(data)} bytes)")
                else:
                    logger.warning(f"Failed to download attachment {attachment.filename}: HTTP {resp.status}")
        except Exception as e:
            logger.error(f"Error downloading attachment {attachment.filename}: {e}")

    return image_bytes_list

@bot.command(name='sync_names')
async def sync_names_cmd(ctx):
    """Syncs server members to data/contacts.json."""
    if not ctx.guild:
        await ctx.send("This command can only be used in a server.")
        return

    # Load existing
    contacts = load_contacts()
    
    # Add members
    added_count = 0
    updated_count = 0
    
    status_msg = await ctx.send("⏳ Syncing members... (this may take a moment for large servers)")
    
    try:
        async for member in ctx.guild.fetch_members(limit=None):
            if member.bot:
                continue
            
            uid_str = str(member.id)
            if uid_str not in contacts:
                contacts[uid_str] = member.display_name
                added_count += 1
            else:
                # Update display name if it's different and not already customized?
                # For now just log new ones to avoid overwriting manual edits
                pass
        
        if save_contacts(contacts):
            await status_msg.edit(content=f"✅ Sync complete! Added {added_count} new members to `data/contacts.json`. Total: {len(contacts)}.")
            logger.info(f"Sync complete: {added_count} members added to contacts.")
        else:
            await status_msg.edit(content="❌ Failed to save synced contacts.")
            
    except discord.Forbidden:
        await status_msg.edit(content="❌ I don't have permission to fetch members. Please enable 'Server Members Intent' in the Developer Portal.")
    except Exception as e:
        logger.error(f"Error during sync: {e}")
        await status_msg.edit(content=f"❌ An error occurred: {e}")

@bot.event
async def on_ready():
    if not session_manager.http_session:
        session_manager.http_session = aiohttp.ClientSession()
        logger.info("Initialized shared HTTP session on bot ready.")
    
    # Start background cleanup of old sessions
    asyncio.create_task(session_manager._cleanup_old_sessions())
    
    logger.info(f'✅ Logged in as {bot.user.name} ({bot.user.id})')
    logger.info(f'Connected to {len(bot.guilds)} server(s):')
    for guild in bot.guilds:
        logger.info(f' - {guild.name} (ID: {guild.id})')
    
    logger.info('Bot is ready to receive commands!')
    logger.info('------')
    
    # Start the calendar polling task if channels are configured and not already running
    if ANNOUNCEMENT_CHANNEL_ID:
        if not poll_calendar.is_running():
            poll_calendar.start()

# Registry sync management
async def _resolve_and_repair_uid(raw_input, contacts, announcement_channel, event_id, status_group):
    # Use regex to extract the numeric ID from any format (raw ID or mention)
    match = re.search(r'(\d{17,20})', str(raw_input))
    if not match:
        logger.warning(f"Could not extract numeric ID from: {raw_input}")
        return str(raw_input), None
        
    uid_str = match.group(1)
    
    # 1. Exact match in contacts
    if uid_str in contacts:
        contact_data = contacts[uid_str]
        name = contact_data.get('name') if isinstance(contact_data, dict) else contact_data
        return uid_str, name
        
    # 2. Precision repair
    try:
        uid_float = float(uid_str)
        for contact_id, contact_data in contacts.items():
            if int(float(contact_id)) == int(uid_float):
                name = contact_data.get('name') if isinstance(contact_data, dict) else contact_data
                logger.warning(f"Precision repair: replacing malformed ID {raw_input} with {contact_id}")
                reminder_manager.remove_subscription(event_id, raw_input, status_group)
                reminder_manager.add_subscription(event_id, contact_id, status_group)
                return contact_id, name
    except Exception as e:
        logger.error(f"Error during precision repair check for {uid_str}: {e}")

    # 3. Cache fallback
    try:
        uid_int = int(uid_str)
        if announcement_channel and announcement_channel.guild:
            member = announcement_channel.guild.get_member(uid_int)
            if member:
                return uid_str, member.display_name
                
        user = bot.get_user(uid_int)
        if user:
            return uid_str, user.display_name
            
        # 4. API fallback
        try:
            user = await bot.fetch_user(uid_int)
            if user:
                return uid_str, user.display_name
        except Exception as e:
            logger.warning(f"Failed to fetch user {uid_str} from API: {e}")
            
    except Exception as e:
        logger.error(f"Error in fallback lookup for {uid_str}: {e}")
        
    return uid_str, None

sync_api_lock = asyncio.Lock()
sync_registry_pending = False
pending_dashboard_refreshes = {} # {channel_id: task}

async def trigger_sync_registry(force: bool = False):
    global sync_registry_pending
    if sync_api_lock.locked():
        if not sync_registry_pending:
            sync_registry_pending = True
            logger.info("Sync already running, queuing pending sync.")
        return
    asyncio.create_task(_run_sync_with_pending(force=force))

async def _run_sync_with_pending(force: bool = False):
    global sync_registry_pending
    await sync_registry(force=force)
    while sync_registry_pending:
        sync_registry_pending = False
        logger.info("Executing pending sync.")
        await sync_registry(force=True) # If one was pending, it's likely a change

async def sync_registry(force: bool = False):
    """
    Synchronizes the announcement channel dashboard and sends reminders.
    This should generally be called via trigger_sync_registry() to handle debouncing.
    
    :param force: If True, skips the hash check and re-posts the dashboard even if data is unchanged.
    """
    async with sync_api_lock:
        if not ANNOUNCEMENT_CHANNEL_ID:
            return

        announcement_channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
        reminders_channel = bot.get_channel(REMINDERS_CHANNEL_ID) if REMINDERS_CHANNEL_ID else None
        
        if not announcement_channel:
            return

        time_min = datetime.datetime.now(datetime.timezone.utc).isoformat()
        events = get_upcoming_events_data(max_results=50, time_min=time_min)
        
        if not events or isinstance(events, str):
            events = []

        dashboard_events = []
        for event in events[:10]: # Limit to next 10 for dashboard
            summary = event.get('summary', 'Untitled Event').strip()
            
            start_dt = None
            if 'dateTime' in event['start']:
                dt_str = event['start']['dateTime'].replace('Z', '+00:00')
                start_dt = datetime.datetime.fromisoformat(dt_str)
            else:
                dt_str = event['start']['date']
                start_dt = datetime.datetime.strptime(dt_str, '%Y-%m-%d').replace(tzinfo=datetime.timezone.utc)
            
            subs = reminder_manager.get_all_subscribers(event['id'])
            going_subscribers = subs.get('going', [])
            going_count = len(going_subscribers)
            
            # format for dashboard
            date_str = start_dt.strftime('%b %d')
            time_str = start_dt.strftime('%-I:%M %p')
            
            attendees_data = []
            contacts = load_contacts()
            for uid in list(going_subscribers):
                final_uid, name = await _resolve_and_repair_uid(uid, contacts, announcement_channel, event['id'], "going")
                
                if name:
                    # Silent for existing known
                    initials = get_initials(name)
                else:
                    logger.info(f"RSVP from unknown ID (please add to data/contacts.json): {final_uid}")
                    initials = "?"
                
                # Get custom color from contacts if available
                user_data = contacts.get(final_uid)
                custom_color = None
                if isinstance(user_data, dict):
                    custom_color = user_data.get('color')
                
                attendees_data.append({
                    "id": str(final_uid),
                    "initials": initials,
                    "color": custom_color or generate_color(final_uid)
                })

            dashboard_events.append({
                'schedule': f"{date_str}  {time_str}",
                'title': summary,
                'attendees': going_count,
                'attendees_data': attendees_data
            })
            
            # Reminder check
            event_id = event['id']
            if reminders_channel and not reminder_manager.is_reminder_sent(event_id):
                if reminder_manager.is_in_progress(event_id):
                    continue

                now_dt = datetime.datetime.now(datetime.timezone.utc)
                time_diff = start_dt - now_dt
                if datetime.timedelta(minutes=-5) < time_diff < datetime.timedelta(minutes=60):
                    going_subscribers = subs.get('going', [])
                    if going_subscribers:
                        mentions = " ".join([f"<@{uid}>" for uid in going_subscribers])
                        reminder_text = (f"⏰ **Reminder!**\n"
                                         f"**{summary}** is starting soon!\n"
                                         f"{mentions}")
                        reminder_manager.set_in_progress(event_id, True)
                        try:
                            await reminders_channel.send(reminder_text)
                            reminder_manager.mark_reminder_sent(event_id)
                            logger.info(f"Sent reminder for event: {summary} ({event_id}).")
                        finally:
                            reminder_manager.set_in_progress(event_id, False)
                    else:
                        reminder_manager.mark_reminder_sent(event_id)
        
        # --- Dashboard Update Optimization ---
        
        # Calculate state hash to detect changes
        state_str = json.dumps(dashboard_events, sort_keys=True)
        current_hash = hashlib.md5(state_str.encode('utf-8')).hexdigest()
        
        has_changed = current_hash != reminder_manager.last_dashboard_hash
        
        # Check if the dashboard is still the last message in the channel
        is_at_bottom = False
        if reminder_manager.dashboard_message_id:
            if announcement_channel.last_message_id == reminder_manager.dashboard_message_id:
                is_at_bottom = True
            else:
                # Double check with history in case last_message_id is stale or includes non-content messages
                try:
                    last_msg = None
                    async for msg in announcement_channel.history(limit=1):
                        last_msg = msg
                    if last_msg and last_msg.id == reminder_manager.dashboard_message_id:
                        is_at_bottom = True
                except Exception:
                    pass

        # Decide whether to refresh the image
        if not force and not has_changed and is_at_bottom:
            logger.debug("Skipping dashboard refresh: Data is same and message is at the bottom.")
            return

        logger.info(f"Refreshing dashboard (force={force}, changed={has_changed}, at_bottom={is_at_bottom})")

        # Update hash
        reminder_manager.last_dashboard_hash = current_hash
        
        # Render image
        output_path = os.path.join(tempfile.gettempdir(), 'dashboard.png')
        render_event_dashboard(dashboard_events, output_path)

        # Message management: Try direct deletion of stored dashboard ID
        # Fallback to history scan if the ID is missing or deletion fails.
        deleted_old = False
        if reminder_manager.dashboard_message_id:
            try:
                old_msg = await announcement_channel.fetch_message(reminder_manager.dashboard_message_id)
                await old_msg.delete()
                deleted_old = True
                logger.info(f"Directly deleted old dashboard message {reminder_manager.dashboard_message_id}.")
            except Exception:
                # Silently fail and fallback to history scan
                pass

        if not deleted_old:
            try:
                async for msg in announcement_channel.history(limit=50):
                    if msg.author == bot.user:
                        # Check if it has the dashboard attachment
                        if any(a.filename == 'dashboard.png' for a in msg.attachments):
                            try:
                                await msg.delete()
                            except Exception:
                                pass
            except Exception as e:
                logger.error(f"Error cleaning up old dashboard messages via history: {e}")
 
        # Post the new dashboard at the bottom
        with open(output_path, 'rb') as f:
            discord_file = discord.File(f, filename='dashboard.png')
            new_dashboard_msg = await announcement_channel.send(file=discord_file)
            reminder_manager.dashboard_message_id = new_dashboard_msg.id
            reminder_manager.save()

@tasks.loop(minutes=5)
async def poll_calendar():
    """Poll Google Calendar for new events and upcoming reminders."""
    if poll_lock.locked():
        logger.warning("poll_calendar is already running, skipping this iteration.")
        return

    async with poll_lock:
        try:
            await sync_registry()
        except Exception as e:
            logger.error(f"Error in poll_calendar loop: {e}")

# Reactions are no longer supported.

@bot.command(name='color')
async def color_cmd(ctx, hex_code: str = None):
    """Sets your custom color for the schedule image (e.g. !color #FF5733)."""
    user_id = str(ctx.author.id)
    contacts = load_contacts()
    
    if not hex_code:
        # Show current color
        user_data = contacts.get(user_id)
        current_color = None
        if isinstance(user_data, dict):
            current_color = user_data.get('color')
        
        if current_color:
            await ctx.send(f"🎨 Your current custom color is `{current_color}`. Use `!color <hex>` to change it.")
        else:
            # Show the generated default
            default = generate_color(user_id)
            await ctx.send(f"🎨 You haven't set a custom color yet. Your current default is `{default}`. Use `!color <hex>` to set a custom one.")
        return

    # Validate hex code
    if not re.match(r'^#(?:[0-9a-fA-F]{3}){1,2}$', hex_code):
        await ctx.send("❌ Invalid hex code format. Please use something like `#FF5733` or `#ABC`.")
        return

    # Normalize hex
    hex_code = hex_code.upper()
    if len(hex_code) == 4: # Handle #ABC -> #AABBCC
        hex_code = "#" + "".join([c*2 for c in hex_code[1:]])

    # Update or create entry
    if user_id in contacts:
        if not isinstance(contacts[user_id], dict):
            contacts[user_id] = {"name": contacts[user_id], "color": hex_code}
        else:
            contacts[user_id]["color"] = hex_code
    else:
        # Fallback if they aren't in contacts yet
        contacts[user_id] = {"name": ctx.author.display_name, "color": hex_code}

    if save_contacts(contacts):
        await ctx.send(f"✅ Your custom color has been set to `{hex_code}`! Refreshing the dashboard...")
        # Trigger dashboard refresh
        await trigger_sync_registry(force=True)
    else:
        await ctx.send("❌ Failed to save your color settings.")

@bot.command(name='help')
async def help_cmd(ctx):
    """Displays this help message."""
    help_text = """**Brolympus Bot Commands:**
`!sync_names` - Automatically populate data/contacts.json with server members.
`!color <hex>` - Set your custom color for the schedule image (e.g. #FF5733).
`!clear` - Reset my conversation context immediately.
`!rebase <new prompt>` - Reset conversation context and completely replace my system prompt.
`!stop` - Interrupt the current active task.
`!session` - Display current session details (model, message count, idle time).
`!help` - Display this message.

Just mention me or talk directly to me to check and modify the squad's Google Calendar!"""
    await ctx.send(help_text)

@bot.command(name='stop')
async def stop_cmd(ctx):
    """Interrupt the current active task."""
    channel_id = ctx.channel.id
    if channel_id in session_manager.tasks:
        task = session_manager.tasks[channel_id]
        if not task.done():
            task.cancel()
            logger.info(f"User {ctx.author} stopped task in channel {channel_id}.")
            await ctx.send("🛑 Stopping current task...")
        else:
            await ctx.send("No active task to stop.")
    else:
        await ctx.send("No active task to stop.")

@bot.command(name='clear')
async def clear_cmd(ctx):
    """Reset the conversation context."""
    logger.info(f"User {ctx.author} ran !clear command in channel {ctx.channel.id}.")
    agent, lock = await session_manager.get_session(ctx.channel.id)
    async with lock:
        agent.reset()
        await session_manager.delete_session_file(ctx.channel.id)
        await ctx.send("✅ Conversation context for this channel has been cleared.")

@bot.command(name='rebase')
async def rebase_cmd(ctx, *, new_prompt: str = None):
    """Reset the conversation context and replace the system prompt."""
    if not new_prompt:
        await ctx.send("❌ You must provide a new prompt. Usage: `!rebase <new prompt>`")
        return

    logger.info(f"User {ctx.author} ran !rebase command in channel {ctx.channel.id}.")
    agent, lock = await session_manager.get_session(ctx.channel.id)
    async with lock:
        agent.rebase(new_prompt)
        await session_manager.delete_session_file(ctx.channel.id)
        await session_manager.save_session(ctx.channel.id)
        await ctx.send("✅ Conversation reset and system instructions updated!")

@bot.command(name='session')
async def session_cmd(ctx):
    """Display current session details."""
    logger.info(f"User {ctx.author} ran !session command in channel {ctx.channel.id}.")
    agent, _ = await session_manager.get_session(ctx.channel.id)
    info = agent.get_session_info()
    idle_str = f"{info['idle_seconds']} seconds"
    if info['idle_seconds'] > 60:
        idle_str = f"{info['idle_seconds'] // 60} min {info['idle_seconds'] % 60} sec"
    
    msg = (f"**Session Info (Channel Context):**\n"
           f"- Model: `{info['model']}`\n"
           f"- Message Count: `{info['message_count']}`\n"
           f"- Estimated Tokens: `{info.get('estimated_tokens', '?')}` / {info.get('context_window', 32768)}\n"
           f"- Memory Compressions: `{info.get('compression_count', 0)}`\n"
           f"- Idle Time: `{idle_str}`")
    await ctx.send(msg)

@bot.event
async def on_message(message):
    # Don't respond to ourselves
    if message.author == bot.user:
        return

    # Process commands first (like !help, !clear, !session)
    if message.content.startswith('!'):
        await bot.process_commands(message)
        return

    # Check if the bot is mentioned or if it's a DM
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = bot.user in message.mentions
    is_announcement_channel = message.channel.id == ANNOUNCEMENT_CHANNEL_ID

    # Handle 30-second debounced dashboard refresh for the announcement channel
    if is_announcement_channel and not message.author.bot:
        if message.channel.id in pending_dashboard_refreshes:
            pending_dashboard_refreshes[message.channel.id].cancel()
        
        async def debounced_sync():
            await asyncio.sleep(30)
            try:
                await trigger_sync_registry(force=True)
            finally:
                if pending_dashboard_refreshes.get(message.channel.id) == refresh_task:
                    del pending_dashboard_refreshes[message.channel.id]

        refresh_task = asyncio.create_task(debounced_sync())
        pending_dashboard_refreshes[message.channel.id] = refresh_task

    # Diagnostic logging for server interaction
    if not is_dm and is_mentioned:
        logger.info(f"Mentioned in channel {message.channel.id} of guild {message.guild.id}. Attachments: {len(message.attachments)}")
        for i, a in enumerate(message.attachments):
            logger.info(f" - Attachment {i}: {a.filename} (content_type: {a.content_type})")

    # If not mentioned and not a DM, ignore the message
    if not (is_dm or is_mentioned):
        return
    
    # Strip the mention from the message content to avoid confusing the agent
    content = message.content
    if is_mentioned:
        # discord.py's message.content includes the mention. 
        # We replace the bot's mention (both <@ID> and <@!ID> formats) with empty string
        mention_str = bot.user.mention
        content = content.replace(mention_str, '').strip()
        # Also handle the variant mention with '!' which sometimes appears
        content = content.replace(mention_str.replace('<@', '<@!'), '').strip()

    # Download any image attachments
    images = await download_images(message.attachments)

    # If the message has no text and no images after stripping the mention, don't respond
    if not content and not images and is_mentioned:
        await message.reply("How can I help the squad today? (Type `!help` for commands)")
        return
    
    # If there's no text at all (pure image, no mention text) don't respond to non-DM/non-mention
    if not content and images and is_mentioned:
        content = "What do you see in this image?"

    # Handle reply in a separate task so it can be cancelled
    task = asyncio.create_task(process_and_reply(message, content, is_mentioned, images))
    session_manager.tasks[message.channel.id] = task
    
    try:
        await task
    except asyncio.CancelledError:
        logger.info(f"Task for channel {message.channel.id} was cancelled.")
        # Optional: We could send a message here, but !stop command already sends one.
    except Exception as e:
        logger.exception(f"Error in task for channel {message.channel.id}: {e}")
    finally:
        if session_manager.tasks.get(message.channel.id) == task:
            del session_manager.tasks[message.channel.id]

async def process_and_reply(message, content, is_mentioned, images: list = None):
    sender_name = f"{message.author.display_name} (ID: {message.author.id})"
    server_name = message.guild.name if message.guild else "DM"
    channel_name = message.channel.name if hasattr(message.channel, 'name') else "DM"
    
    logger.info(f"Processing message from {sender_name} in [{server_name} | #{channel_name}]: '{content}'")
    
    agent, lock = await session_manager.get_session(message.channel.id)
    
    # Use a lock to process channel messages sequentially
    if lock.locked():
        wait_msg = await message.reply("*(Waiting for my turn to process your request...)*")
    else:
        wait_msg = None

    async with lock:
        if wait_msg:
            try:
                await wait_msg.delete()
            except:
                pass
                
        active_msg = await message.reply("*(Thinking...)*")
        full_text = ""
        out_chars = 0
        created_event_links = []
        last_edit_time = asyncio.get_event_loop().time()
        tools_used = []

        async def seal_overflow():
            nonlocal active_msg, out_chars
            while len(full_text) - out_chars > DISCORD_MAX_MESSAGE_LENGTH:
                tail = full_text[out_chars:]
                head, _ = split_text(tail, DISCORD_MAX_MESSAGE_LENGTH)
                await active_msg.edit(content=head)
                out_chars += len(head)
                rem = full_text[out_chars:]
                while len(rem) > DISCORD_MAX_MESSAGE_LENGTH:
                    h, rem = split_text(rem, DISCORD_MAX_MESSAGE_LENGTH)
                    active_msg = await message.reply(h)
                    out_chars += len(h)
                if rem:
                    active_msg = await message.reply(rem)

        async def sync_active_edit(force: bool = False):
            nonlocal last_edit_time
            tail = full_text[out_chars:]
            now = asyncio.get_event_loop().time()
            if not force and now - last_edit_time <= 1.2:
                return
            try:
                await active_msg.edit(content=tail)
                last_edit_time = now
            except discord.errors.HTTPException as e:
                logger.warning(f"Ignored HTTPException during message edit update: {e}")

        try:
            async for event in agent.chat_step(content, sender_name=sender_name, images=images or []):
                if event['type'] == 'status':
                    # Always show status until the actual streaming response starts
                    if not full_text:
                        try:
                            await active_msg.edit(content=f"*({event['content']})*")
                        except Exception as e:
                            logger.warning(f"Failed to edit status: {e}")
                elif event['type'] == 'debug_event':
                    # Surface scraping/summarization progress to the user
                    if not full_text and event.get('category') == 'scraping':
                        try:
                            await active_msg.edit(content=f"*({event['content']})*")
                        except Exception as e:
                            logger.warning(f"Failed to edit debug status: {e}")
                elif event['type'] == 'tool_call':
                    logger.info(f"Agent requested tool call: {event['tool']} with args: {event['args']}")
                    tools_used.append(event['tool'])
                    if not full_text:
                        try:
                            await active_msg.edit(content=f"*(Calling tool: {event['tool']}...)*")
                        except Exception as e:
                            logger.warning(f"Failed to edit tool call status: {e}")
                elif event['type'] == 'stream_chunk':
                    full_text += event['content']
                    await seal_overflow()
                    await sync_active_edit()
                elif event['type'] == 'tool_result':
                    logger.debug(f"Tool {event['tool']} returned: {event['result']}")
                    if event['tool'] in ['create_event', 'delete_event', 'rsvp_to_event']:
                        # Trigger an immediate registry sync via the debounced wrapper
                        await trigger_sync_registry(force=True)
                    
                    if event['tool'] == 'create_event':
                        # Look for the URL pattern in the create_event result
                        match = re.search(r'(https://www\.google\.com/calendar/event\?eid=[\w]+)', event['result'])
                        if match:
                            created_event_links.append(match.group(1))
                            logger.info(f"Captured calendar link for embed: {match.group(1)}")
                    pass # Silent on result, wait for the agent to talk
                elif event['type'] == 'message':
                    logger.info(f"Agent generated response (Tokens: {event.get('tokens', 'N/A')}): '{event.get('content', '')}'")
                elif event['type'] == 'error':
                    logger.error(f"Agent generated an error: {event['content']}")
                    await message.reply(f"❌ Error: {event['content']}")
                    break
                    
            # Final update to ensure we didn't miss the last chunks
            # Try to embed the link in the text using markdown if "Google Calendar" is mentioned
            if created_event_links and full_text:
                link = created_event_links[0] # Grab the first created link
                if "Google Calendar" in full_text:
                    full_text = full_text.replace("Google Calendar", f"[Google Calendar]({link})")
                else:
                    full_text += f"\n\n[View Event on Google Calendar]({link})"

            # Save session to disk after each successful response
            await session_manager.save_session(message.channel.id)

            if full_text:
                # Append tools used if any
                if tools_used:
                    # Count tools while preserving order of first appearance
                    counts = Counter(tools_used)
                    unique_tools = []
                    for t in tools_used:
                        if t not in unique_tools:
                            unique_tools.append(t)

                    tool_parts = []
                    for t in unique_tools:
                        count = counts[t]
                        if count > 1:
                            tool_parts.append(f"`{t}` (x{count})")
                        else:
                            tool_parts.append(f"`{t}`")

                    full_text += f"\n\n*Tools used: {', '.join(tool_parts)}*"

                await seal_overflow()
                try:
                    await active_msg.edit(content=full_text[out_chars:])
                except discord.errors.NotFound:
                    # Message might have been deleted
                    pass
            elif not full_text:
                 # Fallback if no content was generated
                 logger.warning("No content was generated by the agent.")
                 try:
                    await active_msg.edit(content="I'm sorry, I couldn't generate a response.")
                 except discord.errors.NotFound:
                    pass
                
        except asyncio.CancelledError:
            # Re-raise to be caught by the outer try-except
            raise
        except Exception as e:
            logger.exception(f"An unexpected error occurred during chat step: {e}")
            await message.reply(f"❌ An error occurred: {e}")

if __name__ == '__main__':
    logger.info("Starting Discord bot...")
    bot.run(DISCORD_TOKEN)
