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
from app.integrations.email.base import EmailSender, SendResult

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

    def _service(self):  # pragma: no cover - never runs without credentials
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
        ).with_subject(settings.gmail_impersonate)
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def send(
        self, to: str, subject: str, body: str,
        reply_to: str = "", in_reply_to: str = "",
    ) -> SendResult:
        self.check()
        message = EmailMessage()
        message["To"] = to
        message["From"] = settings.gmail_impersonate
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
            self._service()
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
