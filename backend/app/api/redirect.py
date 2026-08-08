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

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.settings import live_settings
from app.db import SessionLocal, utcnow
from app.events import emit
from app.models import Outreach

log = logging.getLogger("liner.redirect")

router = APIRouter(tags=["redirect"])


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
