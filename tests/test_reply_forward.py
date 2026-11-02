import asyncio
import base64
import os
import sys
import time
import unittest
from email import message_from_bytes, policy
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtWidgets import QApplication

import main
import ui  # noqa: F401
from actions import email_control
from ui_workspace import data_views
from ui_workspace.workspace_view import WorkspaceView


ORIGINAL = {
    "id": "m1", "thread_id": "t77", "message_id_header": "<orig@mail>", "references": "<root@mail>", "reply_to": "",
    "trashed": False, "from": "Alex Novak <alex@example.com>", "to": "me@example.com", "cc": "",
    "date": "Mon, 2 Nov 2026 09:15:00 +0100", "subject": "Invoice 2026", "body": "Please pay by Friday.",
    "unread": False,
    "attachments": [{"filename": "Invoice.pdf", "mime": "application/pdf", "size": 100, "attachment_id": "att1", "message_id": "m1"}],
}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


class _Base(unittest.TestCase):
    def setUp(self):
        self.service = mock.MagicMock()
        self.service.users().messages().send().execute.return_value = {"id": "sent1"}
        self.service.users().messages().send.reset_mock()
        self.service.users().messages().attachments().get().execute.return_value = {"data": _b64(b"%PDF-1.4 fake")}
        for patcher in (
            mock.patch.object(email_control, "_gmail_service", return_value=self.service),
            mock.patch.object(email_control, "_LAST_READ_MESSAGE_ID", "m1"),
            mock.patch.object(data_views, "gmail_message", return_value=dict(ORIGINAL)),
            mock.patch.object(email_control, "_gmail_web_prepare", side_effect=AssertionError("no browser")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        email_control._clear_pending_email()
        self.addCleanup(email_control._clear_pending_email)

    def _sent_mail(self):
        call = self.service.users().messages().send.call_args.kwargs["body"]
        return call, message_from_bytes(base64.urlsafe_b64decode(call["raw"]), policy=policy.default)


class ReplyTests(_Base):
    def test_reply_needs_no_address_and_fills_everything_from_the_mail(self):
        result = email_control.email_control({"action": "reply", "body": "Thanks, paying Thursday."})
        self.assertTrue(result.startswith("EMAIL_APPROVAL_REQUIRED|"), result)
        self.assertIn("To: alex@example.com", result)
        self.assertIn("Subject: Re: Invoice 2026", result)
        self.assertIn("Thanks, paying Thursday.", result)
        pending = email_control._get_pending_email()
        self.assertEqual(pending["thread_id"], "t77")
        self.assertEqual(pending["in_reply_to"], "<orig@mail>")
        self.assertEqual(pending["delivery"], "gmail_api")

    def test_saying_send_it_sends_the_reply_in_the_same_thread(self):
        email_control.email_control({"action": "reply", "body": "Thanks."})
        self.service.users().messages().send.assert_not_called()   # drafting sends nothing
        sent = email_control.email_control({"action": "approve"})
        self.assertIn("sent through Gmail", sent)
        body, mail = self._sent_mail()
        self.assertEqual(body["threadId"], "t77")
        self.assertEqual(mail["To"], "alex@example.com")
        self.assertEqual(mail["In-Reply-To"], "<orig@mail>")
        self.assertEqual(mail["References"], "<root@mail> <orig@mail>")
        self.assertTrue(mail.get_body(("plain",)).get_content().startswith("Thanks."))
        self.assertEqual(email_control._get_pending_email(), {})

    def test_reply_without_text_asks_what_to_say(self):
        self.assertIn("what the reply should say", email_control.email_control({"action": "reply"}))
        self.assertEqual(email_control._get_pending_email(), {})

    def test_reply_without_an_opened_mail_asks_to_open_one(self):
        with mock.patch.object(email_control, "_LAST_READ_MESSAGE_ID", ""):
            self.assertIn("Open or read the mail", email_control.email_control({"action": "reply", "body": "x"}))

    def test_reply_to_a_specific_message_id_and_a_different_recipient(self):
        email_control.email_control({"action": "reply", "body": "Hi", "message_id": "m1", "to": "boss@example.com", "cc": "ana@example.com"})
        pending = email_control._get_pending_email()
        self.assertEqual((pending["to"], pending["cc"]), ("boss@example.com", "ana@example.com"))

    def test_missing_recipient_error_points_to_reply_instead_of_giving_up(self):
        result = email_control.email_control({"action": "prepare", "subject": "x", "body": "y"})
        self.assertIn("action=reply", result)

    def test_gmail_failure_while_reading_the_original_is_explained(self):
        with mock.patch.object(data_views, "gmail_message", side_effect=RuntimeError("offline")):
            result = email_control.email_control({"action": "reply", "body": "x"})
        self.assertIn("failed", result)


class ForwardTests(_Base):
    def test_forward_needs_a_recipient(self):
        self.assertIn("who to forward", email_control.email_control({"action": "forward"}))

    def test_forward_drafts_the_original_with_its_attachments(self):
        result = email_control.email_control({"action": "forward", "to": "bob@example.com", "body": "FYI"})
        self.assertTrue(result.startswith("EMAIL_APPROVAL_REQUIRED|"), result)
        self.assertIn("Subject: Fwd: Invoice 2026", result)
        self.assertIn("Attachments: Invoice.pdf", result)
        self.assertIn("Please pay by Friday.", result)

    def test_saying_send_it_sends_the_forward_with_the_file(self):
        email_control.email_control({"action": "forward", "to": "bob@example.com"})
        sent = email_control.email_control({"action": "approve"})
        self.assertIn("sent through Gmail", sent)
        body, mail = self._sent_mail()
        self.assertNotIn("threadId", body)
        names = [part.get_filename() for part in mail.walk() if part.get_filename()]
        self.assertEqual(names, ["Invoice.pdf"])
        self.assertEqual(mail["To"], "bob@example.com")

    def test_a_forward_without_a_recipient_can_get_one_when_approving(self):
        data_views._register_pending(data_views.forward_payload(dict(ORIGINAL)))
        self.assertIn("no recipient", email_control.email_control({"action": "approve"}))
        sent = email_control.email_control({"action": "approve", "to": "bob@example.com"})
        self.assertIn("sent through Gmail", sent)

    def test_executable_attachments_are_not_forwarded(self):
        bad = dict(ORIGINAL, attachments=[{"filename": "run.exe", "mime": "x", "size": 1, "attachment_id": "a", "message_id": "m1"}])
        with mock.patch.object(data_views, "gmail_message", return_value=bad):
            email_control.email_control({"action": "forward", "to": "bob@example.com"})
        result = email_control.email_control({"action": "approve"})
        self.assertIn("will not be forwarded", result)
        self.service.users().messages().send.assert_not_called()


class SyncWithTheWindowTests(_Base):
    def test_edits_made_in_the_window_are_what_gets_sent(self):
        email_control.email_control({"action": "reply", "body": "Original draft."})
        email_control.update_pending_from_compose({
            "to": "alex@example.com, ana@example.com", "cc": "", "subject": "Re: Invoice 2026 (edited)",
            "body": "My edited answer.", "attachments": [],
        })
        email_control.email_control({"action": "approve"})
        _, mail = self._sent_mail()
        self.assertEqual(mail["Subject"], "Re: Invoice 2026 (edited)")
        self.assertIn("ana@example.com", mail["To"])
        self.assertIn("My edited answer.", mail.get_body(("plain",)).get_content())

    def test_removing_an_attachment_in_the_window_removes_it_from_the_forward(self):
        email_control.email_control({"action": "forward", "to": "bob@example.com"})
        email_control.update_pending_from_compose({"to": "bob@example.com", "attachments": []})
        email_control.email_control({"action": "approve"})
        _, mail = self._sent_mail()
        self.assertEqual([p.get_filename() for p in mail.walk() if p.get_filename()], [])

    def test_blank_window_fields_do_not_wipe_the_draft(self):
        email_control.email_control({"action": "reply", "body": "Keep me."})
        email_control.update_pending_from_compose({"to": "", "cc": "", "subject": "", "body": "   "})
        pending = email_control._get_pending_email()
        self.assertEqual(pending["to"], "alex@example.com")
        self.assertIn("Keep me.", pending["body"])


class DispatcherFlowTests(_Base):
    def _live(self, **extra):
        live = main.JarvisLive.__new__(main.JarvisLive)
        self.shown, self.closed = [], []
        live.ui = SimpleNamespace(
            muted=True, current_file=None, set_state=lambda *_: None, write_log=lambda *_: None,
            workspace_show_data=lambda kind, payload, title: self.shown.append((kind, title, payload)),
            workspace_compose_fields=lambda: self.window_fields,
            workspace_close_compose=lambda: self.closed.append(True) or 1,
            **extra,
        )
        live.cloud_safe = False
        self.window_fields = None
        return live

    def _run(self, live, name, args):
        return asyncio.run(live._execute_tool(SimpleNamespace(name=name, args=args, id="1"))).response["result"]

    def test_voice_flow_reply_then_ok_send_it(self):
        live = self._live()
        result = self._run(live, "email_control", {"action": "reply", "body": "Thanks, paying Thursday."})
        self.assertTrue(result.startswith("EMAIL_APPROVAL_REQUIRED"))
        self.assertEqual([(k, t) for k, t, _ in self.shown], [("compose", "Reply")])
        self.assertEqual(self.shown[0][2]["to"], "alex@example.com")
        self.assertEqual(self.closed, [])
        sent = self._run(live, "email_control", {"action": "approve"})
        self.assertIn("sent through Gmail", sent)
        self.assertEqual(self.closed, [True])   # the draft window closes itself
        _, mail = self._sent_mail()
        self.assertEqual(mail["To"], "alex@example.com")

    def test_voice_send_uses_the_edits_the_user_made_in_the_window(self):
        live = self._live()
        self._run(live, "email_control", {"action": "reply", "body": "Draft."})
        self.window_fields = {"to": "alex@example.com", "cc": "", "subject": "Re: Invoice 2026", "body": "Edited by hand.", "attachments": []}
        self._run(live, "email_control", {"action": "approve"})
        _, mail = self._sent_mail()
        self.assertIn("Edited by hand.", mail.get_body(("plain",)).get_content())

    def test_forward_flow_by_voice(self):
        live = self._live()
        self._run(live, "email_control", {"action": "forward", "to": "bob@example.com"})
        self.assertEqual([(k, t) for k, t, _ in self.shown], [("compose", "Forward")])
        self.assertEqual([a["filename"] for a in self.shown[0][2]["attachments"]], ["Invoice.pdf"])
        self.assertIn("sent through Gmail", self._run(live, "email_control", {"action": "approve"}))

    def test_cancel_by_voice_closes_the_draft_and_sends_nothing(self):
        live = self._live()
        self._run(live, "email_control", {"action": "reply", "body": "Draft."})
        result = self._run(live, "email_control", {"action": "cancel"})
        self.assertIn("cancelled", result)
        self.assertEqual(self.closed, [True])
        self.service.users().messages().send.assert_not_called()

    def test_a_failed_send_keeps_the_draft_window_open(self):
        live = self._live()
        self._run(live, "email_control", {"action": "reply", "body": "Draft."})
        self.service.users().messages().send().execute.side_effect = RuntimeError("boom")
        result = self._run(live, "email_control", {"action": "approve"})
        self.assertNotIn("Email sent", result)
        self.assertEqual(self.closed, [])

    def test_everything_works_with_the_workspace_hidden_or_missing(self):
        live = main.JarvisLive.__new__(main.JarvisLive)
        live.ui = SimpleNamespace(muted=True, current_file=None, set_state=lambda *_: None, write_log=lambda *_: None)
        live.cloud_safe = False
        self.assertTrue(self._run(live, "email_control", {"action": "reply", "body": "Hi"}).startswith("EMAIL_APPROVAL_REQUIRED"))
        self.assertIn("sent through Gmail", self._run(live, "email_control", {"action": "approve"}))

    def test_workspace_reply_view_is_also_the_pending_draft_so_send_it_works(self):
        kind, draft, title, text = data_views.fetch("reply", {"body": "On it."})
        self.assertEqual((kind, title), ("compose", "Reply"))
        self.assertIn("email_control action=approve", text)
        self.assertIn("sent through Gmail", email_control.email_control({"action": "approve"}))
        body, _ = self._sent_mail()
        self.assertEqual(body["threadId"], "t77")

    def test_workspace_forward_view_is_also_the_pending_draft(self):
        data_views.fetch("forward", {"to": "bob@example.com"})
        self.assertIn("sent through Gmail", email_control.email_control({"action": "approve"}))

    def test_tool_schema_offers_reply_and_forward_and_explains_no_address_is_needed(self):
        decl = next(d for d in main.TOOL_DECLARATIONS if d["name"] == "email_control")
        enum = decl["parameters"]["properties"]["action"]["enum"]
        self.assertIn("reply", enum)
        self.assertIn("forward", enum)
        self.assertIn("no address is needed", decl["description"])


class _Qt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv[:1] or ["arnox"])


class WindowTests(_Qt):
    def test_workspace_exposes_the_live_fields_and_closes_drafts(self):
        view = WorkspaceView()
        self.addCleanup(view.close_all)
        self.assertIsNone(view.compose_fields())
        view.show_data("compose", {"to": "a@x.com", "subject": "S", "body": "B", "attachments": [{"filename": "f.pdf"}]}, "Reply")
        view.current_widget()._body.setPlainText("Changed")
        fields = view.compose_fields()
        self.assertEqual((fields["to"], fields["body"]), ("a@x.com", "Changed"))
        self.assertEqual([a["filename"] for a in fields["attachments"]], ["f.pdf"])
        self.assertEqual(view.close_compose(), 1)
        self.assertEqual(view.tab_count(), 0)

    def test_sending_from_the_window_clears_the_pending_draft(self):
        email_control._set_pending_email({"to": "a@x.com", "subject": "S", "body": "B", "provider": "gmail", "delivery": "gmail_api"})
        view = WorkspaceView()
        self.addCleanup(view.close_all)
        view.show_data("compose", {"to": "a@x.com", "subject": "S", "body": "B"}, "New mail")
        with mock.patch.object(email_control, "send_mail_now", return_value=(True, "Email sent through Gmail to a@x.com.")):
            view.current_widget()._send.click()
            end = time.time() + 3
            while time.time() < end and view.tab_count():
                self.app.processEvents()
                time.sleep(0.01)
        self.assertEqual(email_control._get_pending_email(), {})
        self.assertEqual(view.tab_count(), 0)


if __name__ == "__main__":
    unittest.main()
