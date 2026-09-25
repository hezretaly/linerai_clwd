"""Change one account's password in place.

Passwords are hashed into the ``users`` table when the database is seeded, so
editing ``MANAGER_PASSWORD`` in ``.env`` afterwards changes nothing that already
exists -- the guard in ``config.py`` reads it, but the stored hash does not
move. Until now the only way to correct that was ``make reset-db``, which
rehashes every account by deleting the database and rebuilding it. That is fine
on day one and unacceptable once there are leads in there.

This changes one row and touches nothing else:

    make set-password EMAIL=dana.mercer@riversideauto.example

A dealership's staff are in that store's database, so on a box serving several
stores the store is named: ``DEALERSHIP=alsbou make set-password EMAIL=...``.
Ours (``founder@``, ``cto@``) are in Liner's own database and are found
without it.

It prompts rather than taking the password as an argument, so it never lands in
shell history or the process list where ``ps`` would show it. ``--stdin`` is
there for scripts:

    printf '%s' "$NEW" | make set-password EMAIL=... ARGS=--stdin

Note this does not touch ``.env``. Set the same value there if you want a fresh
``make reset-db`` to seed the account with it again.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from passlib.context import CryptContext

from app.config import DEV_SEED_PASSWORD, settings
from app.add_user import dealership_in, store_label
from app.db import SessionLocal, active_store, ops_session
from app.models import OpsUser, User

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

# bcrypt silently truncates at 72 bytes, so a longer passphrase would appear to
# work while only its first 72 bytes were ever checked. Refuse instead.
MAX_BYTES = 72
MIN_LEN = 8


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="set-password", description="Change one account's password in place."
    )
    parser.add_argument("email", help="the account to change")
    parser.add_argument(
        "--stdin", action="store_true", help="read the password from stdin instead of prompting"
    )
    args = parser.parse_args()

    # The store is searched only when it holds a dealership. Opening a SQLite
    # store creates its file, so asking an unseeded one left an empty database
    # behind and then failed on "no such table" before reaching our accounts.
    slug = active_store()
    db = SessionLocal() if dealership_in(slug) else None
    ops = ops_session()
    try:
        # Both tables: our accounts are in `ops_users` and a password change
        # is exactly as much a password change for one of them.
        email = args.email.strip().lower()
        # The row is committed through the session it was read from. It was
        # always `db.commit()`, so an owner's new hash sat in the ops session
        # and was dropped at exit -- while this printed "Password updated".
        user = db.query(User).filter(User.email == email).one_or_none() if db else None
        owner = db
        if user is None:
            # Ours are in Liner's own database, not this store's.
            user = ops.query(OpsUser).filter(OpsUser.email == email).one_or_none()
            owner = ops
        if user is None:
            known = ([u.email for u in db.query(User).order_by(User.email).all()] if db else []) + [
                u.email for u in ops.query(OpsUser).order_by(OpsUser.email).all()
            ]
            print(f"No account with the email {args.email!r}.", file=sys.stderr)
            # Say where it looked. A dealership's staff are in that store's
            # database, so on a box whose unprefixed store is empty on purpose
            # the answer to "not found" is almost always a missing DEALERSHIP.
            print(f"Looked in {store_label(slug)} and in Liner's own accounts.", file=sys.stderr)
            if db is None:
                print("That store has no dealership in it. Name the store:  "
                      "DEALERSHIP=<store> make set-password EMAIL=...  (`make stores` lists them)",
                      file=sys.stderr)
            if known:
                print("Accounts on this database:", file=sys.stderr)
                for email in known:
                    print(f"  {email}", file=sys.stderr)
            else:
                print("This database has no accounts. Run `make reset-db`.", file=sys.stderr)
            return 1

        if args.stdin:
            password = sys.stdin.readline().rstrip("\n")
        else:
            password = getpass.getpass(f"New password for {user.name} <{user.email}>: ")
            if password != getpass.getpass("Again: "):
                print("Those did not match.", file=sys.stderr)
                return 1
        # A trailing \r survives a copy-paste out of a CRLF file and is invisible
        # in every terminal, producing a password nobody can retype. Taken off
        # *before* the checks below, so they judge what is stored: stripped
        # after them, "  abc   " passed the length check and was saved as
        # "abc", and " liner-dev" got past the production refusal as liner-dev.
        if password != password.strip():
            print(
                "That has spaces at the start or end, which are invisible and "
                "impossible to retype. Removing them.",
                file=sys.stderr,
            )
            password = password.strip()

        if len(password) < MIN_LEN:
            print(f"Too short -- use at least {MIN_LEN} characters.", file=sys.stderr)
            return 1
        if len(password.encode("utf-8")) > MAX_BYTES:
            print(
                f"Too long -- bcrypt only checks the first {MAX_BYTES} bytes, so a longer "
                "password would give false confidence.",
                file=sys.stderr,
            )
            return 1
        if password == DEV_SEED_PASSWORD and settings.is_production:
            print(
                "That is the published development password and this is a production "
                "install. Anyone reading the repo would have this account.",
                file=sys.stderr,
            )
            return 1

        user.password_hash = pwd.hash(password)
        owner.commit()
        # Read back through a fresh session on the same database before saying
        # so. The line below was printed for an owner whose new hash had been
        # committed through the other session and dropped at exit; a second
        # look at the row is what would have shown it.
        model = OpsUser if owner is ops else User
        with (ops_session() if owner is ops else SessionLocal(slug)) as fresh:
            stored = fresh.query(model.password_hash).filter(model.email == user.email).scalar()
        if not stored or not pwd.verify(password, stored):
            print("The new password did not reach the database, so nothing was changed.",
                  file=sys.stderr)
            return 1
        print(f"Password updated for {user.name} <{user.email}> ({user.role}).")
        print("Existing sessions stay signed in -- the cookie is not tied to the password.")
        return 0
    finally:
        if db is not None:
            db.close()
        ops.close()


if __name__ == "__main__":
    raise SystemExit(main())
