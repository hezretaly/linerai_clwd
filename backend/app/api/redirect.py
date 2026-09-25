"""The click hop. Public, no login -- a buyer follows it from their inbox.

A link straight to the dealership's own finance application is invisible to
this system: the buyer's browser talks to the dealer's site and nobody tells
us. So a send rewrites the link to `/r/<token>`, which records the click and
then forwards to the real page.

What that count honestly is, and is not:

* It is **clicks on the link we sent**, not applications completed. Whether the
  buyer filled the form in is on the dealer's side and nothing reports it back.
* A link the rep deleted from the draft has no token and can never register,
  which is why a missing token is stored as `None` rather than a zero.
* Some mail clients and security scanners follow links before a human does, so
  a count can lead the human by one. Recording the first and last click is what
  makes that visible rather than hidden inside a single number.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.settings import live_settings
from app.db import SessionLocal, current_host, current_store, utcnow
from app.events import emit
from app.models import LinkClick, Outreach

log = logging.getLogger("liner.redirect")

router = APIRouter(tags=["redirect"])


def opens_between(db: Session, kind: str, since, until=None) -> dict:
    """"An application opened in [since, until)" -- the one definition, on
    the clock of the *open* itself, that both the Overview card's emailed and
    website halves now use.

    Before this, the emailed half was windowed on send time
    (`Outreach.created_at`) while the website half was windowed on click time
    (`LinkClick.created_at`): a link sent 25 hours ago and opened a minute ago
    added zero to a card labelled "last 24 hours", and an old send re-opened
    just outside the window silently vanished the moment the send itself
    turned 24h old. `Outreach.first_clicked_at` already records the moment of
    the open (`api/redirect.py`'s own `follow()`), so the emailed half is
    windowed on it instead, while keeping its one-buyer-one-open rule: a
    second click on the same send is not a second open.
    """
    q = db.query(Outreach).filter(
        Outreach.kind == kind, Outreach.status == "sent", Outreach.first_clicked_at >= since,
    )
    if until is not None:
        q = q.filter(Outreach.first_clicked_at < until)
    emailed = q.count()

    site_q = db.query(LinkClick).filter(LinkClick.kind == kind, LinkClick.created_at >= since)
    if until is not None:
        site_q = site_q.filter(LinkClick.created_at < until)
    site_rows = site_q.all()
    website = sum(1 for r in site_rows if r.source == "website")
    chat = sum(1 for r in site_rows if r.source == "chat")

    sent_q = db.query(Outreach).filter(
        Outreach.kind == kind, Outreach.status == "sent", Outreach.created_at >= since,
    )
    if until is not None:
        sent_q = sent_q.filter(Outreach.created_at < until)

    return {
        "emailed": emailed,
        "website": website,
        "chat": chat,
        "site": website + chat,
        "sent": sent_q.count(),
    }


def _target(db: Session, record: Outreach) -> str:
    if record.kind == "credit_application":
        return (live_settings(db).credit_application_url or "").strip()
    return ""


@router.get("/r/{token}")
def follow(token: str) -> RedirectResponse:
    # Its own session: this runs on a buyer's click, outside any dealer request,
    # and must not depend on one being open.
    db = SessionLocal()
    try:
        record = db.query(Outreach).filter_by(click_token=token).one_or_none()
        if record is None:
            raise HTTPException(404, "That link has expired or was never issued.")

        destination = _target(db, record)
        if not destination:
            # The dealership changed or cleared the link after sending. Sending
            # the buyer to a guess would be worse than telling them plainly.
            raise HTTPException(
                410,
                "This application link is no longer set up. Call the dealership and "
                "they will send you a new one.",
            )

        first = record.click_count == 0
        now = utcnow()
        record.click_count += 1
        record.first_clicked_at = record.first_clicked_at or now
        record.last_clicked_at = now
        db.commit()

        if first:
            # Only the first one is news. A buyer who opens the form three times
            # has not done three things.
            emit(db, "outreach.opened", {
                "outreach_id": record.id,
                "lead_id": record.lead_id,
                "kind": record.kind,
            })
        return RedirectResponse(destination, status_code=302)
    finally:
        db.close()


def store_path(path: str) -> str:
    """`path` under the store this request is for, as a public link.

    A link a browser follows arrives with no store but the one written in it:
    `/r/<token>` unprefixed is looked up in the *default* store's file, so an
    Alsbou application link resolved to nothing and answered 404 -- the same
    hole `withStore` closes in the browser, here on a URL we compose.
    """
    slug = current_store.get()
    # On the dealership's own subdomain the host already names the store, and
    # a prefix there would name it twice.
    return f"/{slug}{path}" if slug and slug != current_host.get() else path


#: The storefront links that are counted, by the kind a press is filed under.
#: Each resolves to a URL the dealership configured -- never to one written in
#: the link, which would make this an open redirect anyone could point
#: anywhere under our name.
COUNTED = {"credit-application": "credit_application"}


def site_path(kind: str) -> str:
    """The counted hop for `kind`, before any store is put on it."""
    slug = next(k for k, v in COUNTED.items() if v == kind)
    return f"/r/site/{slug}"


def site_hop(kind: str) -> str:
    """The counted path a storefront link to `kind` is rewritten to."""
    return store_path(site_path(kind))


#: Where a counted press can come from. Closed, because it is written straight
#: into a row from a query string anybody can type.
SOURCES = {"website", "chat"}


@router.get("/r/site/{what}")
def follow_site(what: str, source: str = Query("website", alias="from")) -> RedirectResponse:
    """A storefront visitor opening the dealer's finance page, counted.

    **The website half of Credit applications.** Their Financing banner, nav
    item and promo lead to their own page on their own host; pressed straight
    through, nothing here would ever know. So the storefront's link to the
    configured application URL is rewritten to this hop, which files one
    anonymous press and forwards -- in a new tab, so the buyer keeps the
    storefront and the chat they were in. Clicks, never completions.
    """
    kind = COUNTED.get(what)
    if kind is None:
        raise HTTPException(404, "There is no such link.")
    db = SessionLocal()
    try:
        destination = (live_settings(db).credit_application_url or "").strip()
        if not destination:
            raise HTTPException(
                410,
                "This dealership has not set up its finance application link. "
                "Call them and they will send it to you.",
            )
        # The chat's application button comes through here too -- the same
        # act, so the same card on the overview, told apart by `source`.
        db.add(LinkClick(kind=kind, source=source if source in SOURCES else "website"))
        db.commit()
        return RedirectResponse(destination, status_code=302)
    finally:
        db.close()
