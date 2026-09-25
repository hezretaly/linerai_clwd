#!/usr/bin/env python3
"""Take Liner's own mail and demo bookings off a box, and nothing generated.

Runs on the box being left behind, with nothing but Python's standard
library, so it does not care which commit that box is on:

    cd /tmp && curl -fsSLO https://raw.githubusercontent.com/hezretaly/linerai_clwd/claude/liner-ai-implementation-8xehez/scripts/export_ops.py
    sudo -u liner python3 export_ops.py /srv/liner          # -> /tmp/ops-export-<stamp>.tar.gz

What it takes:

- **Demo bookings and support requests** -- `ops_demo_requests`.
- **The mail we wrote** -- `ops_messages`, their envelopes, their files, and
  the read/trash marks (`ops_mail_state`).
- **The mail people wrote to us** -- a delivery nobody could place is a row in
  a *store's* `inbound_emails`, not in an `ops_` table, so `make dump-ops`
  never saw it. Every store file is read for them; the import keeps only the
  ones addressed to us.
- **STOP replies** (`ops_sms_opt_outs`): a consent record, carried because it
  cannot be rebuilt -- unless the number is one the gates made up (a 555
  exchange, which no person has).
- The `.eml` files and attachments those rows point at, at the same paths.

**What it leaves: anything generated.** Every address the seed, the demo seed
and the gates write is on a reserved domain -- `.invalid`, `.example`,
`.test`, `example.com` (RFC 2606) -- so no real person can have one. A row is
left out when the person it is from or to has one, or when it says it is a
demo row, and a delivery that was refused (a wrong secret, an oversize body)
is a receipt rather than mail. Nothing is guessed from a name. Accounts' password hashes are never
exported: an address is what the import needs to find the account again.

Reads SQLite read-only, and prints what it kept and left per table.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import pathlib
import re
import sqlite3
import sys
import tarfile
from email.utils import parseaddr

RESERVED = re.compile(
    r"@(?:[^@>\s]+\.)?(?:invalid|example|test|localhost)>?$"
    r"|@(?:[^@>\s]+\.)?example\.(?:com|net|org)>?$",
    re.IGNORECASE,
)

#: Columns that must never leave the box.
SECRET = {"password_hash"}


def generated(*addresses: str) -> bool:
    for raw in addresses:
        for part in re.split(r"[,;]", raw or ""):
            address = (parseaddr(part)[1] or part).strip().lower()
            if address and RESERVED.search(address):
                return True
    return False


def fictional(phone: str) -> bool:
    """A number the gates made up: a 555 exchange, or letters where digits go.

    North American 555 numbers are not assigned to people, which is why
    every fixture here uses one; an old smoke run also built numbers out of
    hex. A real STOP never comes from either.
    """
    raw = (phone or "").strip()
    digits = re.sub(r"\D", "", raw)[-10:]
    return bool(re.search(r"[a-z]", raw, re.I)) or len(digits) < 10 or digits[3:6] == "555"


def connect(path: pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def rows(conn: sqlite3.Connection, table: str, where: str = "", args: tuple = ()) -> list[dict]:
    if table not in tables(conn):
        return []
    sql = f'SELECT * FROM "{table}"' + (f" WHERE {where}" if where else "")
    return [{k: row[k] for k in row.keys() if k not in SECRET} for row in conn.execute(sql, args)]


def recipients(json_text: str) -> str:
    try:
        return ",".join(str(p.get("address", "")) for p in json.loads(json_text or "[]"))
    except (ValueError, AttributeError):
        return ""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = pathlib.Path(sys.argv[1]).resolve()
    backend = root / "backend"
    var = backend / "var"
    ops_files = [var / "ops.db"]
    store_files = [backend / "liner.db", *sorted((var / "stores").glob("*.db"))]
    files = [p for p in dict.fromkeys(ops_files + store_files) if p.is_file()]
    if not files:
        print(f"No databases under {backend}. Is {root} the checkout?")
        return 1

    out: dict[str, dict] = {}   # table -> id -> row
    left: dict[str, int] = {}
    keep_files: set[str] = set()   # paths relative to backend/var

    def put(table: str, row: dict) -> None:
        out.setdefault(table, {}).setdefault(row["id"], row)

    def drop(table: str) -> None:
        left[table] = left.get(table, 0) + 1

    for path in files:
        conn = connect(path)
        # ---- ours. Pre-split store files carry ops_ tables too, so every file.
        users = {u["id"]: u for u in rows(conn, "ops_users")}
        for user in users.values():
            put("ops_users", {k: user[k] for k in ("id", "email", "name") if k in user})
        for form in rows(conn, "ops_demo_requests"):
            if generated(form.get("email", "")):
                drop("ops_demo_requests")
            else:
                put("ops_demo_requests", form)
        envelopes = {e["message_id"]: e for e in rows(conn, "ops_mail_envelopes")}
        kept_messages = set()
        for msg in rows(conn, "ops_messages"):
            env = envelopes.get(msg["id"], {})
            if generated(msg.get("to_address", ""), recipients(env.get("to_json", "")),
                         recipients(env.get("cc_json", ""))):
                drop("ops_messages")
                continue
            put("ops_messages", msg)
            kept_messages.add(msg["id"])
            if env:
                put("ops_mail_envelopes", env)
        for att in rows(conn, "ops_mail_attachments"):
            if att.get("message_id") in kept_messages:
                put("ops_mail_attachments", att)
                if att.get("path"):
                    keep_files.add(f"attachments/{att['path']}")
        for opt in rows(conn, "ops_sms_opt_outs"):
            if fictional(opt.get("phone", "")):
                drop("ops_sms_opt_outs")
            else:
                put("ops_sms_opt_outs", opt)
        states = rows(conn, "ops_mail_state")

        # ---- mail to us, filed in a store: unplaced deliveries.
        kept_receipts = set()
        # A refused delivery -- a wrong secret, an oversize body -- is a
        # receipt, not a message: nothing arrived that anybody could read.
        for rec in rows(conn, "inbound_emails",
                        "lead_id IS NULL AND outcome IN ('unresolved', 'received')"):
            if generated(rec.get("from_address", ""), rec.get("to_address", "")) \
                    or str(rec.get("detail") or "").startswith("Demo row"):
                drop("inbound_emails")
                continue
            put("inbound_emails", rec)
            kept_receipts.add(rec["id"])
        kept_envelopes = set()
        for env in rows(conn, "email_envelopes"):
            if env.get("receipt_id") in kept_receipts:
                put("email_envelopes", env)
                kept_envelopes.add(env["id"])
                if env.get("raw_path"):
                    keep_files.add(f"mail/{env['raw_path']}")
        for att in rows(conn, "email_attachments"):
            if att.get("envelope_id") in kept_envelopes:
                put("email_attachments", att)
                if att.get("path"):
                    keep_files.add(f"attachments/{att['path']}")

        # Read and trash marks follow whatever they mark.
        marked = kept_messages | kept_receipts | set(out.get("ops_demo_requests", {}))
        for state in states:
            if state.get("ref_id") in marked:
                put("ops_mail_state", state)
        conn.close()

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = pathlib.Path.cwd() / f"ops-export-{stamp}.tar.gz"
    payload = {
        "exported_at": dt.datetime.now().isoformat(),
        "from": str(root),
        "tables": {t: list(r.values()) for t, r in out.items()},
    }
    missing = []
    with tarfile.open(target, "w:gz") as tar:
        blob = json.dumps(payload, default=str, indent=1).encode()
        info = tarfile.TarInfo("export.json")
        info.size = len(blob)
        tar.addfile(info, io.BytesIO(blob))
        for rel in sorted(keep_files):
            source = var / rel
            if source.is_file():
                tar.add(source, arcname=f"var/{rel}")
            else:
                missing.append(rel)

    print(f"\nRead {len(files)} database(s) under {backend}\n")
    for table in sorted(set(out) | set(left)):
        print(f"  {table:22} kept {len(out.get(table, {})):5}   left (generated) {left.get(table, 0):5}")
    print(f"  {'files':22} kept {len(keep_files) - len(missing):5}"
          + (f"   missing on disk {len(missing)}" if missing else ""))
    print(f"\nWrote {target}")
    print("Nothing on this box was changed. Copy the file to the new server, then there:")
    print(f"  cd /srv/liner && sudo -u liner make import-ops FILE=/path/to/{target.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
