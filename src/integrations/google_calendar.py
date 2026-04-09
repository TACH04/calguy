import os
import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import datetime
import logging
from dotenv import load_dotenv

logger = logging.getLogger('integrations.google_calendar')

load_dotenv()

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_ID = os.getenv('CALENDAR_ID', 'primary')

def get_calendar_service():
    """Shows basic usage of the Google Calendar API.
    Prints the start and name of the next 10 events on the user's calendar.
    """
    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            credentials_file = os.getenv('CREDENTIALS_FILE', 'credentials.json')
            if not os.path.exists(credentials_file):
                # Try to find any .json file that looks like a client secret
                import glob
                json_files = glob.glob('client_secret_*.json')
                if json_files:
                    credentials_file = json_files[0]
                else:
                    raise FileNotFoundError(f"Google Calendar credentials not found. Expected at: {credentials_file}")
            
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            # Use a fixed port to avoid issues, or run local server
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    return build('calendar', 'v3', credentials=creds, static_discovery=False)

def list_upcoming_events(max_results=10, time_min=None):
    """
    Lists the upcoming events on the user's primary calendar.
    """
    service = get_calendar_service()
    
    if not time_min:
        time_min = datetime.datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
        
    logger.info(f'Fetching the upcoming {max_results} events from Google Calendar.')
    events_result = service.events().list(calendarId=CALENDAR_ID, timeMin=time_min,
                                        maxResults=max_results, singleEvents=True,
                                        orderBy='startTime').execute()
    events = events_result.get('items', [])

    if not events:
        return 'No upcoming events found.'
    
    result = "Upcoming events:\n"
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        result += f"- {event['summary']} at {start} (ID: {event['id']})\n"
    return result

def create_event(summary, description, start_time, end_time, timezone='UTC'):
    """
    Creates a new event on the primary calendar.
    Format expectations for time: '2026-04-03T10:00:00'.
    """
    service = get_calendar_service()
    
    event = {
        'summary': summary,
        'description': description,
        'start': {
            'dateTime': start_time,
            'timeZone': timezone,
        },
        'end': {
            'dateTime': end_time,
            'timeZone': timezone,
        },
    }

    event_result = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
    return f"Event created: {event_result.get('htmlLink')} (ID: {event_result.get('id')})"

def delete_event(event_id):
    """
    Deletes an event based on its event ID.
    """
    service = get_calendar_service()
    try:
        service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
        return f"Successfully deleted event {event_id} from {CALENDAR_ID}."
    except Exception as e:
        return f"Error deleting event: {str(e)}"

def verify_date(date_string):
    """
    Parses a date string and returns the day of the week.
    Helps prevent hallucinating dates.
    Format expectations for time: '2026-04-03T10:00:00' or '2026-04-03'.
    """
    try:
        # Give a simpler fallback if they just provide a date
        if 'T' not in date_string:
            dt = datetime.datetime.strptime(date_string, '%Y-%m-%d')
        else:
            # Handle some basic iso formats
            clean_date = date_string.replace('Z', '+00:00')
            dt = datetime.datetime.fromisoformat(clean_date)
        return f"The date {date_string} falls on a {dt.strftime('%A')}."
    except Exception as e:
        return f"Error parsing date {date_string}: {str(e)}. Please ensure format is YYYY-MM-DD or ISO 8601."

if __name__ == '__main__':
    # Try fetching the calendar service to trigger authentication if needed
    get_calendar_service()
    print("Authentication successful.")
