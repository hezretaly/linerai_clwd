"""Add one person to the dealership's staff, on a database already in use.

    make add-user EMAIL=austin@example.com NAME="Austin ..." ROLE=manager

**Why this exists.** Staff arrive through `_seed_users`, which only runs on a
fresh seed -- so the only way to give a real person an account was
`make reset-db`, which deletes every lead, conversation and appointment on the
box. That is fine on day one and unacceptable the moment a demo has anything
real in it. Same shape and same reason as `make add-owners`, which solved this
for our own two accounts.

**Dealership staff only, and that is the point.** It writes to `users`, never
`ops_users`. The two tables are separate because a role string is a filter
every query has to remember, and three of them forgot -- which put us on
somebody else's team roster and in their assignment pickers. A tool that could
write to either would be a fourth way to make that mistake.

**The password is generated here and printed once.** Not read from `.env`: an
environment variable per person is a variable somebody has to add to the
deployment, and the seed only reads them on a fresh database anyway. Not
prompted either, because the common case is creating an account *for* somebody
else, and a password you invent on the spot at a terminal is a weak one. It is
printed exactly once and never stored anywhere but the bcrypt hash -- change it
afterwards with `make set-password EMAIL=...`.

**Idempotent, and it never touches an existing password.** Run again for an
address that is already there and it reports the account and stops. Somebody
may have changed their own password, and silently re-hashing it here would lock
them out with nothing saying why.

**`create_user` is the part `POST /team` shares.** Everything about turning an
address, a name and a role into a `users` row -- the CLI wraps it with a store
to add to and messages to print; the endpoint wraps it with a 409 in place of
the CLI's silent no-op.
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from dataclasses import dataclass

from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.db import MISSING_TABLE, SessionLocal, active_store, create_all, has_database, ops_session
from app.models import Dealership, OpsUser, User

#: Its own context rather than importing the seed's. The dependency has to run
#: this way round -- `seed` builds a profile's `staff:` list through `initials`
#: here -- and two modules importing each other is an ImportError at startup,
#: not a style point. Same scheme and same defaults, which is what matters.
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _hash(password: str) -> str:
    return pwd.hash(password)

#: What a role means here, and the whole list. `owner` is deliberately absent:
#: that is us, it lives in `ops_users`, and it is `make add-owners`.
ROLES = {
    "manager": "sees every lead, the team page, the assistant settings, and can publish",
    "rep": "works the floor: their own leads, the calendar, the buyer pages",
}

#: Enough to be worth generating rather than typing. `token_urlsafe(12)` is 16
#: characters of base64url -- 96 bits, comfortably inside bcrypt's 72-byte
#: ceiling, and short enough to read down a phone line once.
PASSWORD_BYTES = 12

#: Not validation of whether an address exists -- nothing here can know that.
#: It catches a shell that swallowed the argument, which is the real failure:
#: `make add-user EMAIL=` would otherwise create an account with no address
#: that nobody can ever sign in to and that the team page then lists.
LOOKS_LIKE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InvalidEmail(ValueError):
    """`email` does not look like an email address."""

    def __init__(self, email: str):
        self.email = email
        super().__init__(f"{email!r} does not look like an email address.")


class InvalidRole(ValueError):
    """`role` is not one this system knows (see `ROLES`)."""

    def __init__(self, role: str):
        self.role = role
        super().__init__(f"Unknown role {role!r}.")


class OpsEmailConflict(Exception):
    """`email` is already one of Liner's own, in `ops_users`."""

    def __init__(self, email: str):
        self.email = email
        super().__init__(f"{email} is one of Liner's own accounts, in ops_users.")


@dataclass
class CreateUserResult:
    user: User
    #: Plaintext, only set when this call actually created the row.
    password: str | None
    created: bool
    #: True when `name` was blank and derived from the address's local part.
    name_defaulted: bool


def create_user(db: Session, email: str, name: str, role: str) -> CreateUserResult:
    """Validate, check for a clash with our own accounts, and create the row.

    The core `add_user` (the CLI) and `POST /team` share. Raises
    `InvalidEmail` or `InvalidRole` for bad input and `OpsEmailConflict` when
    `email` is already one of Liner's own -- three refusals either caller can
    act on. An address that already belongs to a *dealership* account is not
    raised as an error here: it comes back with `created=False` and no
    password, because the two callers disagree about what that means -- the
    CLI's answer is a no-op, `POST /team`'s is a 409, and that distinction is
    the caller's to make, not this function's.
    """
    email = (email or "").strip().lower()
    name = (name or "").strip()
    role = (role or "rep").strip().lower()

    if not LOOKS_LIKE_EMAIL.match(email):
        raise InvalidEmail(email)
    if role not in ROLES:
        raise InvalidRole(role)

    name_defaulted = not name
    if name_defaulted:
        # Derived rather than refused: the local part is almost always their
        # name, and an account whose name is blank renders as an empty avatar
        # on every row they touch.
        name = email.split("@")[0].replace(".", " ").replace("_", " ").title()

    # Both tables, because the email is what somebody types at the login
    # form and it has to identify exactly one account. An address that is
    # already one of ours would otherwise create a second row that the
    # dealership's login finds first.
    with ops_session() as ops:
        clash = ops.query(OpsUser).filter(OpsUser.email == email).one_or_none()
    if clash is not None:
        raise OpsEmailConflict(email)

    existing = db.query(User).filter(User.email == email).one_or_none()
    if existing is not None:
        return CreateUserResult(existing, None, False, name_defaulted)

    password = secrets.token_urlsafe(PASSWORD_BYTES)
    user = User(
        name=name, email=email, role=role,
        password_hash=_hash(password),
        avatar_initials=initials(name), active=True,
    )
    db.add(user)
    db.commit()
    return CreateUserResult(user, password, True, name_defaulted)


def initials(name: str) -> str:
    """`Austin Rowe` -> `AR`. The avatar on every row they touch."""
    parts = [p for p in re.split(r"[^A-Za-z]+", name or "") if p]
    if not parts:
        return "??"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def store_label(slug: str) -> str:
    """How a store is named to whoever is at the terminal."""
    return f"store {slug!r}" if slug else "the unprefixed store (no DEALERSHIP set)"


def dealership_in(slug: str) -> str:
    """The dealership seeded in this store, or "" -- asked without creating it.

    **An account in a store with no dealership is a login to nothing.** A
    deployment that serves its groups by prefix or subdomain can leave the
    unprefixed store empty on purpose -- linerai.us's production does -- and
    this ran against whichever store `DEALERSHIP=` named, which on that box is
    none. Forgetting it created a manager nobody could ever find, with a
    password printed as though it had worked. `has_database` first, because
    opening a SQLite store creates its file; then the row, because on a
    database server the tables exist from `make migrate` before any seed.
    And `SessionLocal(slug)`, the store asked about: `SessionLocal()` is
    whichever store is active, which answers for the wrong one the moment a
    caller names another.
    """
    if not has_database(slug):
        return ""
    with SessionLocal(slug) as db:
        try:
            row = db.query(Dealership).first()
        except MISSING_TABLE:
            return ""
        return row.name if row else ""


def add_user(email: str, name: str, role: str) -> int:
    slug = active_store()
    dealership = dealership_in(slug)
    if not dealership:
        print(f"{store_label(slug)} has no dealership in it, so nobody could sign in there.",
              file=sys.stderr)
        print("Name the store:  DEALERSHIP=<store> make add-user ...  "
              "(`make stores` lists them and says which are seeded)", file=sys.stderr)
        return 1

    create_all()
    db = SessionLocal()
    try:
        try:
            result = create_user(db, email, name, role)
        except InvalidEmail as e:
            print(f"{e.email!r} does not look like an email address.", file=sys.stderr)
            print('Usage: make add-user EMAIL=someone@example.com NAME="Their Name" ROLE=rep',
                  file=sys.stderr)
            return 1
        except InvalidRole as e:
            print(f"Unknown role {e.role!r}. One of:", file=sys.stderr)
            for known, what in ROLES.items():
                print(f"  {known:8} {what}", file=sys.stderr)
            return 1
        except OpsEmailConflict as e:
            print(str(e), file=sys.stderr)
            print("Dealership staff and our staff are separate on purpose. Use a different "
                  "address, or `make set-password` to change that one.", file=sys.stderr)
            return 1

        if result.name_defaulted:
            print(f"No NAME given, using {result.user.name!r} from the address.")

        if not result.created:
            existing = result.user
            print(f"{existing.name} <{existing.email}> is already on the team "
                  f"({existing.role}{'' if existing.active else ', deactivated'}).")
            print("Nothing changed -- their password is not touched, in case they have "
                  "changed it themselves.")
            print(f"To change it:  DEALERSHIP={slug} make set-password EMAIL={existing.email}"
                  if slug else f"To change it:  make set-password EMAIL={existing.email}")
            return 0
    finally:
        db.close()

    print(f"\nAdded {result.user.name} <{result.user.email}> as a {result.user.role} of "
          f"{dealership} ({store_label(slug)}).")
    print(f"  {ROLES[result.user.role]}")
    # The address is on the password's line on purpose: the runbook sends
    # this output to a root-only file and shows the session only the lines
    # without an `@` (docs/NEW-SERVER.md), which is what the seed's login
    # lines already rely on. A bare "Password:" line went straight through.
    print(f"\n  Password for {result.user.email}:  {result.password}")
    print("\nThis is the only time it is shown -- only the bcrypt hash is stored.")
    print("They can be given a new one with:  "
          + (f"DEALERSHIP={slug} " if slug else "") + f"make set-password EMAIL={result.user.email}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="add-user", description="Add one person to the dealership's staff."
    )
    parser.add_argument("email")
    parser.add_argument("--name", default="")
    parser.add_argument("--role", default="rep", choices=sorted(ROLES))
    args = parser.parse_args()
    return add_user(args.email, args.name, args.role)


if __name__ == "__main__":
    raise SystemExit(main())
