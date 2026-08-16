"""Put Liner's own accounts on a database that predates them.

    make add-owners

Two jobs, both of which exist because there is no Alembic here and both of
which are safe to run on a box that is taking real bookings.

**Create.** `founder@linerai.us` and `cto@linerai.us` arrive through the seed,
which only runs on a fresh database. An install that has been live since before
they existed boots fine and then cannot sign anybody in to `/ops`; the only
other route was `make reset-db`, which deletes the leads.

**Move.** Our rows briefly lived in the dealership's tables: accounts as a
third role in `users`, and demo bookings in `demo_requests`. Both are now
`ops_`-prefixed and separate, because a role string is a filter every query has
to remember and three of them forgot. A row left behind is a stale login that
can walk back onto the dealership's roster, or -- for `demo_requests` -- a real
booking with a real person on the other end that the new code cannot see.

Idempotent, and it never re-hashes an account that already exists: somebody may
have changed a password with `make set-password`, and doing that silently here
would lock them out. Change one afterwards with:

    make set-password EMAIL=founder@linerai.us
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import inspect, text

from app.config import DEV_SEED_PASSWORD, settings
from app.db import SessionLocal, create_all, engine
from app.models import DemoRequest, OpsUser, User
from app.seed import OWNERS, build_owner

#: The table demo bookings used to live in, before they were separated from
#: the dealership's. Rows are copied across and the old table is left in
#: place: dropping it would make the upgrade one-way, and it is a handful of
#: rows on any real install.
LEGACY_DEMO_TABLE = "demo_requests"

#: Roles that belong in `users`. Anything else there is from before the split.
DEALERSHIP_ROLES = ("manager", "rep")


def migrate_demo_requests(db) -> int:
    """Copy `demo_requests` into `ops_demo_requests`, skipping what is there.

    Raw SQL because the old table has no model any more, and by id because
    that is what makes it safe to run twice -- these are bookings with real
    people on the other end, and a duplicate would be a second calendar entry
    for a demo that happens once.
    """
    if LEGACY_DEMO_TABLE not in inspect(engine).get_table_names():
        return 0

    existing = {row.id for row in db.query(DemoRequest.id).all()}
    columns = [
        "id", "kind", "name", "dealership", "email", "phone", "dealership_url",
        "message", "slot_at", "consent_at", "consent_text", "status", "created_at",
    ]
    #: Columns the model declares as DateTime. Raw SQL over SQLite returns
    #: these as *strings* -- the driver only applies type affinity when the
    #: ORM asked for the column -- and the DateTime column then refuses the
    #: insert outright. Measured, not reasoned: the first run of this against
    #: a copy of a real database died on exactly that.
    stamps = ("slot_at", "consent_at", "created_at")

    rows = db.execute(text(f"SELECT {', '.join(columns)} FROM {LEGACY_DEMO_TABLE}")).all()
    moved = 0
    for row in rows:
        values = dict(zip(columns, row))
        if values["id"] in existing:
            continue
        for key in stamps:
            values[key] = _as_datetime(values[key])
        db.add(DemoRequest(**values))
        moved += 1
    if moved:
        db.commit()
        print(f"  moved          {moved} demo request(s)  {LEGACY_DEMO_TABLE} -> "
              f"{DemoRequest.__tablename__}")
    elif rows:
        print(f"  already there  {len(rows)} demo request(s)")
    return moved


def _as_datetime(value):
    """Whatever the driver handed back, as a datetime or None."""
    if value is None or isinstance(value, datetime):
        return value
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        return datetime.fromisoformat(text_value)
    except ValueError:
        # Not a shape we recognise. Dropping the timestamp loses when
        # somebody consented, so the row is skipped and said aloud instead --
        # a booking with no consent time is worse than one still in the old
        # table where it can be looked at.
        raise SystemExit(
            f"Cannot read the timestamp {value!r} in {LEGACY_DEMO_TABLE}. "
            "Nothing was migrated; the old table is untouched."
        ) from None


def add_owners() -> tuple[int, int]:
    """Returns (created, moved). `moved` counts rows of either kind."""
    create_all()
    db = SessionLocal()
    created = moved = 0
    try:
        moved += migrate_demo_requests(db)
        # Move first. A legacy row carries a password somebody may be using,
        # and creating a fresh account before rescuing it would mean the new
        # one wins the email uniqueness and the old password stops working
        # with nothing saying why.
        for legacy in db.query(User).filter(User.role.notin_(DEALERSHIP_ROLES)).all():
            if db.query(OpsUser).filter_by(email=legacy.email).first() is None:
                db.add(OpsUser(
                    name=legacy.name, email=legacy.email,
                    password_hash=legacy.password_hash,
                    avatar_initials=legacy.avatar_initials, active=legacy.active,
                    password_env=_env_key_for(legacy.email),
                ))
                moved += 1
                print(f"  moved          {legacy.email}  users -> ops_users")
            else:
                print(f"  dropped stale  {legacy.email}  (already in ops_users)")
            db.delete(legacy)

        # Flush before the create pass. A moved row is pending, not written,
        # and `query(OpsUser)` below would not see it -- so both accounts got
        # inserted a second time and the run died on the email unique index
        # with everything rolled back.
        db.flush()

        for person in OWNERS:
            name, email, env_key, _initials = person
            if db.query(OpsUser).filter_by(email=email).first() is not None:
                print(f"  already there  {email}  ({env_key})")
                continue
            db.add(build_owner(*person))
            created += 1
            print(f"  added          {email}  ({env_key})")
        db.commit()
    finally:
        db.close()
    return created, moved


def _env_key_for(email: str) -> str:
    for _name, known, env_key, _initials in OWNERS:
        if known.lower() == (email or "").lower():
            return env_key
    # Somebody we do not ship. Empty means OWNER_PASSWORD, which is the
    # fallback every ops account already has.
    return ""


def main() -> int:
    print("Liner's own accounts:\n")
    try:
        created, moved = add_owners()
    except RuntimeError as exc:
        # A permissions problem is not a bug in this script, and a sixty-line
        # SQLAlchemy traceback buries the one sentence that fixes it.
        raise SystemExit(str(exc)) from None
    print()
    if not created and not moved:
        print("Nothing to do -- every account is already in ops_users.")
        return 0

    print(f"Created {created} account(s), moved {moved} row(s) into the ops_ tables.")
    print("Sign in at /login?as=owner.")
    weak = [
        env_key for _n, _e, env_key, _i in OWNERS
        if settings.password_for_ops(env_key.lower()) == DEV_SEED_PASSWORD
    ]
    if weak:
        # Only reachable outside production, where the boot guard already
        # refuses this value. Saying it here as well is the difference between
        # a laptop and a box somebody forgot they had exposed.
        print(
            f"\n{' and '.join(weak)} resolve to the development default, which is\n"
            "published in this repository. Set them before this is reachable from\n"
            "outside, or change one now with:\n"
            "    make set-password EMAIL=founder@linerai.us"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
