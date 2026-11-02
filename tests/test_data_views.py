import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtWidgets import QApplication

import main
from actions import calendar_control, email_control
from core import workspace_router as router
from ui_workspace import data_views
from ui_workspace.workspace_view import WorkspaceView


def _gmail_service(messages):
    service = mock.MagicMock()
    listing = {"messages": [{"id": m["id"]} for m in messages]}
    service.users().messages().list().execute.return_value = listing
    by_id = {m["id"]: m for m in messages}

    def get(userId, id, format, metadataHeaders=None):
        call = mock.MagicMock()
        call.execute.return_value = by_id[id]["message"]
        return call

    service.users().messages().get.side_effect = get
    return service


class DataViewTests(unittest.TestCase):
    def test_inbox_frame_has_metadata_only_and_flags_unread(self):
        messages = [
            {"id": "m1", "message": {
                "id": "m1", "labelIds": ["UNREAD"],
                "payload": {"headers": [
                    {"name": "From", "value": "Alex <a@example.com>"},
                    {"name": "Subject", "value": "Invoice"},
                    {"name": "Date", "value": "Mon, 2 Nov 2026"},
                ], "body": {"data": "c2VjcmV0"}},
                "snippet": "private body text",
            }},
            {"id": "m2", "message": {
                "id": "m2", "labelIds": [],
                "payload": {"headers": [{"name": "Subject", "value": "Hello"}]},
            }},
        ]
        with mock.patch.object(email_control, "_gmail_service", return_value=_gmail_service(messages)):
            frame = data_views.gmail_frame("in:inbox", 10)
        self.assertEqual(list(frame.columns), data_views.MAIL_COLUMNS)
        self.assertEqual(frame.iloc[0]["from"], "Alex <a@example.com>")
        self.assertEqual(frame.iloc[0]["unread"], "yes")
        self.assertEqual(frame.iloc[1]["from"], "Unknown sender")
        self.assertNotIn("private body text", frame.to_string())
        self.assertNotIn("c2VjcmV0", frame.to_string())

    def test_agenda_frame_maps_timed_and_all_day_events(self):
        service = mock.MagicMock()
        service.events().list().execute.return_value = {"items": [
            {"id": "e1", "summary": "Standup", "start": {"dateTime": "2026-11-05T09:00:00+01:00"}, "location": "Office"},
            {"id": "e2", "summary": "Holiday", "start": {"date": "2026-11-06"}},
        ]}
        with mock.patch.object(calendar_control, "_calendar_service", return_value=service):
            frame = data_views.agenda_frame(7, 10)
        self.assertEqual(frame.iloc[0]["title"], "Standup")
        self.assertEqual(frame.iloc[0]["location"], "Office")
        self.assertEqual(frame.iloc[1]["start"], "2026-11-06")

    def test_only_known_views_are_routed(self):
        self.assertEqual(router.route_data("inbox").kind, "data")
        self.assertEqual(router.route_data("agenda").kind, "data")
        self.assertEqual(router.route_data("passwords").kind, "unsupported")


class DataViewWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv[:1] or ["arnox"])

    def test_workspace_shows_inbox_as_a_table_tab(self):
        frame = data_views.pandas.DataFrame([{"from": "a", "subject": "s", "date": "d", "unread": "", "id": "1"}],
                                            columns=data_views.MAIL_COLUMNS)
        view = WorkspaceView()
        with mock.patch.dict(data_views.VIEWS, {"inbox": ("Inbox", lambda limit, days: frame)}):
            message = view.open_data("inbox")
        self.assertIn("Inbox shown", message)
        self.assertEqual(view.tab_titles(), ["Inbox"])
        self.assertEqual(view.current_widget().total_rows(), 1)
        view.close_all()

    def test_failed_load_is_reported_without_a_tab(self):
        view = WorkspaceView()
        with mock.patch.dict(data_views.VIEWS, {"agenda": ("Calendar", mock.Mock(side_effect=RuntimeError("offline")))}):
            self.assertIn("Could not load Calendar", view.open_data("agenda"))
        self.assertEqual(view.tab_count(), 0)


class ToolTests(unittest.TestCase):
    def test_tool_view_argument_reaches_the_workspace(self):
        live = main.JarvisLive.__new__(main.JarvisLive)
        shown = []
        live.ui = SimpleNamespace(
            muted=True, current_file=None, set_state=lambda *_: None, write_log=lambda *_: None,
            workspace_open_data=lambda view: shown.append(view) or "Inbox shown.",
        )
        live.cloud_safe = False
        call = SimpleNamespace(name="workspace_open", args={"view": "inbox"}, id="1")
        response = asyncio.run(live._execute_tool(call))
        self.assertEqual(shown, ["inbox"])
        self.assertEqual(response.response["result"], "Inbox shown.")

    def test_tool_declares_the_views(self):
        decl = next(d for d in main.TOOL_DECLARATIONS if d["name"] == "workspace_open")
        self.assertEqual(decl["parameters"]["properties"]["view"]["enum"], ["inbox", "unread", "agenda"])


if __name__ == "__main__":
    unittest.main()
