"""The dealership's own front page, and the one endpoint behind it.

**What this is for.** A demo is a link you send somebody. Handing a prospect
`/chat` is handing them a chat window floating on nothing -- correct, and it
answers none of the question they actually have, which is *what does this look
like on my website*. So this is that: their name, their logo, their colour,
their address and phone, their real cars, and Liner sitting in the corner the
way it would sit on their own site.

**Public, and therefore narrower than the dealer's view of the same rows.**
`vehicle_out` carries `rules` and `mention_count` -- an internal note reading
"Consignment, owner has not signed the agreement yet" and a count of how many
buyers have been quoted the car. Neither belongs on a page anybody can open,
so this composes its own payload rather than filtering a richer one: a
serializer that has to remember to drop a field is one that will eventually
forget.

**The cars come through `tools.offerable`,** the same predicate
`search_inventory` narrows with. Two copies is how the do-not-discuss vehicle
ends up rendered on the page beside the chat window that refuses to talk about
it.

**Nothing here is a second source of truth.** The dealership row, the assistant
greeting, the brand and the integration state are each read from the one place
that already owns them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.agent.tools import offerable
from app.api.settings import live_settings
from app.brand import brand
from app.config import settings
from app.db import get_db
from app.models import Dealership, Vehicle
from app.schemas.serialize import dealership_out as _row_out
from app.schemas.serialize import loads

router = APIRouter(prefix="/showroom", tags=["showroom"])

#: Enough of a lot to look like a lot, and few enough to send over a phone
#: connection in a demo. The page asks for more as the buyer scrolls.
PAGE_SIZE = 24


def _car(v: Vehicle) -> dict:
    """One card. Every field here is on the dealer's own public listing."""
    return {
        "vin": v.vin,
        "title": f"{v.year} {v.make} {v.model}".strip(),
        "trim": v.trim or "",
        "year": v.year,
        "make": v.make,
        "model": v.model,
        "price": v.price,
        "mileage": v.mileage,
        "body_style": v.body_style or "",
        "features": loads(v.features_json, [])[:4],
        "photo_url": v.photo_url,
        "listing_url": v.listing_url or "",
    }


def identity(db: Session) -> dict:
    """Who this instance is, for any surface that has to say so.

    Public and unauthenticated on purpose: the login form, the chat header and
    the call header all print the dealership's name, and all three used to
    print the literal string "Riverside Auto" because there was nowhere to
    read it from. A rebranded instance then greeted a prospect's buyer as
    somebody else's showroom.

    Composed from the one dealership serializer plus the brand, rather than
    from the row again -- the fields are the same fields, and the way they
    start disagreeing is a second function that reads the same columns.
    """
    row = db.query(Dealership).first()
    out = _row_out(row) if row else {
        "id": "", "name": "", "timezone": "", "hours": {},
        "address": "", "phone": "", "website_url": "",
    }
    return {**out, "brand": brand()}


@router.get("")
def showroom(
    offset: int = Query(0, ge=0),
    limit: int = Query(PAGE_SIZE, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """The page's whole payload: who they are, what is on the lot, what works.

    `channels` is counted rather than declared, the same rule the buyer
    timeline's channel strip follows. Voice is off unless a dealership has
    turned it on, and a Call button that opens a page saying voice is
    unavailable is worse than no button -- so the page is told, and does not
    draw one.
    """
    query = offerable(db.query(Vehicle)).order_by(Vehicle.price.desc())
    total = query.count()
    cars = query.offset(offset).limit(limit).all()
    return {
        "dealership": identity(db),
        "greeting": live_settings(db).greeting,
        "vehicles": [_car(v) for v in cars],
        "total": total,
        "offset": offset,
        "channels": {
            "chat": True,
            # A key alone does not answer the phone: taking calls is a
            # decision a dealership makes, not a side effect of configuring
            # the chat agent.
            "voice": bool(settings.voice_provider),
        },
    }


@router.get("/dealership")
def dealership(db: Session = Depends(get_db)) -> dict:
    """Just the identity, for surfaces that do not want the lot with it.

    The login form is the reason it is separate: it needs one string, and
    fetching a page of vehicles to render a subtitle would put the whole lot
    on the wire before anybody has signed in.
    """
    return identity(db)
