#!/usr/bin/env python3
"""Crawl a dealer's site with every step narrated, and say exactly where it broke.

    make ingest                 # crawl, report, write nothing to the database
    make ingest ARGS=--publish  # ... and apply the diff when it looks right
    make ingest ARGS="--pages 2 --save-html"

**Why this exists.** `/app/inventory/import` runs the identical crawl and gives
you a spinner and one line of error. That is fine when it works and useless
when it does not: a crawl can fail at robots.txt, at DNS, at a 403, at "no
adapter matched this markup", at "the adapter matched but every card was
dropped", or at "it worked and the diff is empty because they were already
imported" -- and those need six different answers. This prints each stage as it
happens, with the numbers, so the failure names itself.

**It is the same pipeline, not a second one.** `_robots_allows`,
`list_adapter_for`, `crawl_list`, `discover`, `fetch_and_extract`, `build_diff`
and `publish` are imported from `app.ingest.pipeline`. A parallel
implementation would be a crawl that succeeds here and fails in the product,
which is the opposite of a diagnostic.

**Nothing is published unless you ask.** Without `--publish` the database is
never written to -- not even an `IngestRun` row -- so this is safe to run
against a live box mid-demo. With it, the run is recorded and the diff applied
exactly as the web path does, manual overrides still winning.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from collections import Counter
from urllib.parse import urljoin, urlparse

sys.path.insert(0, "backend")

import httpx  # noqa: E402

from app import profile  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.ingest import snapshot  # noqa: E402
from app.ingest.extract import Listing  # noqa: E402
from app.ingest.pipeline import (  # noqa: E402
    _robots_allows,
    build_diff,
    crawl_list,
    discover,
    fetch_and_extract,
    list_adapter_for,
    publish,
)
from app.models import Dealership, IngestRun, Vehicle  # noqa: E402

OUT = pathlib.Path(".artifacts/ingest")

#: What a listing needs before Liner can talk about it, reported as a fill rate
#: rather than pass/fail. "412 of 481 have a price" is a different problem from
#: "0 of 481 have a VIN", and only the second one means the adapter is wrong.
FIELDS = ("vin", "year", "make", "model", "trim", "price", "mileage",
          "body_style", "seats", "photo_url", "listing_url")


def rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")
    print("-" * max(len(title), 40))


def fail(message: str, *fixes: str) -> int:
    """Stop, and say what to do about it.

    Every exit from this script names a next action. A crawl that prints a
    stack trace and stops has told you the same thing as one that prints
    nothing: that it did not work.
    """
    print(f"\n\033[31mSTOPPED\033[0m  {message}")
    for fix in fixes:
        print(f"  -> {fix}")
    return 1


def main() -> int:  # noqa: C901 -- a narration, deliberately linear
    parser = argparse.ArgumentParser(prog="ingest", description=__doc__)
    parser.add_argument("--url", default="", help="override the profile's source_url")
    parser.add_argument("--dealer-id", default=None,
                        help="override which store to keep; '' takes every lot")
    parser.add_argument("--pages", type=int, default=0,
                        help="stop after N list pages (default: the profile's limit)")
    parser.add_argument("--publish", action="store_true",
                        help="apply the diff. Without this nothing is written at all.")
    parser.add_argument("--allow-removals", action="store_true",
                        help="publish even when the crawl would take a large share of the "
                             "lot off sale. Required after a truncated crawl.")
    parser.add_argument("--save-html", action="store_true",
                        help=f"keep every fetched page under {OUT}/ for reading")
    parser.add_argument("--limit-print", type=int, default=8,
                        help="how many example rows and errors to show")
    args = parser.parse_args()

    started = time.monotonic()

    # ---------------------------------------------------------------- 1 ----
    rule("1. Which dealership, and where its cars come from")
    source = profile.inventory()
    url = args.url.strip() or source["source_url"]
    dealer_id = source["dealer_id"] if args.dealer_id is None else args.dealer_id.strip()

    db = SessionLocal()
    try:
        seeded = (db.query(Dealership).first() or None)
        seeded_name = seeded.name if seeded else ""
        before = db.query(Vehicle).count()
    finally:
        db.close()

    print(f"  profile      {settings.dealership_config.name}"
          f"  (DEALERSHIP={settings.dealership or '<unset>'})")
    print(f"  seeded as    {seeded_name or '(nothing seeded yet)'}")
    print(f"  source       {url or '(none)'}"
          f"  [{'--url' if args.url else source['origin']}]")
    print(f"  keeping lot  {dealer_id or '(every lot on the page)'}")
    print(f"  on the lot   {before} vehicle(s) already in the database")

    # The same mismatch the boot log shouts about, checked here because this
    # is the command that writes into the seeded dealership -- crawling their
    # site into somebody else's rows is the thing to catch before, not after.
    if seeded_name and settings.dealership_config.is_file():
        import yaml

        want = (yaml.safe_load(settings.dealership_config.read_text()) or {}).get("name")
        if want and want != seeded_name:
            print(f"\n  \033[33mWARNING\033[0m the database was seeded as {seeded_name!r} but "
                  f"this profile is {want!r}.\n"
                  "  The crawl will write into the seeded one. `make reset-db` or fix "
                  "DEALERSHIP= first.")

    if not url:
        return fail(
            "No inventory source is configured.",
            f"Add `inventory.source_url` to {settings.dealership_config}",
            "or pass --url https://their-site/inventory",
            "or upload a CSV at /app/inventory/import, which needs no configuration.",
        )

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return fail(f"{url!r} is not a URL this can fetch.",
                    "It needs a scheme and a host: https://www.example.com/inventory")
    root = f"{parsed.scheme}://{parsed.netloc}"

    if args.save_html:
        OUT.mkdir(parents=True, exist_ok=True)
        print(f"  saving pages to {OUT}/")

    # ---------------------------------------------------------------- 2 ----
    rule("2. Asking the site whether we may")
    headers = {"User-Agent": settings.scraper_user_agent}
    print(f"  as           {settings.scraper_user_agent}")
    print(f"  rate limit   {settings.scraper_rate_limit}/sec "
          f"({1.0 / max(settings.scraper_rate_limit, 0.1):.1f}s between requests)")

    with httpx.Client(headers=headers, follow_redirects=True, timeout=25) as client:
        try:
            allowed = _robots_allows(client, root, parsed.path or "/")
        except Exception as exc:  # noqa: BLE001 -- reported, not raised
            return fail(f"Could not even read robots.txt: {exc!r}",
                        "Check the host resolves and is reachable from this machine.")
        print(f"  robots.txt   {'allows this path' if allowed else 'DISALLOWS this path'}")
        if not allowed:
            return fail(
                f"robots.txt at {root} disallows this path for our agent.",
                "That is the site's answer and it is not ours to override.",
                "Ask the dealership for a CSV export or written permission instead.",
            )

        # ------------------------------------------------------------ 3 ----
        rule("3. Fetching the listing page")
        began = time.monotonic()
        try:
            first = client.get(url, timeout=25)
        except httpx.HTTPError as exc:
            return fail(
                f"{type(exc).__name__}: {exc}",
                "DNS, TLS or a firewall -- none of which this script can fix.",
                f"Try `curl -sS -o /dev/null -w '%{{http_code}}' {url}` from this machine.",
            )
        took = time.monotonic() - began
        print(f"  HTTP {first.status_code}   {len(first.text):,} bytes   {took:.1f}s")
        print(f"  final url    {first.url}")
        print(f"  content-type {first.headers.get('content-type', '(none)')}")
        if args.save_html:
            (OUT / "01-list.html").write_text(first.text)

        if first.status_code != 200:
            body = first.text.strip()[:400]
            return fail(
                f"The site answered HTTP {first.status_code}.",
                "403 or 429 usually means a bot filter -- lower SCRAPER_RATE_LIMIT, or ask "
                "the dealership to allow our agent.",
                "404 means the URL moved; open it in a browser and copy the real one.",
                f"Body began: {body!r}" if body else "The body was empty.",
            )
        if "html" not in first.headers.get("content-type", "").lower():
            print("  \033[33mNOTE\033[0m that is not HTML. An adapter will almost certainly "
                  "not match.")

        # ------------------------------------------------------------ 4 ----
        rule("4. Which reader understands this page")
        adapter = list_adapter_for(first.text, url)
        if adapter is not None:
            print(f"  list adapter {adapter.name}  (one page carries many vehicles)")
            if dealer_id:
                adapter = adapter.for_dealer(dealer_id)
                print(f"  narrowed to  store {dealer_id}")
            else:
                adapter = adapter.for_dealer("")
        else:
            print("  list adapter none matched")
            print("  falling back to per-vehicle pages (JSON-LD)")

        if args.pages:
            settings.scraper_max_pages = args.pages
            print(f"  page limit   {args.pages} (--pages)")

        # ------------------------------------------------------------ 5 ----
        rule("5. Crawling")
        began = time.monotonic()
        if adapter is not None:
            listings, errors, method = crawl_list(client, adapter, url, first.text)
        else:
            urls = discover(client, url)
            print(f"  discovered   {len(urls)} vehicle page(s)")
            if not urls:
                return fail(
                    "No adapter matched and no vehicle detail pages were found.",
                    "Save the page and look at it: `make ingest ARGS=--save-html`",
                    "Then `make capture URL=...` reports whether it emits JSON-LD at all.",
                    "If it does not, this platform needs an adapter in "
                    "backend/app/ingest/sites/.",
                )
            listings, errors = fetch_and_extract(client, urls)
            method = "jsonld"
        took = time.monotonic() - began

    print(f"  method       {method}")
    print(f"  kept         {len(listings)} vehicle(s) in {took:.1f}s")
    print(f"  dropped      {len(errors)} row(s)")

    for entry in errors[: args.limit_print]:
        print(f"    - {entry.get('error')}   {entry.get('url', '')[:70]}")
    if len(errors) > args.limit_print:
        print(f"    ... and {len(errors) - args.limit_print} more")

    if not listings:
        return fail(
            "The crawl finished and produced no usable vehicles.",
            f"{len(errors)} row(s) were dropped -- the reasons are above.",
            "If every one says 'no VIN on the card', the adapter matched the page but not "
            "its markup: the site has been restyled and the selectors need updating.",
            "`make ingest ARGS=--save-html` keeps the pages to compare against "
            "backend/app/ingest/fixtures/.",
        )

    # ---------------------------------------------------------------- 6 ----
    rule("6. What came back, field by field")
    # A fill rate rather than a pass/fail. "412 of 481 have a price" is a
    # normal lot; "0 of 481 have a VIN" means the adapter is reading the wrong
    # element, and the two need completely different answers.
    for field in FIELDS:
        filled = sum(1 for car in listings if getattr(car, field, None) not in (None, "", 0))
        share = filled / len(listings)
        bar = "#" * round(share * 20)
        flag = ""
        if field in ("vin", "make", "model") and share < 1:
            flag = "  <- required"
        elif filled == 0:
            flag = "  <- always empty"
        print(f"  {field:12} {filled:>5}/{len(listings)}  {bar:<20}{flag}")

    makes = Counter(car.make for car in listings if car.make)
    print(f"\n  makes        {len(makes)} distinct, top: "
          + ", ".join(f"{name} ({n})" for name, n in makes.most_common(5)))
    priced = [car.price for car in listings if car.price]
    if priced:
        print(f"  prices       ${min(priced):,} - ${max(priced):,}")
    lots = Counter((car.raw or {}).get("dealer_id", "") for car in listings)
    if len(lots) > 1:
        print(f"  \033[33mNOTE\033[0m cards from {len(lots)} different stores came back: "
              f"{dict(lots)}")
        print("       Set `inventory.dealer_id` unless this dealership really is all of them.")

    print("\n  first few:")
    for car in listings[: args.limit_print]:
        price = f"${car.price:,}" if car.price else "no price"
        print(f"    {car.vin}  {car.year} {car.make} {car.model} {car.trim}  {price}")

    # ---------------------------------------------------------------- 7 ----
    rule("7. Keeping the record")
    db = SessionLocal()
    try:
        written = snapshot.write(listings, source_url=url, method=method,
                                 errors=errors, dealership_name=seeded_name)
        print(f"  snapshot     {written}")
        if settings.scraper_save_photos:
            got = snapshot.fetch_photos(listings, dealership_name=seeded_name)
            print(f"  photos       {got['saved']} saved, {got['already_had']} already had, "
                  f"{len(got['failed'])} failed")
        else:
            print("  photos       hotlinked from the dealer (SCRAPER_SAVE_PHOTOS is off)")

        # ------------------------------------------------------------ 8 ----
        rule("8. What this would change")
        diff = build_diff(db, listings)
        print(f"  new          {len(diff['created'])}")
        print(f"  changed      {len(diff['updated'])}")
        print(f"  gone         {len(diff['removed'])}   (marked removed, never deleted)")

        for entry in diff["updated"][: args.limit_print]:
            bits = ", ".join(f"{k}: {v['from']} -> {v['to']}"
                             for k, v in list(entry["changes"].items())[:3])
            note = "  REAPPEARED" if entry.get("reappeared") else ""
            protected = f"  (kept by hand: {', '.join(entry['protected'])})" \
                if entry.get("protected") else ""
            print(f"    {entry['vin']}  {bits}{note}{protected}")
        for entry in diff["removed"][: args.limit_print]:
            print(f"    {entry['vin']}  {entry['title']}  -> removed")

        if not any(diff.values()):
            print("\n  Nothing to apply. The database already matches the site, which is "
                  "a successful run, not a failure.")

        # A car the crawl did not see is marked removed, and a crawl that
        # stopped early did not see most of the lot. `--pages 3` against a
        # 481-car site "finds" 3 and reports 478 gone -- publish that and the
        # showroom empties, the assistant stops offering anything, and it
        # looks exactly like the site changed. The diff cannot tell a sold-out
        # week from a crawl that died on page two, so it has to ask.
        on_sale = db.query(Vehicle).filter(
            Vehicle.status == "available", Vehicle.source == "scrape"
        ).count()
        share = len(diff["removed"]) / on_sale if on_sale else 0.0
        truncated = bool(args.pages) or bool(errors)
        risky = diff["removed"] and (share > 0.2 or truncated)
        if risky:
            print(f"\n  \033[33mHOLD ON\033[0m this would take {len(diff['removed'])} of "
                  f"{on_sale} cars off sale ({share:.0%}).")
            if args.pages:
                print(f"  The crawl was cut to {args.pages} page(s), so it never saw the rest "
                      "of the lot.")
            if errors:
                print(f"  {len(errors)} page(s) or row(s) failed, so the crawl may be "
                      "incomplete.")
            print("  A car this crawl did not see looks identical to a car that sold.")
            print("  Run it again without --pages, and with the failures fixed, before "
                  "publishing.")

        if not args.publish:
            rule("Not published")
            print("  Nothing was written to the database -- not even a run record.")
            print("  If the numbers above look right:")
            print("      make ingest ARGS=--publish")
            print(f"\n  Took {time.monotonic() - started:.1f}s.")
            return 0

        if risky and not args.allow_removals:
            return fail(
                "Refusing to publish a crawl that would empty most of the lot.",
                "Re-run it complete: `make ingest ARGS=--publish`",
                "If the removals are real -- they genuinely sold -- say so:",
                "  make ingest ARGS='--publish --allow-removals'",
            )

        # ------------------------------------------------------------ 9 ----
        rule("9. Publishing")
        run = IngestRun(
            source_url=url, status="ready", method=method,
            created_count=len(diff["created"]), updated_count=len(diff["updated"]),
            removed_count=len(diff["removed"]),
            diff_json=json.dumps(diff), errors_json=json.dumps(errors),
            listings_found=len(listings),
        )
        db.add(run)
        db.commit()
        applied = publish(db, run)
        print(f"  created      {applied['created']}")
        print(f"  updated      {applied['updated']}")
        print(f"  removed      {applied['removed']}")
        print(f"  protected    {applied['protected']}  (fields a rep had edited)")
        print(f"\n  on the lot   {db.query(Vehicle).count()} vehicle(s), was {before}")
        print(f"\n  Took {time.monotonic() - started:.1f}s. Open /showroom to see them.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted. Nothing was published.")
        sys.exit(130)
