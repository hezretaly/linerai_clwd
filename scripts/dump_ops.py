"""Save every `ops_` row to a JSON file, from every store that holds any.

    make dump-ops                 # -> backend/var/ops-dump-<timestamp>.json
    make dump-ops OUT=path.json

**It scans every store, not just one, and that is the point.** `/ops` reads
the default store, but `create_all` builds the whole metadata into each
store's file, so a deployment that has run with `DEALERSHIP=` set to different
values at different times can have ops rows in more than one place. A dump
that assumed a single file would silently miss them, and you would only find
out after dropping the databases.

Written against `OPS_TABLES` rather than a list of its own -- that tuple is
already the answer to "what is ours", and a second copy is how a table added
later gets left out of the backup and nothing says so.

Read through SQLAlchemy Core rather than the ORM: this runs against databases
that are about to be replaced, and a mapper that has moved on from the schema
on disk would fail on exactly the rows worth saving. Columns are taken from
the file itself, so a table with an unexpected shape still comes out.

WHAT THIS IS NOT. It is a readable, re-importable record of the ops tables,
not a backup of the whole system. Before dropping anything, copy the database
files as well -- see `--files`, which prints the exact command including the
`-wal` and `-shm` sidecars. Copying `liner.db` alone can lose recent writes:
WAL keeps them beside the file until a checkpoint, which is why a freshly
seeded 486-car store reports 4 KB.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import MetaData, Table, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import engine_for  # noqa: E402
from app.models.ops import OPS_TABLES  # noqa: E402


def _files_for(slug: str) -> list[pathlib.Path]:
    url = settings.database_url_for(slug)
    if not url.startswith("sqlite") or "///" not in url:
        return []
    path = pathlib.Path(url.split("///", 1)[-1])
    return [path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")]


def _jsonable(value):
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def stores() -> list[str]:
    """The default first, then every profile with a database on disk."""
    found = [""] if _files_for("")[0].exists() else []
    for slug in settings.store_slugs:
        if _files_for(slug)[0].exists():
            found.append(slug)
    return found


def dump_store(slug: str) -> dict:
    """Every ops table in one store's file, by table name."""
    engine = engine_for(slug)
    meta = MetaData()
    out: dict[str, list[dict]] = {}
    with engine.connect() as conn:
        for name in OPS_TABLES:
            try:
                table = Table(name, meta, autoload_with=conn)
            except Exception:
                # The table is not in this file at all. Not an error: a store
                # seeded before the split has no reason to carry it.
                continue
            rows = conn.execute(select(table)).mappings().all()
            out[name] = [{k: _jsonable(v) for k, v in row.items()} for row in rows]
    return out


def main() -> int:
    if "--files" in sys.argv:
        print("\nCopy the database files themselves before dropping anything.")
        print("All three per store: WAL keeps recent writes in the sidecars, so")
        print("copying the .db alone can lose them.\n")
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        print(f"  mkdir -p backend/var/backup-{stamp}")
        for slug in stores():
            for path in _files_for(slug):
                if path.exists():
                    print(f"  cp {path} backend/var/backup-{stamp}/")
        print()
        return 0

    out = pathlib.Path("backend/var")
    target = None
    for arg in sys.argv[1:]:
        if not arg.startswith("-"):
            target = pathlib.Path(arg)
    if target is None:
        out.mkdir(parents=True, exist_ok=True)
        target = out / f"ops-dump-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"

    payload = {
        "taken_at": dt.datetime.now().isoformat(timespec="seconds"),
        "tables": list(OPS_TABLES),
        "default_store": settings.dealership.strip(),
        "stores": {},
    }

    grand = 0
    print()
    for slug in stores():
        data = dump_store(slug)
        total = sum(len(v) for v in data.values())
        grand += total
        payload["stores"][slug or "(default)"] = data
        counts = ", ".join(f"{k.removeprefix('ops_')}={len(v)}" for k, v in data.items() if v)
        label = slug or "(default)"
        print(f"  {label:22} {total:5} rows   {counts or 'nothing'}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    size = target.stat().st_size

    print(f"\n  {grand} ops row(s) from {len(payload['stores'])} store(s)")
    print(f"  -> {target}  ({size // 1024 or 1} KB)\n")
    if grand == 0:
        print("  Nothing to save. If that is a surprise, check `make stores` --")
        print("  the rows are in whichever file DEALERSHIP= pointed at.\n")
    else:
        # Said every time rather than only when it matters: this file is the
        # thing standing between a migration and somebody's demo request.
        print("  This is the ops tables only. Before dropping anything, also run")
        print("  `make dump-ops ARGS=--files` and copy the databases themselves.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
