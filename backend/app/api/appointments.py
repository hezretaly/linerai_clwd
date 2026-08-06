from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.db import get_db
from app.models import Appointment, User
from app.schemas.serialize import appointment_out

router = APIRouter(prefix="/appointments", tags=["appointments"])

# booked -> confirmed, and either can be cancelled or marked no_show.
TRANSITIONS: dict[str, set[str]] = {
    "booked": {"confirmed", "cancelled", "no_show"},
    "confirmed": {"cancelled", "no_show"},
    "cancelled": set(),
    "no_show": set(),
}


def get_appointment(db: Session, appointment_id: str) -> Appointment:
    appointment = db.query(Appointment).filter_by(id=appointment_id).one_or_none()
    if appointment is None:
        raise HTTPException(404, "Appointment not found")
    return appointment


def assert_transition(current: str, target: str) -> None:
    if target not in TRANSITIONS.get(current, set()):
        raise HTTPException(409, f"Cannot go from {current} to {target}")


@router.get("")
def list_appointments(
    status: str | None = Query(None),
    user_id: str | None = Query(None),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    query = db.query(Appointment)
    if status:
        query = query.filter(Appointment.status == status)
    if user_id:
        query = query.filter(Appointment.assigned_user_id == user_id)
    if start:
        query = query.filter(Appointment.starts_at >= start)
    if end:
        query = query.filter(Appointment.starts_at < end)
    rows = query.order_by(Appointment.starts_at.asc()).all()
    return {"appointments": [appointment_out(a, db) for a in rows]}


@router.get("/{appointment_id}")
def get_one(
    appointment_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    return appointment_out(get_appointment(db, appointment_id), db)
