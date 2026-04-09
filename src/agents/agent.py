import os
import re
import datetime
import time
import logging
from dotenv import load_dotenv
import ollama

logger = logging.getLogger('agents.agent')

from core.tools import OLLAMA_TOOLS, execute_tool
from core.memory_manager import MemoryManager, estimate_tokens

load_dotenv()
MODEL = os.getenv("OLLAMA_MODEL", "qwen3-coder:30b")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "32768"))
SERVER_TIMEZONE = os.getenv("SERVER_TIMEZONE", "America/Los_Angeles")


def get_system_prompt():
    """Generates a dynamic system prompt with the current time and context."""
    now = datetime.datetime.now()
    return f"""You are Brolympus Bot. You manage the crew's shared Google Calendar and search the web for information using the tools provided.
Current Date and Time: {now.strftime('%A, %Y-%m-%d %H:%M:%S')}
Timezone: {SERVER_TIMEZONE}

### 📅 CALENDAR MANAGEMENT PROTOCOLS
1. **MANDATORY Date Verification**: When resolving relative dates (like "next Tuesday", "tomorrow", or "next weekend"), you MUST ALWAYS use the `verify_date` tool to confirm that the chosen date string actually aligns with the requested day of the week. Do this BEFORE scheduling the event.
2. **Missing Year**: If a year is not specified, assume the current year or the next occurrence of that date.
3. **Always Confirm Details**: When scheduling events, always confirm the time and duration.
4. **Event Editing**: To edit an event, delete the original event and create a new one with the updated details. Do not attempt to modify events in place.

### 🔍 WEB SEARCH & INVESTIGATION PROTOCOLS
1. **Tool Hierarchy**:
   - `search_web`: Use for quick facts, current headlines, or finding URLs.
   - `scrape_url`: Use to read the full content of a specific page when snippets aren't enough.
   - `investigate_topic`: Use for complex questions requiring synthesis from multiple sources or a comprehensive report.
2. **Multi-Query Strategy**: Never rely on a single search query for complex topics. If the first fails, rephrase and try again.
3. **Citation**: Cite your findings if possible (e.g., "According to [Source Name]...").
4. **No Placeholders**: Do not guess or hallucinate details missing from search results.

### RESPONSE GUIDELINES
- Be concise.
- Let the user know what tool actions were taken.
- IMPORTANT: Use ONLY the JSON tool calling mechanism. No XML, no preamble.
"""



class CalendarAgent:
    def __init__(self):
        self.model = MODEL
        self.last_activity_time = time.time()
        self.memory = MemoryManager(model=self.model)
        self.reset()

    def reset(self):
        prompt = get_system_prompt()
        self.memory.reset({"role": "system", "content": prompt})
        self.last_activity_time = time.time()

    @property
    def messages(self):
        """Expose the underlying message list (for compatibility with callers)."""
        return self.memory.messages

    @property
    def compression_count(self):
        return self.memory.compression_count

    def get_history(self):
        return self.memory.messages

    def get_total_tokens(self):
        return self.memory.get_total_tokens()

    def get_session_info(self):
        now = time.time()
        idle_time = now - self.last_activity_time
        msg_count = len([m for m in self.memory.messages if m['role'] != 'system'])
        return {
            "model": self.model,
            "message_count": msg_count,
            "idle_seconds": int(idle_time),
            "estimated_tokens": self.get_total_tokens(),
            "compression_count": self.compression_count,
        }

    def check_auto_reset(self):
        """Reset conversation if inactive for > 10 minutes."""
        now = time.time()
        if now - self.last_activity_time > 600:
            logger.info("Session inactive for > 10 minutes. Auto-resetting context.")
            self.reset()
            return True
        return False


    async def chat_step(self, user_input=None, sender_name=None):
        """
        Takes user input, appends to history, and processes one turn of Ollama (async).
        Yields status updates and intermediate results.
        """
        self.check_auto_reset()
        self.last_activity_time = time.time()

        # Async Ollama client
        client = ollama.AsyncClient()

        if user_input:
            msg_content = f"[Sender: {sender_name}] {user_input}" if sender_name else user_input
            self.memory.append({"role": "user", "content": msg_content})

            yield {"type": "status", "content": "Assistant is thinking...", "tokens": 0}
            
        try:
            MAX_TURNS = 1000
            turn_count = 0
            
            while turn_count < MAX_TURNS:
                response = await client.chat(
                    model=self.model,
                    messages=self.messages,
                    tools=OLLAMA_TOOLS,
                    stream=True,
                    options={"num_ctx": OLLAMA_NUM_CTX}
                )
                
                # Streaming assembly
                full_message = ""
                tool_calls = None
                
                async for chunk in response:
                    msg_chunk = chunk.get('message', {})
                    if hasattr(msg_chunk, 'model_dump'):
                        msg_chunk = msg_chunk.model_dump()
                        
                    content_chunk = msg_chunk.get('content', '')
                    if content_chunk:
                        full_message += content_chunk
                        yield {"type": "stream_chunk", "content": content_chunk}
                        
                    if msg_chunk.get('tool_calls'):
                        if tool_calls is None:
                            tool_calls = msg_chunk['tool_calls']
                        else:
                            # Not merging chunked tools right now if Ollama passes them sequentially, usually they come in one chunk from Ollama python client.
                            pass
                
                # SAFETY NET: Check for leaked XML tool calls in the raw output
                if tool_calls is None and '<function=' in full_message:
                    match = re.search(r'<function=(.*?)>(.*?)</function>', full_message, re.DOTALL)
                    if match:
                        func_name = match.group(1).strip()
                        params_str = match.group(2)
                        
                        args = {}
                        param_matches = re.finditer(r'<parameter=(.*?)>(.*?)</parameter>', params_str, re.DOTALL)
                        for pm in param_matches:
                            try:
                                val = pm.group(2).strip()
                                if val.isdigit():
                                    val = int(val)
                                elif val.lower() == 'true':
                                    val = True
                                elif val.lower() == 'false':
                                    val = False
                                args[pm.group(1).strip()] = val
                            except Exception:
                                args[pm.group(1).strip()] = pm.group(2).strip()
                                
                        tool_calls = [{
                            "function": {
                                "name": func_name,
                                "arguments": args
                            }
                        }]
                        # Clean the message so the assistant memory doesn't contain raw XML
                        full_message = re.sub(r'<function=.*?>(.*?)</function>\n?(?:</tool_call>\n?)?', '', full_message, flags=re.DOTALL).strip()
                        logger.info(f"Regex safety net dynamically captured tool call: {func_name}")
                
                msg = {"role": "assistant", "content": full_message}
                if tool_calls:
                    msg["tool_calls"] = tool_calls

                self.memory.append(msg)
                self.last_activity_time = time.time()

                # Check for tool invocations
                if msg.get('tool_calls'):
                    for tool_call in msg['tool_calls']:
                        tool_name = tool_call['function']['name']
                        tool_args = tool_call['function']['arguments']

                        yield {
                            "type": "tool_call",
                            "tool": tool_name,
                            "args": tool_args,
                            "tokens": estimate_tokens(tool_name) + estimate_tokens(str(tool_args))
                        }

                        import inspect
                        
                        raw_result = execute_tool(tool_name, tool_args)
                        if inspect.isawaitable(raw_result):
                            tool_result = await raw_result
                        else:
                            tool_result = raw_result
                        

                        if isinstance(tool_result, dict) and tool_result.get("SPAWN_SUBAGENT"):
                            query = tool_result.get("query")
                            yield {"type": "status", "content": "Initializing Research Sub-Agent...", "tokens": 0}
                            brief = ""
                            async for b_event in self.memory.generate_brief():
                                if b_event["type"] == "debug_event" or b_event["type"] == "debug_stream":
                                    yield b_event
                                elif b_event["type"] == "brief_result":
                                    brief = b_event["content"]
                            
                            from agents.research_agent import ResearchAgent
                            debug_events = []
                            def capture_debug(event):
                                debug_events.append(event)
                                
                            subagent = ResearchAgent(model=self.model, debug_callback=capture_debug)
                            report = ""
                            
                            yield {"type": "subagent_start", "content": "Investigation initiated"}
                            
                            async for sub_event in subagent.research_loop(query, brief):
                                while debug_events:
                                    yield debug_events.pop(0)
                                    
                                yield sub_event
                                if sub_event.get("type") == "subagent_final_report":
                                    report = sub_event.get("content", "")
                                    
                            while debug_events:
                                yield debug_events.pop(0)
                                    
                            tool_result = report

                        # append via memory (handles pruning automatically)
                        self.memory.append({
                            "role": "tool",
                            "name": tool_name,
                            "content": str(tool_result),
                        })

                        yield {
                            "type": "tool_result",
                            "tool": tool_name,
                            "result": tool_result,
                            "tokens": estimate_tokens(str(tool_result))
                        }
                        
                    yield {"type": "status", "content": "Assistant is processing tool results...", "tokens": estimate_tokens("Assistant is processing tool results...")}
                    
                    # Check if we need to compress history mid-loop after tool results
                    if self.memory.needs_compression():
                        total_tokens = self.get_total_tokens()
                        logger.info(f"Token count ({total_tokens}) exceeds threshold mid-loop. Compressing memory...")
                        yield {"type": "status", "content": "Compressing conversation memory...", "tokens": 0}
                        async for c_event in self.memory.compress_history():
                            yield c_event
                        yield {"type": "compressed"}

                    turn_count += 1
                else:
                    yield {"type": "message", "content": msg.get('content'), "tokens": msg['tokens']}
                    break
                    
            if turn_count >= MAX_TURNS:
                yield {"type": "error", "content": f"Reached maximum number of tool turns ({MAX_TURNS})."}

            # Check if we need to compress history at the end of the turn
            if self.memory.needs_compression():
                total_tokens = self.get_total_tokens()
                logger.info(f"Token count ({total_tokens}) exceeds threshold at end of turn. Compressing memory...")
                yield {"type": "status", "content": "Compressing conversation memory...", "tokens": 0}
                async for c_event in self.memory.compress_history():
                    yield c_event
                yield {"type": "compressed"}
                
        except Exception as e:
            yield {"type": "error", "content": str(e)}

import asyncio

async def cli_chat_loop():
    print(f"Starting Brolympus Bot CLI Harness (Model: {MODEL})")
    print("Type 'quit' or 'exit' to stop.\n")
    
    agent = CalendarAgent()
    
    while True:
        try:
            user_input = input("You> ").strip()
            if not user_input:
                continue
                
            if user_input.lower() in ['quit', 'exit']:
                print("Goodbye!")
                break
                
            async for event in agent.chat_step(user_input, sender_name="CLIUser"):
                if event['type'] == 'status':
                    print(event['content'])
                elif event['type'] == 'tool_call':
                    print(f"-> Calling Tool: {event['tool']} with {event['args']}")
                elif event['type'] == 'tool_result':
                    print(f"<- Tool Result: {event['result']}")
                elif event['type'] == 'message':
                    print(f"\nAssistant> {event['content']}\n")
                elif event['type'] == 'stream_chunk':
                    print(event['content'], end='', flush=True)
                elif event['type'] == 'error':
                    print(f"\n[ERROR] {event['content']}\n")
                    
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break

if __name__ == "__main__":
    asyncio.run(cli_chat_loop())
