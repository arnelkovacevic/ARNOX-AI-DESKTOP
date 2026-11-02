"""WorkspaceView: the tabbed area where ARNOX shows documents without leaving the app."""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QTabBar, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from core.workspace_router import Route, classify_file, route_data, route_web
from ui_workspace import theme
from ui_workspace.mail_actions import MailActions
from ui_workspace.mail_tabs import CalendarTab, ComposeTab, InboxTab, MessageTab
from ui_workspace.pdf_tab import PdfTab
from ui_workspace.table_tab import TableTab
from ui_workspace.web_tab import WebTab

MAX_TABS = 12


class WorkspaceView(QWidget):
    hide_requested = pyqtSignal()    # user pressed "Back to core"
    emptied = pyqtSignal()           # the last tab was closed
    message_requested = pyqtSignal(str)   # a row in the inbox was opened

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("workspaceRoot")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self._title = QLabel("WORKSPACE")
        self._title.setObjectName("workspaceStatus")
        self._flash_label = QLabel("")
        self._flash_label.setObjectName("workspaceStatus")
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(lambda: self._flash_label.setText(""))
        self._mail = MailActions(self)
        self._mail.done.connect(self._on_mail_done)
        self._compose_count = 0
        self._back = QPushButton("◀  BACK TO CORE")
        self._back.setToolTip("Hide the workspace and return to ARNOX")
        self._back.clicked.connect(self.hide_requested.emit)
        header.addWidget(self._title)
        header.addSpacing(16)
        header.addWidget(self._flash_label)
        header.addStretch(1)
        header.addWidget(self._back)
        layout.addLayout(header)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        layout.addWidget(self._tabs, stretch=1)

        self._paths: dict[str, int] = {}
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(220)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.refresh_theme()

    # ----- state -----------------------------------------------------------------------------
    def tab_count(self) -> int:
        return self._tabs.count()

    def tab_titles(self) -> list[str]:
        return [self._tabs.tabText(i) for i in range(self._tabs.count())]

    def current_title(self) -> str:
        return self._tabs.tabText(self._tabs.currentIndex()) if self._tabs.count() else ""

    def current_widget(self):
        return self._tabs.currentWidget()

    def refresh_theme(self) -> None:
        self.setStyleSheet(theme.workspace_stylesheet())

    def fade_in(self) -> None:
        self._fade.stop()
        self._effect.setOpacity(0.0)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    # ----- opening and closing ---------------------------------------------------------------
    def show_data(self, kind: str, payload, title: str) -> str:
        """Show data already loaded elsewhere (in a worker thread), so the window never blocks on the network."""
        if kind == "compose":
            self._compose_count += 1
            key = f"data:compose:{self._compose_count}"
        elif kind == "message":
            key = f"data:message:{payload.get('id', '')}"
        else:
            key = f"data:{kind}"
        existing = self._index_of(key)
        if existing is not None:
            if kind == "message":
                self._tabs.setCurrentIndex(existing)
                return f"{title} is already open. Switched to it."
            self.close_tab(existing)   # a refreshed inbox, folder, or agenda replaces the old tab
        if self._tabs.count() >= MAX_TABS:
            self.close_tab(0)
        if kind in ("inbox", "sent", "trash"):
            widget = InboxTab(payload, title, mode=kind)
            widget.open_requested.connect(self._request_message)
            widget.compose_requested.connect(lambda: self.show_data("compose", self._blank_draft(), "New mail"))
        elif kind == "agenda":
            widget = CalendarTab(payload, title)
        elif kind == "message":
            widget = MessageTab(payload)
            widget.reply_requested.connect(self._start_reply)
            widget.trash_requested.connect(self._mail.trash)
            widget.restore_requested.connect(self._mail.restore)
        elif kind == "compose":
            widget = ComposeTab(payload)
            widget.setProperty("mail_token", key)
            widget.send_requested.connect(lambda data, token=key: self._mail.send(token, data))
            widget.discard_requested.connect(lambda token=key: self._close_by_key(token))
        else:
            return f"Cannot show '{kind}'."
        widget.setProperty("route_target", key)
        index = self._tabs.addTab(widget, title)
        self._tabs.setCurrentIndex(index)
        return f"{title} shown in the workspace."

    def _request_message(self, message_id: str) -> None:
        self.message_requested.emit(message_id)

    @staticmethod
    def _blank_draft() -> dict:
        return {"to": "", "cc": "", "subject": "", "body": "", "thread_id": "", "in_reply_to": "", "references": ""}

    def _start_reply(self, message: dict) -> None:
        from ui_workspace.data_views import reply_payload

        self.show_data("compose", reply_payload(message), "Reply")

    def _close_by_key(self, key: str) -> None:
        index = self._index_of(key)
        if index is not None:
            self.close_tab(index)

    def flash(self, text: str) -> None:
        """Short status in the workspace header: what just happened, for a few seconds."""
        self._flash_label.setText(text)
        self._flash_timer.start(6000)

    def _on_mail_done(self, action: str, ok: bool, message: str, key: str) -> None:
        if action == "send":
            index = self._index_of(key)
            tab = self._tabs.widget(index) if index is not None else None
            if ok:
                self.flash(message)
                self._close_by_key(key)
            elif tab is not None:
                tab.mail_result(False, message)
            else:
                self.flash(message)
            return
        index = self._index_of(f"data:message:{key}")
        tab = self._tabs.widget(index) if index is not None else None
        if ok:
            for position in range(self._tabs.count()):
                widget = self._tabs.widget(position)
                if hasattr(widget, "remove_message"):
                    widget.remove_message(key)
            self.flash(message)
            if index is not None:
                self.close_tab(index)
        elif tab is not None:
            tab.mail_result(False, message)
        else:
            self.flash(message)

    def open_web(self, address: str) -> str:
        """Show an https page (or http on localhost) in a web tab."""
        route = route_web(address)
        return self.open_route(route)

    def _open_popup_tab(self):
        """Pop-ups get a new WebTab inside the workspace, returned to Qt as the new page."""
        tab = WebTab("about:blank", popup_handler=self._open_popup_tab)
        index = self._tabs.addTab(tab, "New tab")
        self._tabs.setCurrentIndex(index)
        return tab.page()

    def open_document(self, path: str) -> str:
        """Show a file in a tab. Returns a short sentence ARNOX can say or display."""
        route = classify_file(path)
        return self.open_route(route)

    def open_route(self, route: Route) -> str:
        if route.kind == "unsupported":
            return route.reason or "That file cannot be shown in the workspace."
        existing = self._index_of(route.target)
        if existing is not None:
            self._tabs.setCurrentIndex(existing)
            return f"{route.title} is already open. Switched to it."
        if self._tabs.count() >= MAX_TABS:
            self.close_tab(0)
        if route.kind == "web":
            widget = WebTab(route.target, popup_handler=self._open_popup_tab)
        elif route.kind == "html":
            widget = WebTab(route.target, is_local_file=True, popup_handler=self._open_popup_tab)
        else:
            widget = PdfTab(route.target) if route.kind == "pdf" else TableTab(route.target)
        widget.setProperty("route_target", route.target)
        if getattr(widget, "error", "") and route.kind in ("web", "html"):
            message = widget.error
            widget.deleteLater()
            return message
        if getattr(widget, "error", ""):
            message = widget.error
            widget.deleteLater()
            return message
        index = self._tabs.addTab(widget, route.title)
        self._tabs.setTabToolTip(index, route.target)
        close = QToolButton()
        close.setText("✕")
        close.setObjectName("tabClose")
        close.setToolTip("Close tab")
        close.setAutoRaise(True)
        close.clicked.connect(lambda _checked=False, w=widget: self.close_tab(self._tabs.indexOf(w)))
        self._tabs.tabBar().setTabButton(index, QTabBar.ButtonPosition.RightSide, close)
        self._tabs.setCurrentIndex(index)
        return f"Opened {route.title} in the workspace."

    def _index_of(self, target: str) -> int | None:
        for index in range(self._tabs.count()):
            if self._tabs.widget(index).property("route_target") == target:
                return index
        return None

    def close_tab(self, index: int) -> None:
        if not 0 <= index < self._tabs.count():
            return
        widget = self._tabs.widget(index)
        self._tabs.removeTab(index)
        if hasattr(widget, "close_document"):
            widget.close_document()
        widget.deleteLater()
        if self._tabs.count() == 0:
            self.emptied.emit()

    def close_current(self) -> str:
        if not self._tabs.count():
            return "There is no open tab."
        title = self.current_title()
        self.close_tab(self._tabs.currentIndex())
        return f"Closed {title}."

    def close_all(self) -> None:
        while self._tabs.count():
            self.close_tab(self._tabs.count() - 1)
