---
name: Calendar Management
description: Instructions for checking, creating, and deleting Google Calendar events.
---

# Calendar Management Skill

You have access to tools for managing a shared Google Calendar. Follow these strict rules when scheduling or checking events:

1. **Always Confirm Details**: When scheduling events, always confirm the time and duration.
2. **Missing Year**: If a year is not specified, assume the current year or the next occurrence of that date.
3. **MANDATORY Date Verification**: When resolving relative dates (like "next Tuesday", "tomorrow", or "next weekend"), you MUST ALWAYS use the `verify_date` tool to confirm that the chosen date string actually aligns with the requested day of the week. This prevents scheduling on the wrong day. Do this *before* scheduling the event.
4. **Event Editing**: When asked to edit an event, make sure you delete the original event and create a new one with the updated details. Do not attempt to modify the event in place.
