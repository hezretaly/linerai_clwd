"""Act 2: confirm, assign, reach out, log a call.

The round trip that matters -- the dealer clicks Send and the buyer's chat
window updates -- happens here: the outreach is mirrored into the buyer's
thread at the same moment it is dispatched.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.appointments import assert_transition, get_appointment
from app.api.deps import current_user, get_dealership
from app.api.team import rep_load
from app.config import settings
from app.db import get_db, utcnow
from app.events import emit
from app.integrations.registry import get_email_sender
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
from app.schemas.serialize import appointment_out, outreach_out

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
        "appointment_id": appointment.id, "by": user.id,
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
) -> dict:
    appointment = get_appointment(db, appointment_id)

    if body.auto:
        # Round-robin over reps who are under their daily cap -- the rule the
        # dashboard advertises. Showing a rule beats showing a chore.
        reps = db.query(User).filter_by(role="rep", active=True).order_by(User.name.asc()).all()
        loads = [(rep, rep_load(db, rep)) for rep in reps]
        available = [(rep, load) for rep, load in loads if not load["at_capacity"]]
        if not available:
            raise HTTPException(
                409,
                "Every rep is at their daily cap. Raise a cap on the team page or assign "
                "manually.",
            )
        chosen = min(available, key=lambda pair: pair[1]["todays_appointments"])[0]
    elif body.user_id:
        chosen = db.query(User).filter_by(id=body.user_id, active=True).one_or_none()
        if chosen is None:
            raise HTTPException(404, "User not found")
    else:
        raise HTTPException(400, "Pass a user_id or auto=true")

    appointment.assigned_user_id = chosen.id
    lead = db.query(Lead).filter_by(id=appointment.lead_id).one_or_none()
    if lead is not None and not lead.assigned_user_id:
        lead.assigned_user_id = chosen.id
    db.commit()

    emit(db, "appointment.assigned", {
        "appointment_id": appointment.id, "user_id": chosen.id, "user_name": chosen.name,
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
    return {"outreach": [outreach_out(o) for o in rows]}


class OutreachBody(BaseModel):
    subject: str
    body: str


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
    if lead is None or not lead.email:
        raise HTTPException(
            409,
            "No email on file for this lead, so there is nothing to send to. Log a call "
            "instead.",
        )

    sender = get_email_sender()
    record = Outreach(
        appointment_id=appointment.id, lead_id=lead.id, sent_by_user_id=user.id,
        channel="email", to_address=lead.email, subject=body.subject, body=body.body,
        provider=sender.name, status="queued",
    )
    db.add(record)
    db.commit()

    # DEMO_MODE blocks real delivery unless the address was explicitly
    # allow-listed. Cheap insurance against a rehearsal mailing a prospect.
    blocked = (
        sender.delivers
        and settings.demo_mode
        and lead.email.lower() not in settings.allowlist
    )
    if blocked:
        record.status = "failed"
        record.error = (
            f"DEMO_MODE is on and {lead.email} is not in EMAIL_ALLOWLIST, so nothing was sent."
        )
        db.commit()
        return outreach_out(record)

    try:
        result = sender.send(lead.email, body.subject, body.body, reply_to=user.email)
        record.provider_message_id = result.message_id
        record.provider_thread_id = result.thread_id
        # 'sent' means the provider accepted it. There is no delivery callback.
        record.status = result.status
        record.error = result.detail if result.status != "sent" else ""
        record.sent_at = utcnow()
    except Exception as exc:
        record.status = "failed"
        record.error = str(exc)
        db.commit()
        return outreach_out(record)

    db.commit()

    # Mirror into the buyer's thread. This is what makes the demo visibly land
    # -- and it means the round trip never depends on inbox delivery.
    convo = (
        db.query(Conversation)
        .filter_by(id=appointment.conversation_id)
        .one_or_none()
        if appointment.conversation_id
        else db.query(Conversation).filter_by(lead_id=lead.id).first()
    )
    if convo is not None:
        db.add(Message(
            conversation_id=convo.id, role="rep",
            content=f"{body.subject}\n\n{body.body}",
            tool_calls_json=json.dumps([{"name": "outreach", "outreach_id": record.id}]),
        ))
        db.commit()

    emit(db, "outreach.sent", {
        "outreach_id": record.id, "appointment_id": appointment.id, "lead_id": lead.id,
        "to": lead.email, "provider": record.provider,
        "delivered_externally": sender.delivers,
        "conversation_id": convo.id if convo else None,
    })
    return outreach_out(record)


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
