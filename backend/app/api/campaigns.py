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

from app import appointment_scope, timeline
from app.api.deps import current_user
from app.db import get_db, utcnow
from app.models import (
    Appointment,
    Conversation,
    Lead,
    User,
    Vehicle,
    VehicleMention,
)
from app.schemas.serialize import stamp

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

#: How long a buyer has to have been silent to count as gone cold. Longer than
#: `threads.LIVE_AFTER` by a wide margin: that one is about whether a
#: conversation is still happening, this is about whether a person has moved on.
COLD_DAYS = 14

#: How many buyers a card shows by name.
EXAMPLES = 5


def _audience(rows: list[dict]) -> tuple[int, list[dict], int]:
    """`rows`: one dict per (buyer, reason), each carrying `lead_id`, already
    in the order the card should prefer them. Returns `(buyers, examples,
    more)`: one example line per *buyer* -- their first/best reason, in the
    order they first appear -- and `more`, the buyers not shown.

    **The one place "how many buyers" is decided**, so a caller cannot repeat
    a lead across its own first-five rows and then report it as extra
    audience. Before this: `_price_drops` keyed its rows on `(lead_id,
    vehicle_id)` and reported `len(seen)` as the audience, so one buyer
    quoted two dropped cars read "2 buyers right now" (item 41); `_still_here`
    took its first five *(lead, vehicle)* pairs as examples but its audience
    from `len({distinct lead ids})`, so a repeated buyer among those five
    made `audience - len(examples)` (the frontend's arithmetic) undercount
    "and N more" by one per repeat, and a two-car buyer could show as "2
    buyers right now" the same way (item 43).
    """
    first: dict[str, dict] = {}
    for r in rows:
        first.setdefault(r["lead_id"], r)
    examples = list(first.values())[:EXAMPLES]
    return len(first), examples, len(first) - len(examples)


def _price_drop_rows(db: Session) -> list[dict]:
    """One row per (lead, vehicle): that pair's *newest* mention carrying a
    quoted price, whichever price that was -- picked *before* comparing to
    today's price.

    Before this, the price-drop filter (`Vehicle.price < quoted_price`) ran
    in SQL ahead of picking the newest quote, so a buyer re-quoted at the
    car's current (already-dropped) price still matched on an *older*, higher
    quote and was reported as still owed a price-drop email at a price they
    had already been told (item 41). `agent/tools.py`'s `_record_mentions`
    writes a fresh `quoted_price` every time Liner names a car, so a re-quote
    after a drop is the ordinary case, not an edge one.

    Keyed on `VehicleMention.vehicle_id`, never on the display label: two
    different cars can share one ("2022 Tesla Model S" is not unique on a
    real lot), and keying on the string silently merged their audiences and
    picked whichever one the query happened to see first.
    """
    from sqlalchemy import func as sa_func

    ranked = (
        db.query(
            Conversation.lead_id.label("lead_id"),
            VehicleMention.vehicle_id.label("vehicle_id"),
            VehicleMention.quoted_price.label("quoted_price"),
            sa_func.row_number().over(
                partition_by=(Conversation.lead_id, VehicleMention.vehicle_id),
                order_by=(VehicleMention.created_at.desc(), VehicleMention.id.asc()),
            ).label("rn"),
        )
        .join(Conversation, Conversation.id == VehicleMention.conversation_id)
        .filter(
            Conversation.lead_id.is_not(None),
            VehicleMention.quoted_price.is_not(None),
        )
        .subquery()
    )
    rows = (
        db.query(
            ranked.c.lead_id, Lead.name, Lead.email,
            Vehicle.year, Vehicle.make, Vehicle.model,
            ranked.c.quoted_price, Vehicle.price,
        )
        .join(Vehicle, Vehicle.id == ranked.c.vehicle_id)
        .join(Lead, Lead.id == ranked.c.lead_id)
        .filter(
            ranked.c.rn == 1,
            Vehicle.status == "available",
            Vehicle.price.is_not(None),
            # `Lead.has_email`: the audience for an `channel="email"` card
            # marked "ready to run" must be buyers this system can actually
            # email -- a facebook-sourced lead with no address never reaches
            # the composer or the buyer page's email box (item 38).
            Lead.has_email,
            Vehicle.price < ranked.c.quoted_price,
        )
        .all()
    )
    out = [
        {
            "lead_id": lead_id,
            "name": name or email or "Unnamed buyer",
            "vehicle": f"{year} {make} {model}",
            "was": quoted,
            "now": now,
            "saving": quoted - now,
        }
        for lead_id, name, email, year, make, model, quoted, now in rows
    ]
    # Largest saving first within a buyer's own rows, so `_audience`'s
    # first-seen-per-lead pick keeps their best example.
    out.sort(key=lambda r: -r["saving"])
    return out


def _price_drops(db: Session) -> tuple[int, list[dict], int]:
    """Buyers quoted a car that now costs less, and by how much."""
    return _audience(_price_drop_rows(db))


def _gone_cold(db: Session) -> tuple[int, list[dict], int]:
    """Buyers who talked, have no standing booking, and have not been heard
    from since. "Heard from" -- `timeline.last_heard_query` -- is the buyer's
    own words: a message they typed, an email or text they sent, never
    Liner's own reply, which is always the newest row in an answered thread
    and used to make "last heard" read as "we last spoke", one clock-hour
    or one calendar day later than the truth (item 40).
    """
    cutoff = utcnow() - timedelta(days=COLD_DAYS)
    booked = db.query(Appointment.lead_id).filter(
        Appointment.status.in_(appointment_scope.STANDING_STATUSES)
    )
    last = timeline.last_heard_query(db).subquery()
    rows = (
        db.query(Lead.id, Lead.name, Lead.email, last.c.at)
        .join(last, last.c.lead_id == Lead.id)
        .filter(last.c.at < cutoff, ~Lead.id.in_(booked), Lead.has_email)
        .order_by(last.c.at.desc())
        .all()
    )
    out = [
        {
            "lead_id": lead_id, "name": name or email or "Unnamed buyer",
            # The real instant, not `str(at)[:10]` (a naive-UTC date that is
            # already tomorrow's from the evening on, at a dealership west of
            # Greenwich). The frontend renders it in the dealership's own
            # zone, the same as everything else timestamped here.
            "last_heard_at": stamp(at),
        }
        for lead_id, name, email, at in rows
    ]
    return _audience(out)


def _still_here(db: Session) -> tuple[int, list[dict], int]:
    """Buyers whose car is still on the lot and who never came in to see it.

    A visit that "happened" is read the same way the calendar and the Overview
    read one -- `appointment_scope.STANDING_STATUSES` (booked/confirmed) is
    the only "still on the books" test, and a `completed`/`cancelled`/
    `no_show` row is not one (item 39; `completed` is a status only the demo
    seed ever wrote and the app itself never produces).
    """
    booked = db.query(Appointment.lead_id).filter(
        Appointment.status.in_(appointment_scope.STANDING_STATUSES)
    )
    # Grouped, not `.distinct()`: the old `.distinct()` over every selected
    # column had no `ORDER BY` at all, so which (lead, vehicle) pair came back
    # first depended on the storage engine's own row order (SQLite: rowid
    # insertion order; Postgres: whichever plan the query happened to pick,
    # unstable across reads) -- so re-adding an old two-car conversation to
    # the audience (a sold car re-listed, a cancelled appointment) put its
    # *oldest* mention first every time, not its most recent. Grouping on
    # each table's own primary key lets Postgres treat every other selected
    # column of that table as functionally determined, so this is portable.
    rows = (
        db.query(
            Lead.id, Lead.name, Lead.email,
            Vehicle.id, Vehicle.year, Vehicle.make, Vehicle.model,
            func.max(VehicleMention.created_at).label("mentioned_at"),
        )
        .join(Conversation, Conversation.lead_id == Lead.id)
        .join(VehicleMention, VehicleMention.conversation_id == Conversation.id)
        .join(Vehicle, Vehicle.id == VehicleMention.vehicle_id)
        .filter(Vehicle.status == "available", ~Lead.id.in_(booked), Lead.has_email)
        .group_by(Lead.id, Vehicle.id)
        .order_by(func.max(VehicleMention.created_at).desc(), Lead.id.asc())
        .all()
    )
    out = [
        {"lead_id": i, "name": n or e or "Unnamed buyer", "vehicle": f"{y} {mk} {md}"}
        for i, n, e, _vid, y, mk, md, _mentioned_at in rows
    ]
    return _audience(out)


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
    drops, drop_examples, drops_more = _price_drops(db)
    cold, cold_examples, cold_more = _gone_cold(db)
    waiting, waiting_examples, waiting_more = _still_here(db)

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
                # Buyers not among the examples -- computed here, once, so
                # the frontend never re-derives it as `audience -
                # examples.length` (which is buyers minus buyers only when
                # neither side has repeated a lead; see `_audience`, item 43).
                "more": drops_more,
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
                "more": waiting_more,
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
                "more": cold_more,
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
                "more": None,
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
                "more": None,
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
                "more": None,
                "ready": False,
                "blocked_by": (
                    "No Facebook integration: same Meta app and webhook as "
                    "Instagram. Nothing is sent or received today."
                ),
            },
            {
                "key": "sms",
                "name": "Text message",
                "why": "A phone number is the thing Liner asks for, and now a rep can text it.",
                "channel": "sms",
                "audience": None,
                "examples": [],
                "more": None,
                # **Still blocked, and for a narrower reason than before.**
                # One-to-one texting is real now -- a rep sends from the buyer's
                # page and replies land on their timeline. What a *campaign*
                # additionally needs is A2P 10DLC registration: sending the same
                # message to a list from a number that is not registered as a
                # brand gets it filtered by the carriers rather than refused by
                # Twilio, which is the worst of both -- billed, and delivered to
                # nobody. Flipping this to ready because sending works would be
                # exactly the "looked ready to press" failure the note below
                # exists to prevent.
                "ready": False,
                "blocked_by": (
                    "Texting one buyer works from their own page. A campaign needs "
                    "A2P 10DLC brand and campaign registration first -- without it "
                    "carriers filter bulk sends silently, so they are billed and "
                    "never arrive."
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
