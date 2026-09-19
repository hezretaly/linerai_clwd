"""Mail that reached us but belongs to nobody, gathered from every store.

`inbound_emails` stays on the **dealership's** side of the split and cannot
move: it carries foreign keys to `leads` and `outreach`, and a cross-database
foreign key is impossible in SQLite. But an unresolved delivery — somebody
writing to `support@` who is not a buyer anywhere — is listed in *our* mailbox,
because there is no buyer page for it to appear on instead.

So `/ops` reads it across every seeded store rather than out of one. **That is
more correct than what it did before**, not merely different: it used to read
whichever store was the default, so a stranger's mail that arrived while
`DEALERSHIP=` pointed somewhere else was invisible — a receipt written
precisely so a lost message could not be silent, silently lost.

Rows come back as plain dicts. They are read through sessions that close
immediately, and handing a detached ORM object to a caller that then touches
an unloaded column is a failure that only appears once the data is big enough
to be lazily loaded.
"""

from __future__ import annotations

import logging

from sqlalchemy.exc import OperationalError

from app.config import settings
from app.db import SessionLocal, has_database
from app.models import InboundEmail
from app.stores import known_stores

log = logging.getLogger("liner.ops_inbox")


def _stores() -> list[str]:
    """Default first, then the rest — the order `locate_store` uses."""
    default = settings.dealership.strip()
    return [default] + [s for s in known_stores() if s != default]


def _each(fn):
    """Run `fn(session)` against every store, skipping ones with no database.

    An unseeded store has a profile but no file, and opening it *creates* an
    empty one — so the query then fails with `no such table: inbound_emails`
    rather than returning nothing. That exact shape took down every sign-in
    once; it is skipped here rather than allowed to 500 the ops dashboard.

    Asked through `has_database` **before** the connect, which is the half
    this originally missed: catching the error afterwards keeps `/ops`
    working but the file has already been created by then, and this runs on
    every read of the ops inbox.
    """
    out = []
    for slug in _stores():
        if not has_database(slug):
            continue
        try:
            with SessionLocal(slug) as db:
                out.append((slug, fn(db)))
        except OperationalError:
            continue
    return out


def unresolved_count() -> int:
    return sum(
        n for _slug, n in _each(
            lambda db: db.query(InboundEmail)
            .filter(InboundEmail.outcome == "unresolved")
            .count()
        )
    )


def unresolved(limit: int = 300) -> list[dict]:
    """Every unplaced delivery, newest first, as plain dicts.

    `store` rides along on each row because an id is only unique within the
    file it came from, and a read or trash mark has to be able to find it
    again.
    """
    rows: list[dict] = []
    for slug, found in _each(
        lambda db: [
            {
                "id": m.id,
                "from_address": m.from_address,
                "to_address": m.to_address or "",
                "subject": m.subject or "",
                "body": m.body or "",
                "created_at": m.created_at,
                "outcome": m.outcome,
            }
            for m in db.query(InboundEmail)
            .filter(InboundEmail.outcome == "unresolved")
            .order_by(InboundEmail.created_at.desc())
            .limit(limit)
            .all()
        ]
    ):
        for row in found:
            row["store"] = slug
            rows.append(row)
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows[:limit]


def exists(inbound_id: str) -> bool:
    """Whether any store holds this delivery.

    Marking one read or trashed writes to `ops_mail_state`, which is ours —
    the dealership's row is never touched. This is only here so a mark against
    an id that does not exist anywhere is a 404 rather than a stored mark
    pointing at nothing.
    """
    return any(
        found for _slug, found in _each(
            lambda db: db.query(InboundEmail.id).filter_by(id=inbound_id).first() is not None
        )
    )
