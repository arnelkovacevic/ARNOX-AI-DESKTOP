"""Viewers for images and text-like documents, so an attachment opens inside the workspace."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ui_workspace import theme

_MAX_TEXT_CHARS = 400_000


class ImageTab(QWidget):
    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.error = ""
        self._pixmap = QPixmap(path)
        self._zoom = 1.0
        self._fit = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        bar = QHBoxLayout()
        self._info = QLabel("")
        self._info.setObjectName("workspaceStatus")
        self._fit_button = QPushButton("Fit")
        self._actual = QPushButton("100%")
        self._in = QPushButton("+")
        self._out = QPushButton("−")
        for widget in (self._fit_button, self._actual, self._out, self._in):
            bar.addWidget(widget)
        bar.addWidget(self._info, stretch=1)
        layout.addLayout(bar)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._label)
        layout.addWidget(self._scroll, stretch=1)
        if self._pixmap.isNull():
            self.error = f"{Path(path).name} could not be shown as an image."
            self._label.setText(self.error)
            for widget in (self._fit_button, self._actual, self._in, self._out):
                widget.setEnabled(False)
            return
        self._info.setText(f"{self._pixmap.width()} × {self._pixmap.height()} px")
        self._fit_button.clicked.connect(self.fit)
        self._actual.clicked.connect(lambda: self.set_zoom(1.0))
        self._in.clicked.connect(lambda: self.set_zoom(self._zoom * 1.25))
        self._out.clicked.connect(lambda: self.set_zoom(self._zoom / 1.25))
        self.fit()

    def zoom(self) -> float:
        return self._zoom

    def fit(self) -> None:
        self._fit = True
        self._render()

    def set_zoom(self, value: float) -> None:
        self._fit = False
        self._zoom = max(0.1, min(8.0, value))
        self._render()

    def _render(self) -> None:
        if self._pixmap.isNull():
            return
        if self._fit:
            box = self._scroll.viewport().size()
            scaled = self._pixmap.scaled(max(50, box.width() - 8), max(50, box.height() - 8),
                                         Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._zoom = scaled.width() / max(1, self._pixmap.width())
        else:
            scaled = self._pixmap.scaled(int(self._pixmap.width() * self._zoom), int(self._pixmap.height() * self._zoom),
                                         Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._label.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit:
            self._render()

    def title(self) -> str:
        return Path(self.path).name


class TextTab(QWidget):
    """Read-only text. Used for text files and for Word/PowerPoint text extracted locally."""

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.error = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self._edit = QPlainTextEdit()
        self._edit.setReadOnly(True)
        self._edit.setObjectName("composeBody")
        font = QFont("Menlo" if Path("/System").exists() else "Consolas", 11)
        self._edit.setFont(font)
        layout.addWidget(self._edit)
        text = self._load(path)
        if text is None:
            self.error = f"{Path(path).name} could not be read."
            self._edit.setPlainText(self.error)
        else:
            self._edit.setPlainText(text)

    @staticmethod
    def _load(path: str) -> str | None:
        ext = Path(path).suffix.lower().lstrip(".")
        try:
            if ext in {"docx", "pptx"}:
                from actions.document_reader import read_document

                text = read_document(path, max_chars=_MAX_TEXT_CHARS)
                return text
            data = Path(path).read_bytes()
        except OSError:
            return None
        for encoding in ("utf-8-sig", "cp1250", "latin-1"):
            try:
                return data.decode(encoding)[:_MAX_TEXT_CHARS]
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")[:_MAX_TEXT_CHARS]

    def text(self) -> str:
        return self._edit.toPlainText()

    def title(self) -> str:
        return Path(self.path).name
