"""A rep-facing recap of one conversation, composed from rows.

The rail used to show `conversations.summary`, which is whatever Liner said
last -- a reply, not a summary. A rep opening a thread wants to know who this
is, what car it is about, what was captured and where it got to, and the last
line answers none of that.

Composed here rather than asked of the model, for the same reason
`answer_from_knowledge` is: a model-written summary is a second place a fact
can be invented, it costs a call per turn, and there is no model at all in
stub mode. Everything below comes from a row -- captured fields, vehicle
mentions, the appointment, the escalation. If a clause is missing it is
because the row is missing, which is itself worth knowing.

`conversations.summary` stays what it was -- the last thing Liner said, or the
sign-off `close_conversation` wrote -- and still backs the one-line preview in
the list. This is the panel a rep reads before opening the transcript.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import (
    Appointment,
    CapturedField,
    Conversation,
    Escalation,
    Lead,
    Message,
    Vehicle,
    VehicleMention,
)

# Contact details have their own panel two inches up the rail; repeating them
# in prose costs a line and says nothing new.
SKIP_KEYS = {"name", "email", "phone", "vehicle_interest"}

# Keys worth a plainer word than their snake_case. Anything not here is
# de-underscored as-is, so a new capture key still reads.
FIELD_LABEL = {
    "trade_in": "Trade-in",
    "use_case": "Use",
    "seats_needed": "Seats",
    "buying_signal": "Signal",
}

CHANNEL_VERB = {"chat": "started a chat", "voice": "called in"}


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _field_label(key: str) -> str:
    """`Label: value`, never `label value`. A captured value is the buyer's own
    phrasing -- "likely financing", "third row" -- and gluing a key in front of
    it produced "financing likely financing". A colon makes no claim about how
    the two fit together grammatically, so it cannot get that wrong."""
    label = FIELD_LABEL.get(key)
    if label:
        return label
    plain = key.replace("_", " ")
    return plain[:1].upper() + plain[1:]


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

    # --- What Liner got out of them -----------------------------------------
    if lead is not None:
        fields = (
            db.query(CapturedField)
            .filter(CapturedField.lead_id == lead.id, CapturedField.key.notin_(SKIP_KEYS))
            .order_by(CapturedField.updated_at.asc())
            .all()
        )
        if fields:
            parts.append(
                " · ".join(f"{_field_label(f.key)}: {f.value}" for f in fields[:4])
            )

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
        parts.append(
            f"{turns} message{'' if turns == 1 else 's'} in, nothing captured yet"
        )

    return " ".join(_sentence(p) for p in parts if p)


def _title(v: Vehicle) -> str:
    return " ".join(str(x) for x in (v.year, v.make, v.model, v.trim) if x).strip()
