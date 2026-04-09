import os
import logging
from google_calendar import list_upcoming_events, create_event, delete_event, verify_date
from web_search import search_web, scrape_url
from research_agent import ResearchAgent
from dotenv import load_dotenv
from tool_registry import ToolRegistry
from skill_loader import get_skill_content

logger = logging.getLogger('tools')

load_dotenv()
SERVER_TIMEZONE = os.getenv("SERVER_TIMEZONE", "America/Los_Angeles")

# Initialize the registry
registry = ToolRegistry()

# --- Tool Registrations ---

@registry.register(
    name="list_upcoming_events",
    description="List the user's upcoming events on Google Calendar.",
    parameters={
        "type": "object",
        "properties": {
            "max_results": {
                "type": "integer",
                "description": "The maximum number of events to list. Default is 10."
            }
        },
        "required": []
    },
    required_skill="Calendar Management"
)
def list_upcoming_events_tool(max_results=10):
    return list_upcoming_events(max_results)

@registry.register(
    name="create_event",
    description="Create a new event on Google Calendar.",
    parameters={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "The title or summary of the event."
            },
            "description": {
                "type": "string",
                "description": "The description for the event."
            },
            "start_time": {
                "type": "string",
                "description": f"The start time of the event in ISO 8601 format (e.g. 2026-04-03T10:00:00). Assumes timezone: {SERVER_TIMEZONE}."
            },
            "end_time": {
                "type": "string",
                "description": f"The end time of the event in ISO 8601 format (e.g. 2026-04-03T11:00:00). Assumes timezone: {SERVER_TIMEZONE}."
            }
        },
        "required": ["summary", "start_time", "end_time"]
    },
    required_skill="Calendar Management"
)
def create_event_tool(summary, start_time, end_time, description=""):
    return create_event(summary, description, start_time, end_time, timezone=SERVER_TIMEZONE)

@registry.register(
    name="delete_event",
    description="Delete an event on Google Calendar using its ID.",
    parameters={
        "type": "object",
        "properties": {
            "event_id": {
                "type": "string",
                "description": "The ID of the event to delete."
            }
        },
        "required": ["event_id"]
    },
    required_skill="Calendar Management"
)
def delete_event_tool(event_id):
    return delete_event(event_id)

@registry.register(
    name="verify_date",
    description="Verify the day of the week for a given date. ALWAYS use this before creating an event to confirm you haven't hallucinated the calendar mapping for a day of the week.",
    parameters={
        "type": "object",
        "properties": {
            "date_string": {
                "type": "string",
                "description": "The date string to verify, typically in YYYY-MM-DD or ISO 8601 format."
            }
        },
        "required": ["date_string"]
    },
    required_skill="Calendar Management"
)
def verify_date_tool(date_string):
    return verify_date(date_string)

@registry.register(
    name="search_web",
    description="Search the web for up-to-date information, news, or answers to questions.",
    parameters={
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
)
def search_web_tool(query, max_results=5):
    return search_web(query, max_results)

@registry.register(
    name="deep_research",
    description="Spawn a sub-agent to perform deep, multi-step research on a complex topic. Use for complex questions, synthesis tasks, or when asked to 'research' something thoroughly.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The complex query or topic to research."
            }
        },
        "required": ["query"]
    },
    required_skill="Deep Research"
)
def deep_research_tool(query):
    return {"SPAWN_SUBAGENT": True, "query": query}

@registry.register(
    name="scrape_url",
    description="Scrape the full readable content of a specific URL. Use this when you have a specific link you want to read in depth.",
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full URL to scrape."
            }
        },
        "required": ["url"]
    }
)
def scrape_url_tool(url):
    return scrape_url(url)

# --- Compatibility Layer ---

@registry.register(
    name="get_skill",
    description="Load the full instructions for a specific skill by name. Call this before using a skill's tools to get detailed guidance on how to use them correctly.",
    parameters={
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "The exact name of the skill to load (e.g. 'Calendar Management', 'Deep Research')."
            }
        },
        "required": ["skill_name"]
    }
)
def get_skill_tool(skill_name):
    return get_skill_content(skill_name)

# Export OLLAMA_TOOLS for agent.py
OLLAMA_TOOLS = registry.get_ollama_tools()

def execute_tool(name, arguments):
    """
    Compatibility wrapper for executing tools via the registry.
    """
    # Note: We keep the logging here to match previous behavior
    logger.info(f"Executing tool: {name} with args: {arguments}")
    return registry.execute(name, arguments)
