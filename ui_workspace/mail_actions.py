"""Runs Gmail actions started from the workspace (trash, restore, send) off the GUI thread."""

from __future__ import annotations

import threading
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal


class MailActions(QObject):
    # action ("trash" | "restore" | "send"), success, readable message, key (message id or compose token)
    done = pyqtSignal(str, bool, str, str)

    def trash(self, message_id: str) -> None:
        from actions import email_control

        self._run("trash", message_id, lambda: email_control.set_trash(message_id, True))

    def restore(self, message_id: str) -> None:
        from actions import email_control

        self._run("restore", message_id, lambda: email_control.set_trash(message_id, False))

    def send(self, token: str, payload: dict) -> None:
        """Send a message the user wrote and approved by pressing Send."""
        from actions import email_control

        self._run("send", token, lambda: email_control.send_mail_now(
            payload.get("to", ""), payload.get("subject", ""), payload.get("body", ""), payload.get("cc", ""),
            payload.get("thread_id", ""), payload.get("in_reply_to", ""), payload.get("references", ""),
            payload.get("attachments") or [],
        ))

    def _run(self, action: str, key: str, work: Callable[[], tuple[bool, str]]) -> None:
        def target():
            try:
                ok, message = work()
            except Exception as exc:
                ok, message = False, f"{action} failed: {exc}"
            self.done.emit(action, bool(ok), str(message), key)

        threading.Thread(target=target, daemon=True).start()
