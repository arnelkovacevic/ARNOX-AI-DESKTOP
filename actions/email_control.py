"""Read Gmail and prepare approval-gated email in a visible compose window.

Gmail reading uses Google's desktop OAuth flow. Refresh credentials are stored
only in the operating-system keychain, never in a token file. New Gmail drafts
are typed into ARNOX's controlled browser and sent only after a later explicit
approval. Pending drafts live in memory only.
"""

from __future__ import annotations

import base64
import asyncio
import html
import json
import os
import platform
import re
import subprocess
import tempfile
import threading
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path
from urllib.parse import quote, urlencode


_SYSTEM = platform.system()
_FIELD_SEPARATOR = chr(31)
_RECORD_SEPARATOR = chr(30)
_PENDING_LOCK = threading.RLock()
_PENDING_EMAIL: dict[str, str] = {}
_GMAIL_CREDENTIALS_CACHE = ""
_GMAIL_ACCOUNT_CACHE = ""
_TENANT_PENDING_EMAILS: dict[str, dict[str, str]] = {}
_TENANT_GMAIL_CREDENTIALS: dict[str, str] = {}
_TENANT_GMAIL_ACCOUNTS: dict[str, str] = {}

GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
)
_GMAIL_TOKEN_KEY = "gmail_oauth_credentials"
_GMAIL_ACCOUNT_KEY = "gmail_account_email"
_GMAIL_CLIENT_PATH_KEY = "gmail_oauth_client_path"


class GmailConnectionError(RuntimeError):
    pass


def _secret_store():
    from core.secret_store import get_secret_store

    return get_secret_store()


def _gmail_dependencies():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        return Request, Credentials, InstalledAppFlow, build
    except ImportError as exc:
        raise GmailConnectionError(
            "Gmail support is not installed. Run: pip install google-api-python-client "
            "google-auth-httplib2 google-auth-oauthlib"
        ) from exc


def _discover_gmail_client_path(explicit_path: str = "") -> Path | None:
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path).expanduser())
    env_path = os.environ.get("GMAIL_OAUTH_CLIENT_FILE", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())
    try:
        saved = _secret_store().get(_GMAIL_CLIENT_PATH_KEY)
        if saved:
            candidates.append(Path(saved).expanduser())
    except Exception:
        pass

    project = Path(__file__).resolve().parent.parent
    candidates.extend([
        project / "config" / "gmail_credentials.json",
        project / "config" / "credentials.json",
        Path.home() / "Downloads" / "gmail_credentials.json",
        Path.home() / "Downloads" / "credentials.json",
    ])
    downloads = Path.home() / "Downloads"
    if downloads.exists():
        try:
            candidates.extend(sorted(
                downloads.glob("client_secret_*.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )[:5])
        except OSError:
            pass

    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        if _valid_gmail_client_file(resolved):
            return resolved
    return None


def _valid_gmail_client_file(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        installed = data.get("installed") if isinstance(data, dict) else None
        return bool(
            isinstance(installed, dict)
            and installed.get("client_id")
            and installed.get("auth_uri")
            and installed.get("token_uri")
        )
    except (OSError, ValueError, TypeError):
        return False


def _save_gmail_credentials(credentials, account: str = "", client_path: str = "") -> bool:
    global _GMAIL_CREDENTIALS_CACHE, _GMAIL_ACCOUNT_CACHE
    payload = credentials.to_json()
    from core.tenant import get_current_user_id

    user_id = get_current_user_id()
    if user_id:
        _TENANT_GMAIL_CREDENTIALS[user_id] = payload
        if account:
            _TENANT_GMAIL_ACCOUNTS[user_id] = account
    else:
        _GMAIL_CREDENTIALS_CACHE = payload
        if account:
            _GMAIL_ACCOUNT_CACHE = account
    persisted = False
    try:
        store = _secret_store()
        store.set(_GMAIL_TOKEN_KEY, payload)
        if account:
            store.set(_GMAIL_ACCOUNT_KEY, account)
        if client_path:
            store.set(_GMAIL_CLIENT_PATH_KEY, client_path)
        persisted = store.get(_GMAIL_TOKEN_KEY) == payload
    except Exception:
        persisted = False
    return persisted


def _load_gmail_credentials():
    Request, Credentials, _InstalledAppFlow, _build = _gmail_dependencies()
    from core.tenant import get_current_user_id

    user_id = get_current_user_id()
    payload = _TENANT_GMAIL_CREDENTIALS.get(user_id, "") if user_id else _GMAIL_CREDENTIALS_CACHE
    if not payload:
        try:
            payload = _secret_store().get(_GMAIL_TOKEN_KEY) or ""
        except Exception:
            payload = ""
    if not payload:
        raise GmailConnectionError(
            "Gmail is not connected. Download a Desktop app OAuth credentials JSON file, "
            "then tell ARNOX: connect Gmail using this file."
        )
    try:
        credentials = Credentials.from_authorized_user_info(json.loads(payload), list(GMAIL_SCOPES))
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            _save_gmail_credentials(credentials)
        if not credentials.valid:
            raise GmailConnectionError("The Gmail authorization has expired. Connect Gmail again.")
        return credentials
    except GmailConnectionError:
        raise
    except Exception as exc:
        message = str(exc).lower()
        if "invalid_grant" in message or "revoked" in message:
            _clear_gmail_credentials()
            raise GmailConnectionError("Google revoked or expired the Gmail connection. Connect Gmail again.") from exc
        raise GmailConnectionError(f"Could not restore the Gmail connection: {exc}") from exc


def _gmail_service():
    _Request, _Credentials, _InstalledAppFlow, build = _gmail_dependencies()
    credentials = _load_gmail_credentials()
    try:
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)
    except Exception as exc:
        raise GmailConnectionError(f"Could not initialize Gmail: {exc}") from exc


def _clear_gmail_credentials() -> None:
    global _GMAIL_CREDENTIALS_CACHE, _GMAIL_ACCOUNT_CACHE
    from core.tenant import get_current_user_id

    user_id = get_current_user_id()
    if user_id:
        _TENANT_GMAIL_CREDENTIALS.pop(user_id, None)
        _TENANT_GMAIL_ACCOUNTS.pop(user_id, None)
    else:
        _GMAIL_CREDENTIALS_CACHE = ""
        _GMAIL_ACCOUNT_CACHE = ""
    try:
        store = _secret_store()
        store.delete(_GMAIL_TOKEN_KEY)
        store.delete(_GMAIL_ACCOUNT_KEY)
        store.delete(_GMAIL_CLIENT_PATH_KEY)
    except Exception:
        pass


def _gmail_connect(credentials_path: str = "") -> str:
    try:
        _Request, _Credentials, InstalledAppFlow, build = _gmail_dependencies()
    except GmailConnectionError as exc:
        return str(exc)
    path = _discover_gmail_client_path(credentials_path)
    if path is None:
        return (
            "Gmail needs a Google Desktop OAuth credentials JSON file. In Google Cloud: enable the Gmail API, "
            "configure the OAuth consent screen, create an OAuth Client ID with application type Desktop app, "
            "download the JSON, then drag it into ARNOX and say: connect Gmail using this file."
        )
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(path), list(GMAIL_SCOPES))
        credentials = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            open_browser=True,
            timeout_seconds=240,
            access_type="offline",
            prompt="consent",
            success_message="Gmail is connected to ARNOX. You can close this browser tab.",
        )
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()
        account = str(profile.get("emailAddress", "your Google account"))
        persisted = _save_gmail_credentials(credentials, account, str(path))
        persistence = "The refresh credential is secured in your system keychain." if persisted else (
            "The connection is active for this session, but the system keychain was unavailable."
        )
        return f"Gmail connected: {account}. {persistence}"
    except Exception as exc:
        text = str(exc)
        if "access_denied" in text.lower():
            return "Gmail connection was cancelled before permission was granted."
        return f"Could not connect Gmail: {text}"


def _gmail_account() -> str:
    from core.tenant import get_current_user_id

    user_id = get_current_user_id()
    cached = _TENANT_GMAIL_ACCOUNTS.get(user_id, "") if user_id else _GMAIL_ACCOUNT_CACHE
    if cached:
        return cached
    try:
        return _secret_store().get(_GMAIL_ACCOUNT_KEY) or ""
    except Exception:
        return ""


def _gmail_status() -> str:
    try:
        service = _gmail_service()
        profile = service.users().getProfile(userId="me").execute()
        account = str(profile.get("emailAddress", "connected account"))
        return f"Gmail is connected as {account}."
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, "status check")


def _gmail_api_error(exc: Exception, operation: str) -> str:
    status = getattr(getattr(exc, "resp", None), "status", None)
    text = str(exc)
    if status == 401 or "invalid_grant" in text.lower():
        _clear_gmail_credentials()
        return "The Gmail connection expired or was revoked. Connect Gmail again."
    if status == 403:
        return (
            f"Gmail {operation} was denied. Confirm the Gmail API and Google Calendar API are enabled, "
            "and connect Gmail again so ARNOX can request the newer permissions "
            "(gmail.modify and calendar.events)."
        )
    if status == 429:
        return "Gmail is temporarily rate-limiting requests. Try again shortly."
    return f"Gmail {operation} failed: {text}"


def _decode_header_value(value: str) -> str:
    try:
        return str(make_header(decode_header(str(value or ""))))
    except Exception:
        return str(value or "")


def _headers_map(payload: dict) -> dict[str, str]:
    result = {}
    for item in payload.get("headers", []) or []:
        name = str(item.get("name", "")).lower()
        if name:
            result[name] = _decode_header_value(item.get("value", ""))
    return result


def _decode_gmail_data(value: str) -> str:
    if not value:
        return ""
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _html_to_text(value: str) -> str:
    try:
        from bs4 import BeautifulSoup

        return BeautifulSoup(value, "html.parser").get_text("\n", strip=True)
    except Exception:
        clean = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", value)
        clean = re.sub(r"(?s)<[^>]+>", " ", clean)
        return html.unescape(re.sub(r"\s+", " ", clean)).strip()


def _gmail_body(payload: dict) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict) -> None:
        mime_type = str(part.get("mimeType", "")).lower()
        data = str((part.get("body") or {}).get("data", ""))
        if data and mime_type == "text/plain":
            plain_parts.append(_decode_gmail_data(data))
        elif data and mime_type == "text/html":
            html_parts.append(_decode_gmail_data(data))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload or {})
    body = "\n".join(part.strip() for part in plain_parts if part.strip())
    if body:
        return body
    return _html_to_text("\n".join(html_parts)) if html_parts else ""


def _gmail_list(query: str, limit: int, heading: str) -> str:
    try:
        service = _gmail_service()
        result = service.users().messages().list(
            userId="me", q=query, maxResults=limit, includeSpamTrash=False
        ).execute()
        rows = []
        for item in result.get("messages", []) or []:
            message = service.users().messages().get(
                userId="me",
                id=item["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = _headers_map(message.get("payload", {}))
            labels = set(message.get("labelIds", []) or [])
            rows.append({
                "id": str(message.get("id", item["id"])),
                "sender": headers.get("from", "Unknown sender"),
                "subject": headers.get("subject", "(No subject)"),
                "date": headers.get("date", "Unknown date"),
                "read": "UNREAD" not in labels,
            })
        return _format_rows(rows, heading)
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, "message listing")


def _gmail_read(message_id: str, max_chars: int = 12000) -> str:
    try:
        service = _gmail_service()
        message = service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()
        headers = _headers_map(message.get("payload", {}))
        body = _gmail_body(message.get("payload", {})) or str(message.get("snippet", ""))
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "\n\n[Message truncated]"
        global _LAST_READ_MESSAGE_ID
        _LAST_READ_MESSAGE_ID = message_id
        attachments = _attachment_parts(message.get("payload", {}))
        attachment_note = (
            "\n\nAttachments (use action=attachment_read to read one):\n" + _format_attachment_list(attachments)
            if attachments else ""
        )
        return (
            f"Subject: {headers.get('subject', '(No subject)')}\n"
            f"From: {headers.get('from', 'Unknown sender')}\n"
            f"To: {headers.get('to', _gmail_account() or 'Unknown recipient')}\n"
            f"Received: {headers.get('date', 'Unknown date')}\n\n{body}{attachment_note}"
        )
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, "message reading")


def _gmail_send_core(draft: dict[str, str]) -> tuple[bool, str]:
    """Send one message through the Gmail API. A reply keeps its thread when thread_id is given."""
    try:
        service = _gmail_service()
        message = EmailMessage()
        message["To"] = draft["to"]
        if draft.get("cc"):
            message["Cc"] = draft["cc"]
        if draft.get("bcc"):
            message["Bcc"] = draft["bcc"]
        message["Subject"] = draft["subject"]
        if draft.get("in_reply_to"):
            message["In-Reply-To"] = draft["in_reply_to"]
            message["References"] = (draft.get("references", "") + " " + draft["in_reply_to"]).strip()
        message.set_content(draft["body"])
        for item in draft.get("attachments") or []:
            mime = str(item.get("mime") or "application/octet-stream")
            maintype, _, subtype = mime.partition("/")
            message.add_attachment(item["data"], maintype=maintype or "application",
                                   subtype=subtype or "octet-stream", filename=item["filename"])
        body = {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")}
        if draft.get("thread_id"):
            body["threadId"] = draft["thread_id"]
        result = service.users().messages().send(userId="me", body=body).execute()
        if not result.get("id"):
            return False, "Gmail did not confirm that the email was sent."
        return True, f"Email sent through Gmail to {draft['to']}."
    except GmailConnectionError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, _gmail_api_error(exc, "sending")


def _send_gmail(draft: dict[str, str]) -> str:
    draft = dict(draft)
    if not str(draft.get("to", "")).strip():
        return "The draft has no recipient yet. Tell me who to send it to, or fill in the To field."
    if draft.get("attachments") and not all("data" in item for item in draft["attachments"]):
        try:
            draft["attachments"] = _collect_forwarded(draft["attachments"])
        except Exception as exc:
            return f"Could not attach the files: {exc}"
    ok, message = _gmail_send_core(draft)
    if ok:
        _clear_pending_email()
        return message
    return message + " The pending draft is still available." if "confirm" in message else message


def fetch_attachment_bytes(message_id: str, attachment_id: str, inline_data: str = "") -> bytes:
    """Download one attachment's bytes from Gmail (or decode the inline copy)."""
    if inline_data:
        data = inline_data
    else:
        service = _gmail_service()
        data = service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id
        ).execute().get("data", "")
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"An attachment is larger than the {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB limit.")
    return raw


def _collect_forwarded(attachments: list[dict]) -> list[dict]:
    """Download the attachments being forwarded. Executables are refused, like everywhere else."""
    collected = []
    total = 0
    for item in attachments or []:
        name = _safe_filename(item.get("filename", "attachment"))
        if Path(name).suffix.lower().lstrip(".") in _BLOCKED_ATTACHMENT_EXTS:
            raise ValueError(f"{name} is an executable or installer type and will not be forwarded.")
        data = fetch_attachment_bytes(str(item.get("message_id", "")), str(item.get("attachment_id", "")),
                                      str(item.get("inline_data", "")))
        total += len(data)
        if total > MAX_ATTACHMENT_BYTES:
            raise ValueError("The attachments together are over the 25 MB limit.")
        collected.append({"filename": name, "mime": item.get("mime", "application/octet-stream"), "data": data})
    return collected


def send_mail_now(to: str, subject: str, body: str, cc: str = "", thread_id: str = "",
                  in_reply_to: str = "", references: str = "", attachments: list | None = None) -> tuple[bool, str]:
    """Send from the workspace compose window. The user pressing Send is the explicit approval."""
    recipients = _normalize_recipients(to)
    if not recipients:
        return False, "A valid recipient email address is required."
    body = str(body or "").strip()
    if not body:
        return False, "The message is empty."
    subject = re.sub(r"[\r\n]+", " ", str(subject or "")).strip() or "(No subject)"
    try:
        files = _collect_forwarded(attachments or [])
    except Exception as exc:
        return False, f"Could not attach the files: {exc}"
    return _gmail_send_core({
        "to": recipients, "cc": _normalize_recipients(cc), "subject": subject, "body": body,
        "thread_id": thread_id, "in_reply_to": in_reply_to, "references": references, "attachments": files,
    })


def set_trash(message_id: str, trash: bool) -> tuple[bool, str]:
    """Move a message to the trash or back to where it was. Reversible, never permanent."""
    try:
        service = _gmail_service()
        messages = service.users().messages()
        if trash:
            messages.trash(userId="me", id=message_id).execute()
            return True, "Moved to the trash. You can restore it."
        messages.untrash(userId="me", id=message_id).execute()
        # Gmail's untrash only removes the TRASH label; the mail would otherwise sit in "All Mail".
        messages.modify(userId="me", id=message_id, body={"addLabelIds": ["INBOX"], "removeLabelIds": []}).execute()
        return True, "Restored to your inbox."
    except GmailConnectionError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, _gmail_api_error(exc, "trash update")


async def _first_visible(page, selectors: tuple[str, ...], timeout_seconds: float = 20.0):
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                for index in range(count - 1, -1, -1):
                    candidate = locator.nth(index)
                    if await candidate.is_visible():
                        return candidate
            except Exception:
                continue
        await asyncio.sleep(0.25)
    return None


def _gmail_login_progress(player, message: str) -> None:
    if not player:
        return
    try:
        player.write_log(f"[Gmail Login] {message}")
    except Exception:
        pass
    try:
        player.show_subtitle(f"GMAIL // {message.upper()}")
    except Exception:
        pass


async def _assist_gmail_sign_in(page, player=None, timeout_seconds: float = 90.0) -> bool:
    """Select the connected account, then wait for Google's password/2FA step.

    ARNOX never reads or stores a Google password. The persistent browser profile
    retains the resulting Google session after the user completes sign-in once.
    """
    account = _gmail_account().strip()
    _gmail_login_progress(player, "Sign-in required")

    # Gmail's public landing page sometimes appears before the account chooser.
    for locator in (
        page.get_by_role("link", name=re.compile(r"^sign in$", re.I)),
        page.get_by_role("button", name=re.compile(r"^sign in$", re.I)),
        page.get_by_text("Sign in", exact=True),
    ):
        try:
            if await locator.count() and await locator.first.is_visible():
                await locator.first.click(timeout=3_000)
                await asyncio.sleep(0.8)
                break
        except Exception:
            continue

    if account:
        # Reuse an account tile when Google already knows this address.
        try:
            account_tile = page.get_by_text(account, exact=False)
            if await account_tile.count() and await account_tile.first.is_visible():
                await account_tile.first.click(timeout=3_000)
                await asyncio.sleep(0.6)
        except Exception:
            pass

        # Otherwise enter only the known account address. Password and 2FA stay
        # exclusively between the user and Google.
        email_field = await _first_visible(page, (
            'input[type="email"]',
            'input[name="identifier"]',
            'input[autocomplete="username"]',
        ), timeout_seconds=4.0)
        if email_field is not None:
            try:
                await email_field.fill(account)
                next_button = await _first_visible(page, (
                    '#identifierNext button',
                    '#identifierNext',
                    'button:has-text("Next")',
                ), timeout_seconds=3.0)
                if next_button is not None:
                    await next_button.click()
                    await asyncio.sleep(0.8)
            except Exception:
                pass

    _gmail_login_progress(
        player,
        "Complete Google password or 2FA; compose will resume",
    )
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if "mail.google.com" in str(page.url or "").lower():
            _gmail_login_progress(player, "Signed in; resuming compose")
            return True
        await asyncio.sleep(0.5)
    return False


async def _open_gmail_compose_fields(session, player=None):
    page = await session._get_page()
    await page.bring_to_front()
    compose_url = "https://mail.google.com/mail/u/0/?view=cm&fs=1&tf=1"
    try:
        await page.goto(compose_url, wait_until="domcontentloaded", timeout=45_000)
    except Exception:
        pass
    await page.bring_to_front()
    await asyncio.sleep(1.0)

    current_url = str(page.url or "").lower()
    signed_out = "accounts.google.com" in current_url
    if not signed_out:
        try:
            page_text = (await page.locator("body").inner_text())[:800].lower()
            signed_out = "sign in" in page_text and "gmail" in page_text and "inbox" not in page_text
        except Exception:
            pass
    if signed_out:
        if not await _assist_gmail_sign_in(page, player=player):
            return page, None, (
                "Gmail is waiting for Google sign-in. Complete the password or 2FA step in the open "
                "ARNOX Chrome window, then ask me to prepare the email again. ARNOX never stores your password."
            )
        try:
            await page.goto(compose_url, wait_until="domcontentloaded", timeout=45_000)
        except Exception:
            pass
        await asyncio.sleep(0.6)

    subject = await _first_visible(page, (
        'input[name="subjectbox"]',
        'input[aria-label="Subject"]',
        'input[placeholder="Subject"]',
    ), timeout_seconds=12.0)
    body = await _first_visible(page, (
        'div[aria-label="Message Body"][contenteditable="true"]',
        'div[role="textbox"][aria-label*="Message Body" i]',
        'div[contenteditable="true"][role="textbox"]',
    ), timeout_seconds=3.0)

    if subject is None or body is None:
        for locator in (
            page.get_by_role("button", name=re.compile(r"^compose$", re.I)),
            page.locator('[gh="cm"]'),
            page.get_by_text("Compose", exact=True),
        ):
            try:
                if await locator.count() and await locator.first.is_visible():
                    await locator.first.click(timeout=4_000)
                    break
            except Exception:
                continue
        subject = await _first_visible(page, (
            'input[name="subjectbox"]',
            'input[aria-label="Subject"]',
            'input[placeholder="Subject"]',
        ), timeout_seconds=15.0)
        body = await _first_visible(page, (
            'div[aria-label="Message Body"][contenteditable="true"]',
            'div[role="textbox"][aria-label*="Message Body" i]',
            'div[contenteditable="true"][role="textbox"]',
        ), timeout_seconds=5.0)

    if subject is None or body is None:
        page_text = ""
        try:
            page_text = (await page.locator("body").inner_text())[:500]
        except Exception:
            pass
        if "sign in" in page_text.lower():
            return page, None, "Gmail requires a browser sign-in. Sign in, then ask me to prepare the email again."
        return page, None, "Gmail opened, but ARNOX could not find a new compose window. Open Gmail once, finish any sign-in prompts, and try again."

    to_field = await _first_visible(page, (
        'input[aria-label^="To recipients" i]',
        'input[aria-label="To"]',
        'input[name="to"]',
        'textarea[name="to"]',
        'input[role="combobox"][aria-autocomplete="list"]',
    ), timeout_seconds=8.0)
    if to_field is None:
        return page, None, "Gmail opened a compose window, but ARNOX could not locate the recipient field."
    return page, {"to": to_field, "subject": subject, "body": body}, ""


async def _type_visible(locator, text: str, delay_ms: int = 3) -> None:
    await locator.click()
    try:
        await locator.fill("")
    except Exception:
        try:
            await locator.press("Meta+A" if _SYSTEM == "Darwin" else "Control+A")
            await locator.press("Backspace")
        except Exception:
            pass
    if text:
        try:
            await locator.press_sequentially(text, delay=delay_ms)
        except AttributeError:
            await locator.type(text, delay=delay_ms)


def _compose_progress(player, field: str, value: str) -> None:
    if not player:
        return
    preview = value if len(value) <= 90 else value[:87].rstrip() + "..."
    try:
        player.write_log(f"[Gmail Compose] {field}: {preview}")
    except Exception:
        pass
    try:
        player.show_subtitle(f"GMAIL // TYPING {field.upper()}")
    except Exception:
        pass


async def _gmail_visible_recipient_text(page) -> str:
    visible_data: list[str] = []
    try:
        visible_data.append(await page.locator("body").inner_text())
    except Exception:
        pass
    for selector in (
        '[email]',
        '[data-hovercard-id]',
        'input[name="to"], textarea[name="to"]',
        'input[name="cc"], textarea[name="cc"]',
        'input[name="bcc"], textarea[name="bcc"]',
    ):
        try:
            locator = page.locator(selector)
            for index in range(await locator.count()):
                candidate = locator.nth(index)
                for attribute in ("email", "data-hovercard-id", "value", "aria-label", "title"):
                    value = await candidate.get_attribute(attribute)
                    if value:
                        visible_data.append(value)
        except Exception:
            continue
    return "\n".join(visible_data)


async def _gmail_web_prepare_once(session, draft: dict[str, str], player=None) -> str:
    page, fields, error = await _open_gmail_compose_fields(session, player=player)
    if error:
        return error

    recipients = [item.strip() for item in draft["to"].split(",") if item.strip()]
    _compose_progress(player, "To", draft["to"])
    await fields["to"].click()
    for recipient in recipients:
        try:
            await fields["to"].press_sequentially(recipient, delay=4)
        except AttributeError:
            await fields["to"].type(recipient, delay=4)
        await fields["to"].press("Enter")

    if draft.get("cc") or draft.get("bcc"):
        for locator in (
            page.locator('[aria-label*="Add Cc" i]'),
            page.locator('[aria-label*="Add Bcc" i]'),
            page.get_by_text("Cc", exact=True),
            page.get_by_text("Bcc", exact=True),
        ):
            try:
                if await locator.count() and await locator.last.is_visible():
                    await locator.last.click(timeout=3_000)
                    break
            except Exception:
                continue

    for field_name, selectors in (
        ("Cc", ('input[name="cc"]', 'textarea[name="cc"]', 'input[aria-label^="Cc recipients" i]')),
        ("Bcc", ('input[name="bcc"]', 'textarea[name="bcc"]', 'input[aria-label^="Bcc recipients" i]')),
    ):
        key = field_name.lower()
        if not draft.get(key):
            continue
        locator = await _first_visible(page, selectors, timeout_seconds=4.0)
        if locator is None:
            return f"Gmail opened the draft, but ARNOX could not locate the {field_name} field. Nothing was sent."
        _compose_progress(player, field_name, draft[key])
        for recipient in [item.strip() for item in draft[key].split(",") if item.strip()]:
            try:
                await locator.press_sequentially(recipient, delay=4)
            except AttributeError:
                await locator.type(recipient, delay=4)
            await locator.press("Enter")

    _compose_progress(player, "Subject", draft["subject"])
    await _type_visible(fields["subject"], draft["subject"], delay_ms=3)
    _compose_progress(player, "Body", draft["body"])
    await _type_visible(fields["body"], draft["body"], delay_ms=1)

    # Verify the exact text. If a key event was dropped, finish the field with a
    # direct fill instead of abandoning a half-written draft.
    visible_subject = await fields["subject"].input_value()
    visible_body = await fields["body"].inner_text()
    if _normalized_visible_text(visible_subject) != _normalized_visible_text(draft["subject"]):
        await fields["subject"].fill(draft["subject"])
    if _normalized_visible_text(visible_body) != _normalized_visible_text(draft["body"]):
        await fields["body"].fill(draft["body"])
    visible_subject = await fields["subject"].input_value()
    visible_body = await fields["body"].inner_text()
    if _normalized_visible_text(visible_subject) != _normalized_visible_text(draft["subject"]):
        raise RuntimeError("Gmail did not retain the complete subject")
    if _normalized_visible_text(visible_body) != _normalized_visible_text(draft["body"]):
        raise RuntimeError("Gmail did not retain the complete message body")
    recipient_text = await _gmail_visible_recipient_text(page)
    expected_recipients = [
        item.strip()
        for item in (draft["to"] + "," + draft.get("cc", "") + "," + draft.get("bcc", "")).split(",")
        if item.strip()
    ]
    for address in expected_recipients:
        if address.lower() not in recipient_text.lower():
            raise RuntimeError(f"Gmail did not retain recipient {address}")
    await page.bring_to_front()
    _gmail_login_progress(player, "Draft complete; awaiting approval")
    return "GMAIL_WEB_DRAFT_READY"


async def _gmail_web_prepare_async(session, draft: dict[str, str], player=None) -> str:
    last_error = ""
    for attempt in range(2):
        try:
            return await _gmail_web_prepare_once(session, draft, player=player)
        except Exception as exc:
            last_error = str(exc)
            _gmail_login_progress(player, "Compose interrupted; recovering")
            try:
                await _gmail_web_cancel_async(session)
            except Exception:
                pass
            if attempt == 0:
                await asyncio.sleep(0.4)
    return (
        "Gmail interrupted the compose operation twice, so ARNOX discarded the incomplete draft. "
        f"Nothing was sent. Details: {last_error}"
    )


def _gmail_web_prepare(draft: dict[str, str], player=None, browser: str = "chrome") -> str:
    try:
        from actions.browser_control import _registry

        session = _registry.get(browser or "chrome")
        return session.run(_gmail_web_prepare_async(session, draft, player), timeout=210)
    except Exception as exc:
        return f"Could not open the Gmail compose window: {exc}"


def _normalized_visible_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


async def _gmail_web_send_async(session, draft: dict[str, str]) -> str:
    page = await session._get_page()
    await page.bring_to_front()
    if "mail.google.com" not in str(page.url or "").lower():
        return "The Gmail draft is no longer the active ARNOX browser tab. Nothing was sent."

    subject = await _first_visible(page, (
        'input[name="subjectbox"]', 'input[aria-label="Subject"]', 'input[placeholder="Subject"]'
    ), timeout_seconds=4.0)
    body = await _first_visible(page, (
        'div[aria-label="Message Body"][contenteditable="true"]',
        'div[role="textbox"][aria-label*="Message Body" i]',
    ), timeout_seconds=4.0)
    if subject is None or body is None:
        return "The approved Gmail compose window is no longer available. Nothing was sent."

    visible_subject = await subject.input_value()
    visible_body = await body.inner_text()
    if _normalized_visible_text(visible_subject) != _normalized_visible_text(draft["subject"]):
        return "The visible Gmail subject changed after approval was requested. Nothing was sent. Prepare the email again."
    if _normalized_visible_text(visible_body) != _normalized_visible_text(draft["body"]):
        return "The visible Gmail body changed after approval was requested. Nothing was sent. Prepare the email again."

    compose_text = await _gmail_visible_recipient_text(page)
    for address in [item.strip() for item in (draft["to"] + "," + draft.get("cc", "") + "," + draft.get("bcc", "")).split(",") if item.strip()]:
        if address.lower() not in compose_text.lower():
            return f"The visible Gmail draft no longer contains {address}. Nothing was sent. Prepare the email again."

    send_button = await _first_visible(page, (
        'div[role="button"][aria-label^="Send" i]',
        'div[role="button"][data-tooltip^="Send" i]',
        'button[aria-label^="Send" i]',
    ), timeout_seconds=5.0)
    if send_button is None:
        return "ARNOX could not find Gmail's Send button. Nothing was sent."
    await send_button.click()
    try:
        await page.get_by_text(re.compile(r"message sent", re.I)).last.wait_for(state="visible", timeout=8_000)
    except Exception:
        await asyncio.sleep(1.0)
        try:
            button_still_visible = await send_button.is_visible()
        except Exception:
            button_still_visible = False
        if button_still_visible:
            return "Gmail did not confirm the send. The pending approval remains active."
    return "GMAIL_WEB_SENT"


def _gmail_web_send(draft: dict[str, str]) -> str:
    try:
        from actions.browser_control import _registry

        session = _registry.get(draft.get("browser", "chrome"))
        result = session.run(_gmail_web_send_async(session, draft), timeout=30)
        if result == "GMAIL_WEB_SENT":
            _clear_pending_email()
            return f"Email sent through the visible Gmail compose window to {draft['to']}."
        return result
    except Exception as exc:
        return f"Could not send the visible Gmail draft: {exc}"


async def _gmail_web_cancel_async(session) -> bool:
    page = await session._get_page()
    if "mail.google.com" not in str(page.url or "").lower():
        return False
    discard = await _first_visible(page, (
        '[aria-label*="Discard draft" i]',
        '[data-tooltip*="Discard draft" i]',
        '[aria-label="Discard"]',
    ), timeout_seconds=3.0)
    if discard is None:
        return False
    await discard.click()
    return True


def _gmail_web_cancel(draft: dict[str, str]) -> bool:
    try:
        from actions.browser_control import _registry

        session = _registry.get(draft.get("browser", "chrome"))
        return bool(session.run(_gmail_web_cancel_async(session), timeout=15))
    except Exception:
        return False


def _run_osascript(script: str, *arguments: str) -> subprocess.CompletedProcess:
    command = ["osascript", "-e", script, *arguments]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception as exc:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=str(exc))


def _mail_error(result: subprocess.CompletedProcess, operation: str) -> str:
    detail = (result.stderr or result.stdout or "Apple Mail did not respond").strip()
    if "not authorized" in detail.lower() or "not permitted" in detail.lower():
        return (
            f"Email {operation} needs macOS Automation permission. "
            "Allow ARNOX or Terminal to control Mail in System Settings > Privacy & Security > Automation."
        )
    return f"Email {operation} failed: {detail}"


def _normalize_recipients(value) -> str:
    if isinstance(value, (list, tuple)):
        raw = ", ".join(str(item) for item in value)
    else:
        raw = str(value or "")
    addresses = []
    for _name, address in getaddresses([raw]):
        candidate = address.strip()
        if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", candidate):
            if candidate.lower() not in {item.lower() for item in addresses}:
                addresses.append(candidate)
    return ",".join(addresses)


def _bounded_limit(value, default: int = 10) -> int:
    try:
        return max(1, min(int(value), 30))
    except (TypeError, ValueError):
        return default


def _set_pending_email(draft: dict[str, str]) -> None:
    from core.tenant import get_current_user_id

    user_id = get_current_user_id()
    with _PENDING_LOCK:
        if user_id:
            _TENANT_PENDING_EMAILS[user_id] = dict(draft)
        else:
            _PENDING_EMAIL.clear()
            _PENDING_EMAIL.update(draft)


def _get_pending_email() -> dict[str, str]:
    from core.tenant import get_current_user_id

    user_id = get_current_user_id()
    with _PENDING_LOCK:
        return dict(_TENANT_PENDING_EMAILS.get(user_id, {})) if user_id else dict(_PENDING_EMAIL)


def _clear_pending_email() -> None:
    from core.tenant import get_current_user_id

    user_id = get_current_user_id()
    with _PENDING_LOCK:
        if user_id:
            _TENANT_PENDING_EMAILS.pop(user_id, None)
        else:
            _PENDING_EMAIL.clear()


def _parse_rows(raw: str) -> list[dict[str, str]]:
    rows = []
    for record in str(raw or "").split(_RECORD_SEPARATOR):
        if not record.strip():
            continue
        fields = record.split(_FIELD_SEPARATOR)
        if len(fields) < 5:
            continue
        rows.append({
            "id": fields[0].strip(),
            "sender": fields[1].strip(),
            "subject": fields[2].strip() or "(No subject)",
            "date": fields[3].strip(),
            "read": fields[4].strip().lower() == "true",
        })
    return rows


def _format_rows(rows: list[dict[str, str]], heading: str) -> str:
    if not rows:
        return f"{heading}: no matching messages."
    lines = [f"{heading}: {len(rows)} message(s)."]
    for index, row in enumerate(rows, 1):
        unread = "UNREAD" if not row["read"] else "READ"
        lines.append(
            f"{index}. [{unread}] {row['subject']}\n"
            f"   From: {row['sender']}\n"
            f"   Received: {row['date']}\n"
            f"   Message ID: {row['id']}"
        )
    return "\n".join(lines)


def _list_macos(limit: int, unread_only: bool = False) -> str:
    script = r'''
on run argv
    set itemLimit to (item 1 of argv) as integer
    set unreadOnly to (item 2 of argv) is "true"
    set fieldSep to ASCII character 31
    set recordSep to ASCII character 30
    set outputText to ""
    set emitted to 0
    tell application "Mail"
        repeat with currentMessage in messages of inbox
            if emitted is greater than or equal to itemLimit then exit repeat
            set isRead to read status of currentMessage
            if (not unreadOnly) or (not isRead) then
                set outputText to outputText & (message id of currentMessage as text) & fieldSep
                set outputText to outputText & (sender of currentMessage as text) & fieldSep
                set outputText to outputText & (subject of currentMessage as text) & fieldSep
                set outputText to outputText & (date received of currentMessage as text) & fieldSep
                set outputText to outputText & (isRead as text) & recordSep
                set emitted to emitted + 1
            end if
        end repeat
    end tell
    return outputText
end run
'''
    result = _run_osascript(script, str(limit), "true" if unread_only else "false")
    if result.returncode != 0:
        return _mail_error(result, "inbox access")
    return _format_rows(_parse_rows(result.stdout), "Unread inbox" if unread_only else "Recent inbox")


def _search_macos(query: str, limit: int) -> str:
    script = r'''
on run argv
    set searchText to item 1 of argv
    set itemLimit to (item 2 of argv) as integer
    set fieldSep to ASCII character 31
    set recordSep to ASCII character 30
    set outputText to ""
    set emitted to 0
    ignoring case
        tell application "Mail"
            repeat with currentMessage in messages of inbox
                if emitted is greater than or equal to itemLimit then exit repeat
                set messageSubject to subject of currentMessage as text
                set messageSender to sender of currentMessage as text
                if messageSubject contains searchText or messageSender contains searchText then
                    set outputText to outputText & (message id of currentMessage as text) & fieldSep
                    set outputText to outputText & messageSender & fieldSep
                    set outputText to outputText & messageSubject & fieldSep
                    set outputText to outputText & (date received of currentMessage as text) & fieldSep
                    set outputText to outputText & (read status of currentMessage as text) & recordSep
                    set emitted to emitted + 1
                end if
            end repeat
        end tell
    end ignoring
    return outputText
end run
'''
    result = _run_osascript(script, query, str(limit))
    if result.returncode != 0:
        return _mail_error(result, "search")
    return _format_rows(_parse_rows(result.stdout), f"Email search for '{query}'")


def _read_macos(message_id: str, max_chars: int = 12000) -> str:
    script = r'''
on run argv
    set requestedId to item 1 of argv
    set fieldSep to ASCII character 31
    tell application "Mail"
        repeat with currentMessage in messages of inbox
            if (message id of currentMessage as text) is requestedId then
                return (sender of currentMessage as text) & fieldSep & ¬
                    (subject of currentMessage as text) & fieldSep & ¬
                    (date received of currentMessage as text) & fieldSep & ¬
                    (content of currentMessage as text)
            end if
        end repeat
    end tell
    return "NOT_FOUND"
end run
'''
    result = _run_osascript(script, message_id)
    if result.returncode != 0:
        return _mail_error(result, "reading")
    if result.stdout.strip() == "NOT_FOUND":
        return "That email is no longer available in the inbox."
    fields = result.stdout.split(_FIELD_SEPARATOR, 3)
    if len(fields) != 4:
        return "Mail returned an unreadable message format."
    sender, subject, received, content = (field.strip() for field in fields)
    if len(content) > max_chars:
        content = content[:max_chars].rstrip() + "\n\n[Message truncated]"
    return f"Subject: {subject or '(No subject)'}\nFrom: {sender}\nReceived: {received}\n\n{content}"


def _prepare_reply_or_forward(action: str, params: dict, player=None) -> str:
    """Draft an answer or a forward of the mail just read. Recipient, subject and thread come from that mail."""
    from ui_workspace import data_views

    message_id = str(params.get("message_id", "")).strip() or _LAST_READ_MESSAGE_ID
    if not message_id:
        return "Open or read the mail first, then I can reply to it or forward it."
    try:
        original = data_views.gmail_message(message_id)
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, "reading the mail to answer")
    body = str(params.get("body", "")).strip()
    if action == "reply":
        if not body:
            return "Tell me what the reply should say."
        draft = data_views.reply_payload(original, body, str(params.get("to", "")).strip())
    else:
        to = str(params.get("to", "")).strip()
        if not to:
            return "Tell me who to forward it to."
        draft = data_views.forward_payload(original, to, body)
    if params.get("cc"):
        draft["cc"] = str(params["cc"])
    return _prepare_email({**draft, "provider": "gmail"}, player=player)


def pending_as_compose() -> dict | None:
    """The pending draft in the shape of the workspace compose window, or None."""
    draft = _get_pending_email()
    if not draft:
        return None
    return {
        "to": draft.get("to", ""), "cc": draft.get("cc", ""), "subject": draft.get("subject", ""),
        "body": draft.get("body", ""), "thread_id": draft.get("thread_id", ""),
        "in_reply_to": draft.get("in_reply_to", ""), "references": draft.get("references", ""),
        "attachments": [dict(item) for item in draft.get("attachments", []) or []],
    }


def update_pending_from_compose(fields: dict) -> None:
    """Make the pending draft match what the user sees in the compose window (they may have edited it)."""
    draft = _get_pending_email()
    if not draft or not fields:
        return
    recipients = _normalize_recipients(fields.get("to", ""))
    if recipients:
        draft["to"] = recipients
    draft["cc"] = _normalize_recipients(fields.get("cc", ""))
    if str(fields.get("subject", "")).strip():
        draft["subject"] = re.sub(r"[\r\n]+", " ", str(fields["subject"])).strip()
    if str(fields.get("body", "")).strip():
        draft["body"] = str(fields["body"]).strip()
    if "attachments" in fields:
        draft["attachments"] = [dict(item) for item in fields["attachments"] or []]
    _set_pending_email(draft)


def _prepare_email(params: dict, player=None) -> str:
    to = _normalize_recipients(params.get("to", ""))
    cc = _normalize_recipients(params.get("cc", ""))
    bcc = _normalize_recipients(params.get("bcc", ""))
    subject = re.sub(r"[\r\n]+", " ", str(params.get("subject", ""))).strip()
    body = str(params.get("body", "")).strip()
    if not to:
        return (
            "A valid recipient email address is required. To answer the mail that was just read or opened, "
            "use action=reply (no address needed). To forward it, use action=forward with the recipient."
        )
    if not subject:
        return "An email subject is required."
    if not body:
        return "Email body text is required."
    provider = str(params.get("provider", "gmail")).lower().strip().replace(" ", "_")
    provider = {"google": "gmail", "apple": "apple_mail", "mail": "apple_mail"}.get(provider, provider)
    if provider not in {"gmail", "apple_mail", "default"}:
        return "Email provider must be Gmail, Apple Mail, or default."
    draft = {
        "to": to,
        "cc": cc,
        "bcc": bcc,
        "subject": subject,
        "body": body,
        "provider": provider,
    }
    if provider == "gmail":
        # RULE 1: no external browser. The draft is held in memory and sent through the Gmail API
        # only after the user approves it.
        draft["delivery"] = "gmail_api"
        for key in ("thread_id", "in_reply_to", "references"):
            if params.get(key):
                draft[key] = str(params[key])
        if params.get("attachments"):
            draft["attachments"] = [dict(item) for item in params["attachments"]]
    _set_pending_email(draft)
    preview = body if len(body) <= 800 else body[:800].rstrip() + "…"
    copies = (f"\nCc: {cc}" if cc else "") + (f"\nBcc: {bcc}" if bcc else "")
    return (
        "EMAIL_APPROVAL_REQUIRED|A pending email is ready. Nothing has been sent.\n"
        + ("The draft is held inside ARNOX. Nothing opened outside the app.\n" if provider == "gmail" else "")
        + f"Provider: {'Gmail' if provider == 'gmail' else 'Apple Mail' if provider == 'apple_mail' else 'Default mail app'}\n"
        f"To: {to}{copies}\nSubject: {subject}\n"
        + (f"Attachments: {', '.join(str(a.get('filename', 'file')) for a in draft.get('attachments', []))}\n" if draft.get("attachments") else "")
        + f"\n{preview}\n\n"
        "Ask the user to approve, revise, or cancel this exact email. When the user says send it, call action=approve."
    )


def _send_macos(draft: dict[str, str]) -> str:
    script = r'''
on splitAddresses(csvText)
    set oldDelimiters to AppleScript's text item delimiters
    set AppleScript's text item delimiters to ","
    set addressList to text items of csvText
    set AppleScript's text item delimiters to oldDelimiters
    return addressList
end splitAddresses

on run argv
    set toCsv to item 1 of argv
    set ccCsv to item 2 of argv
    set bccCsv to item 3 of argv
    set subjectText to item 4 of argv
    set bodyText to item 5 of argv
    tell application "Mail"
        set outgoingMessage to make new outgoing message with properties ¬
            {subject:subjectText, content:bodyText & return & return, visible:false}
        tell outgoingMessage
            repeat with recipientAddress in my splitAddresses(toCsv)
                make new to recipient at end of to recipients with properties {address:recipientAddress}
            end repeat
            if ccCsv is not "" then
                repeat with recipientAddress in my splitAddresses(ccCsv)
                    make new cc recipient at end of cc recipients with properties {address:recipientAddress}
                end repeat
            end if
            if bccCsv is not "" then
                repeat with recipientAddress in my splitAddresses(bccCsv)
                    make new bcc recipient at end of bcc recipients with properties {address:recipientAddress}
                end repeat
            end if
            send
        end tell
    end tell
    return "SENT"
end run
'''
    result = _run_osascript(
        script,
        draft["to"],
        draft.get("cc", ""),
        draft.get("bcc", ""),
        draft["subject"],
        draft["body"],
    )
    if result.returncode != 0 or "SENT" not in result.stdout:
        return _mail_error(result, "sending")
    _clear_pending_email()
    return f"Email sent to {draft['to']}."


def _open_default_draft(draft: dict[str, str]) -> str:
    query = urlencode({
        "subject": draft["subject"],
        "body": draft["body"],
        **({"cc": draft["cc"]} if draft.get("cc") else {}),
        **({"bcc": draft["bcc"]} if draft.get("bcc") else {}),
    })
    uri = f"mailto:{quote(draft['to'], safe='@,')}?{query}"
    try:
        if _SYSTEM == "Windows":
            os.startfile(uri)  # type: ignore[attr-defined]
        else:
            command = ["open", uri] if _SYSTEM == "Darwin" else ["xdg-open", uri]
            result = subprocess.run(command, capture_output=True, text=True, timeout=12, check=False)
            if result.returncode != 0:
                return f"Could not open the email draft: {(result.stderr or result.stdout).strip()}"
        _clear_pending_email()
        return "The approved email draft is open in your default mail application. Review it there and press Send."
    except Exception as exc:
        return f"Could not open the email draft: {exc}"


_FOLDER_ALIASES = {
    "inbox": "inbox", "sent": "sent", "drafts": "drafts", "draft": "drafts",
    "spam": "spam", "trash": "trash", "bin": "trash", "starred": "starred",
    "important": "important", "archive": "archive", "all": "all",
}
_DRAFT_HEADERS = ("To", "Cc", "Subject")


def _gmail_labels() -> str:
    try:
        service = _gmail_service()
        labels = service.users().labels().list(userId="me").execute().get("labels", []) or []
        names = sorted(label.get("name", "") for label in labels if label.get("name"))
        return "Gmail folders and labels:\n" + "\n".join(f"- {name}" for name in names)
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, "label listing")


def _gmail_folder(folder: str, limit: int) -> str:
    key = _FOLDER_ALIASES.get(folder.lower().strip(), folder.strip())
    if not key:
        return "Tell me which folder to open."
    query = "" if key == "all" else f"label:{key}" if key in set(_FOLDER_ALIASES.values()) else f'label:"{folder}"'
    return _gmail_list(query, limit, f"Gmail folder '{folder}'")


def _gmail_modify_message(message_id: str, add: tuple = (), remove: tuple = (), operation: str = "update") -> str:
    try:
        service = _gmail_service()
        service.users().messages().modify(
            userId="me", id=message_id,
            body={"addLabelIds": list(add), "removeLabelIds": list(remove)},
        ).execute()
        return f"Gmail message {operation} done."
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, operation)


def _gmail_trash_message(message_id: str, restore: bool = False) -> str:
    try:
        service = _gmail_service()
        messages = service.users().messages()
        if restore:
            messages.untrash(userId="me", id=message_id).execute()
            return "Message restored from the trash."
        messages.trash(userId="me", id=message_id).execute()
        return "Message moved to the trash. It can be restored with action=untrash."
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, "trash update")


def _build_mime(params: dict) -> EmailMessage:
    message = EmailMessage()
    message["To"] = _normalize_recipients(params.get("to", ""))
    if params.get("cc"):
        message["Cc"] = _normalize_recipients(params.get("cc", ""))
    if params.get("bcc"):
        message["Bcc"] = _normalize_recipients(params.get("bcc", ""))
    message["Subject"] = str(params.get("subject", "")).strip() or "(No subject)"
    message.set_content(str(params.get("body", "")))
    return message


def _draft_raw(params: dict) -> dict:
    raw = base64.urlsafe_b64encode(_build_mime(params).as_bytes()).decode("ascii")
    return {"message": {"raw": raw}}


def _gmail_draft_list(limit: int) -> str:
    try:
        service = _gmail_service()
        result = service.users().drafts().list(userId="me", maxResults=limit).execute()
        rows = []
        for item in result.get("drafts", []) or []:
            message = service.users().messages().get(
                userId="me", id=item["message"]["id"], format="metadata",
                metadataHeaders=["To", "Subject", "Date"],
            ).execute()
            headers = _headers_map(message.get("payload", {}))
            rows.append({
                "id": str(item.get("id", "")),
                "sender": f"To: {headers.get('to', '(no recipient)')}",
                "subject": headers.get("subject", "(No subject)"),
                "date": headers.get("date", "Unknown date"),
                "read": True,
            })
        return _format_rows(rows, "Gmail drafts") if rows else "There are no Gmail drafts."
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, "draft listing")


def _gmail_draft_create(params: dict) -> str:
    if not str(params.get("to", "")).strip():
        return "A recipient is required to create a draft."
    try:
        service = _gmail_service()
        created = service.users().drafts().create(userId="me", body=_draft_raw(params)).execute()
        return f"Draft saved in Gmail. Draft ID: {created.get('id', 'unknown')}. Nothing was sent."
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, "draft creation")


def _gmail_draft_update(params: dict) -> str:
    draft_id = str(params.get("draft_id", "")).strip()
    if not draft_id:
        return "A draft ID from the draft list is required."
    if not str(params.get("to", "")).strip():
        return "A recipient is required to update a draft."
    try:
        service = _gmail_service()
        service.users().drafts().update(userId="me", id=draft_id, body=_draft_raw(params)).execute()
        return f"Draft {draft_id} updated. Nothing was sent."
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, "draft update")


def _gmail_draft_delete(draft_id: str) -> str:
    if not draft_id.strip():
        return "A draft ID from the draft list is required."
    try:
        service = _gmail_service()
        service.users().drafts().delete(userId="me", id=draft_id.strip()).execute()
        return "Draft deleted. Nothing was sent."
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, "draft deletion")


# The message the user most recently had read, so "read the attachment" needs no message ID.
_LAST_READ_MESSAGE_ID = ""

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
_BLOCKED_ATTACHMENT_EXTS = {
    "exe", "msi", "dmg", "pkg", "app", "scr", "com", "dll", "jar", "apk", "iso", "vbs", "lnk", "bat", "cmd", "ps1",
}


def _attachment_parts(payload: dict) -> list[dict]:
    """Collect parts that carry a file, from a Gmail message payload, in order."""
    found: list[dict] = []

    def walk(part: dict) -> None:
        body = part.get("body", {}) or {}
        filename = part.get("filename") or ""
        if filename and (body.get("attachmentId") or body.get("data")):
            found.append({
                "filename": filename,
                "mime": part.get("mimeType", "application/octet-stream"),
                "size": int(body.get("size", 0) or 0),
                "attachment_id": body.get("attachmentId"),
                "data": body.get("data"),
            })
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload or {})
    return found


def _safe_filename(name: str) -> str:
    """Keep only the final path component and drop characters that are unsafe in file names."""
    base = Path(str(name).replace("\\", "/")).name
    base = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", base).strip(" .")
    base = base[-120:] if len(base) > 120 else base
    return base or "attachment"


def _format_attachment_list(parts: list[dict]) -> str:
    lines = []
    for index, part in enumerate(parts, start=1):
        kb = max(1, part["size"] // 1024) if part["size"] else 0
        size = f", {kb} KB" if kb else ""
        lines.append(f"{index}. {part['filename']} ({part['mime']}{size})")
    return "\n".join(lines)


def _select_attachment(parts: list[dict], selector: str) -> dict | str:
    selector = str(selector or "").strip()
    if not parts:
        return "This message has no attachments."
    if not selector:
        if len(parts) == 1:
            return parts[0]
        return "This message has several attachments. Tell me which one:\n" + _format_attachment_list(parts)
    if selector.isdigit() and 1 <= int(selector) <= len(parts):
        return parts[int(selector) - 1]
    matches = [part for part in parts if selector.lower() in part["filename"].lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return f"No attachment matches '{selector}'. Available:\n" + _format_attachment_list(parts)
    return f"More than one attachment matches '{selector}':\n" + _format_attachment_list(matches)


def _fetch_attachment(message_id: str, selector: str) -> tuple[str, bytes] | str:
    """Download one attachment. Returns (safe_filename, bytes) or a readable error string."""
    service = _gmail_service()
    message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    parts = _attachment_parts(message.get("payload", {}))
    chosen = _select_attachment(parts, selector)
    if isinstance(chosen, str):
        return chosen
    filename = _safe_filename(chosen["filename"])
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension in _BLOCKED_ATTACHMENT_EXTS:
        return f"{filename} is an executable or installer type, so ARNOX will not open or save it."
    if chosen["size"] > MAX_ATTACHMENT_BYTES:
        return f"{filename} is {chosen['size'] // (1024 * 1024)} MB, over the {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB limit."
    if chosen["attachment_id"]:
        result = service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=chosen["attachment_id"]
        ).execute()
        data = result.get("data", "")
    else:
        data = chosen.get("data", "")
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    if len(raw) > MAX_ATTACHMENT_BYTES:
        return f"{filename} is larger than the {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB limit."
    return filename, raw


def attachment_to_temp(message_id: str, selector: str) -> tuple[str, str]:
    """Download one attachment into a private temp folder. Returns (path, '') or ('', readable error)."""
    message_id = message_id or _LAST_READ_MESSAGE_ID
    if not message_id:
        return "", "Read or open an email first, or give a message ID."
    try:
        fetched = _fetch_attachment(message_id, selector)
    except GmailConnectionError as exc:
        return "", str(exc)
    except Exception as exc:
        return "", _gmail_api_error(exc, "attachment opening")
    if isinstance(fetched, str):
        return "", fetched
    filename, raw = fetched
    folder = Path(tempfile.mkdtemp(prefix="arnox-attachment-"))
    target = folder / filename
    target.write_bytes(raw)
    return str(target), ""


def _gmail_attachments(message_id: str) -> str:
    try:
        service = _gmail_service()
        message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        parts = _attachment_parts(message.get("payload", {}))
        if not parts:
            return "This message has no attachments."
        return "Attachments:\n" + _format_attachment_list(parts)
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, "attachment listing")


def _gmail_attachment_read(message_id: str, selector: str) -> str:
    """Download to a private temp folder, read the text locally, then delete the file."""
    try:
        fetched = _fetch_attachment(message_id, selector)
        if isinstance(fetched, str):
            return fetched
        filename, raw = fetched
        from actions.document_reader import read_document

        with tempfile.TemporaryDirectory(prefix="arnox-attachment-") as folder:
            target = Path(folder) / filename
            target.write_bytes(raw)
            return read_document(target)
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, "attachment reading")


def _gmail_attachment_save(message_id: str, selector: str) -> str:
    """Save to Downloads/ARNOX Attachments only when the user explicitly asks."""
    try:
        fetched = _fetch_attachment(message_id, selector)
        if isinstance(fetched, str):
            return fetched
        filename, raw = fetched
        folder = Path.home() / "Downloads" / "ARNOX Attachments"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / filename
        counter = 1
        while target.exists():
            target = folder / f"{Path(filename).stem} ({counter}){Path(filename).suffix}"
            counter += 1
        target.write_bytes(raw)
        return f"Attachment saved: {target}"
    except GmailConnectionError as exc:
        return str(exc)
    except Exception as exc:
        return _gmail_api_error(exc, "attachment saving")


def _gmail_management(action: str, params: dict) -> str | None:
    """Actions beyond read/send: labels, folders, trash, read state, stars, drafts.

    Returns None when the action is not a management action.
    """
    message_id = str(params.get("message_id", "")).strip()
    if not message_id and action in {"trash", "untrash", "mark_read", "mark_unread", "star", "unstar", "archive"}:
        message_id = _LAST_READ_MESSAGE_ID   # "delete this mail" acts on the mail just opened
    limit = _bounded_limit(params.get("limit", 10))
    if action == "folders":
        return _gmail_labels()
    if action == "folder":
        return _gmail_folder(str(params.get("folder", "")), limit)
    if action in {"mark_read", "mark_unread", "star", "unstar", "archive"}:
        if not message_id:
            return "A message ID from the inbox or search results is required."
        changes = {
            "mark_read": ((), ("UNREAD",)),
            "mark_unread": (("UNREAD",), ()),
            "star": (("STARRED",), ()),
            "unstar": ((), ("STARRED",)),
            "archive": ((), ("INBOX",)),
        }
        add, remove = changes[action]
        return _gmail_modify_message(message_id, add, remove, operation=action.replace("_", " "))
    if action in {"trash", "untrash"}:
        if not message_id:
            return "A message ID from the inbox or search results is required."
        return _gmail_trash_message(message_id, restore=action == "untrash")
    if action in {"attachments", "attachment_read", "attachment_save"}:
        message_id = message_id or _LAST_READ_MESSAGE_ID
        if not message_id:
            return "Read or search an email first, or give a message ID from the inbox or search results."
        selector = str(params.get("attachment", "")).strip()
        if action == "attachments":
            return _gmail_attachments(message_id)
        if action == "attachment_read":
            return _gmail_attachment_read(message_id, selector)
        return _gmail_attachment_save(message_id, selector)
    if action == "draft_list":
        return _gmail_draft_list(limit)
    if action == "draft_create":
        return _gmail_draft_create(params)
    if action == "draft_update":
        return _gmail_draft_update(params)
    if action == "draft_delete":
        return _gmail_draft_delete(str(params.get("draft_id", "")))
    return None


def email_control(parameters: dict | None = None, response=None, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = str(params.get("action", "inbox")).lower().strip().replace("-", "_").replace(" ", "_")
    aliases = {
        "list": "inbox",
        "recent": "inbox",
        "compose": "prepare",
        "send": "prepare",
        "cancel_draft": "cancel",
        "login": "connect",
        "authorize": "connect",
        "logout": "disconnect",
    }
    action = aliases.get(action, action)
    provider = str(params.get("provider", "gmail")).lower().strip().replace(" ", "_")
    provider = {"google": "gmail", "apple": "apple_mail", "mail": "apple_mail"}.get(provider, provider)
    if player:
        player.write_log(f"[Email] {provider} // {action}")

    if action == "connect":
        return _gmail_connect(str(params.get("credentials_path", "")).strip())
    if action == "status":
        return _gmail_status()
    if action == "disconnect":
        _clear_gmail_credentials()
        return (
            "The local Gmail connection was removed from ARNOX. To revoke Google-side access too, "
            "remove ARNOX from your Google Account connections."
        )
    if action == "inbox":
        limit = _bounded_limit(params.get("limit", 10))
        if provider == "gmail":
            return _gmail_list("in:inbox", limit, "Recent Gmail inbox")
        if _SYSTEM == "Darwin" and provider == "apple_mail":
            return _list_macos(limit)
        return "Inbox access for this provider is unavailable. Connect Gmail or use Apple Mail on macOS."
    if action == "unread":
        limit = _bounded_limit(params.get("limit", 10))
        if provider == "gmail":
            return _gmail_list("in:inbox is:unread", limit, "Unread Gmail inbox")
        if _SYSTEM == "Darwin" and provider == "apple_mail":
            return _list_macos(limit, unread_only=True)
        return "Unread inbox access for this provider is unavailable."
    if action == "search":
        query = str(params.get("query", "")).strip()
        if not query:
            return "Tell me which sender or subject to search for."
        limit = _bounded_limit(params.get("limit", 10))
        if provider == "gmail":
            return _gmail_list(query, limit, f"Gmail search for '{query}'")
        if _SYSTEM == "Darwin" and provider == "apple_mail":
            return _search_macos(query, limit)
        return "Email search for this provider is unavailable."
    if action == "read":
        message_id = str(params.get("message_id", "")).strip()
        if not message_id:
            return "A message ID from the inbox or search results is required."
        if provider == "gmail":
            return _gmail_read(message_id)
        if _SYSTEM == "Darwin" and provider == "apple_mail":
            return _read_macos(message_id)
        return "Email reading for this provider is unavailable."
    if action in {"reply", "forward"}:
        return _prepare_reply_or_forward(action, params, player)
    if action == "prepare":
        return _prepare_email(params, player=player)
    if action == "approve":
        draft = _get_pending_email()
        if not draft:
            return "There is no pending email to approve."
        if params.get("to"):
            draft["to"] = _normalize_recipients(params["to"]) or draft.get("to", "")
            _set_pending_email(draft)
        draft_provider = draft.get("provider", "gmail")
        if draft.get("delivery") == "gmail_web":
            return _gmail_web_send(draft)
        if draft_provider == "gmail":
            return _send_gmail(draft)
        if draft_provider == "apple_mail" and _SYSTEM == "Darwin":
            return _send_macos(draft)
        return _open_default_draft(draft)
    if action == "cancel":
        draft = _get_pending_email()
        if not draft:
            return "There is no pending email to cancel."
        discarded = False
        if draft.get("delivery") == "gmail_web":
            discarded = _gmail_web_cancel(draft)
        _clear_pending_email()
        if discarded:
            return "Pending email cancelled and the visible Gmail draft was discarded. Nothing was sent."
        if draft.get("delivery") == "gmail_web":
            return "Pending email cancelled. Nothing was sent. The visible Gmail draft may still be open."
        return "Pending email cancelled. Nothing was sent."
    if provider == "gmail":
        managed = _gmail_management(action, params)
        if managed is not None:
            return managed
    return (
        "Unknown email action. Available: connect, status, disconnect, inbox, unread, search, read, "
        "folders, folder, mark_read, mark_unread, star, unstar, archive, trash, untrash, "
        "draft_list, draft_create, draft_update, draft_delete, attachments, attachment_read, "
        "attachment_save, prepare, approve, cancel."
    )
