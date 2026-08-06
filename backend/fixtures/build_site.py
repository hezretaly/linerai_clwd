#!/usr/bin/env python3
"""Generate the fixture dealer site the scraper is developed against.

Test data for a real pipeline, not a simulated integration -- the scraper makes
genuine HTTP requests and genuinely parses JSON-LD. Two pages are deliberately
malformed (missing VIN, "Call for price") so the error paths get written now
rather than the night before a demo.

    python fixtures/build_site.py && make fixture-site
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "sites" / "riverside"
BASE = "http://localhost:8100"

# vin, year, make, model, trim, price, mileage, body, seats
CARS = [
    ("1HGCV1F34LA015782", 2020, "Honda", "Accord", "Sport", 21400, 38120, "Sedan", 5),
    ("5TDKZ3DC8JS905311", 2018, "Toyota", "Sienna", "LE", 19850, 74300, "Minivan", 8),
    ("1FTEW1EP7JKD41209", 2018, "Ford", "F-150", "XLT", 26900, 68240, "Truck", 5),
    ("3VW217AU9HM045118", 2017, "Volkswagen", "Golf", "S", 13950, 92400, "Hatchback", 5),
    ("KM8J3CA46JU622180", 2018, "Hyundai", "Tucson", "SEL", 16750, 79880, "SUV", 5),
    ("1N4AL3AP7JC201955", 2018, "Nissan", "Altima", "SV", 14200, 88600, "Sedan", 5),
    ("5XYPH4A54KG455012", 2019, "Kia", "Sorento", "EX", 22400, 61200, "SUV", 7),
    ("1G1ZD5ST4LF071244", 2020, "Chevrolet", "Malibu", "LT", 17300, 47110, "Sedan", 5),
    ("JTMRFREV8HJ135806", 2017, "Toyota", "RAV4", "XLE", 18450, 86750, "SUV", 5),
    ("1C4RJFAG9JC301877", 2018, "Jeep", "Grand Cherokee", "Laredo", 21950, 71300, "SUV", 5),
    # New to the site: exercises the "created" branch of the diff.
    ("2T3H1RFV8LC069412", 2020, "Toyota", "RAV4", "LE", 23900, 41200, "SUV", 5),
    ("3FA6P0HD3KR228841", 2019, "Ford", "Fusion", "SE", 15600, 63400, "Sedan", 5),
]


def vdp(car) -> str:
    vin, year, make, model, trim, price, mileage, body, seats = car
    ld = {
        "@context": "https://schema.org",
        "@type": "Vehicle",
        "name": f"{year} {make} {model} {trim}",
        "vehicleIdentificationNumber": vin,
        "brand": {"@type": "Brand", "name": make},
        "model": model,
        "vehicleConfiguration": trim,
        "vehicleModelDate": str(year),
        "bodyType": body,
        "seatingCapacity": seats,
        "mileageFromOdometer": {"@type": "QuantitativeValue", "value": mileage,
                                "unitCode": "SMI"},
        "offers": {"@type": "Offer", "price": price, "priceCurrency": "USD",
                   "availability": "https://schema.org/InStock"},
        "image": f"{BASE}/img/{vin}.jpg",
    }
    return _page(f"{year} {make} {model} {trim}", json.dumps(ld, indent=2), f"""
    <p class="price">${price:,}</p>
    <ul><li>{mileage:,} miles</li><li>{body}</li><li>VIN {vin}</li></ul>""")


def broken_no_vin() -> str:
    """A VDP whose JSON-LD omits the VIN entirely."""
    ld = {
        "@context": "https://schema.org", "@type": "Vehicle",
        "name": "2016 Subaru Outback 2.5i",
        "brand": {"@type": "Brand", "name": "Subaru"}, "model": "Outback",
        "vehicleModelDate": "2016", "bodyType": "Wagon",
        "offers": {"@type": "Offer", "price": 14200, "priceCurrency": "USD"},
    }
    return _page("2016 Subaru Outback 2.5i", json.dumps(ld, indent=2),
                 '<p class="price">$14,200</p>')


def broken_call_for_price() -> str:
    """Price is prose. The VIN is good, so this listing is importable -- Liner
    simply has no number to quote."""
    ld = {
        "@context": "https://schema.org", "@type": "Vehicle",
        "name": "2021 Ram 1500 Big Horn",
        "vehicleIdentificationNumber": "1C6SRFFT5MN738820",
        "brand": {"@type": "Brand", "name": "Ram"}, "model": "1500",
        "vehicleConfiguration": "Big Horn", "vehicleModelDate": "2021",
        "bodyType": "Truck", "seatingCapacity": 5,
        "mileageFromOdometer": {"@type": "QuantitativeValue", "value": 31900},
        "offers": {"@type": "Offer", "price": "Call for price", "priceCurrency": "USD"},
    }
    return _page("2021 Ram 1500 Big Horn", json.dumps(ld, indent=2),
                 '<p class="price">Call for price</p>')


def _page(title: str, ld: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title} | Riverside Auto</title>
<script type="application/ld+json">
{ld}
</script></head>
<body><a href="/inventory/">&larr; All inventory</a><h1>{title}</h1>{body}</body></html>
"""


def listing_page(cars, page: int, total: int) -> str:
    rows = "\n".join(
        f'    <li><a href="/inventory/vehicle/{c[0]}.html">{c[1]} {c[2]} {c[3]} {c[4]}</a>'
        f" &mdash; ${c[5]:,}</li>"
        for c in cars
    )
    nav = ""
    if page < total:
        nav += f'<a href="/inventory/page-{page + 1}.html">Next &rarr;</a> '
    if page > 1:
        nav += f'<a href="/inventory/page-{page - 1}.html">&larr; Previous</a>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Inventory page {page} | Riverside Auto</title>
</head><body><h1>Used inventory &mdash; page {page} of {total}</h1>
<ul>
{rows}
</ul>
<nav>{nav}</nav></body></html>
"""


def main() -> None:
    (ROOT / "inventory" / "vehicle").mkdir(parents=True, exist_ok=True)

    urls = []
    for car in CARS:
        (ROOT / "inventory" / "vehicle" / f"{car[0]}.html").write_text(vdp(car))
        urls.append(f"{BASE}/inventory/vehicle/{car[0]}.html")

    (ROOT / "inventory" / "vehicle" / "broken-no-vin.html").write_text(broken_no_vin())
    (ROOT / "inventory" / "vehicle" / "call-for-price.html").write_text(broken_call_for_price())
    urls += [
        f"{BASE}/inventory/vehicle/broken-no-vin.html",
        f"{BASE}/inventory/vehicle/call-for-price.html",
    ]

    per_page = 5
    pages = [CARS[i:i + per_page] for i in range(0, len(CARS), per_page)]
    for index, chunk in enumerate(pages, start=1):
        (ROOT / "inventory" / f"page-{index}.html").write_text(
            listing_page(chunk, index, len(pages))
        )
    (ROOT / "inventory" / "index.html").write_text(listing_page(pages[0], 1, len(pages)))

    locs = "\n".join(f"  <url><loc>{u}</loc></url>" for u in urls)
    (ROOT / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{locs}\n</urlset>\n'
    )
    (ROOT / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {BASE}/sitemap.xml\n"
    )
    (ROOT / "index.html").write_text(
        '<!doctype html><html><head><meta charset="utf-8"><title>Riverside Auto</title></head>'
        '<body><h1>Riverside Auto</h1><a href="/inventory/">Browse inventory</a></body></html>'
    )
    print(f"Fixture site written to {ROOT} -- {len(CARS)} good VDPs, 2 deliberately broken.")


if __name__ == "__main__":
    main()
