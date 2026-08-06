from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.db import get_db
from app.models import Appointment, Conversation, Escalation, Lead, User, Vehicle
from app.schemas.serialize import iso, lead_out, vehicle_out

router = APIRouter(prefix="/leads", tags=["leads"])

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

        touches = [c.started_at for c in mine] + [a.created_at for a in my_appts]
        out[lead.id] = {
            "stage": stage,
            "flagged": any(c.id in open_escalations for c in mine),
            "vehicle_of_interest": vehicle_out(vehicle) if vehicle else None,
            "appointment_count": len(live),
            "unconfirmed_count": len([a for a in live if a.status == "booked"]),
            "last_touch_at": iso(max(touches)) if touches else iso(lead.created_at),
            "conversation_id": mine[0].id if mine else None,
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
    lead = db.query(Lead).filter_by(id=lead_id).one_or_none()
    if lead is None:
        raise HTTPException(404, "Lead not found")
    return lead_out(lead, db, detail=True)
