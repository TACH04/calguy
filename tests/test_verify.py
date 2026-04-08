import asyncio
from agent import CalendarAgent

async def test_agent():
    agent = CalendarAgent()
    print("Testing verify_date tool integration...")
    prompt = "Can you schedule a team meeting for next Friday at 2 PM for 1 hour?"
    print(f"User> {prompt}")
    
    async for event in agent.chat_step(prompt, sender_name="TestUser"):
        if event['type'] == 'tool_call':
            print(f"[TOOL_CALL] {event['tool']} with args: {event['args']}")
        elif event['type'] == 'tool_result':
            print(f"[TOOL_RESULT] {event['result']}")
        elif event['type'] == 'error':
            print(f"[ERROR] {event['content']}")

if __name__ == "__main__":
    asyncio.run(test_agent())
