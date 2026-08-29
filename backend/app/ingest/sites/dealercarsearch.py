"""Dealer Car Search listing pages.

The first real platform adapter in this repository. Everything before it was
written against a fixture site that emits JSON-LD; DCS emits none, and instead
puts the whole vehicle on each card of the search results.

Written against a real capture of
`craigandlandrethcars.com/newandusedcars`, trimmed into
`fixtures/dealercarsearch_list.html`. That matters more than it sounds: the
`Adapter` docstring in `extract.py` has said since the beginning that writing
one needs a real site to write it against, and guessing a platform's markup
from its URL is how you get a parser for a page that does not exist.

**One card is one vehicle, and the card has everything.** VIN, year, make,
model, trim, price, mileage and a photo, so 481 vehicles cost five list pages
rather than 481 detail fetches. Two fields the cards do *not* carry are left
empty rather than guessed: body style and seat count live only in the sidebar
filters, so `search_inventory`'s body-style and min-seats filters simply
narrow nothing for this dealer. A missing field is a smaller error than an
invented one, and "third row" still matches through the keyword haystack.

**A DCS site can list more than one lot.** Craig and Landreth's Louisville
page mixes Louisville, Clarksville and Bullitt County stock, each card
carrying its own `data-dealer-id` and its own doc fee. This app holds exactly
one dealership, so `dealer_id` filters to the one being ingested -- without
it, Liner offers a buyer a car that is a two-hour drive from the showroom it
says it is standing in.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urljoin, urlunparse

from selectolax.parser import HTMLParser

from app.ingest.extract import Listing, ListAdapter, to_int, valid_vin

#: Every listing URL on this platform is /vdp/<id>/<slug>.
VDP_RE = re.compile(r"/vdp/\d+/", re.IGNORECASE)

#: "Page: 1 of 21 (481 vehicles)"
PAGER_RE = re.compile(r"Page:\s*(\d+)\s*of\s*(\d+)", re.IGNORECASE)

#: The label a card puts on the price varies per store -- "Craig's Best Price"
#: here, "Internet Value Price" for the Clarksville rows. The *class* does not:
#: `price-0` is the asking price and `price-1` is the doc fee. Matching on the
#: label would break the moment a dealer renames their own pricing.
PRICE_CLASS = "price-0"

#: How many cards a page will return. The site offers 25/50/75/100.
PAGE_SIZE = 100


def _feature(card, suffix: str) -> str:
    """The text of one `i13r_opt<X>` line, without its own label."""
    node = card.css_first(f"p.i13r_opt{suffix}")
    if node is None:
        return ""
    text = node.text()
    label = node.css_first("label")
    if label is not None:
        text = text.replace(label.text(), "", 1)
    return text.replace("\xa0", " ").strip()


class DealerCarSearch(ListAdapter):
    name = "dealercarsearch"

    def __init__(self, dealer_id: str = "") -> None:
        #: Empty takes every lot the page lists, which is right for a
        #: single-location dealer and wrong for this one.
        self.dealer_id = str(dealer_id or "").strip()

    def for_dealer(self, dealer_id: str) -> "DealerCarSearch":
        """A copy pinned to one store, built per crawl.

        A copy rather than a mutation: the registered instance is shared, and
        a crawl that reassigned its `dealer_id` would leave the next one
        filtering to whichever store ran last.
        """
        return DealerCarSearch(dealer_id)

    def matches(self, html: str, url: str = "") -> bool:
        # Two independent signals, and both are the platform's own rather than
        # this dealer's: the body class it ships on every listing page, and the
        # analytics blob it writes naming itself. A dealer restyling their site
        # keeps both.
        return 'site_platform": "dcs"' in html or "newandusedcars-dcs" in html

    def parse_list(self, html: str, url: str = "") -> list[Listing]:
        tree = HTMLParser(html)
        found: list[Listing] = []

        for card in tree.css("div.v-card"):
            listing = Listing()
            listing.raw = {}

            link = card.css_first("a[href]")
            for anchor in card.css("a[href]"):
                href = anchor.attributes.get("href", "")
                if VDP_RE.search(href):
                    link = anchor
                    break
            if link is not None:
                href = link.attributes.get("href", "")
                listing.listing_url = urljoin(url or "", href) if href else ""

            # Which lot this car is actually standing on. Read before anything
            # else, because a card from another store is dropped whole.
            owner = ""
            for node in card.css("[data-dealer-id]"):
                owner = node.attributes.get("data-dealer-id", "") or owner
                if owner:
                    break
            listing.raw["dealer_id"] = owner
            if self.dealer_id and owner and owner != self.dealer_id:
                continue

            listing.vin = _feature(card, "Vin").upper()
            listing.mileage = to_int(_feature(card, "Mileage"))

            # "2018 Dodge Challenger" -- year, make, then the model, which is
            # itself several words for a Sierra 3500HD or a Range Rover Sport.
            title = card.css_first("h4.vehicleTitle")
            words = (title.text().strip().split() if title else [])
            if words and to_int(words[0]):
                listing.year = to_int(words[0])
                words = words[1:]
            # Two-word makes exist and splitting on the first space gets them
            # wrong: "Land Rover Range Rover Sport" is not a Rover.
            for make in ("Land Rover", "Mercedes-Benz", "Alfa Romeo", "Aston Martin"):
                head = " ".join(words[: len(make.split())])
                if head.lower() == make.lower():
                    listing.make = make
                    words = words[len(make.split()):]
                    break
            else:
                listing.make = words[0] if words else ""
                words = words[1:]
            listing.model = " ".join(words)

            trim = card.css_first("span.vehicleTrim")
            listing.trim = trim.text().strip() if trim else ""

            price = card.css_first(f"div.{PRICE_CLASS} span.price-price")
            listing.price = to_int(price.text()) if price else None

            photo = card.css_first("img.v-photo")
            if photo is not None:
                # `src` is an inline SVG placeholder until the lazy loader
                # swaps it; the real one is in `data-src`.
                listing.photo_url = (photo.attributes.get("data-src") or "").strip()

            # Kept as features rather than columns: there is nowhere on a
            # vehicle row for a transmission, and they make the keyword
            # haystack much better at "black", "4WD", "leather".
            listing.features = [
                value for value in (
                    _feature(card, "Color"), _feature(card, "Trans"),
                    _feature(card, "Engine"), _feature(card, "Drive"),
                    _feature(card, "Interior"),
                ) if value
            ]
            stock = _feature(card, "Stock")
            if stock:
                listing.raw["stock_number"] = stock

            if not listing.vin:
                listing.errors.append("no VIN on the card")
            elif not valid_vin(listing.vin):
                listing.errors.append(f"VIN {listing.vin!r} is not 17 valid characters")
            if listing.price is None:
                # A real listing state, not a failure: the car belongs in
                # inventory, Liner simply cannot quote it.
                listing.raw["price_note"] = "no price published"

            found.append(listing)

        return found

    def page_url(self, base: str, page: int, html: str) -> str | None:
        """`?page=N&pagesize=100`, until the pager says we are at the end."""
        match = PAGER_RE.search(html)
        if match and page > int(match.group(2)):
            return None
        parts = urlparse(base)
        query = dict(parse_qsl(parts.query))
        query["page"] = str(page)
        query["pagesize"] = str(PAGE_SIZE)
        return urlunparse(parts._replace(query=urlencode(query)))
