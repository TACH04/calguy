import json
import os
import logging
import asyncio

logger = logging.getLogger('bot.reminder_manager')

REMINDERS_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'reminders.json')

class ReminderManager:
    def __init__(self):
        self.announced_events = set()  # Set of event_ids
        self.event_messages = {}       # event_id -> message_id
        self.event_embed_hashes = {}   # event_id -> hash (string)
        self.subscriptions = {}         # event_id -> dict of statuses -> list of user_ids
        self.sent_reminders = set()    # Set of event_ids
        self.dashboard_message_id = None # Single message ID for the announcement dashboard
        self.in_progress = set()       # Runtime lock for events being processed
        self._load()

    def _load(self):
        """Load state from data/reminders.json."""
        if os.path.exists(REMINDERS_FILE):
            try:
                with open(REMINDERS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.announced_events = set(data.get('announced_events', []))
                    self.event_messages = data.get('event_messages', {})
                    self.event_embed_hashes = data.get('event_embed_hashes', {})
                    self.subscriptions = data.get('subscriptions', {})
                    self.sent_reminders = set(data.get('sent_reminders', []))
                    self.dashboard_message_id = data.get('dashboard_message_id')
                logger.info(f"Loaded reminders state: {len(self.announced_events)} announced, {len(self.sent_reminders)} reminded.")
            except Exception as e:
                logger.error(f"Failed to load reminders state: {e}")

    def save(self):
        """Save state to data/reminders.json."""
        data = {
            'announced_events': list(self.announced_events),
            'event_messages': self.event_messages,
            'event_embed_hashes': self.event_embed_hashes,
            'subscriptions': self.subscriptions,
            'sent_reminders': list(self.sent_reminders),
            'dashboard_message_id': self.dashboard_message_id
        }
        os.makedirs(os.path.dirname(REMINDERS_FILE), exist_ok=True)
        try:
            with open(REMINDERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug("Reminders state saved.")
        except Exception as e:
            logger.error(f"Failed to save reminders state: {e}")

    def is_announced(self, event_id: str) -> bool:
        return event_id in self.announced_events

    def mark_announced(self, event_id: str, message_id: int):
        self.announced_events.add(event_id)
        self.event_messages[event_id] = message_id
        self.save()

    def get_event_id_by_message(self, message_id: int) -> str:
        for eid, mid in self.event_messages.items():
            if str(mid) == str(message_id):
                return eid
        return None

    def clear_all_messages(self):
        """Clear all message bindings (used when purging channel to re-sequence)."""
        self.event_messages.clear()
        self.event_embed_hashes.clear()
        self.save()

    def get_embed_hash(self, event_id: str) -> str:
        return self.event_embed_hashes.get(event_id)

    def set_embed_hash(self, event_id: str, embed_hash: str):
        self.event_embed_hashes[event_id] = embed_hash
        self.save()

    def add_subscription(self, event_id: str, user_id: int, status: str = "going"):
        if event_id not in self.subscriptions:
            self.subscriptions[event_id] = {"going": [], "maybe": [], "declined": []}
        
        # Remove from other statuses first
        self.remove_subscription_from_all(event_id, user_id)
            
        if status in self.subscriptions[event_id]:
            if user_id not in self.subscriptions[event_id][status]:
                self.subscriptions[event_id][status].append(user_id)
                self.save()

    def remove_subscription(self, event_id: str, user_id: int, status: str = "going"):
        if event_id in self.subscriptions and status in self.subscriptions[event_id]:
            if user_id in self.subscriptions[event_id][status]:
                self.subscriptions[event_id][status].remove(user_id)
                self.save()

    def remove_subscription_from_all(self, event_id: str, user_id: int):
        if event_id in self.subscriptions:
            changed = False
            for s in ["going", "maybe", "declined"]:
                if user_id in self.subscriptions[event_id].get(s, []):
                    self.subscriptions[event_id][s].remove(user_id)
                    changed = True
            if changed:
                self.save()

    def get_subscribers(self, event_id: str, status: str = "going") -> list:
        return self.subscriptions.get(event_id, {}).get(status, [])
        
    def get_all_subscribers(self, event_id: str) -> dict:
        return self.subscriptions.get(event_id, {"going": [], "maybe": [], "declined": []})

    def is_reminder_sent(self, event_id: str) -> bool:
        return event_id in self.sent_reminders

    def mark_reminder_sent(self, event_id: str):
        self.sent_reminders.add(event_id)
        self.save()

    def is_in_progress(self, event_id: str) -> bool:
        return event_id in self.in_progress

    def set_in_progress(self, event_id: str, state: bool):
        if state:
            self.in_progress.add(event_id)
        else:
            if event_id in self.in_progress:
                self.in_progress.remove(event_id)

reminder_manager = ReminderManager()
