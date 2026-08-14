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


def claim_unresolved(db: Session, lead: Lead) -> int:
    """Mail this buyer sent before we knew who they were.

    Resolution runs once, when a delivery arrives. Somebody who writes to
    `sales@` before they are anyone here is stored unresolved -- correctly,
    because at that moment there is no lead to attach them to. But the next day
    they chat and book, a lead is minted with that same address, and the email
    they sent stays a stranger's forever: not on their timeline, not on their
    buyer page, visible only on the mailbox's unmatched tab. One person, two
    records, and nothing ever joins them.

    So the other half of the ladder runs here: when a buyer comes into
    existence, anything unplaced that they sent is placed. Exactly the same
    resolution as the live path -- the receipt is put back to `received` and
    `_place` re-runs -- rather than a second copy of it, which is how the two
    would start disagreeing about who a reply belongs to.

    Email exact, and only email. Phone is not on an email envelope, and a name
    is not identity here or anywhere else in this module.
    """
    from app.api.inbound_email import _place
    from app.models import InboundEmail

    address = (lead.email or "").strip().lower()
    if not address:
        return 0

    waiting = [
        row for row in db.query(InboundEmail).filter(InboundEmail.outcome == "unresolved").all()
        if (row.from_address or "").strip().lower() == address
    ]
    for row in waiting:
        row.outcome = "received"
    if waiting:
        db.commit()
    for row in waiting:
        _place(row.id)
    return len(waiting)
