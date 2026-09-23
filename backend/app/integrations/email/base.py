from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import formataddr, parseaddr

from app.config import settings


@dataclass
class SendResult:
    provider: str
    #: The provider's own id for the send. Not an RFC 5322 Message-ID --
    #: Resend's is a UUID -- and never to be put in an `In-Reply-To`.
    message_id: str | None
    thread_id: str | None
    status: str  # sent | failed
    detail: str = ""
    #: The `Message-ID` header the message actually went out with, where the
    #: provider reported it or this system wrote it. "" when unknown, which is
    #: always better than a guess: a wrong one threads a reply under a
    #: stranger's message.
    rfc_message_id: str = ""


@dataclass(frozen=True)
class OutgoingAttachment:
    """One file to send, bytes in hand.

    `content_id` (no angle brackets) marks an inline image an HTML body refers
    to as `cid:<content_id>`; everything else is an ordinary attachment.
    """

    filename: str
    content_type: str
    data: bytes
    content_id: str = ""

    @property
    def inline(self) -> bool:
        return bool(self.content_id)


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


def address_list(value) -> list[str]:  # noqa: ANN001 -- str | Sequence[str | Recipient] | None
    """What a sender puts in To, Cc or Bcc: one string per recipient.

    A caller may hand over one string -- the old single-address `to`, or a
    box's text with several addresses in it -- or a list. A string is split on
    the separators a person types, outside quotes and angle brackets, so
    `"Doe, Jane" <jane@x.com>` stays one recipient. Nothing is validated here:
    that happened in `email_addresses.parse` before the send was built, and a
    sender that second-guessed it would refuse a message the rep was already
    told was fine.
    """
    from app.email_addresses import Recipient, split_entries

    if not value:
        return []
    # **One string is a box's text; a list is already one entry per person.**
    # Splitting list items as well cut `Two, Person <two@x>` into `Two` and
    # `Person <two@x>` -- two recipients the provider rejects or, worse,
    # delivers to one of. So only the single-string form is split, and each
    # list item is kept whole with its name re-quoted.
    if isinstance(value, str):
        out = split_entries(value)
    else:
        out = []
        for item in value:
            if isinstance(item, Recipient):
                out.append(_quoted(item.name, item.address))
            elif isinstance(item, str):
                named = _NAMED.match(item.strip())
                out.append(
                    _quoted(named.group(1).strip().strip('"'), named.group(2).strip())
                    if named else item
                )
    return [header_value(v) for v in out if header_value(v)]


#: `Name <address>` with anything, commas included, before the brackets.
_NAMED = re.compile(r"^(.*?)\s*<([^<>\s]+@[^<>\s]+)>$", re.S)


def _quoted(name: str, address: str) -> str:
    """`"Name" <address>`, quoted when it needs to be and never encoded.

    Quoted rather than RFC 2047-encoded because a JSON API builds its own
    headers and would show `=?utf-8?b?...?=` as the name; each sender encodes
    for its own wire (Gmail's `EmailMessage` does it on assignment).
    """
    name = header_value(name)
    if not name:
        return address
    if name.isascii():
        return formataddr((name, address))
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + f'" <{address}>'


_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")

#: Headers a caller may not set through `headers=`: each is built by the
#: sender from its own argument, and a second copy in the extras is how a
#: message ends up with two `To` lines or a `From` the provider never checked.
STRUCTURAL_HEADERS = frozenset({
    "from", "to", "cc", "bcc", "subject", "reply-to", "sender", "date",
    "message-id", "in-reply-to", "references", "content-type",
    "content-transfer-encoding", "mime-version",
})


def header_value(value: str | None) -> str:
    """A header value on one line.

    `References` and `In-Reply-To` come from a *received* message, which is
    whatever its sender wrote; a CR or LF left in one is a header of their own
    choosing in our outgoing mail. Python's SMTP policy refuses such a value
    with an exception, which would fail the send rather than clean it.
    """
    return re.sub(r"\s+", " ", _CONTROL.sub(" ", value or "")).strip()


def extra_headers(headers: dict | None) -> dict[str, str]:
    """The caller's extra headers -- `Importance`, `Auto-Submitted` -- cleaned,
    with anything a sender builds itself dropped."""
    out: dict[str, str] = {}
    for name, value in (headers or {}).items():
        key = header_value(str(name))
        if not key or ":" in key or " " in key or key.lower() in STRUCTURAL_HEADERS:
            continue
        cleaned = header_value(str(value))
        if cleaned:
            out[key] = cleaned
    return out


def recipient_count(to, cc=None, bcc=None) -> int:  # noqa: ANN001
    return len(address_list(to)) + len(address_list(cc)) + len(address_list(bcc))


class EmailSender:
    """One interface, several implementations. Swapping is a config value."""

    name = "base"
    #: True when this implementation actually puts mail on the wire.
    delivers = False

    def send(
        self,
        to: str | list[str],
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
        """Put one message on the wire, or say why not.

        **The positional order is frozen**; everything a full email carries
        beyond the first seven arguments is keyword-only. Callers and the gate
        still pass `(to, subject, body, reply_to, in_reply_to, from_address,
        html_tail)` by position, and a new argument slotted in among them would
        silently put a signature image where a From belongs.

        `to` is one string (which may hold several addresses, as a person
        types them) or a list; `cc` and `bcc` are lists. Header forms (`Name
        <a@b>`) are fine in all three. `reply_to` stays a single address: it is
        the `reply+<token>@` route home, and one is the whole of it.

        `body` is the text/plain half and is always sent. `html` is a complete,
        **already cleaned** HTML body -- `email_html.clean_outbound` -- and
        replaces the paragraphs a sender would otherwise derive from `body`.
        Cleaning is the caller's job and happens once, before the row is
        written, so the stored copy and the sent copy are the same markup.

        `in_reply_to` is an RFC 5322 Message-ID, never a provider's API id,
        and `references` is the whole chain, oldest first. With no chain,
        `References` is `in_reply_to` alone -- the one link that can honestly
        be claimed. `headers` is for the few a message may carry beyond those
        (`Importance`, `Auto-Submitted`); anything a sender builds itself is
        dropped from it. `idempotency_key` is the row id, so a retried request
        cannot send the same message twice where the vendor supports it.

        `SendResult.rfc_message_id` is the Message-ID the message really went
        out with, where it is known -- never a guess, because a wrong one
        threads the buyer's answer under a stranger's message.

        `html_tail` is markup appended to the HTML half only, and the one thing
        it carries today is a signature image. **Plain text cannot hold an
        image**, so a sender that delivers only text simply ignores it and the
        recipient reads the text sign-off -- which is the correct degradation
        rather than a broken attachment. It is markup by necessity and is
        therefore built here, never taken from a request body.

        Each implementation maps the threading ids to whatever its vendor
        wants -- Resend takes a `headers` object, Gmail wants MIME headers on
        the raw message. Passing a rendered header string instead would push
        one vendor's wire format into every caller, which is the thing this
        interface exists to prevent.

        `from_address` is empty for almost every send: mail from the dealership
        is from the dealership, and there is no person to name. It is filled in
        where a *person* is writing and the deployment can prove it owns the
        address -- `outreach_send.identity_for` is the one place that decides,
        and a sender that is handed an address it cannot send as should ignore
        it rather than hand the vendor something it will reject.
        """
        raise NotImplementedError

    def default_address(self, realm: str = "dealership") -> str:
        """The bare address this deployment sends from, for one realm.

        **Two realms, two mailboxes, and the split is not cosmetic.** A
        dealership's buyer mail goes out from its own mailbox on the sending
        domain -- `alsbou@`, from its profile -- or from `sales@` when
        it has none; Liner's own support replies go out from `support@`. It
        was `support@` for both, and the
        cost was a real misroute rather than an odd-looking header: `is_ours`
        sends anything addressed to `support@` to `/ops`, so a buyer who
        composed a *fresh* message to the address on the mail they were
        looking at landed in Liner's mailbox, invisible to the dealership.
        Pressing Reply worked, because that goes to `reply+<token>@`; typing a
        new one did not, and nothing anywhere said so.

        `sales@` is also simply what a car dealership's mail comes from.
        Support is where you write when something is broken.

        Parsed with `bare_address` rather than returned as written, because
        `SENDING_FROM` is a line a person copies and they copy the whole thing.
        `.env.example` carried `Riverside Auto <support@linerai.us>` as its
        illustration, so every deployment that started from that file put a
        fixture dealership's name on every envelope it ever sent. The display
        name is served per realm now (see `outreach_send`), so a name left in
        this setting is dropped rather than sent.
        """
        if realm == "ops":
            # Already a setting, already what `is_ours` reads, and already
            # what the landing page publishes. A third copy of our own support
            # address is how one of them starts disagreeing.
            return bare_address(settings.support_email) or settings.support_email
        # **The dealership's own mailbox first.** On a host serving several
        # dealerships one `SENDING_FROM` cannot be right for all of them, and
        # `sales@` on the shared domain would put every store's buyer mail in
        # one envelope with no way to route the answer back. The profile's
        # mailbox (`alsbou@linerai.us`) is that store's, and the intake
        # routes mail to it into that store. A profile with none -- the
        # fixture -- falls through to what the deployment configured.
        from app import profile

        box = profile.mailbox()
        if box and settings.sending_domain:
            return f"{box}@{settings.sending_domain}"
        configured = settings.sending_from or (
            f"sales@{settings.sending_domain}" if settings.sending_domain else ""
        )
        return bare_address(configured) or configured

    def default_from(self, name: str = "", realm: str = "dealership") -> str:
        """The From header for a send nobody is personally named on.

        The address is this deployment's; the *name* belongs to whoever the
        mail is actually from, and the caller is the only one who knows which
        that is -- the dealership for a booking confirmation, Liner for a
        support reply. Called with no name it is the bare address, which is
        honest rather than wrong.
        """
        return with_name(name, self.default_address(realm))

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
        # Either realm's mailbox is ours. Comparing against one of them made a
        # display name on the *other* need `can_send_as`, which is the wrong
        # question about an address we own.
        mine = {
            (self.default_address("dealership") or "").lower(),
            (self.default_address("ops") or "").lower(),
        }
        if bare and bare.lower() in {a for a in mine if a}:
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
