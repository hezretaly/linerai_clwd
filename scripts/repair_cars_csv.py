#!/usr/bin/env python3
"""Make dash/cars.csv internally consistent. Run once; the result is committed.

The file was generated with each column drawn independently, which shows: a
Mazda3 saloon and a Tesla Model S both listed "Third Row Seating", and no row
had a seat count at all. That is fine for a spreadsheet and not fine for this
system -- Liner reads these rows and says them out loud to a buyer, so a saloon
with a third row becomes an offer to a family of six.

What this changes, and nothing else:

* **Seats**, a column the file did not have. Without it "room for seven" cannot
  be searched, which is the single most common thing a family asks for.
* **Features that contradict the body style** are dropped. A third row belongs
  to an SUV or a van; a tow package does not belong on a coupe.
* **Features that contradict each other** are reconciled: ventilated seats
  imply leather, and a car does not advertise both a sunroof and a panoramic
  roof.
* **Sold, pending and in-transit rows become available**, as asked, with the
  sale date cleared and days-in-inventory recomputed from the acquisition date
  so the two agree.

What it deliberately does not touch: VIN, make, model, year, mileage, price,
MSRP, colours, drivetrain, fuel type, MPG, electric range, condition, title,
accident and owner history, CarFax links. Those were already self-consistent,
and rewriting a dealer's prices to taste is not a repair.

    backend/.venv/bin/python scripts/repair_cars_csv.py
"""

from __future__ import annotations

import collections
import csv
import datetime as dt
import pathlib
import sys

CSV = pathlib.Path(__file__).resolve().parent.parent / "dash" / "cars.csv"

# Which body styles can honestly carry a feature. Absent from here means "any".
BODY_ONLY = {
    "Third Row Seating": {"suv", "van"},
    "Tow Package": {"suv", "truck", "van"},
}

# Models that genuinely offer a third row. A whitelist, not a blacklist: an
# SUV is not automatically a seven-seater, and the file had put a third row in
# a RAV4, a Compass and a Tucson. If a model is not listed here we do not claim
# the row -- the same rule the rest of this system runs on, which is that not
# knowing is a better answer than a confident wrong one.
#
# Vans are all three-row and need no listing.
THREE_ROW_SUVS = {
    "cx-9", "cx-90", "durango", "explorer", "expedition", "grand cherokee l",
    "highlander", "model x", "palisade", "pilot", "q7", "qx60", "sorento",
    "suburban", "tahoe", "telluride", "traverse", "x7", "xc90", "yukon",
    "atlas", "ascent", "mdx", "gls", "navigator", "armada", "pathfinder",
    "wagoneer", "enclave", "acadia", "aviator", "sequoia", "carnival",
    # The GLE offers one as a factory option.
    "gle",
}

# Seats before any third-row adjustment.
BASE_SEATS = {"coupe": 4, "sedan": 5, "suv": 5, "truck": 5, "van": 7, "wagon": 5}


def repair(rows: list[dict]) -> tuple[list[dict], dict]:
    counts: collections.Counter = collections.Counter()
    today = dt.date(2026, 8, 7)

    for row in rows:
        body = (row.get("body_style") or "").strip().lower()

        features = [f.strip() for f in (row.get("features") or "").split(";") if f.strip()]
        kept = []
        model = (row.get("model") or "").strip().lower()
        for feature in features:
            allowed = BODY_ONLY.get(feature)
            if allowed is not None and body not in allowed:
                counts[f"dropped {feature} from {body}"] += 1
                continue
            if (
                feature == "Third Row Seating"
                and body == "suv"
                and model not in THREE_ROW_SUVS
            ):
                counts[f"dropped Third Row Seating from {row['make']} {row['model']}"] += 1
                continue
            kept.append(feature)

        # A car advertises one roof.
        if "Panoramic Roof" in kept and "Sunroof" in kept:
            kept.remove("Sunroof")
            counts["collapsed sunroof into panoramic roof"] += 1

        # Ventilated seats are a leather-seat option; cloth cannot be ventilated
        # in any trim these makes sell.
        if "Ventilated Seats" in kept and "Leather Seats" not in kept:
            kept.append("Leather Seats")
            counts["added leather implied by ventilated seats"] += 1

        row["features"] = "; ".join(kept)

        seats = BASE_SEATS.get(body, 5)
        if "Third Row Seating" in kept:
            seats = 8 if body == "van" else 7
        row["seats"] = str(seats)
        counts["seats set"] += 1

        if (row.get("status") or "").strip().lower() != "available":
            counts[f"{row['status']} -> available"] += 1
            row["status"] = "available"
            row["sale_price"] = ""
            row["date_sold"] = ""

        # days_in_inventory has to agree with date_acquired or the dashboard
        # shows a car acquired last week that has been on the lot for a year.
        try:
            acquired = dt.date.fromisoformat((row.get("date_acquired") or "").strip())
            row["days_in_inventory"] = str(max((today - acquired).days, 0))
        except ValueError:
            pass

    return rows, counts


def main() -> int:
    if not CSV.is_file():
        print(f"not found: {CSV}", file=sys.stderr)
        return 1

    with CSV.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    rows, counts = repair(rows)
    if "seats" not in fieldnames:
        fieldnames.insert(fieldnames.index("body_style") + 1, "seats")

    with CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"repaired {len(rows)} rows in {CSV.name}")
    for label, count in sorted(counts.items(), key=lambda p: -p[1]):
        print(f"  {count:4}  {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
