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
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy import create_engine

from app.config import settings


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC. Stored as TIMESTAMP; every producer and consumer treats it as UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    if not settings.database_url.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def sqlite_path() -> Path | None:
    """The database file, when this deployment is on SQLite."""
    url = settings.database_url
    if not url.startswith("sqlite"):
        return None
    return Path(url.split("///", 1)[-1]) if "///" in url else None


def readonly_help() -> str:
    """What "attempt to write a readonly database" actually means here.

    SQLite reports one error for every way the OS refused a write, and the
    traceback that surfaces is sixty lines of SQLAlchemy with the cause
    nowhere in it. The cause is almost always ownership -- and specifically
    ownership of the *directory*, not the file: in WAL mode SQLite creates
    `liner.db-wal` and `liner.db-shm` alongside the database, so a writable
    file in a directory somebody else owns still fails. Measured, not
    reasoned: with the file owned by the running user and the directory owned
    by root it fails; the other way round it works.

    That matters because the obvious fix -- chown the .db -- is the one that
    does not work, and the second attempt is where an afternoon goes.
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
        f"\n\nSQLite writes `-wal` and `-shm` files next to the database, so the "
        f"DIRECTORY has to be writable -- chowning the .db alone is not enough. "
        f"Give both to the user the service runs as:"
        f"\n  sudo chown -R liner:liner /srv/liner"
        f"\n  sudo systemctl restart liner"
        f"\n\nIf that user is not `liner`, check `User=` in the systemd unit. A "
        f"service running as root leaves root-owned files behind on every write, "
        f"and this comes back the next time anything runs as anyone else."
    )


def create_all() -> None:
    from app import models  # noqa: F401  (registers the mappers)

    try:
        Base.metadata.create_all(bind=engine)
    except OperationalError as exc:
        if "readonly database" not in str(exc) and "unable to open" not in str(exc):
            raise
        raise RuntimeError(f"{exc.orig}{readonly_help()}") from None
