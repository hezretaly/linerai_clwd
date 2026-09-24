"""Schema changes as migrations, for every database this process serves.

Two kinds of database, two histories: a **store** (one per dealer group, built
from `Base`) and **ops** (Liner's own, built from `OpsBase`). Each lives in
`backend/migrations/<kind>/`, and `ensure` brings one database to the newest
revision -- at boot, for every database that exists, and from the seed and the
copy to Postgres for one being made.

**Why now, and not before.** `create_all` adds a table to a database that
already exists and never a column, and this codebase has made a virtue of it
for a long time: `runtime_flags`, `conversation_once`, `user_signatures` and
`email_envelopes` are all tables because a column could not reach a box that
was already running. That was bearable with one SQLite file per box. A server
holding several groups' databases, which cannot be reseeded, needs the other
half -- and the time to start is before there is production data on it.

**A database from before migrations is adopted, not rebuilt.** A file with a
schema and no `alembic_version` was built by `create_all`, and its tables are
the baseline's, because nothing ever altered one -- near enough: a constraint
added to a model after its table existed never reached that file (`drift`
names any, and on Alsbou's it is one unique index on `handoff_rules.key`). So
it is filled out with any table *the baseline* has and it lacks, stamped at
the baseline and upgraded from there. The rows are never touched.

**A model changed without a migration is a failure in the gate**, not a
surprise on the server: `make smoke` builds an empty database from the
migrations and compares it with the models (`drift`).
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, MetaData, inspect

HERE = Path(__file__).resolve().parent.parent / "migrations"

#: The revision a database built by `create_all` is taken to be at.
BASELINE = {"store": "0001_store_baseline", "ops": "0001_ops_baseline"}

#: A table only a built database of each kind has. Its presence without
#: `alembic_version` is what says "built before migrations".
MARKER = {"store": "dealership", "ops": "ops_users"}


def metadata(kind: str) -> MetaData:
    from app import models  # noqa: F401  (registers every mapper)
    from app.db import Base, OpsBase

    if kind == "store":
        return Base.metadata
    if kind == "ops":
        return OpsBase.metadata
    raise ValueError(f"no such schema: {kind!r}")


def config(kind: str, connection=None) -> Config:  # noqa: ANN001
    """An Alembic config for one kind, handed the connection to use.

    Handed a connection rather than a URL, so a migration runs on exactly the
    engine the application opens -- its pragmas on SQLite, its pool settings
    on Postgres -- and never on a second one built from a string.
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE / kind))
    cfg.attributes["kind"] = kind
    cfg.attributes["connection"] = connection
    return cfg


def head(kind: str) -> str:
    return ScriptDirectory.from_config(config(kind)).get_current_head() or ""


def current(engine: Engine) -> str:
    """The revision a database is at, or "" when migrations never ran on it."""
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision() or ""


def ensure(engine: Engine, kind: str) -> str:
    """Bring one database to the newest revision. Returns what it did."""
    if engine.dialect.name == "sqlite":
        return _ensure_sqlite(engine, kind)
    with engine.begin() as conn:
        return _bring_up(conn, kind)


def _bring_up(conn, kind: str) -> str:  # noqa: ANN001
    tables = set(inspect(conn).get_table_names())
    cfg = config(kind, conn)
    if "alembic_version" in tables:
        before = MigrationContext.configure(conn).get_current_revision() or ""
        command.upgrade(cfg, "head")
        return "current" if before == head(kind) else f"upgraded from {before}"
    if MARKER[kind] in tables:
        # Built by `create_all`, before migrations: fill in whatever *the
        # baseline* has and this file lacks, say it is at the baseline, and
        # carry on from there.
        _fill_from_baseline(conn, kind, tables)
        command.stamp(cfg, BASELINE[kind])
        command.upgrade(cfg, "head")
        return "adopted"
    command.upgrade(cfg, "head")
    return "built"


def _fill_from_baseline(conn, kind: str, tables: set[str]) -> None:  # noqa: ANN001
    """Build the baseline's tables this file lacks, and nothing after it.

    **From the baseline revision, never from the models.** This was
    `metadata.create_all()`, which builds every table the code has *today* --
    so a file adopted after any revision that adds a table was given that
    table here, stamped at the baseline, and the revision then failed on
    "table ... already exists": a box whose files predate migrations, which
    is linerai.us, would have stopped booting on the upgrade that introduced
    it. The parked lots revision (branch `claude/rooftops-parked`) is how it
    was found. The baseline's own `upgrade()` is run instead, told to skip
    each table the file already has, so what is filled in is what the
    revisions after it expect to find.
    """
    import importlib.util
    from contextlib import contextmanager

    from alembic.operations import Operations

    path = HERE / kind / "versions" / f"{BASELINE[kind]}.py"
    spec = importlib.util.spec_from_file_location(f"liner_baseline_{kind}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    ops = Operations(MigrationContext.configure(conn))

    class _Nothing:
        def __getattr__(self, name):  # noqa: ANN001, ANN204
            return lambda *a, **k: None

    class _MissingOnly:
        def f(self, name):  # noqa: ANN001, ANN201
            return ops.f(name)

        def create_table(self, name, *columns, **kw):  # noqa: ANN001, ANN201
            return None if name in tables else ops.create_table(name, *columns, **kw)

        def create_index(self, name, table, *columns, **kw):  # noqa: ANN001, ANN201
            return None if table in tables else ops.create_index(name, table, *columns, **kw)

        @contextmanager
        def batch_alter_table(self, name, **kw):  # noqa: ANN001, ANN201
            if name in tables:
                yield _Nothing()
            else:
                with ops.batch_alter_table(name, **kw) as batch:
                    yield batch

    module.op = _MissingOnly()
    module.upgrade()


def _ensure_sqlite(engine: Engine, kind: str) -> str:
    """The same, on SQLite, where two things have to be done by hand.

    **One real transaction.** Python's `sqlite3` begins a transaction only
    before a row is written, never before `CREATE` or `DROP`, so a migration
    that failed half-way kept every table it had made -- and a temporary copy
    of the table it was rebuilding -- in a file still stamped at the revision
    before, and the next boot failed on "table ... already exists", for ever,
    on the box that has to start. With the driver told to stay out of it,
    `BEGIN` here covers the DDL too, and a failure takes all of it back.

    **Foreign keys off while it runs.** SQLite cannot add a foreign key to a
    table that exists, so batch mode rebuilds it -- copies it, drops the
    original, renames the copy -- and the application's `foreign_keys=ON`
    refuses to drop a table other rows point at. It can only be switched off
    outside a transaction, and it is switched back on before the connection
    returns to the pool. Anything the migration broke would then go
    unnoticed, so the file is checked after and a violation that was not
    there before fails the migration.

    Found by migrating the development databases; the first file it reached
    was left half-built and was restored from a copy.
    """
    with engine.connect() as conn:
        raw = conn.connection.dbapi_connection
        saved = raw.isolation_level
        raw.isolation_level = None
        try:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            before = len(conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall())
            conn.exec_driver_sql("BEGIN")
            try:
                what = _bring_up(conn, kind)
                after = len(conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall())
                if after > before:
                    raise RuntimeError(
                        f"the migration left {after - before} row(s) pointing at nothing "
                        "(PRAGMA foreign_key_check); nothing was changed"
                    )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            return what
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()
            raw.isolation_level = saved


def drift(engine: Engine, kind: str) -> list:
    """Every difference between a database and the models, as Alembic sees it.

    Empty is the answer the gate wants from a database the migrations built:
    anything else is a model that changed without a migration saying so.
    """
    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        return [
            diff for diff in compare_metadata(context, metadata(kind))
            # Alembic's own bookkeeping is not a model.
            if not (isinstance(diff, tuple) and len(diff) > 1
                    and getattr(diff[1], "name", "") == "alembic_version")
        ]


def run_env(kind: str) -> None:
    """The body of each `migrations/<kind>/env.py`.

    Offline mode (SQL written out rather than run) is not supported: every
    database here is migrated by the process that owns it, on a connection it
    hands over.
    """
    from alembic import context

    connection = context.config.attributes.get("connection")
    if connection is None:
        raise RuntimeError(
            "Migrations run through app.migrate.ensure, which hands them the "
            "application's own connection. `make migrate` does that for every database."
        )
    context.configure(
        connection=connection,
        target_metadata=metadata(kind),
        # SQLite cannot alter a column or drop a constraint in place; batch
        # mode rebuilds the table instead, and does nothing on Postgres.
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def main() -> int:
    """`make migrate`: every database this deployment serves, to the newest.

    The default store, every store with a database, and Liner's own. The
    server does the same at every boot; this is for a deploy that wants the
    schema moved -- and any failure seen -- before the new code starts.
    """
    import sys

    from app import pg
    from app.config import settings
    from app.db import engine_for, has_database, ops_engine
    from app.stores import known_stores

    # `--create`: on a database server, make the two databases every boot
    # opens -- the default store and Liner's own -- if they are not there yet.
    # A store's own is made by `make to-postgres` or `make reset-db`; these two
    # have nothing to be copied into them on a new server, and a boot that
    # cannot connect to either does not start.
    if "--create" in sys.argv:
        for url in (settings.database_url, settings.ops_database_url):
            if pg.is_postgres(url) and pg.create(url):
                print(f"  created {pg.safe(url)}")

    targets = [("default store", engine_for(""), "store")]
    targets += [(slug, engine_for(slug), "store") for slug in known_stores() if has_database(slug)]
    targets.append(("Liner's own", ops_engine(), "ops"))
    for label, engine, kind in targets:
        what = ensure(engine, kind)
        print(f"  {label:24} {what:>22}   at {current(engine)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
