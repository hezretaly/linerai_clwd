from __future__ import annotations

import uuid

from app.integrations.email.base import (
    EmailSender,
    OutgoingAttachment,
    SendResult,
    address_list,
    extra_headers,
    recipient_count,
)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def would_have(to, cc, bcc, attachments) -> str:  # noqa: ANN001
    """What a recorded-only send would have carried: "3 people with 2 files".

    The outbox row is the only trace a rehearsal leaves, so it says how big
    the message was as well as that nothing left. "Recorded, not sent" over a
    message with a Cc and two files it does not mention reads as though the
    Cc and the files were dropped.
    """
    people = recipient_count(to, cc, bcc)
    files = len(attachments or [])
    who = _plural(people, "person", "people")
    return f"{who} with {_plural(files, 'file', 'files')}" if files else who


class OutboxSender(EmailSender):
    """The default. Records the outreach row and nothing else.

    This is not a simulated Gmail: it does not pretend the mail was delivered.
    ``delivers`` is False, the row is stamped provider='outbox', and every
    surface that shows it says "recorded locally -- not sent".
    """

    name = "outbox"
    delivers = False

    def send(
        self, to, subject: str, body: str,
        reply_to: str = "", in_reply_to: str = "", from_address: str = "",
        # Accepted and ignored: this sender delivers nothing, so an image has
        # nowhere to go. Taking the argument is not optional -- the caller
        # always passes it, and a sender that refuses it raises a TypeError
        # inside the send rather than at import, so it surfaces as mail that
        # quietly failed instead of as a broken build. The same holds for
        # every keyword below.
        html_tail: str = "",
        *,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        html: str = "",
        attachments: list[OutgoingAttachment] | None = None,
        references: str = "",
        headers: dict[str, str] | None = None,
        idempotency_key: str = "",
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
                + f" It would have gone to {would_have(to, cc, bcc, attachments)}"
                + (f", as {sender}." if sender else ".")
            ),
            # Nothing was sent, so nothing has a Message-ID. Inventing one
            # would let a later reply thread under a message that never
            # existed.
            rfc_message_id="",
        )


class ConsoleSender(OutboxSender):
    """Same as the outbox but also prints. Used by scripts."""

    name = "console"

    def send(
        self, to, subject: str, body: str,
        reply_to: str = "", in_reply_to: str = "", from_address: str = "",
        html_tail: str = "",
        *,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        html: str = "",
        attachments: list[OutgoingAttachment] | None = None,
        references: str = "",
        headers: dict[str, str] | None = None,
        idempotency_key: str = "",
    ) -> SendResult:
        # Everything the message would have carried, because the point of the
        # console is to read what a real send would have done -- a printout
        # that showed one To and the text would hide exactly the Cc, the files
        # and the threading this is most likely being run to check.
        lines = ["", "--- email (not delivered) ---",
                 f"To: {', '.join(address_list(to))}"]
        if cc:
            lines.append(f"Cc: {', '.join(address_list(cc))}")
        if bcc:
            lines.append(f"Bcc: {', '.join(address_list(bcc))}")
        if reply_to:
            lines.append(f"Reply-To: {reply_to}")
        lines.append(f"Subject: {subject}")
        if in_reply_to:
            lines.append(f"In-Reply-To: {in_reply_to}")
        if references or in_reply_to:
            lines.append(f"References: {references or in_reply_to}")
        for name, value in extra_headers(headers).items():
            lines.append(f"{name}: {value}")
        for a in attachments or []:
            what = "inline" if a.inline else "attachment"
            lines.append(f"[{what}] {a.filename} ({a.content_type}, {len(a.data)} bytes)")
        if html or html_tail:
            lines.append(f"[html part: {len(html or '') + len(html_tail or '')} characters]")
        lines += ["", body, "---", ""]
        print("\n".join(lines))
        return super().send(
            to, subject, body, reply_to, in_reply_to, from_address, html_tail,
            cc=cc, bcc=bcc, html=html, attachments=attachments,
            references=references, headers=headers,
            idempotency_key=idempotency_key,
        )
