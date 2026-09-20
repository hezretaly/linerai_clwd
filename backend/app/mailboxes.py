"""Which dealership an email is for, on a host that serves several.

One sending domain, one mailbox per dealership: `alsboucars@linerai.us`
writes to Alsbou, `craigsbestcars@linerai.us` to Craig and Landreth. The
provider verifies the *domain*, so every mailbox on it is legal to send from
on one key -- the same fact that lets `founder@` and `cto@` share it -- and a
new dealership is a line in its profile, not a new credential.

The Worker posts to one URL with no store in the path, so the intake has to
work out the store from the envelope. Two rules, in order:

- **`reply+<token>@`** was minted by a send, and the send's row is in exactly
  one store's `outreach` table. Every seeded store is asked; the first that
  holds the token wins.
- **A mailbox** names the dealership whose profile declares it. Compared on
  the local part, the way `is_ours` and the Worker compare, because the
  domain in the envelope is whatever Cloudflare is routing.

Anything else -- `sales@`, `support@`, a stranger -- stays with the default
store, which is what it always was. Nothing here creates a database: a store
without one is skipped, for the reason `ops_inbox._each` skips it.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from app import profile
from app.db import SessionLocal, current_store, has_database
from app.stores import known_stores

#: The one reading of `reply+<token>@`, shared with the intake rather than
#: copied: two patterns for one address is how a token routes on one side
#: and fails to on the other.
from app.email_intake import REPLY_RE  # noqa: E402


@contextmanager
def using(slug: str) -> Iterator[None]:
    """Run a block as one store, and put the previous one back afterwards.

    The profile readers and `SessionLocal()` both follow `current_store`, so
    asking "what is Alsbou's mailbox" from a request that arrived for nobody
    means saying, for a moment, that this is Alsbou. Reset in a `finally`,
    because a ContextVar left set is a later request reading the wrong
    dealership's rows.

    **An empty slug leaves the store alone.** It does not mean "the default
    store": `claim_unresolved` runs under whichever store the buyer was
    minted in and re-places their earlier mail with no slug of its own, and
    setting "" there switched it to the default store mid-pass -- so the
    receipts it had just marked `received` were looked for in a file that
    did not hold them, and stayed `received` for ever.
    """
    if not slug:
        yield
        return
    token = current_store.set(slug)
    try:
        yield
    finally:
        current_store.reset(token)


def local_part(address: str) -> str:
    """`Name <alsboucars@linerai.us>` -> `alsboucars`."""
    bare = (address or "").strip()
    if "<" in bare and ">" in bare:
        bare = bare[bare.index("<") + 1:bare.index(">")]
    return bare.strip().lower().partition("@")[0]


def mailbox_for(slug: str) -> str:
    """One store's mailbox local part, or "" when its profile declares none."""
    with using(slug):
        return profile.mailbox()


def mailboxes() -> dict[str, str]:
    """Every seeded store's mailbox, local part -> slug. Unseeded stores are
    left out: routing mail to a store with no database is a 500 dressed as
    delivery."""
    out: dict[str, str] = {}
    for slug in known_stores():
        if not has_database(slug):
            continue
        box = mailbox_for(slug)
        if box and box not in out:
            out[box] = slug
    return out


def store_for_token(token: str) -> str:
    """The store whose `outreach` table minted this reply token, or ""."""
    from app.models import Outreach

    wanted = (token or "").strip().lower()
    if not wanted:
        return ""
    for slug in known_stores():
        if not has_database(slug):
            continue
        try:
            with SessionLocal(slug) as db:
                hit = (
                    db.query(Outreach.id)
                    .filter(func.lower(Outreach.reply_token) == wanted)
                    .first()
                )
        except OperationalError:
            continue
        if hit is not None:
            return slug
    return ""


def store_for(to_address: str) -> str:
    """Which store this envelope belongs to, or "" for the default store."""
    found = REPLY_RE.search((to_address or "").strip().strip("<>").split("<")[-1].strip(">"))
    if found:
        return store_for_token(found.group(1))
    return mailboxes().get(local_part(to_address), "")
