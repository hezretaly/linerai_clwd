"""When a Liner demo can be booked, and the one function that books one.

**Extracted rather than copied, which is the whole point of the module.** The
marketing form and the phone assistant both offer times and both take a
booking, and two implementations of "which times are free" is how the page
offers a slot the phone has already given away. This is the same rule
`app/matching.py` follows for who a buyer is and `app/recap.py` follows for
what a conversation was about: one answer, wherever it is read.

`api/demo.py` is still where the public form lives -- its length caps, its
consent handling and its HTTP shape are a public endpoint's business and not
this module's. What moved here is the part the phone needs too.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.db import utcnow
from app.events import emit
from app.models import DemoRequest
from app.schemas.serialize import iso


#: What somebody agrees to on a call, and it is deliberately not the web form's
#: wording. That one begins "By submitting" and describes a checkbox; nobody on
#: a phone submitted anything. A consent record whose text describes an act
#: that did not happen is the one failure a consent record has to avoid, which
#: is the same reason the support form has wording of its own.
PHONE_CONSENT = (
    "Agreed out loud on a recorded phone call: Liner AI may contact you by "
    "phone, text, and email about your demo. Consent isn't required to "
    "purchase. Reply STOP to opt out."
)


def open_slots(db: Session, days_ahead: int = 0) -> list[datetime]:
    """Times a demo can actually be booked into.

    Built from a weekday window rather than a calendar we do not have, and the
    ones already taken are removed -- so nothing can offer a time that is not
    really free, which is the rule `check_availability` follows for a buyer
    looking at a dealership's week.
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
    for _ in range(days_ahead or settings.demo_days_ahead):
        if day.weekday() < 5:  # Mon-Fri
            for hour in hours:
                at = day.replace(hour=hour)
                if at > now and at not in taken:
                    out.append(at)
        day += timedelta(days=1)
    return out


def create(
    db: Session,
    *,
    fields: dict,
    slot: datetime | None,
    consent_text: str,
    source: str = "form",
) -> DemoRequest:
    """Write the request and announce it. The single writer.

    Announcing is part of it rather than the caller's job: `demo.requested` is
    what puts the toast on `/ops`, and it is the one event on that dashboard
    nobody clicked for. A second caller that wrote the row and forgot the emit
    would book a demo nobody was told about.
    """
    row = DemoRequest(
        kind="demo" if slot is not None else "support",
        slot_at=slot,
        consent_at=utcnow(),
        consent_text=consent_text,
        **fields,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    emit(db, "demo.requested", {
        "request_id": row.id,
        "kind": row.kind,
        "name": row.name,
        "dealership": row.dealership,
        "slot_at": iso(row.slot_at),
        # Which door it came in by. The toast reads differently for one that
        # arrived on the phone -- somebody was talking to us a moment ago,
        # which is a warmer thing than a form at two in the morning.
        "source": source,
    })
    return row
