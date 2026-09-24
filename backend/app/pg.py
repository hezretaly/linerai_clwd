"""A Postgres server, from the side that makes and removes databases.

On a server there is one database per dealer group and one for Liner's own,
all on one Postgres. Nothing in the application ever creates or drops one --
a request opening a database that does not exist fails, which is the property
`has_database` relies on -- so the three things that do are here, used by
`make reset-db`, `make stores` and the copy from SQLite:

* **Creating** one, with `C` collation. SQLite compares and sorts text byte by
  byte; a Postgres database created with a language's collation sorts "a" and
  "B" together and ignores punctuation, so an A-Z list, a tie-break and the
  order knowledge reaches the prompt in would all change on the move. `C`
  keeps them exactly as they were.
* **Dropping** one, `WITH (FORCE)`, because the server process keeps a pool
  open on every store it has served and a plain DROP waits on it for ever.
* **Describing** one for a listing, with the password never printed.

Every statement goes to the server's `postgres` database with AUTOCOMMIT:
CREATE and DROP DATABASE refuse to run inside a transaction.
"""

from __future__ import annotations

import re

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

#: What a database name may be before it is written into a statement. Names
#: come from `DATABASE_URL_TEMPLATE` with the store's slug in it, and DDL
#: cannot take a bound parameter, so this is the whole defence.
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def is_postgres(url: str) -> bool:
    return (url or "").startswith("postgresql")


def name_of(url: str) -> str:
    """The database a URL names, refused if it is not a plain identifier."""
    name = make_url(url).database or ""
    if not _NAME.match(name):
        raise ValueError(
            f"{name!r} is not a database name this will create or drop -- letters, "
            "digits and underscores, starting with a letter"
        )
    return name


def safe(url: str) -> str:
    """The URL with its password hidden, for printing."""
    return make_url(url).render_as_string(hide_password=True)


def _server(url: str) -> Engine:
    return create_engine(
        make_url(url).set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )


def exists(url: str) -> bool:
    engine = _server(url)
    try:
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": name_of(url)},
            ).first() is not None
    finally:
        engine.dispose()


def create(url: str) -> bool:
    """Create the database if it is missing. True when this call made it."""
    name = name_of(url)
    if exists(url):
        return False
    engine = _server(url)
    try:
        with engine.connect() as conn:
            conn.execute(text(
                f'CREATE DATABASE "{name}" TEMPLATE template0 '
                "ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'"
            ))
    finally:
        engine.dispose()
    return True


def drop(url: str) -> bool:
    """Drop the database if it is there. True when this call removed it."""
    name = name_of(url)
    if not exists(url):
        return False
    engine = _server(url)
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    finally:
        engine.dispose()
    return True


def size(url: str) -> str:
    """How big the database is, as Postgres prints it, or "" when missing."""
    engine = _server(url)
    try:
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT pg_size_pretty(pg_database_size(:name))"),
                {"name": name_of(url)},
            ).scalar() or ""
    except DBAPIError:
        return ""
    finally:
        engine.dispose()
