"""Decides which embedded viewer should show a file or address. No Qt imports, so it is easy to test."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

PDF_EXTS = {"pdf"}
TABLE_EXTS = {"csv", "tsv", "xlsx", "xlsm", "xls"}
HTML_EXTS = {"html", "htm"}
MAX_VIEW_BYTES = 200 * 1024 * 1024

# Apps ARNOX can later show in the web tab. Playback with DRM (Spotify) does not work in the
# embedded Chromium, which has no Widevine, so those stay with the desktop app.
APP_URLS = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "maps": "https://maps.google.com",
}


@dataclass(frozen=True)
class Route:
    kind: str          # "pdf" | "table" | "html" | "web" | "unsupported"
    target: str        # resolved file path or normalized URL
    title: str
    reason: str = ""   # why a route is unsupported


def classify_file(path: str | Path) -> Route:
    """Pick a viewer for a local file, or explain why none applies."""
    file = Path(str(path)).expanduser()
    if not file.exists() or not file.is_file():
        return Route("unsupported", str(file), file.name, f"File not found: {file}")
    ext = file.suffix.lower().lstrip(".")
    try:
        size = file.stat().st_size
    except OSError as exc:
        return Route("unsupported", str(file), file.name, f"Cannot read {file.name}: {exc}")
    if size > MAX_VIEW_BYTES:
        return Route("unsupported", str(file), file.name, f"{file.name} is larger than {MAX_VIEW_BYTES // (1024 * 1024)} MB.")
    if ext in PDF_EXTS:
        return Route("pdf", str(file.resolve()), file.name)
    if ext in TABLE_EXTS:
        return Route("table", str(file.resolve()), file.name)
    if ext in HTML_EXTS:
        return Route("html", str(file.resolve()), file.name)
    return Route(
        "unsupported", str(file), file.name,
        f".{ext or 'unknown'} files cannot be shown in the workspace yet. Supported: PDF, CSV, TSV, Excel, HTML.",
    )


def normalize_web_target(value: str) -> tuple[str | None, str]:
    """Validate an address the model or user wants to show. Returns (url, error).

    Only https and plain-http localhost are allowed. file:, javascript:, data:, and other
    schemes are rejected so a web page or document cannot make the app read local files.
    """
    raw = str(value or "").strip()
    if not raw:
        return None, "No address given."
    alias = APP_URLS.get(raw.lower())
    if alias:
        return alias, ""
    if "://" not in raw and not raw.lower().startswith(("javascript:", "data:", "file:", "about:")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme == "https" and host:
        return raw, ""
    if scheme == "http" and host in {"localhost", "127.0.0.1"}:
        return raw, ""
    return None, f"Only https addresses (or http on localhost) can be opened, not '{scheme or raw}'."


DATA_VIEWS = ("inbox", "unread", "agenda")


def route_data(view: str) -> Route:
    """Route a Gmail or Calendar view to a table tab. Only known views are accepted."""
    key = str(view or "").strip().lower()
    if key not in DATA_VIEWS:
        return Route("unsupported", key, key, f"Unknown view '{key}'. Use inbox, unread, or agenda.")
    return Route("data", key, key)


def route_web(value: str) -> Route:
    """Route an address to the web tab, or explain why it cannot be shown."""
    url, error = normalize_web_target(value)
    if url is None:
        return Route("unsupported", str(value), str(value), error)
    host = urlparse(url).hostname or url
    return Route("web", url, host)
