#!/usr/bin/env python3
"""Rebuild Alsbou Motors' lot from a capture of their own inventory page.

    backend/.venv/bin/python scripts/alsbou_from_capture.py <capture.html>

**A dealer's own page is an export**, which is the rule this follows and the
reason it exists: nothing on this machine can reach `alsboucars.com` (the
egress proxy answers 403 to CONNECT), so their pages arrive as a saved file
and the lot is read out of it rather than crawled. The result is committed as
`backend/fixtures/alsbou/inventory.csv` and seeded from there.

**Three sources in one page, and none of them is complete.** Merging them is
the whole job, and preferring the wrong one loses a field silently:

  1. **The rendered cards** -- all 91 vehicles, each with the six cells their
     card prints: mileage, fuel, exterior, interior, drivetrain, transmission.
     Plus the stock number, the photo, the listing URL and their itemised
     pricing.
  2. **An embedded JSON payload** -- 40 of them, and the only place with the
     engine, the body type, the door and cylinder counts and the **Carfax
     report URL**.
  3. **JSON-LD** -- the first 50, carrying the price and mileage again. Kept
     as a cross-check rather than a source: where it disagrees with the card
     this says so instead of silently picking one.

And a fourth, outside the page: **the CSV this replaces.** Their earlier
capture had the engine, the body style and a Carfax link for every car it
held, and this one only carries those for the 40 in the payload -- so a row
already on file keeps what it had wherever the new capture is silent. That is
their own data from their own site, not a guess, and dropping it would make
the update a regression for two thirds of the lot.

**A field nobody states is left empty.** The storefront draws no cell for a
field the export did not carry, and a missing field is a smaller error than
an invented one -- the rule the body style and seat count already follow.
"""

from __future__ import annotations

import csv
import json
import pathlib
import re
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "backend/fixtures/alsbou/inventory.csv"

HEADER = [
    "vin", "year", "make", "model", "trim", "price", "mileage", "body_style",
    "seats", "features", "photo_url", "listing_url", "location", "dealer_phone",
    "doc_fee", "stock_number", "advertised_price", "fuel_type", "drivetrain",
    "transmission", "exterior_color", "interior_color", "engine",
    "vehicle_history_url",
]

#: Multi-word makes have to be matched before a naive split on whitespace, or
#: "2019 LAND ROVER RANGE ROVER SPORT" becomes a Land make and a Rover model.
#: Collected from the page's own `brand` and `make` values rather than written
#: from memory -- a make this lot does not carry is not this file's business.
KNOWN_MAKES: set[str] = set()

#: Their own "photo coming soon" graphic. A card wearing it is a car they have
#: not photographed yet, which is a fact about the lot rather than a parse
#: failure -- so the row gets no photo and `_photo_for` draws ours, which at
#: least names the car. Hotlinking this would put the same grey tile on every
#: one of them, and keeping a *previous* capture's photo would be worse still:
#: their site is saying today that there is no picture.
COMING_SOON = "178898223090401"


def rendered_cards(html: str) -> dict[str, dict]:
    """Every vehicle card in the document, richest occurrence per VIN.

    A car appears more than once -- the carousels on the page repeat cars that
    are also in the grid -- and the repeats are not equally complete, so the
    one with the most fields filled in wins rather than the last one seen.
    """
    cards: dict[str, dict] = {}
    for chunk in html.split('class="card-float')[1:]:
        vin = _one(chunk, r'data-vin="([A-HJ-NPR-Z0-9]{17})"')
        if not vin:
            continue
        row = {
            "vin": vin,
            "title": _one(chunk, r'class="inv-title[^"]*">([^<]*)<'),
            "trim": _one(chunk, r'class="text-single-line-ellipsis inv-item"><b>([^<]*)</b>'),
            # Not `Stock #<!-- -->: <b>` -- React's text-splitting comment is
            # in only half the cards, and matching it dropped the stock number
            # on the other half while looking like it worked.
            "stock_number": _one(chunk, r"Stock #[^<]*<b>([^<]*)</b>"),
            "mileage": _cell(chunk, "Mileage"),
            "fuel_type": _cell(chunk, "Fuel"),
            "exterior_color": _cell(chunk, "Exterior"),
            "interior_color": _cell(chunk, "Interior"),
            "drivetrain": _cell(chunk, "DriveTrain"),
            "transmission": _cell(chunk, "Transmission"),
            "photo_url": _photo(chunk),
            "listing_url": _one(chunk, r'href="(/used-cars-[^"]+)"'),
            # Their own itemisation, stated and never computed: the gap between
            # the advertised figure and the total is not a constant, because a
            # car with no smog fee pays $58.25 less of it.
            "advertised_price": _money(chunk, "Advertised Price"),
            "doc_fee": _money(chunk, "DOC"),
            "price": _money(chunk, "Total Price"),
        }
        best = cards.get(vin)
        if best is None or _filled(row) > _filled(best):
            cards[vin] = row
    return cards


def payload_vehicles(html: str) -> dict[str, dict]:
    """The embedded JSON objects: the only source for engine and Carfax.

    The document carries them escaped inside script chunks, so the whole thing
    is unescaped once and the objects are found by their own shape. Parsed
    with a brace walker rather than a regex, because a regex cannot balance
    braces and these objects nest.
    """
    text = html.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", " ")
    found: dict[str, dict] = {}
    for m in re.finditer(r'\{"id":\d+,"lid":"', text):
        raw = _object_at(text, m.start())
        if not raw:
            continue
        try:
            car = json.loads(raw)
        except ValueError:
            continue
        vin = car.get("vin")
        if vin and "bodyType" in car:
            found.setdefault(vin, car)
    return found


def linked_data(html: str) -> dict[str, dict]:
    """The JSON-LD `Car` entries, for cross-checking the price and mileage."""
    out: dict[str, dict] = {}
    for block in re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S | re.I
    ):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if data.get("@type") != "ItemList":
            continue
        for element in data.get("itemListElement") or []:
            car = element.get("item") or {}
            vin = car.get("vehicleIdentificationNumber")
            if vin:
                out.setdefault(vin, car)
                KNOWN_MAKES.add(str((car.get("brand") or {}).get("name", "")).upper())
    return out


def split_title(title: str) -> tuple[str, str, str]:
    """`2019 LAND ROVER RANGE ROVER` -> (2019, LAND ROVER, RANGE ROVER).

    The make is matched against the ones this page actually names, longest
    first. A title whose make is not among them is returned with an empty make
    so the caller can report it rather than inventing a split.
    """
    words = title.strip().split()
    if not words or not words[0].isdigit():
        return "", "", title.strip()
    year, rest = words[0], " ".join(words[1:])
    for make in sorted(KNOWN_MAKES, key=len, reverse=True):
        if make and rest.upper().startswith(make):
            return year, make, rest[len(make):].strip()
    # A make named nowhere machine-readable on the page -- their 2001
    # Oldsmobile Aurora is in no JSON-LD block and no payload object, so the
    # only statement of it is the card's own title. The first word is right
    # for every single-word make, which is every make this fallback can reach
    # (a two-word one would have to be in `KNOWN_MAKES` to be split at all),
    # and the caller reports each use rather than letting it pass silently.
    return year, words[1].upper(), " ".join(words[2:])


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    capture = pathlib.Path(sys.argv[1])
    if not capture.is_file():
        print(f"no such capture: {capture}")
        return 2
    html = capture.read_text(errors="replace")

    ld = linked_data(html)          # also fills KNOWN_MAKES
    cards = rendered_cards(html)
    payload = payload_vehicles(html)
    for car in payload.values():
        KNOWN_MAKES.add(str(car.get("make") or "").upper())

    previous = {}
    if OUT.is_file():
        previous = {r["vin"]: r for r in csv.DictReader(OUT.open())}

    print(f"capture:  {len(cards)} cards, {len(payload)} payload objects, {len(ld)} json-ld")
    print(f"on file:  {len(previous)} rows")

    rows, guessed, mismatched, unphotographed = [], [], [], []
    for vin, card in sorted(cards.items(), key=lambda kv: kv[1]["title"]):
        rich = payload.get(vin, {})
        was = previous.get(vin, {})
        year, make, model = split_title(card["title"])
        if rich.get("make"):
            make = str(rich["make"]).upper()
            model = str(rich.get("model") or model)
            year = str(rich.get("year") or year)
        if make not in KNOWN_MAKES:
            guessed.append(f"{card['title']}  ->  make={make!r} model={model!r}")
        if not card["photo_url"]:
            unphotographed.append(f"{year} {make} {model}")

        # The cross-check. Their card and their JSON-LD are two statements of
        # one price, and a lot where they disagree is a lot where one of them
        # is stale -- worth saying out loud rather than quietly preferring one.
        stated = ld.get(vin, {}).get("offers", {}).get("price")
        if stated and card["price"] and int(float(stated)) != int(float(card["price"])):
            mismatched.append((vin, card["price"], stated))

        rows.append({
            "vin": vin,
            "year": year,
            "make": make,
            "model": model,
            "trim": card["trim"] or was.get("trim", ""),
            "price": _int(card["price"]),
            "mileage": _int(card["mileage"]) or was.get("mileage", ""),
            # Only the payload states these, so two thirds of the lot keeps
            # what the previous capture of the same site already said.
            "body_style": str(rich.get("bodyType") or was.get("body_style") or ""),
            "seats": was.get("seats", ""),
            "features": was.get("features", ""),
            # No `was` fallback: a card showing "coming soon" is their site
            # saying *today* that this car has no picture, so a photo kept
            # from an older capture would be us contradicting them.
            "photo_url": card["photo_url"],
            "listing_url": _abs(card["listing_url"]) or was.get("listing_url", ""),
            "location": was.get("location") or "Santa Ana",
            "dealer_phone": was.get("dealer_phone") or "(951) 366-4755",
            "doc_fee": _int(card["doc_fee"]),
            "stock_number": card["stock_number"] or str(rich.get("stockNo") or ""),
            "advertised_price": _int(card["advertised_price"]),
            # Card first, then the payload, then what was already on file --
            # and the card drops out automatically wherever it truncated.
            "fuel_type": _best(card["fuel_type"], rich.get("fuelType"), was.get("fuel_type")),
            "drivetrain": _best(card["drivetrain"], rich.get("driveTrain"), was.get("drivetrain")),
            "transmission": _best(
                card["transmission"], rich.get("transmission"), was.get("transmission")
            ),
            "exterior_color": _best(
                card["exterior_color"], rich.get("colorExterior"), was.get("exterior_color")
            ),
            "interior_color": _best(
                card["interior_color"], rich.get("colorInterior"), was.get("interior_color")
            ),
            "engine": str(rich.get("engine") or was.get("engine") or ""),
            "vehicle_history_url": _carfax(rich) or was.get("vehicle_history_url", ""),
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nwrote {len(rows)} rows -> {OUT.relative_to(ROOT)}")
    filled = Counter()
    for row in rows:
        for key, value in row.items():
            if str(value).strip():
                filled[key] += 1
    for key in HEADER:
        n = filled[key]
        flag = "" if n == len(rows) else "   <- partial"
        print(f"  {key:22} {n:>3}/{len(rows)}{flag}")

    fresh = sorted(set(cards) - set(previous))
    gone = sorted(set(previous) - set(cards))
    print(f"\nnew since the last capture: {len(fresh)}")
    print(f"no longer listed: {len(gone)}")
    for vin in gone:
        was = previous[vin]
        print(f"  {vin}  {was.get('year','')} {was.get('make','')} {was.get('model','')}")
    # A car the capture did not see looks exactly like a car that sold, and
    # this cannot tell the two apart -- so it reports rather than deciding.
    if gone:
        print("  (dropped from the fixture: their site no longer lists them)")
    if unphotographed:
        print(f"\nno photograph on their own card ({len(unphotographed)}):")
        for car in unphotographed:
            print("  ", car)
        print("  (their 'coming soon' tile; ours draws the car's name instead)")
    if guessed:
        print(f"\nmake taken from the title, not stated anywhere ({len(guessed)}):")
        for line in guessed:
            print("  ", line)
    if mismatched:
        print(f"\ncard and json-ld disagree on {len(mismatched)} price(s):")
        for vin, card_price, ld_price in mismatched:
            print(f"   {vin}  card {card_price}  json-ld {ld_price}")
    return 0


# --- small readers ---------------------------------------------------------

def _one(text: str, pattern: str) -> str:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else ""


def _cell(chunk: str, label: str) -> str:
    """One of the six labelled cells their card prints.

    **An ellipsis is a truncation, not a value.** Their cards clip long text
    server-side, so `Transmission` arrives as `AUTOMA...` on 88 of the 91 --
    and a row holding that is one Liner reads out to a buyer. Refused here, so
    the caller falls through to a source that states the whole thing.
    """
    value = _one(
        chunk,
        re.escape(label) + r':?</span><span class="[^"]*font-weight-700[^"]*">([^<]*)</span>',
    )
    return "" if value.endswith("...") else value


def _money(chunk: str, label: str) -> str:
    return _one(chunk, r"<span>" + re.escape(label) + r"</span><span>\$([\d,.]+)").replace(",", "")


def _best(*candidates) -> str:
    """The first source that actually states something.

    The order is always card, then payload, then what was already on file:
    the card is this capture's own word, the payload is the same page's, and
    the previous CSV is an older capture of the same site -- their data every
    time, never a guess. `None` and "" both mean "did not say".
    """
    for value in candidates:
        text = str(value or "").strip()
        # `N / A` is their export saying it does not apply, and it reaches the
        # card as a spec cell reading "N / A" -- which is worse than the cell
        # not being drawn, the rule the storefront already follows for a field
        # the export did not state.
        if text and text.lower() not in ("none", "n/a", "n / a", "na", "-"):
            return text
    return ""


def _int(value: str) -> str:
    """Their figures carry cents; `price` has always been a whole number here
    and `csv_import` reads it with `to_int`. The cents are not dropped from
    anything that quotes them -- they were never in this column."""
    try:
        return str(int(float(str(value).replace(",", ""))))
    except (TypeError, ValueError):
        return ""


def _photo(chunk: str) -> str:
    """Their photo, or "" where their card shows `photo coming soon`."""
    if COMING_SOON in chunk:
        return ""
    return _one(chunk, r'src="(https://cdn\.gma\.to/[^"]+)"')


def _abs(path: str) -> str:
    return f"https://www.alsboucars.com{path}" if path.startswith("/") else path


def _carfax(car: dict) -> str:
    for entry in car.get("infoHistory") or []:
        if entry.get("reportUrl"):
            return str(entry["reportUrl"])
    return ""


def _filled(row: dict) -> int:
    return sum(1 for v in row.values() if str(v).strip())


def _object_at(text: str, start: int) -> str | None:
    """The JSON object beginning at `start`, balanced and string-aware."""
    depth, i, in_string, escaped = 0, start, False, False
    while i < len(text):
        c = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return None


if __name__ == "__main__":
    raise SystemExit(main())
