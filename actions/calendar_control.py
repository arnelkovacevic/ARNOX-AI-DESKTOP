"""Google Calendar access for ARNOX, using the same Google connection as Gmail."""

from __future__ import annotations

from datetime import datetime, timedelta

from actions.email_control import (
    GmailConnectionError,
    _bounded_limit,
    _gmail_api_error,
    _gmail_dependencies,
    _load_gmail_credentials,
)

_CALENDAR_ID = "primary"


def _calendar_service():
    *_, build = _gmail_dependencies()
    credentials = _load_gmail_credentials()
    try:
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)
    except Exception as exc:
        raise GmailConnectionError(f"Could not initialize Google Calendar: {exc}") from exc


def _event_time(value: str) -> dict:
    """Return a Google start/end object for an ISO date or datetime string.

    Naive datetimes are interpreted in the local timezone of this machine.
    """
    text = str(value).strip()
    if "T" not in text:
        return {"date": text}
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return {"dateTime": parsed.isoformat()}


def _event_end_default(start: str) -> str:
    text = str(start).strip()
    if "T" not in text:
        return (datetime.fromisoformat(text) + timedelta(days=1)).date().isoformat()
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return (parsed + timedelta(hours=1)).isoformat()


def _format_event(event: dict) -> str:
    start = event.get("start", {})
    when = start.get("dateTime") or start.get("date") or "unknown time"
    location = f" @ {event['location']}" if event.get("location") else ""
    return f"- [{event.get('id', '')}] {when}: {event.get('summary', '(No title)')}{location}"


def _agenda(days: int, limit: int) -> str:
    try:
        service = _calendar_service()
        now = datetime.now().astimezone()
        result = service.events().list(
            calendarId=_CALENDAR_ID,
            timeMin=now.isoformat(),
            timeMax=(now + timedelta(days=days)).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=limit,
        ).execute()
        events = result.get("items", []) or []
        if not events:
            return f"No calendar events in the next {days} day(s)."
        return f"Calendar events for the next {days} day(s):\n" + "\n".join(_format_event(e) for e in events)
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, "calendar listing")


def _create(params: dict) -> str:
    summary = str(params.get("summary", "")).strip()
    start = str(params.get("start", "")).strip()
    if not summary or not start:
        return "A title and a start time are required to create an event."
    end = str(params.get("end", "")).strip() or _event_end_default(start)
    body = {
        "summary": summary,
        "start": _event_time(start),
        "end": _event_time(end),
    }
    if params.get("location"):
        body["location"] = str(params["location"])
    if params.get("description"):
        body["description"] = str(params["description"])
    try:
        created = _calendar_service().events().insert(calendarId=_CALENDAR_ID, body=body).execute()
        return f"Event created: {_format_event(created)}"
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, "event creation")


def _update(params: dict) -> str:
    event_id = str(params.get("event_id", "")).strip()
    if not event_id:
        return "An event ID from the agenda is required."
    changes: dict = {}
    if params.get("summary"):
        changes["summary"] = str(params["summary"])
    if params.get("location"):
        changes["location"] = str(params["location"])
    if params.get("description"):
        changes["description"] = str(params["description"])
    if params.get("start"):
        changes["start"] = _event_time(str(params["start"]))
        end = str(params.get("end", "")).strip() or _event_end_default(str(params["start"]))
        changes["end"] = _event_time(end)
    elif params.get("end"):
        changes["end"] = _event_time(str(params["end"]))
    if not changes:
        return "Nothing to change. Give a new title, start, end, location, or description."
    try:
        updated = _calendar_service().events().patch(
            calendarId=_CALENDAR_ID, eventId=event_id, body=changes
        ).execute()
        return f"Event updated: {_format_event(updated)}"
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, "event update")


def _delete(params: dict) -> str:
    event_id = str(params.get("event_id", "")).strip()
    if not event_id:
        return "An event ID from the agenda is required."
    if params.get("confirmed") is not True:
        return "Deleting an event needs explicit confirmation. Ask the user first, then call again with confirmed=true."
    try:
        _calendar_service().events().delete(calendarId=_CALENDAR_ID, eventId=event_id).execute()
        return "Event deleted from Google Calendar."
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, "event deletion")


def calendar_control(parameters: dict | None = None, response=None, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = str(params.get("action", "agenda")).lower().strip().replace("-", "_").replace(" ", "_")
    aliases = {"list": "agenda", "events": "agenda", "today": "agenda", "add": "create", "new": "create", "edit": "update", "remove": "delete"}
    action = aliases.get(action, action)
    if player:
        player.write_log(f"[Calendar] {action}")
    if action == "agenda":
        days = max(1, min(int(params.get("days", 7) or 7), 60))
        return _agenda(days, _bounded_limit(params.get("limit", 20), default=20))
    if action == "create":
        return _create(params)
    if action == "update":
        return _update(params)
    if action == "delete":
        return _delete(params)
    return "Unknown calendar action. Available: agenda, create, update, delete."
