"""The dashboard's first paint in one call.

Every KPI and every sidebar badge count is computed here and nowhere else --
the mockups disagreed with themselves across pages (Conversations 4 vs 3, Leads
31 vs 12) precisely because each page counted for itself (§18.4).
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_dealership
from app.api.settings import live_settings
from app.db import get_db, utcnow
from app.models import (
    Appointment,
    Conversation,
    Dealership,
    Escalation,
    Lead,
    Message,
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

    def convos_on(channel: str) -> int:
        return (
            db.query(Conversation)
            .filter(Conversation.started_at >= since, Conversation.channel == channel)
            .count()
        )

    chats = convos_on("chat")
    calls = convos_on("voice")
    # Real rows, and only the ones that actually went. A queued or failed send
    # is not an email the buyer received, and counting it would make the card
    # read best when delivery is broken.
    emails_sent = (
        db.query(Outreach)
        .filter(Outreach.created_at >= since, Outreach.status == "sent")
        .count()
    )
    credit_sent = (
        db.query(Outreach)
        .filter(
            Outreach.created_at >= since,
            Outreach.status == "sent",
            Outreach.kind == "credit_application",
        )
        .count()
    )
    # The number that means something is how many were opened. Sending is the
    # dealership's own activity; a buyer following the link is the buyer doing
    # something, which is the only part that says the outreach worked.
    credit_opened = (
        db.query(Outreach)
        .filter(
            Outreach.created_at >= since,
            Outreach.status == "sent",
            Outreach.kind == "credit_application",
            Outreach.click_count > 0,
        )
        .count()
    )
    credit_url = (live_settings(db).credit_application_url or "").strip()
    appointments_set = db.query(Appointment).filter(Appointment.created_at >= since).count()
    leads_captured = db.query(Lead).filter(Lead.created_at >= since).count()
    # Oldest first: this is a triage queue, and the overview quotes the longest
    # wait in its subheading. Without an explicit order the "oldest" is just
    # whatever the database returned last.
    open_escalations = (
        db.query(Escalation)
        .filter(Escalation.claimed_at.is_(None))
        .order_by(Escalation.created_at.asc())
        .all()
    )

    unconfirmed = (
        db.query(Appointment)
        .filter(Appointment.status == "booked")
        .order_by(Appointment.starts_at.asc())
        .all()
    )
    unassigned = [a for a in unconfirmed if a.assigned_user_id is None]
    active_conversations = (
        db.query(Conversation)
        .filter(Conversation.status.in_(["active", "handoff"]))
        .order_by(Conversation.started_at.desc())
        .all()
    )
    # "Happening now" is the last two hours; the panel expands to the rest of
    # today. Both come from one list so the client splits rather than refetches.
    #
    # Ordered on last *activity*, not on start: a conversation opened at nine
    # with a message two minutes ago is the most live thing on the screen, and
    # sorting by started_at buried it under quieter, newer threads.
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = (
        db.query(Conversation)
        .filter(Conversation.started_at >= start_of_day)
        .all()
    )
    last_activity = dict(
        db.query(Message.conversation_id, func.max(Message.created_at))
        .filter(Message.conversation_id.in_([c.id for c in today] or [""]))
        .group_by(Message.conversation_id)
        .all()
    )

    def activity_of(convo: Conversation):
        return last_activity.get(convo.id) or convo.started_at

    today.sort(key=activity_of, reverse=True)
    day_payload = [
        {**conversation_out(c, db), "last_activity_at": activity_of(c).isoformat()}
        for c in today
    ]
    # Nobody owns these yet. There is no round-robin in this system, so the
    # queue is exactly "assigned to no one", oldest first -- not a rotation.
    unclaimed_leads = (
        db.query(Lead)
        .filter(Lead.assigned_user_id.is_(None))
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
    stale_rows = (
        db.query(Vehicle, func.count(VehicleMention.id))
        .join(VehicleMention, VehicleMention.vehicle_id == Vehicle.id)
        .filter(Vehicle.status != "available")
        .group_by(Vehicle.id)
        .all()
    )
    inventory_issues = [
        {**vehicle_out(v, mentions=count), "quoted_to": count} for v, count in stale_rows
    ]

    return {
        "dealership": dealership_out(dealership),
        "generated_at": now.isoformat(),
        "kpis": [
            {"key": "chat", "label": "Chats",
             "value": chats, "window": "last 24 hours"},
            {"key": "email", "label": "Emails sent",
             "value": emails_sent, "window": "last 24 hours"},
            # Voice conversations. The count is real -- the seed has them and the
            # transcript endpoints work -- but no voice vendor is configured, so
            # nothing new arrives here until one is. The banner says so.
            {"key": "calls", "label": "Calls",
             "value": calls, "window": "last 24 hours"},
            {"key": "appointments_set", "label": "Appointments set",
             "value": appointments_set, "window": "last 24 hours"},
            {"key": "needs_a_person", "label": "Needs a person",
             "value": len(open_escalations), "window": "open now"},
            # Counts applications a rep actually sent. With no application URL
            # configured there is nothing to send, and the card says that rather
            # than showing a zero that looks like a quiet day.
            {"key": "credit_apps", "label": "Credit applications",
             "value": credit_opened,
             "window": (
                 f"opened, of {credit_sent} sent -- last 24 hours" if credit_url
                 else "no application link set"
             ),
             "unavailable": not credit_url},
        ],
        "leads_captured": leads_captured,
        "badges": {
            "conversations": len(active_conversations),
            "appointments": len(unconfirmed),
            "escalations": len(open_escalations),
            "inventory": len(inventory_issues),
        },
        "queues": {
            "needs_a_person": [escalation_out(e, db) for e in open_escalations],
            "unconfirmed_appointments": [appointment_out(a, db) for a in unconfirmed],
            "unassigned_appointments": [appointment_out(a, db) for a in unassigned],
            # The whole day, newest activity first. The client shows the
            # last two hours and expands to the rest -- see happening_now_since.
            "active_conversations": day_payload,
            "unclaimed_leads": [
                {**lead_out(lead, db), "conversation_id": convo_of.get(lead.id)}
                for lead in unclaimed_leads
            ],
            "inventory_issues": inventory_issues,
        },
        "happening_now_since": (now - timedelta(hours=2)).isoformat(),
        "mix": _channel_mix(db, since),
        "source_mix": _source_mix(db, since),
        "by_hour": _by_hour(db, dealership, start_of_day, now),
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


def _window(now, key: str):
    """(start, end) for a range key. Naive and dealership-local throughout --
    these are the same clock the conversations were stamped with."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if key == "yesterday":
        return midnight - timedelta(days=1), midnight
    if key == "week":
        return midnight - timedelta(days=6), now
    if key == "month":
        return midnight - timedelta(days=29), now
    return midnight, now


def _custom_window(now, first: str, last: str):
    """(start, end, label) for an explicit date, or a date to a date.

    `to` defaults to `from`, so one date is a legal answer -- picking a single
    day is the common case and should not need the same date typed twice. The
    end is exclusive midnight of the day after, or a range ending today would
    stop at 00:00 and show nothing.
    """
    try:
        start_date = date.fromisoformat(first)
        end_date = date.fromisoformat(last) if last else start_date
    except ValueError:
        raise HTTPException(400, "from and to must be dates, as YYYY-MM-DD") from None

    if end_date < start_date:
        raise HTTPException(400, "`from` is after `to`.")
    if (end_date - start_date).days + 1 > MAX_SPAN_DAYS:
        raise HTTPException(400, f"That is more than {MAX_SPAN_DAYS} days.")

    start = datetime.combine(start_date, time.min)
    end = min(datetime.combine(end_date, time.min) + timedelta(days=1), now)
    if start_date == end_date:
        label = f"{start_date:%a} {start_date.day} {start_date:%B}"
    elif start_date.year == end_date.year:
        label = f"{start_date.day} {start_date:%b} to {end_date.day} {end_date:%b}"
    else:
        label = f"{start_date.day} {start_date:%b %Y} to {end_date.day} {end_date:%b %Y}"
    return start, end, label


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
    now = utcnow()
    if from_:
        # Explicit dates win. Sending both a range and a from would otherwise
        # answer for one of them silently, and the caption would name the other.
        start, end, label = _custom_window(now, from_, to)
        range = "custom"
    else:
        if range not in RANGES:
            raise HTTPException(
                400, f"range must be one of: {', '.join(RANGES)}, or pass from/to"
            )
        start, end = _window(now, range)
        label = RANGES[range]

    first_day = start.date()
    last_day = (end - timedelta(microseconds=1)).date()
    return {
        "range": range,
        "label": label,
        "from": first_day.isoformat(),
        "to": last_day.isoformat(),
        # Calendar days the window covers, counting both ends. Deriving it from
        # `end` instead made a range ending today one day shorter than the same
        # range asked for tomorrow, because `end` is clamped to now.
        "days": (last_day - first_day).days + 1,
        "conversations": (
            db.query(Conversation)
            .filter(Conversation.started_at >= start, Conversation.started_at < end)
            .count()
        ),
        "by_hour": _by_hour(db, dealership, start, end),
        "source_mix": _source_mix(db, start, end),
    }


def _channel_mix(db: Session, since) -> list[dict]:
    rows = (
        db.query(Conversation.channel, func.count(Conversation.id))
        .filter(Conversation.started_at >= since)
        .group_by(Conversation.channel)
        .all()
    )
    return [{"channel": channel, "count": count} for channel, count in rows]


def _source_mix(db: Session, start, end=None) -> list[dict]:
    """Where leads came from -- `leads.source`, not the conversation channel.

    The two are different axes and the overview shows both: a buyer can arrive
    from the website and then be handled over voice.
    """
    query = db.query(Lead.source, func.count(Lead.id)).filter(Lead.created_at >= start)
    if end is not None:
        query = query.filter(Lead.created_at < end)
    rows = query.group_by(Lead.source).all()
    return [{"source": source, "count": count} for source, count in rows]


def _by_hour(db: Session, dealership: Dealership, start, end) -> list[dict]:
    """Conversations in the window bucketed by hour of day, open or closed.

    The point the chart makes is that Liner answers when the showroom cannot,
    so every bucket carries whether the dealership was open at that hour. That
    comes from `hours_json` -- never a hardcoded 8-to-6. Timestamps are naive
    and already in the dealership's local frame, so `.hour` is the local hour.

    Over more than a day, `open` is "open at that hour on most days in the
    window". A Sunday in a seven-day window does not make 10 AM a closed hour,
    and requiring every day would paint the whole week closed.
    """
    day_names = [
        "monday", "tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday",
    ]
    hours = json.loads(dealership.hours_json or "{}")

    days = []
    cursor = start.date()
    while cursor < end.date() or cursor == start.date():
        days.append(hours.get(day_names[cursor.weekday()]))
        cursor += timedelta(days=1)

    counts = _bucket(db, start, end)

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


def _bucket(db: Session, start, end) -> dict[int, int]:
    """Bucket in Python rather than SQL.

    `strftime` is SQLite-only and `date_part` is Postgres-only; the Postgres
    door stays open, so neither goes in a query. Today's conversations are a
    small enough set that the loop costs nothing.
    """
    rows = (
        db.query(Conversation.started_at)
        .filter(Conversation.started_at >= start, Conversation.started_at < end)
        .all()
    )
    counts: dict[int, int] = {}
    for (started_at,) in rows:
        counts[started_at.hour] = counts.get(started_at.hour, 0) + 1
    return counts
