from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_dealership, require_manager
from app.db import get_db, utcnow
from app.models import Appointment, Dealership, User
from app.schemas.serialize import dealership_out, user_out

router = APIRouter(tags=["team"])


def rep_load(db: Session, user: User) -> dict:
    """Today's booked load and the next free slot. Auto-assign reads this."""
    now = utcnow()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    todays = (
        db.query(Appointment)
        .filter(
            Appointment.assigned_user_id == user.id,
            Appointment.starts_at >= start,
            Appointment.starts_at < end,
            Appointment.status.in_(["booked", "confirmed"]),
        )
        .order_by(Appointment.starts_at.asc())
        .all()
    )
    last_end = None
    if todays:
        last = todays[-1]
        last_end = last.starts_at + timedelta(minutes=last.duration_min)
    return {
        **user_out(user),
        "todays_appointments": len(todays),
        "at_capacity": len(todays) >= user.daily_cap,
        "next_free_at": (last_end or now).isoformat(),
    }


@router.get("/team")
def list_team(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    rows = db.query(User).filter_by(active=True).order_by(User.role.asc(), User.name.asc()).all()
    return {"members": [rep_load(db, u) for u in rows]}


class MemberPatch(BaseModel):
    daily_cap: int | None = None
    notify_channel: str | None = None
    active: bool | None = None


@router.patch("/team/{user_id}")
def patch_member(
    user_id: str,
    body: MemberPatch,
    db: Session = Depends(get_db),
    manager: User = Depends(require_manager),
) -> dict:
    member = db.query(User).filter_by(id=user_id).one_or_none()
    if member is None:
        raise HTTPException(404, "Member not found")
    if body.notify_channel is not None and body.notify_channel not in {"email", "dashboard"}:
        raise HTTPException(400, "notify_channel must be 'email' or 'dashboard'")
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(member, key, value)
    db.commit()
    return rep_load(db, member)


@router.get("/dealership")
def get_dealership_settings(
    dealership: Dealership = Depends(get_dealership),
    user: User = Depends(current_user),
) -> dict:
    """Hours live here and nowhere else -- no page states its own (§18.4)."""
    return dealership_out(dealership)
