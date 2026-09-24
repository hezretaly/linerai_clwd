"""Copy every SQLite database into Postgres, one database per store.

    make to-postgres                                # the plan: what goes where
    make to-postgres ARGS=--apply                   # copy it
    make to-postgres ARGS="--apply --from /srv/old" # from files taken off another box
    make to-postgres ARGS="--apply --default-as riverside"

Run it with the **new server's** settings in the environment --
`DATABASE_URL_TEMPLATE`, `OPS_DATABASE_URL` and (if the default store is
copied) `DATABASE_URL` pointing at Postgres -- and the SQLite files where a
checkout keeps them: `liner.db`, `var/stores/<slug>.db` and `var/ops.db` under
`backend/`, or under `--from`.

**Each target is built by the migrations before a row goes in**, the same way
the server builds one, so a copied database and a fresh one are the same
schema -- the one `make smoke` compares with the models.

**Rows are read through the models**, not as raw SQLite values: a timestamp
stored as text comes out a `datetime` and a 0/1 comes out a boolean, which is
what Postgres will take. Parents before children, in the order the foreign
keys require. `events` keeps its ids, because a dashboard replays with
`?since=<id>`, and its counter is moved past them afterwards.

**It refuses a target that already holds rows** unless `--replace`, which
drops and rebuilds that database first. Copying twice into one is how a store
ends up with every buyer twice.

`--default-as <slug>` copies `liner.db` -- the unprefixed default store --
into that store's database instead of `DATABASE_URL`'s: on a server where
every dealership has a name, the one that used to be the default gets its own.

Nothing is written to the SQLite files, and nothing leaves this machine except
the rows, to the database server the settings name.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine, func, inspect, select, text  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app import migrate, pg  # noqa: E402
from app.config import BACKEND_DIR, settings  # noqa: E402
from app.db import engine_args  # noqa: E402

BATCH = 500


def plan(source_dir: pathlib.Path, default_as: str) -> list[tuple[str, pathlib.Path, str, str]]:
    """(label, SQLite file, Postgres URL, kind) for every file that exists."""
    out = []
    default_file = source_dir / "liner.db"
    if default_file.exists():
        url = settings.database_url_for(default_as) if default_as else settings.database_url
        out.append((f"default store{f' -> {default_as}' if default_as else ''}",
                    default_file, url, "store"))
    for slug in settings.store_slugs:
        path = source_dir / "var" / "stores" / f"{slug}.db"
        if path.exists() and slug != default_as:
            out.append((slug, path, settings.database_url_for(slug), "store"))
    ops_file = source_dir / "var" / "ops.db"
    if ops_file.exists():
        out.append(("Liner's own (ops)", ops_file, settings.ops_database_url, "ops"))
    return out


def seeded(path: pathlib.Path, kind: str) -> bool:
    """A file with the kind's tables in it -- a stray empty one is skipped."""
    engine = create_engine(f"sqlite:///{path}", poolclass=NullPool)
    try:
        return migrate.MARKER[kind] in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def rows_in(engine, tables) -> int:  # noqa: ANN001
    with engine.connect() as conn:
        present = set(inspect(conn).get_table_names())
        return sum(
            conn.execute(select(func.count()).select_from(t)).scalar() or 0
            for t in tables if t.name in present
        )


def copy_one(label: str, path: pathlib.Path, url: str, kind: str, replace: bool) -> dict:
    tables = migrate.metadata(kind).sorted_tables
    if replace:
        pg.drop(url)
    made = pg.create(url)
    target = create_engine(url, **engine_args(url))
    source = create_engine(f"sqlite:///{path}", poolclass=NullPool)
    report = {"label": label, "made": made, "tables": {}, "skipped": [], "left": []}
    try:
        migrate.ensure(target, kind)
        if rows_in(target, tables):
            raise SystemExit(
                f"{label}: {pg.safe(url)} already holds rows. Copying again would put every "
                "row in twice. --replace drops and rebuilds it first."
            )
        with source.connect() as src, target.begin() as dst:
            present = {name: {c["name"] for c in inspect(src).get_columns(name)}
                       for name in inspect(src).get_table_names()}
            wanted = {t.name for t in tables}
            report["left"] = sorted(
                name for name in present
                if name not in wanted and name != "alembic_version"
                and (src.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar() or 0)
            )
            for table in tables:
                if table.name not in present:
                    report["skipped"].append(table.name)
                    continue
                columns = [c for c in table.columns if c.name in present[table.name]]
                result = src.execute(select(*columns))
                copied = 0
                while True:
                    batch = result.mappings().fetchmany(BATCH)
                    if not batch:
                        break
                    dst.execute(table.insert(), [dict(row) for row in batch])
                    copied += len(batch)
                report["tables"][table.name] = copied
            if kind == "store":
                # The counter behind `events.id`, moved past the ids copied in:
                # otherwise the next event takes id 1 and a dashboard replaying
                # `?since=` from the old numbering never sees it.
                dst.execute(text(
                    "SELECT setval(pg_get_serial_sequence('events', 'id'), "
                    "COALESCE((SELECT MAX(id) FROM events), 0) + 1, false)"
                ))
        # Counted again on both sides, after the commit, so what is reported
        # is what the server has rather than what was sent.
        with source.connect() as src, target.connect() as dst:
            mismatched = []
            for table in tables:
                if table.name in report["skipped"]:
                    continue
                a = src.execute(select(func.count()).select_from(table)).scalar()
                b = dst.execute(select(func.count()).select_from(table)).scalar()
                if a != b:
                    mismatched.append(f"{table.name}: {a} in SQLite, {b} in Postgres")
            report["mismatched"] = mismatched
    finally:
        source.dispose()
        target.dispose()
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="copy; without it, only the plan")
    ap.add_argument("--replace", action="store_true",
                    help="drop and rebuild a target that already holds rows")
    ap.add_argument("--from", dest="source", default=str(BACKEND_DIR),
                    help="the directory holding liner.db and var/ (default: backend/)")
    ap.add_argument("--default-as", default="",
                    help="copy liner.db into this store's database instead of DATABASE_URL's")
    args = ap.parse_args()

    if args.default_as and args.default_as not in settings.store_slugs:
        print(f"--default-as {args.default_as}: no such profile; known: {settings.store_slugs}")
        return 2
    pairs = plan(pathlib.Path(args.source), args.default_as)
    if not pairs:
        print(f"No SQLite databases under {args.source}.")
        return 1
    not_pg = [label for label, _, url, _ in pairs if not pg.is_postgres(url)]
    if not_pg:
        print("These would be copied into something that is not Postgres -- set "
              "DATABASE_URL_TEMPLATE, OPS_DATABASE_URL and DATABASE_URL first:")
        for label in not_pg:
            print(f"  {label}")
        return 2

    print(f"\n{'Copying' if args.apply else 'Would copy'}:\n")
    for label, path, url, kind in pairs:
        print(f"  {label:34} {path}\n  {'':34} -> {pg.safe(url)}")
    if not args.apply:
        print("\nNothing written. Run again with --apply.\n")
        return 0

    failed = 0
    for label, path, url, kind in pairs:
        if not seeded(path, kind):
            print(f"\n{label}: {path} holds no {kind} tables -- a stray file, skipped.")
            continue
        report = copy_one(label, path, url, kind, args.replace)
        total = sum(report["tables"].values())
        print(f"\n{label}: {total} rows into {len(report['tables'])} tables"
              f"{' (database created)' if report['made'] else ''}")
        if report["skipped"]:
            print(f"  not in the SQLite file, left empty: {', '.join(report['skipped'])}")
        if report["left"]:
            # Not part of this schema but holding rows: the `ops_` tables that
            # predate Liner's own database, most likely. `make restore-ops`
            # reads those into ops.db, before this runs, and nothing else will.
            print(f"  NOT COPIED, and holding rows: {', '.join(report['left'])} -- "
                  "run `make restore-ops` against these files first if they matter")
        if report["mismatched"]:
            failed += 1
            print("  COUNTS DIFFER: " + "; ".join(report["mismatched"]))
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
