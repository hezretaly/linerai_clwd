"""Gmail sender.

# PLACEHOLDER(gmail): unverified. Requires a Google Workspace service account
# with domain-wide delegation plus GMAIL_IMPERSONATE, neither of which exists in
# this environment, so this code path has never been executed. The google-api
# client libraries are also not in pyproject.toml -- add them alongside the
# credentials. Until then ``check()`` raises and the app falls back to the
# outbox sender with a visible not-configured state.

The message itself is built by `message()`, which needs no credentials and no
network, so the MIME tree can be checked offline even though the API call
cannot be.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate, make_msgid

from app.config import settings
from app.integrations.base import NotConfigured
from app.integrations.email.base import (
    EmailSender,
    OutgoingAttachment,
    SendResult,
    address_list,
    bare_address,
    domain_of,
    extra_headers,
    header_value,
    with_name,
)
from app.integrations.email.resend import as_html

REQUIRED = ["GOOGLE_SERVICE_ACCOUNT_JSON", "GMAIL_IMPERSONATE"]
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

#: Above this the raw message goes as a media upload rather than inside the
#: JSON body. Gmail's simple upload is documented at 5 MB and base64 grows a
#: message by a third, so a message with a couple of photos is already past it.
SIMPLE_UPLOAD_MAX = 4 * 1024 * 1024


def _types(content_type: str) -> tuple[str, str]:
    """`("image", "png")` out of `image/png`, with a safe fallback.

    `message/rfc822` and `multipart/*` are sent as opaque bytes: the stdlib
    content manager refuses raw bytes for both, and a forwarded `.eml` is a
    file to the person receiving it, not a part for their client to unfold.
    """
    main, _, sub = (content_type or "").partition("/")
    main, sub = main.strip().lower(), sub.split(";")[0].strip().lower()
    if not main or not sub or main in ("multipart", "message"):
        return "application", "octet-stream"
    return main, sub


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

    def default_from(self, name: str = "", realm: str = "dealership") -> str:
        """The impersonated mailbox, wearing whichever name the caller gives.

        Gmail sends as whoever the service account is impersonating, so the
        address is that mailbox rather than SENDING_DOMAIN's -- and it is the
        same mailbox for both realms, because it is the one account the
        delegation was granted for. `realm` is accepted for the base
        signature: it took no arguments, and `dealership_from` and
        `identity_for` both pass them, so every send and the ops summary
        raised a TypeError under `EMAIL_SENDER=gmail`.
        """
        address = settings.gmail_impersonate or self.default_address(realm)
        return with_name(name, address) if address else ""

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

    def message(
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
    ) -> EmailMessage:
        """The whole message as MIME, with its own `Message-ID`.

        The canonical tree, which is what every client expects:
        `mixed[ alternative[ text/plain, related[ text/html, image… ] ], file… ]`
        -- text first and HTML last, because a reader shows the last part it
        can render; inline images beside the HTML that refers to them by
        `cid:`; files after. Each layer appears only when there is something to
        put in it, so a plain reply is still a two-part alternative.

        It used to be `set_content(body)` and nothing else, so under Gmail no
        HTML went at all -- not the rep's formatting, not the signature image,
        which is what `html_tail` exists to carry.

        The Message-ID is ours, written from the sending domain, because Gmail
        reports only its own API id. Whether Gmail keeps a Message-ID the
        client set is **unverified here** (see the placeholder above); if it
        replaces it, a reply threads by `References` and subject instead.
        """
        sender = self.from_header(from_address)
        message = EmailMessage(policy=SMTP)
        message["From"] = sender
        message["To"] = ", ".join(address_list(to))
        copied, blind = address_list(cc), address_list(bcc)
        if copied:
            message["Cc"] = ", ".join(copied)
        if blind:
            # Gmail reads Bcc off the raw message to deliver it and strips the
            # header from every copy, which is what a Bcc is.
            message["Bcc"] = ", ".join(blind)
        message["Subject"] = header_value(subject)
        if reply_to:
            message["Reply-To"] = header_value(reply_to)
        message["Date"] = formatdate(usegmt=True)
        domain = domain_of(sender) or (settings.sending_domain or "").strip().lower() or None
        message["Message-ID"] = make_msgid(domain=domain)
        parent, chain = header_value(in_reply_to), header_value(references)
        if parent:
            # Both, not just In-Reply-To: a client threads on References
            # and shows an orphan without it.
            message["In-Reply-To"] = parent
            message["References"] = chain or parent
        elif chain:
            message["References"] = chain
        for name, value in extra_headers(headers).items():
            message[name] = value

        message.set_content(body or "")
        markup = (html or as_html(body)) + (html_tail or "")
        files = list(attachments or [])
        inline = [a for a in files if a.inline] if markup else []
        placed = {id(a) for a in inline}
        if markup:
            message.add_alternative(markup, subtype="html")
            html_part = message.get_payload()[1]
            for a in inline:
                main, sub = _types(a.content_type)
                html_part.add_related(
                    a.data, main, sub, cid=f"<{a.content_id.strip('<>')}>",
                    filename=a.filename,
                )
        for a in files:
            if id(a) in placed:
                continue
            main, sub = _types(a.content_type)
            message.add_attachment(a.data, main, sub, filename=a.filename)
        return message

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
        # Gmail has no idempotency header. Accepted so the one send path can
        # pass it to every sender without asking which one it has.
        idempotency_key: str = "",
    ) -> SendResult:
        self.check()
        # Gmail will not let a message claim a From the authenticated user does
        # not own, so the header and the impersonated mailbox have to be the
        # same person -- setting one without the other is silently rewritten
        # at best and rejected at worst.
        message = self.message(
            to, subject, body, reply_to, in_reply_to, from_address, html_tail,
            cc=cc, bcc=bcc, html=html, attachments=attachments,
            references=references, headers=headers,
        )
        raw_bytes = message.as_bytes()
        messages = self._service(bare_address(str(message["From"]))).users().messages()
        if len(raw_bytes) <= SIMPLE_UPLOAD_MAX:
            request = messages.send(
                userId="me", body={"raw": base64.urlsafe_b64encode(raw_bytes).decode()}
            )
        else:  # pragma: no cover - never runs without credentials
            import io

            from googleapiclient.http import MediaIoBaseUpload  # type: ignore[import-not-found]

            request = messages.send(
                userId="me", body={},
                media_body=MediaIoBaseUpload(
                    io.BytesIO(raw_bytes), mimetype="message/rfc822", resumable=True,
                ),
            )
        sent = request.execute()
        # 'sent' means the API accepted it. Nothing more -- Gmail has no
        # delivery webhook; bounces arrive later as a DSN in the mailbox.
        return SendResult(
            provider="gmail",
            message_id=sent.get("id"),
            thread_id=sent.get("threadId"),
            status="sent",
            rfc_message_id=str(message["Message-ID"] or ""),
        )
