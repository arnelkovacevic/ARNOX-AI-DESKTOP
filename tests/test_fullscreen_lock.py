import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

import ui


class _NoSecrets:
    def get(self, _key):
        return None


class FullscreenLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(__import__("sys").argv[:1] or ["arnox"])
        cls.temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(cls.temp_dir.name)
        cls.patchers = [
            patch.object(ui, "UI_SETTINGS_FILE", temp_path / "ui_settings.json"),
            patch.object(ui, "LAYOUT_SETTINGS_FILE", temp_path / "layout_settings.json"),
            patch.object(ui, "API_FILE", temp_path / "api_keys.json"),
            patch.object(ui, "get_secret_store", return_value=_NoSecrets()),
            patch.object(ui.MainWindow, "_setup_system_tray", lambda self: None),
            patch.object(ui.MainWindow, "_update_metrics", lambda self: None),
            patch.object(ui.MainWindow, "_restore_detached_panels", lambda self: None),
            patch.object(ui.MainWindow, "_start_layout_autosave", lambda self: None),
            patch.object(ui.MainWindow, "_start_auto_graphics_detection", lambda self: None),
            patch.object(ui.MainWindow, "_set_kiosk", lambda self, on: None),
        ]
        for patcher in cls.patchers:
            patcher.start()
        ui.UI_SETTINGS_FILE.write_text(json.dumps({"intro_completed": True, "intro_every_launch": False}), encoding="utf-8")
        cls.window = ui.MainWindow("face.png")
        cls.window.show()
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.window._fullscreen_locked = False
        cls.window.hide()
        cls.window.deleteLater()
        cls.app.processEvents()
        for patcher in reversed(cls.patchers):
            patcher.stop()
        cls.temp_dir.cleanup()

    def setUp(self):
        self.window._fullscreen_locked = False
        self.window.showNormal()

    def test_lock_enters_fullscreen_and_stays_on_top(self):
        self.window.lock_fullscreen()
        self.assertTrue(self.window._fullscreen_locked)
        self.assertTrue(self.window.isFullScreen())
        self.assertTrue(bool(self.window.windowFlags() & ui.Qt.WindowType.WindowStaysOnTopHint))

    def test_fullscreen_toggle_cannot_leave_while_locked(self):
        self.window.lock_fullscreen()
        self.window._toggle_fullscreen()
        self.app.processEvents()
        self.assertTrue(self.window.isFullScreen())

    def test_window_close_is_refused_while_locked(self):
        self.window.lock_fullscreen()
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())

    def test_unlock_requires_the_user_to_confirm(self):
        self.window.lock_fullscreen()
        with patch.object(ui.QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.window.unlock_fullscreen()
        self.assertTrue(self.window._fullscreen_locked)

    def test_user_confirmed_unlock_releases_the_lock(self):
        self.window.lock_fullscreen()
        with patch.object(ui.QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.unlock_fullscreen()
        self.assertFalse(self.window._fullscreen_locked)
        self.app.processEvents()
        self.assertFalse(self.window.isFullScreen())
        self.window._toggle_fullscreen()
        self.app.processEvents()
        self.assertTrue(self.window.isFullScreen())

    def test_model_can_lock_through_the_ui_tool_but_has_no_unlock_action(self):
        import main

        decl = next(d for d in main.TOOL_DECLARATIONS if d["name"] == "jarvis_ui_control")
        actions = decl["parameters"]["properties"]["action"]["enum"]
        self.assertIn("lock_fullscreen", actions)
        self.assertNotIn("unlock_fullscreen", actions)

    def test_focus_loss_while_locked_schedules_reclaim(self):
        self.window.lock_fullscreen()
        with patch.object(ui.QTimer, "singleShot") as single:
            self.window._reclaim_focus()
        self.assertTrue(self.window.isFullScreen())


if __name__ == "__main__":
    unittest.main()
