"""PDF viewer built on PyMuPDF, so no external reader and no WebEngine is needed.

Pages are rendered lazily: only pages near the viewport hold an image, which keeps long
documents responsive. Search highlights every match and steps through them.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

_MIN_ZOOM, _MAX_ZOOM = 0.4, 4.0
_PAGE_GAP = 14
_KEEP_PAGES_AROUND = 3


class _PageLabel(QLabel):
    def __init__(self, width: int, height: int, parent=None):
        super().__init__(parent)
        self.setFixedSize(width, height)
        self.setStyleSheet("background: #ffffff;")
        self.rendered_zoom = 0.0
        self.rendered_hits = -1


class PdfTab(QWidget):
    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.error = ""
        self._doc: pymupdf.Document | None = None
        self._labels: list[_PageLabel] = []
        self._zoom = 1.25
        self._hits: list[tuple[int, pymupdf.Rect]] = []
        self._hit_index = -1
        self._hits_by_page: dict[int, list[pymupdf.Rect]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        self._prev = QPushButton("◀")
        self._next = QPushButton("▶")
        self._page_label = QLabel("")
        self._page_label.setObjectName("workspaceStatus")
        self._zoom_out = QPushButton("−")
        self._zoom_in = QPushButton("+")
        self._fit = QPushButton("Fit width")
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search in document")
        self._search.setClearButtonEnabled(True)
        self._hit_label = QLabel("")
        self._hit_prev = QPushButton("↑")
        self._hit_next = QPushButton("↓")
        for widget in (self._prev, self._page_label, self._next, self._zoom_out, self._zoom_in, self._fit):
            bar.addWidget(widget)
        bar.addWidget(self._search, stretch=1)
        for widget in (self._hit_label, self._hit_prev, self._hit_next):
            bar.addWidget(widget)
        layout.addLayout(bar)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._holder = QWidget()
        self._pages_layout = QVBoxLayout(self._holder)
        self._pages_layout.setContentsMargins(0, _PAGE_GAP, 0, _PAGE_GAP)
        self._pages_layout.setSpacing(_PAGE_GAP)
        self._pages_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._scroll.setWidget(self._holder)
        layout.addWidget(self._scroll, stretch=1)

        self._prev.clicked.connect(lambda: self.go_to_page(self.current_page() - 1))
        self._next.clicked.connect(lambda: self.go_to_page(self.current_page() + 1))
        self._zoom_in.clicked.connect(lambda: self.set_zoom(self._zoom * 1.2))
        self._zoom_out.clicked.connect(lambda: self.set_zoom(self._zoom / 1.2))
        self._fit.clicked.connect(self.fit_width)
        self._search.returnPressed.connect(lambda: self.search(self._search.text()))
        self._hit_next.clicked.connect(lambda: self._step_hit(1))
        self._hit_prev.clicked.connect(lambda: self._step_hit(-1))
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(30)
        self._render_timer.timeout.connect(self._render_visible)

        self._open()

    # ----- loading -------------------------------------------------------------------------
    def _open(self) -> None:
        try:
            doc = pymupdf.open(self.path)
        except Exception as exc:
            self._fail(f"{Path(self.path).name} could not be opened: {exc}")
            return
        if doc.needs_pass:
            doc.close()
            self._fail("This PDF is password protected, so it cannot be shown.")
            return
        if doc.page_count == 0:
            doc.close()
            self._fail("This PDF has no pages.")
            return
        self._doc = doc
        self._build_pages()
        self._update_page_label()
        QTimer.singleShot(0, self._render_visible)

    def _fail(self, message: str) -> None:
        self.error = message
        notice = QLabel(message)
        notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        notice.setWordWrap(True)
        self._pages_layout.addWidget(notice)
        for widget in (self._prev, self._next, self._zoom_in, self._zoom_out, self._fit, self._search,
                       self._hit_prev, self._hit_next):
            widget.setEnabled(False)

    def _build_pages(self) -> None:
        for label in self._labels:
            label.deleteLater()
        self._labels = []
        assert self._doc is not None
        for page in self._doc:
            rect = page.rect
            label = _PageLabel(int(rect.width * self._zoom), int(rect.height * self._zoom))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._pages_layout.addWidget(label)
            self._labels.append(label)

    # ----- public API (also used by tests) -----------------------------------------------
    def page_count(self) -> int:
        return len(self._labels)

    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, value: float) -> None:
        if self._doc is None:
            return
        value = max(_MIN_ZOOM, min(_MAX_ZOOM, value))
        if abs(value - self._zoom) < 0.001:
            return
        page = self.current_page()
        self._zoom = value
        for label, doc_page in zip(self._labels, self._doc):
            label.setFixedSize(int(doc_page.rect.width * value), int(doc_page.rect.height * value))
            label.clear()
            label.rendered_zoom = 0.0
        self.go_to_page(page)
        self._schedule_render()

    def fit_width(self) -> None:
        if self._doc is None:
            return
        available = self._scroll.viewport().width() - 40
        width = self._doc[0].rect.width
        if available > 0 and width > 0:
            self.set_zoom(available / width)

    def current_page(self) -> int:
        if not self._labels:
            return 0
        bar = self._scroll.verticalScrollBar()
        if bar.value() <= bar.minimum():
            return 0
        if bar.value() >= bar.maximum():
            return len(self._labels) - 1
        middle = bar.value() + self._scroll.viewport().height() // 2
        for index, label in enumerate(self._labels):
            if label.y() + label.height() > middle:
                return index
        return len(self._labels) - 1

    def go_to_page(self, index: int) -> None:
        if not self._labels:
            return
        index = max(0, min(len(self._labels) - 1, index))
        self._scroll.verticalScrollBar().setValue(max(0, self._labels[index].y() - _PAGE_GAP))
        self._update_page_label()
        self._schedule_render()

    def search(self, text: str) -> int:
        """Highlight every match and jump to the first. Returns the number of matches."""
        text = str(text or "").strip()
        self._hits, self._hits_by_page, self._hit_index = [], {}, -1
        if text and self._doc is not None:
            for number, page in enumerate(self._doc):
                for rect in page.search_for(text):
                    self._hits.append((number, rect))
                    self._hits_by_page.setdefault(number, []).append(rect)
        for label in self._labels:
            label.rendered_zoom = 0.0
        self._hit_label.setText(f"{len(self._hits)} match(es)" if text else "")
        if self._hits:
            self._step_hit(1)
        self._schedule_render()
        return len(self._hits)

    def search_count(self) -> int:
        return len(self._hits)

    def current_hit_page(self) -> int:
        return self._hits[self._hit_index][0] if 0 <= self._hit_index < len(self._hits) else -1

    def title(self) -> str:
        return Path(self.path).name

    def close_document(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None

    # ----- internals ----------------------------------------------------------------------
    def _step_hit(self, direction: int) -> None:
        if not self._hits:
            return
        self._hit_index = (self._hit_index + direction) % len(self._hits)
        for page_label in self._labels:
            page_label.rendered_zoom = 0.0   # repaint so the current match gets the strong highlight
        number, rect = self._hits[self._hit_index]
        label = self._labels[number]
        self._scroll.verticalScrollBar().setValue(max(0, int(label.y() + rect.y0 * self._zoom - 80)))
        self._hit_label.setText(f"{self._hit_index + 1} / {len(self._hits)}")
        self._update_page_label()
        self._schedule_render()

    def _update_page_label(self) -> None:
        if self._labels:
            self._page_label.setText(f"{self.current_page() + 1} / {len(self._labels)}")

    def _on_scroll(self) -> None:
        self._update_page_label()
        self._schedule_render()

    def _schedule_render(self) -> None:
        self._render_timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_render()

    def _render_visible(self) -> None:
        if self._doc is None:
            return
        top = self._scroll.verticalScrollBar().value()
        bottom = top + self._scroll.viewport().height()
        first = self.current_page()
        for index, label in enumerate(self._labels):
            near = abs(index - first) <= _KEEP_PAGES_AROUND
            visible = label.y() + label.height() >= top - 400 and label.y() <= bottom + 400
            if visible and label.rendered_zoom != self._zoom:
                self._render_page(index, label)
            elif not near and label.rendered_zoom:
                label.clear()
                label.rendered_zoom = 0.0

    def _render_page(self, index: int, label: _PageLabel) -> None:
        assert self._doc is not None
        ratio = max(1.0, float(self.devicePixelRatioF()))
        pixmap_data = self._doc[index].get_pixmap(matrix=pymupdf.Matrix(self._zoom * ratio, self._zoom * ratio), alpha=False)
        image = QImage(pixmap_data.samples, pixmap_data.width, pixmap_data.height, pixmap_data.stride,
                       QImage.Format.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(ratio)
        hits = self._hits_by_page.get(index, [])
        if hits:
            painter = QPainter(pixmap)
            current = self._hits[self._hit_index] if 0 <= self._hit_index < len(self._hits) else None
            for rect in hits:
                is_current = current is not None and current[0] == index and current[1] == rect
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 140, 0, 130) if is_current else QColor(255, 220, 0, 110))
                painter.drawRect(QRectF(rect.x0 * self._zoom, rect.y0 * self._zoom,
                                        rect.width * self._zoom, rect.height * self._zoom))
            painter.end()
        label.setPixmap(pixmap)
        label.rendered_zoom = self._zoom
