import asyncio
import base64
import os
import shutil
import sys
import tempfile
import time
import unittest
from email import message_from_bytes
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
import pandas
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

import main
import ui  # noqa: F401
from actions import email_control
from core import workspace_router as router
from ui_workspace import data_views
from ui_workspace.mail_tabs import ComposeTab, InboxTab, MessageTab
from ui_workspace.viewer_tabs import ImageTab, TextTab
from ui_workspace.workspace_view import WorkspaceView


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _frame(rows=3, token="", start=0, query="in:inbox", title="Inbox"):
    data = [{"from": f"P{n} <p{n}@x.com>", "to": "", "subject": f"Subject {n}", "date": "d",
             "unread": "yes" if n == 0 else "", "id": f"id{start + n}"} for n in range(rows)]
    frame = pandas.DataFrame(data, columns=data_views.MAIL_COLUMNS)
    frame.attrs.update({"query": query, "include_trash": False, "next_token": token, "title": title})
    return frame


class RestoreTests(unittest.TestCase):
    def test_restore_puts_the_mail_back_in_the_inbox(self):
        service = mock.MagicMock()
        with mock.patch.object(email_control, "_gmail_service", return_value=service):
            ok, message = email_control.set_trash("m1", False)
        self.assertTrue(ok)
        self.assertIn("inbox", message.lower())
        service.users().messages().untrash.assert_called_with(userId="me", id="m1")
        service.users().messages().modify.assert_called_with(
            userId="me", id="m1", body={"addLabelIds": ["INBOX"], "removeLabelIds": []}
        )

    def test_trashing_does_not_touch_labels(self):
        service = mock.MagicMock()
        with mock.patch.object(email_control, "_gmail_service", return_value=service):
            email_control.set_trash("m1", True)
        service.users().messages().modify.assert_not_called()


class PagingTests(unittest.TestCase):
    def _service(self, ids, token):
        service = mock.MagicMock()
        service.users().messages().list().execute.return_value = {"messages": [{"id": i} for i in ids], "nextPageToken": token}
        service.users().messages().list.reset_mock()

        def get(userId, id, format, metadataHeaders=None):
            call = mock.MagicMock()
            call.execute.return_value = {"id": id, "labelIds": [], "payload": {"headers": [{"name": "Subject", "value": id}]}}
            return call

        service.users().messages().get.side_effect = get
        return service

    def test_a_page_returns_its_next_token_and_remembers_the_list(self):
        with mock.patch.object(email_control, "_gmail_service", return_value=self._service(["a", "b"], "T2")):
            frame = data_views.gmail_frame("in:inbox", 2, title="Inbox")
        self.assertEqual(frame.attrs["next_token"], "T2")
        self.assertEqual(data_views._LAST_LIST["token"], "T2")
        self.assertEqual(data_views.last_inbox_id(2), "b")

    def test_next_page_uses_the_token_and_extends_the_numbering(self):
        service = self._service(["a", "b"], "T2")
        with mock.patch.object(email_control, "_gmail_service", return_value=service):
            data_views.gmail_frame("in:inbox", 2, title="Inbox")
            service.users().messages().list().execute.return_value = {"messages": [{"id": "c"}], "nextPageToken": ""}
            frame = data_views.gmail_frame("in:inbox", 2, token="T2", title="Inbox")
        self.assertEqual(service.users().messages().list.call_args.kwargs["pageToken"], "T2")
        self.assertEqual(data_views.last_inbox_id(3), "c")
        self.assertEqual(frame.attrs["next_token"], "")

    def test_more_continues_the_list_just_shown(self):
        with mock.patch.dict(data_views._LAST_LIST, {"query": "in:inbox", "include_trash": False, "token": "T9", "title": "Inbox"}), \
             mock.patch.object(data_views, "gmail_frame", return_value=_frame(2, start=25)) as page:
            kind, payload, title, text = data_views.fetch("more")
        page.assert_called_once_with("in:inbox", data_views.PAGE_SIZE, False, "T9", "Inbox")
        self.assertEqual(kind, "more")
        self.assertIn("more message", text)

    def test_more_without_a_next_page_explains(self):
        with mock.patch.dict(data_views._LAST_LIST, {"query": "", "include_trash": False, "token": "", "title": ""}):
            with self.assertRaisesRegex(ValueError, "no more"):
                data_views.fetch("more")

    def test_search_covers_the_whole_folder_and_names_the_scope(self):
        with mock.patch.object(data_views, "gmail_frame", return_value=_frame(2)) as page:
            kind, payload, title, text = data_views.fetch("search", {"query": "from:alex invoice"})
        self.assertEqual(page.call_args.args[0], "in:inbox from:alex invoice")
        self.assertEqual(kind, "search")
        self.assertIn("across the whole inbox", text)
        with mock.patch.object(data_views, "gmail_frame", return_value=_frame(1)) as page:
            data_views.fetch("search", {"query": "x", "folder": "trash"})
        self.assertEqual(page.call_args.args[0], "in:trash x")
        self.assertTrue(page.call_args.kwargs["include_trash"])
        with mock.patch.object(data_views, "gmail_frame", return_value=_frame(1)) as page:
            data_views.fetch("search", {"query": "x", "folder": "all"})
        self.assertEqual(page.call_args.args[0], "x")

    def test_search_without_a_query_asks_for_one(self):
        with self.assertRaisesRegex(ValueError, "what to search"):
            data_views.fetch("search", {})


class ForwardSendTests(unittest.TestCase):
    def setUp(self):
        self.service = mock.MagicMock()
        self.service.users().messages().send().execute.return_value = {"id": "sent"}
        self.service.users().messages().send.reset_mock()
        patcher = mock.patch.object(email_control, "_gmail_service", return_value=self.service)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_forward_sends_the_attachments_inside_the_mail(self):
        self.service.users().messages().attachments().get().execute.return_value = {"data": _b64(b"%PDF fake")}
        ok, message = email_control.send_mail_now(
            "bob@example.com", "Fwd: Invoice", "See below", attachments=[
                {"filename": "invoice.pdf", "mime": "application/pdf", "attachment_id": "att1", "message_id": "m1"},
            ])
        self.assertTrue(ok, message)
        raw = self.service.users().messages().send.call_args.kwargs["body"]["raw"]
        mail = message_from_bytes(base64.urlsafe_b64decode(raw))
        names = [part.get_filename() for part in mail.walk() if part.get_filename()]
        self.assertEqual(names, ["invoice.pdf"])

    def test_executable_attachments_are_never_forwarded(self):
        ok, message = email_control.send_mail_now("bob@example.com", "s", "b", attachments=[
            {"filename": "setup.exe", "mime": "application/octet-stream", "attachment_id": "x", "message_id": "m1"}])
        self.assertFalse(ok)
        self.assertIn("will not be forwarded", message)
        self.service.users().messages().send.assert_not_called()

    def test_oversize_attachments_are_refused(self):
        with mock.patch.object(email_control, "MAX_ATTACHMENT_BYTES", 5):
            self.service.users().messages().attachments().get().execute.return_value = {"data": _b64(b"123456789")}
            ok, message = email_control.send_mail_now("bob@example.com", "s", "b", attachments=[
                {"filename": "big.pdf", "mime": "application/pdf", "attachment_id": "x", "message_id": "m1"}])
        self.assertFalse(ok)
        self.assertIn("limit", message)

    def test_inline_attachment_data_needs_no_download(self):
        ok, _ = email_control.send_mail_now("bob@example.com", "s", "b", attachments=[
            {"filename": "n.txt", "mime": "text/plain", "inline_data": _b64(b"hello"), "attachment_id": "", "message_id": "m1"}])
        self.assertTrue(ok)
        self.service.users().messages().attachments().get.assert_not_called()

    def test_forward_payload_keeps_text_attachments_and_prefixes_the_subject(self):
        original = {"from": "A <a@x.com>", "to": "me@x.com", "date": "Mon", "subject": "Invoice", "body": "Pay soon",
                    "attachments": [{"filename": "i.pdf", "attachment_id": "a1", "message_id": "m1"}]}
        draft = data_views.forward_payload(original, to="bob@x.com")
        self.assertEqual(draft["subject"], "Fwd: Invoice")
        self.assertIn("Forwarded message", draft["body"])
        self.assertIn("Pay soon", draft["body"])
        self.assertEqual(draft["attachments"][0]["filename"], "i.pdf")
        self.assertEqual(data_views.forward_payload({**original, "subject": "Fwd: Invoice"})["subject"], "Fwd: Invoice")

    def test_forward_view_uses_the_mail_last_read(self):
        message = {"id": "m1", "from": "A <a@x.com>", "to": "me", "date": "d", "subject": "S", "body": "B",
                   "attachments": [{"filename": "f.pdf", "attachment_id": "a", "message_id": "m1"}]}
        with mock.patch.object(email_control, "_LAST_READ_MESSAGE_ID", "m1"), \
             mock.patch.object(data_views, "gmail_message", return_value=message) as load:
            kind, draft, title, text = data_views.fetch("forward", {"to": "bob@x.com"})
        load.assert_called_once_with("m1")
        self.assertEqual((kind, title, draft["to"]), ("compose", "Forward", "bob@x.com"))
        self.assertIn("f.pdf", text)
        self.assertIn("Nothing has been sent", text)

    def test_forward_without_an_open_mail_explains(self):
        with mock.patch.object(email_control, "_LAST_READ_MESSAGE_ID", ""):
            with self.assertRaisesRegex(ValueError, "Open the mail"):
                data_views.fetch("forward", {})


class DownloadTests(unittest.TestCase):
    def test_download_writes_a_private_temp_file(self):
        with mock.patch.object(email_control, "fetch_attachment_bytes", return_value=b"data"):
            path = data_views.download_attachment("m1", {"filename": "../../evil/report.pdf", "attachment_id": "a"})
        self.addCleanup(shutil.rmtree, Path(path).parent, True)
        self.assertEqual(Path(path).name, "report.pdf")
        self.assertEqual(Path(path).read_bytes(), b"data")
        self.assertIn("arnox-attachment-", Path(path).parent.name)

    def test_download_refuses_executables(self):
        with mock.patch.object(email_control, "fetch_attachment_bytes", side_effect=AssertionError("no download")):
            with self.assertRaisesRegex(ValueError, "never opened"):
                data_views.download_attachment("m1", {"filename": "run.exe", "attachment_id": "a"})

    def test_attachment_to_temp_reports_what_is_missing(self):
        with mock.patch.object(email_control, "_LAST_READ_MESSAGE_ID", ""):
            self.assertIn("Read or open", email_control.attachment_to_temp("", "1")[1])
        service = mock.MagicMock()
        service.users().messages().get().execute.return_value = {"payload": {"parts": []}}
        with mock.patch.object(email_control, "_gmail_service", return_value=service):
            self.assertIn("no attachments", email_control.attachment_to_temp("m1", "1")[1])


class RouterTests(unittest.TestCase):
    def test_images_text_and_office_files_route_to_viewers(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, kind in (("a.png", "image"), ("b.JPG", "image"), ("c.txt", "text"), ("d.json", "text"),
                               ("e.docx", "text"), ("f.pptx", "text"), ("g.zip", "unsupported")):
                path = Path(tmp) / name
                path.write_bytes(b"x")
                self.assertEqual(router.classify_file(path).kind, kind, name)

    def test_new_views_are_routed(self):
        for view in ("more", "search", "forward"):
            self.assertEqual(router.route_data(view).kind, "data")


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


class ViewerTests(_Qt):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_image_tab_loads_fits_and_zooms(self):
        path = self.dir / "pic.png"
        image = QImage(200, 100, QImage.Format.Format_RGB32)
        image.fill(0x2266AA)
        image.save(str(path))
        tab = ImageTab(str(path))
        self.assertEqual(tab.error, "")
        tab.resize(600, 400)
        tab.show()
        self.app.processEvents()
        tab.set_zoom(2.0)
        self.assertEqual(tab.zoom(), 2.0)
        tab.set_zoom(100)
        self.assertEqual(tab.zoom(), 8.0)
        tab.fit()
        self.assertGreater(tab.zoom(), 0)

    def test_broken_image_is_reported(self):
        bad = self.dir / "bad.png"
        bad.write_bytes(b"not an image")
        self.assertIn("could not be shown", ImageTab(str(bad)).error)

    def test_text_tab_handles_windows_encoding_and_word_files(self):
        path = self.dir / "note.txt"
        path.write_bytes("Čaj i šećer".encode("cp1250"))
        self.assertEqual(TextTab(str(path)).text(), "Čaj i šećer")
        import docx

        document = docx.Document()
        document.add_paragraph("Contract text")
        word = self.dir / "c.docx"
        document.save(str(word))
        self.assertIn("Contract text", TextTab(str(word)).text())


class WorkspaceTests(_Qt):
    def setUp(self):
        self.view = WorkspaceView()
        self.addCleanup(self.view.close_all)

    def test_every_tab_has_a_close_button_that_works(self):
        self.view.show_data("inbox", _frame(), "Inbox")
        self.view.show_data("agenda", pandas.DataFrame([], columns=data_views.AGENDA_COLUMNS), "Calendar")
        self.view.show_data("message", {"id": "m", "from": "a@x.com", "to": "", "cc": "", "date": "", "subject": "Hi",
                                        "body": "x", "attachments": []}, "Hi")
        bar = self.view._tabs.tabBar()
        for index in range(3):
            self.assertIsNotNone(bar.tabButton(index, bar.ButtonPosition.RightSide), index)
        bar.tabButton(1, bar.ButtonPosition.RightSide).click()
        self.assertEqual(self.view.tab_titles(), ["Inbox", "Hi"])

    def test_assistant_can_list_close_switch_and_close_all(self):
        self.view.show_data("inbox", _frame(), "Inbox")
        self.view.show_data("agenda", pandas.DataFrame([], columns=data_views.AGENDA_COLUMNS), "Calendar")
        self.assertIn("Inbox, Calendar", self.view.manage_tabs("list"))
        self.assertIn("Switched to Inbox", self.view.manage_tabs("switch", "inbox"))
        self.assertIn("Closed Calendar", self.view.manage_tabs("close", "calend"))
        self.assertIn("No open tab matches", self.view.manage_tabs("close", "nothing"))
        self.view.show_data("agenda", pandas.DataFrame([], columns=data_views.AGENDA_COLUMNS), "Calendar")
        self.assertIn("Closed Calendar", self.view.manage_tabs("close"))   # no title: current tab
        self.assertIn("Closed 1 tab", self.view.manage_tabs("close_all"))
        self.assertIn("No tabs", self.view.manage_tabs("list"))
        self.assertIn("Unknown tab action", self.view.manage_tabs("explode"))

    def test_ambiguous_title_does_not_close_anything(self):
        self.view.show_data("inbox", _frame(), "Inbox")
        self.view.show_data("sent", _frame(), "Sent mail")
        self.assertIn("More than one", self.view.manage_tabs("close", "n"))
        self.assertEqual(self.view.tab_count(), 2)

    def test_show_more_appends_rows_and_hides_the_button_at_the_end(self):
        self.view.show_data("inbox", _frame(3, token="T2"), "Inbox")
        tab = self.view.current_widget()
        self.assertFalse(tab._more.isHidden())
        self.assertEqual(tab.total_rows(), 3)
        self.view.show_data("more", _frame(2, start=3), "Inbox")
        self.assertEqual(tab.total_rows(), 5)
        self.assertTrue(tab._more.isHidden())

    def test_show_more_button_loads_the_next_page_in_the_background(self):
        self.view.show_data("inbox", _frame(3, token="T2"), "Inbox")
        tab = self.view.current_widget()
        with mock.patch.object(data_views, "gmail_frame", return_value=_frame(2, start=3)) as page:
            tab._more.click()
            self._wait(lambda: tab.total_rows() == 5)
        page.assert_called_once()
        self.assertEqual(page.call_args.args[3], "T2")
        self.assertEqual(tab.total_rows(), 5)

    def test_failed_page_load_reenables_the_button(self):
        self.view.show_data("inbox", _frame(3, token="T2"), "Inbox")
        tab = self.view.current_widget()
        with mock.patch.object(data_views, "gmail_frame", side_effect=RuntimeError("offline")):
            tab._more.click()
            self._wait(lambda: tab._more.isEnabled())
        self.assertTrue(tab._more.isEnabled())
        self.assertIn("Could not load more", tab._count.text())

    def test_enter_in_search_searches_the_whole_folder(self):
        self.view.show_data("inbox", _frame(3), "Inbox")
        tab = self.view.current_widget()
        with mock.patch.object(data_views, "gmail_frame", return_value=_frame(1, query="in:inbox invoice", title="Search: invoice")) as page:
            tab._search.setText("invoice")
            tab._search.returnPressed.emit()
            self._wait(lambda: "Search: invoice" in self.view.tab_titles())
        self.assertEqual(page.call_args.args[0], "in:inbox invoice")
        self.assertIn("Search: invoice", self.view.tab_titles())

    def test_typing_only_filters_loaded_rows_without_the_network(self):
        self.view.show_data("inbox", _frame(3), "Inbox")
        tab = self.view.current_widget()
        with mock.patch.object(data_views, "gmail_frame", side_effect=AssertionError("typing must not search Gmail")):
            tab.search("Subject 1")
        self.assertEqual(tab.visible_rows(), 1)

    def test_forward_button_opens_a_draft_with_the_attachments_attached(self):
        message = {"id": "m1", "from": "A <a@x.com>", "to": "me", "cc": "", "date": "d", "subject": "Invoice", "body": "Pay",
                   "attachments": [{"filename": "i.pdf", "mime": "application/pdf", "size": 10, "attachment_id": "a", "message_id": "m1"}]}
        self.view.show_data("message", message, "Invoice")
        self.view.current_widget()._forward.click()
        self.assertEqual(self.view.tab_titles(), ["Invoice", "Forward"])
        compose = self.view.current_widget()
        self.assertEqual(compose.fields()["subject"], "Fwd: Invoice")
        self.assertEqual(compose.attachment_names(), ["i.pdf"])

    def test_removing_an_attachment_in_compose_keeps_it_out_of_the_sent_mail(self):
        tab = ComposeTab({"to": "a@x.com", "subject": "s", "body": "hi", "attachments": [
            {"filename": "keep.pdf"}, {"filename": "drop.pdf"}]})
        seen = []
        tab.send_requested.connect(seen.append)
        tab.remove_attachment(1)
        tab._send.click()
        self.assertEqual([a["filename"] for a in seen[0]["attachments"]], ["keep.pdf"])

    def test_clicking_an_attachment_opens_it_in_the_workspace_and_cleans_up(self):
        folder = Path(tempfile.mkdtemp(prefix="arnox-attachment-"))
        path = folder / "photo.png"
        image = QImage(40, 40, QImage.Format.Format_RGB32)
        image.fill(0x00AA66)
        image.save(str(path))
        message = {"id": "m1", "from": "a@x.com", "to": "", "cc": "", "date": "", "subject": "Holiday", "body": "x",
                   "attachments": [{"filename": "photo.png", "mime": "image/png", "size": 10, "attachment_id": "a"}]}
        self.view.show_data("message", message, "Holiday")
        with mock.patch.object(data_views, "download_attachment", return_value=str(path)):
            self.view.current_widget().attachment_buttons()[0].click()
            self._wait(lambda: "photo.png" in self.view.tab_titles())
        self.assertIn("photo.png", self.view.tab_titles())
        self.assertTrue(folder.exists())
        self.view.manage_tabs("close", "photo")
        self.assertFalse(folder.exists())

    def test_unsupported_attachment_is_explained_and_its_download_removed(self):
        folder = Path(tempfile.mkdtemp(prefix="arnox-attachment-"))
        path = folder / "bundle.zip"
        path.write_bytes(b"PK")
        message = {"id": "m1", "from": "a@x.com", "to": "", "cc": "", "date": "", "subject": "Zip", "body": "x",
                   "attachments": [{"filename": "bundle.zip", "mime": "application/zip", "size": 2, "attachment_id": "a"}]}
        self.view.show_data("message", message, "Zip")
        with mock.patch.object(data_views, "download_attachment", return_value=str(path)):
            self.view.current_widget().attachment_buttons()[0].click()
            self._wait(lambda: not folder.exists())
        self.assertFalse(folder.exists())
        self.assertEqual(self.view.tab_titles(), ["Zip"])
        self.assertIn("cannot be shown", self.view._flash_label.text())

    def test_download_failure_is_reported_in_the_header(self):
        message = {"id": "m1", "from": "a@x.com", "to": "", "cc": "", "date": "", "subject": "X", "body": "x",
                   "attachments": [{"filename": "f.pdf", "mime": "application/pdf", "size": 2, "attachment_id": "a"}]}
        self.view.show_data("message", message, "X")
        with mock.patch.object(data_views, "download_attachment", side_effect=RuntimeError("offline")):
            self.view.current_widget().attachment_buttons()[0].click()
            self._wait(lambda: "Could not open" in self.view._flash_label.text())
        self.assertIn("Could not open f.pdf", self.view._flash_label.text())


class ToolTests(unittest.TestCase):
    def _live(self, **ui_extra):
        live = main.JarvisLive.__new__(main.JarvisLive)
        live.ui = SimpleNamespace(muted=True, current_file=None, set_state=lambda *_: None, write_log=lambda *_: None, **ui_extra)
        live.cloud_safe = False
        return live

    def test_tabs_tool_reaches_the_workspace(self):
        seen = []
        live = self._live(workspace_manage_tabs=lambda action, title: seen.append((action, title)) or "Closed Inbox.")
        call = SimpleNamespace(name="workspace_tabs", args={"action": "close", "title": "inbox"}, id="1")
        response = asyncio.run(live._execute_tool(call))
        self.assertEqual(seen, [("close", "inbox")])
        self.assertEqual(response.response["result"], "Closed Inbox.")

    def test_attachment_open_goes_to_the_workspace_never_outside(self):
        opened = []
        live = self._live(workspace_open_temp_document=lambda path: opened.append(path) or "Opened f.pdf in the workspace.")
        with mock.patch.object(email_control, "attachment_to_temp", return_value=("/tmp/x/f.pdf", "")):
            call = SimpleNamespace(name="email_attachment", args={"action": "open", "attachment": "1"}, id="1")
            response = asyncio.run(live._execute_tool(call))
        self.assertEqual(opened, ["/tmp/x/f.pdf"])
        self.assertIn("workspace", response.response["result"])

    def test_attachment_open_errors_are_returned(self):
        live = self._live(workspace_open_temp_document=lambda path: "should not run")
        with mock.patch.object(email_control, "attachment_to_temp", return_value=("", "This message has no attachments.")):
            call = SimpleNamespace(name="email_attachment", args={"action": "open"}, id="1")
            response = asyncio.run(live._execute_tool(call))
        self.assertEqual(response.response["result"], "This message has no attachments.")

    def test_declarations_expose_the_new_options(self):
        names = [d["name"] for d in main.TOOL_DECLARATIONS]
        self.assertIn("workspace_tabs", names)
        opener = next(d for d in main.TOOL_DECLARATIONS if d["name"] == "workspace_open")
        views = opener["parameters"]["properties"]["view"]["enum"]
        for view in ("more", "search", "forward"):
            self.assertIn(view, views)
        self.assertIn("query", opener["parameters"]["properties"])
        attach = next(d for d in main.TOOL_DECLARATIONS if d["name"] == "email_attachment")
        self.assertIn("open", attach["parameters"]["properties"]["action"]["enum"])


if __name__ == "__main__":
    unittest.main()
