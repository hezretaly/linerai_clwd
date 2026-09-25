from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import appointment_scope, clock
from app.add_user import PASSWORD_BYTES, InvalidEmail, OpsEmailConflict, create_user, pwd
from app.api.deps import (
    DEALERSHIP_ROLES,
    current_user,
    get_dealership,
    require_manager,
    staff_query,
)
from app.db import get_db, utcnow
from app.events import emit
from app.models import Appointment, Conversation, Dealership, Escalation, Lead, User
from app.schemas.serialize import dealership_out, user_out

router = APIRouter(tags=["team"])


def rep_load(db: Session, user: User, dealership: Dealership) -> dict:
    """Today's booked load and the next free slot. Auto-assign reads this.

    "Today" and "now" are the dealership's own wall clock (`app.clock`), not
    `db.utcnow()`: `Appointment.starts_at` is stored dealership-local, and
    from about 7pm to midnight local the UTC calendar date has already
    rolled to tomorrow. A UTC-midnight window compared against that column
    counted tomorrow's visits as today's and dropped today's evening ones --
    which also meant `at_capacity` and auto-assign disagreed with themselves
    depending on the hour, on data that never changed.
    """
    now = clock.wall_now(dealership)
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
        # Wall-clock either way, like `starts_at` itself -- the fallback used
        # to be `utcnow()`, a UTC instant shown with no zone, which read as
        # 7:48 AM on a page a Chicago manager was reading at 2:48 AM.
        "next_free_at": (last_end or now).isoformat(),
    }


@router.get("/team")
def list_team(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    dealership: Dealership = Depends(get_dealership),
) -> dict:
    # Dealership staff only. Liner's own `owner` accounts share this table
    # and have no business on somebody else's roster -- they take no
    # appointments, own no leads, and are not a manager's to administer.
    rows = staff_query(db).order_by(User.role.asc(), User.name.asc()).all()
    return {"members": [rep_load(db, u, dealership) for u in rows]}


class NewMember(BaseModel):
    name: str
    email: str


@router.post("/team")
def add_member(
    body: NewMember,
    db: Session = Depends(get_db),
    manager: User = Depends(require_manager),
    dealership: Dealership = Depends(get_dealership),
) -> dict:
    """A manager adds a rep. Always role "rep" -- a dealership has exactly one
    manager, and there is no path here to a second.

    Reuses `add_user.create_user`, the same validation, ops_users check,
    password generation and row creation the CLI runs. Unlike the CLI, an
    address already on the team is refused with a 409 rather than a silent
    no-op: a manager pressing Add expects either a new account or an error,
    never the same result for both.
    """
    try:
        result = create_user(db, body.email, body.name, "rep")
    except InvalidEmail:
        raise HTTPException(400, "That does not look like an email address.")
    except OpsEmailConflict:
        raise HTTPException(409, "That address is one of Liner's own accounts.")
    if not result.created:
        raise HTTPException(409, "That address is already on the team.")
    return {"member": rep_load(db, result.user, dealership), "password": result.password}


class MemberPatch(BaseModel):
    daily_cap: int | None = None
    notify_channel: str | None = None
    active: bool | None = None


def _member(db: Session, user_id: str) -> User:
    """A current dealership rep or manager, by id -- 404 for anyone else.

    404 rather than 403 for an owner too: from a dealership's side "that
    account exists but is not yours" is itself something they should not
    learn.
    """
    member = db.query(User).filter_by(id=user_id).one_or_none()
    if member is None or member.role not in DEALERSHIP_ROLES:
        raise HTTPException(404, "Member not found")
    return member


@router.patch("/team/{user_id}")
def patch_member(
    user_id: str,
    body: MemberPatch,
    db: Session = Depends(get_db),
    manager: User = Depends(require_manager),
    dealership: Dealership = Depends(get_dealership),
) -> dict:
    member = _member(db, user_id)
    if body.notify_channel is not None and body.notify_channel not in {"email", "dashboard"}:
        raise HTTPException(400, "notify_channel must be 'email' or 'dashboard'")
    leaving = body.active is False and member.active
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(member, key, value)
    handed_back = _hand_back(db, member, dealership) if leaving else {}
    db.commit()
    if leaving:
        emit(db, "team.deactivated", {"user_id": member.id, **handed_back})
    return {**rep_load(db, member, dealership), **handed_back}


def _returning(
    db: Session, member: User, dealership: Dealership
) -> tuple[list[Lead], list[Appointment], list[Escalation]]:
    """What leaving hands back: every lead the member owns, every standing
    appointment of theirs still ahead, and every escalation they claimed on a
    thread that is still open. One definition, read by `_hand_back` (which
    mutates what this returns) and `remove_preview` (which only counts it) --
    so the blast radius a manager is shown before removing someone can never
    drift from what removing them actually does.

    Only appointments still ahead: rewriting the history of who hosted a
    visit last March would make the record wrong to make a queue tidy.
    `starts_at` is dealership wall-clock; comparing it against `utcnow()`
    mis-keeps or mis-releases visits in the 5-6 hour band where the two
    clocks disagree.

    Only escalations on a thread that is still open: on a closed one, claimed
    is history -- they took it, the conversation ended, and reopening years
    of that on a departure would bury the live queue under work that is
    genuinely done. This is the difference between leaving and being merely
    unassigned -- when a rep is handed a different buyer they are still here
    to finish what they claimed, which is why `assign_lead` deliberately
    leaves these alone.
    """
    leads = db.query(Lead).filter(Lead.assigned_user_id == member.id).all()
    visits = (
        db.query(Appointment)
        .filter(
            Appointment.assigned_user_id == member.id,
            Appointment.status.in_(appointment_scope.STANDING_STATUSES),
            Appointment.starts_at >= clock.wall_now(dealership),
        )
        .all()
    )
    live_threads = {
        c.id for c in db.query(Conversation.id, Conversation.status)
        .filter(Conversation.status != "closed").all()
    }
    escalations = [
        e for e in db.query(Escalation)
        .filter(Escalation.claimed_by_user_id == member.id, Escalation.claimed_at.isnot(None))
        .all()
        if e.conversation_id in live_threads
    ]
    return leads, visits, escalations


def _hand_back(db: Session, member: User, dealership: Dealership) -> dict:
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
    leads, visits, escalations = _returning(db, member, dealership)
    for lead in leads:
        lead.assigned_user_id = None
    for visit in visits:
        visit.assigned_user_id = None
    for escalation in escalations:
        escalation.claimed_by_user_id = None
        escalation.claimed_at = None
    return {
        "leads_returned": len(leads),
        "appointments_returned": len(visits),
        "escalations_reopened": len(escalations),
    }


@router.get("/team/{user_id}/remove-preview")
def remove_preview(
    user_id: str,
    db: Session = Depends(get_db),
    manager: User = Depends(require_manager),
    dealership: Dealership = Depends(get_dealership),
) -> dict:
    """What `PATCH /team/{id}` with `active: false` would hand back, read-only.

    Reads `_returning` -- the exact same rows `_hand_back` would mutate --
    and only counts them. Backs a confirmation dialog: a manager needs to see
    the blast radius before committing to removing someone, which is exactly
    the reason `_hand_back` itself exists (see its docstring).
    """
    member = _member(db, user_id)
    leads, visits, escalations = _returning(db, member, dealership)
    return {
        "leads_returned": len(leads),
        "appointments_returned": len(visits),
        "escalations_reopened": len(escalations),
    }


@router.post("/team/{user_id}/reset-password")
def reset_password(
    user_id: str,
    db: Session = Depends(get_db),
    manager: User = Depends(require_manager),
) -> dict:
    member = _member(db, user_id)
    password = secrets.token_urlsafe(PASSWORD_BYTES)
    member.password_hash = pwd.hash(password)
    db.commit()
    return {"password": password}


class OutPatch(BaseModel):
    out: bool


@router.patch("/team/{user_id}/out")
def set_out(
    user_id: str,
    body: OutPatch,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    dealership: Dealership = Depends(get_dealership),
) -> dict:
    """A rep sets their own out status; a manager sets anyone's.

    Same split `assign_lead` enforces for who may hand a buyer to whom: who
    works which lead, and who is on the floor right now, are both how a
    floor is run rather than something a rep decides for a colleague.
    """
    member = _member(db, user_id)
    if user.role != "manager" and user.id != member.id:
        raise HTTPException(403, "You can only set your own status.")
    member.out = body.out
    db.commit()
    emit(db, "team.out_changed", {"user_id": member.id, "out": member.out})
    return rep_load(db, member, dealership)


@router.get("/dealership")
def get_dealership_settings(
    dealership: Dealership = Depends(get_dealership),
    user: User = Depends(current_user),
) -> dict:
    """Hours live here and nowhere else -- no page states its own (§18.4)."""
    return dealership_out(dealership)
