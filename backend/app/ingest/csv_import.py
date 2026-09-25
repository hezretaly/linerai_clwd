"""CSV import -- the fallback that always works.

Twenty lines that save scraper projects. It is also the only inventory path
available when no dealer site is configured, which is the default state.
"""

from __future__ import annotations

import csv
import io
import json
import re

from sqlalchemy.orm import Session

from app.db import utcnow
from app.ingest.extract import to_int, usable_vin
from app.models import IngestRun, Vehicle

COLUMNS = "vin,year,make,model,trim,price,mileage,body_style,seats"

# A real DMS export does not use those names. These are the aliases seen in the
# wild (and in dash/cars.csv), so a dealer can upload what their system gives
# them instead of reshaping it by hand first.
ALIASES = {
    "year": ("year", "model_year"),
    "photo_url": ("photo_url", "image_url", "image", "photo", "primary_image"),
    "listing_url": ("listing_url", "url", "vdp_url", "detail_url", "link"),
    # Which of the dealership's lots the car is standing on. A group with more
    # than one address lists them all in one export, and a buyer told to come
    # and see a car that is two hours away has been told the wrong thing.
    "location": ("location", "store", "lot", "branch", "site"),
    "dealer_phone": ("dealer_phone", "phone", "store_phone"),
    "doc_fee": ("doc_fee", "documentation_fee", "docfee"),
    "stock_number": ("stock_number", "stock", "stock_no", "stock #"),
    # The six a used-car listing prints under the photo, and the number above
    # them. Every DMS export carries these; ours did not read them, so a card
    # could say a car was a 2018 Audi Q7 and nothing else -- while the
    # dealer's own page said 64,342 miles, gasoline, AWD, white over black.
    # They go to `raw_json` rather than columns, because `create_all` adds a
    # table to an existing database and never a column.
    "fuel_type": ("fuel_type", "fuel", "fueltype"),
    "drivetrain": ("drivetrain", "drive_train", "drive", "drivetype"),
    "transmission": ("transmission", "trans", "transmission_type"),
    "exterior_color": ("exterior_color", "exterior_colour", "color", "colour", "ext_color"),
    "interior_color": ("interior_color", "interior_colour", "int_color"),
    "engine": ("engine", "engine_description"),
    # What the dealer advertises before their own fees, where the price above
    # already includes them. Stated by the source, never derived here: which
    # fees apply is the dealer's rule and varies per car -- four of Alsbou's
    # are electric and pay no smog fee, so subtracting a fixed schedule would
    # be wrong on exactly the cars nobody would check.
    "advertised_price": ("advertised_price", "advertised", "base_price"),
    # The dealer's own vehicle-history report for this car. A link to their
    # provider, never a claim about what is in it.
    "vehicle_history_url": ("vehicle_history_url", "carfax_url", "history_url"),
    "price": ("price", "list_price", "asking_price", "selling_price"),
    "mileage": ("mileage", "odometer", "miles"),
    "body_style": ("body_style", "bodystyle", "body", "vehicle_type"),
    "seats": ("seats", "seating_capacity", "passenger_capacity"),
    "trim": ("trim", "trim_level", "series"),
    "features": ("features", "options", "equipment"),
    "status": ("status", "availability", "stock_status"),
}

# Columns that must never become a vehicle field, however they are spelled.
# acquisition_cost is what the dealership paid -- on the sample file that is
# $8,200 against a $10,800 list price. If it reached a row it would reach
# search_inventory, and from there the model, and a buyer would be able to ask
# Liner what the margin is. Dropping it here is the only place that is
# guaranteed: a prompt telling the model to ignore a field it can see is a
# request, not a control.
NEVER_IMPORT = {
    "acquisition_cost", "cost", "dealer_cost", "invoice", "invoice_price",
    "floor_price", "floorplan_cost", "profit", "margin", "sale_price",
    "salesperson_email", "salesperson_name",
}

# The lot's own words for "not for sale yet" or "gone". The schema has three
# states; anything not clearly available maps to removed rather than being
# offered to a buyer.
STATUS_MAP = {
    "available": "available", "active": "available", "for sale": "available",
    "in stock": "available", "instock": "available",
    "sold": "sold", "delivered": "sold",
    "pending": "removed", "in_transit": "removed", "in transit": "removed",
    "hold": "removed", "on hold": "removed", "wholesale": "removed",
}


# A few features are physically tied to the shape of the car. A DMS export
# written by hand, or sample data generated at random, will happily put a third
# row on a saloon -- and then a buyer asking for seven seats is shown one.
#
# This is not silent correction of a dealer's data: the vehicle still imports,
# only the impossible feature is dropped, and the run reports how many. The
# alternative is Liner confidently offering a Mazda3 to a family of six.
IMPOSSIBLE = {
    "third row": {"sedan", "coupe", "hatchback", "convertible", "wagon"},
    "3rd row": {"sedan", "coupe", "hatchback", "convertible", "wagon"},
    "seven seat": {"sedan", "coupe", "hatchback", "convertible"},
    "8 passenger": {"sedan", "coupe", "hatchback", "convertible"},
    "tow package": {"coupe", "convertible"},
    "bed liner": {"sedan", "coupe", "hatchback", "convertible", "suv", "van"},
}


def stored_value(vehicle, key: str):
    """What the row holds for one payload key, in the payload's shape.

    `features` and `raw` are JSON text under other column names, so reading
    them by the payload's name gave `None` for every car -- and every import
    then reported a features change on every existing row, a review diff
    that cried wolf on all of them. One reader for the CSV and crawl diffs,
    so the two cannot disagree about what "unchanged" means.
    """
    if key == "features":
        return json.loads(vehicle.features_json or "[]")
    if key == "raw":
        return json.loads(vehicle.raw_json or "{}")
    return getattr(vehicle, key, None)


#: What a source states that this schema has no column for, kept in
#: `raw_json`. One tuple for the CSV importer and every crawl, so the two
#: cannot build differently-shaped rows -- the first crawl after a CSV seed
#: would otherwise report every car changed.
RAW_KEYS = (
    "location", "dealer_phone", "doc_fee", "stock_number", "advertised_price",
    "fuel_type", "drivetrain", "transmission", "exterior_color", "interior_color",
    "engine", "vehicle_history_url",
)


def status_of(text: str) -> str:
    """A source's word for a car's state, in ours. Anything not clearly for
    sale is not offered to a buyer; nothing said means available."""
    word = (text or "").strip().lower()
    return STATUS_MAP.get(word, "available" if not word else "removed")


def unstated(value) -> bool:
    """The source did not say. An empty list or mapping counts: a listing page
    that prints no options has not said the car has none, and diffing it as a
    statement wiped a hundred-line options list pasted by hand."""
    return value in (None, "", [], {})


def merged_raw(before: dict, stated: dict) -> dict:
    """What the row keeps under `raw_json` after a source speaks.

    A key the source does not state is kept, never dropped. `raw_json` holds
    more than any one source says: the seed's `detail_doc` (a car's written
    history, the only one on the lot), and `location` and `dealer_phone`,
    which a listing page never prints. Replaced whole, every re-import deleted
    them -- the car's history write-up included.
    """
    return {**(before or {}), **(stated or {})}


def changes_for(vehicle, payload: dict, manual: set[str]) -> dict:
    """What re-importing `payload` would change on `vehicle`, key by key.

    One definition for the CSV import and every crawl, for the reason
    `stored_value` exists: two versions of "unchanged" is how one path starts
    crying wolf. A field the source left unstated is not a change, a field a
    rep edited is not the source's to change, and `raw` is merged, not
    replaced -- so `to` is exactly what publishing writes.
    """
    changes = {}
    for key, value in payload.items():
        if key == "vin" or unstated(value) or key in manual:
            continue
        before = stored_value(vehicle, key)
        if key == "raw":
            value = merged_raw(before, value)
        if before != value:
            changes[key] = {"from": before, "to": value}
    return changes


def plausible_features(features: list[str], body_style: str) -> tuple[list[str], list[str]]:
    """Returns (kept, dropped) for one vehicle."""
    body = (body_style or "").strip().lower()
    kept, dropped = [], []
    for feature in features:
        lowered = feature.lower()
        if any(k in lowered and body in bodies for k, bodies in IMPOSSIBLE.items()):
            dropped.append(feature)
        else:
            kept.append(feature)
    return kept, dropped


def pick(row: dict, field: str) -> str:
    """The first alias present in this row, so one importer reads both shapes."""
    for name in ALIASES.get(field, (field,)):
        value = row.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def import_csv(db: Session, raw: str) -> IngestRun:
    run = IngestRun(source_url="csv upload", method="csv", status="pending")
    db.add(run)
    db.commit()

    reader = csv.DictReader(io.StringIO(raw))
    existing = {v.vin: v for v in db.query(Vehicle).all()}
    created, updated, errors = [], [], []
    skipped_unavailable = 0
    impossible: list[str] = []

    for index, row in enumerate(reader, start=2):
        # Strip the columns we refuse before anything else touches the row, so
        # there is no path by which they end up somewhere later.
        row = {k: v for k, v in row.items() if (k or "").strip().lower() not in NEVER_IMPORT}

        vin = (row.get("vin") or "").upper().strip()
        if not usable_vin(vin):
            errors.append({"row": index, "error": f"VIN {vin!r} is not a usable VIN"})
            continue

        status = status_of(pick(row, "status"))
        if status != "available" and vin not in existing:
            # A car that is sold or still in transit is not something to offer.
            # Counted rather than errored: it is a correct row, just not one a
            # buyer should see.
            skipped_unavailable += 1
            continue

        payload = {
            "vin": vin,
            "year": to_int(pick(row, "year")),
            "make": (row.get("make") or "").strip(),
            "model": (row.get("model") or "").strip(),
            "trim": pick(row, "trim"),
            "price": to_int(pick(row, "price")),
            "mileage": to_int(pick(row, "mileage")),
            "body_style": pick(row, "body_style").lower(),
            "seats": to_int(pick(row, "seats")),
            # Semicolon-separated in a DMS export, a list everywhere here. This
            # is what makes "heated seats" a findable phrase rather than free
            # text nobody searches.
            "features": [],
            # Where the buyer would actually go to see it. A DMS export has
            # neither; a scrape of the dealer's own site has both, and losing
            # them here would mean re-deriving a photo and a link this file
            # already states.
            "photo_url": pick(row, "photo_url"),
            "listing_url": pick(row, "listing_url"),
            # Kept in `raw_json` rather than a column, because `create_all`
            # adds a table to an existing database and never a column. It is
            # also exactly what that field is for: what the source said.
            "raw": {key: pick(row, key) for key in RAW_KEYS if pick(row, key)},
        }
        features, dropped = plausible_features(
            # One option per line is how a dealer's site prints them, and a CSV
            # cell can hold newlines -- so a pasted "Vehicle Options" block
            # imports as-is, beside the `;` and `|` a DMS export uses.
            [
                f.strip()
                for f in re.split(r"[;|\r\n]+", pick(row, "features"))
                if f.strip()
            ],
            payload["body_style"],
        )
        payload["features"] = features
        if dropped:
            impossible.append(f"{payload['body_style']} {vin[-6:]}: {', '.join(dropped)}")
        if vin in existing:
            current = existing[vin]
            manual = set(json.loads(current.manual_fields_json or "[]"))
            changes = changes_for(current, payload, manual)
            if changes:
                updated.append({"vin": vin, "changes": changes,
                                "protected": sorted(manual & set(payload))})
        else:
            created.append(payload)

    diff = {"created": created, "updated": updated, "removed": []}
    if impossible:
        errors.append({
            "error": f"{len(impossible)} vehicle(s) listed a feature their body style "
                     f"cannot have; the feature was dropped, the vehicle kept. "
                     f"e.g. {impossible[0]}",
        })
    if skipped_unavailable:
        errors.append({
            "error": f"{skipped_unavailable} row(s) skipped: sold, pending or in transit, "
                     "so not something to offer a buyer.",
        })
    run.listings_found = len(created) + len(updated)
    run.created_count = len(created)
    run.updated_count = len(updated)
    run.diff_json = json.dumps(diff, default=str)
    run.errors_json = json.dumps(errors)
    run.status = "ready" if (created or updated) else "failed"
    if run.status == "failed" and not errors:
        run.errors_json = json.dumps([{"error": "Nothing to import."}])
    run.finished_at = utcnow()
    db.commit()
    db.refresh(run)
    return run
