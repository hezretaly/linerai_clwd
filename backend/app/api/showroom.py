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

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agent.phrasing import cased
from app.agent.tools import home_location, inquiry_url, offerable
from app.api.settings import live_settings
from app.profile import brand, site
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


#: The orders their own results toolbar offers, keyed by what the page sends.
#:
#: **An unpriced car is not the cheapest car, in either direction.** SQLite
#: sorts NULL before every number, so a plain `price.asc()` puts all 119 of
#: Craig and Landreth's call-for-price cars at the top of "Price: low to high"
#: -- a buyer asking for the cheapest thing on the lot gets a screen of cars
#: nobody can quote them. `is_(None)` first in the key pushes them last in both
#: price orders, which is the same rule `search_inventory` follows.
#:
#: Make/Model A-Z is the default because it is the one order that is *about the
#: cars* rather than about the money: it is what their own page defaults to, and
#: "most expensive first" as the first impression of somebody's lot is a choice
#: nobody made on purpose. VIN breaks every tie, so paging is stable -- without
#: a total order SQLite may return the same car on page one and page two.
SORTS: dict[str, list] = {
    # Lowercased, because SQLite compares text by byte: one dealer's export is
    # all caps and another's is mixed, and `A-Z` that puts every SHOUTED make
    # above every Title-cased one is not alphabetical to the person reading it.
    "az": [func.lower(Vehicle.make).asc(), func.lower(Vehicle.model).asc(),
           Vehicle.year.desc()],
    "price_low": [Vehicle.price.is_(None).asc(), Vehicle.price.asc()],
    "price_high": [Vehicle.price.is_(None).asc(), Vehicle.price.desc()],
    "year_new": [Vehicle.year.desc()],
    "year_old": [Vehicle.year.asc()],
}
DEFAULT_SORT = "az"


def _car(v: Vehicle, home: str = "") -> dict:
    """One card. Every field here is on the dealer's own public listing.

    `home` is the dealership's own address, lowercased, and it is what decides
    whether the card says where the car is standing. Alsbou's export stamps
    "Santa Ana" on all 69 of their cars, which is the address at the top of the
    page -- printed on every row it is noise, and noise is how the one row that
    says *Riverside* stops being read. The same comparison `tools.home_location`
    makes for the note the assistant raises, so a card and a sentence about the
    same car cannot disagree about whether it is somewhere else.
    """
    raw = loads(v.raw_json or "{}", {})
    where = str(raw.get("location") or "")
    return {
        "vin": v.vin,
        "title": f"{v.year} {cased(v.make)} {cased(v.model)}".strip(),
        "trim": cased(v.trim or ""),
        "year": v.year,
        "make": cased(v.make),
        "model": cased(v.model),
        "price": v.price,
        "mileage": v.mileage,
        "body_style": v.body_style or "",
        "features": loads(v.features_json, [])[:4],
        "photo_url": v.photo_url,
        "listing_url": v.listing_url or "",
        # Which of the group's lots it is on, and only when that is not the one
        # whose address is at the top of the page. A dealership with three
        # addresses lists them in one feed, and a car 90 minutes away should
        # say where the buyer would be driving; a car on the forecourt they
        # are reading about should not.
        "location": where if where and where.lower() not in home else "",
        # Derived, never stored: their own enquiry form is the listing URL with
        # `?mode=inquiry`, and it only exists where there is no price to show.
        "inquiry_url": inquiry_url(v),
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
        "makes": [{"name": cased(name), "count": count} for name, count in makes],
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
    sort: str = Query(DEFAULT_SORT),
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

    # A 400 rather than a fall back to A-Z, the same rule `/api/overview/trends`
    # follows for an unknown range: answering a typo with the default order
    # shows the wrong grid under the right caption on the toolbar, and nothing
    # on the page contradicts it.
    if sort not in SORTS:
        raise HTTPException(400, f"Unknown sort: {sort}")

    total = query.count()
    # Once per request, not once per card: a page is 24 rows and the
    # dealership's address does not change between them.
    home = home_location(db)
    cars = (
        query.order_by(*SORTS[sort], Vehicle.vin.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "dealership": identity(db),
        "greeting": live_settings(db).greeting,
        "vehicles": [_car(v, home) for v in cars],
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
