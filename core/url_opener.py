"""One place that decides where a web address opens: inside ARNOX when the app can show it.

RULE 1: nothing opens in an outside browser while the desktop app is running. Actions that used to
call the system browser go through open_url(); the app registers its workspace as the opener.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Callable

_in_app_opener: Callable[[str], str] | None = None


def set_in_app_opener(opener: Callable[[str], str] | None) -> None:
    global _in_app_opener
    _in_app_opener = opener


def has_in_app_opener() -> bool:
    return _in_app_opener is not None


def open_url(url: str) -> str:
    """Open an address in the ARNOX workspace; fall back to the system browser only when there is no app."""
    if _in_app_opener is not None:
        return _in_app_opener(url)
    # Headless runs (tests, hosted service) have no workspace, so the system opener is the only option.
    if sys.platform == "darwin":
        subprocess.Popen(["open", url])
    elif sys.platform.startswith("linux"):
        subprocess.Popen(["xdg-open", url])
    else:
        subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
    return f"Opened {url}."
