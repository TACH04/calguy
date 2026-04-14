import os
import re
import datetime
import time
import logging
import asyncio
import inspect
from dotenv import load_dotenv
import ollama

logger = logging.getLogger('agents.agent')

from core.tools import OLLAMA_TOOLS, execute_tool
from core.memory_manager import MemoryManager, estimate_tokens
from core.prompt_loader import load_prompt


load_dotenv()
MODEL = os.getenv("OLLAMA_MODEL", "qwen3-coder:30b")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "32768"))
SERVER_TIMEZONE = os.getenv("SERVER_TIMEZONE", "America/Los_Angeles")


def get_system_prompt():
    """Generates a dynamic system prompt with the current time and context."""
    now = datetime.datetime.now()
    
    enable_scraping = os.getenv("ENABLE_WEB_SCRAPING", "false").lower() == "true"
    enable_deep_research = os.getenv("ENABLE_DEEP_RESEARCH", "false").lower() == "true"

    template = load_prompt("main_system.md")

    
    optional_tools = ""
    if enable_scraping:
        optional_tools += "\n   - `scrape_url`: Use to read the full content of a specific page when snippets aren't enough."
    
    if enable_deep_research:
        optional_tools += "\n   - `investigate_topic`: Use for complex questions requiring synthesis from multiple sources or a comprehensive report."

    return template.format(
        now=now.strftime('%A, %Y-%m-%d %H:%M:%S'),
        timezone=SERVER_TIMEZONE,
        optional_tools=optional_tools
    )





class GeneralAgent:
    def __init__(self):
        self.model = MODEL
        self.last_activity_time = time.time()
        self.memory = MemoryManager(model=self.model)
        self.reset()

    def reset(self):
        prompt = get_system_prompt()
        self.memory.reset({"role": "system", "content": prompt})
        self.last_activity_time = time.time()

    def load_history(self, messages: list[dict]):
        """
        Restore agent memory from a persisted list of messages (e.g., loaded from disk).
        Images are expected to be Base64-encoded strings and are decoded back to bytes.
        The current system prompt is always prepended, replacing any saved one.
        """
        import base64
        restored = []
        for msg in messages:
            # Skip saved system messages — we always regenerate a fresh one
            if msg.get("role") == "system" and not msg.get("is_memory"):
                continue
            m = dict(msg)
            if m.get("images"):
                decoded_images = []
                for img in m["images"]:
                    if isinstance(img, str):
                        try:
                            decoded_images.append(base64.b64decode(img))
                        except Exception:
                            decoded_images.append(img)  # keep as-is if decode fails
                    else:
                        decoded_images.append(img)
                m["images"] = decoded_images
            restored.append(m)

        # Prepend a fresh system prompt, then load the rest
        fresh_system = {"role": "system", "content": get_system_prompt()}
        self.memory.load_messages([fresh_system] + restored)
        self.last_activity_time = time.time()
        logger.info(f"GeneralAgent: session restored with {len(restored)} historical messages.")

    @property
    def messages(self):
        """Expose the underlying message list (for compatibility with callers)."""
        return self.memory.messages

    @property
    def compression_count(self):
        return self.memory.compression_count

    def get_history(self):
        """Returns a JSON-serializable version of the message history."""
        import base64
        serializable_messages = []
        for msg in self.memory.messages:
            full_msg = dict(msg)
            if 'images' in full_msg and full_msg['images']:
                # Convert bytes to base64 strings for frontend delivery
                b64_images = []
                for img in full_msg['images']:
                    if isinstance(img, bytes):
                        b64_images.append(base64.b64encode(img).decode('utf-8'))
                    else:
                        b64_images.append(img)
                full_msg['images'] = b64_images
            serializable_messages.append(full_msg)
        return serializable_messages

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


    async def chat_step(self, user_input=None, sender_name=None, images: list = None):
        """
        Takes user input, appends to history, and processes one turn of Ollama (async).
        Yields status updates and intermediate results.

        Args:
            user_input: The text message from the user.
            sender_name: Optional display name of the sender.
            images: Optional list of raw image bytes to pass to the vision model.
        """
        self.last_activity_time = time.time()

        # Async Ollama client
        client = ollama.AsyncClient()

        if user_input or images:
            msg_content = f"[Sender: {sender_name}] {user_input}" if sender_name else (user_input or "")
            user_msg = {"role": "user", "content": msg_content}
            if images:
                user_msg["images"] = images
                logger.info(f"User message includes {len(images)} image(s).")
            self.memory.append(user_msg)

            vision_note = f" (with {len(images)} image{'s' if len(images) > 1 else ''})" if images else ""
            yield {"type": "status", "content": f"Assistant is thinking{vision_note}...", "tokens": 0}
            
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
                
                msg = {"role": "assistant", "content": full_message, "tokens": estimate_tokens(full_message)}
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

                        # Support real-time debug events from tools
                        debug_queue = asyncio.Queue()
                        def debug_callback(event):
                            debug_queue.put_nowait(event)

                        # Execute tool in a task so we can poll for debug events
                        raw_result = execute_tool(tool_name, tool_args, debug_callback=debug_callback)
                        
                        if inspect.isawaitable(raw_result):
                            # It's a coroutine (like scrape_url), so we can poll for debug events
                            tool_coro_task = asyncio.create_task(raw_result)
                            while not tool_coro_task.done():
                                try:
                                    # Wait for a debug event or short timeout
                                    d_event = await asyncio.wait_for(debug_queue.get(), timeout=0.2)
                                    yield d_event
                                except asyncio.TimeoutError:
                                    pass
                                except Exception as e:
                                    logger.error(f"Error polling debug events: {e}")
                                    break
                            
                            tool_result = await tool_coro_task
                            # Drain any remaining debug events
                            while not debug_queue.empty():
                                yield debug_queue.get_nowait()
                        else:
                            # It's a sync result, debug events (if any) are already in the queue
                            tool_result = raw_result
                            while not debug_queue.empty():
                                yield debug_queue.get_nowait()
                        

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


async def cli_chat_loop():
    print(f"Starting Brolympus Bot CLI Harness (Model: {MODEL})")
    print("Type 'quit' or 'exit' to stop.\n")
    
    agent = GeneralAgent()
    
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
