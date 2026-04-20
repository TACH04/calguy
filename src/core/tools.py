import os
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from integrations.google_calendar import list_upcoming_events, create_event, delete_event, verify_date
from integrations.web_search import search_web, scrape_url
from agents.research_agent import ResearchAgent
from dotenv import load_dotenv
from core.tool_registry import ToolRegistry
from core.skill_loader import get_skill_content
from bot.reminder_manager import reminder_manager

logger = logging.getLogger('tools')

load_dotenv()
SERVER_TIMEZONE = os.getenv("SERVER_TIMEZONE", "America/Los_Angeles")
ENABLE_WEB_SCRAPING = os.getenv("ENABLE_WEB_SCRAPING", "false").lower() == "true"
ENABLE_DEEP_RESEARCH = os.getenv("ENABLE_DEEP_RESEARCH", "false").lower() == "true"

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
                "description": "The maximum number of events to list. Default is 20."
            }
        },
        "required": []
    }
)
def list_upcoming_events_tool(max_results=20):
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
                "description": f"The end time of the event in ISO 8601 format (e.g. 2026-04-03T11:00:00). Assumes timezone: {SERVER_TIMEZONE}. If omitted, defaults to 2 hours after start_time."
            }
        },
        "required": ["summary", "start_time"]
    }
)
def create_event_tool(summary, start_time, end_time=None, description=""):
    if not end_time:
        try:
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            if start_dt.tzinfo is not None:
                # Normalize offset-aware timestamps to the configured calendar timezone
                # and send naive local datetimes to avoid offset/timeZone conflicts.
                start_dt = start_dt.astimezone(ZoneInfo(SERVER_TIMEZONE)).replace(tzinfo=None)
                start_time = start_dt.isoformat(timespec='seconds')
            end_time = (start_dt + timedelta(hours=2)).isoformat(timespec='seconds')
        except (ValueError, TypeError):
            return (
                "Error: start_time must be a valid ISO 8601 datetime "
                "(e.g. 2026-04-03T10:00:00) when end_time is omitted."
            )
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
    }
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
    }
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
    name="rsvp_to_event",
    description="RSVP a user to an event.",
    parameters={
        "type": "object",
        "properties": {
            "event_id": {
                "type": "string",
                "description": "The ID of the event."
            },
            "user_id": {
                "type": "integer",
                "description": "The Discord numeric ID of the user RSVPing."
            },
            "status": {
                "type": "string",
                "enum": ["going", "maybe", "declined"],
                "description": "The RSVP status."
            }
        },
        "required": ["event_id", "user_id", "status"]
    }
)
def rsvp_to_event_tool(event_id, user_id, status):
    reminder_manager.add_subscription(event_id, user_id, status)
    return f"Successfully RSVP'd user {user_id} as {status} for event {event_id}."

@registry.register(
    name="check_rsvp_status",
    description="Check the RSVP status of a user for a specific event.",
    parameters={
        "type": "object",
        "properties": {
            "event_id": {
                "type": "string",
                "description": "The ID of the event."
            },
            "user_id": {
                "type": "integer",
                "description": "The Discord numeric ID of the user."
            }
        },
        "required": ["event_id", "user_id"]
    }
)
def check_rsvp_status_tool(event_id, user_id):
    subs = reminder_manager.get_all_subscribers(event_id)
    if user_id in subs.get('going', []):
        return "going"
    elif user_id in subs.get('maybe', []):
        return "maybe"
    elif user_id in subs.get('declined', []):
        return "declined"
    else:
        return "none"

if ENABLE_DEEP_RESEARCH:
    @registry.register(
        name="investigate_topic",
        description="Gather detailed information on a topic by performing multiple search and reading steps. Use this for complex questions or when you need more than a simple search result.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The topic or question to investigate in depth."
                }
            },
            "required": ["query"]
        }
    )
    def investigate_topic_tool(query):
        return {"SPAWN_SUBAGENT": True, "query": query}

if ENABLE_WEB_SCRAPING:
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
    def scrape_url_tool(url, debug_callback=None):
        return scrape_url(url, debug_callback=debug_callback)


# --- Compatibility Layer ---


# Export OLLAMA_TOOLS for agent.py
OLLAMA_TOOLS = registry.get_ollama_tools()

def execute_tool(name, arguments, debug_callback=None):
    """
    Compatibility wrapper for executing tools via the registry.
    """
    # Note: We keep the logging here to match previous behavior
    logger.info(f"Executing tool: {name} with args: {arguments}")
    return registry.execute(name, arguments, debug_callback=debug_callback)
