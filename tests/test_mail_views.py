import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
import pandas
from PyQt6.QtWidgets import QApplication

import ui  # noqa: F401
from actions import email_control
from ui_workspace import data_views, mail_tabs
from ui_workspace.mail_tabs import CalendarTab, InboxTab, MessageTab
from ui_workspace.workspace_view import WorkspaceView


def _inbox_frame():
    return pandas.DataFrame([
        {"from": "Alex Novak <alex@example.com>", "subject": "Invoice 2026", "date": "Mon, 2 Nov 2026 09:15:00 +0100", "unread": "yes", "id": "m1"},
        {"from": '"Ana" <ana@example.com>', "subject": "Lunch?", "date": "Sun, 1 Nov 2026 18:00:00 +0100", "unread": "", "id": "m2"},
        {"from": "news@shop.example", "subject": "Weekly deals", "date": "Fri, 30 Oct 2026 07:00:00 +0100", "unread": "", "id": "m3"},
    ], columns=data_views.MAIL_COLUMNS)


class HelperTests(unittest.TestCase):
    def test_sender_parsing_and_initials(self):
        self.assertEqual(mail_tabs.parse_sender("Alex Novak <alex@example.com>"), ("Alex Novak", "alex@example.com"))
        self.assertEqual(mail_tabs.parse_sender('"Ana" <ana@example.com>')[0], "Ana")
        self.assertEqual(mail_tabs.parse_sender("news@shop.example")[0], "news@shop.example")
        self.assertEqual(mail_tabs.initials("Alex Novak"), "AN")
        self.assertEqual(mail_tabs.initials("ana"), "AN")
        self.assertEqual(mail_tabs.initials(""), "?")

    def test_avatar_colour_is_stable_per_sender(self):
        self.assertEqual(mail_tabs.avatar_color("Alex").name(), mail_tabs.avatar_color("alex").name())

    def test_short_date_formats(self):
        now = datetime.fromisoformat("2026-11-02T12:00:00+01:00")
        self.assertEqual(mail_tabs.short_date("Mon, 2 Nov 2026 09:15:00 +0100", now), "09:15")
        self.assertEqual(mail_tabs.short_date("Sun, 1 Nov 2026 18:00:00 +0100", now), "1 Nov")
        self.assertEqual(mail_tabs.short_date("Sat, 1 Nov 2025 18:00:00 +0100", now), "1 Nov 2025")
        self.assertEqual(mail_tabs.short_date("garbage"), "garbage")

    def test_day_labels(self):
        from datetime import date

        today = date(2026, 11, 2)
        self.assertTrue(mail_tabs.day_label(today, today).startswith("Today"))
        self.assertTrue(mail_tabs.day_label(today + timedelta(days=1), today).startswith("Tomorrow"))
        self.assertTrue(mail_tabs.day_label(today + timedelta(days=3), today).startswith("Thu"))

    def test_human_size(self):
        self.assertEqual(mail_tabs.human_size(500), "500 B")
        self.assertEqual(mail_tabs.human_size(2048), "2 KB")
        self.assertEqual(mail_tabs.human_size(3 * 1024 * 1024), "3.0 MB")


class _Qt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv[:1] or ["arnox"])


class InboxTabTests(_Qt):
    def test_rows_unread_count_and_search(self):
        tab = InboxTab(_inbox_frame())
        self.assertEqual(tab.total_rows(), 3)
        self.assertEqual(tab.unread_count(), 1)
        tab.search("invoice")
        self.assertEqual(tab.visible_rows(), 1)
        self.assertEqual(tab.message_id_at(0), "m1")
        tab.search("ana")
        self.assertEqual(tab.visible_rows(), 1)
        tab.search("")
        self.assertEqual(tab.visible_rows(), 3)

    def test_opening_a_row_requests_that_message(self):
        tab = InboxTab(_inbox_frame())
        seen = []
        tab.open_requested.connect(seen.append)
        tab._open_index(tab._proxy.index(1, 0))
        self.assertEqual(seen, ["m2"])

    def test_empty_inbox_says_so(self):
        tab = InboxTab(pandas.DataFrame([], columns=data_views.MAIL_COLUMNS))
        tab.show()
        self.assertFalse(tab._empty.isHidden())

    def test_the_list_paints_without_error(self):
        tab = InboxTab(_inbox_frame())
        tab.resize(900, 500)
        tab.show()
        self.app.processEvents()
        self.assertFalse(tab.grab().isNull())


class CalendarTabTests(_Qt):
    def _frame(self):
        today = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)
        rows = [
            {"start": today.isoformat(), "end": (today + timedelta(hours=1)).isoformat(), "title": "Standup", "location": "Office", "id": "e1"},
            {"start": (today + timedelta(hours=5)).isoformat(), "end": (today + timedelta(hours=6)).isoformat(), "title": "Review", "location": "", "id": "e2"},
            {"start": (today + timedelta(days=1)).date().isoformat(), "end": (today + timedelta(days=2)).date().isoformat(), "title": "Holiday", "location": "", "id": "e3"},
        ]
        return pandas.DataFrame(rows, columns=data_views.AGENDA_COLUMNS)

    def test_events_are_grouped_under_day_headers(self):
        tab = CalendarTab(self._frame())
        self.assertEqual(tab.event_count(), 3)
        headers = tab.day_headers()
        self.assertEqual(len(headers), 2)
        self.assertTrue(headers[0].startswith("Today"))
        self.assertTrue(headers[1].startswith("Tomorrow"))

    def test_time_text_covers_timed_and_all_day_events(self):
        tab = CalendarTab(self._frame())
        times = [str(tab._model.index(r, 0).data(mail_tabs.ROLE_TIME)) for r in range(tab._model.rowCount())
                 if tab._model.index(r, 0).data(mail_tabs.ROLE_KIND) == "event"]
        self.assertEqual(times[0], "09:00 – 10:00")
        self.assertEqual(times[2], "All day")

    def test_search_filters_events_but_keeps_the_layout(self):
        tab = CalendarTab(self._frame())
        tab.search("standup")
        self.assertEqual(tab.visible_events(), 1)
        tab.search("office")
        self.assertEqual(tab.visible_events(), 1)

    def test_empty_agenda_and_paint(self):
        empty = CalendarTab(pandas.DataFrame([], columns=data_views.AGENDA_COLUMNS))
        empty.show()
        self.assertFalse(empty._empty.isHidden())
        tab = CalendarTab(self._frame())
        tab.resize(900, 500)
        tab.show()
        self.app.processEvents()
        self.assertFalse(tab.grab().isNull())


class MessageTabTests(_Qt):
    def _message(self, **extra):
        data = {
            "id": "m1", "from": "Alex Novak <alex@example.com>", "to": "arnel@example.com", "cc": "ana@example.com",
            "date": "Mon, 2 Nov 2026 09:15:00 +0100", "subject": "Invoice 2026", "unread": True,
            "body": "Hello Arnel,\n\nPlease find the invoice.\n\nAlex", "attachments": [],
        }
        data.update(extra)
        return data

    def test_message_is_laid_out_like_a_real_mail(self):
        tab = MessageTab(self._message())
        texts = [label.text() for label in tab.findChildren(mail_tabs.QLabel)]
        self.assertIn("Invoice 2026", texts)
        self.assertIn("Alex Novak", texts)
        self.assertIn("alex@example.com", texts)
        self.assertIn("UNREAD", texts)
        for key in ("FROM", "TO", "CC", "DATE"):
            self.assertIn(key, texts)
        self.assertIn("Please find the invoice.", tab.body_text())

    def test_missing_cc_row_is_not_shown(self):
        tab = MessageTab(self._message(cc=""))
        self.assertNotIn("CC", [label.text() for label in tab.findChildren(mail_tabs.QLabel)])

    def test_html_in_the_body_is_shown_as_text_never_rendered(self):
        tab = MessageTab(self._message(body='<script>alert(1)</script><a href="https://evil.test">click</a><img src="https://evil.test/x.png">'))
        self.assertIn("<script>alert(1)</script>", tab.body_text())
        self.assertIn('<a href="https://evil.test">click</a>', tab.body_text())
        self.assertFalse(tab._body.openLinks())
        self.assertFalse(tab._body.openExternalLinks())
        self.assertNotIn('<a href="https://evil.test">', tab._body.toHtml())

    def test_attachments_are_listed_with_sizes(self):
        tab = MessageTab(self._message(attachments=[{"filename": "invoice.pdf", "mime": "application/pdf", "size": 2048}]))
        texts = [label.text() for label in tab.findChildren(mail_tabs.QLabel)]
        chips = [button.text() for button in tab.attachment_buttons()]
        self.assertTrue(any("invoice.pdf" in text and "2 KB" in text for text in chips))
        self.assertTrue(any(text.startswith("ATTACHMENTS") for text in texts))

    def test_empty_body_has_a_placeholder(self):
        self.assertIn("no text", MessageTab(self._message(body="  ")).body_text())

    def test_tab_paints(self):
        tab = MessageTab(self._message())
        tab.resize(900, 600)
        tab.show()
        self.app.processEvents()
        self.assertFalse(tab.grab().isNull())


class WorkspaceMessageTests(_Qt):
    def test_message_tab_opens_once_and_is_reused(self):
        view = WorkspaceView()
        message = {"id": "m9", "from": "a@x.com", "to": "", "cc": "", "date": "", "subject": "Hello", "body": "x", "attachments": []}
        self.assertIn("shown", view.show_data("message", message, "Hello"))
        self.assertIn("already open", view.show_data("message", message, "Hello"))
        self.assertEqual(view.tab_titles(), ["Hello"])
        view.close_all()

    def test_opening_an_inbox_row_emits_a_message_request(self):
        view = WorkspaceView()
        seen = []
        view.message_requested.connect(seen.append)
        view.show_data("inbox", _inbox_frame(), "Inbox")
        view.current_widget()._open_index(view.current_widget()._proxy.index(0, 0))
        self.assertEqual(seen, ["m1"])
        view.close_all()


class FetchTests(unittest.TestCase):
    def _message(self):
        return {
            "id": "m1", "from": "Alex <a@x.com>", "to": "me@x.com", "cc": "", "date": "d", "subject": "Invoice",
            "body": "Total due 500", "unread": True, "attachments": [],
        }

    def test_message_by_sender_finds_the_newest_and_loads_it(self):
        with mock.patch.object(data_views, "find_message_id", return_value="m1") as find, \
             mock.patch.object(data_views, "gmail_message", return_value=self._message()):
            kind, payload, title, text = data_views.fetch("message", {"sender": "Alex"})
        find.assert_called_once_with("Alex", "")
        self.assertEqual((kind, title), ("message", "Invoice"))
        self.assertIn("Total due 500", text)
        self.assertIn("Untrusted", text)

    def test_message_by_number_uses_the_last_shown_inbox(self):
        with mock.patch.object(data_views, "_LAST_INBOX_IDS", ["a", "b", "c"]), \
             mock.patch.object(data_views, "gmail_message", return_value=self._message()) as load:
            data_views.fetch("message", {"number": 2})
        load.assert_called_once_with("b")

    def test_number_without_an_inbox_explains_what_to_do(self):
        with mock.patch.object(data_views, "_LAST_INBOX_IDS", []):
            with self.assertRaisesRegex(ValueError, "inbox first"):
                data_views.fetch("message", {"number": 3})

    def test_unknown_sender_and_unknown_view_are_readable_errors(self):
        with mock.patch.object(data_views, "find_message_id", return_value=""):
            with self.assertRaisesRegex(ValueError, "could not find"):
                data_views.fetch("message", {"sender": "Nobody"})
        with self.assertRaisesRegex(ValueError, "Unknown view"):
            data_views.fetch("passwords")

    def test_inbox_view_numbers_the_rows_for_the_assistant(self):
        frame = _inbox_frame()
        with mock.patch.dict(data_views.VIEWS, {"inbox": ("Inbox", lambda limit, days: frame)}):
            kind, payload, title, text = data_views.fetch("inbox")
        self.assertEqual(kind, "inbox")
        self.assertIn("1. Alex Novak: Invoice 2026 (unread)", text)
        self.assertIn("2. Ana: Lunch?", text)

    def test_gmail_message_returns_plain_data(self):
        service = mock.MagicMock()
        service.users().messages().get().execute.return_value = {
            "id": "m1", "labelIds": ["UNREAD"],
            "payload": {"headers": [
                {"name": "From", "value": "A <a@x.com>"}, {"name": "To", "value": "me@x.com"},
                {"name": "Subject", "value": "Hi"}, {"name": "Date", "value": "d"},
            ], "mimeType": "text/plain", "body": {"data": "SGVsbG8"}, "parts": [
                {"filename": "f.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "x", "size": 10}},
            ]},
        }
        with mock.patch.object(email_control, "_gmail_service", return_value=service):
            message = data_views.gmail_message("m1")
        self.assertEqual(message["subject"], "Hi")
        self.assertTrue(message["unread"])
        self.assertEqual(message["attachments"][0]["filename"], "f.pdf")


if __name__ == "__main__":
    unittest.main()
