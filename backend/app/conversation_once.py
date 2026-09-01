"""Things that happen once in a conversation, and never twice.

The closed vocabulary for `ConversationOnce`. One key so far, and the shape is
deliberately minimal: `claim` writes the row and says whether it was the caller
who wrote it. Everything that reads for a decision reads that return value, so
"has this already happened?" and "mark it happened" cannot drift apart into two
statements with a race between them.

**Why a closed vocabulary.** `key` is a free string on the row and this is a
per-thread store, which is the exact shape `CapturedField` already drifted into
-- that table holds both `timeframe` and `timeline` rows meaning the same
thing, written by the same model on different days. Here nothing is written by
a model, so keeping it closed costs nothing and stops the next one being added
without anybody deciding.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Conversation, ConversationOnce

#: The buyer said they were done and nobody here had a way to ring them, so the
#: assistant was sent back to ask for a number. Once. A second refusal is a
#: buyer who cannot end the conversation.
ASKED_FOR_CONTACT = "asked_for_contact"

KEYS = frozenset({ASKED_FOR_CONTACT})


def claim(db: Session, convo: Conversation, key: str) -> bool:
    """Take the one chance, if it is still there. True means it was ours.

    The unique constraint is the arbiter rather than a read-then-write: two
    turns of the same conversation do not usually race, but a retried request
    and a browser that double-submitted do, and losing that race has to mean
    "somebody else already did it" rather than a second row.
    """
    if key not in KEYS:
        raise ValueError(f"Unknown conversation_once key: {key!r}")
    db.add(ConversationOnce(conversation_id=convo.id, key=key))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    return True


def taken(db: Session, convo: Conversation, key: str) -> bool:
    """Whether it has already happened. For reporting, not for deciding --
    deciding goes through `claim`, which cannot be raced."""
    if key not in KEYS:
        raise ValueError(f"Unknown conversation_once key: {key!r}")
    return (
        db.query(ConversationOnce)
        .filter_by(conversation_id=convo.id, key=key)
        .first()
        is not None
    )
