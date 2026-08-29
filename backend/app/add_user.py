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
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys

from app.db import SessionLocal, create_all
from app.models import OpsUser, User
from app.seed import _hash

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


def initials(name: str) -> str:
    """`Austin Rowe` -> `AR`. The avatar on every row they touch."""
    parts = [p for p in re.split(r"[^A-Za-z]+", name or "") if p]
    if not parts:
        return "??"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def add_user(email: str, name: str, role: str) -> int:
    email = (email or "").strip().lower()
    name = (name or "").strip()
    role = (role or "rep").strip().lower()

    if not LOOKS_LIKE_EMAIL.match(email):
        print(f"{email!r} does not look like an email address.", file=sys.stderr)
        print('Usage: make add-user EMAIL=someone@example.com NAME="Their Name" ROLE=rep',
              file=sys.stderr)
        return 1
    if role not in ROLES:
        print(f"Unknown role {role!r}. One of:", file=sys.stderr)
        for known, what in ROLES.items():
            print(f"  {known:8} {what}", file=sys.stderr)
        return 1
    if not name:
        # Derived rather than refused: the local part is almost always their
        # name, and an account whose name is blank renders as an empty avatar
        # on every row they touch.
        name = email.split("@")[0].replace(".", " ").replace("_", " ").title()
        print(f"No NAME given, using {name!r} from the address.")

    create_all()
    db = SessionLocal()
    try:
        # Both tables, because the email is what somebody types at the login
        # form and it has to identify exactly one account. An address that is
        # already one of ours would otherwise create a second row that the
        # dealership's login finds first.
        if db.query(OpsUser).filter(OpsUser.email == email).one_or_none() is not None:
            print(f"{email} is one of Liner's own accounts, in ops_users.", file=sys.stderr)
            print("Dealership staff and our staff are separate on purpose. Use a different "
                  "address, or `make set-password` to change that one.", file=sys.stderr)
            return 1

        existing = db.query(User).filter(User.email == email).one_or_none()
        if existing is not None:
            print(f"{existing.name} <{existing.email}> is already on the team "
                  f"({existing.role}{'' if existing.active else ', deactivated'}).")
            print("Nothing changed -- their password is not touched, in case they have "
                  "changed it themselves.")
            print(f"To change it:  make set-password EMAIL={email}")
            return 0

        password = secrets.token_urlsafe(PASSWORD_BYTES)
        db.add(User(
            name=name, email=email, role=role,
            password_hash=_hash(password),
            avatar_initials=initials(name), active=True,
        ))
        db.commit()
    finally:
        db.close()

    print(f"\nAdded {name} <{email}> as a {role}.")
    print(f"  {ROLES[role]}")
    print(f"\n  Password:  {password}")
    print("\nThis is the only time it is shown -- only the bcrypt hash is stored.")
    print(f"They can be given a new one with:  make set-password EMAIL={email}")
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
