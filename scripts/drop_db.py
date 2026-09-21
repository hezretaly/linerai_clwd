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

`--list` also prints, per store, the address its mail leaves from and who can
sign in as a manager. Those are the two questions somebody actually opens this
for -- "which mailbox is this dealership's" and "what do I log in with" -- and
the answers otherwise live in a profile file and a database respectively, with
nothing putting them side by side.
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


def sends_from(slug: str) -> str:
    """The address this store's buyer mail leaves from, and comes back to.

    Asked of the sender rather than composed here, because `mailbox@domain`
    is only the first rung -- `SENDING_FROM` and the derived `sales@` are the
    other two, and a second implementation of "which mailbox is this
    dealership's" is how a listing promises an address the send does not use.
    """
    from app import mailboxes
    from app.integrations.registry import get_email_sender

    try:
        with mailboxes.using(slug):
            return get_email_sender().default_address("dealership")
    except Exception:  # a listing must not fail over an unconfigured sender
        return ""


def managers(slug: str) -> str:
    """Who can sign in to this store as a manager.

    Read out of the file read-only, like `rows` -- a listing must not create a
    database, and opening one through the application's engine would. The
    password is not here and cannot be: only its hash is stored. A reseed
    prints a new one, or `make set-password EMAIL=...` sets it.
    """
    paths = files_for(slug)
    if not paths or not paths[0].exists():
        return ""
    try:
        with sqlite3.connect(f"file:{paths[0]}?mode=ro", uri=True) as conn:
            found = [
                row[0] for row in conn.execute(
                    "SELECT email FROM users WHERE role = 'manager' AND active = 1"
                ).fetchall()
            ]
    except sqlite3.Error:
        return ""
    return ", ".join(found)


def _detail(slug: str) -> None:
    """The two facts somebody opens this listing to find.

    A store with a mailbox and no `SENDING_DOMAIN` names the setting rather
    than printing nothing: "this dealership has no address" and "this
    deployment has no domain to put one on" are different problems with
    different fixes, and a blank line says neither.
    """
    from app import mailboxes, profile

    mail, people = sends_from(slug), managers(slug)
    if mail:
        print(f"      mail    {mail}")
    else:
        with mailboxes.using(slug):
            box = profile.mailbox()
        if box:
            print(f"      mail    {box}@ -- no SENDING_DOMAIN set, so nothing finishes the address")
    if people:
        print(f"      sign in {people}")


def main() -> int:
    if "--list" in sys.argv:
        slugs = known_stores()
        current = settings.dealership.strip()
        print(f"\nStores in {settings.dealership_dir}:\n")
        for slug in slugs:
            here = " <- DEALERSHIP=" if slug == current else ""
            print(f"  {slug:22} {rows(slug):>12}   {files_for(slug)[0]}{here}")
            _detail(slug)
        if not current:
            print(f"\n  (no DEALERSHIP set, so the default store is {settings.database_url})")
            _detail("")
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
