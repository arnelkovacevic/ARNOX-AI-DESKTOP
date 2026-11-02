"""CSV and Excel viewer: sortable, searchable table in the ARNOX theme."""

from __future__ import annotations

from pathlib import Path

import pandas
from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QLabel, QLineEdit, QTableView, QVBoxLayout, QWidget,
)

from ui_workspace.loaders import TableLoadError, load_tables, truncate


class DataFrameModel(QAbstractTableModel):
    def __init__(self, frame: pandas.DataFrame, parent=None):
        super().__init__(parent)
        self._frame = frame

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._frame)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._frame.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return str(self._frame.iat[index.row(), index.column()])
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return str(self._frame.columns[section])
        return str(section + 1)


class _NumericAwareProxy(QSortFilterProxyModel):
    """Sorts numbers as numbers (2 before 10) and everything else as text."""

    def lessThan(self, left, right):
        a = str(self.sourceModel().data(left) or "")
        b = str(self.sourceModel().data(right) or "")
        try:
            return float(a.replace(",", "")) < float(b.replace(",", ""))
        except ValueError:
            return a.lower() < b.lower()


class TableTab(QWidget):
    def __init__(self, path: str, parent=None, frame: pandas.DataFrame | None = None, title: str = ""):
        super().__init__(parent)
        self.path = path
        self._title = title or Path(path).name
        self._tables: dict[str, pandas.DataFrame] = {}
        self._truncated = False
        self.error = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        self._sheet_box = QComboBox()
        self._sheet_box.currentTextChanged.connect(self._show_sheet)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search all columns")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        self._status = QLabel("")
        self._status.setObjectName("workspaceStatus")
        bar.addWidget(self._sheet_box)
        bar.addWidget(self._search, stretch=1)
        bar.addWidget(self._status)
        layout.addLayout(bar)

        self._view = QTableView()
        self._view.setAlternatingRowColors(True)
        self._view.setSortingEnabled(True)
        self._view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._view.horizontalHeader().setStretchLastSection(True)
        self._view.verticalHeader().setDefaultSectionSize(26)
        layout.addWidget(self._view, stretch=1)

        self._proxy = _NumericAwareProxy(self)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(-1)
        self._view.setModel(self._proxy)

        try:
            self._tables = {title or "Data": frame} if frame is not None else load_tables(path)
        except TableLoadError as exc:
            self.error = str(exc)
            self._status.setText(self.error)
            self._sheet_box.hide()
            self._search.setEnabled(False)
            return
        self._sheet_box.blockSignals(True)
        self._sheet_box.addItems(list(self._tables))
        self._sheet_box.blockSignals(False)
        self._sheet_box.setVisible(len(self._tables) > 1)
        self._show_sheet(next(iter(self._tables)))

    def sheet_names(self) -> list[str]:
        return list(self._tables)

    def visible_rows(self) -> int:
        return self._proxy.rowCount()

    def total_rows(self) -> int:
        model = self._proxy.sourceModel()
        return model.rowCount() if model else 0

    def search(self, text: str) -> None:
        self._search.setText(text)

    def _show_sheet(self, name: str) -> None:
        frame = self._tables.get(name)
        if frame is None:
            return
        frame, self._truncated = truncate(frame)
        model = DataFrameModel(frame, self)
        self._proxy.setSourceModel(model)
        self._view.resizeColumnsToContents()
        for column in range(model.columnCount()):
            if self._view.columnWidth(column) > 360:
                self._view.setColumnWidth(column, 360)
        self._apply_filter(self._search.text())

    def _apply_filter(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)
        shown, total = self.visible_rows(), self.total_rows()
        suffix = "  (first 200,000 rows shown)" if self._truncated else ""
        self._status.setText(f"{shown:,} of {total:,} rows{suffix}")

    def title(self) -> str:
        return self._title
