"""ADF/XML lead import, manual lead entry, and lead-level outreach.

Everything before this point assumed Liner created the lead itself, by talking
to someone. Dealers do not work that way: most of a day's leads arrive as ADF
documents from marketplaces they already pay for. This module takes those in and
gives the dashboard something real to reach out to.

Two honest limits, surfaced in the UI rather than papered over:

* **Nothing here is scheduled.** There is no job runner in this system, so a
  "reminder" is a draft a rep reviews and sends. It is not a drip campaign, and
  the page says so.
* **Delivery is still the outbox.** An imported address is by definition not in
  ``EMAIL_ALLOWLIST``, so with ``DEMO_MODE`` on the send is refused and recorded
  as refused. That is the guard working, not a bug.

Import is preview-then-commit, the same shape as inventory ingest: parsing never
writes a row. Unlike inventory it holds the preview client-side instead of in an
``ingest_runs`` row -- that table's diff is vehicle-shaped, and a lead drop is
small enough that round-tripping the reviewed rows costs nothing.
"""

from __future__ import annotations

import secrets

from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_dealership
from app.integrations.base import NotConfigured
from app.config import settings
from app.db import get_db, utcnow
from app import outreach_send
from app.events import emit
from app.matching import match_lead
from app.ingest.adf import AdfError, parse_adf
from app.integrations.registry import get_email_sender
from app.models import Appointment, CapturedField, Dealership, Lead, Outreach, User, Vehicle
from app.schemas.serialize import lead_out, outreach_out, vehicle_out

router = APIRouter(prefix="/leads", tags=["leads"])

SOURCES = {"chat", "phone", "website", "adf"}


class ProspectIn(BaseModel):
    """The reviewed form of one ADF prospect -- also what the manual form posts.

    Manual entry deliberately shares this shape. A lead typed by a rep and a
    lead parsed from a marketplace end up as the same row; only ``source``
    differs, so there is one create path to keep correct.
    """

    name: str = ""
    email: str = ""
    phone: str = ""
    provider: str = ""
    requested_at: str = ""
    comments: str = ""
    timeframe: str = ""
    vehicle_year: int | None = None
    vehicle_make: str = ""
    vehicle_model: str = ""
    vehicle_trim: str = ""
    vehicle_vin: str = ""
    vehicle_stock: str = ""
    source: str = "adf"

    @property
    def vehicle_label(self) -> str:
        parts = [str(self.vehicle_year or ""), self.vehicle_make, self.vehicle_model,
                 self.vehicle_trim]
        return " ".join(p for p in parts if p).strip()


def _match_vehicle(db: Session, prospect) -> Vehicle | None:
    """Is the car they asked about actually on the lot? VIN, then year/make/model."""
    vin = (prospect.vehicle_vin or "").upper().strip()
    if vin:
        hit = db.query(Vehicle).filter(Vehicle.vin == vin).one_or_none()
        if hit is not None:
            return hit
    if prospect.vehicle_make and prospect.vehicle_model:
        query = db.query(Vehicle).filter(
            Vehicle.status == "available",
            Vehicle.make.ilike(prospect.vehicle_make),
            Vehicle.model.ilike(prospect.vehicle_model),
        )
        if prospect.vehicle_year:
            query = query.filter(Vehicle.year == prospect.vehicle_year)
        return query.first()
    return None


SAMPLE = Path(__file__).resolve().parent.parent / "ingest" / "fixtures" / "sample_leads.adf.xml"


@router.get("/import/adf/sample")
def sample_adf(user: User = Depends(current_user)) -> Response:
    """A real ADF document to try the importer with, so the page is not a
    dead upload box for anyone who does not have a marketplace feed to hand."""
    return Response(
        SAMPLE.read_text(),
        media_type="application/xml",
        headers={"Content-Disposition": 'attachment; filename="sample_leads.adf.xml"'},
    )


@router.post("/import/adf/preview")
async def preview_adf(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Parse and match. Writes nothing -- the dealer reviews, then commits."""
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    try:
        prospects, errors = parse_adf(raw)
    except AdfError as exc:
        raise HTTPException(400, str(exc)) from None

    rows = []
    for prospect in prospects:
        existing = match_lead(db, prospect.email, prospect.phone)
        vehicle = _match_vehicle(db, prospect)
        row = prospect.as_dict()
        row["source"] = "adf"
        row["existing_lead"] = lead_out(existing, db) if existing else None
        row["in_stock"] = vehicle_out(vehicle) if vehicle else None
        if prospect.vehicle_label and vehicle is None:
            row["warnings"] = [
                *row["warnings"],
                f"{prospect.vehicle_label} is not in inventory -- nothing to show them yet.",
            ]
        rows.append(row)

    return {
        "filename": file.filename or "upload.xml",
        "prospects": rows,
        "errors": errors,
        "found": len(prospects) + len(errors),
    }


def _write_fields(db: Session, lead: Lead, prospect: ProspectIn, provenance: str) -> None:
    """What the document said, recorded with provenance 'adf'.

    Not 'typed': the buyer typed it into somebody else's form and we are taking
    a third party's word for it. Not 'inferred' either -- we did not guess. The
    agent cannot claim this value; ``save_captured_fields`` only accepts the
    four conversational ones, so a field marked 'adf' can only have come from
    a document a dealer uploaded.

    A lead a rep keys in by hand gets 'typed' instead: the buyer did say it, to
    a person, and there is no document to point at.
    """
    values = {
        "vehicle_interest": prospect.vehicle_label,
        "timeframe": prospect.timeframe,
        "comments": prospect.comments[:500],
        "lead_source": prospect.provider,
    }
    for key, value in values.items():
        if not value:
            continue
        row = db.query(CapturedField).filter_by(lead_id=lead.id, key=key).one_or_none()
        if row is None:
            db.add(CapturedField(lead_id=lead.id, key=key, value=value, provenance=provenance))
        elif row.provenance == "adf":
            # A value the buyer said in conversation outranks a marketplace form,
            # so only a previous feed value is overwritten by a re-drop.
            row.value = value
            row.provenance = provenance


class CommitBody(BaseModel):
    prospects: list[ProspectIn]
    assign_to_user_id: str | None = None


@router.post("/import/adf")
def commit_adf(
    body: CommitBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Create or merge the reviewed prospects. Idempotent on email/phone."""
    if not body.prospects:
        raise HTTPException(400, "Nothing selected to import.")

    assignee = None
    if body.assign_to_user_id:
        assignee = db.query(User).filter_by(id=body.assign_to_user_id, active=True).one_or_none()
        if assignee is None:
            raise HTTPException(404, "User not found")

    created, merged = [], []
    for prospect in body.prospects:
        if not prospect.email and not prospect.phone:
            raise HTTPException(400, f"{prospect.name or 'A prospect'} has no way to be contacted.")
        source = prospect.source if prospect.source in SOURCES else "adf"

        lead = match_lead(db, prospect.email, prospect.phone)
        if lead is None:
            lead = Lead(
                name=prospect.name.strip(),
                email=prospect.email.lower().strip(),
                phone=prospect.phone.strip(),
                source=source,
                assigned_user_id=assignee.id if assignee else None,
            )
            db.add(lead)
            db.flush()
            created.append(lead)
        else:
            # Fill gaps only. A rep may have corrected this row by hand and a
            # re-drop of the same feed must not undo that.
            lead.name = lead.name or prospect.name.strip()
            lead.email = lead.email or prospect.email.lower().strip()
            lead.phone = lead.phone or prospect.phone.strip()
            if assignee and not lead.assigned_user_id:
                lead.assigned_user_id = assignee.id
            merged.append(lead)
        _write_fields(db, lead, prospect, "adf" if source == "adf" else "typed")

    db.commit()

    emit(db, "lead.imported", {
        "created": len(created), "merged": len(merged), "by": user.id,
        "lead_ids": [lead.id for lead in created + merged],
    })
    return {
        "created": [lead_out(lead, db) for lead in created],
        "merged": [lead_out(lead, db) for lead in merged],
    }


@router.post("", status_code=201)
def create_lead(
    prospect: ProspectIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Manual entry. One prospect through the same path as an import."""
    if not prospect.name.strip():
        raise HTTPException(400, "A lead needs a name.")
    if not prospect.email.strip() and not prospect.phone.strip():
        raise HTTPException(
            400, "Enter an email or a phone number -- otherwise there is no way to reach them."
        )
    if prospect.source == "adf":
        # 'adf' means a document exists. A typed lead has to say where it came
        # from, so the source filter on the leads table stays truthful.
        raise HTTPException(400, "Choose where this lead came from: phone, website or chat.")
    body = CommitBody(prospects=[prospect])
    result = commit_adf(body, db=db, user=user)
    rows = result["created"] or result["merged"]
    return {"lead": rows[0], "merged": not result["created"]}


# --- Lead-level outreach -----------------------------------------------------
# `outreach.appointment_id` was already nullable, so a lead with no appointment
# gets a real outreach row with no schema change.

def _upcoming(db: Session, lead: Lead) -> Appointment | None:
    return (
        db.query(Appointment)
        .filter(
            Appointment.lead_id == lead.id,
            Appointment.status.in_(("booked", "confirmed")),
            Appointment.starts_at >= utcnow() - timedelta(hours=1),
        )
        .order_by(Appointment.starts_at.asc())
        .first()
    )


def _credit_draft(db: Session, lead: Lead, dealership: Dealership, sender: User) -> dict:
    """An invitation to start the dealer's own finance application.

    The link is the whole point, so with no link configured this refuses rather
    than mailing a buyer an invitation to apply nowhere. Nothing here quotes a
    rate, a term or an approval -- none of that exists in this system, and a
    number in a finance email is one a buyer holds you to.
    """
    from app.api.settings import live_settings

    url = (live_settings(db).credit_application_url or "").strip()
    if not url:
        raise NotConfigured(
            integration="credit_application",
            missing=["Credit application URL"],
            detail=(
                "No finance application link is set, so there is nothing to send. Add "
                "the dealership's own application URL on the Liner setup page."
            ),
        )

    first_name = (lead.name or "there").split()[0] if lead.name else "there"
    return {
        "kind": "credit_application",
        "to": lead.email,
        "subject": f"Finance application -- {dealership.name}",
        "body": (
            f"Hi {first_name},\n\n"
            f"Here is our finance application, which you can fill in before you come "
            f"in so we are not doing paperwork while you are here:\n\n{url}\n\n"
            f"It takes a few minutes. Nothing is decided by filling it in -- one of "
            f"our team goes through the numbers with you in person.\n\n"
            f"Any questions, call {dealership.phone}.\n\n"
            f"Best,\n{sender.name}\n{dealership.name}"
        ),
        "appointment_id": None,
        "note": "",
    }


def _lead_draft(
    db: Session, lead: Lead, dealership: Dealership, sender: User
) -> dict:
    """Two drafts, picked by what is actually true of this lead.

    A lead with a booked visit gets a reminder naming the slot; a lead with none
    gets a first touch naming the car they asked about -- and only if that car is
    genuinely in stock. Neither invents a price, a discount or a person on shift.
    """
    first_name = (lead.name or "there").split()[0] if lead.name else "there"
    fields = {f.key: f.value for f in db.query(CapturedField).filter_by(lead_id=lead.id).all()}
    appointment = _upcoming(db, lead)

    if appointment is not None:
        when = appointment.starts_at
        hour = when.hour % 12 or 12
        ampm = "AM" if when.hour < 12 else "PM"
        slot = f"{when.strftime('%A, %B %-d')} at {hour}:{when.minute:02d} {ampm}"
        vehicle = (
            db.query(Vehicle).filter_by(id=appointment.vehicle_id).one_or_none()
            if appointment.vehicle_id else None
        )
        car = f"the {vehicle.year} {vehicle.make} {vehicle.model}" if vehicle else "your visit"
        return {
            "kind": "reminder",
            "to": lead.email,
            "subject": f"Reminder: {slot} at {dealership.name}",
            "body": (
                f"Hi {first_name},\n\n"
                f"Just a reminder that you're booked in for {slot} to see {car}.\n\n"
                f"We're at {dealership.address}. Reply here or call {dealership.phone} "
                f"if you need a different time.\n\n"
                f"See you then,\n{sender.name}\n{dealership.name}"
            ),
            "appointment_id": appointment.id,
        }

    wanted = fields.get("vehicle_interest", "")
    match = None
    if wanted:
        parts = wanted.split()
        year = int(parts[0]) if parts and parts[0].isdigit() else None
        make, model = (parts[1], parts[2]) if len(parts) > 2 else ("", "")
        if make and model:
            query = db.query(Vehicle).filter(
                Vehicle.status == "available",
                Vehicle.make.ilike(make),
                Vehicle.model.ilike(model),
            )
            if year:
                query = query.filter(Vehicle.year == year)
            match = query.first()

    if match is not None and match.rule_discuss:
        line = (
            f"You asked about the {match.year} {match.make} {match.model}"
            f"{' ' + match.trim if match.trim else ''} -- it's still here."
        )
        ask = "Would you like to come and drive it? Tell me a day that works and I'll book it."
    elif wanted:
        # Deliberately does not claim the car is available. It isn't on the lot.
        line = f"You asked about a {wanted}."
        ask = (
            "That exact one isn't on the lot right now, but we get cars in every week -- "
            "tell me what you're after and I'll watch for it."
        )
    else:
        line = "Thanks for getting in touch."
        ask = "Tell me what you're looking for and I'll see what we have."

    source = f" via {fields['lead_source']}" if fields.get("lead_source") else ""
    return {
        "kind": "follow_up",
        "to": lead.email,
        "subject": f"About your enquiry at {dealership.name}",
        "body": (
            f"Hi {first_name},\n\n"
            f"{line} {ask}\n\n"
            f"We're at {dealership.address}, or call {dealership.phone}.\n\n"
            f"Best,\n{sender.name}\n{dealership.name}"
        ),
        "appointment_id": None,
        "note": f"Enquiry received{source}." if source else "",
    }


def _get_lead(db: Session, lead_id: str) -> Lead:
    lead = db.query(Lead).filter_by(id=lead_id).one_or_none()
    if lead is None:
        raise HTTPException(404, "Lead not found")
    return lead


@router.get("/{lead_id}/outreach")
def lead_outreach(
    lead_id: str,
    draft: int = Query(0),
    kind: str = Query("followup"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    dealership: Dealership = Depends(get_dealership),
) -> dict:
    lead = _get_lead(db, lead_id)
    if draft:
        if kind == "credit_application":
            return _credit_draft(db, lead, dealership, user)
        return _lead_draft(db, lead, dealership, user)
    rows = (
        db.query(Outreach)
        .filter_by(lead_id=lead.id)
        .order_by(Outreach.created_at.desc())
        .all()
    )
    return {"outreach": [outreach_out(o) for o in rows]}


class SendBody(BaseModel):
    subject: str
    body: str
    appointment_id: str | None = None
    kind: str = "followup"


def _track_links(request: Request, db: Session, record: Outreach) -> None:
    """Rewrite the dealer's application URL to a hop we can count.

    Done at send rather than at draft because the token belongs to the row, and
    the row does not exist until now. The base comes from the request, so the
    link matches the host the dealer is actually using -- hardcoding one is how
    a staging box mails production links.

    A rep who deleted the link from the draft gets no token, and that is the
    honest outcome: there is nothing in that email to click.
    """
    from app.api.settings import live_settings

    if record.kind != "credit_application":
        return
    target = (live_settings(db).credit_application_url or "").strip()
    if not target or target not in record.body:
        return

    token = secrets.token_urlsafe(16)
    base = (settings.public_base_url or str(request.base_url)).rstrip("/")
    record.click_token = token
    record.body = record.body.replace(target, f"{base}/r/{token}")


@router.post("/{lead_id}/outreach")
def send_lead_outreach(
    lead_id: str,
    payload: SendBody,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    lead = _get_lead(db, lead_id)
    if not lead.email:
        raise HTTPException(
            409,
            "No email on file for this lead, so there is nothing to send to. A rep has to "
            "call them.",
        )

    sender = get_email_sender()
    record = Outreach(
        appointment_id=payload.appointment_id, lead_id=lead.id, sent_by_user_id=user.id,
        channel="email", kind=payload.kind, to_address=lead.email,
        subject=payload.subject, body=payload.body,
        provider=sender.name, status="queued",
        reply_token=outreach_send.mint_reply_token(db),
    )
    db.add(record)
    db.flush()
    _track_links(request, db, record)
    db.commit()

    # An imported address is by definition not one we allow-listed. Refusing is
    # the point -- a rehearsal must not mail a real prospect. One guard, shared
    # with the appointment send; see app/outreach_send.py.
    blocked = outreach_send.blocked_reason(sender, lead.email)
    if blocked:
        record.status = "failed"
        record.error = blocked
        db.commit()
        return outreach_out(record)

    try:
        # record.body, not payload.body: the tracked link is the one that goes.
        result = sender.send(
            lead.email, payload.subject, record.body,
            reply_to=outreach_send.reply_to_address(record.reply_token),
        )
        record.provider_message_id = result.message_id
        record.provider_thread_id = result.thread_id
        record.status = result.status
        record.error = result.detail if result.status != "sent" else ""
        record.sent_at = utcnow()
    except Exception as exc:
        record.status = "failed"
        record.error = str(exc)
        db.commit()
        return outreach_out(record)

    db.commit()
    emit(db, "outreach.sent", {
        "outreach_id": record.id, "appointment_id": payload.appointment_id, "lead_id": lead.id,
        "to": lead.email, "provider": record.provider,
        "delivered_externally": sender.delivers,
        "conversation_id": None,
    })
    return outreach_out(record)
