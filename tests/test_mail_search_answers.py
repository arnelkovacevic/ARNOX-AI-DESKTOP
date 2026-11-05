import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
import PyQt6.QtWebEngineWidgets  # noqa: E402,F401

from actions import email_control
from ui_workspace.workspace_view import WorkspaceView


class MailSearchAnswersTests(unittest.TestCase):
    def test_search_returns_text_and_opens_nothing(self):
        service = mock.MagicMock()
        service.users().messages().list().execute.return_value = {"messages": [{"id": "m1"}]}
        service.users().messages().get().execute.return_value = {
            "id": "m1", "labelIds": ["UNREAD"],
            "payload": {"headers": [
                {"name": "From", "value": "ngrok <hello@ngrok.com>"},
                {"name": "Subject", "value": "Your ngrok invoice"},
                {"name": "Date", "value": "Tue, 4 Nov 2026 10:00:00 +0100"},
            ]},
        }
        with mock.patch.object(email_control, "_gmail_service", return_value=service):
            text = email_control.email_control({"action": "search", "query": "from:ngrok"})
        self.assertIn("ngrok", text)
        self.assertIn("Your ngrok invoice", text)

    def test_the_new_mail_button_is_always_in_the_workspace_header(self):
        from PyQt6.QtWidgets import QApplication
        import sys

        self.app = QApplication.instance() or QApplication(sys.argv[:1] or ["arnox"])
        view = WorkspaceView()
        self.assertEqual(view._new_mail.text(), "✎  New mail")
        view.show_data("agenda", __import__("pandas").DataFrame([], columns=["start", "end", "title", "location", "id"]), "Calendar")
        view._new_mail.click()
        self.assertEqual(view.tab_titles()[-1], "New mail")
        view.close_all()


if __name__ == "__main__":
    unittest.main()
