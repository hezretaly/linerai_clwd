from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.db import get_db, utcnow
from app.models import Conversation, Lead, User, Vehicle, VehicleMention
from app.schemas.serialize import vehicle_out

router = APIRouter(prefix="/inventory", tags=["inventory"])

EDITABLE = {
    "year", "make", "model", "trim", "price", "mileage", "body_style", "seats",
    "title_status", "status", "keywords", "rule_discuss", "rule_hold_price",
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
    return out


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
