"""Listing extraction.

Ladder, in order of preference: JSON-LD schema.org/Vehicle -> a platform
adapter -> nothing. A surprising share of dealer sites emit JSON-LD, and it
hands over VIN, price, mileage and images without a single CSS selector.

The last two rungs of the plan's ladder are not here: an LLM extraction pass
(needs a key) and CSV upload (lives in csv_import.py, and is the fallback that
always works).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from selectolax.parser import HTMLParser

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")  # no I, O or Q
INT_RE = re.compile(r"[\d,]+")


@dataclass
class Listing:
    vin: str = ""
    year: int | None = None
    make: str = ""
    model: str = ""
    trim: str = ""
    price: int | None = None
    mileage: int | None = None
    body_style: str = ""
    seats: int | None = None
    photo_url: str = ""
    listing_url: str = ""
    features: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return bool(self.vin) and not self.errors


def valid_vin(vin: str) -> bool:
    return bool(VIN_RE.match((vin or "").upper().strip()))


#: A VIN from before the 17-character standard. Vehicles built up to model year
#: 1980 carry shorter ones -- 11 to 16 characters, no fixed length -- and they
#: are real cars a dealer really sells: a 1978 Corvette and a 1979 SL-Class
#: turned up in the first real export this repository was given.
OLD_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{11,16}$")


def usable_vin(vin: str) -> bool:
    """A VIN we are willing to key a vehicle on.

    Looser than `valid_vin`, and deliberately used only for a file somebody
    exported rather than for a page somebody scraped. In an export a short VIN
    is a classic car; on a scraped listing card it is far more likely a
    selector reading the wrong element, and accepting it would fill the lot
    with rows keyed on a stock number or a phone extension. So the crawl stays
    strict and the importer does not.
    """
    text = (vin or "").upper().strip()
    return valid_vin(text) or bool(OLD_VIN_RE.match(text))


def to_int(value) -> int | None:
    """'$18,900' -> 18900. 'Call for price' -> None, which is not an error."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = INT_RE.search(str(value))
    if not match:
        return None
    try:
        return int(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _walk(node, out: list[dict]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, out)
    elif isinstance(node, dict):
        if "@graph" in node:
            _walk(node["@graph"], out)
        types = node.get("@type", "")
        types = [types] if isinstance(types, str) else list(types or [])
        if any(t in {"Vehicle", "Car", "Product"} for t in types):
            out.append(node)


def parse_jsonld(html: str, url: str = "") -> Listing | None:
    tree = HTMLParser(html)
    blocks: list[dict] = []
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            _walk(json.loads(script.text()), blocks)
        except (ValueError, TypeError):
            continue
    if not blocks:
        return None

    node = blocks[0]
    offer = node.get("offers") or {}
    if isinstance(offer, list):
        offer = offer[0] if offer else {}

    listing = Listing(listing_url=url, raw=node)
    listing.vin = str(node.get("vehicleIdentificationNumber") or node.get("sku") or "").upper().strip()
    listing.make = _name_of(node.get("brand") or node.get("manufacturer"))
    listing.model = _name_of(node.get("model"))
    listing.trim = str(node.get("vehicleConfiguration") or node.get("trim") or "").strip()
    listing.body_style = str(node.get("bodyType") or "").strip()
    listing.price = to_int(offer.get("price") or node.get("price"))
    listing.year = to_int(node.get("vehicleModelDate") or node.get("modelDate")
                          or node.get("productionDate"))
    listing.seats = to_int(node.get("seatingCapacity"))

    odometer = node.get("mileageFromOdometer") or {}
    if isinstance(odometer, dict):
        listing.mileage = to_int(odometer.get("value"))
    else:
        listing.mileage = to_int(odometer)

    image = node.get("image")
    if isinstance(image, list):
        image = image[0] if image else ""
    if isinstance(image, dict):
        image = image.get("url", "")
    listing.photo_url = str(image or "")

    if not listing.vin:
        listing.errors.append("no VIN in the JSON-LD block")
    elif not valid_vin(listing.vin):
        listing.errors.append(f"VIN {listing.vin!r} is not 17 valid characters")
    if listing.price is None:
        # Not an error. "Call for price" is a real listing state, and the
        # vehicle still belongs in inventory -- Liner just cannot quote it.
        listing.raw["price_note"] = "no price published"

    if not listing.year or not listing.make:
        title = tree.css_first("h1")
        if title:
            words = title.text().split()
            if words and to_int(words[0]) and not listing.year:
                listing.year = to_int(words[0])
            if len(words) > 1 and not listing.make:
                listing.make = words[1]

    return listing


def _name_of(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name", "")).strip()
    return str(value or "").strip()


class Adapter:
    """Platform-specific fallback when a site emits no JSON-LD.

    Each adapter is a small class with matches() and parse(). None ship yet --
    writing one needs a real site to write it against (§16 Q2). The interface
    is here so adding one is a file, not a refactor.
    """

    name = "base"

    def matches(self, html: str) -> bool:
        raise NotImplementedError

    def parse(self, html: str, url: str) -> Listing | None:
        raise NotImplementedError


ADAPTERS: list[Adapter] = []


class ListAdapter:
    """A platform whose *listing* page already carries every field.

    The rung above assumes one page is one vehicle, which is what a site
    emitting JSON-LD on its detail pages gives you. Some platforms do not work
    that way: Dealer Car Search puts VIN, price, mileage and a photo on every
    card of the search results, and 481 vehicles is 481 detail fetches to
    learn what five list pages already said. That is not an optimisation -- it
    is the difference between a polite crawl and one a dealer would be right
    to block.

    So a list adapter takes one page and yields many listings, and
    `pipeline.run_ingest` tries it before falling back to discovering detail
    pages. `page_url` is how it paginates; returning None ends the crawl.
    """

    name = "base-list"

    def for_dealer(self, dealer_id: str) -> "ListAdapter":
        """This adapter, narrowed to one lot. The default ignores it.

        Which lot to keep is a fact about the dealership, and the dealership
        is chosen per deployment and read per request -- so it cannot be baked
        into the registered instance at import time, which is what it was.
        Most platforms list one store and have nothing to narrow.
        """
        return self

    def matches(self, html: str, url: str) -> bool:
        raise NotImplementedError

    def parse_list(self, html: str, url: str) -> list[Listing]:
        raise NotImplementedError

    def page_url(self, base: str, page: int, html: str) -> str | None:
        """The next page, or None when there is no next page."""
        return None


LIST_ADAPTERS: list[ListAdapter] = []


def extract_list(html: str, url: str = "") -> tuple[list[Listing], str]:
    """Every vehicle one listing page describes. ([], "none") if none can."""
    for adapter in LIST_ADAPTERS:
        if adapter.matches(html, url):
            return adapter.parse_list(html, url), adapter.name
    return [], "none"


def list_adapter_for(html: str, url: str = "") -> ListAdapter | None:
    for adapter in LIST_ADAPTERS:
        if adapter.matches(html, url):
            return adapter
    return None


def extract(html: str, url: str = "") -> tuple[Listing | None, str]:
    """Returns (listing, method)."""
    listing = parse_jsonld(html, url)
    if listing is not None:
        return listing, "jsonld"
    for adapter in ADAPTERS:
        if adapter.matches(html):
            return adapter.parse(html, url), adapter.name
    return None, "none"
