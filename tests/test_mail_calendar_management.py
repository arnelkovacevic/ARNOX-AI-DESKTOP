import unittest
from unittest import mock

from actions import calendar_control as calendar
from actions import email_control as mail


class GmailManagementTests(unittest.TestCase):
    def setUp(self):
        self.service = mock.MagicMock()
        patcher = mock.patch.object(mail, "_gmail_service", return_value=self.service)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_trash_moves_message_and_is_reversible_wording(self):
        result = mail.email_control({"action": "trash", "message_id": "m1"})
        self.service.users().messages().trash.assert_called_with(userId="me", id="m1")
        self.assertIn("restored", result)

    def test_untrash_restores(self):
        mail.email_control({"action": "untrash", "message_id": "m1"})
        self.service.users().messages().untrash.assert_called_with(userId="me", id="m1")

    def test_mark_read_removes_unread_label(self):
        mail.email_control({"action": "mark_read", "message_id": "m2"})
        self.service.users().messages().modify.assert_called_with(
            userId="me", id="m2", body={"addLabelIds": [], "removeLabelIds": ["UNREAD"]}
        )

    def test_star_adds_starred_label(self):
        mail.email_control({"action": "star", "message_id": "m3"})
        self.service.users().messages().modify.assert_called_with(
            userId="me", id="m3", body={"addLabelIds": ["STARRED"], "removeLabelIds": []}
        )

    def test_message_action_requires_id(self):
        result = mail.email_control({"action": "trash"})
        self.assertIn("message ID", result)
        self.service.users().messages().trash.assert_not_called()

    def test_draft_create_never_sends(self):
        mail.email_control({"action": "draft_create", "to": "a@example.com", "subject": "Hi", "body": "Text"})
        self.service.users().drafts().create.assert_called_once()
        self.service.users().messages().send.assert_not_called()

    def test_draft_create_requires_recipient(self):
        result = mail.email_control({"action": "draft_create", "subject": "Hi"})
        self.assertIn("recipient", result)
        self.service.users().drafts().create.assert_not_called()

    def test_draft_delete_and_update_use_draft_id(self):
        mail.email_control({"action": "draft_delete", "draft_id": "d1"})
        self.service.users().drafts().delete.assert_called_with(userId="me", id="d1")
        mail.email_control({"action": "draft_update", "draft_id": "d2", "to": "a@example.com", "body": "x"})
        self.service.users().drafts().update.assert_called_with(
            userId="me", id="d2", body=mock.ANY
        )

    def test_folder_aliases_map_to_system_labels(self):
        self.assertEqual(mail._FOLDER_ALIASES["trash"], "trash")
        self.assertEqual(mail._FOLDER_ALIASES["bin"], "trash")

    def test_unknown_action_lists_new_actions(self):
        result = mail.email_control({"action": "nonsense"})
        self.assertIn("draft_create", result)
        self.assertIn("trash", result)


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.service = mock.MagicMock()
        patcher = mock.patch.object(calendar, "_calendar_service", return_value=self.service)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_delete_refuses_without_confirmation(self):
        result = calendar.calendar_control({"action": "delete", "event_id": "e1"})
        self.assertIn("confirmation", result)
        self.service.events().delete.assert_not_called()

    def test_delete_with_confirmation_calls_api(self):
        calendar.calendar_control({"action": "delete", "event_id": "e1", "confirmed": True})
        self.service.events().delete.assert_called_with(calendarId="primary", eventId="e1")

    def test_create_defaults_end_to_one_hour_later(self):
        self.service.events().insert().execute.return_value = {"id": "x", "summary": "Call", "start": {"dateTime": "t"}}
        calendar.calendar_control({"action": "create", "summary": "Call", "start": "2026-11-05T14:00:00+01:00"})
        body = self.service.events().insert.call_args.kwargs["body"]
        self.assertEqual(body["start"]["dateTime"], "2026-11-05T14:00:00+01:00")
        self.assertEqual(body["end"]["dateTime"], "2026-11-05T15:00:00+01:00")

    def test_all_day_uses_date_field(self):
        self.assertEqual(calendar._event_time("2026-11-05"), {"date": "2026-11-05"})

    def test_create_requires_title_and_start(self):
        result = calendar.calendar_control({"action": "create", "summary": "Only title"})
        self.assertIn("required", result)

    def test_update_without_changes_is_refused(self):
        result = calendar.calendar_control({"action": "update", "event_id": "e1"})
        self.assertIn("Nothing to change", result)

    def test_unknown_action(self):
        self.assertIn("Unknown calendar action", calendar.calendar_control({"action": "sync"}))


if __name__ == "__main__":
    unittest.main()
