"""WorkspaceView: the tabbed area where ARNOX shows documents without leaving the app."""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, pyqtSignal
from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QTabBar, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from core.workspace_router import Route, classify_file, route_data, route_web
from ui_workspace import theme
from ui_workspace.pdf_tab import PdfTab
from ui_workspace.table_tab import TableTab
from ui_workspace.web_tab import WebTab

MAX_TABS = 12


class WorkspaceView(QWidget):
    hide_requested = pyqtSignal()    # user pressed "Back to core"
    emptied = pyqtSignal()           # the last tab was closed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("workspaceRoot")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self._title = QLabel("WORKSPACE")
        self._title.setObjectName("workspaceStatus")
        self._back = QPushButton("◀  BACK TO CORE")
        self._back.setToolTip("Hide the workspace and return to ARNOX")
        self._back.clicked.connect(self.hide_requested.emit)
        header.addWidget(self._title)
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
    def open_data(self, view: str) -> str:
        return self.open_route(route_data(view))

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
        if route.kind == "data":
            from ui_workspace.data_views import VIEWS

            title, build = VIEWS[route.target]
            try:
                frame = build(25, 14)
            except Exception as exc:
                return f"Could not load {title}: {exc}"
            widget = TableTab(f"data:{route.target}", frame=frame, title=title)
            widget.setProperty("route_target", f"data:{route.target}")
            index = self._tabs.addTab(widget, title)
            self._tabs.setCurrentIndex(index)
            return f"{title} shown in the workspace ({len(frame)} row(s))."
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
