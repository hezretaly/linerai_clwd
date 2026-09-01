"""Campaigns: reaching a group of buyers rather than answering one.

Everything else in this system is a conversation -- somebody wrote in and gets
answered. A campaign is the other direction: a reason to go back to people who
already talked to this dealership, and the reasons are things the database
already knows. The car somebody was quoted has come down. The car they asked
about is still sitting there. A buyer went quiet three weeks ago.

**Nothing here sends anything, and the page says so on every card.** What it
does do is count the audience *for real*, from rows, because that is the part
worth having early and the part that cannot be faked: "41 buyers were quoted a
car that is now cheaper" is either true of this database or it is not. A
mockup with a plausible number on it would be the one thing this codebase has
consistently refused to build.

**Where the data does not exist, the card says which data.** The Instagram and
Facebook cards have no audience at all -- there is no integration, no inbox and
no token -- so they report that instead of a number, exactly as
`/api/integrations` reports a missing sender. A campaign card sitting at zero
reads as a quiet week; one that says "no Instagram integration" reads as the
truth.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.db import get_db, utcnow
from app.models import (
    Appointment,
    Conversation,
    Lead,
    Message,
    User,
    Vehicle,
    VehicleMention,
)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

#: How long a buyer has to have been silent to count as gone cold. Longer than
#: `LIVE_AFTER_MINUTES` by a wide margin: that one is about whether a
#: conversation is still happening, this is about whether a person has moved on.
COLD_DAYS = 14


def _price_drops(db: Session) -> tuple[int, list[dict]]:
    """Buyers quoted a car that now costs less, and by how much.

    Real, and computable today because `vehicle_mentions.quoted_price` records
    what the buyer was actually told at the time. No price-history table is
    needed: the quote *is* the history, one row per time Liner named the car.
    """
    rows = (
        db.query(
            Lead.id, Lead.name, Lead.email,
            Vehicle.year, Vehicle.make, Vehicle.model,
            VehicleMention.quoted_price, Vehicle.price,
        )
        .join(Conversation, Conversation.id == VehicleMention.conversation_id)
        .join(Lead, Lead.id == Conversation.lead_id)
        .join(Vehicle, Vehicle.id == VehicleMention.vehicle_id)
        .filter(
            Vehicle.status == "available",
            Vehicle.price.is_not(None),
            VehicleMention.quoted_price.is_not(None),
            Vehicle.price < VehicleMention.quoted_price,
        )
        .all()
    )
    # One buyer per car, not one per time it was mentioned: a buyer told about
    # the same Silverado four times is one person to write to.
    seen: dict[tuple[str, str], dict] = {}
    for lead_id, name, email, year, make, model, quoted, now in rows:
        key = (lead_id, f"{year} {make} {model}")
        if key in seen:
            continue
        seen[key] = {
            "lead_id": lead_id,
            "name": name or email or "Unnamed buyer",
            "vehicle": f"{year} {make} {model}",
            "was": quoted,
            "now": now,
            "saving": quoted - now,
        }
    examples = sorted(seen.values(), key=lambda r: -r["saving"])[:5]
    return len(seen), examples


def _gone_cold(db: Session) -> tuple[int, list[dict]]:
    """Buyers who talked, never booked, and have not been heard from since."""
    cutoff = utcnow() - timedelta(days=COLD_DAYS)
    booked = db.query(Appointment.lead_id).filter(
        Appointment.status.in_(["booked", "confirmed"])
    )
    last = (
        db.query(Conversation.lead_id, func.max(Message.created_at).label("at"))
        .join(Message, Message.conversation_id == Conversation.id)
        .filter(Conversation.lead_id.is_not(None))
        .group_by(Conversation.lead_id)
        .subquery()
    )
    rows = (
        db.query(Lead.id, Lead.name, Lead.email, last.c.at)
        .join(last, last.c.lead_id == Lead.id)
        .filter(last.c.at < cutoff, ~Lead.id.in_(booked))
        .order_by(last.c.at.desc())
        .all()
    )
    examples = [
        {"lead_id": i, "name": n or e or "Unnamed buyer", "last_seen": str(at)[:10]}
        for i, n, e, at in rows[:5]
    ]
    return len(rows), examples


def _still_here(db: Session) -> tuple[int, list[dict]]:
    """Buyers whose car is still on the lot and who never came in to see it."""
    booked = db.query(Appointment.lead_id).filter(
        Appointment.status.in_(["booked", "confirmed"])
    )
    rows = (
        db.query(Lead.id, Lead.name, Lead.email, Vehicle.year, Vehicle.make, Vehicle.model)
        .join(Conversation, Conversation.lead_id == Lead.id)
        .join(VehicleMention, VehicleMention.conversation_id == Conversation.id)
        .join(Vehicle, Vehicle.id == VehicleMention.vehicle_id)
        .filter(Vehicle.status == "available", ~Lead.id.in_(booked))
        .distinct()
        .all()
    )
    examples = [
        {"lead_id": i, "name": n or e or "Unnamed buyer",
         "vehicle": f"{y} {mk} {md}"}
        for i, n, e, y, mk, md in rows[:5]
    ]
    return len({r[0] for r in rows}), examples


@router.get("")
def list_campaigns(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    """Every campaign this dealership could run, and who is in each.

    The audiences are counted from rows on every request rather than stored.
    A campaign is a *question about the database* -- "who was quoted a car that
    is now cheaper" -- and the answer changes whenever the lot does, so a
    cached one would be wrong by the time somebody read it.
    """
    drops, drop_examples = _price_drops(db)
    cold, cold_examples = _gone_cold(db)
    waiting, waiting_examples = _still_here(db)

    return {
        # Named once here so the page cannot disagree with the API about what
        # is built. Every one of these is `sends: false` today.
        "campaigns": [
            {
                "key": "price_drop",
                "name": "Price dropped on a car they asked about",
                "why": (
                    "The strongest reason there is to write to somebody: they told "
                    "you what they wanted and the number moved in their favour."
                ),
                "channel": "email",
                "audience": drops,
                "examples": drop_examples,
                "ready": True,
                "blocked_by": "",
            },
            {
                "key": "still_available",
                "name": "The car they were looking at is still here",
                "why": (
                    "They asked about it and never came in. Nothing has been lost "
                    "yet, which is exactly the window worth using."
                ),
                "channel": "email",
                "audience": waiting,
                "examples": waiting_examples,
                "ready": True,
                "blocked_by": "",
            },
            {
                "key": "gone_cold",
                "name": f"Went quiet more than {COLD_DAYS} days ago",
                "why": (
                    "A buyer who stopped answering has usually bought elsewhere or "
                    "put it off. One of those is worth a message."
                ),
                "channel": "email",
                "audience": cold,
                "examples": cold_examples,
                "ready": True,
                "blocked_by": "",
            },
            {
                "key": "sale_event",
                "name": "A sale is on",
                "why": (
                    "The one campaign with a date rather than a trigger, so it is "
                    "also the one that most needs a person to decide who gets it."
                ),
                "channel": "email",
                # Deliberately not counted. Every other card here answers a
                # question about the buyer; this one is a decision about the
                # dealership's calendar, and putting "everyone" next to it
                # invites exactly the untargeted blast the others avoid.
                "audience": None,
                "examples": [],
                "ready": True,
                "blocked_by": "",
            },
            {
                "key": "instagram",
                "name": "Instagram",
                "why": "Buyers message dealerships on Instagram more than they email.",
                "channel": "instagram",
                "audience": None,
                "examples": [],
                "ready": False,
                # Named, not "coming soon". The whole cost of an unbuilt
                # integration is the hour spent working out what it needs.
                "blocked_by": (
                    "No Instagram integration: this needs a Meta app, a Page "
                    "connected to a business account, and the messaging webhook. "
                    "Nothing is sent or received today."
                ),
            },
            {
                "key": "facebook",
                "name": "Facebook",
                "why": "Marketplace and Page messages land in the same inbox as Instagram.",
                "channel": "facebook",
                "audience": None,
                "examples": [],
                "ready": False,
                "blocked_by": (
                    "No Facebook integration: same Meta app and webhook as "
                    "Instagram. Nothing is sent or received today."
                ),
            },
            {
                "key": "sms",
                "name": "Text message",
                "why": "A phone number is the thing Liner asks for, and it cannot text it.",
                "channel": "sms",
                "audience": None,
                "examples": [],
                "ready": False,
                "blocked_by": (
                    "No SMS provider. This needs a number, a carrier registration "
                    "and per-message consent recorded against each buyer."
                ),
            },
        ],
        # **The whole page in one sentence, served rather than written into it.**
        # A campaign list that looked ready to press would be the one place this
        # product claimed something it cannot do.
        "note": (
            "Audiences are counted from real rows and update as the lot does. "
            "Nothing here sends yet -- there is no scheduler, so a campaign is a "
            "list a rep works through from each buyer's page."
        ),
        "cold_days": COLD_DAYS,
    }
