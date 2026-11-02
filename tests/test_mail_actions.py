import base64
import os
import sys
import time
import unittest
from email import message_from_bytes
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
import pandas
from PyQt6.QtWidgets import QApplication

import ui  # noqa: F401
from actions import email_control
from ui_workspace import data_views
from ui_workspace.mail_tabs import ROLE_NAME, ComposeTab, InboxTab, MessageTab
from ui_workspace.workspace_view import WorkspaceView


def _sent_headers(service):
    raw = service.users().messages().send.call_args.kwargs["body"]["raw"]
    return message_from_bytes(base64.urlsafe_b64decode(raw))


class SendTests(unittest.TestCase):
    def setUp(self):
        self.service = mock.MagicMock()
        self.service.users().messages().send().execute.return_value = {"id": "sent1"}
        self.service.users().messages().send.reset_mock()   # the line above is setup, not a real send
        patcher = mock.patch.object(email_control, "_gmail_service", return_value=self.service)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_prepare_never_opens_a_browser_and_approve_sends_through_the_api(self):
        with mock.patch.object(email_control, "_gmail_web_prepare", side_effect=AssertionError("no browser")):
            result = email_control.email_control({
                "action": "prepare", "to": "alex@example.com", "subject": "Hi", "body": "Hello Alex",
            })
        self.assertIn("EMAIL_APPROVAL_REQUIRED", result)
        self.assertIn("Nothing opened outside the app", result)
        self.assertIn("Nothing has been sent", result)
        self.service.users().messages().send.assert_not_called()   # prepare only holds the draft
        sent = email_control.email_control({"action": "approve"})
        self.assertIn("sent through Gmail", sent)
        mail = _sent_headers(self.service)
        self.assertEqual(mail["To"], "alex@example.com")
        self.assertEqual(mail["Subject"], "Hi")

    def test_reply_keeps_the_thread(self):
        ok, message = email_control.send_mail_now(
            "alex@example.com", "Re: Invoice", "Thanks", thread_id="t42",
            in_reply_to="<abc@mail>", references="<root@mail>",
        )
        self.assertTrue(ok, message)
        body = self.service.users().messages().send.call_args.kwargs["body"]
        self.assertEqual(body["threadId"], "t42")
        mail = _sent_headers(self.service)
        self.assertEqual(mail["In-Reply-To"], "<abc@mail>")
        self.assertEqual(mail["References"], "<root@mail> <abc@mail>")

    def test_send_validates_before_calling_gmail(self):
        self.assertFalse(email_control.send_mail_now("not-an-address", "s", "body")[0])
        self.assertFalse(email_control.send_mail_now("a@example.com", "s", "   ")[0])
        self.service.users().messages().send.assert_not_called()

    def test_missing_subject_gets_a_placeholder_and_cc_is_kept(self):
        ok, _ = email_control.send_mail_now("a@example.com", "", "Hello", cc="b@example.com")
        self.assertTrue(ok)
        mail = _sent_headers(self.service)
        self.assertEqual(mail["Subject"], "(No subject)")
        self.assertEqual(mail["Cc"], "b@example.com")

    def test_gmail_failure_is_returned_not_raised(self):
        self.service.users().messages().send().execute.side_effect = RuntimeError("boom")
        ok, message = email_control.send_mail_now("a@example.com", "s", "body")
        self.assertFalse(ok)
        self.assertIn("failed", message)


class TrashTests(unittest.TestCase):
    def setUp(self):
        self.service = mock.MagicMock()
        patcher = mock.patch.object(email_control, "_gmail_service", return_value=self.service)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_trash_and_restore(self):
        self.assertEqual(email_control.set_trash("m1", True)[0], True)
        self.service.users().messages().trash.assert_called_with(userId="me", id="m1")
        self.assertIn("Restored", email_control.set_trash("m1", False)[1])
        self.service.users().messages().untrash.assert_called_with(userId="me", id="m1")

    def test_trash_failure_is_reported(self):
        self.service.users().messages().trash().execute.side_effect = RuntimeError("nope")
        self.assertFalse(email_control.set_trash("m1", True)[0])

    def test_delete_this_mail_uses_the_mail_just_opened(self):
        with mock.patch.object(email_control, "_LAST_READ_MESSAGE_ID", "m9"):
            email_control.email_control({"action": "trash"})
        self.service.users().messages().trash.assert_called_with(userId="me", id="m9")
        with mock.patch.object(email_control, "_LAST_READ_MESSAGE_ID", "m9"):
            email_control.email_control({"action": "untrash"})
        self.service.users().messages().untrash.assert_called_with(userId="me", id="m9")

    def test_explicit_id_wins_over_the_last_read_mail(self):
        with mock.patch.object(email_control, "_LAST_READ_MESSAGE_ID", "m9"):
            email_control.email_control({"action": "trash", "message_id": "m2"})
        self.service.users().messages().trash.assert_called_with(userId="me", id="m2")


class DataTests(unittest.TestCase):
    def test_trash_view_asks_gmail_to_include_trash(self):
        service = mock.MagicMock()
        service.users().messages().list().execute.return_value = {"messages": []}
        with mock.patch.object(email_control, "_gmail_service", return_value=service):
            data_views.VIEWS["trash"][1](10, 14)
        self.assertTrue(service.users().messages().list.call_args.kwargs["includeSpamTrash"])
        with mock.patch.object(email_control, "_gmail_service", return_value=service):
            data_views.VIEWS["inbox"][1](10, 14)
        self.assertFalse(service.users().messages().list.call_args.kwargs["includeSpamTrash"])

    def test_sent_and_trash_return_their_own_kind(self):
        frame = pandas.DataFrame([], columns=data_views.MAIL_COLUMNS)
        with mock.patch.dict(data_views.VIEWS, {"sent": ("Sent", lambda l, d: frame), "trash": ("Trash", lambda l, d: frame)}):
            self.assertEqual(data_views.fetch("sent")[0], "sent")
            self.assertEqual(data_views.fetch("trash")[0], "trash")

    def test_reply_payload_quotes_the_original_and_keeps_the_thread(self):
        original = {
            "from": "Alex <alex@example.com>", "reply_to": "", "subject": "Invoice", "date": "Mon",
            "body": "Please pay\nby Friday", "thread_id": "t1", "message_id_header": "<m@x>", "references": "<r@x>",
        }
        draft = data_views.reply_payload(original, body="Will do")
        self.assertEqual(draft["to"], "alex@example.com")
        self.assertEqual(draft["subject"], "Re: Invoice")
        self.assertTrue(draft["body"].startswith("Will do"))
        self.assertIn("> Please pay", draft["body"])
        self.assertEqual((draft["thread_id"], draft["in_reply_to"], draft["references"]), ("t1", "<m@x>", "<r@x>"))

    def test_reply_does_not_double_the_re_prefix_and_prefers_reply_to(self):
        draft = data_views.reply_payload({"from": "a@x.com", "reply_to": "Boss <boss@x.com>", "subject": "Re: Plan", "body": ""})
        self.assertEqual(draft["subject"], "Re: Plan")
        self.assertEqual(draft["to"], "boss@x.com")

    def test_compose_view_returns_a_draft_and_says_nothing_was_sent(self):
        kind, payload, title, text = data_views.fetch("compose", {"to": "a@x.com", "subject": "Hi", "body": "Hello"})
        self.assertEqual((kind, title, payload["to"]), ("compose", "New mail", "a@x.com"))
        self.assertIn("Nothing has been sent", text)

    def test_reply_without_an_open_mail_explains_what_to_do(self):
        with mock.patch.object(email_control, "_LAST_READ_MESSAGE_ID", ""):
            with self.assertRaisesRegex(ValueError, "Open or read"):
                data_views.fetch("reply", {})


class _Qt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv[:1] or ["arnox"])

    def _wait(self, condition, seconds=3.0):
        end = time.time() + seconds
        while time.time() < end and not condition():
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()


def _message(**extra):
    data = {
        "id": "m1", "thread_id": "t1", "message_id_header": "<m@x>", "references": "", "reply_to": "", "trashed": False,
        "from": "Alex <alex@example.com>", "to": "me@x.com", "cc": "", "date": "Mon, 2 Nov 2026 09:15:00 +0100",
        "subject": "Invoice", "body": "Please pay", "unread": False, "attachments": [],
    }
    data.update(extra)
    return data


class TabTests(_Qt):
    def test_sent_folder_shows_the_recipient(self):
        frame = pandas.DataFrame([{"from": "me@x.com", "to": "Ana <ana@x.com>", "subject": "Hi", "date": "d", "unread": "", "id": "1"}],
                                 columns=data_views.MAIL_COLUMNS)
        tab = InboxTab(frame, "Sent", mode="sent")
        self.assertEqual(tab._model.index(0, 0).data(ROLE_NAME), "Ana")

    def test_remove_message_updates_counts(self):
        frame = pandas.DataFrame([
            {"from": "a@x.com", "to": "", "subject": "1", "date": "d", "unread": "yes", "id": "a"},
            {"from": "b@x.com", "to": "", "subject": "2", "date": "d", "unread": "", "id": "b"},
        ], columns=data_views.MAIL_COLUMNS)
        tab = InboxTab(frame)
        tab.remove_message("a")
        self.assertEqual((tab.total_rows(), tab.unread_count()), (1, 0))

    def test_reader_shows_delete_for_normal_mail_and_restore_for_trashed(self):
        normal = MessageTab(_message())
        normal.show()
        self.assertTrue(normal._trash.isVisible())
        self.assertFalse(normal._restore.isVisible())
        trashed = MessageTab(_message(trashed=True))
        trashed.show()
        self.assertFalse(trashed._trash.isVisible())
        self.assertTrue(trashed._restore.isVisible())

    def test_reader_buttons_emit_their_requests(self):
        tab = MessageTab(_message())
        seen = []
        tab.trash_requested.connect(lambda i: seen.append(("trash", i)))
        tab.reply_requested.connect(lambda m: seen.append(("reply", m["id"])))
        tab._reply.click()
        tab._trash.click()
        self.assertEqual(seen, [("reply", "m1"), ("trash", "m1")])
        self.assertFalse(tab._trash.isEnabled())
        tab.mail_result(False, "failed")
        self.assertTrue(tab._trash.isEnabled())

    def test_compose_validates_before_asking_to_send(self):
        tab = ComposeTab({"to": "", "subject": "", "body": ""})
        seen = []
        tab.send_requested.connect(seen.append)
        tab._send.click()
        self.assertIn("recipient", tab._status.text())
        tab._to.setText("a@x.com")
        tab._send.click()
        self.assertIn("empty", tab._status.text())
        tab._body.setPlainText("Hello")
        tab._send.click()
        self.assertEqual(seen[0]["to"], "a@x.com")
        self.assertFalse(tab._send.isEnabled())

    def test_compose_keeps_the_reply_thread_in_what_it_sends(self):
        tab = ComposeTab({"to": "a@x.com", "subject": "Re: x", "body": "Hi", "thread_id": "t9", "in_reply_to": "<z@x>", "references": ""})
        seen = []
        tab.send_requested.connect(seen.append)
        tab._send.click()
        self.assertEqual((seen[0]["thread_id"], seen[0]["in_reply_to"]), ("t9", "<z@x>"))


class WorkspaceFlowTests(_Qt):
    def setUp(self):
        self.view = WorkspaceView()
        self.addCleanup(self.view.close_all)

    def test_reply_button_opens_a_prefilled_compose_tab(self):
        self.view.show_data("message", _message(), "Invoice")
        self.view.current_widget()._reply.click()
        self.assertEqual(self.view.tab_titles(), ["Invoice", "Reply"])
        fields = self.view.current_widget().fields()
        self.assertEqual(fields["to"], "alex@example.com")
        self.assertEqual(fields["subject"], "Re: Invoice")
        self.assertIn("> Please pay", fields["body"])

    def test_send_goes_through_the_api_then_closes_the_compose_tab(self):
        self.view.show_data("compose", {"to": "a@x.com", "subject": "Hi", "body": "Hello", "thread_id": "", "in_reply_to": "", "references": ""}, "New mail")
        with mock.patch.object(email_control, "send_mail_now", return_value=(True, "Email sent through Gmail to a@x.com.")) as send:
            self.view.current_widget()._send.click()
            self._wait(lambda: self.view.tab_count() == 0)
        send.assert_called_once()
        self.assertEqual(self.view.tab_count(), 0)
        self.assertIn("sent through Gmail", self.view._flash_label.text())

    def test_failed_send_keeps_the_draft_and_explains(self):
        self.view.show_data("compose", {"to": "a@x.com", "subject": "Hi", "body": "Hello", "thread_id": "", "in_reply_to": "", "references": ""}, "New mail")
        tab = self.view.current_widget()
        with mock.patch.object(email_control, "send_mail_now", return_value=(False, "Gmail is rate limiting.")):
            tab._send.click()
            self._wait(lambda: "rate limiting" in tab._status.text())
        self.assertEqual(self.view.tab_count(), 1)
        self.assertTrue(tab._send.isEnabled())

    def test_delete_moves_to_trash_removes_the_row_and_closes_the_reader(self):
        frame = pandas.DataFrame([{"from": "a@x.com", "to": "", "subject": "1", "date": "d", "unread": "", "id": "m1"}], columns=data_views.MAIL_COLUMNS)
        self.view.show_data("inbox", frame, "Inbox")
        self.view.show_data("message", _message(), "Invoice")
        with mock.patch.object(email_control, "set_trash", return_value=(True, "Moved to the trash. You can restore it.")) as trash:
            self.view.current_widget()._trash.click()
            self._wait(lambda: self.view.tab_titles() == ["Inbox"])
        trash.assert_called_once_with("m1", True)
        self.assertEqual(self.view.tab_titles(), ["Inbox"])
        self.assertEqual(self.view.current_widget().total_rows(), 0)
        self.assertIn("trash", self.view._flash_label.text())

    def test_restore_from_the_trash_reader(self):
        self.view.show_data("message", _message(trashed=True), "Invoice")
        with mock.patch.object(email_control, "set_trash", return_value=(True, "Restored from the trash.")) as trash:
            self.view.current_widget()._restore.click()
            self._wait(lambda: self.view.tab_count() == 0)
        trash.assert_called_once_with("m1", False)

    def test_failed_delete_leaves_the_reader_open_with_the_reason(self):
        self.view.show_data("message", _message(), "Invoice")
        tab = self.view.current_widget()
        with mock.patch.object(email_control, "set_trash", return_value=(False, "Permission denied.")):
            tab._trash.click()
            self._wait(lambda: "denied" in tab._status.text())
        self.assertEqual(self.view.tab_count(), 1)
        self.assertTrue(tab._trash.isEnabled())

    def test_new_mail_button_in_the_inbox_opens_a_blank_compose_tab(self):
        frame = pandas.DataFrame([], columns=data_views.MAIL_COLUMNS)
        self.view.show_data("inbox", frame, "Inbox")
        self.view.current_widget()._compose.click()
        self.assertEqual(self.view.tab_titles(), ["Inbox", "New mail"])

    def test_discard_closes_the_compose_tab_without_sending(self):
        self.view.show_data("compose", {"to": "", "subject": "", "body": "", "thread_id": "", "in_reply_to": "", "references": ""}, "New mail")
        with mock.patch.object(email_control, "send_mail_now", side_effect=AssertionError("must not send")):
            self.view.current_widget()._discard.click()
        self.assertEqual(self.view.tab_count(), 0)


if __name__ == "__main__":
    unittest.main()
