"""Embedded web tab: shows https pages and local HTML files inside the ARNOX window.

Navigation is restricted: only https (and http on localhost) pages, plus the one local HTML
file the tab was opened for. Pop-ups open as new tabs in the same workspace, never as windows.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from core.workspace_router import normalize_web_target

PROFILE_DIR = Path.home() / ".arnox" / "web_profile"


def _shared_profile() -> QWebEngineProfile:
    """One persistent profile for all web tabs, so sign-ins and cookies survive restarts."""
    profile = getattr(_shared_profile, "_profile", None)
    if profile is None:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        profile = QWebEngineProfile("arnox", None)
        profile.setPersistentStoragePath(str(PROFILE_DIR))
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
        _shared_profile._profile = profile
    return profile


class _RestrictedPage(QWebEnginePage):
    def __init__(self, profile: QWebEngineProfile, allowed_local: str | None, on_popup, parent=None):
        super().__init__(profile, parent)
        self._allowed_local = allowed_local
        self._on_popup = on_popup

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame: bool) -> bool:
        scheme = url.scheme().lower()
        if scheme == "file":
            # Local files are allowed only for the one HTML document this tab was opened for.
            return bool(self._allowed_local) and Path(url.toLocalFile()).resolve() == Path(self._allowed_local).resolve()
        if scheme in ("about", "blob", "data") and not is_main_frame:
            return True
        allowed, _ = normalize_web_target(url.toString())
        return allowed is not None

    def createWindow(self, _kind):
        # Pop-ups become tabs inside ARNOX; they never open as a separate window.
        return self._on_popup()


class WebTab(QWidget):
    def __init__(self, target: str, is_local_file: bool = False, popup_handler=None, parent=None):
        super().__init__(parent)
        self.target = target
        self.error = ""
        self._view = QWebEngineView(self)
        allowed_local = target if is_local_file else None
        self._page = _RestrictedPage(_shared_profile(), allowed_local, self._new_popup, self._view)
        self._view.setPage(self._page)
        self._popup_handler = popup_handler
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)
        if is_local_file:
            self._view.setUrl(QUrl.fromLocalFile(str(Path(target).resolve())))
        else:
            url, error = normalize_web_target(target)
            if url is None:
                self.error = error
            else:
                self.target = url
                self._view.setUrl(QUrl(url))

    def url_text(self) -> str:
        return self._view.url().toString()

    def page(self) -> QWebEnginePage:
        return self._page

    def _new_popup(self):
        if self._popup_handler is None:
            return None
        return self._popup_handler()

    def close_document(self) -> None:
        self._view.setPage(None)
        self._page.deleteLater()
