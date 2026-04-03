import os
import json
import datetime
import time
from dotenv import load_dotenv
import ollama

from tools import OLLAMA_TOOLS, execute_tool

load_dotenv()
MODEL = os.getenv("OLLAMA_MODEL", "qwen3-coder:30b")
SERVER_TIMEZONE = os.getenv("SERVER_TIMEZONE", "America/Los_Angeles")

def get_system_prompt():
    """Generates a dynamic system prompt with the current time and context."""
    now = datetime.datetime.now()
    return f"""You are a helpful, professional AI calendar assistant.
You can manage the user's Google Calendar using the tools provided.

Current Context:
- Current Date and Time: {now.strftime('%A, %Y-%m-%d %H:%M:%S')}
- Timezone: {SERVER_TIMEZONE}

When scheduling events, always confirm the time and duration. If a year is not specified, assume the current year or the next occurrence of that date.
When responding after a tool call, be concise and let the user know what was done.
"""

def estimate_tokens(text):
    if not text:
        return 0
    return len(str(text)) // 4


class CalendarAgent:
    def __init__(self):
        self.model = MODEL
        self.last_activity_time = time.time()
        self.reset()
        
    def reset(self):
        prompt = get_system_prompt()
        self.messages = [
            {"role": "system", "content": prompt, "tokens": estimate_tokens(prompt)}
        ]
        self.last_activity_time = time.time()
        
    def get_history(self):
        return self.messages
        
    def get_session_info(self):
        now = time.time()
        idle_time = now - self.last_activity_time
        msg_count = len([m for m in self.messages if m['role'] != 'system'])
        return {
            "model": self.model,
            "message_count": msg_count,
            "idle_seconds": int(idle_time)
        }
        
    def check_auto_reset(self):
        """Reset conversation if inactive for > 10 minutes."""
        now = time.time()
        if now - self.last_activity_time > 600:
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
            tokens = estimate_tokens(msg_content)
            self.messages.append({"role": "user", "content": msg_content, "tokens": tokens})
            yield {"type": "status", "content": "Assistant is thinking...", "tokens": estimate_tokens("Assistant is thinking...")}
            
        try:
            MAX_TURNS = 10
            turn_count = 0
            
            while turn_count < MAX_TURNS:
                response = await client.chat(
                    model=self.model,
                    messages=self.messages,
                    tools=OLLAMA_TOOLS,
                    stream=True
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
                
                msg = {"role": "assistant", "content": full_message}
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                    
                msg['tokens'] = estimate_tokens(msg.get('content', ''))
                if msg.get('tool_calls'):
                    msg['tokens'] += estimate_tokens(str(msg['tool_calls']))
                    
                self.messages.append(msg)
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
                        
                        tool_result = execute_tool(tool_name, tool_args)
                        result_tokens = estimate_tokens(tool_result)
                        
                        self.messages.append({
                            "role": "tool",
                            "name": tool_name,
                            "content": str(tool_result),
                            "tokens": result_tokens
                        })
                        
                        yield {
                            "type": "tool_result",
                            "tool": tool_name,
                            "result": tool_result,
                            "tokens": result_tokens
                        }
                        
                    yield {"type": "status", "content": "Assistant is processing tool results...", "tokens": estimate_tokens("Assistant is processing tool results...")}
                    turn_count += 1
                else:
                    yield {"type": "message", "content": msg.get('content'), "tokens": msg['tokens']}
                    break
                    
            if turn_count >= MAX_TURNS:
                yield {"type": "error", "content": f"Reached maximum number of tool turns ({MAX_TURNS})."}
                
        except Exception as e:
            yield {"type": "error", "content": str(e)}

import asyncio

async def cli_chat_loop():
    print(f"Starting Calendar LLM Harness (Model: {MODEL})")
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
