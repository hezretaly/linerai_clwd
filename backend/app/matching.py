"""Is this the same person? One answer, for every path that creates a lead.

Three places can mint a `Lead`: the ADF importer, manual entry, and
`book_appointment` when a buyer finishes the booking form. They used to
disagree -- the importer matched on email *then* phone, and booking matched on
email alone. So a buyer who booked from chat and later called back leaving a
second address arrived as a second lead, even though the number on file was
identical. A dealership then has two rows for one person and no way to know.

Matching is deliberately narrow. Email exact, phone by its last ten digits, and
nothing else: a name is not identity. Two Dave Joneses are two people, and
merging them silently is a worse failure than leaving a duplicate for someone
to look at.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models import Lead


def digits(value: str) -> str:
    """Last ten digits, so +1 (555) 013-4 and 5550134 are the same person."""
    return re.sub(r"\D", "", value or "")[-10:]


def match_lead(
    db: Session, email: str, phone: str, *, exclude_id: str | None = None
) -> Lead | None:
    """Email first, then phone. A marketplace resends the same buyer for weeks.

    `.first()`, never `.one_or_none()`: `leads.email` is indexed but not
    unique, so a duplicate pair -- which is the whole reason this module
    exists -- would otherwise raise MultipleResultsFound and turn the next
    booking into a 500.
    """
    for candidate, _ in candidates_for(db, email, phone, exclude_id=exclude_id):
        return candidate
    return None


def candidates_for(
    db: Session, email: str, phone: str, *, exclude_id: str | None = None
) -> list[tuple[Lead, str]]:
    """Every lead this contact detail could be, and *why* it matched.

    The reason is not decoration. A rep deciding whether two rows are one
    person needs to know whether the system saw the same address or only the
    same phone -- a shared household number is a real thing, and "we matched
    them, trust us" is not something they can check.
    """
    found: list[tuple[Lead, str]] = []
    seen: set[str] = set()

    clean_email = (email or "").lower().strip()
    if clean_email:
        for lead in db.query(Lead).filter(Lead.email == clean_email).all():
            if lead.id != exclude_id and lead.id not in seen:
                seen.add(lead.id)
                found.append((lead, "same email"))

    tail = digits(phone)
    # Under ten digits is an extension or a fragment, not a number that
    # identifies anyone.
    if len(tail) >= 7:
        for lead in db.query(Lead).filter(Lead.phone != "").all():
            if digits(lead.phone) == tail and lead.id != exclude_id and lead.id not in seen:
                seen.add(lead.id)
                found.append((lead, "same phone"))

    return found
