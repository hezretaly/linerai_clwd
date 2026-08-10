from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import matching, timeline
from app.recap import conversation_recap
from app.api.deps import current_user
from app.db import get_db
from app.models import (
    Appointment,
    CapturedField,
    Conversation,
    Escalation,
    Lead,
    Message,
    Outreach,
    User,
    Vehicle,
)
from app.schemas.serialize import conversation_out, iso, lead_out, vehicle_out

router = APIRouter(prefix="/leads", tags=["leads"])


def _get(db: Session, lead_id: str) -> Lead:
    lead = db.query(Lead).filter_by(id=lead_id).one_or_none()
    if lead is None:
        raise HTTPException(404, "Lead not found")
    return lead

# A lead has no stage column -- the state lives on its conversation and its
# appointments. This derives one rather than adding a field that two writers
# would then have to keep in step.
STAGE_RANK = {
    "opening": 0, "browsing": 1, "vehicle_focus": 2, "objection": 2,
    "qualifying": 3, "slot_offered": 3, "contact_capture": 3, "booked": 4,
}


def lead_summaries(db: Session, leads: list[Lead]) -> dict[str, dict]:
    """Per-lead stage, vehicle of interest and last activity, in three queries."""
    ids = [lead.id for lead in leads]
    if not ids:
        return {}

    convos = db.query(Conversation).filter(Conversation.lead_id.in_(ids)).all()
    appts = db.query(Appointment).filter(Appointment.lead_id.in_(ids)).all()
    convo_ids = [c.id for c in convos]
    last_message = dict(
        db.query(Message.conversation_id, func.max(Message.created_at))
        .filter(Message.conversation_id.in_(convo_ids))
        .group_by(Message.conversation_id)
        .all()
    ) if convo_ids else {}
    open_escalations = {
        row.conversation_id
        for row in db.query(Escalation).filter(Escalation.claimed_at.is_(None)).all()
    }

    vehicle_ids = {c.focus_vehicle_id for c in convos if c.focus_vehicle_id}
    vehicle_ids |= {a.vehicle_id for a in appts if a.vehicle_id}
    vehicles = {
        v.id: v
        for v in (
            db.query(Vehicle).filter(Vehicle.id.in_(vehicle_ids)).all() if vehicle_ids else []
        )
    }

    # An imported lead has no conversation, so the car it asked about lives on a
    # captured field. Matched back to a real row here rather than shown as free
    # text, so the table never names a car that is not on the lot.
    wanted = {
        row.lead_id: row.value
        for row in db.query(CapturedField)
        .filter(CapturedField.lead_id.in_(ids), CapturedField.key == "vehicle_interest")
        .all()
    }
    by_label: dict[str, Vehicle] = {}
    if wanted:
        for v in db.query(Vehicle).filter(Vehicle.status == "available").all():
            by_label[f"{v.year} {v.make} {v.model}".lower()] = v

    out: dict[str, dict] = {}
    for lead in leads:
        mine = [c for c in convos if c.lead_id == lead.id]
        my_appts = [a for a in appts if a.lead_id == lead.id]
        live = [a for a in my_appts if a.status in {"booked", "confirmed"}]

        best = max((STAGE_RANK.get(c.stage, 0) for c in mine), default=0)
        if live:
            stage = "appointment"
        elif best >= 3:
            stage = "qualified"
        elif best >= 1:
            stage = "qualifying"
        else:
            stage = "new"

        vehicle = next(
            (
                vehicles.get(vid)
                for vid in (
                    [a.vehicle_id for a in my_appts] + [c.focus_vehicle_id for c in mine]
                )
                if vid and vehicles.get(vid)
            ),
            None,
        )
        if vehicle is None and lead.id in wanted:
            label = " ".join(wanted[lead.id].split()[:3]).lower()
            vehicle = by_label.get(label)

        # The last thing that actually happened, not when the thread opened. The
        # conversations list is ordered by this, and a chat someone started this
        # morning and abandoned should not outrank one being typed in now.
        touches = [last_message.get(c.id) or c.started_at for c in mine]
        touches += [a.created_at for a in my_appts]

        still_open = [c for c in mine if c.status != "closed"]
        out[lead.id] = {
            "stage": stage,
            "flagged": any(c.id in open_escalations for c in mine),
            "vehicle_of_interest": vehicle_out(vehicle) if vehicle else None,
            "appointment_count": len(live),
            "unconfirmed_count": len([a for a in live if a.status == "booked"]),
            "last_touch_at": iso(max(touches)) if touches else iso(lead.created_at),
            "conversation_id": mine[0].id if mine else None,
            # What the conversations list needs to draw a lead row without a
            # query per row: how many threads, which channels, and whether any
            # of it is still running.
            "conversation_count": len(mine),
            "channels": sorted({c.channel for c in mine}),
            "open": bool(still_open),
            # Declined only while it stays declined. A buyer who said no in
            # March and is chatting again today is not a closed lead.
            "declined": (
                not still_open and any(c.outcome == "declined" for c in mine)
            ),
        }
    return out


@router.get("")
def list_leads(
    source: str | None = Query(None),
    risk: bool | None = Query(None, description="Only leads with no way to reach them"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    query = db.query(Lead)
    if source:
        query = query.filter(Lead.source == source)
    rows = query.order_by(Lead.created_at.desc()).all()
    if risk is True:
        # contact_risk inverted when SMS came out: no email is what makes a
        # lead unreachable now, not a missing phone number (§18.5).
        rows = [lead for lead in rows if lead.contact_risk]

    # The table needs a stage, a vehicle and a last-touch per row. Computing
    # those from a detail call per lead would be N+1; they are gathered here in
    # three queries and folded onto each row.
    summaries = lead_summaries(db, rows)
    leads = []
    for lead in rows:
        out = lead_out(lead, db)
        out.update(summaries.get(lead.id, {}))
        leads.append(out)
    return {"leads": leads}


@router.get("/{lead_id}")
def get_lead(
    lead_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    lead = _get(db, lead_id)
    out = lead_out(lead, db, detail=True)
    out.update(lead_summaries(db, [lead]).get(lead.id, {}))
    return out


@router.get("/{lead_id}/timeline")
def get_timeline(
    lead_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Every channel this buyer used, in one ordered list.

    `channels` is counted from the entries rather than declared, so the filter
    strip can only offer a channel the buyer actually used -- and can never
    offer one this system cannot do at all.
    """
    lead = _get(db, lead_id)
    entries = timeline.lead_timeline(db, lead)
    convos = (
        db.query(Conversation)
        .filter_by(lead_id=lead.id)
        .order_by(Conversation.started_at.asc())
        .all()
    )
    return {
        "lead": lead_out(lead, db, detail=True),
        "entries": entries,
        "channels": timeline.channel_counts(entries),
        "conversations": [conversation_out(c, db) for c in convos],
        # One recap, from the newest thread, rather than one per conversation:
        # composing it costs a handful of queries each and the rail shows one.
        "recap": conversation_recap(db, convos[-1]) if convos else "",
        # Where a reply goes. A rep typing into this page has to be told which
        # thread they are answering on -- the alternative is a message landing
        # on a conversation the buyer closed last week.
        "reply_to": _reply_target(convos),
    }


def _reply_target(convos: list[Conversation]) -> str | None:
    """The most recently active thread that is still open, or nothing.

    Nothing is a real answer: Liner cannot start a chat with someone who is not
    on the page. When every thread is closed the page offers the outreach
    composers instead, which is the only way a dealer can actually reach out.
    """
    live = [c for c in convos if c.status != "closed"]
    if not live:
        return None
    return max(live, key=lambda c: c.started_at).id


@router.get("/{lead_id}/duplicates")
def get_duplicates(
    lead_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Other leads that look like the same person, and why.

    Detection only -- nothing here merges anything. The reason is returned
    because a rep deciding whether two rows are one person needs to know
    whether we saw the same address or only the same phone; a shared household
    number is a real thing, and "trust us" is not something they can check.

    A name is never a reason. Two Dave Joneses are two people.
    """
    lead = _get(db, lead_id)
    found = matching.candidates_for(db, lead.email, lead.phone, exclude_id=lead.id)
    summaries = lead_summaries(db, [other for other, _ in found])
    out = []
    for other, why in found:
        row = lead_out(other, db)
        row.update(summaries.get(other.id, {}))
        out.append({"reason": why, "lead": row})
    return {"duplicates": out}
