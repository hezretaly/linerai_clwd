"""Act 2: confirm, assign, reach out, log a call.

The round trip that matters -- the dealer clicks Send and the buyer's chat
window updates -- happens here: the outreach is mirrored into the buyer's
thread at the same moment it is dispatched.
"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.appointments import assert_transition, get_appointment
from app.api.deps import assignable_query, current_user, find_staff, get_dealership
from app.api.team import rep_load
from app.config import settings
from app.db import get_db, utcnow
from app import appointment_scope, clock, email_outbound
from app.events import emit
from app.models import (
    Appointment,
    CapturedField,
    Conversation,
    Dealership,
    Lead,
    Message,
    Outreach,
    User,
    Vehicle,
)
from app.schemas.serialize import appointment_out, outreach_many, outreach_out

router = APIRouter(prefix="/appointments", tags=["appointments"])


@router.post("/{appointment_id}/confirm")
def confirm(
    appointment_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    appointment = get_appointment(db, appointment_id)
    assert_transition(appointment.status, "confirmed")
    appointment.status = "confirmed"
    db.commit()
    emit(db, "appointment.confirmed", {
        "appointment_id": appointment.id, "lead_id": appointment.lead_id, "by": user.id,
    })
    return appointment_out(appointment, db)


def _restage(db: Session, appointment: Appointment) -> None:
    """A cancelled visit is not an appointment set.

    `conversations.stage` is written once, by `book_appointment`, and nothing
    walked it back -- so a cancelled booking left the thread at `booked`
    forever. The conversations list reads that stage for its badge and for the
    Appointed filter, while the lead beside it derives the same thing from
    appointment rows and correctly said the buyer had none. One buyer, two
    answers, and no way to tell from the screen which one to believe. This
    codebase already wrote that failure down as the reason the filters were
    unified; it was reachable through the cancel button the whole time.

    Only when nothing else of theirs is still standing: a buyer who booked
    twice and cancelled one visit is still a buyer with an appointment.
    """
    if not appointment.conversation_id:
        return
    convo = db.query(Conversation).filter_by(id=appointment.conversation_id).one_or_none()
    if convo is None or convo.stage != "booked":
        return
    still_booked = (
        db.query(Appointment)
        .filter(
            Appointment.conversation_id == convo.id,
            Appointment.id != appointment.id,
            Appointment.status.in_(["booked", "confirmed"]),
        )
        .count()
    )
    if still_booked:
        return
    # Back to where booking was reached from, not to the beginning. They gave
    # their contact details and were offered times; none of that un-happened,
    # and dropping them to `opening` would have the rails greet them again.
    convo.stage = "contact_capture"


class Reschedule(BaseModel):
    starts_at: datetime


@router.post("/{appointment_id}/reschedule")
def reschedule(
    appointment_id: str,
    body: Reschedule,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    dealership: Dealership = Depends(get_dealership),
) -> dict:
    """Move a visit without destroying it.

    There was no way to. A rep who needed to shift somebody by an hour had to
    cancel and rebook, which mints a new row -- so the appointment loses its
    id, its assigned salesperson, and the outreach already sent against it,
    and the buyer's timeline shows a cancellation next to a fresh booking
    rather than a move. Nobody reading that later can tell whether the buyer
    rescheduled or walked away and came back.

    The clash check is the same one `book_appointment` runs, for the same
    reason: the calendar is re-decided at the moment of the move, not when the
    rep opened the page.
    """
    appointment = get_appointment(db, appointment_id)
    if appointment.status not in {"booked", "confirmed"}:
        raise HTTPException(409, f"A {appointment.status} appointment cannot be moved.")

    when = body.starts_at.replace(tzinfo=None)
    if when == appointment.starts_at:
        return appointment_out(appointment, db)
    # `starts_at` is dealership wall-clock; comparing it against `utcnow()`
    # refused an honest reschedule (or let a stale one through) in the 5-6
    # hour band where the two clocks disagree.
    if when < clock.wall_now(dealership):
        raise HTTPException(400, "That time has already passed.")

    # Capacity-aware, like `book_appointment`'s own clash check: a slot holds
    # as many appointments as the dealership has staff, not one, so only a
    # genuinely full slot refuses the move. `exclude_id` is what stops the
    # appointment being moved from counting against itself.
    if appointment_scope.slot_taken(
        db, when, exclude_id=appointment.id
    ) >= appointment_scope.capacity(db, dealership):
        raise HTTPException(409, "Something else is booked at that time.")
    if appointment.assigned_user_id and appointment_scope.rep_conflict(
        db, appointment.assigned_user_id, when, appointment.duration_min, exclude_id=appointment.id
    ):
        raise HTTPException(409, "The assigned rep already has an appointment at that time.")

    was = appointment.starts_at
    appointment.starts_at = when
    # Back to unconfirmed: a buyer who confirmed Tuesday at ten has not
    # confirmed Wednesday at two, and carrying the tick across would put a
    # green row on the calendar nobody has actually agreed to.
    if appointment.status == "confirmed":
        appointment.status = "booked"
    db.commit()

    emit(db, "appointment.rescheduled", {
        "appointment_id": appointment.id,
        "lead_id": appointment.lead_id,
        "from": was.isoformat(),
        "starts_at": when.isoformat(),
        "by": user.id,
    })
    return appointment_out(appointment, db)


@router.post("/{appointment_id}/cancel")
def cancel(
    appointment_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Cancel is a transition the model has always allowed and nothing offered.

    Without it a slot booked once is gone for good: book_appointment refuses a
    clash against booked and confirmed rows, so a mistaken booking blocks that
    time forever. It also made `make smoke` non-repeatable -- each run took a
    slot out of the fixture's week and never gave it back, and after enough
    runs there was nothing left to offer and the booking flow failed.
    """
    appointment = get_appointment(db, appointment_id)
    assert_transition(appointment.status, "cancelled")
    appointment.status = "cancelled"
    _restage(db, appointment)
    db.commit()
    emit(db, "appointment.cancelled", {
        "appointment_id": appointment.id,
        "lead_id": appointment.lead_id,
        "starts_at": appointment.starts_at.isoformat(),
        "by": user.id,
    })
    return appointment_out(appointment, db)


class AssignBody(BaseModel):
    user_id: str | None = None
    auto: bool = False


@router.post("/{appointment_id}/assign")
def assign(
    appointment_id: str,
    body: AssignBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    dealership: Dealership = Depends(get_dealership),
) -> dict:
    appointment = get_appointment(db, appointment_id)

    if body.auto:
        # Round-robin over reps who are under their daily cap -- the rule the
        # dashboard advertises. Showing a rule beats showing a chore.
        # `assignable_query` adds the `out` exclusion on top of the existing
        # reps-only, active-only filter; managers stay out of auto-assign,
        # which is an intentional behaviour of its own.
        reps = assignable_query(db).filter_by(role="rep").order_by(User.name.asc()).all()
        loads = [(rep, rep_load(db, rep, dealership)) for rep in reps]
        available = [
            (rep, load) for rep, load in loads
            if not load["at_capacity"]
            and appointment_scope.rep_conflict(
                db, rep.id, appointment.starts_at, appointment.duration_min,
                exclude_id=appointment.id,
            ) is None
        ]
        if not available:
            raise HTTPException(
                409,
                "Every rep is at their daily cap or already has something booked then. "
                "Raise a cap on the team page or assign manually.",
            )
        chosen = min(available, key=lambda pair: pair[1]["todays_appointments"])[0]
    elif body.user_id:
        chosen = find_staff(db, body.user_id)
        if chosen is None:
            raise HTTPException(404, "User not found")
        if assignable_query(db).filter_by(id=chosen.id).one_or_none() is None:
            raise HTTPException(409, f"{chosen.name} is marked out and cannot take new work.")
        if appointment_scope.rep_conflict(
            db, chosen.id, appointment.starts_at, appointment.duration_min,
            exclude_id=appointment.id,
        ) is not None:
            raise HTTPException(409, f"{chosen.name} already has an appointment at that time.")
    else:
        raise HTTPException(400, "Pass a user_id or auto=true")

    appointment.assigned_user_id = chosen.id
    lead = db.query(Lead).filter_by(id=appointment.lead_id).one_or_none()
    if lead is not None and not lead.assigned_user_id:
        lead.assigned_user_id = chosen.id
    db.commit()

    # `lead_id` on every appointment event, like the others. An event that
    # names only the appointment cannot be routed to the buyer it belongs to,
    # so their page has no reason to refresh when their visit is assigned --
    # and the two panels showing it drift apart until somebody reloads.
    emit(db, "appointment.assigned", {
        "appointment_id": appointment.id, "lead_id": appointment.lead_id,
        "user_id": chosen.id, "user_name": chosen.name,
        "auto": body.auto,
    })
    return appointment_out(appointment, db)


def _draft(db: Session, appointment: Appointment, dealership: Dealership, sender: User) -> dict:
    lead = db.query(Lead).filter_by(id=appointment.lead_id).one_or_none()
    vehicle = (
        db.query(Vehicle).filter_by(id=appointment.vehicle_id).one_or_none()
        if appointment.vehicle_id else None
    )
    when = appointment.starts_at
    hour = when.hour % 12 or 12
    ampm = "AM" if when.hour < 12 else "PM"
    slot = f"{when.strftime('%A, %B %-d')} at {hour}:{when.minute:02d} {ampm}"
    car = f"the {vehicle.year} {vehicle.make} {vehicle.model}" if vehicle else "your visit"

    # One line referencing something the buyer actually said. Only ever from a
    # field the buyer typed -- an inferred value must not be quoted back at them.
    personal = ""
    if lead is not None:
        typed = (
            db.query(CapturedField)
            .filter_by(lead_id=lead.id, provenance="typed")
            .order_by(CapturedField.updated_at.desc())
            .first()
        )
        if typed is not None:
            personal = f"\n\nYou mentioned {typed.value.rstrip('.').lower()} -- I've made a note of that.".replace(
                "  ", " "
            )

    first_name = (lead.name or "there").split()[0] if lead else "there"
    subject = f"Your {slot} appointment at {dealership.name}"
    body = (
        f"Hi {first_name},\n\n"
        f"You're booked in for {slot} to see {car}.{personal}\n\n"
        f"We're at {dealership.address}. Ask for {sender.name} when you arrive, "
        f"or call {dealership.phone} if anything changes.\n\n"
        f"See you then,\n{sender.name}\n{dealership.name}"
    )
    return {
        "to": lead.email if lead else "",
        "subject": subject,
        "body": body,
        "lead_name": lead.name if lead else "",
    }


@router.get("/{appointment_id}/outreach")
def outreach_draft(
    appointment_id: str,
    draft: int = Query(0, alias="draft"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    dealership: Dealership = Depends(get_dealership),
) -> dict:
    appointment = get_appointment(db, appointment_id)
    if draft:
        return _draft(db, appointment, dealership, user)
    rows = (
        db.query(Outreach)
        .filter_by(appointment_id=appointment.id)
        .order_by(Outreach.created_at.desc())
        .all()
    )
    return {"outreach": outreach_many(db, rows)}


class OutreachBody(BaseModel):
    subject: str
    #: The text half. Written from `html` instead when that is given.
    body: str = ""
    # Optional, and `{subject, body}` alone is the send it always was: to the
    # buyer's address on file, text only.
    to: str | list[str | dict] | None = None
    cc: str | list[str | dict] | None = None
    bcc: str | list[str | dict] | None = None
    html: str = ""
    attachment_ids: list[str] | None = None
    importance: str = "normal"


@router.post("/{appointment_id}/outreach")
def send_outreach(
    appointment_id: str,
    body: OutreachBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    dealership: Dealership = Depends(get_dealership),
) -> dict:
    appointment = get_appointment(db, appointment_id)
    lead = db.query(Lead).filter_by(id=appointment.lead_id).one_or_none()
    to = email_outbound.typed_or_on_file(body.to, lead.email) if lead is not None else None
    if lead is None or not to:
        raise HTTPException(
            409,
            "No email on file for this lead, so there is nothing to send to. Log a call "
            "instead.",
        )

    try:
        # Unsigned: the draft ends in the rep's name and the dealership's,
        # written into the text they are looking at.
        message = email_outbound.build(
            db, to=to, cc=body.cc, bcc=body.bcc,
            subject=body.subject, body=body.body, html=body.html,
            attachment_ids=body.attachment_ids, importance=body.importance,
            uploader_id=user.id,
        )
    except email_outbound.OutboundError as exc:
        raise HTTPException(exc.status, str(exc)) from None

    # The one path, the one guard over every recipient -- see
    # app/email_outbound.py. The event waits until the mirror below is
    # written, so a dashboard that refetches on it finds both.
    sent = email_outbound.send(
        db, message, kind="followup", lead_id=lead.id, appointment_id=appointment.id,
        sent_by_user_id=user.id, announce_event=False,
    )
    if sent.result is None:
        return sent.out()

    # Mirror into the buyer's thread. This is what makes the demo visibly land
    # -- and it means the round trip never depends on inbox delivery. Plain
    # text, as the thread is, and only for mail that went: a refused send
    # sitting in the buyer's chat reads as one that arrived.
    convo = (
        db.query(Conversation)
        .filter_by(id=appointment.conversation_id)
        .one_or_none()
        if appointment.conversation_id
        else db.query(Conversation).filter_by(lead_id=lead.id)
        .order_by(Conversation.started_at.desc(), Conversation.id.asc()).first()
    )
    if convo is not None and sent.ok:
        db.add(Message(
            conversation_id=convo.id, role="rep",
            content=f"{sent.record.subject}\n\n{sent.record.body}",
            tool_calls_json=json.dumps([{"name": "outreach", "outreach_id": sent.record.id}]),
        ))
        db.commit()

    email_outbound.announce(db, sent, conversation_id=convo.id if convo else None)
    return sent.out()


class LogCallBody(BaseModel):
    note: str


@router.post("/{appointment_id}/log-call")
def log_call(
    appointment_id: str,
    body: LogCallBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """A rep records a callback so the lead history stays complete.

    This is the answer to the hole SMS left behind: a caller who leaves only a
    phone number is unreachable by the product, so the rep phones them and the
    record lives here. A form and a row -- no telephony (§18.5).
    """
    appointment = get_appointment(db, appointment_id)
    record = Outreach(
        appointment_id=appointment.id, lead_id=appointment.lead_id,
        sent_by_user_id=user.id, channel="phone_logged", subject="Call logged",
        body=body.note.strip(), provider="manual", status="sent", sent_at=utcnow(),
    )
    db.add(record)
    db.commit()
    return outreach_out(record)
