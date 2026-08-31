"""Is this the same person? One answer, for every path that creates a lead.

Three places can mint a `Lead`: the ADF importer, manual entry, and
`book_appointment` when a buyer finishes the booking form. They used to
disagree -- the importer matched on email *then* phone, and booking matched on
email alone. So a buyer who booked from chat and later called back leaving a
second address arrived as a second lead, even though the number on file was
identical. A dealership then has two rows for one person and no way to know.

Matching is deliberately narrow. Email exact, then an address a *person* linked
to the buyer, then phone by its last ten digits, and nothing else: a name is
not identity. Two Dave Joneses are two people, and merging them silently is a
worse failure than leaving a duplicate for someone to look at.

The linked-address rung is not an inference. `lead_addresses` is written from
the buyer page by a rep who knows that the buyer who chatted from one address
is the one now writing from another; nothing in this system ever adds one on
its own, and a shared domain or a shared name never will.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models import Lead, LeadAddress


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

    # A second address a *person* attached to this buyer. It sits between the
    # primary email and the phone deliberately: it is exact, like an email, and
    # somebody deliberately said so -- but the column is still the first
    # answer, so nothing about who an existing address matches changes. This
    # rung can only ever *add* a candidate.
    #
    # It is a human act and never a guess. `lead_addresses` is written from the
    # buyer page by a rep who knows the two are one person; nothing infers one,
    # because a name is not identity and neither is a shared domain.
    if clean_email:
        for link in db.query(LeadAddress).filter(LeadAddress.address == clean_email).all():
            if link.lead_id == exclude_id or link.lead_id in seen:
                continue
            lead = db.query(Lead).filter_by(id=link.lead_id).one_or_none()
            if lead is not None:
                seen.add(lead.id)
                found.append((lead, "address a rep linked to them"))

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

    Every address the buyer is known by -- the column and anything a rep has
    linked -- and only addresses. Phone is not on an email envelope, and a name
    is not identity here or anywhere else in this module.

    **Mail addressed to Liner's own desk is never claimed onto a dealership's
    buyer.** `support@` and `founder@` are ours; a stranger who wrote to us and
    later chats with a dealership has two relationships, not one, and the
    resolution ladder must not merge them. The live path checks this in
    `_lead_from`, and this one went round it -- once a lead exists, `_place`
    never reaches `_lead_from` and the From-address rung files it regardless.
    """
    from app.api.inbound_email import _place
    from app.models import InboundEmail

    from app.email_intake import is_ours, sender_address
    from app.models import LeadAddress as _LeadAddress

    # Every address this buyer is known by, not only the column. A rep who has
    # just linked a second one is saying "their earlier mail is theirs", and
    # leaving it unresolved would make the link do nothing visible -- which is
    # the whole reason they pressed the button.
    addresses = {(lead.email or "").strip().lower()} | {
        (row.address or "").strip().lower()
        for row in db.query(_LeadAddress).filter_by(lead_id=lead.id).all()
    }
    addresses.discard("")
    if not addresses:
        return 0

    # `from_address` is the whole envelope -- `Austin Miller <a@b>` -- so the
    # address has to come out of it before it is compared. Comparing the header
    # matched only a sender with no display name, which is a minority of real
    # mail, and the failure was invisible: everything else simply stayed
    # unresolved, which is what it looked like anyway.
    waiting = [
        row for row in db.query(InboundEmail).filter(InboundEmail.outcome == "unresolved").all()
        if sender_address(row.from_address) in addresses and not is_ours(row.to_address)
    ]
    for row in waiting:
        row.outcome = "received"
    if waiting:
        db.commit()
    for row in waiting:
        _place(row.id)
    return len(waiting)
