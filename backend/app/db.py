"""Engine and session factory.

SQLite in WAL mode on a laptop, Postgres on a server -- one database per store
either way, and Liner's own beside them. Everything goes through SQLAlchemy and
no SQLite-only SQL is used anywhere; what differs between the two is here and
in nothing that reads a row:

* The pragmas are SQLite's alone, and a Postgres connection is never handed
  one: a failed statement leaves a Postgres transaction aborted, and the pool
  would hand that connection to the next request.
* `String(n)` is an unbounded `VARCHAR` on Postgres. SQLite never enforced a
  length, so every row on disk and every writer was built without one; a
  Postgres that enforced them would fail an insert this app has always made,
  and the lengths stay in the models as a statement of intent.
* A NUL character is taken out of any string before it is written, on both.
  Postgres refuses one in text and the writer would fail; SQLite kept it, and
  a mail part decoded in the wrong charset is exactly where one appears.
* Whether a store has a database is asked of the file on SQLite and of the
  server on Postgres, and neither asking creates one.
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

from sqlalchemy import Engine, MetaData, String, event, inspect, text
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy import create_engine

from app.config import settings


#: How a constraint is named when the model does not name it. Without one,
#: SQLite stores them unnamed and Postgres invents names of its own, and a
#: migration that has to drop or alter a constraint later cannot say which --
#: so every database built by the migrations names them the same way.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """The dealership's tables. One set of these per store."""

    metadata = MetaData(naming_convention=NAMING)


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

    metadata = MetaData(naming_convention=NAMING)


#: What a query against a table this database does not have raises: SQLite
#: says `OperationalError` ("no such table"), Postgres `ProgrammingError`
#: ("relation does not exist"). Every handler written for the first missed
#: the second, so on Postgres the path it guarded became a 500.
MISSING_TABLE = (OperationalError, ProgrammingError)


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
        _engines[slug] = create_engine(url, future=True, **engine_args(url))
    return _engines[slug]


def engine_args(url: str) -> dict:
    """How to open one engine, by what it is.

    **On Postgres every store has a pool of its own**, so the pools are kept
    small: a server with three groups and Liner's own database holds four of
    them. `pool_pre_ping` because a server restart otherwise hands the next
    request a dead connection. `lock_timeout` stands in for SQLite's
    `busy_timeout` -- without it a Postgres lock is waited on for ever -- and
    the idle-in-transaction limit is generous because a chat turn holds its
    transaction across the model's reply.
    """
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 5,
        "pool_recycle": 1800,
        "connect_args": {
            "options": "-c timezone=UTC -c lock_timeout=5000 "
                       "-c idle_in_transaction_session_timeout=300000",
        },
    }


def is_postgres(db_or_engine) -> bool:  # noqa: ANN001 - a Session or an Engine
    """Whether this session or engine talks to Postgres."""
    bind = db_or_engine.get_bind() if isinstance(db_or_engine, Session) else db_or_engine
    return bind.dialect.name == "postgresql"


@compiles(String, "postgresql")
def _unbounded_varchar(type_, compiler, **kw) -> str:  # noqa: ANN001
    """`String(n)` as `VARCHAR`, unbounded, on Postgres (see the module doc)."""
    return "VARCHAR"


@event.listens_for(Session, "before_flush")
def _no_nul(session, flush_context, instances) -> None:  # noqa: ANN001
    """Take NUL characters out of every string about to be written.

    Read off each object's *loaded* state rather than through `getattr`, so an
    expired attribute is not fetched just to be looked at.
    """
    for obj in list(session.new) + list(session.dirty):
        state = inspect(obj)
        for column in state.mapper.columns:
            if not isinstance(column.type, String):
                continue
            key = state.mapper.get_property_by_column(column).key
            value = state.dict.get(key)
            if isinstance(value, str) and "\x00" in value:
                setattr(obj, key, value.replace("\x00", ""))


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
    # **SQLite's alone.** This used to try the pragmas on every connection and
    # swallow the failure -- which on Postgres leaves the transaction the
    # failed statement opened *aborted*, and SQLAlchemy has already run its
    # own first-connect checks by then, so the pool accepted the connection
    # and the next request's first query failed with "current transaction is
    # aborted". Asked of the driver's connection type rather than attempted.
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
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
        _ops_engine = create_engine(url, future=True, **engine_args(url))
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
    """Bring Liner's own database to the newest migration (`app/migrate.py`).

    Kept under the name it has always had, because every caller -- the boot,
    the seed, `add-owners`, `restore-ops` -- means exactly "make sure the ops
    schema is there", and that is still what it does.
    """
    from app import migrate

    try:
        migrate.ensure(ops_engine(), "ops")
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
        return _server_has_database(active_store() if slug is None else slug)
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


#: Stores whose database answered, and when. The middleware asks on every
#: prefixed request, so a yes is kept for a minute; a no is never kept, so a
#: store seeded a moment ago is served on the next request.
_present: dict[str, float] = {}


def _server_has_database(slug: str) -> bool:
    """`has_database` for a database server: the database exists and holds a
    dealership. Connecting to a Postgres database that does not exist fails
    rather than creating it, so this can simply try."""
    import time

    seen = _present.get(slug)
    if seen is not None and time.monotonic() - seen < 60:
        return True
    try:
        with engine_for(slug).connect() as conn:
            found = conn.execute(
                text("SELECT to_regclass('public.dealership') IS NOT NULL")
            ).scalar()
    except DBAPIError:
        return False
    if found:
        _present[slug] = time.monotonic()
    return bool(found)


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
    """Bring one store's database to the newest migration (`app/migrate.py`).

    The name is kept for the same reason `create_ops_all`'s is. What changed
    is underneath: this was `Base.metadata.create_all`, which adds a table to
    a database that already exists but never a column; the migrations do
    both, and a database built the old way is adopted in place.

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
    from app import migrate

    try:
        migrate.ensure(engine_for(active_store() if slug is None else slug), "store")
    except OperationalError as exc:
        if "readonly database" not in str(exc) and "unable to open" not in str(exc):
            raise
        raise RuntimeError(f"{exc.orig}{readonly_help()}") from None
