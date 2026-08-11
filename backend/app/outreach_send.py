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

from sqlalchemy.orm import Session

from app.config import settings
from app.integrations.email.base import EmailSender
from app.models import Outreach


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
