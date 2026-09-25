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
and the list, the badge, the KPI, the overview's "today" panel, the
Conversations-by-hour chart, the channel mix and every per-lead field
(`conversation_count`, `channels`, `open`, `declined`, `live`) all read it --
via `conversations()` below, so a new count starts from the filtered query
rather than from `db.query(Conversation)` and forgetting the rule.

The rule is the list's original one, unchanged: a conversation has **started**
once there is a message from the buyer in it. A call whose buyer track was
never transcribed has none and is hidden by this rule as it always was; the
recording still has both halves and the buyer's page still lists the call.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, Query

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


def conversations(db: Session, *cols):
    """The base query for "conversation rows that are conversations" --
    started(db) already applied. Every count of conversations should start
    here rather than from `db.query(Conversation)` directly, so a new count
    cannot forget the filter the way `trends()`, `_by_hour()` and `_channel_mix()`
    once did: a chat widget opened and abandoned, with no buyer message,
    inflated the chart and the channel mix while the badge, the KPI, the
    today panel and the list all correctly ignored it.
    """
    q: Query = db.query(*(cols or (Conversation,)))
    return q.filter(started(db))


#: A conversation goes quiet after half an hour with nothing new from either
#: side. Thirty minutes is a conversation's own patience -- a buyer comparing
#: two cars pauses for minutes, and a buyer who has gone is gone -- not a
#: business rule, and it lives here once so the badge, the "In progress"
#: card, the lead row and the Overview "Happening now" panel cannot each
#: pick their own window.
LIVE_AFTER = timedelta(minutes=30)


def last_activity(db: Session, conversation_ids: list[str]) -> dict[str, datetime]:
    """The last message time per conversation, in one grouped query -- the
    query that used to be copied by hand in overview.py, conversations.py and
    leads.py, each free to drift from the others."""
    if not conversation_ids:
        return {}
    return dict(
        db.query(Message.conversation_id, func.max(Message.created_at))
        .filter(Message.conversation_id.in_(conversation_ids))
        .group_by(Message.conversation_id)
        .all()
    )


def is_live(convo: Conversation, last_at: datetime | None, now: datetime) -> bool:
    """A thread is live iff it is not closed and its own last activity (or,
    with none yet, its start) is under LIVE_AFTER old.

    This is the one definition of "live" -- Overview's badge, the
    Conversations page's In progress card and Live chip, each lead row's
    In progress/Gone quiet badge and the Overview "Happening now" panel all
    read a flag built from this, rather than re-deriving a window on the
    client (which is how a 30-minute rule on the Conversations page and a
    2-hour rule on the Overview panel, both called "Live", drifted apart).
    """
    if convo.status == "closed":
        return False
    anchor = last_at or convo.started_at
    return (now - anchor) < LIVE_AFTER


def live_keys(db: Session, now: datetime) -> set[str]:
    """`coalesce(lead_id, id)` for every started, not-closed, live thread --
    one key per person (a lead) or per anonymous thread. This is the unit the
    Conversations page's In progress card counts in, and what the sidebar
    badge must equal."""
    rows = conversations(db).filter(Conversation.status != "closed").all()
    activity = last_activity(db, [c.id for c in rows])
    keys: set[str] = set()
    for c in rows:
        if is_live(c, activity.get(c.id), now):
            keys.add(c.lead_id or c.id)
    return keys


def lead_declined(convos: list[Conversation]) -> bool:
    """A buyer has declined only while it stays true: every one of their
    threads is closed, and at least one of them closed as a client decline.

    A buyer who declined in March and is chatting again today is not a
    declined lead -- and a buyer with one open thread is not declined either,
    even if an older thread of theirs ended that way, because the header's
    per-thread "Client declined" control has to agree with this lead-level
    flag or a rep sees one screen say "declined" over another that offers to
    keep talking to them.
    """
    return bool(convos) and all(c.status == "closed" for c in convos) and any(
        c.outcome == "declined" for c in convos
    )
