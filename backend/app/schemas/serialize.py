"""Model -> dict serializers.

Hand-written rather than Pydantic response models: the frontend types in
``lib/types.ts`` are hand-written too, and one shaping layer is easier to keep
honest than two. Shapes follow the mockups' data files (§18.1) with tuple rows
turned into typed objects and every id a UUID string.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import (
    Appointment,
    AssistantSettings,
    CapturedField,
    Conversation,
    Dealership,
    Escalation,
    HandoffRule,
    IngestRun,
    KnowledgeEntry,
    Lead,
    Message,
    Outreach,
    Rail,
    User,
    Vehicle,
)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def loads(raw: str, fallback):
    try:
        return json.loads(raw or "")
    except (ValueError, TypeError):
        return fallback


def user_out(u: User | None) -> dict | None:
    if u is None:
        return None
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "role": u.role,
        "avatar_initials": u.avatar_initials,
        "daily_cap": u.daily_cap,
        "notify_channel": u.notify_channel,
        "active": u.active,
    }


def dealership_out(d: Dealership) -> dict:
    return {
        "id": d.id,
        "name": d.name,
        "timezone": d.timezone,
        "hours": loads(d.hours_json, {}),
        "address": d.address,
        "phone": d.phone,
        "website_url": d.website_url,
    }


def vehicle_out(v: Vehicle, *, mentions: int = 0) -> dict:
    return {
        "id": v.id,
        "vin": v.vin,
        "year": v.year,
        "make": v.make,
        "model": v.model,
        "trim": v.trim,
        "title": f"{v.year} {v.make} {v.model}".strip(),
        "price": v.price,
        "mileage": v.mileage,
        "body_style": v.body_style,
        "seats": v.seats,
        "title_status": v.title_status,
        "features": loads(v.features_json, []),
        "photo_url": v.photo_url,
        "listing_url": v.listing_url,
        "status": v.status,
        "source": v.source,
        "rules": {
            "discuss": v.rule_discuss,
            "hold_price": v.rule_hold_price,
            "mention_warranty": v.rule_mention_warranty,
            "note": v.rule_note,
        },
        "manual_fields": loads(v.manual_fields_json, []),
        "mention_count": mentions,
        "first_seen_at": iso(v.first_seen_at),
        "last_seen_at": iso(v.last_seen_at),
    }


def captured_out(c: CapturedField) -> dict:
    return {
        "id": c.id,
        "key": c.key,
        "value": c.value,
        "provenance": c.provenance,
        # The UI renders inferred values in italic under "check before using
        # them on a call" -- the difference between reporting what we know and
        # laundering a guess into a fact a rep repeats on the phone.
        # 'adf' counts as verified: the buyer did state it, just on a
        # marketplace form rather than to us. Only 'inferred' is a guess.
        "verified": c.provenance != "inferred",
        "updated_at": iso(c.updated_at),
    }


def lead_out(lead: Lead, db: Session | None = None, *, detail: bool = False) -> dict:
    out = {
        "id": lead.id,
        "name": lead.name or "Unknown caller",
        "email": lead.email,
        "phone": lead.phone,
        "source": lead.source,
        "assigned_user_id": lead.assigned_user_id,
        # No email means the product has no way to reach them (§18.5).
        "contact_risk": lead.contact_risk,
        "email_consent_at": iso(lead.email_consent_at),
        "created_at": iso(lead.created_at),
    }
    if db is not None:
        out["assigned_to"] = user_out(
            db.query(User).filter_by(id=lead.assigned_user_id).one_or_none()
            if lead.assigned_user_id else None
        )
        fields = db.query(CapturedField).filter_by(lead_id=lead.id).all()
        out["captured_fields"] = [captured_out(f) for f in fields]
    if detail and db is not None:
        appts = (
            db.query(Appointment)
            .filter_by(lead_id=lead.id)
            .order_by(Appointment.starts_at.asc())
            .all()
        )
        out["appointments"] = [appointment_out(a, db) for a in appts]
        convos = db.query(Conversation).filter_by(lead_id=lead.id).all()
        out["conversations"] = [conversation_out(c, db) for c in convos]
        reach = (
            db.query(Outreach)
            .filter_by(lead_id=lead.id)
            .order_by(Outreach.created_at.desc())
            .all()
        )
        out["outreach"] = [outreach_out(o) for o in reach]
    return out


def message_out(m: Message) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "tool_calls": loads(m.tool_calls_json, []),
        "via_rail_id": m.via_rail_id,
        "created_at": iso(m.created_at),
    }


def conversation_out(c: Conversation, db: Session | None = None, *, detail: bool = False) -> dict:
    out = {
        "id": c.id,
        "lead_id": c.lead_id,
        "channel": c.channel,
        "status": c.status,
        "agent_paused": c.agent_paused,
        "stage": c.stage,
        "focus_vehicle_id": c.focus_vehicle_id,
        "started_at": iso(c.started_at),
        "ended_at": iso(c.ended_at),
        "summary": c.summary,
    }
    if db is not None:
        lead = db.query(Lead).filter_by(id=c.lead_id).one_or_none() if c.lead_id else None
        out["lead"] = lead_out(lead, db) if lead else None
        out["message_count"] = db.query(Message).filter_by(conversation_id=c.id).count()
        # Nothing stops a conversation having two unclaimed escalations, and
        # one_or_none() here turned that into a 500 on the whole conversations
        # list -- one poisoned row and the dealer's main page is gone. Show the
        # first one raised; the rest are the same handoff asked for twice.
        escalation = (
            db.query(Escalation)
            .filter(Escalation.conversation_id == c.id, Escalation.claimed_at.is_(None))
            .order_by(Escalation.created_at.asc())
            .first()
        )
        out["open_escalation"] = escalation_out(escalation, db) if escalation else None
    if detail and db is not None:
        msgs = (
            db.query(Message)
            .filter_by(conversation_id=c.id)
            .order_by(Message.created_at.asc())
            .all()
        )
        out["messages"] = [message_out(m) for m in msgs]
        if c.focus_vehicle_id:
            v = db.query(Vehicle).filter_by(id=c.focus_vehicle_id).one_or_none()
            out["focus_vehicle"] = vehicle_out(v) if v else None
    return out


def appointment_out(a: Appointment, db: Session | None = None) -> dict:
    out = {
        "id": a.id,
        "lead_id": a.lead_id,
        "vehicle_id": a.vehicle_id,
        "assigned_user_id": a.assigned_user_id,
        "starts_at": iso(a.starts_at),
        "duration_min": a.duration_min,
        "status": a.status,
        "booked_by": a.booked_by,
        "conversation_id": a.conversation_id,
        "created_at": iso(a.created_at),
    }
    if db is not None:
        lead = db.query(Lead).filter_by(id=a.lead_id).one_or_none()
        out["lead"] = lead_out(lead, db) if lead else None
        vehicle = (
            db.query(Vehicle).filter_by(id=a.vehicle_id).one_or_none() if a.vehicle_id else None
        )
        out["vehicle"] = vehicle_out(vehicle) if vehicle else None
        out["assigned_to"] = user_out(
            db.query(User).filter_by(id=a.assigned_user_id).one_or_none()
            if a.assigned_user_id else None
        )
        out["outreach"] = [
            outreach_out(o)
            for o in db.query(Outreach).filter_by(appointment_id=a.id)
            .order_by(Outreach.created_at.desc()).all()
        ]
    return out


def outreach_out(o: Outreach) -> dict:
    return {
        "id": o.id,
        "appointment_id": o.appointment_id,
        "lead_id": o.lead_id,
        "sent_by_user_id": o.sent_by_user_id,
        "channel": o.channel,
        "to_address": o.to_address,
        "subject": o.subject,
        "body": o.body,
        "provider": o.provider,
        "provider_message_id": o.provider_message_id,
        "provider_thread_id": o.provider_thread_id,
        # 'sent' means the provider accepted it. Nothing more -- there is no
        # delivery callback anywhere in this system (§0).
        "status": o.status,
        "delivered_externally": o.provider not in {"", "outbox", "console"},
        "error": o.error,
        "sent_at": iso(o.sent_at),
        # Clicks on the link we sent, not applications completed -- what
        # happens on the dealer's own form never comes back to us.
        "kind": o.kind,
        "trackable": o.click_token is not None,
        "opened": o.click_count > 0,
        "click_count": o.click_count,
        "first_clicked_at": iso(o.first_clicked_at),
        "created_at": iso(o.created_at),
    }


def escalation_out(e: Escalation, db: Session | None = None) -> dict:
    out = {
        "id": e.id,
        "conversation_id": e.conversation_id,
        "handoff_rule_id": e.handoff_rule_id,
        "reason": e.reason,
        "claimed_by_user_id": e.claimed_by_user_id,
        "claimed_at": iso(e.claimed_at),
        "created_at": iso(e.created_at),
    }
    if db is not None and e.handoff_rule_id:
        rule = db.query(HandoffRule).filter_by(id=e.handoff_rule_id).one_or_none()
        out["rule"] = handoff_rule_out(rule) if rule else None
    # The "Needs a person" table names the buyer, the car and the channel in
    # one row -- a rep triages on those, not on a conversation id. All three
    # hang off the conversation, so the row costs one extra join, not a
    # denormalised column.
    if db is not None:
        convo = (
            db.query(Conversation).filter_by(id=e.conversation_id).one_or_none()
            if e.conversation_id else None
        )
        out["channel"] = convo.channel if convo else None
        lead = (
            db.query(Lead).filter_by(id=convo.lead_id).one_or_none()
            if convo and convo.lead_id else None
        )
        out["lead"] = lead_out(lead, db) if lead else None
        vehicle = (
            db.query(Vehicle).filter_by(id=convo.focus_vehicle_id).one_or_none()
            if convo and convo.focus_vehicle_id else None
        )
        out["vehicle"] = vehicle_out(vehicle) if vehicle else None
    return out


def handoff_rule_out(r: HandoffRule) -> dict:
    return {
        "id": r.id,
        "key": r.key,
        "label": r.label,
        "description": r.description,
        "enabled": r.enabled,
        "threshold_value": r.threshold_value,
        "threshold_unit": r.threshold_unit,
        "route_target": r.route_target,
        "notify": r.notify,
        "fired_count": r.fired_count,
        "updated_at": iso(r.updated_at),
    }


def knowledge_out(k: KnowledgeEntry) -> dict:
    return {
        "id": k.id,
        "topic": k.topic,
        "answer": k.answer,
        "use_count": k.use_count,
        "updated_at": iso(k.updated_at),
    }


def rail_out(r: Rail) -> dict:
    return {
        "id": r.id,
        "kind": r.kind,
        "stage": r.stage,
        "label": r.label,
        "message_text": r.message_text,
        "requires_vehicle": r.requires_vehicle,
        "knowledge_entry_id": r.knowledge_entry_id,
        "advances_to": r.advances_to,
        "sort_order": r.sort_order,
        "enabled": r.enabled,
    }


def settings_out(s: AssistantSettings) -> dict:
    return {
        "id": s.id,
        "version": s.version,
        "status": s.status,
        "tone": s.tone,
        "push_level": s.push_level,
        "price_mode": s.price_mode,
        "discount_pct": s.discount_pct,
        "financing_mode": s.financing_mode,
        "after_hours_mode": s.after_hours_mode,
        "greeting": s.greeting,
        "booking_slot_length": s.booking_slot_length,
        "credit_application_url": s.credit_application_url,
        "published_by": s.published_by,
        "published_at": iso(s.published_at),
        "updated_at": iso(s.updated_at),
    }


def ingest_run_out(r: IngestRun) -> dict:
    return {
        "id": r.id,
        "source_url": r.source_url,
        "method": r.method,
        "status": r.status,
        "listings_found": r.listings_found,
        "created_count": r.created_count,
        "updated_count": r.updated_count,
        "removed_count": r.removed_count,
        "diff": loads(r.diff_json, {}),
        "errors": loads(r.errors_json, []),
        "started_at": iso(r.started_at),
        "finished_at": iso(r.finished_at),
    }


def booking_card(slots: list[str], slot_minutes: int) -> dict:
    """Group check_availability's flat slot list into day -> times.

    Deliberately built here from the tool *result* rather than asked of the
    model, for the same reason rail chips are: a slot the model composed is a
    second place it could offer a time the calendar does not have. This
    reshapes what check_availability already returned and invents nothing --
    if a time is not in that list it cannot appear on the card.
    """
    from app.agent.tools import clock_label

    days: dict[str, dict] = {}
    for iso in slots:
        try:
            when = datetime.fromisoformat(iso)
        except ValueError:
            continue
        day = days.setdefault(
            when.date().isoformat(),
            {
                "date": when.date().isoformat(),
                "label": f"{when:%A}",
                "short": f"{when:%a}",
                "sub": f"{when:%b} {when.day}",
                "slots": [],
            },
        )
        day["slots"].append({"starts_at": iso, "label": clock_label(when)})
    return {"slot_minutes": slot_minutes, "days": list(days.values())}
