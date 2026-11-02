import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import ui


class GraphicsQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(__import__("sys").argv[:1] or ["arnox"])
        ui._load_bundled_fonts()

    def test_setting_is_persistent_and_preserves_other_preferences(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "settings.json"
            settings_file.write_text(json.dumps({"theme": "platinum"}), encoding="utf-8")
            with patch.object(ui, "UI_SETTINGS_FILE", settings_file):
                self.assertEqual(ui.set_graphics_quality("high"), "high")
                self.assertEqual(json.loads(settings_file.read_text(encoding="utf-8")), {
                    "theme": "platinum",
                    "graphics_quality": "high",
                })

    def test_hud_profiles_change_real_rendering_cost(self):
        hud = ui.HudCanvas("missing.png")
        try:
            hud.set_graphics_quality("low")
            self.assertEqual((hud._tmr.interval(), hud._render_stride, hud._noise_count), (50, 3, 0))
            hud.set_graphics_quality("medium")
            self.assertEqual((hud._tmr.interval(), hud._render_stride, hud._noise_count), (33, 2, 60))
            hud.set_graphics_quality("high")
            self.assertEqual((hud._tmr.interval(), hud._render_stride, hud._noise_count), (16, 1, 200))
        finally:
            hud._tmr.stop()
            hud.deleteLater()

    def test_settings_exposes_exactly_three_quality_choices(self):
        overlay = ui.SettingsOverlay(current_graphics="medium")
        try:
            self.assertEqual(set(overlay._graphics_btns), {"low", "medium", "high"})
            selected = []
            overlay.graphics_changed.connect(selected.append)
            overlay._select_graphics("high")
            self.assertEqual(selected, ["high"])
        finally:
            overlay.deleteLater()


if __name__ == "__main__":
    unittest.main()
