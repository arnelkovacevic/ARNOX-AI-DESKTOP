import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

# QtWebEngine must be imported before any QApplication exists, and needs a real argv.
import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWidgets import QApplication

import ui  # noqa: F401
from actions import silent_lookup, weather_report
from core import workspace_router as router
from ui_workspace.web_tab import WebTab


class WebRouteTests(unittest.TestCase):
    def test_https_and_localhost_are_routed_to_the_web_tab(self):
        self.assertEqual(router.route_web("https://mail.google.com").kind, "web")
        self.assertEqual(router.route_web("http://localhost:8000").kind, "web")

    def test_other_schemes_are_refused(self):
        for bad in ("file:///etc/passwd", "javascript:alert(1)", "http://example.com"):
            self.assertEqual(router.route_web(bad).kind, "unsupported", bad)

    def test_html_files_route_to_the_local_web_tab(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.html"
            path.write_text("<h1>hi</h1>", encoding="utf-8")
            self.assertEqual(router.classify_file(path).kind, "html")


class SilentLookupTests(unittest.TestCase):
    def test_weather_is_answered_without_opening_a_browser_or_workspace(self):
        results = [{"title": "Sarajevo weather", "body": "Sunny, 12 C", "href": "https://example.com/weather"}]
        with patch.object(silent_lookup, "_ddg", return_value=results), \
             patch.object(silent_lookup, "_page_text", return_value="Today: sunny, 12 C"), \
             patch("webbrowser.open", side_effect=AssertionError("no browser")):
            answer = weather_report.weather_action({"city": "Sarajevo", "time": "today"})
        self.assertIn("Sunny, 12 C", answer)
        self.assertIn("Today: sunny", answer)

    def test_no_results_and_search_failure_are_explained(self):
        with patch.object(silent_lookup, "_ddg", return_value=[]):
            self.assertIn("found nothing", silent_lookup.silent_web_lookup("zzz"))
        with patch.object(silent_lookup, "_ddg", side_effect=RuntimeError("offline")):
            self.assertIn("could not run", silent_lookup.silent_web_lookup("news"))

    def test_only_https_pages_are_read(self):
        with patch.object(silent_lookup.requests, "get", side_effect=AssertionError("must not fetch")):
            self.assertEqual(silent_lookup._page_text("http://example.com"), "")


class WebTabPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv[:1] or ["arnox"])

    def _allowed(self, tab, url):
        return tab.page().acceptNavigationRequest(QUrl(url), QWebEnginePage.NavigationType.NavigationTypeLinkClicked, True)

    def test_remote_navigation_only_https(self):
        tab = WebTab("about:blank")
        self.assertTrue(self._allowed(tab, "https://mail.google.com"))
        self.assertFalse(self._allowed(tab, "http://example.com"))
        self.assertFalse(self._allowed(tab, "javascript:alert(1)"))
        tab.close_document()

    def test_local_file_is_allowed_only_for_its_own_html_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            other = Path(tmp) / "secret.html"
            page.write_text("<p>ok</p>", encoding="utf-8")
            other.write_text("<p>no</p>", encoding="utf-8")
            tab = WebTab(str(page), is_local_file=True)
            self.assertTrue(self._allowed(tab, QUrl.fromLocalFile(str(page)).toString()))
            self.assertFalse(self._allowed(tab, QUrl.fromLocalFile(str(other)).toString()))
            tab.close_document()

    def test_non_https_address_is_reported_not_loaded(self):
        tab = WebTab("file:///etc/passwd")
        self.assertTrue(tab.error)


if __name__ == "__main__":
    unittest.main()
