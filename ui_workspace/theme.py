"""Colours for the workspace, read from the live ARNOX theme so it matches every state."""

from __future__ import annotations


def _colors():
    # Imported lazily: ui.py imports this package, so a top-level import would be circular.
    from ui import C

    return C


def font_family() -> str:
    try:
        from ui import UI_FONT

        return str(UI_FONT)
    except Exception:
        return "Space Grotesk"


def workspace_stylesheet() -> str:
    C = _colors()
    return f"""
        QWidget#workspaceRoot {{ background: {C.BG}; }}
        QTabWidget::pane {{ border: 1px solid {C.BORDER}; background: {C.PANEL}; top: -1px; }}
        QTabBar::tab {{
            background: {C.DARK}; color: {C.TEXT_MED}; padding: 8px 16px;
            border: 1px solid {C.BORDER}; border-bottom: none; margin-right: 2px;
            font-family: "{font_family()}"; font-size: 12px;
        }}
        QTabBar::tab:selected {{ background: {C.PANEL2}; color: {C.WHITE}; border-top: 2px solid {C.PRI}; }}
        QTabBar::tab:hover {{ color: {C.PRI}; }}
        QToolButton#tabClose {{
            background: transparent; border: none; color: {C.TEXT_DIM}; padding: 0 6px; font-size: 11px;
        }}
        QToolButton#tabClose:hover {{ color: {C.RED}; background: transparent; }}
        QLineEdit {{
            background: {C.DARK}; color: {C.WHITE}; border: 1px solid {C.BORDER_B};
            padding: 5px 8px; selection-background-color: {C.PRI_DIM};
        }}
        QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        QPushButton, QToolButton {{
            background: {C.DARK2}; color: {C.PRI}; border: 1px solid {C.BORDER_B};
            padding: 5px 10px; font-family: "{font_family()}";
        }}
        QPushButton:hover, QToolButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {C.TEXT_DIM}; border: 1px solid {C.BORDER}; }}
        QLabel {{ color: {C.TEXT_MED}; background: transparent; }}
        QLabel#workspaceStatus {{ color: {C.PRI}; }}
        QComboBox {{
            background: {C.DARK}; color: {C.WHITE}; border: 1px solid {C.BORDER_B}; padding: 4px 8px;
        }}
        QComboBox QAbstractItemView {{ background: {C.PANEL}; color: {C.WHITE}; selection-background-color: {C.PRI_DIM}; }}
        QTableView {{
            background: {C.PANEL}; alternate-background-color: {C.PANEL2}; color: {C.WHITE};
            gridline-color: {C.BORDER}; border: none; selection-background-color: {C.PRI_DIM};
            selection-color: {C.WHITE};
        }}
        QHeaderView::section {{
            background: {C.DARK2}; color: {C.PRI}; border: none; border-right: 1px solid {C.BORDER};
            border-bottom: 1px solid {C.BORDER_B}; padding: 6px 8px;
        }}
        QScrollArea {{ background: {C.DARK}; border: none; }}
        QScrollBar:vertical, QScrollBar:horizontal {{ background: {C.DARK}; border: none; }}
        QScrollBar::handle {{ background: {C.BORDER_B}; border-radius: 3px; min-height: 24px; min-width: 24px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    """
