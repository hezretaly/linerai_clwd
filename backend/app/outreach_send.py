"""Everything that has to be true of a send, in one place.

`api/outreach.py` and `api/lead_import.py` both put mail on the wire, and both
grew their own copy of the outbound recipient check. That was survivable
while the only sender was the outbox, which delivers nothing -- the guard was
protecting a no-op. With Resend behind it the two copies are one rehearsal away
from mailing a real prospect, and the copy that drifts is the one nobody
notices, because a guard that fails open looks exactly like a guard that
passed.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import settings
from app.integrations.email.base import EmailSender, with_name
from app.models import OpsUser, Outreach, User


def blocked_reason(sender: EmailSender, to_address: str) -> str:
    """Why this send must not go out, or "" if it may.

    Only bites when the sender actually delivers: with the outbox there is
    nothing to protect anyone from, and refusing there would hide the row a
    rep is supposed to see.

    The message names the setting and the value that lifts it. A refusal that
    says only "blocked" costs whoever reads it a search through the codebase.
    """
    if not sender.delivers:
        return ""
    allowed = settings.outbound_recipients
    if allowed is None:
        return ""
    if (to_address or "").lower() in allowed:
        return ""
    return (
        f"Not sent: OUTBOUND_ONLY_TO does not include {to_address}. "
        + (
            f"It currently allows {', '.join(allowed)}. "
            if allowed else "It is empty, so every address is refused. "
        )
        + "Add the address to it, or set OUTBOUND_ONLY_TO=everyone to send freely."
    )


@dataclass
class Identity:
    """Whose name is on a message, and why it is not theirs when it is not."""

    #: What goes in the `From` header, display name included.
    from_address: str
    #: Where an answer comes back to. Their own address, always -- a reply that
    #: routes to the wrong one of two people is half the mail lost.
    reply_to: str
    #: True when `from_address` really is this person's own.
    personal: bool
    #: Empty when personal. Otherwise the reason, in words somebody can act on.
    note: str = ""


def identity_for(sender: EmailSender, user: User | OpsUser | None) -> Identity:
    """Who this message is from, decided once for the send and the screen.

    A person writing from the ops inbox should reach the recipient under their
    own name -- with two of us, a reply that always came back to the founder
    sent half the answers to the wrong person, and a From that always said
    `support@` made a personal answer read like a ticket.

    What makes that safe is that the provider verifies the *domain*: once
    `linerai.us` is verified in Resend, `founder@` and `cto@` are both legal on
    the same key, so a third person is a row in the users table and nothing
    else. There is no per-user credential anywhere in this, and there must not
    be -- a mailbox password per person is a thing to leak.

    Where the deployment cannot prove it owns the address, the send falls back
    to the configured sender **and says so**. The alternatives are both worse:
    putting an unverified address in a From gets the whole message rejected,
    and quietly swapping it leaves somebody believing they wrote from an
    address they did not.
    """
    fallback = sender.default_from()
    if user is None:
        return Identity(from_address=fallback, reply_to=fallback, personal=False)

    address = (user.email or "").strip()
    reply_to = address or fallback
    if not address:
        return Identity(fallback, reply_to, False, "That account has no email address.")

    if sender.can_send_as(address):
        return Identity(with_name(user.name, address), reply_to, True)

    domain = settings.sending_domain or "(unset)"
    return Identity(
        fallback,
        reply_to,
        False,
        f"Sending as {address} needs it to be on SENDING_DOMAIN, which is "
        f"{domain}. Mail goes out as {fallback or 'the configured sender'} and "
        "replies still come back to you.",
    )


# Lowercase letters and digits only, and deliberately so. `token_urlsafe`
# gives mixed case plus `-` and `_`, and both hurt here: the local part of an
# address is case-insensitive in practice, so a mail server that lowercases
# `reply+AbC@` leaves a token that no longer matches the stored value --
# SQLite's `=` is case-sensitive -- and a hyphen breaks the `[a-z0-9_]+`
# extraction some Workers use. Neither failure is visible: the reply simply
# falls through to the weaker matching rules, or lands unresolved.
ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
TOKEN_LENGTH = 20


def mint_reply_token(db: Session) -> str:
    """A token that routes a reply back to the row that sent it.

    Collision-checked rather than assumed: a duplicate would attach one
    buyer's reply to another buyer's history, which is worse than the retry
    this costs.
    """
    while True:
        token = "".join(secrets.choice(ALPHABET) for _ in range(TOKEN_LENGTH))
        if db.query(Outreach).filter_by(reply_token=token).first() is None:
            return token


def reply_to_address(token: str) -> str:
    """`reply+<token>@<domain>`, or "" when no domain is configured.

    Empty is the honest answer rather than a plausible-looking address: with
    no SENDING_DOMAIN there is no Cloudflare route behind it, so a reply would
    bounce. Better that the send carries no Reply-To than one that eats mail.
    """
    if not settings.sending_domain:
        return ""
    return f"reply+{token}@{settings.sending_domain}"
