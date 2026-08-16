"""Put Liner's own accounts on a database that was seeded before they existed.

    make add-owners

`founder@linerai.us` and `cto@linerai.us` arrive through `_seed_users`, which
only runs on a fresh seed. A deployment that has been taking real bookings was
seeded before the `owner` role existed, so after upgrading it boots fine and
then cannot sign anybody in to `/ops` -- the rows are simply not there, and the
only other way to create them is `make reset-db`, which deletes the leads.

So this inserts what is missing and touches nothing else. Idempotent: an
account that already exists is left exactly as it is, password included, because
somebody may have changed it with `make set-password` and re-hashing it here
would silently lock them out.

Passwords come from OWNER_PASSWORD, the same as a seed would use. Change one
afterwards with:

    make set-password EMAIL=founder@linerai.us
"""

from __future__ import annotations

from app.config import DEV_SEED_PASSWORD, settings
from app.db import SessionLocal, create_all
from app.models import User
from app.seed import OWNERS, build_user


def add_owners() -> int:
    create_all()
    db = SessionLocal()
    added = 0
    try:
        for person in OWNERS:
            name, email, role, _initials, _cap = person
            existing = db.query(User).filter_by(email=email).one_or_none()
            if existing is not None:
                state = "active" if existing.active else "deactivated"
                print(f"  already there  {email}  ({existing.role}, {state})")
                continue
            db.add(build_user(*person))
            added += 1
            print(f"  added          {email}  ({role})")
        db.commit()
    finally:
        db.close()
    return added


def main() -> int:
    print("Liner's own accounts:\n")
    added = add_owners()
    print()
    if not added:
        print("Nothing to do -- both accounts already exist.")
        return 0

    print(f"Added {added} account(s). They sign in at /login?as=owner and land on /ops.")
    if settings.owner_password == DEV_SEED_PASSWORD:
        # Only reachable outside production, where the boot guard already
        # refuses this value. Saying so here as well is the difference between
        # a laptop and a box somebody forgot they exposed.
        print(
            "\nThe password is the development default, which is published in this\n"
            "repository. Set OWNER_PASSWORD before this is reachable from outside,\n"
            "or change it now with: make set-password EMAIL=founder@linerai.us"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
