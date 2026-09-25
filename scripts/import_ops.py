#!/usr/bin/env python3
"""Read an `export_ops.py` file into this box: Liner's mail and demo bookings.

    make import-ops FILE=ops-export-<stamp>.tar.gz              # the plan
    make import-ops FILE=ops-export-<stamp>.tar.gz ARGS=--apply # the copy

The other half of `scripts/export_ops.py`, which says what the file holds and
why nothing generated is in it. This side decides where each row goes:

- The `ops_` rows go into Liner's own database. **An existing row wins**, by
  id, so running it twice copies nothing twice.
- **Mail we wrote is re-attached to its author by address.** `founder@` has a
  new id on a fresh box, and the message's `author_id` is the old one;
  matched on the address, the way `restore_ops.identity_of` matches accounts.
  A message whose author has no account here is reported and left.
- **Mail people wrote to us** goes into the unprefixed store, where this box's
  own intake files it and where `/ops` lists it. Only rows `is_ours` says are
  addressed to us: a stranger's note to a dealership's `sales@` on the old
  box was that dealership's mail, not ours.
- The `.eml` files and attachments are unpacked under `backend/var/` at the
  paths their rows name.

Values are converted to each column's type rather than trusted: the file came
out of SQLite, where a time is text and a boolean is 0 or 1, and Postgres
takes neither in their place.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import tarfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import Boolean, DateTime, Integer, insert, select  # noqa: E402

from app.config import BACKEND_DIR  # noqa: E402
from app.db import Base, OpsBase, SessionLocal, ops_session  # noqa: E402
from app.email_intake import is_ours  # noqa: E402
import app.models  # noqa: E402,F401 -- registers every table

OPS_ORDER = ["ops_demo_requests", "ops_messages", "ops_mail_envelopes",
             "ops_mail_attachments", "ops_mail_state", "ops_sms_opt_outs"]
STORE_ORDER = ["inbound_emails", "email_envelopes", "email_attachments"]


def typed(column, value):
    """One value as the target column wants it, or None."""
    if value is None or value == "":
        return None if column.nullable or value is None else value
    kind = column.type
    if isinstance(kind, DateTime) and isinstance(value, str):
        return dt.datetime.fromisoformat(value.replace("Z", "").replace(" ", "T"))
    if isinstance(kind, Boolean):
        return bool(int(value)) if isinstance(value, (int, str)) and str(value).isdigit() else bool(value)
    if isinstance(kind, Integer) and isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return value


def shaped(table, row: dict) -> dict:
    """Only the columns this schema has, each converted to its type."""
    return {c.name: typed(c, row[c.name]) for c in table.columns if c.name in row}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    if not args:
        print(__doc__)
        return 2
    source = pathlib.Path(args[0])
    with tarfile.open(source, "r:gz") as tar:
        data = json.load(tar.extractfile("export.json"))
        members = [m for m in tar.getmembers() if m.name.startswith("var/") and m.isfile()]
        tables = data.get("tables", {})

        # Old account id -> this box's account id, through the address.
        from app.models.ops import OpsUser

        with ops_session() as ops:
            here = {u.email.lower(): u.id for u in ops.query(OpsUser).all()}
        author = {u["id"]: here.get(str(u.get("email", "")).lower()) for u in tables.get("ops_users", [])}

        plan: dict[str, list] = {}
        notes: list[str] = []
        for row in tables.get("ops_messages", []):
            new_id = author.get(row.get("author_id"))
            if not new_id:
                notes.append(f"ops_messages {row['id'][:8]}: its author has no account here -- left")
                continue
            plan.setdefault("ops_messages", []).append({**row, "author_id": new_id})
        kept_messages = {r["id"] for r in plan.get("ops_messages", [])}
        for name in ("ops_mail_envelopes", "ops_mail_attachments"):
            for row in tables.get(name, []):
                if row.get("message_id") in kept_messages:
                    if "uploaded_by" in row:
                        row = {**row, "uploaded_by": author.get(row["uploaded_by"])}
                    plan.setdefault(name, []).append(row)
        for name in ("ops_demo_requests", "ops_mail_state", "ops_sms_opt_outs"):
            plan[name] = list(tables.get(name, []))

        ours = [r for r in tables.get("inbound_emails", []) if is_ours(r.get("to_address", ""))]
        notes += [f"inbound_emails {r['id'][:8]} to {r.get('to_address')}: not addressed to us -- left"
                  for r in tables.get("inbound_emails", []) if r not in ours]
        plan["inbound_emails"] = ours
        receipts = {r["id"] for r in ours}
        plan["email_envelopes"] = [e for e in tables.get("email_envelopes", []) if e.get("receipt_id") in receipts]
        envelopes = {e["id"] for e in plan["email_envelopes"]}
        plan["email_attachments"] = [a for a in tables.get("email_attachments", []) if a.get("envelope_id") in envelopes]

        wanted = {f"var/mail/{e['raw_path']}" for e in plan["email_envelopes"] if e.get("raw_path")}
        wanted |= {f"var/attachments/{a['path']}" for name in ("email_attachments", "ops_mail_attachments")
                   for a in plan.get(name, []) if a.get("path")}

        def load(session, metadata, order) -> None:
            for name in order:
                table = metadata.tables[name]
                pk = list(table.primary_key.columns)[0]
                have = set(session.execute(select(pk)).scalars())
                new = [r for r in plan.get(name, []) if r["id"] not in have]
                print(f"  {name:22} {len(new):5} new   {len(plan.get(name, [])) - len(new):5} already here")
                if apply and new:
                    session.execute(insert(table), [shaped(table, r) for r in new])

        print(f"\n{source.name}, exported {data.get('exported_at', '?')} from {data.get('from', '?')}\n")
        with ops_session() as ops:
            load(ops, OpsBase.metadata, OPS_ORDER)
            if apply:
                ops.commit()
        with SessionLocal("") as store:
            load(store, Base.metadata, STORE_ORDER)
            if apply:
                store.commit()

        var = BACKEND_DIR / "var"
        placed = 0
        for member in members:
            if member.name not in wanted or ".." in pathlib.PurePosixPath(member.name).parts:
                continue
            target = BACKEND_DIR / member.name
            if apply and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tar.extractfile(member).read())
            placed += 1
        print(f"  {'files':22} {placed:5} under {var}")

    for note in notes:
        print(f"  note  {note}")
    print("\nCopied." if apply else "\nThe plan only -- nothing was written. ARGS=--apply to copy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
