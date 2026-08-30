"""Booking a demo of Liner, and asking us for help.

The marketing page's own back end, and the one part of this system whose
customer is a dealership rather than a car buyer. Deliberately its own table
and its own module: a `Lead` is somebody buying a car, and putting prospects
into the list a rep works from would make that list mean two things.

Unauthenticated by necessity -- it is a public form -- so everything a public
write needs is here: a length cap on every field, a slot that must be one this
endpoint really offered, and a refusal to take a time already taken.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db, utcnow
from app.events import emit
from app.models import DemoRequest, OpsUser
from app.api.deps import require_owner
from app.schemas.serialize import iso, stamp

router = APIRouter(prefix="/demo", tags=["demo"])

#: The exact words on the checkbox. Stored with each row rather than referenced,
#: so a later edit to the page cannot rewrite what somebody already agreed to.
CONSENT = (
    "By submitting, you agree that Liner AI may contact you by phone, text, and "
    "email about your demo. Consent isn't required to purchase. Reply STOP to opt out."
)

#: And the other kind. Somebody reporting that something is broken is not
#: booking a demo, and asking them to agree to be contacted "about your demo"
#: is a consent record that does not describe what happened -- which is the one
#: thing a consent record is for. The support form also takes no phone number,
#: so promising phone and text, and offering an SMS opt-out for messages that
#: will never be sent, would be wrong in the other direction too.
SUPPORT_CONSENT = (
    "By submitting, you agree that Liner AI may email you back about your message. "
    "Consent isn't required to purchase."
)

#: A field long enough for any real answer and short enough that an unguarded
#: public endpoint is not a place to store things.
LIMIT = 500


def _slots(db: Session) -> list[datetime]:
    """Times a demo can actually be booked into.

    Built from a weekday window rather than a calendar we do not have, and the
    ones already taken are removed -- so the page can only ever offer a time
    that is really free, which is the same rule `check_availability` follows
    for a buyer looking at a dealership's week.
    """
    now = utcnow()
    taken = {
        row.slot_at
        for row in db.query(DemoRequest)
        .filter(DemoRequest.slot_at.isnot(None), DemoRequest.status != "cancelled")
        .all()
    }
    hours = [int(h) for h in settings.demo_hours.split(",") if h.strip().isdigit()]
    out: list[datetime] = []
    day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    for _ in range(settings.demo_days_ahead):
        if day.weekday() < 5:  # Mon-Fri
            for hour in hours:
                at = day.replace(hour=hour)
                if at > now and at not in taken:
                    out.append(at)
        day += timedelta(days=1)
    return out


@router.get("/slots")
def open_slots(db: Session = Depends(get_db)) -> dict:
    """Grouped by day, because that is how the page asks the question."""
    days: dict[str, list[str]] = {}
    for at in _slots(db):
        days.setdefault(at.strftime("%Y-%m-%d"), []).append(at.strftime("%H:%M"))
    return {
        "timezone": settings.demo_timezone,
        "consent_text": CONSENT,
        # Both, because the page shows one of them depending on which form is
        # open and neither may be a second copy living in the HTML.
        "support_consent_text": SUPPORT_CONSENT,
        "days": [
            {"date": date, "label": datetime.strptime(date, "%Y-%m-%d").strftime("%a %-d %b"),
             "slots": times}
            for date, times in days.items()
        ],
    }


class Booking(BaseModel):
    name: str = ""
    dealership: str = ""
    email: str = ""
    phone: str = ""
    dealership_url: str = ""
    message: str = ""
    #: "2026-08-20T14:00" -- one of the values /slots offered.
    slot: str | None = None
    consent: bool = False


def _clean(body: Booking) -> dict:
    return {
        field: (getattr(body, field) or "").strip()[:LIMIT]
        for field in ("name", "dealership", "email", "phone", "dealership_url", "message")
    }


@router.post("/requests")
def book_demo(body: Booking, db: Session = Depends(get_db)) -> dict:
    fields = _clean(body)
    if not fields["name"] or not fields["email"]:
        raise HTTPException(400, "A name and an email address are needed.")
    if "@" not in fields["email"]:
        raise HTTPException(400, "That does not look like an email address.")
    if not body.consent:
        # The tick is the record. Taking the booking without it would leave a
        # row that cannot say whether anyone agreed to be contacted, which is
        # the only thing that row is for.
        raise HTTPException(400, "Please agree to be contacted about your demo.")

    at = None
    if body.slot:
        try:
            at = datetime.fromisoformat(body.slot)
        except ValueError:
            raise HTTPException(400, "That is not a time we offered.") from None
        # Re-decided at submit, never at render: the form sits on screen while
        # somebody types their details, and "still free" a minute ago is not
        # an answer. Same reason `book_appointment` re-checks a clash.
        if at not in _slots(db):
            raise HTTPException(409, "That time has just been taken. Pick another.")

    row = DemoRequest(
        kind="demo" if at is not None else "support",
        slot_at=at,
        consent_at=utcnow(),
        # The wording that was actually on the checkbox they ticked, which is
        # a different one on each path.
        consent_text=CONSENT if at is not None else SUPPORT_CONSENT,
        **fields,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    # The one thing on our own dashboard that nobody clicked for, so it is the
    # one thing worth interrupting somebody about.
    emit(db, "demo.requested", {
        "request_id": row.id, "kind": row.kind, "name": row.name,
        "dealership": row.dealership, "slot_at": iso(row.slot_at),
    })
    return {
        "id": row.id,
        "kind": row.kind,
        "slot_at": iso(row.slot_at),
        "reply_to": settings.founder_email or settings.support_email,
    }


@router.post("/requests/{request_id}/cancel")
def cancel_demo(
    request_id: str,
    db: Session = Depends(get_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    """Give the slot back.

    Somebody has to be able to, and `_slots` already skips cancelled rows -- so
    without this the only way to free a time was to edit the database. It is
    also what stops `make smoke` eating one slot a run, which is the same
    self-reinforcing leak the appointment fixture had.
    """
    row = db.query(DemoRequest).filter_by(id=request_id).one_or_none()
    if row is None:
        raise HTTPException(404, "No such request")
    row.status = "cancelled"
    db.commit()
    return {"cancelled": True, "id": row.id}


@router.get("/requests")
def list_requests(
    db: Session = Depends(get_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    """Everything the page has taken.

    Ours, so `require_owner` and not `current_user`. These are the names,
    addresses and phone numbers of *other dealerships* asking us for a demo --
    which is to say a list of Riverside Auto's competitors, and about the last
    thing their staff should be able to read from inside their own dashboard.
    That it was ever a dealer session is exactly the confusion `ops_users`
    exists to end.
    """
    rows = (
        db.query(DemoRequest).order_by(DemoRequest.created_at.desc()).limit(200).all()
    )
    return {
        "requests": [
            {
                "id": r.id, "kind": r.kind, "name": r.name, "dealership": r.dealership,
                "email": r.email, "phone": r.phone, "dealership_url": r.dealership_url,
                "message": r.message, "slot_at": iso(r.slot_at),
                "consented_at": stamp(r.consent_at), "status": r.status,
                "created_at": stamp(r.created_at),
            }
            for r in rows
        ]
    }
