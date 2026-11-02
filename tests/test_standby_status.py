import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import ui


class _NoSecrets:
    def get(self, _key):
        return None


class StandbyStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
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
        ]
        for patcher in cls.patchers:
            patcher.start()
        ui.UI_SETTINGS_FILE.write_text(
            json.dumps({"intro_completed": True, "intro_every_launch": False}), encoding="utf-8"
        )
        cls.window = ui.MainWindow("face.png")
        cls.window.show()
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.window.hide()
        cls.window.deleteLater()
        cls.app.processEvents()
        for patcher in reversed(cls.patchers):
            patcher.stop()
        cls.temp_dir.cleanup()

    def setUp(self):
        ui.ThemeManager.set_theme("arc_reactor")
        self.window._apply_status_theme("normal")
        self.base_primary = ui.C.PRI

    def test_timeouts_are_sixty_seconds_per_stage(self):
        self.assertEqual(ui.STATUS_WAITING_TIMEOUT_MS, 60_000)

    def test_finished_reply_keeps_the_chosen_theme_and_starts_the_timer(self):
        self.window._apply_status_theme("reply_done")
        self.assertEqual(self.window._status, "normal")
        self.assertEqual(ui.C.PRI, self.base_primary)
        self.assertTrue(self.window._idle_timer.isActive())

    def test_first_timeout_turns_yellow_second_turns_red_and_sleeps(self):
        self.window._apply_status_theme("reply_done")
        self.window._on_idle_timeout()
        self.assertEqual(self.window._status, "waiting")
        self.assertEqual(ui.C.PRI, "#ffd400")
        self.window._on_idle_timeout()
        self.assertEqual(self.window._status, "standby")
        self.assertEqual(ui.C.PRI, "#ff2244")
        self.assertEqual(self.window.hud.state, "SLEEPING")

    def test_any_reply_clears_yellow_and_restores_the_chosen_theme(self):
        self.window._apply_status_theme("waiting")
        self.window._on_user_activity("hello there")
        self.assertEqual(self.window._status, "normal")
        self.assertEqual(ui.C.PRI, self.base_primary)

    def test_speech_without_the_wake_word_does_not_wake_red_sleep(self):
        self.window._apply_status_theme("standby")
        self.window._on_user_activity("what time is it")
        self.assertEqual(self.window._status, "standby")

    def test_wake_word_wakes_red_sleep_and_restores_theme(self):
        self.window._apply_status_theme("standby")
        self.window._on_user_activity("Arnox wake up")
        self.assertEqual(self.window._status, "normal")
        self.assertEqual(ui.C.PRI, self.base_primary)
        self.assertEqual(self.window.hud.state, "LISTENING")

    def test_typed_input_wakes_red_sleep(self):
        self.window._apply_status_theme("standby")
        self.window._on_user_activity("hello", typed=True)
        self.assertEqual(self.window._status, "normal")

    def test_sleep_does_not_change_the_saved_theme_choice(self):
        ui.ThemeManager.set_theme("nanotech_gold")
        self.window._apply_status_theme("standby")
        self.assertEqual(ui.ThemeManager.current_name(), "nanotech_gold")
        self.window._apply_status_theme("normal")
        self.assertEqual(ui.ThemeManager.current_name(), "nanotech_gold")

    def test_activity_while_normal_cancels_the_idle_countdown(self):
        self.window._apply_status_theme("reply_done")
        self.window._on_user_activity("still talking")
        self.assertFalse(self.window._idle_timer.isActive())


if __name__ == "__main__":
    unittest.main()
