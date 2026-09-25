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

from app import outreach_status
from app.agent.phrasing import cased
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
    LeadAddress,
    Message,
    Outreach,
    Rail,
    User,
    Vehicle,
)


def iso(value: datetime | None) -> str | None:
    """A **wall-clock** time, sent without a zone because it does not have one.

    Appointment times are dealership-local by convention -- `check_availability`
    builds them straight out of `hours_json` in that frame -- so 10:00 means ten
    in the morning at the showroom and the digits are the answer. A browser
    parses a bare date-time as its own local time, which renders those digits
    back unchanged, which is what is wanted.

    Everything else on the wire is a real instant. Use `stamp` for those.
    """
    return value.isoformat() if value else None


def stamp(value: datetime | None) -> str | None:
    """A real **instant**, sent as UTC and marked as one.

    `utcnow()` is naive UTC, and `isoformat()` on it produces
    `2026-08-30T18:17:00` -- a string with no zone. ECMAScript parses a bare
    date-time as *browser-local*, so every "5m ago" on the dashboard was
    computed against a clock shifted by the viewer's own offset: a reply that
    had just arrived read `5h ago` to somebody sitting at UTC+5, and read
    correctly on a machine set to UTC, which is why it survived so long. It hit
    every relative time on every page at once -- `relative`, `waited`, `time`,
    `dateTime` -- and nothing about it looks like a bug, because a plausible
    wrong number is exactly what it produces.

    One `Z` is the whole fix, and it belongs here rather than in the browser:
    the ambiguity is in the wire format, and a frontend that has to remember
    which of two kinds of timestamp it is holding will eventually forget.
    """
    return f"{value.isoformat()}Z" if value else None


def loads(raw: str, fallback):
    try:
        return json.loads(raw or "")
    except (ValueError, TypeError):
        return fallback


def user_out(u) -> dict | None:
    """A dealership `User` or an ops `OpsUser`, in one shape.

    `daily_cap` and `notify_channel` are about taking appointments on a
    showroom floor and an ops account has neither column. They are answered
    with a zero and a blank rather than omitted, so the frontend has one
    account shape and no branch -- a key that exists on some responses and not
    others is how a page ends up rendering `undefined`.
    """
    if u is None:
        return None
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "role": u.role,
        "avatar_initials": u.avatar_initials,
        "daily_cap": getattr(u, "daily_cap", 0),
        "notify_channel": getattr(u, "notify_channel", ""),
        "active": u.active,
        "out": getattr(u, "out", False),
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
        # Shown to a person, so `cased` — a dealer's export may be SHOUTING
        # and the row keeps exactly what they sent. See `phrasing.cased`.
        "make": cased(v.make),
        "model": cased(v.model),
        "trim": cased(v.trim),
        "title": f"{v.year} {cased(v.make)} {cased(v.model)}".strip(),
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
        "first_seen_at": stamp(v.first_seen_at),
        "last_seen_at": stamp(v.last_seen_at),
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
        "updated_at": stamp(c.updated_at),
    }


def lead_out(lead: Lead, db: Session | None = None, *, detail: bool = False) -> dict:
    out = {
        "id": lead.id,
        # Their name, or the next thing that identifies them: the address they
        # write from, then the number they call from.
        #
        # A placeholder was the only fallback, and on a list of email buyers it
        # produced a column of identical "Unnamed buyer" rows -- every one of
        # which a rep had to open to find out who it was. An address is not a
        # name and does not pretend to be one; it is simply the most
        # identifying thing on file, and one a person can act on. The
        # placeholder is what is left when there is nothing at all.
        "name": lead.name or lead.email or lead.phone or "Unnamed buyer",
        #: The name column itself, unsubstituted -- for anything that has to
        #: know whether they actually told us who they are.
        "has_name": bool(lead.name),
        "email": lead.email,
        "phone": lead.phone,
        "source": lead.source,
        "assigned_user_id": lead.assigned_user_id,
        # The one definition of "unclaimed" -- a lead with no owner
        # (`app/ownership.py`) -- served so the client reads it rather than
        # re-deriving it, which is how an anonymous thread (where
        # `lead` is null) once counted as unclaimed on the Conversations
        # page's chip while the Overview panel, which only ever sees leads,
        # never could.
        "unclaimed": lead.assigned_user_id is None,
        # No email means the product has no way to reach them (§18.5).
        "contact_risk": lead.contact_risk,
        "email_consent_at": stamp(lead.email_consent_at),
        "created_at": stamp(lead.created_at),
    }
    if db is not None:
        out["assigned_to"] = user_out(
            db.query(User).filter_by(id=lead.assigned_user_id).one_or_none()
            if lead.assigned_user_id else None
        )
        fields = (
            db.query(CapturedField).filter_by(lead_id=lead.id)
            .order_by(CapturedField.key.asc(), CapturedField.id.asc()).all()
        )
        out["captured_fields"] = [captured_out(f) for f in fields]
    if detail and db is not None:
        appts = (
            db.query(Appointment)
            .filter_by(lead_id=lead.id)
            .order_by(Appointment.starts_at.asc())
            .all()
        )
        out["appointments"] = [appointment_out(a, db) for a in appts]
        convos = (
            db.query(Conversation).filter_by(lead_id=lead.id)
            .order_by(Conversation.started_at.asc(), Conversation.id.asc()).all()
        )
        out["conversations"] = [conversation_out(c, db) for c in convos]
        reach = (
            db.query(Outreach)
            .filter_by(lead_id=lead.id)
            .order_by(Outreach.created_at.desc())
            .all()
        )
        out["outreach"] = outreach_many(db, reach)
        # Other addresses a rep has said are theirs. On the detail payload
        # only: a link is a fact about one buyer, and the list has no room to
        # say it. Without it the button that makes one has no visible effect,
        # which is how a rep ends up pressing it twice.
        out["linked_addresses"] = [
            {"id": row.id, "address": row.address, "created_at": stamp(row.created_at)}
            for row in db.query(LeadAddress)
            .filter_by(lead_id=lead.id)
            .order_by(LeadAddress.created_at.asc())
            .all()
        ]
    return out


def message_out(m: Message) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "tool_calls": loads(m.tool_calls_json, []),
        "via_rail_id": m.via_rail_id,
        "created_at": stamp(m.created_at),
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
        "started_at": stamp(c.started_at),
        "ended_at": stamp(c.ended_at),
        "summary": c.summary,
        "outcome": c.outcome,
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
        # Detail only: it costs four queries, and no list row shows it.
        from app.recap import conversation_recap

        out["recap"] = conversation_recap(db, c)
        # Where they were on the dealer's website while chatting, newest
        # first: the page is what a rep opens to see what the buyer saw.
        from app.models import ConversationPage

        out["pages"] = [
            {"url": p.url, "title": p.title, "vin": p.vin, "seen_at": stamp(p.seen_at)}
            for p in db.query(ConversationPage)
            .filter_by(conversation_id=c.id)
            .order_by(ConversationPage.seen_at.desc())
            .limit(5)
            .all()
        ]
        if c.focus_vehicle_id:
            v = db.query(Vehicle).filter_by(id=c.focus_vehicle_id).one_or_none()
            out["focus_vehicle"] = vehicle_out(v) if v else None
    return out


def appointment_out(a: Appointment, db: Session | None = None) -> dict:
    from app.appointment_scope import OFF_STATUSES

    out = {
        "id": a.id,
        "lead_id": a.lead_id,
        "vehicle_id": a.vehicle_id,
        "assigned_user_id": a.assigned_user_id,
        "starts_at": iso(a.starts_at),
        "duration_min": a.duration_min,
        "status": a.status,
        # Cancelled or a no-show -- the one definition
        # (`app.appointment_scope.OFF_STATUSES`), so the Calendar's list and
        # week views read the same flag rather than each keeping their own
        # allow/deny-list of statuses, which is how 'completed' ended up
        # treated as live on one view and unclassified on the other.
        "off": a.status in OFF_STATUSES,
        "booked_by": a.booked_by,
        "conversation_id": a.conversation_id,
        "created_at": stamp(a.created_at),
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
        out["outreach"] = outreach_many(
            db,
            db.query(Outreach).filter_by(appointment_id=a.id)
            .order_by(Outreach.created_at.desc()).all(),
        )
    return out


def outreach_out(o: Outreach, *, email: dict | None = None) -> dict:
    """One `outreach` row as the API serves it.

    `email` is the envelope summary (`email_envelopes.summary`): every To, Cc
    and Bcc, Reply-To, whether there is HTML, importance and the files. Passed
    in rather than looked up, because a list of rows must load its envelopes
    in one query (`outreach_many`) and a serializer that queried per row
    would make every list an N+1. Absent when not given, so every existing
    caller's shape is unchanged.
    """
    out = _outreach_fields(o)
    if email is not None:
        out["email"] = email
    return out


def outreach_many(db: Session, rows: list[Outreach]) -> list[dict]:
    """`outreach_out` for a list, with each email row's envelope batch-loaded.

    Three queries for the whole list whatever its length -- envelopes (and
    the receipts an inbound one hangs off), then their files. An SMS or a
    logged call has no envelope and gets no `email` key.
    """
    from app import email_envelopes

    envelopes = email_envelopes.for_outreach_many(db, rows)
    files = email_envelopes.attachments_of(db, [e.id for e in envelopes.values()])
    out = []
    for o in rows:
        if o.channel != "email":
            out.append(outreach_out(o))
            continue
        env = envelopes.get(o.id)
        out.append(outreach_out(
            o,
            email=email_envelopes.summary(
                env, files.get(env.id, []) if env else [], include_bcc=True,
            ),
        ))
    return out


def _outreach_fields(o: Outreach) -> dict:
    return {
        "id": o.id,
        "appointment_id": o.appointment_id,
        "lead_id": o.lead_id,
        "sent_by_user_id": o.sent_by_user_id,
        "channel": o.channel,
        # Which way it went. An inbound reply is the same shape as a send --
        # address, subject, body, provider id -- and shares the table, so
        # this is the only thing telling the two apart on the timeline.
        "direction": o.direction,
        "to_address": o.to_address,
        "subject": o.subject,
        "body": o.body,
        "provider": o.provider,
        "provider_message_id": o.provider_message_id,
        "provider_thread_id": o.provider_thread_id,
        # 'sent' means the provider accepted it. Nothing more -- there is no
        # delivery callback anywhere in this system (§0).
        "status": o.status,
        # One word for what actually happened -- 'received' | 'sent' |
        # 'sending' | 'not_sent', from `app/outreach_status.py`. Every reader
        # of `status` alone had its own idea of what counts as a failure: the
        # Calendar coloured only failed/bounced, the buyer-page reader only
        # failed, and a row still `queued` (in flight, or orphaned by a
        # crash) read as delivered to both -- while the mailbox's own tab
        # called the same row a red "Not sent" the instant it was queued.
        # `delivery` is the one word every one of those screens should read
        # instead (item 49).
        "delivery": outreach_status.delivery_of(o),
        "delivered_externally": o.provider not in {"", "outbox", "console"},
        "error": o.error,
        "sent_at": stamp(o.sent_at),
        # Clicks on the link we sent, not applications completed -- what
        # happens on the dealer's own form never comes back to us.
        "kind": o.kind,
        "trackable": o.click_token is not None,
        "opened": o.click_count > 0,
        "click_count": o.click_count,
        "first_clicked_at": stamp(o.first_clicked_at),
        "created_at": stamp(o.created_at),
    }


def escalation_out(e: Escalation, db: Session | None = None) -> dict:
    out = {
        "id": e.id,
        "conversation_id": e.conversation_id,
        "handoff_rule_id": e.handoff_rule_id,
        "reason": e.reason,
        "claimed_by_user_id": e.claimed_by_user_id,
        "claimed_at": stamp(e.claimed_at),
        "created_at": stamp(e.created_at),
    }
    if db is not None and e.handoff_rule_id:
        rule = db.query(HandoffRule).filter_by(id=e.handoff_rule_id).one_or_none()
        # No `fired_count` here: the Needs a person queue this feeds never
        # displays it, and the stale seeded counter (§Item 10/28) is exactly
        # what put a "Fired 12 times" figure next to the one row that rule
        # ever actually raised. The Liner setup page reads the real count
        # from `list_handoff_rules`, computed once for the whole list.
        out["rule"] = (
            {k: v for k, v in handoff_rule_out(rule, 0).items() if k != "fired_count"}
            if rule else None
        )
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


def handoff_rule_out(r: HandoffRule, fired: int) -> dict:
    """`fired` is the true count -- `app.escalations.fired_counts(db)`, the
    count of escalation rows carrying this rule's id -- not the stored
    `HandoffRule.fired_count` column, which only one writer ever moved and
    which the demo seed started 31 fires ahead of any row that backed it.
    """
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
        "fired_count": fired,
        "updated_at": stamp(r.updated_at),
    }


def knowledge_out(k: KnowledgeEntry) -> dict:
    return {
        "id": k.id,
        "topic": k.topic,
        "answer": k.answer,
        "use_count": k.use_count,
        "updated_at": stamp(k.updated_at),
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
        "published_at": stamp(s.published_at),
        "updated_at": stamp(s.updated_at),
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
        "started_at": stamp(r.started_at),
        "finished_at": stamp(r.finished_at),
    }


def booking_card(
    slots: list[str], slot_minutes: int, known: dict | None = None
) -> dict:
    """Group check_availability's flat slot list into day -> times.

    Deliberately built here from the tool *result* rather than asked of the
    model, for the same reason rail chips are: a slot the model composed is a
    second place it could offer a time the calendar does not have. This
    reshapes what check_availability already returned and invents nothing --
    if a time is not in that list it cannot appear on the card.

    `known` is whatever is already on the buyer's lead row. The card fills its
    boxes from it and asks for nothing it already has: a buyer who gave their
    name and number two turns ago and is then asked for both again reads that
    as not having been listened to, which is the same rule the reply text
    follows about not listing the times back.
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
    return {
        "slot_minutes": slot_minutes,
        "days": list(days.values()),
        "known": {
            "name": (known or {}).get("name") or "",
            "email": (known or {}).get("email") or "",
            "phone": (known or {}).get("phone") or "",
        },
    }
