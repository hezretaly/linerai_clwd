"""One correspondent's email exchange, counted in one place.

`/app/email` lists messages. This lists **people** -- everyone the dealership
has written to or heard from by mail, one row each, with how far the exchange
has actually got. It is the same instinct as `/app/conversations` listing
buyers rather than threads: four messages with one person are one relationship,
and a rep deciding who to answer next is choosing between people.

**The counter is here and nowhere else.** An exchange decides two things that
are read in different places -- whether a row has graduated into the
conversations list, and whether the badge on it says the buyer is waiting -- and
two copies of "what counts as a back and forth" is exactly how a header ends up
saying 3 over a row that reads as 2. It is the same rule `_in_box` follows for
the mailbox tabs and `conversationFilters.ts` follows for the chat list.

Nothing here reads a body. Grouping and counting only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.email_intake import is_ours, sender_address
from app.models import InboundEmail, Lead, Outreach

#: How many completed back-and-forths make this a conversation rather than a
#: message. Below it the exchange lives in the inbound list; at it, the buyer
#: is somebody the dealership is genuinely talking to and belongs in the list a
#: rep works from.
#:
#: Three is a judgement, not a discovery -- one message is an enquiry and two
#: is a question answered, while three means neither side has finished. It is
#: one constant so moving it moves both the badge and the list together.
EXCHANGE_THRESHOLD = 3


@dataclass
class Tally:
    """What a walk through one correspondent's mail found."""

    #: Completed back-and-forths: an inbound answered by an outbound.
    exchanges: int
    #: True when their last message has had no answer. Falls out of the same
    #: walk as the count, which is the point -- a row badged "waiting" that the
    #: counter disagrees with is a page arguing with itself.
    waiting: bool
    inbound: int
    outbound: int


def tally(events: list[tuple[datetime | None, str]]) -> Tally:
    """Count the exchanges in `(at, direction)` pairs, oldest first.

    **An exchange is an inbound that we answered.** Not a message, and not a
    pair of messages: a buyer who writes three times before anyone replies has
    had one answer, so that is one exchange, and the two extra messages are
    what `waiting` is for rather than something to inflate the count with.

    A thread we opened -- an appointment confirmation, a follow-up -- starts at
    zero and reaches one when they write back and we answer. That is right: an
    outreach nobody replied to is not a conversation.
    """
    ordered = sorted(events, key=lambda pair: (pair[0] is None, pair[0]))
    exchanges, waiting, inbound, outbound = 0, False, 0, 0
    for _, direction in ordered:
        if direction == "in":
            inbound += 1
            waiting = True
        else:
            outbound += 1
            if waiting:
                exchanges += 1
                waiting = False
    return Tally(exchanges=exchanges, waiting=waiting, inbound=inbound, outbound=outbound)


def graduated(counted: Tally) -> bool:
    """Has this exchange become a conversation?

    Read by the list and by the badge, from the one tally, so the two cannot
    disagree about a row.
    """
    return counted.exchanges >= EXCHANGE_THRESHOLD


def threads(db: Session, *, limit: int = 200) -> list[dict]:
    """One row per correspondent, newest activity first.

    Two kinds of row, and neither is dressed up as the other -- the same shape
    the conversations list uses. A **buyer** groups every email against their
    lead, whatever address each one arrived from, because the lead is who they
    are. A **stranger** is an unresolved delivery, grouped by the address it
    came from: there is no lead to group by, and that is exactly the case this
    list exists to make visible.
    """
    rows = (
        db.query(Outreach)
        .filter(Outreach.channel == "email")
        .order_by(Outreach.created_at.asc())
        .all()
    )
    leads = {
        lead.id: lead
        for lead in db.query(Lead).filter(
            Lead.id.in_({r.lead_id for r in rows if r.lead_id} or {""})
        ).all()
    }

    grouped: dict[str, dict] = {}
    for row in rows:
        # A send with no lead -- the setup page's own test message -- is nobody
        # to have an exchange with, so it is a message and not a thread.
        if not row.lead_id:
            continue
        lead = leads.get(row.lead_id)
        entry = grouped.setdefault(f"lead:{row.lead_id}", {
            "key": f"lead:{row.lead_id}",
            "kind": "buyer",
            "lead_id": row.lead_id,
            "name": (lead.name if lead else "") or "",
            "address": (lead.email if lead else "") or row.to_address,
            "events": [],
            "last_subject": "",
            "last_body": "",
            "last_direction": "",
            "at": None,
        })
        entry["events"].append((row.sent_at or row.created_at, row.direction))
        entry["last_subject"] = row.subject
        entry["last_body"] = row.body
        entry["last_direction"] = row.direction
        entry["at"] = row.sent_at or row.created_at

    for row in (
        db.query(InboundEmail)
        .filter(InboundEmail.outcome == "unresolved")
        .order_by(InboundEmail.created_at.asc())
        .all()
    ):
        # Ours is ours: unplaced mail to support@ or founder@ is listed at
        # /ops and is not a dealership's to read.
        if is_ours(row.to_address):
            continue
        address = sender_address(row.from_address) or row.from_address
        entry = grouped.setdefault(f"address:{address}", {
            "key": f"address:{address}",
            "kind": "stranger",
            "lead_id": None,
            "name": "",
            "address": row.from_address,
            "events": [],
            "last_subject": "",
            "last_body": "",
            "last_direction": "",
            "at": None,
        })
        entry["events"].append((row.created_at, "in"))
        entry["last_subject"] = row.subject
        entry["last_body"] = row.body
        entry["last_direction"] = "in"
        entry["at"] = row.created_at

    out = []
    for entry in grouped.values():
        counted = tally(entry.pop("events"))
        out.append({
            **entry,
            "exchanges": counted.exchanges,
            "inbound": counted.inbound,
            "outbound": counted.outbound,
            # Their last message has had no answer -- the one thing a rep
            # scanning this list is actually looking for, and therefore the one
            # thing that must not be claimed about a row where it is not true.
            #
            # A stranger row is never waiting. Since a person writing to a
            # published address becomes a buyer, what is left unplaced is a
            # newsletter, an out-of-office and a no-reply mailbox -- and
            # flagging those as awaiting a reply puts nine rows nobody will
            # ever answer above the one somebody has to.
            "waiting": counted.waiting and entry["kind"] != "stranger",
            "graduated": graduated(counted),
        })
    out.sort(key=lambda row: (row["at"] is None, row["at"]), reverse=True)
    return out[:limit]
