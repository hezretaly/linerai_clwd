"""GMA (GMACRM) dealer sites -- Next.js pages served from `cdn.gma.to`.

Written against a real capture of `alsboucars.com/inventory`, trimmed into
`fixtures/gma_inventory.html` and `fixtures/gma_vdp.html`. Same rule as the
Dealer Car Search adapter: a parser for a page nobody has looked at is a
parser for a page that does not exist.

**The whole lot is in the listing page, as data.** Next.js streams its React
payload into the document as `self.__next_f.push([1, "..."])` chunks, and on
this platform that payload carries an `"inventory": [...]` array with every
car on the lot -- 88 of 88 on the day it was captured, against a stated
`"total": 88`. One request, no pagination, no card markup to scrape.

**It does not carry the price a buyer pays.** Each car's `priceInet` is the
*advertised* price, before the dealer's fees; the total including them is on
the car's own page (`summary.price`), and only the first handful of cards is
rendered with it. The gap is not a constant -- five of Alsbou's cars are
electric and pay no smog fee -- so it is never computed here: `price` is left
unstated on a list read, and `pipeline.read_details` reads the car's own page
for exactly the cars that need it (new to the lot, or advertised at a
different price than last time). CLAUDE.md, "The pricing disclosure states;
it does not compute."

**The car's own page also carries the dealer's costs** (`totCost`,
`totRecon`, a KBB valuation). `parse_detail` copies three named facts out of
it -- the VIN, the price including fees and the options list -- and nothing
else, so no cost can reach a row, the snapshot or the per-car cache, and from
there the model. The same rule `csv_import.NEVER_IMPORT` enforces for a file.

**The platform refuses anything that is not a browser** (HTTP 429 to httpx),
so `browser = True` and `pipeline.open_client` fetches it with a headless
Chromium -- JavaScript off, and nothing but the document requested, which is
enough: the payload is in the server-rendered HTML.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from app.ingest.csv_import import status_of
from app.ingest.extract import Listing, ListAdapter, to_int, valid_vin

#: The card-sized rendition every stored Alsbou photo already uses. The
#: payload gives the original's path; the CDN resizes on request.
CARD_PHOTO = "https://cdn.gma.to/fit-in/360x270/filters:quality(80):no_upscale()"

#: The platform's stock "photo coming soon" image. A car wearing it has no
#: picture yet, and the drawn placeholder says so more honestly.
COMING_SOON = "178898223090401"

PUSH_RE = re.compile(r'self\.__next_f\.push\(\[\d+,("(?:[^"\\]|\\.)*")\]\)', re.S)

#: Values the platform uses for "not stated".
BLANK = {"", "none", "n/a", "n / a", "na", "-", "null", "undefined"}


def flight(html: str) -> str:
    """The page's React payload, joined back into one text.

    The array this adapter reads straddles several chunks, so each chunk is
    decoded as the JSON string it is and the pieces are concatenated -- a
    regex over the raw HTML would stop at the first chunk boundary.
    """
    out = []
    for literal in PUSH_RE.findall(html):
        try:
            out.append(json.loads(literal))
        except json.JSONDecodeError:
            continue
    return "".join(out)


def stated(value) -> str:
    """A field's value, or "" for anything that means *not stated*.

    An RSC reference (`$38`) is a pointer to text elsewhere in the payload,
    not a value; `$$` is the escape for a literal dollar sign.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if text.startswith("$$"):
        text = text[1:]
    elif text.startswith("$"):
        return ""
    if text.lower() in BLANK or text.endswith("..."):
        return ""
    return text


def inventory_array(text: str) -> list[dict]:
    """The longest `"inventory": [...]` array whose elements are cars."""
    decoder = json.JSONDecoder()
    best: list[dict] = []
    for match in re.finditer(r'"inventory"\s*:\s*\[', text):
        try:
            array, _ = decoder.raw_decode(text, match.end() - 1)
        except json.JSONDecodeError:
            continue
        if (isinstance(array, list) and array and isinstance(array[0], dict)
                and "vin" in array[0] and len(array) > len(best)):
            best = array
    return best


def stated_total(text: str) -> int | None:
    """The lot size the page states beside its facets, if it states one."""
    match = re.search(r'"total"\s*:\s*(\d+)\s*,\s*"num_pages"', text) or re.search(
        r'"num_pages"\s*:\s*\d+\s*,\s*"page"\s*:\s*\d+\s*,\s*"total"\s*:\s*(\d+)', text)
    return int(match.group(1)) if match else None


def _carfax(car: dict) -> str:
    for entry in car.get("infoHistory") or []:
        if isinstance(entry, dict) and entry.get("id") == "CARFAX":
            url = stated(entry.get("reportUrl"))
            return url if url.startswith("https://") else ""
    return ""


def _photo(car: dict) -> str:
    path = stated(car.get("image1raw"))
    if not path or COMING_SOON in path:
        return ""
    if path.startswith("http"):
        return path
    return f"{CARD_PHOTO}/{path.lstrip('/')}"


class Gma(ListAdapter):
    name = "gma"
    browser = True

    def __init__(self, lot: str = "") -> None:
        # Every car on Alsbou's page is on "MAIN LOT". A group listing several
        # lots here would name the one to keep in the profile's `dealer_id`.
        self.lot = (lot or "").strip()

    def for_dealer(self, dealer_id: str) -> "Gma":
        return Gma(dealer_id)

    def matches(self, html: str, url: str = "") -> bool:
        return "self.__next_f.push" in html and "cdn.gma.to" in html

    def parse_list(self, html: str, url: str = "") -> list[Listing]:
        text = flight(html)
        cars = inventory_array(text)
        listings: list[Listing] = []
        for car in cars:
            if self.lot and stated(car.get("lotName")).lower() != self.lot.lower():
                continue
            listings.append(self._listing(car, url))
        total = stated_total(text)
        if total is not None and not self.lot and total > len(cars):
            # Half a lot read as a whole one marks the other half sold. One
            # error listing makes the run read as cut short, which is what
            # `pipeline.removal_risk` refuses to publish without a person
            # saying so.
            listings.append(Listing(errors=[
                f"the page states {total} cars but carries {len(cars)} -- "
                "the rest were not read"]))
        return listings

    def _listing(self, car: dict, url: str) -> Listing:
        vin = stated(car.get("vin")).upper()
        listing = Listing(
            vin=vin,
            year=to_int(stated(car.get("year"))),
            make=stated(car.get("make")),
            model=stated(car.get("model")),
            trim=stated(car.get("trim")),
            # Not stated here: see the module docstring.
            price=None,
            mileage=to_int(stated(car.get("mileage")) or stated(car.get("miles"))),
            body_style=stated(car.get("bodyType")).lower(),
            photo_url=_photo(car),
            listing_url=urljoin(url or "https://", stated(car.get("vdpUrl"))),
            status=status_of(stated(car.get("status"))),
        )
        raw = {
            "stock_number": stated(car.get("stockNo")),
            "advertised_price": str(to_int(stated(car.get("priceInet"))) or ""),
            "fuel_type": stated(car.get("fuelType")),
            "drivetrain": stated(car.get("driveTrain")),
            "transmission": stated(car.get("transmission")),
            "exterior_color": stated(car.get("colorExterior")),
            "interior_color": stated(car.get("colorInterior")),
            "engine": stated(car.get("engine")),
            "vehicle_history_url": _carfax(car),
        }
        listing.raw = {k: v for k, v in raw.items() if v}
        if not valid_vin(vin):
            listing.errors.append(f"VIN {vin!r} is not a valid VIN")
        return listing

    def detail_url(self, listing: Listing) -> str | None:
        return listing.listing_url or None

    def parse_detail(self, html: str, url: str, listing: Listing) -> dict | None:
        """The price including fees and the options list, and nothing else.

        Named fields are copied out; nothing is passed through. The vehicle
        object beside them holds the dealer's costs and a valuation, and the
        write-up the dealer wrote for the page (`desc`) is left out on purpose
        -- CLAUDE.md, "Their description is left out".
        """
        text = flight(html)
        decoder = json.JSONDecoder()
        options: list[str] | None = None
        price: int | None = None
        for match in re.finditer(r'\{"(?:id|vin)":', text):
            try:
                obj, _ = decoder.raw_decode(text, match.start())
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict) or stated(obj.get("vin")).upper() != listing.vin:
                continue
            if options is None and ("rawOptions" in obj or "catOptions" in obj):
                raw = obj.get("rawOptions")
                if not isinstance(raw, list):
                    raw = [o.get("optionName") for group in (obj.get("catOptions") or {}).values()
                           if isinstance(group, list) for o in group if isinstance(o, dict)]
                options = [stated(o) for o in raw if stated(o)]
            elif price is None and "price" in obj and "stock" in obj:
                # "10157.25": the cents are dropped, as every stored price here is whole.
                try:
                    price = int(float(stated(obj.get("price"))))
                except ValueError:
                    price = None
                # A car they will not price online states 0. That is "call
                # for price", and a buyer quoted $0 has been told a lie.
                if price is not None and price <= 0:
                    price = None
        if options is None and price is None:
            return None
        seen: set[str] = set()
        features = [o for o in (options or []) if not (o.lower() in seen or seen.add(o.lower()))]
        return {"vin": listing.vin, "price": price, "features": features}
