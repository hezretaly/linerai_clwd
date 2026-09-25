"""Who owns an escalation.

`assign_lead` already decided this: giving a buyer an owner claims everything
of theirs that was waiting for one, because the Needs a person queue means
"waiting for a person to be *found*", and one has been.

That rule was only ever applied at the moment of assigning, and two later
events walked straight round it -- `raise_handoff` on a buyer who already had
a rep, and an inbound reply un-claiming one. Both minted an unclaimed
escalation on an owned buyer, so a row wore "Needs a person" next to the name
of the person it had. A manager who assigns somebody and watches the badge
stay has no way to tell whether the assignment failed or the badge is lying.

So the rule lives here and every writer calls it: an escalation on a buyer who
has an owner is that owner's. It is not silently marked handled -- the thread
still sits at `handoff`, `handoff.triggered` still fires (naming the owner, so
the notification says whose it is), and the escalation is on their timeline
with the claim shown -- it simply stops asking for a person who is already
there.
"""

from __future__ import annotations

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app import threads
from app.db import utcnow
from app.models import Conversation, Escalation, Lead


def waiting_on_person(db: Session) -> dict[str, list[Escalation]]:
    """People a person must still be found for.

    Key is `lead_id` for a lead's thread, or `conversation_id` for an
    anonymous one; value is their unclaimed escalations, oldest first. This
    is the one definition of "Needs a person": the Overview KPI's value is
    `len(...)`, the panel pill reads the same number, the queue is one row
    per key here, and the Conversations page's "Needs a person" card, its
    "Needs attention" chip and each lead's `flagged` flag all read the same
    keys rather than counting escalation rows (which double- or triple-counts
    a buyer with several open threads) or re-deriving their own list.

    A lead-linked thread counts even when it has not started -- an escalated
    thread with a known buyer is never invisible. An anonymous thread only
    counts once it has started (`threads.started`): with no buyer message and
    no lead, there is no row anywhere a rep could open to work it, so an
    unclaimed escalation on one would be a phantom entry on every screen that
    reads this.
    """
    rows = (
        db.query(Escalation, Conversation)
        .join(Conversation, Conversation.id == Escalation.conversation_id)
        .filter(Escalation.claimed_at.is_(None))
        .filter(or_(Conversation.lead_id.isnot(None), threads.started(db)))
        .order_by(Escalation.created_at.asc())
        .all()
    )
    out: dict[str, list[Escalation]] = {}
    for escalation, convo in rows:
        key = convo.lead_id or convo.id
        out.setdefault(key, []).append(escalation)
    return out


def fired_counts(db: Session) -> dict[str, int]:
    """How many times each handoff rule has actually fired -- the count of
    `escalations` rows carrying that rule's id, claimed or not: a fire is a
    fire whether or not somebody has since picked it up.

    This replaces `HandoffRule.fired_count`, a hand-incremented column that
    only one writer (`escalate_to_human`) ever moved, while the demo seed and
    a handful of other callers added escalation rows with no rule id and
    never touched it -- so the Liner setup page's "Fired N times" and the
    rows actually behind "Needs a person" told two different stories for the
    same rule from the day the fixture was seeded.
    """
    rows = (
        db.query(Escalation.handoff_rule_id, func.count(Escalation.id))
        .filter(Escalation.handoff_rule_id.isnot(None))
        .group_by(Escalation.handoff_rule_id)
        .all()
    )
    return dict(rows)


def owner_of(db: Session, convo: Conversation) -> str | None:
    """The rep who owns this thread's buyer, if the thread has one at all.

    Most live chats have no lead -- one is minted when something books -- and
    an anonymous buyer genuinely does need a person to be found.
    """
    if not convo.lead_id:
        return None
    lead = db.query(Lead).filter_by(id=convo.lead_id).one_or_none()
    return lead.assigned_user_id if lead else None


def claim_for_owner(db: Session, escalation: Escalation, convo: Conversation) -> str | None:
    """Stamp the buyer's owner on an escalation, if they have one.

    Returns the user id claimed for, so a caller can put it on the event.
    Does not commit -- the callers here are mid-transaction and one of them
    (`raise_handoff`) has a counter to bump in the same commit.
    """
    owner = owner_of(db, convo)
    if owner is None:
        return None
    escalation.claimed_by_user_id = owner
    escalation.claimed_at = utcnow()
    return owner
