import os
import re
import ollama
import logging
from integrations.web_search import search_web, scrape_url

logger = logging.getLogger('agents.research_agent')
MODEL = os.getenv("OLLAMA_MODEL", "qwen3-coder:30b")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "32768"))

# The tools available to the sub-agent
SUB_AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for up-to-date information, news, or answers to questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to look up."
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "The maximum number of results to return. Default is 5."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scrape_url",
            "description": "Scrape the full readable content of a specific URL to get detailed context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to scrape."
                    },
                    "query": {
                        "type": "string",
                        "description": "The specific query you want to extract from the page. This is required to summarize the page."
                    }
                },
                "required": ["url", "query"]
            }
        }
    }
]

class ResearchAgent:
    def __init__(self, model=MODEL, debug_callback=None):
        self.model = model
        self.messages = []
        self.debug_callback = debug_callback

    async def execute_sub_tool(self, name, args):
        if name == "search_web":
            return search_web(args.get("query"), args.get("max_results", 5))
        elif name == "scrape_url":
            # scrape_url is async now, so we await it
            return await scrape_url(args.get("url"), args.get("query"), debug_callback=self.debug_callback)
        else:
            return f"Error: {name} is not a valid tool."

    async def research_loop(self, query, context_brief):
        """
        An async generator that streams thoughts, sub-tool calls, and the final report.
        """
        system_prompt = f"""You are a specialized Research Agent.
Your task is to thoroughly investigate the following query and provide a comprehensive, factual report.
You have a limited number of turns, so be efficient. 
ALWAYS verify information across multiple sources if possible.
If a scrape returns irrelevant information (like a landing page or ad), try a different search query or engine.
Do NOT output conversational filler in your thought processes. Only invoke tools, or output the final report.

Context Brief from Main Conversation:
{context_brief}

Target Research Query:
{query}
"""
        self.messages = [{"role": "system", "content": system_prompt}]
        
        yield {"type": "subagent_thought", "content": f"Initializing research sequence for: '{query}'..."}
        
        try:
            MAX_TURNS = 6
            turn_count = 0
            client = ollama.AsyncClient()
            
            while turn_count < MAX_TURNS:
                response = await client.chat(
                    model=self.model,
                    messages=self.messages,
                    tools=SUB_AGENT_TOOLS,
                    stream=True,
                    options={"num_ctx": OLLAMA_NUM_CTX}
                )
                
                full_message = ""
                tool_calls = None
                
                async for chunk in response:
                    if hasattr(chunk, 'model_dump'):
                        chunk = chunk.model_dump()
                        
                    msg_chunk = chunk.get('message', {})
                    content_chunk = msg_chunk.get("content", "")
                    
                    if content_chunk:
                        full_message += content_chunk
                        if self.debug_callback:
                            self.debug_callback({"type": "debug_stream", "category": "subagent", "content": content_chunk})
                        yield {"type": "subagent_stream_chunk", "content": content_chunk}
                        
                    if msg_chunk.get('tool_calls'):
                        if tool_calls is None:
                            tool_calls = msg_chunk['tool_calls']
                        else:
                            pass
                            
                msg = {"role": "assistant", "content": full_message}
                if tool_calls:
                    msg["tool_calls"] = tool_calls

                self.messages.append(msg)
                
                if tool_calls:
                    for tool_call in tool_calls:
                        func_name = tool_call['function']['name']
                        func_args = tool_call['function']['arguments']
                        
                        yield {
                            "type": "subagent_tool_call",
                            "tool": func_name,
                            "args": func_args
                        }
                        
                        if self.debug_callback:
                            self.debug_callback({"type": "debug_event", "category": "subagent", "content": f"Sub-agent called tool: {func_name}"})
                        
                        # execute
                        result = await self.execute_sub_tool(func_name, func_args)
                        
                        yield {
                            "type": "subagent_tool_result",
                            "tool": func_name,
                            "result": str(result),
                            "tokens": len(str(result)) // 4
                        }
                        
                        self.messages.append({
                            "role": "tool",
                            "name": func_name,
                            "content": str(result)
                        })
                        
                    turn_count += 1
                else:
                    # Final answer reached
                    if full_message:
                        yield {"type": "subagent_final_report", "content": full_message}
                        break
                    else:
                        if self.debug_callback:
                            self.debug_callback({"type": "debug_event", "category": "subagent", "content": "Reached empty response. Trying again."})
                        turn_count += 1
                        
            if turn_count >= MAX_TURNS:
                yield {"type": "subagent_final_report", "content": "Research stopped: Maximum number of investigation steps reached. Please refine your query."}

        except Exception as e:
            logger.error(f"Research agent error: {e}")
            yield {"type": "error", "content": f"Sub-agent failed: {str(e)}"}
