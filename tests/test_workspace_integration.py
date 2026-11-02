import asyncio
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PyQt6.QtWidgets import QApplication

import main
import ui


class _NoSecrets:
    def get(self, _key):
        return None


class WorkspaceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.temp_dir = tempfile.TemporaryDirectory()
        base = Path(cls.temp_dir.name)
        cls.patchers = [
            patch.object(ui, "UI_SETTINGS_FILE", base / "ui_settings.json"),
            patch.object(ui, "LAYOUT_SETTINGS_FILE", base / "layout_settings.json"),
            patch.object(ui, "API_FILE", base / "api_keys.json"),
            patch.object(ui, "get_secret_store", return_value=_NoSecrets()),
            patch.object(ui.MainWindow, "_setup_system_tray", lambda self: None),
            patch.object(ui.MainWindow, "_update_metrics", lambda self: None),
            patch.object(ui.MainWindow, "_restore_detached_panels", lambda self: None),
            patch.object(ui.MainWindow, "_start_layout_autosave", lambda self: None),
            patch.object(ui.MainWindow, "_start_auto_graphics_detection", lambda self: None),
        ]
        for patcher in cls.patchers:
            patcher.start()
        ui.UI_SETTINGS_FILE.write_text(json.dumps({"intro_completed": True, "intro_every_launch": False}), encoding="utf-8")
        cls.window = ui.MainWindow("face.png")
        cls.window.show()
        cls.app.processEvents()
        cls.pdf = base / "report.pdf"
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "Quarterly report")
        doc.save(cls.pdf)
        doc.close()
        cls.csv = base / "data.csv"
        cls.csv.write_text("a,b\n1,2\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.window._workspace.close_all()
        cls.window.hide()
        cls.window.deleteLater()
        cls.app.processEvents()
        for patcher in reversed(cls.patchers):
            patcher.stop()
        cls.temp_dir.cleanup()

    def setUp(self):
        self.window._workspace.close_all()
        self.window.hide_workspace()

    def _on_core(self):
        return self.window._center_stack.currentWidget() is self.window._ai_core_wrap

    def test_core_is_the_default_page_and_nothing_existing_moved(self):
        self.assertTrue(self._on_core())
        self.assertIs(self.window._splitter.widget(1), self.window._center_stack)

    def test_opening_a_document_switches_to_the_workspace(self):
        message = self.window.open_workspace_document(str(self.pdf))
        self.assertIn("Opened report.pdf", message)
        self.assertIs(self.window._center_stack.currentWidget(), self.window._workspace)

    def test_unsupported_file_keeps_the_core_visible(self):
        text = Path(self.temp_dir.name) / "note.docx"
        text.write_bytes(b"x")
        self.assertIn("cannot be shown", self.window.open_workspace_document(str(text)))
        self.assertTrue(self._on_core())

    def test_hide_and_show_workspace(self):
        self.window.open_workspace_document(str(self.pdf))
        self.window.hide_workspace()
        self.assertTrue(self._on_core())
        self.assertEqual(self.window._workspace.tab_count(), 1)
        self.window.show_workspace()
        self.assertIs(self.window._center_stack.currentWidget(), self.window._workspace)

    def test_showing_an_empty_workspace_explains_itself(self):
        self.assertIn("nothing open", self.window.show_workspace())
        self.assertTrue(self._on_core())

    def test_closing_the_last_tab_returns_to_the_core(self):
        self.window.open_workspace_document(str(self.csv))
        self.window.close_workspace_tab()
        self.assertTrue(self._on_core())

    def test_voice_and_tool_ui_commands_drive_the_workspace(self):
        self.window.open_workspace_document(str(self.pdf))
        self.window.open_workspace_document(str(self.csv))
        self.window._ui_command_sig.emit("close_tab")
        self.app.processEvents()
        self.assertEqual(self.window._workspace.tab_titles(), ["report.pdf"])
        self.window._ui_command_sig.emit("hide_workspace")
        self.app.processEvents()
        self.assertTrue(self._on_core())
        self.window._ui_command_sig.emit("show_workspace")
        self.app.processEvents()
        self.assertIs(self.window._center_stack.currentWidget(), self.window._workspace)

    def test_workspace_recolours_with_the_status_theme(self):
        ui.ThemeManager.set_theme("arc_reactor")
        self.window.open_workspace_document(str(self.csv))
        ui.ThemeManager.apply_status("standby")
        self.assertIn("#800f22", self.window._workspace.styleSheet().lower())
        ui.ThemeManager.set_theme("arc_reactor")
        self.assertNotIn("#800f22", self.window._workspace.styleSheet().lower())

    def test_a_worker_thread_can_open_a_document_and_gets_the_real_result(self):
        facade = ui.JarvisUI.__new__(ui.JarvisUI)
        facade._win = self.window
        result = {}
        worker = threading.Thread(target=lambda: result.setdefault("text", facade.workspace_open_document(str(self.pdf))))
        worker.start()
        while worker.is_alive():
            self.app.processEvents()
            worker.join(0.01)
        self.assertIn("Opened report.pdf", result["text"])
        self.assertIs(self.window._center_stack.currentWidget(), self.window._workspace)

    def test_tool_is_declared_and_ui_actions_are_exposed_to_the_model(self):
        names = [d["name"] for d in main.TOOL_DECLARATIONS]
        self.assertIn("workspace_open", names)
        control = next(d for d in main.TOOL_DECLARATIONS if d["name"] == "jarvis_ui_control")
        actions = control["parameters"]["properties"]["action"]["enum"]
        for action in ("show_workspace", "hide_workspace", "close_tab"):
            self.assertIn(action, actions)

    def test_the_tool_runs_through_the_dispatcher(self):
        live = main.JarvisLive.__new__(main.JarvisLive)
        opened = []
        live.ui = SimpleNamespace(
            muted=True, current_file=None, set_state=lambda *_: None, write_log=lambda *_: None,
            workspace_open_document=lambda path: opened.append(path) or "Opened it.",
        )
        live.cloud_safe = False
        call = SimpleNamespace(name="workspace_open", args={"file_path": str(self.pdf)}, id="1")
        response = asyncio.run(live._execute_tool(call))
        self.assertEqual(response.response["result"], "Opened it.")
        self.assertEqual(opened, [str(self.pdf)])

    def test_the_tool_without_workspace_support_says_so(self):
        live = main.JarvisLive.__new__(main.JarvisLive)
        live.ui = SimpleNamespace(muted=True, current_file=None, set_state=lambda *_: None, write_log=lambda *_: None)
        live.cloud_safe = False
        call = SimpleNamespace(name="workspace_open", args={"file_path": str(self.pdf)}, id="1")
        response = asyncio.run(live._execute_tool(call))
        self.assertIn("only available in the desktop app", response.response["result"])


if __name__ == "__main__":
    unittest.main()
