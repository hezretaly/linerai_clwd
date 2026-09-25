"""One definition of what happened to an `Outreach` row: whether it went out,
and its delivery state.

`Outreach.status` is overloaded. `'sent'` means "the provider accepted this"
for an outbound row, but every **inbound** row -- a buyer's email reply, an
inbound text -- is also filed with `status='sent'` (`inbound_email.py`,
`sms.py`), because arriving needs no provider acknowledgement. Read as
`status == 'sent'` with no `direction` check, and a buyer's own reply counts
as something the dealership sent. Before this module, at least four places
read it that way: the Overview "Emails sent" KPI, the mailbox's people tally
(`email_threads.py`), the buyer-page contact strip (`app/timeline.py`), and
`email_draft.py`'s "emails so far" history.

`'sent'` is not the end of the story either. A row is committed as `'queued'`
*before* the provider is even asked (`email_outbound.send`, `app/sms.py`), so
a send genuinely in flight -- for as long as the provider call takes, or
forever if the process died mid-send -- reads as neither sent nor failed.
`delivery()` is the one place that turns `(direction, status, created_at)`
into the word a screen should actually show: did the buyer receive this, is
it still being tried, or did it not go.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_

from app.db import utcnow
from app.models import Outreach

#: Terminal failure statuses for an outbound row.
NOT_SENT_STATUSES = ("failed", "bounced")
#: In flight: committed, the provider has not yet answered.
PENDING_STATUSES = ("queued",)
#: Longer than any provider timeout this system calls (Resend's is 20s), so a
#: row still 'queued' past this was orphaned by a crash or a restart -- not a
#: send genuinely in progress.
STUCK_AFTER = timedelta(minutes=5)

#: The moment to date a row by: when the provider answered, or when the row
#: was written if it never got that far. One rule, so the KPI's window and
#: the mailbox's own "Sent" list date the same row the same way.
SENT_AT = func.coalesce(Outreach.sent_at, Outreach.created_at)


def sent_at(o: Outreach) -> datetime:
    """The Python twin of `SENT_AT`, for code already holding a row."""
    return o.sent_at or o.created_at


def went_out(direction: str, status: str) -> bool:
    """True only for an outbound row the provider accepted. Never true for an
    inbound row -- those carry `status='sent'` for a reason that has nothing
    to do with anyone here having sent anything."""
    return direction == "out" and status == "sent"


#: The SQL twin of `went_out`, for a filter clause.
WENT_OUT = and_(Outreach.direction == "out", Outreach.status == "sent")


def is_sent_email(channel: str, direction: str, status: str) -> bool:
    """An email the dealership actually sent -- the one fact the Mail page's
    'Sent' tab and the Overview 'Emails sent' KPI both answer."""
    return channel == "email" and went_out(direction, status)


#: The SQL twin of `is_sent_email`, for a query-side count.
SENT_EMAIL = and_(Outreach.channel == "email", WENT_OUT)


def delivery(
    direction: str, status: str, created_at: datetime | None, *, now: datetime | None = None
) -> str:
    """'received' | 'sent' | 'sending' | 'not_sent' -- the one word a screen
    should use, in place of each reader deriving its own from the raw status.
    """
    if direction == "in":
        return "received"
    if status == "sent":
        return "sent"
    if status in NOT_SENT_STATUSES:
        return "not_sent"
    if status in PENDING_STATUSES:
        cutoff = (now or utcnow()) - STUCK_AFTER
        if created_at is not None and created_at < cutoff:
            return "not_sent"
        return "sending"
    return "not_sent"


def delivery_of(o: Outreach, *, now: datetime | None = None) -> str:
    return delivery(o.direction, o.status, o.created_at, now=now)


def not_sent_clause(now: datetime | None = None):
    """The SQL twin of `delivery(...) == 'not_sent'`, outbound rows only."""
    now = now or utcnow()
    stuck_cutoff = now - STUCK_AFTER
    return and_(
        Outreach.direction == "out",
        or_(
            Outreach.status.in_(NOT_SENT_STATUSES),
            and_(Outreach.status.in_(PENDING_STATUSES), Outreach.created_at < stuck_cutoff),
        ),
    )
