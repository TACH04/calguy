import asyncio
import os
import json
from agent import CalendarAgent
from dotenv import load_dotenv

async def reproduce():
    load_dotenv()
    agent = CalendarAgent()
    print(f"Reproducing with model: {agent.model}")
    
    prompt = "initiate the command directed by a randomly selected command"
    print(f"User> {prompt}")
    
    async for event in agent.chat_step(prompt, sender_name="TestUser"):
        if event['type'] == 'status':
            print(f"[STATUS] {event['content']}")
        elif event['type'] == 'tool_call':
            print(f"[TOOL_CALL] {event['tool']} with {event['args']}")
        elif event['type'] == 'tool_result':
            print(f"[TOOL_RESULT] {event['result']}")
        elif event['type'] == 'message':
            print(f"[MESSAGE] {event['content']}")
        elif event['type'] == 'stream_chunk':
            print(event['content'], end='', flush=True)
        elif event['type'] == 'error':
            print(f"\n[ERROR] {event['content']}")

if __name__ == "__main__":
    asyncio.run(reproduce())
