import discord
from discord.ext import commands
import os
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from agents.agent import GeneralAgent

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

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Session Management
class SessionManager:
    def __init__(self):
        self.sessions = {}
        self.tasks = {}
        self.cleanup_task = None

    def get_session(self, channel_id: int):
        """Returns the (CalendarAgent, asyncio.Lock) for a given channel."""
        if channel_id not in self.sessions:
            logger.info(f"Creating new session for channel {channel_id}")
            self.sessions[channel_id] = {
                'agent': GeneralAgent(),
                'lock': asyncio.Lock(),
                'last_access': asyncio.get_event_loop().time()
            }
        
        session = self.sessions[channel_id]
        session['last_access'] = asyncio.get_event_loop().time()
        return session['agent'], session['lock']

    async def cleanup_loop(self):
        """Periodically removes sessions that have been inactive for > 1 hour."""
        while True:
            await asyncio.sleep(600) # Check every 10 minutes
            now = asyncio.get_event_loop().time()
            to_delete = []
            for cid, session in self.sessions.items():
                if now - session['last_access'] > 3600: # 1 hour
                    to_delete.append(cid)
            
            for cid in to_delete:
                logger.info(f"Cleaning up inactive session for channel {cid}")
                del self.sessions[cid]

session_manager = SessionManager()

@bot.event
async def on_ready():
    logger.info(f'✅ Logged in as {bot.user.name} ({bot.user.id})')
    logger.info(f'Connected to {len(bot.guilds)} server(s):')
    for guild in bot.guilds:
        logger.info(f' - {guild.name} (ID: {guild.id})')
    
    # Start cleanup task
    if session_manager.cleanup_task is None:
        session_manager.cleanup_task = asyncio.create_task(session_manager.cleanup_loop())
        
    logger.info('Bot is ready to receive commands!')
    logger.info('------')

@bot.command(name='help')
async def help_cmd(ctx):
    """Displays this help message."""
    help_text = """**Brolympus Bot Commands:**
`!clear` - Reset my conversation context immediately.
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
    agent, lock = session_manager.get_session(ctx.channel.id)
    async with lock:
        agent.reset()
        await ctx.send("✅ Conversation context for this channel has been cleared.")

@bot.command(name='session')
async def session_cmd(ctx):
    """Display current session details."""
    logger.info(f"User {ctx.author} ran !session command in channel {ctx.channel.id}.")
    agent, _ = session_manager.get_session(ctx.channel.id)
    info = agent.get_session_info()
    idle_str = f"{info['idle_seconds']} seconds"
    if info['idle_seconds'] > 60:
        idle_str = f"{info['idle_seconds'] // 60} min {info['idle_seconds'] % 60} sec"
    
    msg = (f"**Session Info (Channel Context):**\n"
           f"- Model: `{info['model']}`\n"
           f"- Message Count: `{info['message_count']}`\n"
           f"- Estimated Tokens: `{info.get('estimated_tokens', '?')}` / 8000\n"
           f"- Memory Compressions: `{info.get('compression_count', 0)}`\n"
           f"- Idle Time: `{idle_str}`\n"
           f"(Note: I auto-reset after 10 minutes of inactivity)")
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

    # If the message is empty after stripping the mention, don't respond
    if not content and is_mentioned:
        await message.reply("How can I help the squad today? (Type `!help` for commands)")
        return

    # Handle reply in a separate task so it can be cancelled
    task = asyncio.create_task(process_and_reply(message, content, is_mentioned))
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

async def process_and_reply(message, content, is_mentioned):
    sender_name = message.author.display_name
    server_name = message.guild.name if message.guild else "DM"
    channel_name = message.channel.name if hasattr(message.channel, 'name') else "DM"
    
    logger.info(f"Processing message from {sender_name} in [{server_name} | #{channel_name}]: '{content}'")
    
    agent, lock = session_manager.get_session(message.channel.id)
    
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
                
        response_msg = await message.reply("*(Thinking...)*")
        current_content = ""
        created_event_links = []
        last_edit_time = asyncio.get_event_loop().time()
        tools_used = []
        
        try:
            async for event in agent.chat_step(content, sender_name=sender_name):
                if event['type'] == 'status':
                    # Always show status until the actual streaming response starts
                    if not current_content:
                        try:
                            await response_msg.edit(content=f"*({event['content']})*")
                        except Exception as e:
                            logger.warning(f"Failed to edit status: {e}")
                elif event['type'] == 'debug_event':
                    # Surface scraping/summarization progress to the user
                    if not current_content and event.get('category') == 'scraping':
                        try:
                            await response_msg.edit(content=f"*({event['content']})*")
                        except Exception as e:
                            logger.warning(f"Failed to edit debug status: {e}")
                elif event['type'] == 'tool_call':
                    logger.info(f"Agent requested tool call: {event['tool']} with args: {event['args']}")
                    tools_used.append(event['tool'])
                    if not current_content:
                        try:
                            await response_msg.edit(content=f"*(Calling tool: {event['tool']}...)*")
                        except Exception as e:
                            logger.warning(f"Failed to edit tool call status: {e}")
                elif event['type'] == 'stream_chunk':
                    current_content += event['content']
                    now = asyncio.get_event_loop().time()
                    # Edit every 1 second to avoid rate limits
                    if now - last_edit_time > 1.2: # increased slightly for safety
                        try:
                            await response_msg.edit(content=current_content)
                            last_edit_time = now
                        except discord.errors.HTTPException as e:
                            logger.warning(f"Ignored HTTPException during message edit update: {e}")
                            pass # Ignore temporary edit failures
                elif event['type'] == 'tool_result':
                    logger.debug(f"Tool {event['tool']} returned: {event['result']}")
                    if event['tool'] == 'create_event':
                        import re
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
            if created_event_links and current_content:
                link = created_event_links[0] # Grab the first created link
                if "Google Calendar" in current_content:
                    current_content = current_content.replace("Google Calendar", f"[Google Calendar]({link})")
                else:
                    current_content += f"\n\n[View Event on Google Calendar]({link})"
                    
            if current_content:
                # Append tools used if any
                if tools_used:
                    from collections import Counter
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
                    
                    current_content += f"\n\n*Tools used: {', '.join(tool_parts)}*"
    
                try:
                    await response_msg.edit(content=current_content)
                except discord.errors.NotFound:
                    # Message might have been deleted
                    pass
            elif not current_content:
                 # Fallback if no content was generated
                 logger.warning("No content was generated by the agent.")
                 try:
                    await response_msg.edit(content="I'm sorry, I couldn't generate a response.")
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
