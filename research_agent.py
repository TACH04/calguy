import os
import re
import ollama
import logging
from web_search import search_web, scrape_url

logger = logging.getLogger('research_agent')
MODEL = os.getenv("OLLAMA_MODEL", "qwen3-coder:30b")

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
    def __init__(self, model=MODEL):
        self.model = model
        self.messages = []

    async def execute_sub_tool(self, name, args):
        if name == "search_web":
            return search_web(args.get("query"), args.get("max_results", 5))
        elif name == "scrape_url":
            # scrape_url is async now, so we await it
            return await scrape_url(args.get("url"), args.get("query"))
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
                    stream=False
                )
                
                if hasattr(response, "model_dump"):
                    response = response.model_dump()
                    
                msg = response.get("message", {})
                tool_calls = msg.get("tool_calls", [])
                content = msg.get("content", "").strip()

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
                        
                        yield {"type": "subagent_thought", "content": f"Sub-agent is executing {func_name}..."}
                        
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
                        
                        yield {"type": "subagent_thought", "content": f"Sub-agent processed {func_name} results."}
                    turn_count += 1
                else:
                    # Final answer reached
                    if content:
                        yield {"type": "subagent_final_report", "content": content}
                        break
                    else:
                        yield {"type": "subagent_thought", "content": "Reached empty response. Trying again."}
                        turn_count += 1
                        
            if turn_count >= MAX_TURNS:
                yield {"type": "subagent_final_report", "content": "Research stopped: Maximum number of investigation steps reached. Please refine your query."}

        except Exception as e:
            logger.error(f"Research agent error: {e}")
            yield {"type": "error", "content": f"Sub-agent failed: {str(e)}"}
