"""Which dealership an email is for, on a host that serves several.

Two shapes of dealership address, and a store has one or the other:

- **A mail domain of its own**, a subdomain of the sending domain:
  `sales@alsbou.linerai.us`, with replies at `reply+<token>@alsbou.linerai.us`.
  The whole domain is that store's, so anything delivered to it is filed
  there -- `sales@`, a stranger guessing `info@`, whatever the Worker keeps.
- **A mailbox on the shared domain**: `craigandlandreth@linerai.us`. Matched
  on the local part, the way `is_ours` and the Worker compare, because the
  domain in the envelope is whatever Cloudflare is routing.

The provider verifies a *domain*, so every mailbox on one sends on one key and
a new dealership on the shared domain is a line in its profile. A subdomain is
a domain of its own to the provider and to Cloudflare, so it is verified and
routed once, by whoever runs the deployment.

The Worker posts to one URL with no store in the path, so the intake works out
the store from the envelope, in this order:

- **`reply+<token>@`** was minted by a send, and the send's row is in exactly
  one store's `outreach` table. Every seeded store is asked; the first that
  holds the token wins. First, because the domain it arrives on is only what
  the buyer's client kept.
- **A store's own mail domain**, then **a shared-domain mailbox**.

Anything else -- `sales@linerai.us`, `support@`, a stranger -- stays with the
default store, which is what it always was. Nothing here creates a database: a
store without one is skipped, for the reason `ops_inbox._each` skips it.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import func

from app import profile
from app.db import MISSING_TABLE, SessionLocal, current_store, has_database
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
    """`Name <sales@alsbou.linerai.us>` -> `sales`."""
    bare = (address or "").strip()
    if "<" in bare and ">" in bare:
        bare = bare[bare.index("<") + 1:bare.index(">")]
    return bare.strip().lower().partition("@")[0]


def domain_of(address: str) -> str:
    """`Name <sales@alsbou.linerai.us>` -> `alsbou.linerai.us`."""
    bare = (address or "").strip()
    if "<" in bare and ">" in bare:
        bare = bare[bare.index("<") + 1:bare.index(">")]
    return bare.strip().lower().rpartition("@")[2].rstrip(".")


def mailbox_for(slug: str) -> str:
    """One store's mailbox local part, or "" when its profile declares none."""
    with using(slug):
        return profile.mailbox()


def domain_for(slug: str) -> str:
    """One store's own mail domain, or "" when it lives on the shared one."""
    with using(slug):
        return profile.own_mail_domain()


def _seeded() -> list[str]:
    """Stores with a database. Routing mail to a store with none is a 500
    dressed as delivery, so they are left out of every map here."""
    return [slug for slug in known_stores() if has_database(slug)]


def mailboxes() -> dict[str, str]:
    """Every seeded shared-domain store's mailbox, local part -> slug.

    A store with a domain of its own is **not** here, and that is the rule
    that matters: Alsbou's mailbox is `sales`, and matched on the local part
    it would claim `sales@linerai.us` -- the deployment's own dealership
    address -- for Alsbou.
    """
    out: dict[str, str] = {}
    for slug in _seeded():
        if domain_for(slug):
            continue
        box = mailbox_for(slug)
        if box and box not in out:
            out[box] = slug
    return out


def domains() -> dict[str, str]:
    """Every seeded store's own mail domain, domain -> slug."""
    out: dict[str, str] = {}
    for slug in _seeded():
        own = domain_for(slug)
        if own and own not in out:
            out[own] = slug
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
        except MISSING_TABLE:
            continue
        if hit is not None:
            return slug
    return ""


def store_for(to_address: str) -> str:
    """Which store this envelope belongs to, or "" for the default store."""
    found = REPLY_RE.search((to_address or "").strip().strip("<>").split("<")[-1].strip(">"))
    if found:
        return store_for_token(found.group(1))
    domain = domain_of(to_address)
    if domain:
        owner = domains().get(domain)
        if owner:
            return owner
    return mailboxes().get(local_part(to_address), "")
