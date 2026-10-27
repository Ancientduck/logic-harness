# Calendar Modification Tips & Guidelines

## Color Palette IDs (Google Calendar)
- `1`: Lavender | `2`: Sage | `3`: Grape | `4`: Flamingo
- `5`: Banana (Yellow) - Daily classes / routine sessions
- `6`: Tangerine (Orange) - Monthly / special revision tests
- `7`: Peacock (Light Blue) | `8`: Graphite (Gray)
- `9`: Blueberry (Dark Blue) - Weekly / major offline exams
- `10`: Basil (Dark Green) | `11`: Tomato (Red) - Holidays, critical alerts

## Direct API Setup
```python
import json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

creds_path = "D:/Ai/logic/logic_tools/oauth_token_files/google_calendar_drive_oauth.json"
with open(creds_path, 'r', encoding='utf-8') as f:
    token_data = json.load(f)

creds = Credentials(
    token=token_data.get('token'),
    refresh_token=token_data.get('refresh_token'),
    token_uri=token_data.get('token_uri', 'https://oauth2.googleapis.com/token'),
    client_id=token_data.get('client_id'),
    client_secret=token_data.get('client_secret'),
    scopes=token_data.get('scopes')
)
service = build('calendar', 'v3', credentials=creds)
```

## Modification Operations

### 1. Query / List Events
```python
events = service.events().list(
    calendarId='primary',
    timeMin='2026-10-26T00:00:00+06:00',
    timeMax='2026-11-17T23:59:59+06:00',
    singleEvents=True,
    orderBy='startTime'
).execute().get('items', [])
```

### 2. Delete / Batch Remove
- Filter by `summary`, `description`, or date range, then delete by ID:
```python
for ev in events:
    if 'keyword' in ev.get('summary', '').lower():
        service.events().delete(calendarId='primary', eventId=ev['id']).execute()
```

### 3. Update / Patch Existing Event
- Use `patch` for partial edits (color, summary, description):
```python
service.events().patch(
    calendarId='primary',
    eventId=event_id,
    body={'summary': 'New Title', 'colorId': '9'}
).execute()
```

### 4. Move / Reschedule Event
- Modify start and end datetimes directly via `patch`:
```python
service.events().patch(
    calendarId='primary',
    eventId=event_id,
    body={
        'start': {'dateTime': '2026-11-01T14:30:00+06:00', 'timeZone': 'Asia/Dhaka'},
        'end': {'dateTime': '2026-11-01T16:00:00+06:00', 'timeZone': 'Asia/Dhaka'}
    }
).execute()
```

### 5. Replace Schedule Block (Delete & Insert)
- Clean matching target window first, then batch insert the new schedule.

## Apurbo's Routine Conventions
- Classes first, exams second: `[Class Code] Class | Daily Exam: [Exam Code]`.
- Shorthand codes: `P` = Physics, `C` = Chemistry, `M` = Math, `E` = English.
- Descriptions: Full chapter titles and numbers in Bengali.
- Track distinction: Check Engineering vs Varsity Ka/Kha vs Medical before modifying.
- Default Timeslots:
  - Daily classes/exams: `13:45:00` - `16:15:00` (Banana / 5)
  - Weekly exams: `14:30:00` - `16:00:00` (Blueberry / 9)
  - Monthly revision: `11:00:00` - `13:00:00` (Tangerine / 6)
