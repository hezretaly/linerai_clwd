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
from app.integrations.email.base import EmailSender, SendResult

API = "https://api.resend.com/emails"
TIMEOUT = 20.0


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

    def from_address(self) -> str:
        return settings.sending_from or f"liner@{settings.sending_domain}"

    def payload(self, to: str, subject: str, body: str, reply_to: str = "") -> dict:
        """The request body, built separately so it can be asserted without
        being sent. `make smoke` checks the shape offline; the HTTP call is the
        only part a key would add."""
        out = {
            "from": self.from_address(),
            "to": [to],
            "subject": subject,
            "text": body,
        }
        if reply_to:
            out["reply_to"] = reply_to
        return out

    def send(self, to: str, subject: str, body: str, reply_to: str = "") -> SendResult:
        self.check()
        try:
            response = httpx.post(
                API,
                json=self.payload(to, subject, body, reply_to),
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
