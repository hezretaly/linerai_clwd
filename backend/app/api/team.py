from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_dealership, require_manager
from app.db import get_db, utcnow
from app.events import emit
from app.models import Appointment, Conversation, Dealership, Escalation, Lead, User
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
    leaving = body.active is False and member.active
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(member, key, value)
    handed_back = _hand_back(db, member) if leaving else {}
    db.commit()
    if leaving:
        emit(db, "team.deactivated", {"user_id": member.id, **handed_back})
    return {**rep_load(db, member), **handed_back}


def _hand_back(db: Session, member: User) -> dict:
    """Someone leaving hands their buyers back, rather than taking them along.

    Deactivating dropped them off the roster and left every lead and
    appointment still pointing at them. That is the worst of both: the buyers
    are not unclaimed, so they never come back to the queue and no panel asks
    anyone to pick them up -- and they are not workable either, because the
    person who owns them is gone and cannot be picked from the assign menu.
    The work does not appear anywhere. It is the same shape as a lead that was
    assigned and still showed as needing a person, except silent.

    Appointments are un-assigned, never cancelled. A visit still happening with
    nobody to host it is the dealership's problem to solve, and it belongs in
    the unassigned queue where somebody picks it up -- quietly deleting it from
    the calendar because a rep left would be a far worse answer.
    """
    leads = db.query(Lead).filter(Lead.assigned_user_id == member.id).all()
    for lead in leads:
        lead.assigned_user_id = None
    # Only what is still ahead. Rewriting the history of who hosted a visit
    # last March would make the record wrong to make a queue tidy.
    visits = (
        db.query(Appointment)
        .filter(
            Appointment.assigned_user_id == member.id,
            Appointment.status.in_(["booked", "confirmed"]),
            Appointment.starts_at >= utcnow(),
        )
        .all()
    )
    for visit in visits:
        visit.assigned_user_id = None

    # And anything they picked up and did not finish. Claiming takes an
    # escalation out of Needs a person permanently -- there is no resolved
    # state -- so one claimed by somebody who has since left is a buyer nobody
    # is calling back and no queue is asking about.
    #
    # Only on a thread that is still open. On a closed one, claimed is history:
    # they took it, the conversation ended, and reopening years of that on a
    # departure would bury the live queue under work that is genuinely done.
    # This is the difference between leaving and being unassigned -- when a rep
    # is merely handed a different buyer they are still here to finish what
    # they claimed, which is why `assign_lead` deliberately leaves these alone.
    live_threads = {
        c.id for c in db.query(Conversation.id, Conversation.status)
        .filter(Conversation.status != "closed").all()
    }
    reopened = [
        e for e in db.query(Escalation)
        .filter(Escalation.claimed_by_user_id == member.id, Escalation.claimed_at.isnot(None))
        .all()
        if e.conversation_id in live_threads
    ]
    for escalation in reopened:
        escalation.claimed_by_user_id = None
        escalation.claimed_at = None

    return {
        "leads_returned": len(leads),
        "appointments_returned": len(visits),
        "escalations_reopened": len(reopened),
    }


@router.get("/dealership")
def get_dealership_settings(
    dealership: Dealership = Depends(get_dealership),
    user: User = Depends(current_user),
) -> dict:
    """Hours live here and nowhere else -- no page states its own (§18.4)."""
    return dealership_out(dealership)
