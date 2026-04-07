import os
import json
import logging
from google_calendar import list_upcoming_events, create_event, delete_event, verify_date
from dotenv import load_dotenv

logger = logging.getLogger('tools')

load_dotenv()
SERVER_TIMEZONE = os.getenv("SERVER_TIMEZONE", "America/Los_Angeles")

# Outline how Ollama expects tools to be defined
# Ollama compatible format
OLLAMA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_upcoming_events",
            "description": "List the user's upcoming events on Google Calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {
                        "type": "integer",
                        "description": "The maximum number of events to list. Default is 10."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": "Create a new event on Google Calendar.",
            "parameters": {
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
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_event",
            "description": "Delete an event on Google Calendar using its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The ID of the event to delete."
                    }
                },
                "required": ["event_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "verify_date",
            "description": "Verify the day of the week for a given date. ALWAYS use this before creating an event to confirm you haven't hallucinated the calendar mapping for a day of the week.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_string": {
                        "type": "string",
                        "description": "The date string to verify, typically in YYYY-MM-DD or ISO 8601 format."
                    }
                },
                "required": ["date_string"]
            }
        }
    }
]

def execute_tool(name, arguments):
    """
    Executes the given tool by name with the given arguments.
    """
    logger.info(f"Executing tool: {name} with args: {arguments}")
    try:
        if name == "list_upcoming_events":
            max_results = arguments.get("max_results", 10)
            return list_upcoming_events(max_results)
        elif name == "create_event":
            # Add descriptions or defaults if missing
            summary = arguments.get("summary")
            description = arguments.get("description", "")
            start_time = arguments.get("start_time")
            end_time = arguments.get("end_time")
            return create_event(summary, description, start_time, end_time, timezone=SERVER_TIMEZONE)
        elif name == "delete_event":
            event_id = arguments.get("event_id")
            return delete_event(event_id)
        elif name == "verify_date":
            date_string = arguments.get("date_string")
            return verify_date(date_string)
        else:
            return f"Error: Function {name} not found."
    except Exception as e:
        return f"Error executing {name}: {str(e)}"
