"""The dashboard's first paint in one call.

Every KPI and every sidebar badge count is computed here and nowhere else --
the mockups disagreed with themselves across pages (Conversations 4 vs 3, Leads
31 vs 12) precisely because each page counted for itself (§18.4).
"""

from __future__ import annotations

import json
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_dealership
from app.db import get_db, utcnow
from app.models import (
    Appointment,
    Conversation,
    Dealership,
    Escalation,
    Lead,
    Message,
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

    conversations_handled = db.query(Conversation).filter(Conversation.started_at >= since).count()
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
            {"key": "conversations_handled", "label": "Conversations handled",
             "value": conversations_handled, "window": "last 24 hours"},
            {"key": "appointments_set", "label": "Appointments set",
             "value": appointments_set, "window": "last 24 hours"},
            {"key": "leads_captured", "label": "Leads captured",
             "value": leads_captured, "window": "last 24 hours"},
            # Replaces the mockups' "Credit applications", which implied a flow
            # that is explicitly out of scope (§12, §18.4).
            {"key": "needs_a_person", "label": "Needs a person",
             "value": len(open_escalations), "window": "open now"},
        ],
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
            "unclaimed_leads": [lead_out(lead, db) for lead in unclaimed_leads],
            "inventory_issues": inventory_issues,
        },
        "happening_now_since": (now - timedelta(hours=2)).isoformat(),
        "mix": _channel_mix(db, since),
        "source_mix": _source_mix(db, since),
        "by_hour": _by_hour(db, now, dealership),
    }


def _channel_mix(db: Session, since) -> list[dict]:
    rows = (
        db.query(Conversation.channel, func.count(Conversation.id))
        .filter(Conversation.started_at >= since)
        .group_by(Conversation.channel)
        .all()
    )
    return [{"channel": channel, "count": count} for channel, count in rows]


def _source_mix(db: Session, since) -> list[dict]:
    """Where leads came from -- `leads.source`, not the conversation channel.

    The two are different axes and the overview shows both: a buyer can arrive
    from the website and then be handled over voice.
    """
    rows = (
        db.query(Lead.source, func.count(Lead.id))
        .filter(Lead.created_at >= since)
        .group_by(Lead.source)
        .all()
    )
    return [{"source": source, "count": count} for source, count in rows]


def _by_hour(db: Session, now, dealership: Dealership) -> list[dict]:
    """Today's conversations bucketed by hour, each marked open or closed.

    The point the chart makes is that Liner answers when the showroom cannot,
    so every bucket carries whether the dealership was open at that hour. That
    comes from `hours_json` -- never a hardcoded 8-to-6. Timestamps are naive
    and already in the dealership's local frame, so `.hour` is the local hour.
    """
    day_names = [
        "monday", "tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday",
    ]
    hours = json.loads(dealership.hours_json or "{}")
    window = hours.get(day_names[now.weekday()])
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    counts = _bucket(db, start_of_day)

    def is_open(hour: int) -> bool:
        if not window:
            return False
        return int(window["open"][:2]) <= hour < int(window["close"][:2])

    return [
        {"hour": h, "count": counts.get(h, 0), "open": is_open(h)}
        for h in range(24)
    ]


def _bucket(db: Session, start_of_day) -> dict[int, int]:
    """Bucket in Python rather than SQL.

    `strftime` is SQLite-only and `date_part` is Postgres-only; the Postgres
    door stays open, so neither goes in a query. Today's conversations are a
    small enough set that the loop costs nothing.
    """
    rows = (
        db.query(Conversation.started_at)
        .filter(Conversation.started_at >= start_of_day)
        .all()
    )
    counts: dict[int, int] = {}
    for (started_at,) in rows:
        counts[started_at.hour] = counts.get(started_at.hour, 0) + 1
    return counts
