"""The dashboard's first paint in one call.

Every KPI and every sidebar badge count is computed here and nowhere else --
the mockups disagreed with themselves across pages (Conversations 4 vs 3, Leads
31 vs 12) precisely because each page counted for itself (§18.4).
"""

from __future__ import annotations

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
    User,
    Vehicle,
    VehicleMention,
)
from app.schemas.serialize import (
    appointment_out,
    conversation_out,
    dealership_out,
    escalation_out,
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
    open_escalations = db.query(Escalation).filter(Escalation.claimed_at.is_(None)).all()

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
            "active_conversations": [conversation_out(c, db) for c in active_conversations[:8]],
            "inventory_issues": inventory_issues,
        },
        "mix": _channel_mix(db, since),
    }


def _channel_mix(db: Session, since) -> list[dict]:
    rows = (
        db.query(Conversation.channel, func.count(Conversation.id))
        .filter(Conversation.started_at >= since)
        .group_by(Conversation.channel)
        .all()
    )
    return [{"channel": channel, "count": count} for channel, count in rows]
