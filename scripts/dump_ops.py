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
from app.db import engine_for, ops_engine  # noqa: E402
from app.models.ops import OPS_TABLES  # noqa: E402


def _files_for(slug: str) -> list[pathlib.Path]:
    if slug == "(ops)":
        return _ops_files()
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


#: The name this script uses for Liner's own database in its output and in the
#: dump. Not a store slug -- no profile is ever called this -- so it cannot
#: collide with a dealership.
OPS = "(ops)"


def _ops_files() -> list[pathlib.Path]:
    url = settings.ops_database_url
    if not url.startswith("sqlite") or "///" not in url:
        return []
    path = pathlib.Path(url.split("///", 1)[-1])
    return [path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")]


def stores() -> list[str]:
    """**`ops.db` first**, then the default store, then every other profile.

    Liner's own database has to be in this list or the tool misses the very
    rows it exists to protect: the `ops_` tables moved there, and a dump that
    only walked the stores came back with four rows and looked like a quiet
    week rather than a broken backup.

    The stores are still walked, and that is not vestigial. Every file seeded
    before the split carries orphaned `ops_` tables -- `founder@` once per
    dealership, and any demo request written while that store was the default.
    Those rows are real and this is the only thing that will ever find them.
    """
    found = [OPS] if _present(settings.ops_database_url, _ops_files()) else []
    if _present(settings.database_url_for(""), _files_for("")):
        found.append("")
    for slug in settings.store_slugs:
        if _present(settings.database_url_for(slug), _files_for(slug)):
            found.append(slug)
    return found


def _present(url: str, files: list[pathlib.Path]) -> bool:
    """Whether there is a database here to read -- its file on SQLite, the
    database itself on a server. Neither question creates one."""
    if files:
        return files[0].exists()
    from app import pg

    return pg.is_postgres(url) and pg.exists(url)


def pg_urls() -> list[tuple[str, str]]:
    """Every server database this deployment names, labelled, or []."""
    from app import pg

    named = [(OPS, settings.ops_database_url), ("default store", settings.database_url_for(""))]
    named += [(slug, settings.database_url_for(slug)) for slug in settings.store_slugs]
    return [(label, url) for label, url in named if pg.is_postgres(url) and pg.exists(url)]


def dump_store(slug: str) -> dict:
    """Every ops table in one database, by table name."""
    engine = ops_engine() if slug == OPS else engine_for(slug)
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
    if "--files" in sys.argv and pg_urls():
        # On a server there are no files to copy: the backup is `pg_dump`,
        # one per database, in its own format so `pg_restore` can read it.
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        print("\nDump each database before dropping anything:\n")
        print(f"  mkdir -p backup-{stamp}")
        for label, url in pg_urls():
            from app import pg

            print(f"  pg_dump --format=custom --dbname='{pg.for_libpq(url)}' "
                  f"--file=backup-{stamp}/{pg.name_of(url)}.dump   # {label}")
        print("\n(Those lines carry the database password; run them, do not paste them anywhere.)\n")
        return 0
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
    strays = []
    print()
    for slug in stores():
        data = dump_store(slug)
        total = sum(len(v) for v in data.values())
        grand += total
        payload["stores"][slug or "(default)"] = data
        counts = ", ".join(f"{k.removeprefix('ops_')}={len(v)}" for k, v in data.items() if v)
        label = slug if slug else "(default)"
        if slug != OPS and data:
            # The tables are *present* in this store's file, whether or not they
            # hold anything -- which is the fact `make prune-ops` acts on.
            strays.append(label)
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
    if strays:
        # These are files seeded before the split. Nothing reads them any more,
        # so this is tidying rather than a fault -- but it is worth naming,
        # because a table nobody has looked at is where a demo request goes to
        # be forgotten, and that is the whole reason this tool walks the stores.
        print(f"  {len(strays)} store(s) still carry the pre-split ops_ tables: "
              f"{', '.join(strays)}")
        print("  Now that this dump exists, `make prune-ops` reports what it would")
        print("  drop and `make prune-ops ARGS=--apply` removes them. It refuses any")
        print("  store holding a row this dump's target database does not have.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
