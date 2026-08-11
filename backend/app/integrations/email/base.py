from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SendResult:
    provider: str
    message_id: str | None
    thread_id: str | None
    status: str  # sent | failed
    detail: str = ""


class EmailSender:
    """One interface, several implementations. Swapping is a config value."""

    name = "base"
    #: True when this implementation actually puts mail on the wire.
    delivers = False

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        reply_to: str = "",
        in_reply_to: str = "",
    ) -> SendResult:
        """`in_reply_to` is a provider message id, not a header value.

        Each implementation maps it to whatever its vendor wants -- Resend
        takes a `headers` object, Gmail wants MIME headers on the raw
        message. Passing a rendered header string instead would push one
        vendor's wire format into every caller, which is the thing this
        interface exists to prevent.
        """
        raise NotImplementedError

    def check(self) -> None:
        """Raise NotConfigured if this sender cannot authenticate."""
        return None
