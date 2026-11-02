import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pymupdf

from actions import email_control as mail


def _pdf_bytes(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _message(*parts):
    return {
        "payload": {
            "headers": [{"name": "Subject", "value": "Docs"}, {"name": "From", "value": "a@example.com"}],
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "filename": "", "body": {"data": _b64(b"Hello there")}},
                *parts,
            ],
        }
    }


def _part(filename, size, attachment_id=None, data=None, mime="application/pdf"):
    body = {"size": size}
    if attachment_id:
        body["attachmentId"] = attachment_id
    if data is not None:
        body["data"] = data
    return {"mimeType": mime, "filename": filename, "body": body}


class GmailAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.service = mock.MagicMock()
        patcher = mock.patch.object(mail, "_gmail_service", return_value=self.service)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pdf = _pdf_bytes("Contract value 9999 EUR")

    def _message(self, message):
        self.service.users().messages().get().execute.return_value = message

    def _attachment_bytes(self, data: bytes):
        self.service.users().messages().attachments().get().execute.return_value = {"data": _b64(data)}

    def test_list_numbers_attachments_in_order(self):
        self._message(_message(_part("a.pdf", 2048, "id1"), _part("b.xlsx", 5000, "id2", mime="application/vnd.ms-excel")))
        result = mail.email_control({"action": "attachments", "message_id": "m1"})
        self.assertIn("1. a.pdf", result)
        self.assertIn("2. b.xlsx", result)

    def test_no_attachments_is_reported(self):
        self._message(_message())
        self.assertIn("no attachments", mail.email_control({"action": "attachments", "message_id": "m1"}))

    def test_reading_an_email_mentions_its_attachments(self):
        self._message(_message(_part("offer.pdf", 1024, "id1")))
        result = mail.email_control({"action": "read", "message_id": "m1"})
        self.assertIn("offer.pdf", result)
        self.assertIn("attachment_read", result)

    def test_read_attachment_extracts_pdf_text_locally(self):
        self._message(_message(_part("contract.pdf", len(self.pdf), "id1")))
        self._attachment_bytes(self.pdf)
        result = mail.email_control({"action": "attachment_read", "message_id": "m1", "attachment": "1"})
        self.assertIn("Contract value 9999 EUR", result)
        self.assertIn("untrusted", result)

    def test_single_attachment_needs_no_selector(self):
        self._message(_message(_part("contract.pdf", len(self.pdf), "id1")))
        self._attachment_bytes(self.pdf)
        result = mail.email_control({"action": "attachment_read", "message_id": "m1"})
        self.assertIn("Contract value", result)

    def test_several_attachments_without_selector_ask_which(self):
        self._message(_message(_part("a.pdf", 10, "id1"), _part("b.pdf", 10, "id2")))
        result = mail.email_control({"action": "attachment_read", "message_id": "m1"})
        self.assertIn("Tell me which one", result)
        self.service.users().messages().attachments().get.assert_not_called()

    def test_select_by_file_name_fragment(self):
        self._message(_message(_part("invoice.pdf", len(self.pdf), "id1"), _part("photo.png", 10, "id2")))
        self._attachment_bytes(self.pdf)
        result = mail.email_control({"action": "attachment_read", "message_id": "m1", "attachment": "invoice"})
        self.assertIn("Contract value", result)

    def test_executable_attachments_are_never_downloaded(self):
        self._message(_message(_part("update.exe", 100, "id1", mime="application/octet-stream")))
        result = mail.email_control({"action": "attachment_read", "message_id": "m1", "attachment": "1"})
        self.assertIn("will not open or save", result)
        self.service.users().messages().attachments().get.assert_not_called()

    def test_oversize_attachment_is_refused_before_download(self):
        self._message(_message(_part("big.pdf", mail.MAX_ATTACHMENT_BYTES + 1, "id1")))
        result = mail.email_control({"action": "attachment_read", "message_id": "m1", "attachment": "1"})
        self.assertIn("limit", result)
        self.service.users().messages().attachments().get.assert_not_called()

    def test_temp_download_is_deleted_after_reading(self):
        self._message(_message(_part("contract.pdf", len(self.pdf), "id1")))
        self._attachment_bytes(self.pdf)
        folders = []
        real = tempfile.TemporaryDirectory

        def recording(*args, **kwargs):
            directory = real(*args, **kwargs)
            folders.append(directory.name)
            return directory

        with mock.patch.object(mail.tempfile, "TemporaryDirectory", recording):
            mail.email_control({"action": "attachment_read", "message_id": "m1", "attachment": "1"})
        self.assertTrue(folders)
        self.assertFalse(any(Path(folder).exists() for folder in folders))

    def test_inline_data_attachments_work_without_a_second_call(self):
        self._message(_message(_part("small.txt", 5, data=_b64(b"hello"), mime="text/plain")))
        result = mail.email_control({"action": "attachment_read", "message_id": "m1", "attachment": "1"})
        self.assertIn("hello", result)
        self.service.users().messages().attachments().get.assert_not_called()

    def test_save_uses_a_safe_name_and_never_overwrites(self):
        self._message(_message(_part("../../evil/report.pdf", len(self.pdf), "id1")))
        self._attachment_bytes(self.pdf)
        with tempfile.TemporaryDirectory() as home, mock.patch.object(Path, "home", return_value=Path(home)):
            first = mail.email_control({"action": "attachment_save", "message_id": "m1", "attachment": "1"})
            second = mail.email_control({"action": "attachment_save", "message_id": "m1", "attachment": "1"})
            folder = Path(home) / "Downloads" / "ARNOX Attachments"
            names = sorted(p.name for p in folder.iterdir())
        self.assertEqual(names, ["report (1).pdf", "report.pdf"])
        self.assertIn("report.pdf", first)
        self.assertIn("report (1).pdf", second)
        self.assertNotIn("..", first)

    def test_message_id_is_required_when_nothing_was_read_yet(self):
        with mock.patch.object(mail, "_LAST_READ_MESSAGE_ID", ""):
            self.assertIn("message ID", mail.email_control({"action": "attachment_read"}))

    def test_reading_an_email_lets_the_attachment_be_read_without_an_id(self):
        self._message(_message(_part("contract.pdf", len(self.pdf), "id1")))
        self._attachment_bytes(self.pdf)
        with mock.patch.object(mail, "_LAST_READ_MESSAGE_ID", ""):
            mail.email_control({"action": "read", "message_id": "m42"})
            result = mail.email_control({"action": "attachment_read"})
        self.assertIn("Contract value 9999 EUR", result)
        self.service.users().messages().get.assert_any_call(userId="me", id="m42", format="full")

    def test_model_sees_a_dedicated_attachment_tool_that_says_it_can_read_them(self):
        import main

        tool = next(d for d in main.TOOL_DECLARATIONS if d["name"] == "email_attachment")
        self.assertIn("YOU CAN read attachments", tool["description"])
        self.assertEqual(tool["parameters"]["properties"]["action"]["enum"], ["list", "read", "save"])
        self.assertEqual(tool["parameters"]["required"], ["action"])

    def test_attachment_scope_needs_no_new_permission(self):
        self.assertIn("https://www.googleapis.com/auth/gmail.readonly", mail.GMAIL_SCOPES)


if __name__ == "__main__":
    unittest.main()
