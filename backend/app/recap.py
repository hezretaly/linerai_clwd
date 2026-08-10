"""A rep-facing recap of one conversation, composed from rows.

The rail used to show `conversations.summary`, which is whatever Liner said
last -- a reply, not a summary. A rep opening a thread wants to know who this
is, what car it is about and where it got to, and the last line answers none
of that.

Composed here rather than asked of the model, for the same reason
`answer_from_knowledge` is: a model-written summary is a second place a fact
can be invented, it costs a call per turn, and there is no model at all in
stub mode. Everything below comes from a row -- vehicle mentions, the
appointment, the escalation. If a clause is missing it is because the row is
missing, which is itself worth knowing.

**It deliberately does not restate the captured fields.** They sit right below
it on the rail, where each one wears its provenance, and prose cannot carry
that: "Financing: likely financing" reads as a fact the buyer stated, when the
row says `inferred`. Repeating a guess without the badge that marks it a guess
is how a rep ends up asserting it on the phone -- which is the whole reason
`save_captured_fields` refuses a dishonest `typed`. Two panels saying the same
thing is only redundant when they say it equally well.

`conversations.summary` stays what it was -- the last thing Liner said, or the
sign-off `close_conversation` wrote -- and still backs the one-line preview in
the list. This is the panel a rep reads before opening the transcript.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import (
    Appointment,
    Conversation,
    Escalation,
    Lead,
    Message,
    Vehicle,
    VehicleMention,
)

CHANNEL_VERB = {"chat": "started a chat", "voice": "called in"}


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    return text if text.endswith((".", "!", "?")) else f"{text}."


def conversation_recap(db: Session, c: Conversation) -> str:
    """Two or three sentences. Empty string when there is genuinely nothing."""
    lead = db.query(Lead).filter_by(id=c.lead_id).one_or_none() if c.lead_id else None
    name = (lead.name or "").strip() if lead else ""

    parts: list[str] = []

    appt = (
        db.query(Appointment)
        .filter(
            Appointment.conversation_id == c.id,
            Appointment.status.in_(("booked", "confirmed")),
        )
        .order_by(Appointment.starts_at.asc())
        .first()
    )

    # --- Who, and about what -------------------------------------------------
    who = name or "An unnamed buyer"
    opening = f"{who} {CHANNEL_VERB.get(c.channel, 'got in touch')}"

    # The car they are coming in to see settles it, even when no focus was ever
    # set -- Devon booked a Sienna and the recap called it a Pacifica, because
    # the last car mentioned was one Liner offered as an alternative.
    vehicle = (
        db.query(Vehicle).filter_by(id=c.focus_vehicle_id).one_or_none()
        if c.focus_vehicle_id
        else None
    )
    if vehicle is None and appt is not None and appt.vehicle_id:
        vehicle = db.query(Vehicle).filter_by(id=appt.vehicle_id).one_or_none()
    if vehicle is None:
        # No car settled on. What Liner actually put in front of them is still
        # the subject of the thread, so name it rather than saying nothing.
        shown = (
            db.query(Vehicle)
            .join(VehicleMention, VehicleMention.vehicle_id == Vehicle.id)
            .filter(VehicleMention.conversation_id == c.id)
            .order_by(VehicleMention.created_at.desc())
            .first()
        )
        count = (
            db.query(VehicleMention).filter_by(conversation_id=c.id).count()
            if shown is not None
            else 0
        )
        if shown is not None and count > 1:
            opening += f" and was shown {count} cars, most recently the {_title(shown)}"
        elif shown is not None:
            opening += f" about the {_title(shown)}"
    else:
        opening += f" about the {_title(vehicle)}"

    parts.append(opening)

    # --- Where it got to -----------------------------------------------------
    if appt is not None:
        from app.agent.tools import when_label

        word = "Confirmed" if appt.status == "confirmed" else "Booked"
        parts.append(f"{word} for {when_label(appt.starts_at)}")

    escalation = (
        db.query(Escalation)
        .filter(Escalation.conversation_id == c.id, Escalation.claimed_at.is_(None))
        .order_by(Escalation.created_at.asc())
        .first()
    )
    if escalation is not None:
        reason = (escalation.reason or "").strip()
        parts.append(f"Waiting on a person: {reason}" if reason else "Waiting on a person")

    if c.outcome == "declined":
        parts.append("Closed as a client decline")

    # --- Nothing above fired ------------------------------------------------
    if len(parts) == 1:
        turns = db.query(Message).filter_by(conversation_id=c.id, role="buyer").count()
        if turns == 0:
            return ""
        # "nothing captured yet" would be a lie now that the fields live in
        # their own panel: there may be four of them an inch below this line.
        # What is actually missing is an outcome.
        parts.append(
            f"{turns} message{'' if turns == 1 else 's'} in, nothing booked yet"
        )

    return " ".join(_sentence(p) for p in parts if p)


def _title(v: Vehicle) -> str:
    return " ".join(str(x) for x in (v.year, v.make, v.model, v.trim) if x).strip()


def lead_recap(db: Session, lead: Lead) -> str:
    """The same recap, for a buyer rather than for one of their threads.

    Not `conversation_recap` on the newest conversation, which is what the
    lead page did first: Devon booked on the website and rang back the next
    morning, so the newest thread is the call -- and the appointment hangs off
    the chat. The rail said "nothing booked yet" to a rep looking at a booked
    buyer. Anything that can span threads has to be asked across all of them.
    """
    convos = (
        db.query(Conversation)
        .filter_by(lead_id=lead.id)
        .order_by(Conversation.started_at.asc())
        .all()
    )
    name = (lead.name or "").strip() or "An unnamed buyer"
    if not convos:
        # An imported lead has never said anything. Saying so is the recap.
        return f"{name} arrived as a lead document. No conversation yet."

    channels = {c.channel for c in convos}
    if channels == {"chat", "voice"}:
        verb = "chatted and called"
    elif channels == {"voice"}:
        verb = "called in"
    else:
        verb = "started a chat"
    opening = f"{name} {verb}"
    if len(convos) > 1:
        opening += f" across {len(convos)} conversations"

    vehicle = None
    for convo in reversed(convos):
        if convo.focus_vehicle_id:
            vehicle = db.query(Vehicle).filter_by(id=convo.focus_vehicle_id).one_or_none()
            if vehicle is not None:
                break

    appt = (
        db.query(Appointment)
        .filter(
            Appointment.lead_id == lead.id,
            Appointment.status.in_(("booked", "confirmed")),
        )
        .order_by(Appointment.starts_at.asc())
        .first()
    )
    if vehicle is None and appt is not None and appt.vehicle_id:
        vehicle = db.query(Vehicle).filter_by(id=appt.vehicle_id).one_or_none()
    if vehicle is None:
        shown = (
            db.query(Vehicle)
            .join(VehicleMention, VehicleMention.vehicle_id == Vehicle.id)
            .filter(VehicleMention.conversation_id.in_([c.id for c in convos]))
            .order_by(VehicleMention.created_at.desc())
            .first()
        )
        vehicle = shown
    if vehicle is not None:
        opening += f" about the {_title(vehicle)}"

    parts = [opening]

    if appt is not None:
        from app.agent.tools import when_label

        word = "Confirmed" if appt.status == "confirmed" else "Booked"
        parts.append(f"{word} for {when_label(appt.starts_at)}")

    escalation = (
        db.query(Escalation)
        .filter(
            Escalation.conversation_id.in_([c.id for c in convos]),
            Escalation.claimed_at.is_(None),
        )
        .order_by(Escalation.created_at.asc())
        .first()
    )
    if escalation is not None:
        reason = (escalation.reason or "").strip()
        parts.append(f"Waiting on a person: {reason}" if reason else "Waiting on a person")

    # Declined only while it stays declined -- a buyer who said no in March and
    # is chatting again today has not declined anything.
    still_open = [c for c in convos if c.status != "closed"]
    if not still_open and any(c.outcome == "declined" for c in convos):
        parts.append("Closed as a client decline")

    if len(parts) == 1 and appt is None:
        turns = (
            db.query(Message)
            .filter(
                Message.conversation_id.in_([c.id for c in convos]),
                Message.role == "buyer",
            )
            .count()
        )
        parts.append(f"{turns} message{'' if turns == 1 else 's'} in, nothing booked yet")

    return " ".join(_sentence(p) for p in parts if p)
