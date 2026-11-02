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
        QListView {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; outline: none; }}
        QFrame#mailCard {{ background: {C.PANEL2}; border: 1px solid {C.BORDER_B}; border-radius: 8px; }}
        QLabel#mailSubject {{ color: {C.WHITE}; font-family: "{font_family()}"; font-size: 21px; font-weight: 600; }}
        QLabel#mailFrom {{ color: {C.WHITE}; font-size: 14px; font-weight: 600; }}
        QLabel#mailKey {{ color: {C.TEXT_DIM}; font-size: 10px; letter-spacing: 1px; }}
        QLabel#mailValue {{ color: {C.TEXT}; font-size: 12px; }}
        QLabel#mailAddress {{ color: {C.TEXT_MED}; font-size: 12px; }}
        QLabel#mailBadge {{
            color: {C.BG}; background: {C.PRI}; border-radius: 4px; padding: 3px 8px;
            font-size: 10px; font-weight: 700;
        }}
        QFrame#mailDivider {{ color: {C.BORDER}; background: {C.BORDER}; max-height: 1px; border: none; }}
        QLabel#mailChip {{
            color: {C.TEXT}; background: {C.DARK2}; border: 1px solid {C.BORDER_B};
            border-radius: 12px; padding: 5px 12px; font-size: 11px;
        }}
        QLabel#mailNote {{ color: {C.TEXT_DIM}; font-size: 10px; }}
        QPlainTextEdit#composeBody {{
            background: {C.PANEL}; color: {C.WHITE}; border: 1px solid {C.BORDER}; border-radius: 8px;
            padding: 16px; font-family: "{font_family()}"; font-size: 13px; selection-background-color: {C.PRI_DIM};
        }}
        QPushButton#sendButton {{ background: {C.PRI}; color: {C.BG}; border: none; font-weight: 700; padding: 8px 20px; }}
        QPushButton#sendButton:hover {{ background: {C.ENERGY}; }}
        QPushButton#sendButton:disabled {{ background: {C.PRI_DIM}; color: {C.TEXT_DIM}; }}
        QTextBrowser#mailBody {{
            background: {C.PANEL}; color: {C.WHITE}; border: 1px solid {C.BORDER}; border-radius: 8px;
            padding: 18px; font-family: "{font_family()}"; font-size: 13px; selection-background-color: {C.PRI_DIM};
        }}
        QScrollArea {{ background: {C.DARK}; border: none; }}
        QScrollBar:vertical, QScrollBar:horizontal {{ background: {C.DARK}; border: none; }}
        QScrollBar::handle {{ background: {C.BORDER_B}; border-radius: 3px; min-height: 24px; min-width: 24px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    """
