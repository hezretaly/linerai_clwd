"""Everything that has to be true of a send, in one place.

`api/outreach.py` and `api/lead_import.py` both put mail on the wire, and both
grew their own copy of the DEMO_MODE allow-list check. That was survivable
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
    """
    if not sender.delivers:
        return ""
    if not settings.demo_mode:
        return ""
    if (to_address or "").lower() in settings.allowlist:
        return ""
    return (
        f"DEMO_MODE is on and {to_address} is not in EMAIL_ALLOWLIST, so nothing was sent."
    )


def mint_reply_token(db: Session) -> str:
    """A token that routes a reply back to the row that sent it.

    Collision-checked rather than assumed: a duplicate would attach one
    buyer's reply to another buyer's history, which is worse than the retry
    this costs.
    """
    while True:
        token = secrets.token_urlsafe(12)
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
