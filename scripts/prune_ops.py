"""Drop the orphaned `ops_` tables out of the store databases.

    make prune-ops                # report only, writes nothing
    make prune-ops ARGS=--apply   # actually drop them

Before the split, `create_all` built the whole metadata into every store's
file, so each one carries the six `ops_` tables -- `founder@` and `cto@` once
per dealership, plus whatever was written while that store happened to be the
default. `OpsBase` stopped them being *created*; this removes the ones already
there.

**It verifies before it drops, and refuses rather than reporting.** The whole
reason these were left alone at startup is that a table here might hold the
only copy of a demo request somebody booked with us. So every row is looked for
in `ops.db` first, by `restore_ops.identity_of` -- the primary key, or the
address for `ops_users`, because three stores each seeded `founder@` with an id
of its own. A row that is not over there stops the whole store and the run
exits non-zero naming it; the fix is `make restore-ops` first, not a flag to
force this.

**A store is cleaned as a set, or not at all**, which the first version of this
got wrong by deciding per table. `ops_phone_calls` points at `ops_users` and
`ops_demo_requests`, so keeping one and dropping its parent leaves a table
referencing one that is gone -- and SQLite only refuses that drop while the
child still holds a row. Measured: with an *empty* child the drop goes through,
and the orphaned child then accepts inserts without complaint, so the breakage
is silent until something reads it. All-or-nothing costs nothing here, since a
store that needs a restore first ends up wholly clean once it has had one.

**Children first within a store**, from `restore_ops.ORDER` reversed. That list
is parent-first because it inserts; this deletes, so it wants the other end --
one list read from both ends rather than a second copy to keep in step.

Report-only by default, for the reason `make ingest` writes nothing without
`--publish`: the destructive direction is the one that has to be asked for.

`ops.db` itself is never touched -- it is not in `stores()` for this, and
dropping a table there is what the tool exists to make unnecessary.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import MetaData, Table, select, text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import engine_for, ops_engine  # noqa: E402
from app.models.ops import OPS_TABLES  # noqa: E402

from dump_ops import OPS, stores  # noqa: E402
from restore_ops import ORDER, identity_of  # noqa: E402

#: Children before parents -- `ORDER` the other way up. See the module docstring.
DROP_ORDER = [name for name, _model in reversed(ORDER)]


def _rows(engine, table_name: str) -> list[dict] | None:
    """Every row in one table, or None when the table is not in this file."""
    meta = MetaData()
    with engine.connect() as conn:
        try:
            table = Table(table_name, meta, autoload_with=conn)
        except Exception:
            return None
        return [dict(row) for row in conn.execute(select(table)).mappings().all()]


def _in_ops() -> dict[str, set]:
    """The identity of every ops row that is safely in Liner's own database."""
    engine = ops_engine()
    held: dict[str, set] = {}
    for name in OPS_TABLES:
        rows = _rows(engine, name)
        held[name] = {identity_of(name, r)[1] for r in (rows or [])}
    return held


def main() -> int:
    apply = "--apply" in sys.argv
    held = _in_ops()

    # Never this one. The rows here are the ones every other file is being
    # checked against, so a bug that let it into the loop would drop the
    # originals and report the copies as safe.
    targets = [s for s in stores() if s != OPS]

    print()
    dropped: list[str] = []
    refused: list[str] = []
    for slug in targets:
        engine = engine_for(slug)
        label = slug or "(default)"
        present = {}
        for name in OPS_TABLES:
            rows = _rows(engine, name)
            if rows is not None:
                present[name] = rows
        if not present:
            print(f"  {label:20} clean")
            continue

        # Every table is checked before any is dropped: all-or-nothing per
        # store, so a kept child can never be left pointing at a dropped
        # parent.
        unsafe: list[tuple[str, str, list]] = []
        for name, rows in present.items():
            field = identity_of(name, rows[0])[0] if rows else "id"
            missing = [r for r in rows if identity_of(name, r)[1] not in held[name]]
            if missing:
                unsafe.append((name, field, missing))

        if unsafe:
            refused.append(label)
            print(f"  {label:20} KEPT ENTIRELY -- ops.db does not have:")
            for name, field, missing in unsafe:
                # Named rather than counted: whoever reads this has to decide
                # whether to restore or to go and look, and "3 rows" tells them
                # neither.
                print(f"  {'':20} {name:22} {len(missing)} row(s), by {field}")
                for row in missing[:5]:
                    print(f"  {'':20}   {identity_of(name, row)[1]}")
            continue

        plan = [name for name in DROP_ORDER if name in present]
        for name in plan:
            rows = present[name]
            note = "empty" if not rows else f"{len(rows)} row(s) all in ops.db"
            print(f"  {label:20} {name:22} {'drop' if apply else 'would drop'} ({note})")
        if apply and plan:
            with engine.begin() as conn:
                for name in plan:
                    conn.execute(text(f"DROP TABLE {name}"))
            dropped += [f"{label}.{name}" for name in plan]

    print()
    if refused:
        print(f"  {len(refused)} store(s) kept, holding rows ops.db does not have: "
              f"{', '.join(refused)}")
        print("  Run `make dump-ops` then `make restore-ops FILE=...` first --")
        print("  there is deliberately no flag to drop them anyway.\n")
        return 1
    if not apply:
        print("  Nothing written. Re-run with ARGS=--apply to drop them.")
        print("  Take a `make dump-ops` first -- it is one command and this is not"
              " reversible.\n")
        return 0
    if dropped:
        print(f"  Dropped {len(dropped)} table(s) from {len({d.split('.')[0] for d in dropped})}"
              f" store(s).")
        print("  Check it: make dump-ops  (every store should read `nothing`)\n")
    else:
        print("  Nothing to drop.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
