"""Read a `make dump-ops` file back into Liner's own database.

    make restore-ops FILE=backend/var/ops-dump-20260919-160232.json
    make restore-ops FILE=... ARGS=--dry-run

The other half of `dump_ops.py`, and the reason that one scans every store:
before the split, `ops_` tables were built into each store's file, so the rows
were scattered. This reads all of that back into the one database they now
belong in.

**Existing rows win.** A restore is a rescue, not a rollback: the usual moment
to run it is after recreating the databases, but the second-usual is after
something has already been written, and overwriting a demo request somebody
has since answered would destroy the newer fact. Rows already present by
primary key are counted and skipped, so running it twice is safe and running
it late is honest.

**`ops_users` is de-duplicated across stores.** `founder@` and `cto@` were
created once per dealership, so a dump of three stores carries three copies
with three different ids and one address. The address is the identity --
`ops_users.email` is unique -- so the first copy wins and the rest are
reported rather than inserted, which would fail on the unique index and roll
the whole restore back.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import inspect as sa_inspect  # noqa: E402

from app.db import create_ops_all, ops_session  # noqa: E402
from app.models.ops import (  # noqa: E402
    DemoRequest,
    OpsMailState,
    OpsMessage,
    OpsUser,
    PhoneCall,
    SmsOptOut,
)

#: Parents before children, the opposite of `_clear`'s order -- that one
#: deletes and this one inserts. `ops_phone_calls` is the only ops table with
#: *declared* foreign keys (a user and a demo request), so it is the only one
#: SQLite with `foreign_keys=ON` would actually refuse out of order; the rest
#: are ordered by the same rule anyway, because which columns carry a real
#: constraint is a thing that changes and an order that only works by accident
#: is one nobody will know to preserve. `make smoke` reads this list and fails
#: on a table missing from it or one sitting above something it points at.
ORDER = [
    (OpsUser.__tablename__, OpsUser),
    (DemoRequest.__tablename__, DemoRequest),
    (OpsMessage.__tablename__, OpsMessage),
    (OpsMailState.__tablename__, OpsMailState),
    (PhoneCall.__tablename__, PhoneCall),
    (SmsOptOut.__tablename__, SmsOptOut),
]

def identity_of(table: str, row: dict) -> tuple[str, str]:
    """What makes two ops rows, in two different databases, the same row.

    The primary key everywhere except `ops_users`, where it is the **address**.
    Three stores each seeded `founder@` with an id of its own, so the id says
    nothing about whether it is the same person -- and `ops_users.email` is
    unique, so inserting the second copy would fail the index and roll a whole
    restore back.

    One function because two callers ask this question in opposite directions:
    the restore asks *is this row already there* before inserting, and
    `prune_ops.py` asks *is this row safely there* before dropping the table it
    sits in. Two copies of the rule is how one of them starts deleting a row
    the other would not have recognised.
    """
    if table == OpsUser.__tablename__:
        return ("email", (row.get("email") or "").lower())
    return ("id", row.get("id"))


def _revive(model, row: dict) -> dict:
    """Turn the dump's ISO strings back into the types the columns declare.

    Load-bearing, and the failure is loud rather than quiet -- which is worth
    writing down, because the reverse was assumed first. SQLAlchemy's SQLite
    dialect refuses a string for a `DateTime` column outright (*"SQLite
    DateTime type only accepts Python datetime and date objects as input"*),
    so without this the restore raises on the first row instead of storing a
    text timestamp that would sort and compare wrongly for ever. Measured, not
    reasoned: passing the dump's own `2026-09-19T16:39:31.745205` through
    unconverted is a `StatementError` on flush.
    """
    columns = {c.name: c for c in model.__table__.columns}
    out = {}
    for key, value in row.items():
        column = columns.get(key)
        if column is None:
            # A column the dump has and this schema does not. Dropped rather
            # than passed on, so an older dump still restores.
            continue
        if isinstance(value, str) and str(column.type).startswith("DATETIME"):
            try:
                value = dt.datetime.fromisoformat(value)
            except ValueError:
                value = None
        out[key] = value
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    dry = "--dry-run" in sys.argv
    if not args:
        print("\nUsage: make restore-ops FILE=backend/var/ops-dump-<stamp>.json\n")
        return 2

    path = pathlib.Path(args[0])
    if not path.is_file():
        print(f"\nNo such dump: {path}\n")
        return 2

    payload = json.loads(path.read_text())
    stores = payload.get("stores", {})
    print(f"\n  from {path}")
    print(f"  taken {payload.get('taken_at', 'unknown')}, {len(stores)} store(s)\n")

    create_ops_all()
    added: dict[str, int] = {}
    #: Two reasons a row is not inserted, counted apart because they are
    #: different facts and the line printed at the end says which. "Already
    #: there" over a de-duplicated copy is a claim the tool cannot back: the
    #: row was not in the database at all, it was the same person arriving a
    #: second time from another store's file.
    skipped: dict[str, int] = {}
    merged: dict[str, int] = {}
    seen_emails: set[str] = set()

    with ops_session() as ops:
        existing_emails = {e for (e,) in ops.query(OpsUser.email).all()}
        seen_emails |= existing_emails

        for table, model in ORDER:
            pk = list(sa_inspect(model).primary_key)[0].name
            have = {row[0] for row in ops.query(getattr(model, pk)).all()}
            for store, tables in stores.items():
                for raw in tables.get(table, []):
                    key = raw.get(pk)
                    if key in have:
                        skipped[table] = skipped.get(table, 0) + 1
                        continue
                    if model is OpsUser:
                        # One address, one account -- `identity_of`, the same
                        # rule `prune_ops.py` reads before it drops a table.
                        email = identity_of(table, raw)[1]
                        if email in seen_emails:
                            merged[table] = merged.get(table, 0) + 1
                            continue
                        seen_emails.add(email)
                    ops.add(model(**_revive(model, raw)))
                    have.add(key)
                    added[table] = added.get(table, 0) + 1
        if dry:
            ops.rollback()
        else:
            ops.commit()

    for table, _model in ORDER:
        a, s, m = added.get(table, 0), skipped.get(table, 0), merged.get(table, 0)
        if not (a or s or m):
            continue
        why = []
        if s:
            why.append(f"{s} already there")
        if m:
            why.append(f"{m} the same address from another store")
        print(f"  {table:22} +{a:<5} {', '.join(why)}")

    total = sum(added.values())
    print(f"\n  {total} row(s) {'would be ' if dry else ''}restored"
          f"{', nothing written (--dry-run)' if dry else ''}\n")
    if not dry and total:
        print("  Check it: make dump-ops\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
