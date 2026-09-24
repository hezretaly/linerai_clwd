"""Engine and session factory.

SQLite in WAL mode. Everything goes through SQLAlchemy and no SQLite-only SQL is
used anywhere, so moving to Postgres is a connection-string change plus a data
copy (see the plan, §4).
"""

from __future__ import annotations

import getpass
import grp
import os
import pwd
import sqlite3
from collections.abc import Iterator
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy import create_engine

from app.config import settings


class Base(DeclarativeBase):
    """The dealership's tables. One set of these per store."""


class OpsBase(DeclarativeBase):
    """Liner's own tables, in Liner's own database.

    A *second* metadata rather than the same one pointed at a different
    engine, and that is the part doing the work: `Base.metadata.create_all`
    runs against every store, so anything sharing it is built into every
    store's file -- empty, unread, and writable by anything that forgets which
    session it is holding. With two metadatas a store's `create_all` cannot
    build an ops table even by accident.

    The cost is that no query can join across the line. That is acceptable
    because nothing needs to: the only foreign keys among the `ops_` tables
    point at each other, and nothing on the dealership's side points back.
    """


def utcnow() -> datetime:
    """Naive UTC. Stored as TIMESTAMP; every producer and consumer treats it as UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------
# One database per store
#
# `current_store` names whichever dealership this request is for. It is a
# ContextVar rather than an argument threaded through every call because the
# alternative is changing the signature of everything that touches the
# database -- and the one that gets missed is the one that reads the wrong
# dealership's buyers, silently, which is the whole failure this split exists
# to prevent.
#
# The default is `settings.dealership`, so a process that serves one store
# behaves exactly as it did: an unprefixed `/api/...` request is that store's.
# --------------------------------------------------------------------------

current_store: ContextVar[str] = ContextVar("current_store", default="")

#: The store this request's *host* names -- `alsbou.linerai.us` -- or "" when
#: the store, if any, came from the path. The two are told apart because a
#: link composed for a request that arrived on the dealership's own subdomain
#: must not repeat the store in its path, and one for `linerai.us/alsbou/...`
#: must.
current_host: ContextVar[str] = ContextVar("current_host", default="")

_engines: dict[str, Engine] = {}
_sessions: dict[str, sessionmaker] = {}


def active_store() -> str:
    """The store this request is for, falling back to the configured one."""
    return current_store.get() or settings.dealership.strip()


def engine_for(slug: str = "") -> Engine:
    """The engine for one store, made once and kept.

    Cached per slug: SQLite opens are cheap but a connection pool per request
    is not, and WAL mode wants a stable pool rather than a new file handle on
    every call.
    """
    slug = (slug or "").strip()
    if slug not in _engines:
        url = settings.database_url_for(slug)
        if url.startswith("sqlite"):
            # The directory has to exist before SQLite will create the file,
            # and WAL writes two sidecars beside it.
            path = Path(url.split("///", 1)[-1])
            path.parent.mkdir(parents=True, exist_ok=True)
        args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engines[slug] = create_engine(url, connect_args=args, future=True)
    return _engines[slug]


def session_factory(slug: str = "") -> sessionmaker:
    slug = (slug or "").strip()
    if slug not in _sessions:
        _sessions[slug] = sessionmaker(
            bind=engine_for(slug), autoflush=False, expire_on_commit=False, future=True
        )
    return _sessions[slug]


#: The default store's engine, kept under its old name so every script,
#: migration helper and `create_all()` caller that imported it still works.
engine = engine_for(settings.dealership.strip())


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """Applied to every engine, not only the first.

    Listening on the `Engine` class rather than one instance is what makes
    that true -- registered against a single engine, a second store would run
    without WAL, without foreign keys and without a busy timeout, and the
    missing `foreign_keys=ON` is the one that turns a bad delete into silent
    orphan rows rather than an error.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    except Exception:
        # Not SQLite. Nothing to set, and a Postgres connection must not fail
        # because of a pragma that does not exist there.
        pass
    finally:
        cursor.close()


class _StoreSession:
    """`SessionLocal()` that opens against whichever store is active.

    Kept callable under the old name because roughly forty call sites do
    `with SessionLocal() as db:` -- scripts, the seed, the tickers. Renaming
    them all would be a large diff whose only purpose is to say the same thing
    differently.
    """

    def __call__(self, slug: str | None = None) -> Session:
        return session_factory(active_store() if slug is None else slug)()


SessionLocal = _StoreSession()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --------------------------------------------------------------------------
# Liner's own database
#
# One file, never per store. `/ops` is ours: the demos people booked with us,
# the mail we wrote, who rang the number. None of it belongs to a dealership
# and none of it should multiply when a second one is added.
# --------------------------------------------------------------------------

_ops_engine: Engine | None = None
_ops_sessions: sessionmaker | None = None


def ops_engine() -> Engine:
    global _ops_engine
    if _ops_engine is None:
        url = settings.ops_database_url
        if url.startswith("sqlite") and "///" in url:
            Path(url.split("///", 1)[-1]).parent.mkdir(parents=True, exist_ok=True)
        args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _ops_engine = create_engine(url, connect_args=args, future=True)
    return _ops_engine


def ops_session() -> Session:
    """A session on Liner's own database.

    Deliberately *not* a FastAPI dependency with a matching `get_db` shape.
    An ops endpoint has to say which side it is reading, and a dependency
    named like the other one is exactly how somebody wires up the wrong
    database and gets a plausible empty list back.
    """
    global _ops_sessions
    if _ops_sessions is None:
        _ops_sessions = sessionmaker(
            bind=ops_engine(), autoflush=False, expire_on_commit=False, future=True
        )
    return _ops_sessions()


def get_ops_db() -> Iterator[Session]:
    db = ops_session()
    try:
        yield db
    finally:
        db.close()


def create_ops_all() -> None:
    """Build the six `ops_` tables, in one place, once."""
    from app.models import ops  # noqa: F401  (registers the mappers)

    try:
        OpsBase.metadata.create_all(bind=ops_engine())
    except OperationalError as exc:
        if "readonly database" not in str(exc) and "unable to open" not in str(exc):
            raise
        raise RuntimeError(f"{exc.orig}{readonly_help()}") from None


def sqlite_path(slug: str | None = None) -> Path | None:
    """The database file for one store, when this deployment is on SQLite."""
    url = settings.database_url_for(active_store() if slug is None else slug)
    if not url.startswith("sqlite"):
        return None
    return Path(url.split("///", 1)[-1]) if "///" in url else None


def has_database(slug: str | None = None) -> bool:
    """Whether this store has a database yet — asked without creating one.

    **Connecting to SQLite creates the file**, so anything that walks the
    store list to look something up has to ask this first. Two places do:
    `locate_store` on every unprefixed sign-in, and `ops_inbox._each` on every
    `/ops` read. Both handled the resulting `no such table` correctly and both
    still left an empty 4KB database behind for each unseeded profile — which
    `make stores` then reports "not seeded" while it sits there looking like a
    dealership, and which makes deleting a store's file not stay deleted.

    One function rather than the same three lines in each, because this was
    written twice in one afternoon and the second copy is how one of them
    stops asking. Same rule `photo_path` follows: a lookup must not create the
    thing it is looking for.

    A deployment that is not on SQLite has no file to test, so it answers True
    and the caller's error handling stays the path that covers it.

    **A file with no tables in it is not a database.** The stray 4 KB files
    this function exists to stop being created were, for a while, created
    anyway -- and on a host that has one, "does the file exist" says yes,
    the store is opened, and the first query fails `no such table`: a 500 on
    the storefront that the 503 for a missing file was written to prevent.
    So the question is whether the `dealership` table is there, asked
    **read-only** -- `mode=ro` on an existing file creates nothing and caches
    no engine, which is the property the rest of this docstring is about.
    """
    path = sqlite_path(slug)
    if path is None:
        return True
    if not path.exists():
        return False
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'dealership'"
            ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def readonly_help() -> str:
    """What "attempt to write a readonly database" actually means here.

    SQLite reports one error for every way the OS refused a write, and the
    traceback that surfaces is sixty lines of SQLAlchemy with the cause
    nowhere in it. The cause is almost always ownership, and it takes *three*
    things rather than one: the database file, the directory (WAL mode creates
    `liner.db-wal` and `liner.db-shm` alongside it), and those sidecars once
    they exist. Any one of them owned by somebody else fails the write.

    Measured as an unprivileged user against a real database, inserting a row
    rather than only opening it: file root-owned in a writable directory
    fails, and a writable file in a root-owned directory fails. Checking only
    `ls -l liner.db` is how this gets diagnosed as fixed when it is not --
    which is why the message below prints every one of them and the fix is a
    recursive chown rather than a single file.
    """
    path = sqlite_path()
    if path is None:
        return ""
    directory = path.parent
    try:
        who = f"{getpass.getuser()} (uid {os.getuid()})"
    except Exception:  # pragma: no cover - no passwd entry in some containers
        who = f"uid {os.getuid()}"

    def owner(target: Path) -> str:
        try:
            stat = target.stat()
            return f"{pwd.getpwuid(stat.st_uid).pw_name}:{grp.getgrgid(stat.st_gid).gr_name}"
        except Exception:
            return "unknown"

    return (
        f"\n\nThe database is on disk but this process cannot write to it."
        f"\n  running as : {who}"
        f"\n  database   : {path}  (owned by {owner(path)})"
        f"\n  directory  : {directory}  (owned by {owner(directory)})"
        f"\n\nAll of these have to belong to the user the service runs as -- the "
        f"file, the directory, and the `-wal`/`-shm` sidecars SQLite writes "
        f"beside it. Chowning one is not enough, so do the tree:"
        f"\n  sudo chown -R liner:liner /srv/liner"
        f"\n  sudo systemctl restart liner"
        f"\n\nIf that user is not `liner`, check `User=` in the systemd unit. A "
        f"service running as root leaves root-owned files behind on every write, "
        f"and this comes back the next time anything runs as anyone else."
    )


def create_all(slug: str | None = None) -> None:
    """Build the dealership's schema in one store's file.

    `Base.metadata` and nothing else, which is the whole reason `OpsBase` is a
    second metadata. This used to build the *whole* thing into every store --
    `ops_` tables included -- so `founder@` existed once per dealership and a
    demo somebody booked with us landed in whichever file happened to be
    active. Two metadatas mean a store's `create_all` cannot build an ops
    table even by accident, rather than a rule somebody has to remember.

    The files seeded before the split still carry those six tables, with real
    rows in them. They are not cleaned up here: deleting a table that might
    hold the only copy of a demo request is not a migration to run silently at
    startup. `make dump-ops` walks every store and finds them, and
    `make restore-ops` reads them into `ops.db`, de-duplicating the `founder@`
    copies on the address.
    """
    from app import models  # noqa: F401  (registers the mappers)

    try:
        Base.metadata.create_all(bind=engine_for(active_store() if slug is None else slug))
    except OperationalError as exc:
        if "readonly database" not in str(exc) and "unable to open" not in str(exc):
            raise
        raise RuntimeError(f"{exc.orig}{readonly_help()}") from None
