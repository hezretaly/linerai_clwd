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

import re

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agent.tools import offerable
from app.api.settings import live_settings
from app.brand import brand, site
from app.config import settings
from app.db import get_db
from app.models import Dealership, Vehicle
from app.schemas.serialize import dealership_out as _row_out
from app.schemas.serialize import loads

router = APIRouter(prefix="/showroom", tags=["showroom"])

#: Enough of a lot to look like a lot, and few enough to send over a phone
#: connection in a demo. The page asks for more as the buyer scrolls.
PAGE_SIZE = 24

#: The four bands their own front page offers, half-open so a car at exactly
#: $15,000 lands in one band rather than in two.
PRICE_BANDS = [
    ("Under $15K", None, 15_000),
    ("$15K - $30K", 15_000, 30_000),
    ("$30K - $50K", 30_000, 50_000),
    ("$50K and over", 50_000, None),
]

#: Splitting a search box on non-alphanumerics rather than on whitespace.
#: `"Do you have a BMW X5?"` tokenised on spaces gives `x5?`, which matches
#: nothing -- and `"BMW X5"` works, which is exactly what kept that bug
#: invisible in the chat for so long.
WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> list[str]:
    return WORD.findall((text or "").lower())[:6]


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
    return {**out, "brand": brand(), "site": site()}


def _facets(db: Session) -> dict:
    """What the lot actually contains, counted.

    Their own site prints "Chevrolet (74)" beside each make and offers four
    price bands. Both are honest here only if the numbers come from rows --
    a browse filter that promises 74 cars and shows 9 is worse than no filter,
    and it is the single easiest thing on a demo page to get wrong.

    Counted over the *offerable* lot, so a make represented only by a sold or
    do-not-discuss car does not appear at all rather than appearing and
    leading to an empty page.
    """
    makes = (
        offerable(db.query(Vehicle.make, func.count(Vehicle.id)))
        .filter(Vehicle.make != "")
        .group_by(Vehicle.make)
        .order_by(func.count(Vehicle.id).desc())
        .all()
    )
    styles = (
        offerable(db.query(Vehicle.body_style, func.count(Vehicle.id)))
        .filter(Vehicle.body_style != "")
        .group_by(Vehicle.body_style)
        .order_by(func.count(Vehicle.id).desc())
        .all()
    )
    bands = []
    for label, low, high in PRICE_BANDS:
        query = offerable(db.query(Vehicle)).filter(Vehicle.price.isnot(None))
        if low is not None:
            query = query.filter(Vehicle.price >= low)
        if high is not None:
            query = query.filter(Vehicle.price < high)
        bands.append({"label": label, "min": low, "max": high, "count": query.count()})
    return {
        "makes": [{"name": name, "count": count} for name, count in makes],
        # Empty for a Dealer Car Search lot, and that is a real answer rather
        # than a gap: body style lives only in their sidebar filters, so the
        # adapter leaves it empty rather than deriving it. The page draws no
        # By Type row at all instead of ten links that all return nothing.
        "body_styles": [{"name": name, "count": count} for name, count in styles],
        "price_bands": bands,
    }


@router.get("")
def showroom(
    offset: int = Query(0, ge=0),
    limit: int = Query(PAGE_SIZE, ge=1, le=100),
    q: str = Query(""),
    make: str = Query(""),
    body_style: str = Query(""),
    min_price: int | None = Query(None, ge=0),
    max_price: int | None = Query(None, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """The page's whole payload: who they are, what is on the lot, what works.

    `channels` is counted rather than declared, the same rule the buyer
    timeline's channel strip follows. Voice is off unless a dealership has
    turned it on, and a Call button that opens a page saying voice is
    unavailable is worse than no button -- so the page is told, and does not
    draw one.

    The filters are the ones their own front page offers -- a keyword box, a
    make list with counts, four price bands. They run against the same rows
    the assistant searches, so a buyer who narrows to "Chevrolet under $15k"
    and then asks the same question in the chat gets the same cars.
    """
    query = offerable(db.query(Vehicle))
    if make.strip():
        query = query.filter(func.lower(Vehicle.make) == make.strip().lower())
    if body_style.strip():
        query = query.filter(Vehicle.body_style.ilike(f"%{body_style.strip()}%"))
    if min_price is not None:
        query = query.filter(Vehicle.price >= min_price)
    if max_price is not None:
        query = query.filter(Vehicle.price < max_price)
    # Every word must hit. This is a keyword box, not the chat: somebody types
    # "silverado 4wd" and expects the trucks that are both, and ORing gives
    # them the whole lot because one car is always a Chevrolet and another is
    # always 4WD.
    #
    # It is deliberately *not* the scoring `search_inventory` does with the
    # same words. That tool is answering a sentence and keeps its best guesses;
    # this one is narrowing a grid, and a grid that answers "Do you have a BMW
    # X5?" with all 112 vehicles ranked is worse than one that answers with
    # nothing and leaves the question to the assistant in the corner, which is
    # what the sentence was for.
    #
    # What both share is the tokenising: split on non-alphanumerics, never on
    # spaces. `"BMW X5?"` on whitespace gives `x5?`, which matches nothing --
    # and `"BMW X5"` works, which is what kept that bug invisible in the chat.
    hay = func.lower(
        Vehicle.keywords + " " + Vehicle.make + " " + Vehicle.model
        + " " + Vehicle.trim + " " + Vehicle.body_style
    )
    for word in _words(q):
        query = query.filter(hay.like(f"%{word}%"))

    total = query.count()
    cars = query.order_by(Vehicle.price.desc()).offset(offset).limit(limit).all()
    return {
        "dealership": identity(db),
        "greeting": live_settings(db).greeting,
        "vehicles": [_car(v) for v in cars],
        "total": total,
        "offset": offset,
        "facets": _facets(db),
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
