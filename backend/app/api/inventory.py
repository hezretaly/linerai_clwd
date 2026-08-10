from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.db import get_db, utcnow
from app.events import emit
from app.models import (
    Appointment,
    Conversation,
    Lead,
    User,
    Vehicle,
    VehicleMention,
)
from app.schemas.serialize import vehicle_out

router = APIRouter(prefix="/inventory", tags=["inventory"])

# `status` is deliberately absent: it has its own endpoint, because taking a
# car off the lot is not the same kind of act as correcting its mileage. A
# second way to set it is how one of them quietly stops emitting the event the
# dashboard listens for.
EDITABLE = {
    "year", "make", "model", "trim", "price", "mileage", "body_style", "seats",
    "title_status", "keywords", "rule_discuss", "rule_hold_price",
    "rule_mention_warranty", "rule_note",
}


def _mention_counts(db: Session) -> dict[str, int]:
    rows = (
        db.query(VehicleMention.vehicle_id, func.count(VehicleMention.id))
        .group_by(VehicleMention.vehicle_id)
        .all()
    )
    return dict(rows)


@router.get("")
def list_inventory(
    status: str | None = Query(None),
    q: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    query = db.query(Vehicle)
    if status:
        query = query.filter(Vehicle.status == status)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            func.lower(Vehicle.make).like(like)
            | func.lower(Vehicle.model).like(like)
            | func.lower(Vehicle.vin).like(like)
            | func.lower(Vehicle.keywords).like(like)
        )
    counts = _mention_counts(db)
    rows = query.order_by(Vehicle.year.desc(), Vehicle.make.asc()).all()
    return {"vehicles": [vehicle_out(v, mentions=counts.get(v.id, 0)) for v in rows]}


@router.get("/{vehicle_id}")
def get_vehicle(
    vehicle_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    vehicle = db.query(Vehicle).filter_by(id=vehicle_id).one_or_none()
    if vehicle is None:
        raise HTTPException(404, "Vehicle not found")
    counts = _mention_counts(db)
    out = vehicle_out(vehicle, mentions=counts.get(vehicle.id, 0))

    # Blast radius: who was quoted this car, so a sold listing can be chased
    # down rather than just corrected (§18.2).
    mentions = (
        db.query(VehicleMention, Conversation, Lead)
        .join(Conversation, Conversation.id == VehicleMention.conversation_id)
        .outerjoin(Lead, Lead.id == Conversation.lead_id)
        .filter(VehicleMention.vehicle_id == vehicle.id)
        .order_by(VehicleMention.created_at.desc())
        .all()
    )
    out["mentions"] = [
        {
            "conversation_id": convo.id,
            "lead_id": lead.id if lead else None,
            "lead_name": (lead.name if lead else None) or "Unknown caller",
            "quoted_price": mention.quoted_price,
            "created_at": mention.created_at.isoformat(),
        }
        for mention, convo, lead in mentions
    ]
    out["appointments"] = _visits(db, vehicle)
    return out


def _visits(db: Session, vehicle: Vehicle) -> list[dict]:
    """Buyers who are coming in to see this specific car.

    The harder half of the blast radius. A quote is a car someone was told
    about; an appointment is someone who will be standing on the lot asking
    for it. Nothing here cancels them -- that is a call a rep makes, and
    silently cancelling a buyer's visit is worse than a wrong car on the
    calendar.
    """
    rows = (
        db.query(Appointment, Lead)
        .outerjoin(Lead, Lead.id == Appointment.lead_id)
        .filter(
            Appointment.vehicle_id == vehicle.id,
            Appointment.status.in_(("booked", "confirmed")),
        )
        .order_by(Appointment.starts_at.asc())
        .all()
    )
    return [
        {
            "id": appt.id,
            "lead_id": appt.lead_id,
            "lead_name": (lead.name if lead else None) or "Unknown caller",
            "starts_at": appt.starts_at.isoformat(),
            "status": appt.status,
        }
        for appt, lead in rows
    ]


STATUSES = {"available", "sold", "removed"}


class StatusBody(BaseModel):
    status: str


@router.post("/{vehicle_id}/status")
def set_status(
    vehicle_id: str,
    body: StatusBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Take a car off the lot, or put it back.

    Never a delete. `vehicle_mentions` and `appointments` both point at this
    row, so removing it either errors or takes the quote history with it -- and
    that history is the only way to answer "who was told about this car?", which
    is the question a rep has the moment one sells. The ingest pipeline made the
    same call for a listing that vanishes from the feed, for the same reason.

    Marked manual so the next import cannot undo it: the dealership's own site
    will still be listing a car that sold an hour ago.
    """
    if body.status not in STATUSES:
        raise HTTPException(400, f"status must be one of {', '.join(sorted(STATUSES))}")

    vehicle = db.query(Vehicle).filter_by(id=vehicle_id).one_or_none()
    if vehicle is None:
        raise HTTPException(404, "Vehicle not found")

    was = vehicle.status
    vehicle.status = body.status
    manual = set(json.loads(vehicle.manual_fields_json or "[]"))
    manual.add("status")
    vehicle.manual_fields_json = json.dumps(sorted(manual))
    db.commit()

    emit(db, "vehicle.status_changed", {
        "vehicle_id": vehicle.id, "from": was, "to": vehicle.status, "by": user.id,
    })
    return get_vehicle(vehicle_id, db, user)


class VehiclePatch(BaseModel):
    model_config = {"extra": "allow"}


@router.patch("/{vehicle_id}")
def update_vehicle(
    vehicle_id: str,
    body: VehiclePatch,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    vehicle = db.query(Vehicle).filter_by(id=vehicle_id).one_or_none()
    if vehicle is None:
        raise HTTPException(404, "Vehicle not found")

    manual = set(json.loads(vehicle.manual_fields_json or "[]"))
    for key, value in body.model_dump().items():
        if key not in EDITABLE:
            continue
        setattr(vehicle, key, value)
        # A rep-edited field is marked so the next ingest run does not
        # overwrite it. Manual override always wins (§5.5).
        manual.add(key)
    vehicle.manual_fields_json = json.dumps(sorted(manual))
    vehicle.source = "manual" if vehicle.source == "seed" else vehicle.source
    vehicle.last_seen_at = utcnow()
    db.commit()
    counts = _mention_counts(db)
    return vehicle_out(vehicle, mentions=counts.get(vehicle.id, 0))
