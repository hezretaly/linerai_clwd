from __future__ import annotations

import uuid

from app.integrations.email.base import EmailSender, SendResult


class OutboxSender(EmailSender):
    """The default. Records the outreach row and nothing else.

    This is not a simulated Gmail: it does not pretend the mail was delivered.
    ``delivers`` is False, the row is stamped provider='outbox', and every
    surface that shows it says "recorded locally -- not sent".
    """

    name = "outbox"
    delivers = False

    def send(
        self, to: str, subject: str, body: str,
        reply_to: str = "", in_reply_to: str = "", from_address: str = "",
    ) -> SendResult:
        # The From is echoed back rather than dropped, and it goes through the
        # same `can_send_as` rule a real provider would apply. An outbox that
        # accepted any From would let a deployment look configured for
        # per-person sending right up until the first real send is rejected.
        sender = self.from_header(from_address)
        return SendResult(
            provider="outbox",
            message_id=f"outbox-{uuid.uuid4()}",
            thread_id=None,
            status="sent",
            detail=(
                "Recorded in the local outbox. No mail was delivered."
                + (f" It would have gone out as {sender}." if sender else "")
            ),
        )


class ConsoleSender(OutboxSender):
    """Same as the outbox but also prints. Used by scripts."""

    name = "console"

    def send(
        self, to: str, subject: str, body: str,
        reply_to: str = "", in_reply_to: str = "", from_address: str = "",
    ) -> SendResult:
        print(f"\n--- email (not delivered) ---\nTo: {to}\nSubject: {subject}\n\n{body}\n---\n")
        return super().send(to, subject, body, reply_to, in_reply_to, from_address)
