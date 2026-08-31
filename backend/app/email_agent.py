"""May Liner answer this email? One function, and every brake in it.

Built and tested **before** anything can answer, deliberately: there must never
be a build where Liner can send mail on its own and cannot be stopped. Phase 5
calls `may_reply`; until then it is exercised entirely by `make smoke`, which
is a weaker claim than "in production" and a much stronger one than "written".

The brakes fail differently on purpose, and the order they run in is the order
they are cheapest to be sure about:

1. **Off** -- `EMAIL_AGENT` in `.env` and the `email_agent` runtime flag. Two
   switches for two situations: one is a deployment saying this dealership has
   not turned it on, the other is somebody at three in the morning making it
   stop. The stricter wins, always.
2. **A machine sent it** -- `automated_reason`, checked at intake. A header is
   the sender declaring itself a machine, and honouring it stops a loop on its
   first turn where a timer only slows it to forty-eight real emails a day.
3. **A person already answered** -- a rep replying is the answer, and Liner
   adding a second one minutes later gives the buyer two emails from the same
   dealership saying possibly different things. There is no window that makes
   that legible, which is why this diverges from the chat rule.
4. **Too soon** -- one reply per correspondent per `EMAIL_REPLY_COOLDOWN_MINUTES`.
   One clock, restarted by *any* outbound: a rep's reply satisfies the buyer's
   message exactly as Liner's does, and two clocks would let a release fire an
   immediate second answer to something a person already handled.
5. **Too many, everywhere** -- an hourly ceiling across all correspondents.
   Per-correspondent stops one loop; a spam run across five hundred addresses
   walks straight past it, because every one is a first contact. On breach this
   throws the kill switch itself rather than waiting for somebody to wake up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import flags
from app.config import settings
from app.db import utcnow
from app.models import Lead, Outreach

#: Why the ceiling tripped, written onto the flag so the morning after does not
#: read as somebody having switched it off by hand.
TRIPPED = (
    "Switched off automatically: more than {ceiling} replies went out in an hour, "
    "which is what a loop or a spam run looks like. Nothing further will be sent "
    "until this is turned back on."
)


@dataclass
class Verdict:
    """Whether to answer, and -- when not -- something a person can act on."""

    allowed: bool
    #: A short machine-readable reason, for the receipt and for the gate.
    reason: str = ""
    #: The same thing in words, for whoever reads it on a screen.
    detail: str = ""


def enabled(db: Session) -> Verdict:
    """Is the email agent on at all?

    Two switches, and the stricter wins. `.env` is the deployment's answer and
    needs a restart, which is right for "this dealership has not turned it on"
    and wrong for "make it stop now"; the runtime flag is the second, and takes
    effect on the next request.
    """
    if not settings.email_agent:
        return Verdict(
            False, "off_in_env",
            "EMAIL_AGENT is not set, so Liner does not answer email on this "
            "deployment. Taking over a mailbox is a decision a dealership "
            "makes, not a side effect of configuring the chat agent.",
        )
    if flags.get(db, "email_agent") != "on":
        return Verdict(
            False, "switched_off",
            "Liner's email replies are switched off in the dashboard.",
        )
    return Verdict(True)


def may_reply(db: Session, lead: Lead, *, automated: str = "") -> Verdict:
    """The whole decision, for one inbound message from one buyer.

    Called with `automated` set to `automated_reason`'s verdict from intake --
    the headers only exist in the request, and this runs afterwards.
    """
    switch = enabled(db)
    if not switch.allowed:
        return switch

    if automated:
        return Verdict(
            False, "automated",
            f"No reply: {automated}. Answering a machine is either shouting "
            "into a void or the first turn of a loop between two robots.",
        )

    now = utcnow()
    sent = (
        db.query(Outreach)
        .filter(
            Outreach.lead_id == lead.id,
            Outreach.channel == "email",
            Outreach.direction == "out",
        )
        .order_by(Outreach.created_at.desc())
        .first()
    )
    if sent is not None:
        # A person's reply *is* the answer. `sent_by_user_id` is the whole test
        # and it needed no new column: a rep's send carries their id and
        # Liner's carries NULL.
        last_at = sent.sent_at or sent.created_at
        since = now - (last_at or now)
        window = timedelta(minutes=max(settings.email_reply_cooldown_minutes, 0))
        if sent.sent_by_user_id and since < window:
            return Verdict(
                False, "person_answered",
                "A person has already replied to this buyer. Liner adding a "
                "second answer minutes later gives them two emails from the "
                "same dealership, possibly disagreeing, with no window that "
                "makes the pair legible.",
            )
        if since < window:
            left = window - since
            return Verdict(
                False, "cooldown",
                f"Answered {int(since.total_seconds() // 60)} minutes ago; "
                f"{int(left.total_seconds() // 60) + 1} to go. "
                "EMAIL_REPLY_COOLDOWN_MINUTES is the setting.",
            )

    ceiling = max(settings.email_replies_per_hour, 0)
    if ceiling:
        recent = (
            db.query(func.count(Outreach.id))
            .filter(
                Outreach.channel == "email",
                Outreach.direction == "out",
                Outreach.sent_by_user_id.is_(None),
                Outreach.created_at >= now - timedelta(hours=1),
            )
            .scalar()
        ) or 0
        if recent >= ceiling:
            # Trips the switch rather than merely refusing this one. A brake
            # that needs a human to pull it is not a brake overnight, and the
            # shape this catches -- a spam run across many addresses -- makes
            # every message a first contact that the per-correspondent clock
            # waves through.
            flags.set(
                db, "email_agent", "off",
                reason=TRIPPED.format(ceiling=ceiling),
            )
            return Verdict(
                False, "hourly_ceiling",
                TRIPPED.format(ceiling=ceiling),
            )

    return Verdict(True)
