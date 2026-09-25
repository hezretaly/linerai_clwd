"""One correspondent's email exchange, counted in one place.

`/app/email` lists messages. This lists **people** -- everyone the dealership
has written to or heard from by mail, one row each, with how far the exchange
has actually got. It is the same instinct as `/app/conversations` listing
buyers rather than threads: four messages with one person are one relationship,
and a rep deciding who to answer next is choosing between people.

**The counter is here and nowhere else.** An exchange decides what a rep sees
in two different places -- the badge that says a buyer is waiting, and the
depth shown beside their name -- and two copies of "what counts as a back and
forth" is exactly how a header ends up saying 3 over a row that reads as 2.
It is the same rule `_in_box` follows for the mailbox tabs and
`conversationFilters.ts` follows for the chat list.

**"In /app/conversations" is not a threshold.** It used to be: below three
completed exchanges a buyer sat in "Open", at three they "graduated" into
`in_conversations`. But `api/inbound_email.py` has minted an email
`Conversation` plus a mirrored buyer message on the very first accepted
delivery since `e8c5185`, and `/api/conversations` (via `threads.started`)
has listed any conversation the buyer has spoken in since before that -- with
no threshold at all. So a buyer was on `/app/conversations` and counted in
the sidebar badge from their first email, while this page kept calling them
"not yet a conversation" until they had answered three times over, which most
buyers who write in once never do. `in_conversations` now reads the same
predicate the conversations list and the badge already use --
`threads.started_leads(db, channel="email")` -- so this page can never claim
a buyer is missing from a list they are actually on, or vice versa. Exchanges
and "waiting" stay useful *depth* numbers; they no longer gate membership.

Nothing here reads a body. Grouping and counting only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app import outreach_status, threads as threads_module
from app.email_intake import is_ours, sender_address
from app.models import InboundEmail, Lead, Outreach


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

    **Every `direction == "out"` pair passed in here is assumed to have
    actually gone out.** Callers (`threads()` below) are responsible for
    leaving out a queued or failed send before calling this -- a reply that
    never left the building does not answer anybody, and counting it here is
    how a buyer who was never actually written back to stopped showing as
    waiting (item 36).
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


def lead_tally(db: Session, lead_id: str) -> Tally:
    """One buyer's own mail tally, on the same basis `threads()` counts by:
    every email `Outreach` row for them, in either direction, minus an
    outbound row that never actually went out.

    The one place `app/timeline.py`'s email chip and this module's own rows
    agree from -- both read the same rows the same way, so "Email 3" on the
    buyer page and "1 in, 1 out" on the Mail page's row for the same person
    can never mean different exchange counts (items 20, 32).
    """
    rows = db.query(Outreach).filter(
        Outreach.lead_id == lead_id, Outreach.channel == "email"
    ).all()
    events = [
        (outreach_status.sent_at(row), row.direction)
        for row in rows
        if row.direction == "in" or outreach_status.went_out(row.direction, row.status)
    ]
    return tally(events)


def threads(db: Session, *, limit: int | None = None) -> list[dict]:
    """One row per correspondent, newest activity first.

    **Uncapped by default.** Every row is loaded and every count computed
    over the full set; `limit`, when given, only slices the *returned* list,
    the same way `/api/email/messages` already pages after counting. Before
    this, the function loaded every row and then silently returned `out[:200]`
    -- the counts callers built from that return value were themselves capped,
    so once more than 200 correspondents existed the People tabs undercounted
    against this function's own untruncated read, and against
    `/api/email/messages`, which counts before it pages (item 35).

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
    in_conversations = threads_module.started_leads(db, channel="email")

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
        # A queued or failed outbound row never reached the buyer, so it does
        # not answer them: it is left out of `events` entirely (tally() would
        # otherwise read it as a reply and clear `waiting`), and it does not
        # become their most recent contact either (item 36). It is still a
        # real row -- the mailbox's message list, a separate query, keeps it
        # in its own "Not sent" tab.
        went = row.direction == "in" or outreach_status.went_out(row.direction, row.status)
        if not went:
            continue
        at = outreach_status.sent_at(row)
        entry["events"].append((at, row.direction))
        entry["last_subject"] = row.subject
        entry["last_body"] = row.body
        entry["last_direction"] = row.direction
        entry["at"] = at

    for row in reversed(unplaced(db)):  # oldest first, matching the loop above
        address = stranger_key(row)
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
            # Whether this buyer is actually on /app/conversations -- read
            # from the one predicate that list itself uses, not a separate
            # exchange threshold. Always false for a stranger: there is no
            # lead, so there is nothing to be "in conversations" as.
            "in_conversations": entry["kind"] == "buyer" and entry["lead_id"] in in_conversations,
        })
    out.sort(key=lambda row: (row["at"] is None, row["at"]), reverse=True)
    return out if limit is None else out[:limit]


def in_box(row: dict, box: str) -> bool:
    """One definition of each People tab, for the counts and for the filter
    both -- moved here from `api/mailbox.py` so `threads()`'s own caller
    cannot compute counts from one slice of the rows and filter from another
    (item 35)."""
    if box == "open":
        return not row["in_conversations"]
    if box == "graduated":
        return row["in_conversations"]
    if box == "waiting":
        return row["waiting"]
    if box == "strangers":
        return row["kind"] == "stranger"
    return True


def counts(rows: list[dict]) -> dict[str, int]:
    """Every tab's total, computed over the full (uncapped) row set -- call
    this before slicing `rows` for display, never after."""
    return {
        key: sum(1 for r in rows if in_box(r, key))
        for key in ("all", "open", "graduated", "waiting", "strangers")
    }


def unplaced(db: Session, *, limit: int | None = None) -> list[InboundEmail]:
    """Unresolved deliveries nobody could place, newest first -- the same
    query `threads()` and `api/mailbox.py`'s unmatched box each used to repeat
    by hand (item 47)."""
    q = (
        db.query(InboundEmail)
        .filter(InboundEmail.outcome == "unresolved")
        .order_by(InboundEmail.created_at.desc())
    )
    rows = [r for r in q.all() if not is_ours(r.to_address)]
    return rows if limit is None else rows[:limit]


def stranger_key(row: InboundEmail) -> str:
    """The address a stranger's unplaced delivery is grouped under -- the
    sender's normalised address where one can be parsed, its raw From header
    otherwise. One function, so the People tab's grouping and any other
    reader of `InboundEmail.from_address` (`ops_inbox.py`, `matching.py`)
    agree that `Repeat@Example.invalid` and `repeat@example.invalid` are one
    correspondent."""
    return sender_address(row.from_address) or row.from_address
