from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.db import get_db
from app.models import Lead, User
from app.schemas.serialize import lead_out

router = APIRouter(prefix="/leads", tags=["leads"])


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
    return {"leads": [lead_out(lead, db) for lead in rows]}


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
