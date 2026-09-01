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


#: What our own mail is signed. Not a dealership -- `/ops` is Liner's desk, and
#: a support reply wearing a customer's own name is worse than one wearing a
#: stranger's.
OPS_SENDER_NAME = "Liner"


def dealership_from(db: Session, sender: EmailSender) -> str:
    """The From header for mail from the dealership to one of its buyers.

    The address is the deployment's verified one and the name is the
    dealership's own, read from the row -- `Craig and Landreth Cars
    <sales@linerai.us>`, the shape every product's transactional mail uses.

    **`sales@`, not `support@`.** They shared one mailbox, and `is_ours` routes
    anything addressed to `support@` into `/ops` -- so a buyer who composed a
    fresh message to the address printed on the mail in front of them reached
    Liner rather than the dealership. Pressing Reply worked, because that is
    `reply+<token>@`; typing a new one silently did not.

    **Served, never written.** It was `SENDING_FROM` in `.env`, which is a
    second copy of a fact that already lives in the database and in the
    profile, and it went stale the moment `DEALERSHIP=` changed: a prospect's
    buyer got their booking confirmation from "Riverside Auto". That is the
    same trap `SCRAPER_BASE_URL` was moved out of `.env` for, and the same rule
    as the five surfaces that used to print the name into a page.
    """
    from app.models import Dealership

    row = db.query(Dealership).first()
    return sender.default_from(row.name if row else "", realm="dealership")


def identity_for(
    sender: EmailSender,
    user: User | OpsUser | None,
    *,
    fallback_name: str = "",
    realm: str = "ops",
) -> Identity:
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
    # The From carries `fallback_name`; the Reply-To is the bare address. They
    # were one value, so a fallback send put a rendered `Name <addr>` header in
    # Reply-To -- legal, and inconsistent with every other branch here.
    fallback = sender.default_from(fallback_name, realm=realm)
    bare = sender.default_address(realm)
    if user is None:
        return Identity(from_address=fallback, reply_to=bare, personal=False)

    address = (user.email or "").strip()
    reply_to = address or bare
    if not address:
        return Identity(fallback, reply_to, False, "That account has no email address.")

    if sender.can_send_as(address):
        # The *name* is the desk's, the *address* and the Reply-To are theirs.
        # It was `user.name`, so a support reply arrived from "Liner Founder"
        # -- which is a job title on an envelope, and reads as a bigger
        # organisation than answers the mail. One name for everything from
        # `/ops`, and the routing argument is untouched: a reply still comes
        # back to whoever wrote, which is what stopped half the answers going
        # to the founder in the first place.
        return Identity(with_name(fallback_name or user.name, address), reply_to, True)

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


def signature(db: Session) -> str:
    """The dealership's sign-off, composed from its row.

    **Not written by the model, and not typed by a rep.** It is the
    dealership's name, address and phone -- three facts that live in
    `dealerships` and are the same on every message. A model asked to sign off
    improvises it, which makes the wording drift between emails and puts the
    phone number in a place it can be invented; a rep typing it from memory
    gets it wrong eventually, and typing it at all is work nobody should do
    twice a day. Same argument as `buyer_summary`: composed from rows, so it
    can be checked against the data rather than trusted.

    Returned as text and appended at the point of send, so the preview a rep
    sees under the composer is the message rather than an impression of it.
    """
    from app.models import Dealership

    row = db.query(Dealership).first()
    if row is None:
        return ""
    return "\n".join(
        line for line in (row.name, row.address, row.phone) if (line or "").strip()
    )


def with_signature(db: Session, body: str, user=None) -> str:
    """`body` plus the sign-off, unless it is already there.

    `user` is whoever is sending: their own block where they have written one,
    the dealership's otherwise. Liner passes nothing and always signs as the
    dealership -- an automated reply carrying a rep's name puts that person on
    words they never saw.

    Idempotent on purpose. A rep who types the dealership's name at the bottom
    out of habit should not send it twice, and a draft composed against an
    earlier version of this must not grow a second block when it is sent.
    """
    text = (body or "").rstrip()
    block = signature_for(db, user)
    if not block:
        return text
    if block in text:
        return text
    return f"{text}\n\n{block}"


def signature_for(db: Session, user=None) -> str:
    """The sign-off for mail *this person* is sending, or the dealership's.

    A rep's own block where they have written one; the dealership's otherwise.
    Liner's own replies pass `None` and always get the dealership's -- an
    automated message signed by a rep who never saw it puts that person's name
    on words they did not write, which is the same failure `save_captured_fields`
    refuses for a captured field.
    """
    from app.models import UserSignature

    if user is not None:
        row = db.query(UserSignature).filter_by(user_id=user.id).one_or_none()
        if row is not None and (row.text or "").strip():
            return row.text.strip()
    return signature(db)


def signature_image_url(db: Session, user=None, base: str = "") -> str:
    """A publicly fetchable URL for this person's signature image, or "".

    **Public on purpose, and unguessable for the same reason.** The thing that
    loads it is a recipient's mail client: it has no session, no cookie and no
    way to get one, so an authenticated path renders a broken image in every
    email. The token is random rather than the user id, so somebody who
    received one email cannot walk the staff list from it.
    """
    from app.models import UserSignature

    if user is None:
        return ""
    row = db.query(UserSignature).filter_by(user_id=user.id).one_or_none()
    if row is None or not row.image_token:
        return ""
    root = (base or settings.public_base_url or "").rstrip("/")
    return f"{root}/s/{row.image_token}.{row.image_ext or 'png'}"


def signature_html(db: Session, user=None, base: str = "") -> str:
    """The `<img>` that goes under this person's sign-off, or "".

    **Built here, never taken from a request.** It is markup going into
    somebody's inbox, so the only variable in it is a URL this system minted --
    the token, the extension and the base. Nothing a person typed reaches it,
    which is why there is no template and no rich-text editor behind this.

    Sized in the tag as well as styled: a mail client that ignores CSS is the
    normal case, and a 1200px logo that ignores `max-width` is a signature
    wider than the message.
    """
    url = signature_image_url(db, user, base)
    if not url:
        return ""
    return (
        '<p style="margin-top:12px"><img src="'
        + url
        + '" alt="" width="180" style="max-width:180px;height:auto;border:0"></p>'
    )
