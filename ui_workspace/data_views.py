"""Builds Gmail and Calendar views as tables for the workspace.

Only metadata is shown (sender, subject, date, read state; start, end, title, location).
Message bodies are never put in the table, so the workspace cannot render mail content.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas

from actions import calendar_control, email_control

MAIL_COLUMNS = ["from", "subject", "date", "unread", "id"]
AGENDA_COLUMNS = ["start", "title", "location", "id"]


def gmail_frame(query: str = "in:inbox", limit: int = 25) -> pandas.DataFrame:
    service = email_control._gmail_service()
    listing = service.users().messages().list(
        userId="me", q=query, maxResults=max(1, min(int(limit), 50)), includeSpamTrash=False
    ).execute()
    rows = []
    for item in listing.get("messages", []) or []:
        message = service.users().messages().get(
            userId="me", id=item["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = email_control._headers_map(message.get("payload", {}))
        labels = set(message.get("labelIds", []) or [])
        rows.append({
            "from": headers.get("from", "Unknown sender"),
            "subject": headers.get("subject", "(No subject)"),
            "date": headers.get("date", ""),
            "unread": "yes" if "UNREAD" in labels else "",
            "id": str(message.get("id", item["id"])),
        })
    return pandas.DataFrame(rows, columns=MAIL_COLUMNS)


def agenda_frame(days: int = 14, limit: int = 50) -> pandas.DataFrame:
    service = calendar_control._calendar_service()
    now = datetime.now().astimezone()
    result = service.events().list(
        calendarId=calendar_control._CALENDAR_ID,
        timeMin=now.isoformat(),
        timeMax=(now + timedelta(days=max(1, min(int(days), 60)))).isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=max(1, min(int(limit), 100)),
    ).execute()
    rows = []
    for event in result.get("items", []) or []:
        start = event.get("start", {})
        rows.append({
            "start": start.get("dateTime") or start.get("date") or "",
            "title": event.get("summary", "(No title)"),
            "location": event.get("location", ""),
            "id": str(event.get("id", "")),
        })
    return pandas.DataFrame(rows, columns=AGENDA_COLUMNS)


VIEWS = {
    "inbox": ("Inbox", lambda limit, days: gmail_frame("in:inbox", limit)),
    "unread": ("Unread mail", lambda limit, days: gmail_frame("in:inbox is:unread", limit)),
    "agenda": ("Calendar", lambda limit, days: agenda_frame(days, limit)),
}
