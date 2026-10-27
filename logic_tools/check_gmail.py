import os
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/gmail.modify'
]

def get_unread_gmail(mark_as_read=True):
    creds_path = r'D:/Ai/logic_backup_27-oct-26/logic_tools/oauth_token_files/google_calendar_drive_oauth.json'
    creds = None
    if os.path.exists(creds_path):
        creds = Credentials.from_authorized_user_file(creds_path, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                return [f"Token refresh failed: {e}"]
        else:
            return ["Credentials invalid or expired, and no refresh token available."]

    service = build('gmail', 'v1', credentials=creds)
    results = service.users().messages().list(userId='me', q='is:unread').execute()
    messages = results.get('messages', [])

    if not messages:
        return ["No unread messages found."]

    mail_list = []
    for msg in messages:
        msg_data = service.users().messages().get(userId='me', id=msg['id'], format='metadata', metadataHeaders=['From', 'Subject', 'Date']).execute()
        headers = msg_data.get('payload', {}).get('headers', [])
        
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
        sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown Sender')
        date = next((h['value'] for h in headers if h['name'] == 'Date'), 'Unknown Date')
        snippet = msg_data.get('snippet', '')

        mail_list.append(f"From: {sender}\nSubject: {subject}\nDate: {date}\nSnippet: {snippet}\n{'-'*40}")

        if mark_as_read:
            service.users().messages().modify(userId='me', id=msg['id'], body={'removeLabelIds': ['UNREAD']}).execute()

    return mail_list

if __name__ == '__main__':
    unread = get_unread_gmail()
    for mail in unread[:5]:
        print(mail)
