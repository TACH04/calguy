import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv
from agent import CalendarAgent

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

if not DISCORD_TOKEN or DISCORD_TOKEN == "your_bot_token_here":
    print("ERROR: DISCORD_TOKEN is not set in .env")
    exit(1)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Shared calendar agent instance
print("Initializing CalendarAgent...")
agent = CalendarAgent()
print("CalendarAgent initialized.")

@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user.name} ({bot.user.id})')
    print(f'Connected to {len(bot.guilds)} server(s):')
    for guild in bot.guilds:
        print(f' - {guild.name} (ID: {guild.id})')
    print('Bot is ready to receive commands!')
    print('------')

@bot.command(name='help')
async def help_cmd(ctx):
    """Displays this help message."""
    help_text = """**CalGuy Commands:**
`!clear` - Reset my conversation context immediately.
`!session` - Display current session details (model, message count, idle time).
`!help` - Display this message.

Or just mention me or talk directly to me to check and modify your Google Calendar!"""
    await ctx.send(help_text)

@bot.command(name='clear')
async def clear_cmd(ctx):
    """Reset the conversation context."""
    agent.reset()
    await ctx.send("✅ Conversation context has been cleared.")

@bot.command(name='session')
async def session_cmd(ctx):
    """Display current session details."""
    info = agent.get_session_info()
    idle_str = f"{info['idle_seconds']} seconds"
    if info['idle_seconds'] > 60:
        idle_str = f"{info['idle_seconds'] // 60} min {info['idle_seconds'] % 60} sec"
    
    msg = (f"**Session Info:**\n"
           f"- Model: `{info['model']}`\n"
           f"- Message Count: `{info['message_count']}`\n"
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
        await message.reply("Yes? How can I help you with your calendar today? (Type `!help` for commands)")
        return

    sender_name = message.author.display_name
    
    response_msg = await message.reply("*(Thinking...)*")
    current_content = ""
    last_edit_time = asyncio.get_event_loop().time()
    
    try:
        async for event in agent.chat_step(content, sender_name=sender_name):
            if event['type'] == 'status':
                # Optional: edit to show status
                if not current_content:
                    await response_msg.edit(content=f"*({event['content']})*")
            elif event['type'] == 'tool_call':
                if not current_content:
                    await response_msg.edit(content=f"*(Calling tool: {event['tool']}...)*")
            elif event['type'] == 'stream_chunk':
                current_content += event['content']
                now = asyncio.get_event_loop().time()
                # Edit every 1 second to avoid rate limits
                if now - last_edit_time > 1.0:
                    try:
                        await response_msg.edit(content=current_content)
                        last_edit_time = now
                    except discord.errors.HTTPException:
                        pass # Ignore temporary edit failures
            elif event['type'] == 'tool_result':
                pass # Silent on result, wait for the agent to talk
            elif event['type'] == 'error':
                await message.reply(f"❌ Error: {event['content']}")
                break
                
        # Final update to ensure we didn't miss the last chunks
        if current_content:
            await response_msg.edit(content=current_content)
        elif not current_content:
             # Fallback if no content was generated
             await response_msg.edit(content="I'm sorry, I couldn't generate a response.")
            
    except Exception as e:
        await message.reply(f"❌ An error occurred: {e}")

if __name__ == '__main__':
    print("Starting Discord bot...")
    bot.run(DISCORD_TOKEN)
