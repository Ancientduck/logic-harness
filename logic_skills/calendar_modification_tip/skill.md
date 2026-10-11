# Calendar Events

## Recurring Events via API
- Use Google Calendar API with `recurrence` field using iCalendar RRULE format.
- Example body:
```python
event = {
    'summary': 'Event Name',
    'start': {'dateTime': 'YYYY-MM-DDTHH:MM:SS+06:00', 'timeZone': 'Asia/Dhaka'},
    'end': {'dateTime': 'YYYY-MM-DDTHH:MM:SS+06:00', 'timeZone': 'Asia/Dhaka'},
    'recurrence': ['RRULE:FREQ=WEEKLY;BYDAY=MO,TU,SA,SU;UNTIL=YYYYMMDDTHHMMSSZ']
}
service.events().insert(calendarId='primary', body=event).execute()
```

## Add Single Events
- Script: `D:/Ai/logic/logic_tools/calendar_add_multiple_events.py`
- Args: single JSON string as sys.argv[1]
- Format: '["YYYY-MM-DD","HH:MM-HH:MM","Name","color_id"]'
- Uses relative cred path: `logic_tools/oauth_token_files/google_calendar_drive_oauth.json`

## Direct API Access
- Cred file: `D:/Ai/logic/logic_tools/oauth_token_files/google_calendar_drive_oauth.json`
- Load with json, use google.oauth2.credentials.Credentials with token, refresh_token, token_uri, client_id, client_secret, scopes fields.