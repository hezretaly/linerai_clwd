"""Tool definitions and executors.

Two rules are enforced here rather than in the prompt, because the prompt is a
request and the executor is a guarantee:

1. A vehicle marked do-not-discuss never reaches the model at all. Filtering in
   the tool is safer than instructing the model not to mention it.
2. ``save_captured_fields`` rejects provenance='typed' for any value the buyer
   did not actually say. The model must not be able to launder a guess into a
   fact a rep repeats on the phone (§18.2).

``book_appointment`` and ``escalate_to_human`` are the only tools with side
effects, so both are idempotent on (conversation_id, tool_call_id) -- a retried
turn must not double-book.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db import utcnow
from app.events import emit
from app.models import (
    Appointment,
    Dealership,
    Conversation,
    Escalation,
    HandoffRule,
    KnowledgeEntry,
    Lead,
    Message,
    Outreach,
    Vehicle,
    VehicleMention,
)

MAX_RESULTS = 5

# A buyer asks for "something German"; the inventory row records a make. This is
# the mapping between the two, and it is curated rather than inferred -- there
# is no country column on a vehicle and guessing one from a VIN's world
# manufacturer identifier gets assembly plants, not marques. A make missing from
# this table simply has no origin, which is a smaller error than a wrong one.
ORIGIN_BY_MAKE = {
    "audi": "german", "bmw": "german", "mercedes": "german",
    "mercedes-benz": "german", "porsche": "german", "volkswagen": "german",
    "vw": "german", "mini": "german",
    "acura": "japanese", "honda": "japanese", "infiniti": "japanese",
    "lexus": "japanese", "mazda": "japanese", "mitsubishi": "japanese",
    "nissan": "japanese", "subaru": "japanese", "toyota": "japanese",
    "genesis": "korean", "hyundai": "korean", "kia": "korean",
    "buick": "american", "cadillac": "american", "chevrolet": "american",
    "chrysler": "american", "dodge": "american", "ford": "american",
    "gmc": "american", "jeep": "american", "lincoln": "american",
    "ram": "american", "tesla": "american",
    "jaguar": "british", "land rover": "british", "mg": "british",
    "alfa romeo": "italian", "fiat": "italian", "maserati": "italian",
    "volvo": "swedish",
}

# The words a buyer actually uses, mapped onto the values above.
ORIGIN_ALIASES = {
    "germany": "german", "german": "german", "euro": "european",
    "european": "european", "europe": "european",
    "japan": "japanese", "japanese": "japanese", "jdm": "japanese",
    "korea": "korean", "korean": "korean",
    "america": "american", "american": "american", "usa": "american",
    "us": "american", "domestic": "american",
    "britain": "british", "british": "british", "uk": "british",
    "italy": "italian", "italian": "italian",
    "sweden": "swedish", "swedish": "swedish",
}

# "European" is a family, not a single origin -- a buyer asking for one means
# any of these.
EUROPEAN = {"german", "british", "italian", "swedish"}


def origin_of(make: str) -> str:
    return ORIGIN_BY_MAKE.get((make or "").strip().lower(), "")


def _origin_matches(make: str, wanted: str) -> bool:
    origin = origin_of(make)
    if not origin:
        return False
    if wanted == "european":
        return origin in EUROPEAN
    return origin == wanted
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ToolError(Exception):
    """A tool refused. The message goes back to the model as a tool result."""


# --------------------------------------------------------------------------
# Definitions (Anthropic tool schema)
# --------------------------------------------------------------------------

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "search_inventory",
        "description": (
            "Search available vehicles. Returns at most 5. Use this before naming any "
            "vehicle or quoting any price -- you have no inventory knowledge without it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keywords": {"type": "string", "description": "Free text, e.g. 'third row awd'"},
                "max_price": {"type": "integer"},
                "min_price": {"type": "integer"},
                "min_year": {"type": "integer"},
                "max_mileage": {
                    "type": "integer",
                    "description": "Miles on the clock, e.g. 100000 for 'under 100k miles'",
                },
                "min_mileage": {"type": "integer"},
                "body_style": {"type": "string"},
                "min_seats": {"type": "integer"},
                "origin": {
                    "type": "string",
                    "description": "Where the marque is from, for 'something German'.",
                    "enum": ["german", "japanese", "korean", "american", "british",
                             "italian", "swedish", "european"],
                },
            },
        },
    },
    {
        "name": "get_vehicle",
        "description": "Full detail for one vehicle by VIN.",
        "input_schema": {
            "type": "object",
            "properties": {"vin": {"type": "string"}},
            "required": ["vin"],
        },
    },
    {
        "name": "check_availability",
        "description": (
            "Open appointment slots. Always offer two concrete times from this result; "
            "never ask the buyer when works for them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {"type": "integer", "description": "Defaults to 7"},
                "preferred_period": {
                    "type": "string",
                    "enum": ["morning", "afternoon", "evening", "any"],
                },
            },
        },
    },
    {
        "name": "book_appointment",
        "description": (
            "Book a test drive. Requires a name and an email address; phone is optional."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "starts_at": {"type": "string", "description": "ISO 8601"},
                "vin": {"type": "string"},
            },
            "required": ["name", "email", "starts_at"],
        },
    },
    {
        "name": "save_captured_fields",
        "description": (
            "Record what you learned about the buyer. Provenance must be honest: use "
            "'typed' only when the buyer said the value in their own words, 'listing' "
            "when it came from a vehicle record, 'caller_id' for phone metadata, and "
            "'inferred' for anything you concluded. Dishonest provenance is rejected."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "value": {"type": "string"},
                            "provenance": {
                                "type": "string",
                                "enum": ["typed", "listing", "caller_id", "inferred"],
                            },
                        },
                        "required": ["key", "value", "provenance"],
                    },
                }
            },
            "required": ["fields"],
        },
    },
    {
        "name": "answer_from_knowledge",
        "description": (
            "Look up the dealership's own answer to a policy question -- trade-ins, doc "
            "fee, deposits, financing, warranty, out-of-state buyers, hours. Use this "
            "for anything about how the dealership operates. You have no policy "
            "knowledge without it, and a wrong answer here is one a buyer repeats back "
            "to a rep."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The buyer's question, in their own words.",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "close_conversation",
        "description": (
            "Call this ONLY when the buyer has said they are done -- 'that's all', "
            "'thanks, bye', 'I'll think about it'. Never end a conversation yourself "
            "because you have run out of things to say; ask a question instead. Before "
            "calling it, offer to email them a summary of what you found, and pass "
            "send_summary=true only if they say yes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "Two or three sentences a rep can read in five seconds: what "
                        "they wanted, what you showed them, what happens next."
                    ),
                },
                "send_summary": {
                    "type": "boolean",
                    "description": "True only if the buyer asked for it by email.",
                },
            },
            "required": ["summary"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Stop and hand the conversation to a person. Use for out-the-door price "
            "requests, credit or financing trouble, a request for a manager, urgency "
            "inside a few days, or a buyer ready to sign."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rule_key": {
                    "type": "string",
                    "enum": [
                        "out_the_door_price", "financing_trouble", "asks_for_manager",
                        "urgency", "ready_to_sign",
                    ],
                },
                "reason": {"type": "string"},
            },
            "required": ["rule_key", "reason"],
        },
    },
]


# --------------------------------------------------------------------------
# Executors
# --------------------------------------------------------------------------


def _vehicle_payload(v: Vehicle) -> dict:
    payload = {
        "vin": v.vin,
        "year": v.year,
        "make": v.make,
        "model": v.model,
        "trim": v.trim,
        "price": v.price,
        "mileage": v.mileage,
        "body_style": v.body_style,
        "seats": v.seats,
        "origin": origin_of(v.make),
        "features": json.loads(v.features_json or "[]"),
        "photo_url": v.photo_url,
        "status": v.status,
    }
    if v.rule_hold_price:
        payload["price_note"] = "This price is firm. Do not suggest it is negotiable."
    if v.rule_mention_warranty:
        payload["warranty_note"] = (
            "Mention the 90-day/4,000-mile limited powertrain warranty on this vehicle."
        )
    if v.rule_note:
        payload["internal_note"] = v.rule_note
    return payload


def _record_mentions(db: Session, conversation_id: str, vehicles: list[Vehicle]) -> None:
    for v in vehicles:
        db.add(VehicleMention(
            conversation_id=conversation_id, vehicle_id=v.id, quoted_price=v.price
        ))
    db.commit()


def search_inventory(db: Session, convo: Conversation, args: dict) -> dict:
    query = db.query(Vehicle).filter(
        Vehicle.status == "available",
        # A do-not-discuss vehicle never reaches the model.
        Vehicle.rule_discuss.is_(True),
    )
    if args.get("max_price"):
        query = query.filter(Vehicle.price <= int(args["max_price"]))
    if args.get("min_price"):
        query = query.filter(Vehicle.price >= int(args["min_price"]))
    if args.get("min_year"):
        query = query.filter(Vehicle.year >= int(args["min_year"]))
    if args.get("body_style"):
        query = query.filter(Vehicle.body_style.ilike(f"%{args['body_style']}%"))
    if args.get("min_seats"):
        query = query.filter(Vehicle.seats >= int(args["min_seats"]))
    if args.get("max_mileage"):
        query = query.filter(Vehicle.mileage <= int(args["max_mileage"]))
    if args.get("min_mileage"):
        query = query.filter(Vehicle.mileage >= int(args["min_mileage"]))

    rows = query.order_by(Vehicle.price.asc()).all()

    # Origin is not a column, so it filters in Python after the SQL narrows.
    wanted_origin = ORIGIN_ALIASES.get((args.get("origin") or "").strip().lower(), "")
    if wanted_origin:
        rows = [v for v in rows if _origin_matches(v.make, wanted_origin)]

    keywords = (args.get("keywords") or "").lower().split()
    if keywords:
        def score(v: Vehicle) -> int:
            haystack = f"{v.keywords} {v.make} {v.model} {v.trim} {v.body_style}".lower()
            return sum(1 for k in keywords if k in haystack)

        scored = [(score(v), v) for v in rows]
        if any(s for s, _ in scored):
            rows = [v for s, v in sorted(scored, key=lambda p: -p[0]) if s > 0]

    rows = rows[:MAX_RESULTS]
    _record_mentions(db, convo.id, rows)
    if not rows:
        # Liner was escalating "do you have anything German?" to a human rather
        # than saying no. Nothing is a complete, correct answer, and it is the
        # one a buyer can act on; the guidance says so because the model's
        # instinct is to treat an empty hand as uncertainty.
        return {
            "count": 0,
            "vehicles": [],
            "guidance": (
                "Nothing on the lot matches. Say so plainly, in your own words -- this "
                "is a certain answer, not a gap in your knowledge, so do not hand it to "
                "a person. Then offer the nearest thing: drop one filter and search "
                "again, or ask what matters most to them."
            ),
        }
    return {"count": len(rows), "vehicles": [_vehicle_payload(v) for v in rows]}


def get_vehicle(db: Session, convo: Conversation, args: dict) -> dict:
    vin = (args.get("vin") or "").upper().strip()
    vehicle = (
        db.query(Vehicle)
        .filter(Vehicle.vin == vin, Vehicle.rule_discuss.is_(True))
        .one_or_none()
    )
    if vehicle is None:
        raise ToolError(f"No vehicle available with VIN {vin}.")
    _record_mentions(db, convo.id, [vehicle])
    convo.focus_vehicle_id = vehicle.id
    db.commit()
    return _vehicle_payload(vehicle)


def _dealership_hours(db: Session) -> dict:
    from app.models import Dealership

    dealership = db.query(Dealership).first()
    return json.loads(dealership.hours_json or "{}") if dealership else {}


DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def clock_label(when: datetime) -> str:
    """"10:00 AM" -- no leading zero, and no %-I, which is glibc-only."""
    return f"{(when.hour % 12) or 12}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"


def when_label(when: datetime) -> str:
    """"Tuesday 12 August at 10:00 AM". Naive datetimes here are dealership-local
    already, so this formats the frame it is given and converts nothing."""
    return f"{when:%A} {when.day} {when:%B} at {clock_label(when)}"


def check_availability(db: Session, convo: Conversation, args: dict) -> dict:
    """Open slots from the calendar and the dealership's hours. 8 AM - 8 PM,
    closed Sunday -- read from the config row, never hardcoded."""
    from app.api.settings import live_settings

    hours = _dealership_hours(db)
    slot_len = live_settings(db).booking_slot_length
    days_ahead = int(args.get("days_ahead") or 7)
    period = args.get("preferred_period") or "any"

    now = utcnow()
    taken = {
        a.starts_at.replace(second=0, microsecond=0)
        for a in db.query(Appointment)
        .filter(Appointment.starts_at >= now, Appointment.status.in_(["booked", "confirmed"]))
        .all()
    }

    windows = {"morning": (8, 12), "afternoon": (12, 17), "evening": (17, 20), "any": (8, 20)}
    lo, hi = windows.get(period, windows["any"])

    slots: list[str] = []
    for day_offset in range(1, days_ahead + 1):
        day = (now + timedelta(days=day_offset)).replace(minute=0, second=0, microsecond=0)
        window = hours.get(DAY_NAMES[day.weekday()])
        if not window:
            continue  # closed
        open_h = int(window["open"].split(":")[0])
        close_h = int(window["close"].split(":")[0])
        start_h, end_h = max(open_h, lo), min(close_h, hi)

        # Cap per day so the result spans the week. Twelve consecutive
        # half-hours on one morning is one option dressed up as twelve, and
        # the caller needs two genuinely different times to offer.
        cursor = day.replace(hour=start_h)
        per_day = 0
        while cursor.hour < end_h and per_day < 3:
            if cursor > now and cursor.replace(second=0, microsecond=0) not in taken:
                slots.append(cursor.isoformat())
                per_day += 1
            cursor += timedelta(hours=3)
        if len(slots) >= 12:
            break

    return {"slot_minutes": slot_len, "slots": slots[:12]}


def book_appointment(
    db: Session, convo: Conversation, args: dict, tool_call_id: str | None = None
) -> dict:
    # Idempotent: a retried turn must not produce two appointments.
    if tool_call_id:
        existing = (
            db.query(Appointment)
            .filter_by(conversation_id=convo.id, tool_call_id=tool_call_id)
            .one_or_none()
        )
        if existing is not None:
            return {"appointment_id": existing.id, "starts_at": existing.starts_at.isoformat(),
                    "already_booked": True}

    name = (args.get("name") or "").strip()
    email = (args.get("email") or "").strip().lower()
    phone = (args.get("phone") or "").strip()

    if not name:
        raise ToolError("A name is required to book.")
    if not EMAIL_RE.match(email):
        raise ToolError(
            "A valid email address is required to book -- it is how the rep confirms "
            "and follows up. Ask the buyer where to send the confirmation."
        )

    try:
        starts_at = datetime.fromisoformat(str(args["starts_at"]).replace("Z", ""))
    except (KeyError, ValueError) as exc:
        raise ToolError("starts_at must be an ISO 8601 datetime.") from exc
    if starts_at.tzinfo is not None:
        starts_at = starts_at.replace(tzinfo=None)

    hours = _dealership_hours(db)
    window = hours.get(DAY_NAMES[starts_at.weekday()])
    if not window:
        raise ToolError(f"We are closed on {DAY_NAMES[starts_at.weekday()].title()}.")
    if not (int(window["open"][:2]) <= starts_at.hour < int(window["close"][:2])):
        raise ToolError(
            f"That is outside our hours ({window['open']} to {window['close']})."
        )

    # Nothing here checked the slot was still free. check_availability filters
    # taken slots, but that answer ages: a buyer looking at a picked time on a
    # booking card can sit on it for minutes, and the model can offer a time it
    # read several turns ago. Two buyers then get the same 10 AM and one of
    # them turns up to nobody. The executor is the guarantee, so it checks.
    clash = (
        db.query(Appointment)
        .filter(
            Appointment.starts_at == starts_at,
            Appointment.status.in_(["booked", "confirmed"]),
        )
        .first()
    )
    if clash is not None:
        raise ToolError(
            f"{when_label(starts_at)} was taken while you were deciding. "
            "Call check_availability again and offer what is still open."
        )

    lead = db.query(Lead).filter_by(id=convo.lead_id).one_or_none() if convo.lead_id else None
    if lead is None:
        lead = db.query(Lead).filter(Lead.email == email).one_or_none()
    if lead is None:
        lead = Lead(name=name, email=email, phone=phone,
                    source="voice" if convo.channel == "voice" else "chat")
        db.add(lead)
        db.flush()
    else:
        lead.name = lead.name or name
        lead.email = lead.email or email
        lead.phone = lead.phone or phone
    # The buyer gave the address for this purpose -- that consent is the record.
    lead.email_consent_at = lead.email_consent_at or utcnow()
    convo.lead_id = lead.id

    vehicle = None
    if args.get("vin"):
        vehicle = db.query(Vehicle).filter_by(vin=str(args["vin"]).upper()).one_or_none()
    elif convo.focus_vehicle_id:
        vehicle = db.query(Vehicle).filter_by(id=convo.focus_vehicle_id).one_or_none()

    from app.api.settings import live_settings

    appointment = Appointment(
        lead_id=lead.id,
        vehicle_id=vehicle.id if vehicle else None,
        starts_at=starts_at,
        duration_min=live_settings(db).booking_slot_length,
        status="booked",
        booked_by="liner",
        conversation_id=convo.id,
        tool_call_id=tool_call_id,
    )
    db.add(appointment)
    convo.stage = "booked"
    db.commit()
    db.refresh(appointment)

    emit(db, "appointment.booked", {
        "appointment_id": appointment.id,
        "lead_id": lead.id,
        "lead_name": lead.name,
        "conversation_id": convo.id,
        "vehicle_id": vehicle.id if vehicle else None,
        "starts_at": appointment.starts_at.isoformat(),
    })
    return {
        "appointment_id": appointment.id,
        "starts_at": appointment.starts_at.isoformat(),
        "duration_min": appointment.duration_min,
        "vehicle": _vehicle_payload(vehicle) if vehicle else None,
        "lead_id": lead.id,
    }


def save_captured_fields(db: Session, convo: Conversation, args: dict) -> dict:
    """Provenance is enforced, not requested.

    'typed' is only accepted when the value appears in something the buyer
    actually wrote in this conversation. Anything else is downgraded to
    'inferred' and the rejection is reported back, so the model learns the
    boundary within the turn.
    """
    if not convo.lead_id:
        raise ToolError("No lead on this conversation yet; book or capture contact first.")

    buyer_text = " ".join(
        m.content.lower()
        for m in db.query(Message)
        .filter_by(conversation_id=convo.id, role="buyer")
        .all()
    )

    from app.models import CapturedField

    saved, downgraded = [], []
    for field in args.get("fields", []):
        key = (field.get("key") or "").strip()
        value = (field.get("value") or "").strip()
        provenance = (field.get("provenance") or "inferred").strip()
        if not key or not value:
            continue
        if provenance not in {"typed", "listing", "caller_id", "inferred"}:
            provenance = "inferred"

        if provenance == "typed" and value.lower() not in buyer_text:
            provenance = "inferred"
            downgraded.append(key)

        row = (
            db.query(CapturedField)
            .filter_by(lead_id=convo.lead_id, key=key)
            .one_or_none()
        )
        if row is None:
            row = CapturedField(lead_id=convo.lead_id, key=key, value=value,
                                provenance=provenance)
            db.add(row)
        else:
            row.value = value
            row.provenance = provenance
        saved.append({"key": key, "value": value, "provenance": provenance})

    db.commit()
    emit(db, "lead.qualified", {
        "lead_id": convo.lead_id, "conversation_id": convo.id, "fields": [s["key"] for s in saved],
    })

    result: dict[str, Any] = {"saved": saved}
    if downgraded:
        result["rejected_typed"] = downgraded
        result["note"] = (
            "Those fields were recorded as 'inferred' because the buyer did not say them "
            "in those words. Only use 'typed' for values quoted from a buyer message."
        )
    return result


def close_conversation(
    db: Session, convo: Conversation, args: dict, tool_call_id: str | None = None
) -> dict:
    """The buyer said they were done. Write the summary, optionally email it.

    Idempotent on tool_call_id like the other side-effecting tools: a retried
    turn must not send a second summary.
    """
    summary = (args.get("summary") or "").strip()
    if not summary:
        raise ToolError("close_conversation needs a summary of what happened.")

    if convo.ended_at is not None:
        return {"closed": True, "already_closed": True, "summary": convo.summary}

    convo.summary = summary
    convo.ended_at = utcnow()
    # 'closed' only if nobody is waiting on it. An escalated thread stays in the
    # dealer's queue even though the buyer has gone -- that is the whole point
    # of the escalation, and a rep still owes them a call back.
    if convo.status != "handoff":
        convo.status = "closed"
    db.commit()

    result = {"closed": True, "summary": summary, "emailed": False}

    if not args.get("send_summary"):
        return result

    lead = db.query(Lead).filter_by(id=convo.lead_id).one_or_none() if convo.lead_id else None
    if lead is None or not lead.email:
        result["note"] = (
            "They asked for it by email but we have no address on file. Ask for one, "
            "then call this again."
        )
        return result

    from app.integrations.registry import get_email_sender

    sender = get_email_sender()
    record = Outreach(
        lead_id=lead.id, channel="email", to_address=lead.email,
        subject=f"Your conversation with {db.query(Dealership).first().name}",
        body=summary, provider=sender.name, status="queued",
    )
    db.add(record)
    db.commit()

    # Delivery is the outbox unless Gmail is configured, and the buyer was just
    # promised an email. Say which happened rather than reporting success.
    result["emailed"] = True
    result["delivered_externally"] = sender.delivers
    result["to"] = lead.email
    if not sender.delivers:
        result["note"] = (
            "Recorded, not delivered -- no email provider is configured. Tell the "
            "buyer a colleague will send it rather than saying it is on its way."
        )
    emit(db, "outreach.sent", {
        "outreach_id": record.id, "lead_id": lead.id, "to": lead.email,
        "provider": record.provider, "delivered_externally": sender.delivers,
        "conversation_id": convo.id, "appointment_id": None,
    })
    return result


def escalate_to_human(
    db: Session, convo: Conversation, args: dict, tool_call_id: str | None = None
) -> dict:
    if tool_call_id:
        existing = (
            db.query(Escalation)
            .filter_by(conversation_id=convo.id, tool_call_id=tool_call_id)
            .one_or_none()
        )
        if existing is not None:
            return {"escalation_id": existing.id, "already_escalated": True}

    # One open handoff per conversation. The tool_call_id check above only
    # makes a *retried* call idempotent; a second turn escalating for a second
    # reason used to add a second unclaimed row. That is not a second job for a
    # rep, it is the same conversation sitting in the queue twice -- and when a
    # guard misfired every turn it stacked up a row per message.
    open_row = (
        db.query(Escalation)
        .filter(Escalation.conversation_id == convo.id, Escalation.claimed_at.is_(None))
        .order_by(Escalation.created_at.asc())
        .first()
    )
    if open_row is not None:
        convo.status = "handoff"
        convo.stage = "escalated"
        db.commit()
        return {"escalation_id": open_row.id, "already_escalated": True}

    rule_key = args.get("rule_key") or ""
    rule = db.query(HandoffRule).filter_by(key=rule_key).one_or_none()
    if rule is not None and not rule.enabled:
        # The rule is switched off, so Liner keeps going instead of stopping.
        # That is the dealership's choice and the setup page warns about it.
        return {"escalated": False, "reason": f"The '{rule_key}' handoff rule is disabled."}

    escalation = Escalation(
        conversation_id=convo.id,
        handoff_rule_id=rule.id if rule else None,
        reason=(args.get("reason") or "").strip(),
        tool_call_id=tool_call_id,
    )
    db.add(escalation)
    convo.status = "handoff"
    # Deliberately NOT agent_paused. Escalating used to gag Liner immediately,
    # so a buyer who asked one question a human had to answer got "someone is
    # picking this up personally" to everything they said afterwards -- and
    # since nobody is watching the queue at 9pm, that was the end of the
    # conversation. Only a rep pressing Take over stops Liner
    # (api/conversations.py), because only then is a person actually there.
    convo.stage = "escalated"
    if rule is not None:
        rule.fired_count += 1
    db.commit()
    db.refresh(escalation)

    emit(db, "handoff.triggered", {
        "escalation_id": escalation.id,
        "conversation_id": convo.id,
        "rule_key": rule_key,
        "reason": escalation.reason,
        "notify": rule.notify if rule else "dashboard",
    })

    # A handoff with no way to reach the buyer is a lost lead, not a handoff.
    lead = db.query(Lead).filter_by(id=convo.lead_id).one_or_none() if convo.lead_id else None
    reachable = bool(lead and (lead.email or lead.phone))
    guidance = (
        "A colleague has been notified. Keep talking to the buyer -- you are not "
        "finished and they are not on hold. Carry on with anything else you can "
        "answer yourself."
    )
    if not reachable:
        guidance += (
            " We have no way to reach this buyer, so ask for a name and an email "
            "address now, in one short sentence, so the rep can follow up if the "
            "buyer leaves. Save it with save_captured_fields."
        )
    return {
        "escalation_id": escalation.id,
        "escalated": True,
        "rule_key": rule_key,
        "buyer_reachable": reachable,
        "guidance": guidance,
    }


def answer_from_knowledge(db: Session, convo: Conversation, args: dict) -> dict:
    """The dealership's own words on a policy question.

    These questions -- trade-ins, the doc fee, deposits -- need no inventory
    lookup and have exactly one right answer, which the dealer wrote. Without
    this the model either invents one (the guards then stop the reply and
    escalate, which is what "do you take trade-ins" was doing) or hands a
    trivial question to a human.

    Returning "no entry" is a real answer: it tells the model to escalate
    rather than fill the gap itself.
    """
    question = (args.get("question") or "").strip()
    if not question:
        raise ToolError("answer_from_knowledge needs the buyer's question.")

    entry = lookup_knowledge(db, question)
    if entry is None:
        return {
            "found": False,
            "topics": [e.topic for e in db.query(KnowledgeEntry).all()],
            "guidance": (
                "The dealership has no written answer to this. Do not compose one -- "
                "say a colleague will confirm, and escalate if it matters to the sale."
            ),
        }

    entry.use_count += 1
    db.commit()
    return {"found": True, "topic": entry.topic, "answer": entry.answer}


def _terms(text: str) -> set[str]:
    """Content words, with a trailing plural 's' stripped.

    Crude on purpose -- it only has to make "deposit" match the "Deposits"
    entry. A real stemmer is a dependency and a behaviour change for the sake
    of a seven-row table.
    """
    out = set()
    for word in re.findall(r"[a-z]+", text.lower()):
        if word in STOPWORDS or len(word) < 2:
            continue
        out.add(word[:-1] if len(word) > 3 and word.endswith("s") else word)
    return out


# Words that carry no topic signal. Filtering on length instead drops "doc",
# "fee", "tax" and "apr" -- the exact words a buyer uses for these questions --
# while keeping "your", which then matches almost every entry.
STOPWORDS = {
    "a", "about", "an", "and", "any", "anything", "are", "at", "back", "be", "buy",
    "can", "car", "could", "did", "do", "does", "for", "from", "get", "guys", "has",
    "have", "how", "i", "if", "in", "is", "it", "its", "just", "me", "much", "my",
    "of", "on", "or", "our", "so", "some", "take", "tell", "than", "that", "the",
    "their", "them", "then", "there", "they", "this", "to", "us", "want", "was",
    "we", "what", "whats", "when", "where", "which", "will", "with", "would",
    "you", "your", "yours",
}


def lookup_knowledge(db: Session, question: str) -> KnowledgeEntry | None:
    """Shared by the stub agent's knowledge rails and the tool above.

    A match on the *topic* is worth far more than a match anywhere in the
    answer: every answer mentions the dealership, so answer-body overlap alone
    picks a plausible-looking wrong entry. Asking about the doc fee and being
    told the deposit policy is worse than not answering, because the buyer
    repeats it to a rep.
    """
    words = _terms(question)
    if not words:
        return None

    best, best_score = None, 0.0
    for entry in db.query(KnowledgeEntry).all():
        topic_words = _terms(entry.topic)
        answer_words = _terms(entry.answer)
        score = 3.0 * len(words & topic_words) + len(words & answer_words)
        if score > best_score:
            best, best_score = entry, score
    # One incidental word in common is noise. Either the topic matched, or
    # several distinct words did.
    return best if best_score >= 3.0 else None


EXECUTORS = {
    "answer_from_knowledge": answer_from_knowledge,
    "search_inventory": search_inventory,
    "get_vehicle": get_vehicle,
    "check_availability": check_availability,
    "save_captured_fields": save_captured_fields,
}

SIDE_EFFECT_EXECUTORS = {
    "close_conversation": close_conversation,
    "book_appointment": book_appointment,
    "escalate_to_human": escalate_to_human,
}


SCHEMAS = {t["name"]: set(t["input_schema"].get("properties", {})) for t in TOOL_DEFS}


def _reject_unknown_args(name: str, args: dict) -> None:
    """An argument the tool does not have is an error, not something to ignore.

    A free-form model will invent a parameter name eventually -- calling
    search_inventory with `need` instead of `keywords`, say. Dropping it
    silently is the worst outcome available: the filter never applies, five
    unrelated cars come back, and the model quotes a price off a vehicle the
    buyer never asked about. Confidently wrong beats obviously broken only for
    the demo, never for the buyer.

    Told about it, the model retries with the right name in the next round.
    """
    allowed = SCHEMAS.get(name)
    if allowed is None:
        return
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise ToolError(
            f"{name} has no argument {', '.join(repr(u) for u in unknown)}. "
            f"It takes: {', '.join(sorted(allowed)) or 'no arguments'}."
        )


def execute(
    db: Session, convo: Conversation, name: str, args: dict, tool_call_id: str | None = None
) -> dict:
    _reject_unknown_args(name, args)
    if name in SIDE_EFFECT_EXECUTORS:
        return SIDE_EFFECT_EXECUTORS[name](db, convo, args, tool_call_id)
    if name in EXECUTORS:
        return EXECUTORS[name](db, convo, args)
    raise ToolError(f"Unknown tool {name}")
