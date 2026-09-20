"""Which conversation rows are conversations.

Opening the chat widget mints a `conversations` row before anybody has typed
-- the greeting needs a session to hang off -- and a visitor who clicks
"Chat with us" and closes it again leaves that row behind, `active`, for
ever, because only the buyer closes a thread and this one never started. The
conversations list has always hidden those: a session where the buyer never
said anything is not something a rep can answer.

The sidebar badge did not. It counted every `active` row, so a dashboard read
**1** on the Conversations icon over a list with nothing in it -- reported
from a real host, where the chat's own message request had failed and left
exactly one such row. Two predicates for one fact, which is the shape every
count on the overview was reorganised to prevent. This is the one predicate,
and the list, the badge, the KPI and the overview's "today" panel all read it.

The rule is the list's original one, unchanged: a conversation has **started**
once there is a message from the buyer in it. A call whose buyer track was
never transcribed has none and is hidden by this rule as it always was; the
recording still has both halves and the buyer's page still lists the call.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, Message


def started(db: Session):
    """A filter clause: conversations the buyer has actually spoken in."""
    spoke = (
        db.query(Message.conversation_id)
        .filter(Message.role == "buyer")
        .distinct()
        .subquery()
    )
    return Conversation.id.in_(select(spoke))
