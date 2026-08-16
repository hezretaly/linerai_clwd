"""Gmail sender.

# PLACEHOLDER(gmail): unverified. Requires a Google Workspace service account
# with domain-wide delegation plus GMAIL_IMPERSONATE, neither of which exists in
# this environment, so this code path has never been executed. The google-api
# client libraries are also not in pyproject.toml -- add them alongside the
# credentials. Until then ``check()`` raises and the app falls back to the
# outbox sender with a visible not-configured state.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage

from app.config import settings
from app.integrations.base import NotConfigured
from app.integrations.email.base import EmailSender, SendResult, bare_address, domain_of

REQUIRED = ["GOOGLE_SERVICE_ACCOUNT_JSON", "GMAIL_IMPERSONATE"]
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


class GmailSender(EmailSender):
    name = "gmail"
    delivers = True

    def _missing(self) -> list[str]:
        missing = []
        if not settings.google_service_account_json:
            missing.append("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not settings.gmail_impersonate:
            missing.append("GMAIL_IMPERSONATE")
        return missing

    def check(self) -> None:
        missing = self._missing()
        if missing:
            raise NotConfigured(
                "gmail",
                missing,
                "Use a Workspace service account with domain-wide delegation; a personal "
                "@gmail.com refresh token expires after 7 days.",
            )

    def default_from(self) -> str:
        # Gmail sends as whoever the service account is impersonating, so the
        # From is that mailbox rather than SENDING_DOMAIN's support@.
        return settings.gmail_impersonate

    def can_send_as(self, address: str) -> bool:
        """Workspace impersonation, not a verified domain -- a different rule.

        Domain-wide delegation lets the service account act as any user in the
        Workspace, so an address on the impersonated account's own domain is
        reachable; anything else is not, whatever SENDING_DOMAIN says. That
        divergence is why `can_send_as` is a method rather than one shared
        function: the two vendors authorise different things.

        # PLACEHOLDER(gmail): this branch has never run. Impersonating a second
        # user needs that user to exist in the Workspace and the delegation to
        # cover them, and neither can be checked from here.
        """
        target = domain_of(address)
        return bool(target) and target == domain_of(settings.gmail_impersonate)

    def _service(self, subject: str = ""):  # pragma: no cover - never runs without credentials
        try:
            from google.oauth2 import service_account  # type: ignore[import-not-found]
            from googleapiclient.discovery import build  # type: ignore[import-not-found]
        except ImportError as exc:
            raise NotConfigured(
                "gmail",
                ["google-api-python-client", "google-auth"],
                "Install the Google client libraries before enabling EMAIL_SENDER=gmail.",
            ) from exc

        creds = service_account.Credentials.from_service_account_file(
            settings.google_service_account_json, scopes=SCOPES
        ).with_subject(subject or settings.gmail_impersonate)
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def send(
        self, to: str, subject: str, body: str,
        reply_to: str = "", in_reply_to: str = "", from_address: str = "",
    ) -> SendResult:
        self.check()
        # Gmail will not let a message claim a From the authenticated user does
        # not own, so the header and the impersonated mailbox have to be the
        # same person -- setting one without the other is silently rewritten
        # at best and rejected at worst.
        sender = (
            from_address
            if from_address and self.can_send_as(bare_address(from_address))
            else self.default_from()
        )
        message = EmailMessage()
        message["To"] = to
        message["From"] = sender
        message["Subject"] = subject
        if reply_to:
            message["Reply-To"] = reply_to
        if in_reply_to:
            # Both, not just In-Reply-To: a client threads on References
            # and shows an orphan without it.
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
        message.set_content(body)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        sent = (
            self._service(bare_address(sender))
            .users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
        # 'sent' means the API accepted it. Nothing more -- Gmail has no
        # delivery webhook; bounces arrive later as a DSN in the mailbox.
        return SendResult(
            provider="gmail",
            message_id=sent.get("id"),
            thread_id=sent.get("threadId"),
            status="sent",
        )
