"""The dashboard's first paint in one call.

Every KPI and every sidebar badge count is computed here and nowhere else --
the mockups disagreed with themselves across pages (Conversations 4 vs 3, Leads
31 vs 12) precisely because each page counted for itself (§18.4).
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import appointment_scope, clock, escalations, ownership, threads
from app.api.deps import current_user, get_dealership
from app.api.inventory import quoted_buyer_counts
from app.api.redirect import opens_between
from app.api.settings import live_settings
from app.db import get_db, utcnow
from app.models import (
    Appointment,
    Conversation,
    Dealership,
    Lead,
    Outreach,
    User,
    Vehicle,
    VehicleMention,
)
from app.schemas.serialize import (
    appointment_out,
    conversation_out,
    dealership_out,
    escalation_out,
    lead_out,
    stamp,
    vehicle_out,
)

router = APIRouter(tags=["overview"])


@router.get("/overview")
def overview(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    dealership: Dealership = Depends(get_dealership),
) -> dict:
    now = utcnow()
    since = now - timedelta(hours=24)

    # Only conversations the buyer spoke in -- the same rule the list applies,
    # from `app/threads.py`. A chat widget opened and closed again is a row,
    # and counting it makes a quiet afternoon read as fourteen chats.
    def convos_on(channel: str) -> int:
        return (
            threads.conversations(db)
            .filter(Conversation.started_at >= since, Conversation.channel == channel)
            .count()
        )

    chats = convos_on("chat")

    # Real rows, and only the ones that actually went. A queued or failed send
    # is not an email the buyer received, and counting it would make the card
    # read best when delivery is broken.
    emails_sent = (
        db.query(Outreach)
        .filter(Outreach.created_at >= since, Outreach.status == "sent")
        .count()
    )
    credit_url = (live_settings(db).credit_application_url or "").strip()
    # Both halves windowed on the moment the buyer opened it, not on when the
    # dealership sent the link -- see `redirect.opens_between`.
    credit_opens = opens_between(db, "credit_application", since)

    appointments_set = (
        db.query(Appointment)
        .filter(
            Appointment.created_at >= since,
            Appointment.status.in_(appointment_scope.STANDING_STATUSES),
        )
        .count()
    )
    leads_captured = db.query(Lead).filter(Lead.created_at >= since).count()

    # The one definition of "needs a person": every buyer with at least one
    # unclaimed escalation, keyed by person rather than by escalation row --
    # so someone with three open threads counts once, the way the list shows
    # them, not three times the way the raw row count used to.
    waiting = escalations.waiting_on_person(db)
    needs_a_person_rows = [
        {**escalation_out(rows[0], db), "escalation_count": len(rows)}
        for rows in waiting.values()
    ]
    needs_a_person_rows.sort(key=lambda r: r["created_at"] or "")

    unconfirmed = appointment_scope.unconfirmed(db, dealership)
    unassigned = [a for a in unconfirmed if a.assigned_user_id is None]

    # The sidebar Conversations badge. The one definition of "live right
    # now" -- `threads.live_keys` -- so this equals the Conversations page's
    # In progress card for the same data, rather than counting every open
    # thread ever (which is the figure CLAUDE.md's "Live means still being
    # said" bullet retired, and which used to still be what this badge read).
    live_now = threads.live_keys(db, now)

    # "Everything today", the panel that expands past the live rows: threads
    # that started since dealership-local midnight. Ordered on last
    # *activity*, not on start, so a thread opened at nine with a message two
    # minutes ago is not buried under quieter, newer threads.
    start_of_day = clock.day_start_utc(dealership, clock.today(dealership))
    # `listed`, not the plain `conversations()`/`started()` base: an escalated
    # caller with no transcribed word is still someone this panel, and the
    # badge built from the same rows, has to show -- see `threads.listed`.
    today = (
        db.query(Conversation)
        .filter(threads.listed(db), Conversation.started_at >= start_of_day)
        .all()
    )
    last_activity = threads.last_activity(db, [c.id for c in today])

    def activity_of(convo: Conversation):
        return last_activity.get(convo.id) or convo.started_at

    today.sort(key=activity_of, reverse=True)
    day_payload = [
        {
            **conversation_out(c, db),
            "last_activity_at": stamp(activity_of(c)),
            "live": threads.is_live(c, last_activity.get(c.id), now),
        }
        for c in today
    ]
    # Nobody owns these yet. There is no round-robin in this system, so the
    # queue is exactly "assigned to no one", oldest first -- not a rotation.
    # A lead with no owner, never an anonymous thread: there is nothing to
    # claim until a lead exists (`app/ownership.py`).
    unclaimed_leads = (
        db.query(Lead)
        .filter(ownership.unclaimed())
        .order_by(Lead.created_at.asc())
        .all()
    )
    # Their thread, so the panel row can open the conversation rather than a
    # profile. One query for the lot -- a row each would be a query each.
    convo_of = {}
    if unclaimed_leads:
        for lead_id, convo_id in (
            db.query(Conversation.lead_id, Conversation.id)
            .filter(Conversation.lead_id.in_([lead.id for lead in unclaimed_leads]))
            .order_by(Conversation.started_at.desc())
            .all()
        ):
            convo_of.setdefault(lead_id, convo_id)

    # Blast radius: vehicles no longer available that Liner has quoted (§18.2).
    # Counted in buyers, not in `vehicle_mentions` rows -- one buyer named the
    # same car across several channels, or quoted it twice in one chat, is one
    # buyer, not several offers.
    stale_vehicle_ids = [
        v.id for v in db.query(Vehicle.id)
        .join(VehicleMention, VehicleMention.vehicle_id == Vehicle.id)
        .filter(Vehicle.status != "available")
        .distinct()
        .all()
    ]
    quoted_counts = quoted_buyer_counts(db, stale_vehicle_ids) if stale_vehicle_ids else {}
    stale_vehicles = (
        db.query(Vehicle).filter(Vehicle.id.in_(stale_vehicle_ids)).all()
        if stale_vehicle_ids else []
    )
    inventory_issues = [
        {**vehicle_out(v, mentions=quoted_counts.get(v.id, 0)), "quoted_to": quoted_counts.get(v.id, 0)}
        for v in stale_vehicles
    ]

    return {
        "dealership": dealership_out(dealership),
        "generated_at": stamp(now),
        "kpis": [
            {"key": "chat", "label": "Chats",
             "value": chats, "window": "last 24 hours"},
            {"key": "email", "label": "Emails sent",
             "value": emails_sent, "window": "last 24 hours"},
            {"key": "appointments_set", "label": "Appointments set",
             "value": appointments_set, "window": "last 24 hours"},
            {"key": "needs_a_person", "label": "Needs a person",
             "value": len(needs_a_person_rows), "window": "open now"},
            # Opens of the application, from the two places a buyer can reach
            # it: the link a rep emailed, and the storefront's Financing links.
            # Clicks, never completions -- the dealer's form reports nothing
            # back. With no application URL configured there is nothing to open,
            # and the card says that rather than a zero that reads as a quiet
            # day.
            {"key": "credit_apps", "label": "Credit applications",
             "value": credit_opens["emailed"] + credit_opens["site"],
             "window": (
                 f"opened -- {credit_opens['emailed']} from emailed links, "
                 f"{credit_opens['site']} from the website -- last 24 hours "
                 f"({credit_opens['sent']} sent)" if credit_url
                 else "no application link set"
             ),
             "unavailable": not credit_url},
        ],
        "leads_captured": leads_captured,
        "badges": {
            # People in progress right now -- the same figure as the
            # Conversations page's In progress card, not "every open thread
            # ever" (that number is `mix`/mentions elsewhere; nothing on the
            # nav names it any more).
            "conversations": len(live_now),
            "appointments": len(unconfirmed),
            "escalations": len(needs_a_person_rows),
            "inventory": len(inventory_issues),
        },
        "queues": {
            "needs_a_person": needs_a_person_rows,
            "unconfirmed_appointments": [appointment_out(a, db) for a in unconfirmed],
            "unassigned_appointments": [appointment_out(a, db) for a in unassigned],
            # Visits whose time has passed with nobody marking them
            # confirmed, cancelled or a no-show -- surfaced on its own rather
            # than hidden inside "nobody has heard back" forever.
            "unmarked_appointments": [
                appointment_out(a, db) for a in appointment_scope.unmarked(db, dealership)
            ],
            # The whole day, newest activity first, each row carrying its own
            # `live` flag. The client shows the live rows and expands to the
            # rest.
            "active_conversations": day_payload,
            "unclaimed_leads": [
                {**lead_out(lead, db), "conversation_id": convo_of.get(lead.id)}
                for lead in unclaimed_leads
            ],
            "inventory_issues": inventory_issues,
        },
        "live_window_minutes": int(threads.LIVE_AFTER.total_seconds() // 60),
        "by_hour": _by_hour(db, dealership, clock.today(dealership), clock.today(dealership)),
    }


# What a range selector on the charts may ask for. Anything else is a typo,
# and answering a typo with "today" quietly shows the wrong window.
RANGES = {
    "today": "Today, midnight to now",
    "yesterday": "Yesterday",
    "week": "Last 7 days",
    "month": "Last 30 days",
}


# A chart over more than a year of hourly buckets is a query nobody asked for
# by accident, and a typo in a date field is how you ask for it.
MAX_SPAN_DAYS = 366


def _window(dealership: Dealership, key: str):
    """(start, end, first_day, last_day) for a range key, in the dealership's
    own local day. `start`/`end` are naive UTC instants -- what the UTC-
    stamped columns are compared against -- and `first_day`/`last_day` are
    the local calendar dates the window actually covers, DST-correct via
    `app.clock`.
    """
    d = clock.today(dealership)
    if key == "yesterday":
        first_day = last_day = d - timedelta(days=1)
    elif key == "week":
        first_day, last_day = d - timedelta(days=6), d
    elif key == "month":
        first_day, last_day = d - timedelta(days=29), d
    else:
        first_day = last_day = d
    start = clock.day_start_utc(dealership, first_day)
    end = utcnow() if last_day >= d else clock.day_start_utc(dealership, last_day + timedelta(days=1))
    return start, end, first_day, last_day


def _custom_window(dealership: Dealership, first: str, last: str):
    """(start, end, first_day, last_day, label) for an explicit date, or a
    date to a date, in the dealership's own local day.

    `to` defaults to `from`, so one date is a legal answer -- picking a single
    day is the common case and should not need the same date typed twice. The
    end is exclusive local midnight of the day after, or a range ending today
    would stop at local 00:00 and show nothing.
    """
    from datetime import date

    try:
        start_date = date.fromisoformat(first)
        end_date = date.fromisoformat(last) if last else start_date
    except ValueError:
        raise HTTPException(400, "from and to must be dates, as YYYY-MM-DD") from None

    if end_date < start_date:
        raise HTTPException(400, "`from` is after `to`.")
    if (end_date - start_date).days + 1 > MAX_SPAN_DAYS:
        raise HTTPException(400, f"That is more than {MAX_SPAN_DAYS} days.")

    start = clock.day_start_utc(dealership, start_date)
    end = min(
        clock.day_start_utc(dealership, end_date + timedelta(days=1)), utcnow()
    )
    if start_date == end_date:
        label = f"{start_date:%a} {start_date.day} {start_date:%B}"
    elif start_date.year == end_date.year:
        label = f"{start_date.day} {start_date:%b} to {end_date.day} {end_date:%b}"
    else:
        label = f"{start_date.day} {start_date:%b %Y} to {end_date.day} {end_date:%b %Y}"
    return start, end, start_date, end_date, label


@router.get("/overview/trends")
def trends(
    range: str = "today",
    from_: str = Query("", alias="from"),
    to: str = Query(""),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    dealership: Dealership = Depends(get_dealership),
) -> dict:
    """The two charts over a chosen window.

    Separate from /api/overview on purpose. The KPIs and queues are one first
    paint and stay that way; changing the chart range must not refetch the
    whole dashboard, and the counts on the cards must not silently start
    meaning "last month" because someone moved a chart selector.
    """
    if from_:
        # Explicit dates win. Sending both a range and a from would otherwise
        # answer for one of them silently, and the caption would name the other.
        start, end, first_day, last_day, label = _custom_window(dealership, from_, to)
        range = "custom"
    else:
        if range not in RANGES:
            raise HTTPException(
                400, f"range must be one of: {', '.join(RANGES)}, or pass from/to"
            )
        start, end, first_day, last_day = _window(dealership, range)
        label = RANGES[range]

    counts = _bucket(db, dealership, start, end)
    return {
        "range": range,
        "label": label,
        "from": first_day.isoformat(),
        "to": last_day.isoformat(),
        # Calendar days the window covers, counting both ends, from the local
        # dates the window was built from -- deriving it from UTC instants
        # made a range ending today one day shorter than the same range asked
        # for tomorrow, and dropped today from a week/month range shown after
        # 7pm local, because the UTC date had already rolled to tomorrow.
        "days": (last_day - first_day).days + 1,
        # Derived from the same bucket counts as `by_hour`, so the two cannot
        # drift the way a separately-queried total and chart once did.
        "conversations": sum(counts.values()),
        "by_hour": _by_hour(db, dealership, first_day, last_day, counts=counts),
        "source_mix": _source_mix(db, start, end),
    }


def _source_mix(db: Session, start, end) -> list[dict]:
    """Where leads came from -- `leads.source`, not the conversation channel.

    `end` is required: an open-ended rolling window here is how the Overview
    used to serve a 24h "source_mix" that no caption on the page ever
    described (the donut's caption always names the picker's range, e.g.
    "today" or "this week"), so a first-paint race or a still-loading range
    switch showed the rolling 24h number under whichever caption the UI had
    already committed to. `/api/overview` no longer serves this field at all
    -- only `/api/overview/trends` does, so the count and its caption always
    come from the same response.
    """
    rows = (
        db.query(Lead.source, func.count(Lead.id))
        .filter(Lead.created_at >= start, Lead.created_at < end)
        .group_by(Lead.source)
        .all()
    )
    return [{"source": source, "count": count} for source, count in rows]


def _by_hour(
    db: Session, dealership: Dealership, first_day, last_day, *, counts: dict[int, int] | None = None
) -> list[dict]:
    """Conversations in the window bucketed by hour of day, open or closed.

    The point the chart makes is that Liner answers when the showroom cannot,
    so every bucket carries whether the dealership was open at that hour. That
    comes from `hours_json` -- never a hardcoded 8-to-6. Hours and days here
    are the dealership's own local ones (`app.clock`), not the UTC hour a
    naive-UTC timestamp's `.hour` used to give: from about 7pm to midnight
    local, that made an evening chat plot into tomorrow morning's bar.

    Over more than a day, `open` is "open at that hour on most days in the
    window". A Sunday in a seven-day window does not make 10 AM a closed hour,
    and requiring every day would paint the whole week closed.
    """
    import json

    day_names = [
        "monday", "tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday",
    ]
    hours = json.loads(dealership.hours_json or "{}")

    days = []
    cursor = first_day
    while cursor <= last_day:
        days.append(hours.get(day_names[cursor.weekday()]))
        cursor += timedelta(days=1)

    if counts is None:
        start = clock.day_start_utc(dealership, first_day)
        end = clock.day_start_utc(dealership, last_day + timedelta(days=1))
        counts = _bucket(db, dealership, start, end)

    def is_open(hour: int) -> bool:
        open_on = sum(
            1 for window in days
            if window and int(window["open"][:2]) <= hour < int(window["close"][:2])
        )
        return open_on * 2 > len(days)

    return [
        {"hour": h, "count": counts.get(h, 0), "open": is_open(h)}
        for h in range(24)
    ]


def _bucket(db: Session, dealership: Dealership, start, end) -> dict[int, int]:
    """Bucket in Python rather than SQL, by dealership-local hour.

    `strftime` is SQLite-only and `date_part` is Postgres-only; the Postgres
    door stays open, so neither goes in a query. Today's conversations are a
    small enough set that the loop costs nothing.

    `threads.conversations(db)` -- started(db) already applied -- so a chat
    widget opened and abandoned does not inflate this chart the way it once
    could while the KPIs, the badge and the today panel correctly ignored it.
    """
    rows = (
        threads.conversations(db, Conversation.started_at)
        .filter(Conversation.started_at >= start, Conversation.started_at < end)
        .all()
    )
    counts: dict[int, int] = {}
    for (started_at,) in rows:
        h = clock.local_hour(dealership, started_at)
        counts[h] = counts.get(h, 0) + 1
    return counts
