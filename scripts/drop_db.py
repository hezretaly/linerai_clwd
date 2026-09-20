"""Delete one store's database, and only that one.

`make reset-db` used to be `rm -f backend/liner.db*`, which was right while
there was exactly one file. With a database per store that line deletes the
wrong one: `DEALERSHIP=alsbou make reset-db` would leave Alsbou's rows
untouched and quietly destroy whatever the unnamed default holds instead.

So the path is computed the same way the application computes it, rather than
written out a second time. A hardcoded path here and a `database_url_for()`
there is exactly the pair that drifts, and the direction it drifts in is
"deleted the database you were not working on".

    python scripts/drop_db.py           # the store DEALERSHIP= names
    python scripts/drop_db.py --list    # every store, and whether it is seeded
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))

from app.config import settings  # noqa: E402
from app.stores import known_stores  # noqa: E402


def files_for(slug: str) -> list[pathlib.Path]:
    """The database and the two sidecars WAL mode writes beside it.

    All three, because deleting only `liner.db` leaves `-wal` holding
    committed transactions: SQLite replays them into the fresh file and the
    "clean" reseed comes back carrying rows from the database that was
    supposed to be gone.
    """
    url = settings.database_url_for(slug)
    if not url.startswith("sqlite") or "///" not in url:
        return []
    path = pathlib.Path(url.split("///", 1)[-1])
    return [path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")]


def rows(slug: str) -> str:
    """Size across the database *and* its sidecars.

    The main file alone is misleading: WAL keeps recent writes in `-wal` until
    a checkpoint, so a freshly seeded 486-car store reports 4 KB and reads as
    empty. Summing all three is the honest number -- and it is the same reason
    `files_for` deletes all three rather than just the one.
    """
    paths = files_for(slug)
    if not paths:
        return "not SQLite"
    if not paths[0].exists():
        return "not seeded"
    # A file with no tables in it is the stray a pre-fix 500 left behind, not
    # a store: listed as what it is, so the operator reaches for `reset-db`
    # rather than wondering why a "seeded" dealership answers nothing. Asked
    # read-only, which creates nothing -- the same probe `db.has_database`
    # makes, for the same reason.
    try:
        with sqlite3.connect(f"file:{paths[0]}?mode=ro", uri=True) as conn:
            seeded = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'dealership'"
            ).fetchone() is not None
    except sqlite3.Error:
        seeded = False
    total = sum(p.stat().st_size for p in paths if p.exists())
    if not seeded:
        return f"{total // 1024} KB file with no tables -- not seeded; run: DEALERSHIP={slug} make reset-db"
    return f"{total // 1024} KB"


def main() -> int:
    if "--list" in sys.argv:
        slugs = known_stores()
        current = settings.dealership.strip()
        print(f"\nStores in {settings.dealership_dir}:\n")
        for slug in slugs:
            here = " <- DEALERSHIP=" if slug == current else ""
            print(f"  {slug:22} {rows(slug):>12}   {files_for(slug)[0]}{here}")
        if not current:
            print(f"\n  (no DEALERSHIP set, so the default store is {settings.database_url})")
        print()
        return 0

    slug = settings.dealership.strip()
    removed = []
    for path in files_for(slug):
        if path.exists():
            path.unlink()
            removed.append(path.name)
    label = slug or "the default store"
    print(f"Deleted {len(removed)} file(s) for {label}: {', '.join(removed) or 'nothing to delete'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
