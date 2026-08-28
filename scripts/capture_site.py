#!/usr/bin/env python3
"""Fetch a dealer site's listings, keep the raw HTML, and say what came out.

Run this where the site is actually reachable -- a laptop or the server --
because it answers the one question that decides how much work the rest is:
**does this site emit JSON-LD?** A surprising share of dealer platforms do,
and where they do the existing ladder in `ingest/extract.py` already reads
VIN, price, mileage and photos without a single CSS selector, so no adapter is
needed at all. Where they do not, the raw pages saved here are what an adapter
gets written against; guessing a site's markup from its URL is how you write a
parser for a page that does not exist.

    make capture URL=https://www.example.com/inventory
    make capture URL=... PAGES=5

Nothing is written to the database and nothing is published. This reads, saves
and reports.

Politeness is not optional and not configurable here: robots.txt is honoured,
the crawl is rate limited by SCRAPER_RATE_LIMIT, and the User-Agent identifies
this crawler. A dealership's own inventory is what this product exists to
ingest, and the way to ask for it is the way any other crawler asks.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from urllib.parse import urljoin, urlparse

sys.path.insert(0, "backend")

import httpx  # noqa: E402
from selectolax.parser import HTMLParser  # noqa: E402

from app.config import settings  # noqa: E402
from app.ingest.extract import extract  # noqa: E402
from app.ingest.pipeline import VDP_HINTS, _robots_allows, discover  # noqa: E402

OUT = pathlib.Path(".artifacts/capture")

#: What a listing has to carry before Liner can talk about it. Reported per
#: page so a summary says *which* field a site withholds rather than just that
#: something is missing -- "no VIN" and "no price" need very different answers.
FIELDS = ("vin", "year", "make", "model", "trim", "price", "mileage",
          "body_style", "photo_url")


def slug(url: str) -> str:
    path = urlparse(url).path.strip("/") or "index"
    return "".join(c if c.isalnum() else "-" for c in path)[:120]


def links_on(html: str, base: str) -> list[str]:
    """Vehicle-detail links on a page we were handed directly.

    `discover()` tries the sitemap first and falls back to the site root. A
    listing URL with query parameters -- which is what a person actually
    copies out of their browser -- is neither, so it is read here as well.
    """
    tree = HTMLParser(html)
    seen: list[str] = []
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href", "")
        if any(hint in href.lower() for hint in VDP_HINTS):
            full = urljoin(base, href)
            if full not in seen:
                seen.append(full)
    return seen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="A listing page, or the site root.")
    parser.add_argument("--pages", type=int, default=8,
                        help="How many vehicle pages to fetch (default 8).")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    parsed = urlparse(args.url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    headers = {"User-Agent": settings.scraper_user_agent}
    delay = 1.0 / max(settings.scraper_rate_limit, 0.1)

    print(f"Capturing {args.url}")
    print(f"  as {settings.scraper_user_agent}")
    print(f"  {delay:.1f}s between requests, at most {args.pages} vehicle pages\n")

    report: dict = {"url": args.url, "pages": [], "robots": True}

    with httpx.Client(headers=headers, follow_redirects=True, timeout=25) as client:
        if not _robots_allows(client, root, parsed.path or "/"):
            print(f"robots.txt at {root} disallows this path for our agent.")
            print("Stopping. That is the site's answer and it is not ours to override;")
            print("ask the dealership for a CSV export or written permission instead.")
            report["robots"] = False
            (out / "report.json").write_text(json.dumps(report, indent=2))
            return 1

        # The page we were handed, saved whatever else happens: it is the one
        # the person actually looked at, and its markup is what an adapter for
        # the *list* would be written against.
        listing_page = client.get(args.url)
        (out / f"00-list-{slug(args.url)}.html").write_text(listing_page.text)
        print(f"list page: HTTP {listing_page.status_code}, "
              f"{len(listing_page.text):,} bytes saved")

        urls = links_on(listing_page.text, args.url)
        if not urls:
            print("  no vehicle links matched on that page; trying sitemap discovery")
            try:
                urls = discover(client, root)
            except Exception as exc:  # noqa: BLE001 -- reported, not raised
                print(f"  discovery failed: {exc}")
                urls = []
        print(f"  {len(urls)} vehicle page(s) found\n")
        report["vehicle_urls_found"] = len(urls)

        for index, url in enumerate(urls[: args.pages], start=1):
            time.sleep(delay)
            try:
                page = client.get(url)
            except httpx.HTTPError as exc:
                print(f"{index:>2}. {url}\n    fetch failed: {exc}")
                report["pages"].append({"url": url, "error": str(exc)})
                continue
            if page.status_code != 200:
                print(f"{index:>2}. {url}\n    HTTP {page.status_code}")
                report["pages"].append({"url": url, "error": f"HTTP {page.status_code}"})
                continue

            name = f"{index:02d}-vdp-{slug(url)}.html"
            (out / name).write_text(page.text)

            listing, method = extract(page.text, url)
            row: dict = {"url": url, "file": name, "method": method}
            if listing is None:
                print(f"{index:>2}. {method:<7} nothing extracted -- {name}")
            else:
                got = {f: getattr(listing, f, None) for f in FIELDS}
                row["fields"] = {k: v for k, v in got.items() if v not in (None, "", 0)}
                row["missing"] = [k for k, v in got.items() if v in (None, "", 0)]
                row["errors"] = listing.errors
                shown = ", ".join(
                    f"{k}={v}" for k, v in list(row["fields"].items())[:5]
                )
                print(f"{index:>2}. {method:<7} {shown or '(nothing)'}")
                if row["missing"]:
                    print(f"    missing: {', '.join(row['missing'])}")
                if listing.errors:
                    print(f"    errors:  {'; '.join(listing.errors)}")
            report["pages"].append(row)

    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))

    # The verdict, which is the whole reason to run this.
    usable = [p for p in report["pages"] if p.get("fields", {}).get("vin")]
    methods = {p.get("method") for p in report["pages"]}
    print("\n" + "=" * 60)
    if not report["pages"]:
        print("No vehicle pages were fetched at all.")
        print("The listing page is saved -- send it over and the link pattern")
        print("can be read off it.")
    elif usable and "jsonld" in methods:
        print(f"JSON-LD works: {len(usable)}/{len(report['pages'])} pages gave a VIN.")
        print("No adapter needed. Point SCRAPER_BASE_URL at this site and")
        print("import through /app/inventory/import as normal.")
    else:
        print(f"No usable JSON-LD ({len(usable)}/{len(report['pages'])} pages gave a VIN).")
        print("This site needs an adapter in backend/app/ingest/sites/.")
        print(f"Send one of the saved VDP files from {out}/ and it can be")
        print("written against the real markup rather than guessed at.")
    print(f"\nSaved to {out}/  (report.json plus the raw pages)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
