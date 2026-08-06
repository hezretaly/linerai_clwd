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

    def send(self, to: str, subject: str, body: str, reply_to: str = "") -> SendResult:
        raise NotImplementedError

    def check(self) -> None:
        """Raise NotConfigured if this sender cannot authenticate."""
        return None
