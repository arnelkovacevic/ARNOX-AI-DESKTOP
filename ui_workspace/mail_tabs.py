"""Inbox list, calendar agenda, and mail reader for the workspace, styled to match ARNOX.

All text from mail is untrusted, so the reader shows it as plain text only: no HTML, no links,
no remote images.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from email.utils import parseaddr, parsedate_to_datetime

import pandas
from PyQt6.QtCore import QModelIndex, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListView, QStyle, QStyledItemDelegate,
    QSizePolicy, QTextBrowser, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import QSortFilterProxyModel

from ui_workspace import theme

ROLE_ID = Qt.ItemDataRole.UserRole + 1
ROLE_NAME = Qt.ItemDataRole.UserRole + 2
ROLE_SUBJECT = Qt.ItemDataRole.UserRole + 3
ROLE_DATE = Qt.ItemDataRole.UserRole + 4
ROLE_UNREAD = Qt.ItemDataRole.UserRole + 5
ROLE_KIND = Qt.ItemDataRole.UserRole + 6
ROLE_TIME = Qt.ItemDataRole.UserRole + 7
ROLE_TITLE = Qt.ItemDataRole.UserRole + 8
ROLE_PLACE = Qt.ItemDataRole.UserRole + 9
ROLE_NOW = Qt.ItemDataRole.UserRole + 10


# ----- helpers ------------------------------------------------------------------------------
def parse_sender(raw: str) -> tuple[str, str]:
    name, address = parseaddr(str(raw or ""))
    name = name.strip().strip('"')
    return (name or address or str(raw)), address


def initials(name: str) -> str:
    parts = [p for p in str(name).replace("@", " ").replace(".", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def avatar_color(seed: str) -> QColor:
    hue = int(hashlib.sha1(seed.lower().encode("utf-8")).hexdigest()[:4], 16) % 360
    return QColor.fromHsv(hue, 130, 170)


def short_date(raw: str, now: datetime | None = None) -> str:
    try:
        moment = parsedate_to_datetime(str(raw))
    except (TypeError, ValueError):
        return str(raw)[:16]
    now = now or datetime.now(moment.tzinfo)
    local = moment.astimezone(now.tzinfo) if moment.tzinfo and now.tzinfo else moment
    if local.date() == now.date():
        return local.strftime("%H:%M")
    if local.year == now.year:
        return f"{local.day} {local.strftime('%b')}"
    return f"{local.day} {local.strftime('%b %Y')}"


def long_date(raw: str) -> str:
    try:
        moment = parsedate_to_datetime(str(raw)).astimezone()
    except (TypeError, ValueError):
        return str(raw)
    return f"{moment.strftime('%A')}, {moment.day} {moment.strftime('%B %Y, %H:%M')}"


def human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size // 1024} KB"
    return f"{size} B" if size else ""


def _colors():
    return theme._colors()


# ----- inbox ---------------------------------------------------------------------------------
class _MailDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(0, 64)

    def paint(self, painter: QPainter, option, index: QModelIndex):
        C = _colors()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.fillRect(rect, QColor(C.PRI_GHO if selected else (C.PANEL2 if hover else C.PANEL)))
        painter.setPen(QColor(C.BORDER))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        unread = bool(index.data(ROLE_UNREAD))
        name = str(index.data(ROLE_NAME) or "")
        subject = str(index.data(ROLE_SUBJECT) or "")
        stamp = str(index.data(ROLE_DATE) or "")

        if unread:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(C.PRI))
            painter.drawEllipse(rect.left() + 10, rect.center().y() - 4, 8, 8)

        avatar = QRect(rect.left() + 30, rect.top() + 12, 40, 40)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(avatar_color(name))
        painter.drawEllipse(avatar)
        painter.setPen(QColor("#ffffff"))
        font = QFont(theme.font_family(), 11, QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(avatar, Qt.AlignmentFlag.AlignCenter, initials(name))

        left = avatar.right() + 14
        date_width = 84
        text_right = rect.right() - date_width - 16

        sender_font = QFont(theme.font_family(), 12, QFont.Weight.Bold if unread else QFont.Weight.Medium)
        painter.setFont(sender_font)
        painter.setPen(QColor(C.WHITE if unread else C.TEXT_MED))
        painter.drawText(QRect(left, rect.top() + 11, text_right - left, 20),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, text_right - left))

        subject_font = QFont(theme.font_family(), 11, QFont.Weight.DemiBold if unread else QFont.Weight.Normal)
        painter.setFont(subject_font)
        painter.setPen(QColor(C.TEXT if unread else C.TEXT_DIM))
        painter.drawText(QRect(left, rect.top() + 33, text_right - left, 20),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         painter.fontMetrics().elidedText(subject, Qt.TextElideMode.ElideRight, text_right - left))

        painter.setFont(QFont(theme.font_family(), 10))
        painter.setPen(QColor(C.PRI if unread else C.TEXT_DIM))
        painter.drawText(QRect(rect.right() - date_width - 12, rect.top() + 11, date_width, 20),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, stamp)
        painter.restore()


class _FilterProxy(QSortFilterProxyModel):
    def filterAcceptsRow(self, row, parent):
        model = self.sourceModel()
        index = model.index(row, 0, parent)
        if index.data(ROLE_KIND) == "day":
            return True
        needle = self.filterRegularExpression().pattern().lower()
        if not needle:
            return True
        haystack = " ".join(str(index.data(role) or "") for role in (ROLE_NAME, ROLE_SUBJECT, ROLE_TITLE, ROLE_PLACE))
        return needle in haystack.lower()


class InboxTab(QWidget):
    open_requested = pyqtSignal(str)

    def __init__(self, frame: pandas.DataFrame, title: str = "Inbox", parent=None):
        super().__init__(parent)
        self.error = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        bar = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search sender or subject")
        self._search.setClearButtonEnabled(True)
        self._count = QLabel("")
        self._count.setObjectName("workspaceStatus")
        bar.addWidget(self._search, stretch=1)
        bar.addWidget(self._count)
        layout.addLayout(bar)

        self._model = QStandardItemModel(self)
        unread_total = 0
        for _, row in frame.iterrows():
            name, _address = parse_sender(row.get("from", ""))
            item = QStandardItem()
            item.setData(str(row.get("id", "")), ROLE_ID)
            item.setData(name, ROLE_NAME)
            item.setData(str(row.get("subject", "")), ROLE_SUBJECT)
            item.setData(short_date(row.get("date", "")), ROLE_DATE)
            unread = bool(str(row.get("unread", "")).strip())
            unread_total += unread
            item.setData(unread, ROLE_UNREAD)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._model.appendRow(item)
        self._unread_total = unread_total
        self._proxy = _FilterProxy(self)
        self._proxy.setSourceModel(self._model)
        self._view = QListView()
        self._view.setModel(self._proxy)
        self._view.setItemDelegate(_MailDelegate(self._view))
        self._view.setMouseTracking(True)
        self._view.setUniformItemSizes(True)
        self._view.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self._view.doubleClicked.connect(self._open_index)
        self._view.activated.connect(self._open_index)
        layout.addWidget(self._view, stretch=1)
        self._empty = QLabel("No messages.")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setVisible(self._model.rowCount() == 0)
        layout.addWidget(self._empty)
        self._search.textChanged.connect(self._filter)
        self._filter("")

    def total_rows(self) -> int:
        return self._model.rowCount()

    def visible_rows(self) -> int:
        return self._proxy.rowCount()

    def unread_count(self) -> int:
        return self._unread_total

    def search(self, text: str) -> None:
        self._search.setText(text)

    def message_id_at(self, row: int) -> str:
        return str(self._proxy.index(row, 0).data(ROLE_ID) or "")

    def _filter(self, text: str) -> None:
        self._proxy.setFilterRegularExpression(text)
        suffix = f"  ·  {self._unread_total} unread" if self._unread_total else ""
        self._count.setText(f"{self.visible_rows()} of {self.total_rows()} messages{suffix}")

    def _open_index(self, index: QModelIndex) -> None:
        message_id = str(index.data(ROLE_ID) or "")
        if message_id:
            self.open_requested.emit(message_id)


# ----- calendar --------------------------------------------------------------------------------
def _parse_moment(text: str) -> tuple[datetime | None, bool]:
    """Parse an API start/end. Returns (moment, all_day)."""
    text = str(text or "").strip()
    if not text:
        return None, False
    if "T" in text:
        try:
            return datetime.fromisoformat(text).astimezone(), False
        except ValueError:
            return None, False
    try:
        return datetime.combine(date.fromisoformat(text), datetime.min.time()).astimezone(), True
    except ValueError:
        return None, False


def day_label(day: date, today: date | None = None) -> str:
    today = today or date.today()
    delta = (day - today).days
    stamp = f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"
    if delta == 0:
        return f"Today  ·  {stamp}"
    if delta == 1:
        return f"Tomorrow  ·  {stamp}"
    return stamp


class _AgendaDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(0, 40 if index.data(ROLE_KIND) == "day" else 70)

    def paint(self, painter: QPainter, option, index: QModelIndex):
        C = _colors()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        painter.fillRect(rect, QColor(C.PANEL))
        if index.data(ROLE_KIND) == "day":
            painter.setFont(QFont(theme.font_family(), 11, QFont.Weight.Bold))
            painter.setPen(QColor(C.PRI))
            painter.drawText(rect.adjusted(14, 12, -10, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             str(index.data(ROLE_TITLE)).upper())
            painter.setPen(QColor(C.BORDER_B))
            painter.drawLine(rect.left() + 14, rect.bottom() - 2, rect.right() - 14, rect.bottom() - 2)
            painter.restore()
            return
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        card = rect.adjusted(14, 4, -14, -4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(C.PRI_GHO if selected else (C.PANEL2 if hover else C.PANEL)))
        painter.drawRoundedRect(card, 6, 6)
        now = bool(index.data(ROLE_NOW))
        accent = QColor(C.GREEN if now else C.PRI)
        painter.setBrush(accent)
        painter.drawRoundedRect(QRect(card.left(), card.top(), 4, card.height()), 2, 2)

        painter.setFont(QFont(theme.font_family(), 11, QFont.Weight.DemiBold))
        painter.setPen(accent)
        painter.drawText(QRect(card.left() + 18, card.top(), 120, card.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, str(index.data(ROLE_TIME)))
        text_left = card.left() + 150
        width = card.right() - text_left - 12
        painter.setFont(QFont(theme.font_family(), 12, QFont.Weight.DemiBold))
        painter.setPen(QColor(C.WHITE))
        has_place = bool(index.data(ROLE_PLACE))
        top = card.top() + (10 if has_place else 0)
        height = 24 if has_place else card.height()
        painter.drawText(QRect(text_left, top, width, height), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         painter.fontMetrics().elidedText(str(index.data(ROLE_TITLE)), Qt.TextElideMode.ElideRight, width))
        if has_place:
            painter.setFont(QFont(theme.font_family(), 10))
            painter.setPen(QColor(C.TEXT_MED))
            painter.drawText(QRect(text_left, card.top() + 34, width, 20),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             painter.fontMetrics().elidedText("⌖  " + str(index.data(ROLE_PLACE)), Qt.TextElideMode.ElideRight, width))
        if now:
            painter.setFont(QFont(theme.font_family(), 9, QFont.Weight.Bold))
            painter.setPen(QColor(C.GREEN))
            painter.drawText(QRect(card.right() - 70, card.top() + 8, 60, 18), Qt.AlignmentFlag.AlignRight, "NOW")
        painter.restore()


class CalendarTab(QWidget):
    def __init__(self, frame: pandas.DataFrame, title: str = "Calendar", parent=None):
        super().__init__(parent)
        self.error = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        bar = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search events")
        self._search.setClearButtonEnabled(True)
        self._count = QLabel("")
        self._count.setObjectName("workspaceStatus")
        bar.addWidget(self._search, stretch=1)
        bar.addWidget(self._count)
        layout.addLayout(bar)

        self._model = QStandardItemModel(self)
        self._events = 0
        current_day: date | None = None
        now = datetime.now().astimezone()
        for _, row in frame.iterrows():
            start, all_day = _parse_moment(row.get("start", ""))
            end, _ = _parse_moment(row.get("end", ""))
            if start is None:
                continue
            if current_day != start.date():
                current_day = start.date()
                header = QStandardItem()
                header.setData("day", ROLE_KIND)
                header.setData(day_label(current_day), ROLE_TITLE)
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                self._model.appendRow(header)
            if all_day:
                time_text = "All day"
            elif end is not None and end > start:
                time_text = f"{start.strftime('%H:%M')} – {end.strftime('%H:%M')}"
            else:
                time_text = start.strftime("%H:%M")
            item = QStandardItem()
            item.setData("event", ROLE_KIND)
            item.setData(str(row.get("id", "")), ROLE_ID)
            item.setData(time_text, ROLE_TIME)
            item.setData(str(row.get("title", "")), ROLE_TITLE)
            item.setData(str(row.get("location", "")), ROLE_PLACE)
            item.setData(bool(end is not None and not all_day and start <= now <= end), ROLE_NOW)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._model.appendRow(item)
            self._events += 1
        self._proxy = _FilterProxy(self)
        self._proxy.setSourceModel(self._model)
        self._view = QListView()
        self._view.setModel(self._proxy)
        self._view.setItemDelegate(_AgendaDelegate(self._view))
        self._view.setMouseTracking(True)
        self._view.setSelectionMode(QListView.SelectionMode.SingleSelection)
        layout.addWidget(self._view, stretch=1)
        self._empty = QLabel("No upcoming events.")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setVisible(self._events == 0)
        layout.addWidget(self._empty)
        self._search.textChanged.connect(self._filter)
        self._filter("")

    def event_count(self) -> int:
        return self._events

    def day_headers(self) -> list[str]:
        return [str(self._model.index(r, 0).data(ROLE_TITLE)) for r in range(self._model.rowCount())
                if self._model.index(r, 0).data(ROLE_KIND) == "day"]

    def visible_events(self) -> int:
        return sum(1 for r in range(self._proxy.rowCount()) if self._proxy.index(r, 0).data(ROLE_KIND) == "event")

    def search(self, text: str) -> None:
        self._search.setText(text)

    def _filter(self, text: str) -> None:
        self._proxy.setFilterRegularExpression(text)
        self._count.setText(f"{self.visible_events()} of {self._events} events")


# ----- mail reader ------------------------------------------------------------------------------
class MessageTab(QWidget):
    """A message laid out like a real email: header card, then the body, then attachments."""

    def __init__(self, message: dict, parent=None):
        super().__init__(parent)
        self.error = ""
        self.message_id = str(message.get("id", ""))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        card = QFrame()
        card.setObjectName("mailCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.setSpacing(12)

        subject = QLabel(str(message.get("subject", "(No subject)")))
        subject.setObjectName("mailSubject")
        subject.setWordWrap(True)
        subject.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(subject)

        sender_name, sender_address = parse_sender(message.get("from", ""))
        top = QHBoxLayout()
        avatar = QLabel(initials(sender_name))
        avatar.setFixedSize(46, 46)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        color = avatar_color(sender_name).name()
        avatar.setStyleSheet(
            f"background: {color}; color: #ffffff; border-radius: 23px; font-size: 15px; font-weight: 600;"
        )
        who = QVBoxLayout()
        who.setSpacing(2)
        name_label = QLabel(sender_name)
        name_label.setObjectName("mailFrom")
        address_label = QLabel(sender_address)
        address_label.setObjectName("mailAddress")
        who.addWidget(name_label)
        who.addWidget(address_label)
        top.addWidget(avatar)
        top.addSpacing(10)
        top.addLayout(who, stretch=1)
        if message.get("unread"):
            badge = QLabel("UNREAD")
            badge.setObjectName("mailBadge")
            badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            top.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)
        card_layout.addLayout(top)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setObjectName("mailDivider")
        card_layout.addWidget(divider)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        rows = [("From", str(message.get("from", ""))), ("To", str(message.get("to", ""))),
                ("Cc", str(message.get("cc", ""))), ("Date", long_date(message.get("date", "")))]
        row = 0
        for key, value in rows:
            if not value.strip():
                continue
            key_label = QLabel(key.upper())
            key_label.setObjectName("mailKey")
            value_label = QLabel(value)
            value_label.setObjectName("mailValue")
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(key_label, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value_label, row, 1)
            row += 1
        grid.setColumnStretch(1, 1)
        card_layout.addLayout(grid)
        layout.addWidget(card)

        self._body = QTextBrowser()
        self._body.setObjectName("mailBody")
        self._body.setOpenLinks(False)
        self._body.setOpenExternalLinks(False)
        self._body.setReadOnly(True)
        self._body.setPlainText(str(message.get("body", "")).strip() or "(This message has no text.)")
        layout.addWidget(self._body, stretch=1)

        attachments = message.get("attachments") or []
        if attachments:
            chips = QHBoxLayout()
            chips.setSpacing(8)
            heading = QLabel("ATTACHMENTS")
            heading.setObjectName("mailKey")
            chips.addWidget(heading)
            for item in attachments[:6]:
                size = human_size(int(item.get("size", 0) or 0))
                chip = QLabel(f"📎  {item.get('filename', 'file')}" + (f"   ·   {size}" if size else ""))
                chip.setObjectName("mailChip")
                chips.addWidget(chip)
            chips.addStretch(1)
            layout.addLayout(chips)

        note = QLabel("Shown as plain text. Links, images, and scripts in the message are not loaded.")
        note.setObjectName("mailNote")
        layout.addWidget(note)

    def body_text(self) -> str:
        return self._body.toPlainText()

    def title(self) -> str:
        return ""
