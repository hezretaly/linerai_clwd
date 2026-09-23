"""Resend sender.

# PLACEHOLDER(resend): the HTTP call has never run here. There is no
# RESEND_API_KEY in this environment and inventing one to make a green tick
# appear is the opposite of what this codebase is for. Everything either side
# of the request is real and tested -- the allow-list guard, the Reply-To that
# makes a reply traceable, the row that records what was attempted, the error
# path that stores what the API said. Only the send itself is unproven, and
# ``check()`` says so until a key exists.

Set ``EMAIL_SENDER=resend``, ``RESEND_API_KEY`` and ``SENDING_DOMAIN``.
"""

from __future__ import annotations

import base64

import httpx

from app.config import settings
from app.integrations.base import NotConfigured
from app.integrations.email.base import (
    EmailSender,
    OutgoingAttachment,
    SendResult,
    address_list,
    extra_headers,
    header_value,
)

API = "https://api.resend.com/emails"
TIMEOUT = 20.0
#: The follow-up read of the Message-ID. Short, because it is a nicety on the
#: back of a send that has already succeeded, and a slow answer must not hold
#: the rep's request open.
LOOKUP_TIMEOUT = 4.0
#: Resend's own limit on an `Idempotency-Key`.
IDEMPOTENCY_KEY_MAX = 256


def as_html(text: str) -> str:
    """Plain text as paragraphs, escaped.

    Deliberately not a template. The bodies here are drafts a rep read and
    possibly edited, and wrapping them in a branded shell would mean sending
    something other than what they approved. Escaping is not optional: a body
    can contain a buyer's own words, and `<` in an unescaped body is how a
    stray angle bracket eats the rest of the email.
    """
    from html import escape

    blocks = [b.strip() for b in (text or "").split("\n\n") if b.strip()]
    return "".join(
        f"<p>{escape(b).replace(chr(10), '<br>')}</p>" for b in blocks
    )


class ResendSender(EmailSender):
    name = "resend"
    delivers = True

    def _missing(self) -> list[str]:
        missing = []
        if not settings.resend_api_key:
            missing.append("RESEND_API_KEY")
        if not settings.sending_domain:
            missing.append("SENDING_DOMAIN")
        return missing

    def check(self) -> None:
        missing = self._missing()
        if missing:
            raise NotConfigured(
                "resend",
                missing,
                "The domain has to be verified in Resend before it will accept a send, "
                "and SENDING_DOMAIN must match it -- replies come back to the same "
                "domain through Cloudflare, so a mismatch breaks receiving too.",
            )

    def payload(
        self,
        to,
        subject: str,
        body: str,
        reply_to: str = "",
        in_reply_to: str = "",
        from_address: str = "",
        html_tail: str = "",
        *,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        html: str = "",
        attachments: list[OutgoingAttachment] | None = None,
        references: str = "",
        headers: dict[str, str] | None = None,
    ) -> dict:
        """The request body, built separately so it can be asserted without
        being sent. `make smoke` checks the shape offline; the HTTP call is the
        only part a key would add.

        Both parts go: `text` is the source of truth -- it is what the row
        stores and what a plain-text client shows -- and `html` is either the
        rep's own formatting, already cleaned, or paragraphs derived from the
        text so the message does not arrive as one run-on block. Sending only
        one of the two is how an email either looks broken in a modern client
        or arrives unreadable in a plain-text one.

        Every recipient field is an array, always: the spec documents arrays
        for more than one address and says nothing about splitting a
        comma-joined string, so a string with two addresses in it would be one
        malformed recipient. `cc`, `bcc` and `attachments` appear only when
        there is something in them, so a plain send is byte for byte what it
        was before any of them existed.
        """
        # Resend verifies the *domain*, not the mailbox, so once linerai.us is
        # verified any address on it is legal to send as -- which is what lets
        # the founder and the CTO each write from their own name on one key.
        # `can_send_as` is re-checked here rather than trusted: this is the
        # last place before the wire, and a From the provider refuses fails the
        # whole send rather than degrading.
        out = {
            "from": self.from_header(from_address),
            "to": address_list(to),
            "subject": subject,
            "text": body,
            # The image, if there is one, rides here and nowhere else. The
            # text half keeps the words and loses nothing a reader needs.
            "html": (html or as_html(body)) + (html_tail or ""),
        }
        copied, blind = address_list(cc), address_list(bcc)
        if copied:
            out["cc"] = copied
        if blind:
            out["bcc"] = blind
        if reply_to:
            out["reply_to"] = reply_to
        extra = extra_headers(headers)
        parent = header_value(in_reply_to)
        chain = header_value(references)
        if parent:
            # What makes a reply land *under* the message it answers instead of
            # starting a second thread in the buyer's inbox. `References` as
            # well as `In-Reply-To`: several clients thread on the former only.
            # With no chain, the parent alone is the one link we can honestly
            # claim.
            extra["In-Reply-To"] = parent
            extra["References"] = chain or parent
        elif chain:
            extra["References"] = chain
        # Absent rather than empty on a fresh send with nothing extra: the
        # request should say only what this message actually carries.
        if extra:
            out["headers"] = extra
        files = [self._attachment(a) for a in attachments or []]
        if files:
            out["attachments"] = files
        return out

    @staticmethod
    def _attachment(a: OutgoingAttachment) -> dict:
        """One file as Resend takes it: base64 content, never a URL for it to
        fetch -- a download link to our own server would need to be public,
        and an attachment is exactly what must not be."""
        item = {
            "filename": a.filename,
            "content": base64.b64encode(a.data).decode("ascii"),
            "content_type": a.content_type,
        }
        if a.content_id:
            # Makes it inline: the HTML refers to it as `cid:<content_id>`.
            item["content_id"] = a.content_id
        return item

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {settings.resend_api_key}"}

    def rfc_message_id(self, provider_id: str | None) -> str:
        """The Message-ID Resend gave the message, read back after the send.

        Resend's `id` is its own UUID and cannot go in a reply's
        `In-Reply-To`; the real header is only available from `GET
        /emails/{id}`. Best effort, and deliberately so: the send has already
        succeeded, and any failure here -- a timeout, a 404 while the message
        is still queued, a body without the field -- leaves it "" rather than
        failing a delivered message. An empty id means the next reply in this
        thread is not threaded by header, which is the honest outcome.

        # PLACEHOLDER(resend): never executed here -- api.resend.com is not
        # reachable from this environment and there is no key.
        """
        if not provider_id:
            return ""
        try:
            response = httpx.get(
                f"{API}/{provider_id}", headers=self._auth(), timeout=LOOKUP_TIMEOUT,
            )
            if response.status_code >= 400:
                return ""
            found = str((response.json() or {}).get("message_id") or "").strip()
        except Exception:  # noqa: BLE001 -- a nicety after a real success
            return ""
        return found if "@" in found else ""

    def send(
        self,
        to,
        subject: str,
        body: str,
        reply_to: str = "",
        in_reply_to: str = "",
        from_address: str = "",
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
        self.check()
        request_headers = self._auth()
        if idempotency_key:
            # The row id. A request retried after a timeout -- ours or a
            # proxy's -- then returns the first send's answer instead of
            # mailing the buyer a second copy.
            request_headers["Idempotency-Key"] = idempotency_key[:IDEMPOTENCY_KEY_MAX]
        try:
            response = httpx.post(
                API,
                json=self.payload(
                    to, subject, body, reply_to, in_reply_to, from_address, html_tail,
                    cc=cc, bcc=bcc, html=html, attachments=attachments,
                    references=references, headers=headers,
                ),
                headers=request_headers,
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as exc:
            # Never reached the API at all. Distinct from a rejection, and the
            # rep debugging this needs to know which of the two it was.
            return SendResult(
                provider=self.name, message_id=None, thread_id=None,
                status="failed", detail=f"Could not reach Resend: {exc}",
            )

        if response.status_code >= 400:
            # Verbatim. Resend's errors name the actual problem -- an
            # unverified domain, a malformed From -- and summarising them into
            # "send failed" is how someone spends an afternoon guessing.
            return SendResult(
                provider=self.name, message_id=None, thread_id=None,
                status="failed",
                detail=f"Resend returned {response.status_code}: {response.text[:500]}",
            )

        try:
            data = response.json() or {}
        except ValueError:
            # Accepted, with a body we could not read. Still a send: the
            # status said so, and reporting it failed would invite a retry
            # that mails the buyer twice.
            data = {}
        provider_id = data.get("id")
        # 'sent' means Resend accepted it. Nothing more: delivery and bounces
        # arrive later on a webhook this system does not yet listen to, so a
        # row saying sent is not a row saying delivered.
        return SendResult(
            provider=self.name,
            message_id=provider_id,
            thread_id=None,
            status="sent",
            rfc_message_id=self.rfc_message_id(provider_id),
        )
