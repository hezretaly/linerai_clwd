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

from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import Conversation, Escalation, Lead


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
