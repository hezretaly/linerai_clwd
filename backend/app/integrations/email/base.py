from __future__ import annotations

from dataclasses import dataclass
from email.utils import formataddr, parseaddr

from app.config import settings


@dataclass
class SendResult:
    provider: str
    message_id: str | None
    thread_id: str | None
    status: str  # sent | failed
    detail: str = ""


def bare_address(address: str) -> str:
    """`founder@linerai.us` out of `Liner Founder <founder@linerai.us>`.

    Every check here is on the bare address, because the display name is
    attacker-shaped text -- somebody's own name, typed by them -- and matching
    a domain against a string that may contain an `@` inside a quoted name is
    how a permission check says yes to the wrong thing.
    """
    return parseaddr(address or "")[1].strip()


def with_name(name: str, address: str) -> str:
    """`Liner Founder <founder@linerai.us>`, quoted properly if it has to be.

    `formataddr` rather than an f-string: a name with a comma or a quote in it
    produces a header that a strict parser reads as two recipients.
    """
    return formataddr(((name or "").strip(), address)) if name else address


def domain_of(address: str) -> str:
    """The bit after the `@`, lowercased. "" when there isn't one."""
    _, _, domain = bare_address(address).rpartition("@")
    return domain.strip().lower()


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
        from_address: str = "",
    ) -> SendResult:
        """`in_reply_to` is a provider message id, not a header value.

        Each implementation maps it to whatever its vendor wants -- Resend
        takes a `headers` object, Gmail wants MIME headers on the raw
        message. Passing a rendered header string instead would push one
        vendor's wire format into every caller, which is the thing this
        interface exists to prevent.

        `from_address` is empty for almost every send: mail from the dealership
        is from the dealership, and there is no person to name. It is filled in
        where a *person* is writing and the deployment can prove it owns the
        address -- `outreach_send.identity_for` is the one place that decides,
        and a sender that is handed an address it cannot send as should ignore
        it rather than hand the vendor something it will reject.
        """
        raise NotImplementedError

    def default_address(self) -> str:
        """The bare address this deployment sends from.

        `support@` rather than `liner@`: it is the address the domain is set up
        around, and the one somebody replying by hand rather than by hitting
        Reply will send to -- which the catch-all routes back either way.

        Parsed with `bare_address` rather than returned as written, because
        `SENDING_FROM` is a line a person copies and they copy the whole thing.
        `.env.example` carried `Riverside Auto <support@linerai.us>` as its
        illustration, so every deployment that started from that file put a
        fixture dealership's name on every envelope it ever sent -- including
        our own support replies, which are not from a dealership at all. The
        display name is served per realm now (see `outreach_send`), so a name
        left in this setting is dropped rather than sent.
        """
        configured = settings.sending_from or (
            f"support@{settings.sending_domain}" if settings.sending_domain else ""
        )
        return bare_address(configured) or configured

    def default_from(self, name: str = "") -> str:
        """The From header for a send nobody is personally named on.

        The address is this deployment's; the *name* belongs to whoever the
        mail is actually from, and the caller is the only one who knows which
        that is -- the dealership for a booking confirmation, Liner for a
        support reply. Called with no name it is the bare address, which is
        honest rather than wrong.
        """
        return with_name(name, self.default_address())

    def from_header(self, from_address: str) -> str:
        """The From this send may actually use, decided in one place.

        Was three identical copies, one per sender, which is how one of them
        stops applying the rule. Two cases, and separating them is the point:

        - **A display name on our own address is always legal.** `Craig and
          Landreth Cars <support@linerai.us>` is our verified mailbox wearing
          the dealership's name, the shape every product's transactional mail
          uses. There is no authority question to ask, so `can_send_as` must
          not be asked -- gated on it, the name was dropped on any deployment
          with no `SENDING_DOMAIN` set, which is every one before the domain is
          verified. That is exactly when somebody is looking at the result.
        - **Somebody else's address needs proving.** That is `can_send_as`, and
          a From the provider has not verified fails the whole send rather than
          degrading, so an address we cannot prove falls back rather than being
          guessed at.
        """
        bare = bare_address(from_address)
        if bare and bare.lower() == (self.default_address() or "").lower():
            return from_address
        if bare and self.can_send_as(bare):
            return from_address
        return self.default_from()

    def can_send_as(self, address: str) -> bool:
        """May this deployment put `address` in a From header?

        One rule for every vendor that authenticates a domain rather than a
        mailbox: the address must be on `SENDING_DOMAIN`, which is the domain
        whose DNS this deployment controls and which the provider has verified.
        Anything else is somebody else's name on our envelope -- a provider
        rejects it outright, and where one did not, it would be a forgery.

        Deliberately not a per-user credential. Verifying `linerai.us` once is
        what makes both founder@ and cto@ legal to send as, so adding a third
        person is a row in the users table and nothing else. The cost is that
        an owner whose address is *not* on the domain cannot be honoured, and
        that falls back visibly rather than quietly.
        """
        if not settings.sending_domain or "@" not in (address or ""):
            return False
        return domain_of(address) == settings.sending_domain.strip().lower()

    def check(self) -> None:
        """Raise NotConfigured if this sender cannot authenticate."""
        return None
