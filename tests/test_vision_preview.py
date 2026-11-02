import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication, QWidget

import ui


class VisionPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(__import__("sys").argv[:1] or ["arnox"])
        ui._load_bundled_fonts()

    def test_preview_tracks_source_and_lifecycle(self):
        host = QWidget()
        host.resize(900, 640)
        host.show()
        preview = ui.VisionPreviewWindow(host)
        try:
            with patch.object(preview, "_open_source"), \
                 patch.object(preview, "_update_frame"):
                preview.start("camera")
                self.app.processEvents()
                self.assertTrue(preview.isVisible())
                self.assertIs(preview.parentWidget(), host)
                self.assertFalse(preview.isWindow())
                self.assertEqual(preview._source, "camera")
                self.assertEqual(preview._source_label.text(), "CAMERA FEED")
                self.assertTrue(preview._frame_timer.isActive())

                preview.finish(2500)
                self.assertEqual(preview._status.text(), "ANALYSIS COMPLETE")
                self.assertTrue(preview._hide_timer.isActive())

                preview.stop()
                self.assertFalse(preview.isVisible())
                self.assertFalse(preview._frame_timer.isActive())
        finally:
            preview.stop()
            preview.deleteLater()
            host.deleteLater()

    def test_drag_position_is_clamped_inside_jarvis(self):
        host = QWidget()
        host.resize(700, 500)
        preview = ui.VisionPreviewWindow(host)
        try:
            preview.move(preview._bounded_position(QPoint(-500, -500)))
            self.assertGreaterEqual(preview.x(), 12)
            self.assertGreaterEqual(preview.y(), 12)

            preview.move(preview._bounded_position(QPoint(5000, 5000)))
            self.assertLessEqual(preview.x() + preview.width(), host.width() - 12)
            self.assertLessEqual(preview.y() + preview.height(), host.height() - 12)
        finally:
            preview.stop()
            preview.deleteLater()
            host.deleteLater()

    def test_preview_cadence_follows_graphics_quality(self):
        preview = ui.VisionPreviewWindow()
        try:
            preview.set_graphics_quality("low")
            self.assertEqual(preview._frame_timer.interval(), 100)
            preview.set_graphics_quality("medium")
            self.assertEqual(preview._frame_timer.interval(), 66)
            preview.set_graphics_quality("high")
            self.assertEqual(preview._frame_timer.interval(), 42)
        finally:
            preview.stop()
            preview.deleteLater()


if __name__ == "__main__":
    unittest.main()
