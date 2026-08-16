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

import httpx

from app.config import settings
from app.integrations.base import NotConfigured
from app.integrations.email.base import EmailSender, SendResult, bare_address

API = "https://api.resend.com/emails"
TIMEOUT = 20.0


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
        to: str,
        subject: str,
        body: str,
        reply_to: str = "",
        in_reply_to: str = "",
        from_address: str = "",
    ) -> dict:
        """The request body, built separately so it can be asserted without
        being sent. `make smoke` checks the shape offline; the HTTP call is the
        only part a key would add.

        Both parts go: `text` is the source of truth -- every draft in this
        system is written as plain text -- and `html` is derived from it so the
        message renders as paragraphs rather than as one run-on block. Sending
        only one of the two is how an email either looks broken in a modern
        client or arrives unreadable in a plain-text one.
        """
        # Resend verifies the *domain*, not the mailbox, so once linerai.us is
        # verified any address on it is legal to send as -- which is what lets
        # the founder and the CTO each write from their own name on one key.
        # `can_send_as` is re-checked here rather than trusted: this is the
        # last place before the wire, and a From the provider refuses fails the
        # whole send rather than degrading.
        out = {
            "from": from_address if self.can_send_as(bare_address(from_address)) else self.default_from(),
            "to": [to],
            "subject": subject,
            "text": body,
            "html": as_html(body),
        }
        if reply_to:
            out["reply_to"] = reply_to
        if in_reply_to:
            # What makes a reply land *under* the message it answers instead of
            # starting a second thread in the buyer's inbox. `References` as
            # well as `In-Reply-To`: several clients thread on the former only,
            # and one line of ours is the whole chain we can honestly claim.
            out["headers"] = {
                "In-Reply-To": in_reply_to,
                "References": in_reply_to,
            }
        return out

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        reply_to: str = "",
        in_reply_to: str = "",
        from_address: str = "",
    ) -> SendResult:
        self.check()
        try:
            response = httpx.post(
                API,
                json=self.payload(to, subject, body, reply_to, in_reply_to, from_address),
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
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

        data = response.json()
        # 'sent' means Resend accepted it. Nothing more: delivery and bounces
        # arrive later on a webhook this system does not yet listen to, so a
        # row saying sent is not a row saying delivered.
        return SendResult(
            provider=self.name,
            message_id=data.get("id"),
            thread_id=None,
            status="sent",
        )
