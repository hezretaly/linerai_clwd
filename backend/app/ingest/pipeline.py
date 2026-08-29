"""discover -> fetch -> extract -> normalise -> diff -> review -> publish.

Two rules keep this from breaking a demo, and both are enforced in code rather
than in a runbook:

- **Nothing auto-publishes.** A run produces a diff; a human applies it.
- **Manual override always wins.** A field a rep edited is listed in
  ``vehicles.manual_fields_json`` and the publisher skips it.
"""

from __future__ import annotations

import json
import logging
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from selectolax.parser import HTMLParser
from sqlalchemy.orm import Session

from app import profile
from app.config import settings
from app.db import utcnow
from app.ingest import snapshot
from app.ingest.extract import Listing, extract, list_adapter_for
# Imported for the side effect: each module registers itself onto the ladder.
import app.ingest.sites  # noqa: F401,E402
from app.models import Dealership, IngestRun, Vehicle

log = logging.getLogger("liner.ingest")

VDP_HINTS = ("/vehicle/", "/inventory/", "/vdp", "/used/", "/detail")


class IngestError(RuntimeError):
    pass


def _robots_allows(client: httpx.Client, base: str, path: str) -> bool:
    try:
        response = client.get(urljoin(base, "/robots.txt"), timeout=10)
        if response.status_code != 200:
            return True
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser.can_fetch(settings.scraper_user_agent, path)
    except httpx.HTTPError:
        return True


def discover(client: httpx.Client, base: str) -> list[str]:
    """sitemap.xml filtered to vehicle-detail patterns, then a listing crawl."""
    urls: list[str] = []
    try:
        response = client.get(urljoin(base, "/sitemap.xml"), timeout=15)
        if response.status_code == 200:
            tree = HTMLParser(response.text)
            urls = [
                node.text().strip()
                for node in tree.css("loc")
                if any(hint in node.text().lower() for hint in VDP_HINTS)
            ]
    except httpx.HTTPError as exc:
        log.info("no usable sitemap at %s: %s", base, exc)

    if not urls:
        try:
            response = client.get(base, timeout=15)
            tree = HTMLParser(response.text)
            seen = set()
            for anchor in tree.css("a[href]"):
                href = anchor.attributes.get("href", "")
                if any(hint in href.lower() for hint in VDP_HINTS):
                    full = urljoin(base, href)
                    if full not in seen:
                        seen.add(full)
                        urls.append(full)
        except httpx.HTTPError as exc:
            raise IngestError(f"Could not reach {base}: {exc}") from exc

    return urls[: settings.scraper_max_pages]


def fetch_and_extract(client: httpx.Client, urls: list[str]) -> tuple[list[Listing], list[dict]]:
    listings: list[Listing] = []
    errors: list[dict] = []
    delay = 1.0 / max(settings.scraper_rate_limit, 0.1)

    for url in urls:
        try:
            response = client.get(url, timeout=20)
            if response.status_code != 200:
                errors.append({"url": url, "error": f"HTTP {response.status_code}"})
                continue
            listing, method = extract(response.text, url)
            if listing is None:
                errors.append({"url": url, "error": "no JSON-LD and no adapter matched"})
                continue
            if listing.errors:
                # A malformed page is recorded, not silently dropped -- the
                # review screen shows what was skipped and why.
                errors.append({"url": url, "error": "; ".join(listing.errors),
                               "method": method})
                continue
            listings.append(listing)
        except httpx.HTTPError as exc:
            errors.append({"url": url, "error": str(exc)})
        time.sleep(delay)

    return listings, errors


def crawl_list(
    client: httpx.Client, adapter, base_url: str, first_page: str
) -> tuple[list[Listing], list[dict], str]:
    """Page through a listing site, keeping what each page already tells us.

    The first page is handed in because `run_ingest` has already fetched it to
    decide which adapter applies -- fetching it twice would be a wasted
    request against somebody else's server on every single import.

    **A page that yields no new VIN ends the crawl.** Pagination is a request
    the *site* has to honour, and a site that does not simply hands back page
    one again: an unknown `pagesize`, a filter that resets, a session that
    expired, a proxy serving a cached copy. Nothing about that response says
    it is a repeat -- it is HTTP 200 with a page full of cars -- so without
    this the crawl reads page one twenty-one times, reports "481 vehicles
    found", and hands the diff forty-two copies of two cars. Measured: exactly
    that, against a server that ignores query strings.

    Stopping on *no new VINs* rather than on identical bytes, because a real
    site can differ by a timestamp or an ad slot and still be the same page.
    """
    listings: list[Listing] = []
    errors: list[dict] = []
    seen: set[str] = set()
    delay = 1.0 / max(settings.scraper_rate_limit, 0.1)
    html, url, page = first_page, base_url, 1

    while page <= settings.scraper_max_pages:
        fresh = 0
        for listing in adapter.parse_list(html, url):
            if listing.errors:
                errors.append({"url": listing.listing_url or url,
                               "error": "; ".join(listing.errors),
                               "method": adapter.name})
                continue
            # Deduplicated by VIN, which is the identity a vehicle actually
            # has. A car legitimately appearing on two pages -- a "featured"
            # strip above the results is common -- should be one row, not two.
            if listing.vin and listing.vin in seen:
                continue
            if listing.vin:
                seen.add(listing.vin)
            listings.append(listing)
            fresh += 1

        if page > 1 and fresh == 0:
            errors.append({
                "url": url,
                "error": "this page repeated the previous one -- pagination is not advancing",
                "method": adapter.name,
            })
            break

        page += 1
        following = adapter.page_url(base_url, page, html)
        if not following:
            break
        time.sleep(delay)
        try:
            response = client.get(following, timeout=25)
        except httpx.HTTPError as exc:
            errors.append({"url": following, "error": str(exc)})
            break
        if response.status_code != 200:
            errors.append({"url": following, "error": f"HTTP {response.status_code}"})
            break
        html, url = response.text, following

    return listings, errors, adapter.name


def build_diff(db: Session, listings: list[Listing]) -> dict:
    existing = {v.vin: v for v in db.query(Vehicle).all()}
    seen_vins = {listing.vin for listing in listings}

    created, updated = [], []
    for listing in listings:
        current = existing.get(listing.vin)
        payload = {
            "vin": listing.vin, "year": listing.year, "make": listing.make,
            "model": listing.model, "trim": listing.trim, "price": listing.price,
            "mileage": listing.mileage, "body_style": listing.body_style,
            "seats": listing.seats, "photo_url": listing.photo_url,
            "listing_url": listing.listing_url,
        }
        if current is None:
            created.append(payload)
            continue

        manual = set(json.loads(current.manual_fields_json or "[]"))
        changes = {}
        for key, value in payload.items():
            if key == "vin" or value in (None, ""):
                continue
            if key in manual:
                continue  # a rep edited this; the scrape does not get to win
            if getattr(current, key, None) != value:
                changes[key] = {"from": getattr(current, key, None), "to": value}
        if changes or current.status != "available":
            updated.append({"vin": listing.vin, "changes": changes,
                            "protected": sorted(manual & set(payload)),
                            "reappeared": current.status != "available"})

    removed = [
        {"vin": vin, "title": f"{v.year} {v.make} {v.model}"}
        for vin, v in existing.items()
        if vin not in seen_vins and v.status == "available" and v.source == "scrape"
    ]

    return {"created": created, "updated": updated, "removed": removed}


def run_ingest(db: Session, base_url: str) -> IngestRun:
    run = IngestRun(source_url=base_url, status="pending")
    db.add(run)
    db.commit()

    headers = {"User-Agent": settings.scraper_user_agent}
    try:
        with httpx.Client(headers=headers, follow_redirects=True) as client:
            parsed = urlparse(base_url)
            root = f"{parsed.scheme}://{parsed.netloc}"
            if not _robots_allows(client, root, parsed.path or "/"):
                raise IngestError(f"robots.txt disallows crawling {base_url}")

            # A platform whose listing page already carries every field is
            # crawled through that page, not through 481 detail fetches for
            # facts five list pages already stated. Tried first because where
            # it applies it is both faster and far less to ask of a dealer's
            # server.
            first = client.get(base_url, timeout=20)
            adapter = list_adapter_for(first.text, base_url)
            if adapter is not None:
                # Which of the stores on this page is ours, read now rather
                # than at import: the dealership is a per-deployment choice
                # and the profile is read per call, so an adapter narrowed
                # when the process started is narrowed to whoever it started
                # as.
                adapter = adapter.for_dealer(profile.inventory()["dealer_id"])
                listings, errors, method = crawl_list(client, adapter, base_url, first.text)
            else:
                urls = discover(client, base_url)
                if not urls:
                    raise IngestError(
                        "No vehicle detail pages found. Check the URL, or import a CSV instead."
                    )
                listings, errors = fetch_and_extract(client, urls)
                method = "jsonld"

        # Written before the diff, and before anything is published: a
        # snapshot is what the site said, and it is most useful for a run that
        # went wrong. An IngestRun keeps what *changed*, so a field the
        # adapter never read leaves no trace once a run is published.
        dealer = db.query(Dealership).first()
        name = dealer.name if dealer else ""
        try:
            written = snapshot.write(listings, source_url=base_url, method=method,
                                     errors=errors, dealership_name=name)
            log.info("snapshot: %d vehicles -> %s", len(listings), written)
            if settings.scraper_save_photos:
                # One request per car, so it is behind a setting. Their CDN
                # 404ing a sold car mid-demo is the failure this prevents.
                got = snapshot.fetch_photos(listings, dealership_name=name)
                log.info("photos: %d saved, %d already had, %d failed",
                         got["saved"], got["already_had"], len(got["failed"]))
        except OSError as exc:
            # Never fatal. The crawl's result belongs in the database whether
            # or not a disk somewhere would take a copy of it.
            log.warning("could not write the snapshot: %s", exc)

        diff = build_diff(db, listings)
        # What actually read the pages. This was hardcoded "jsonld", which was
        # true while that was the only rung and a lie the moment it was not --
        # and the run record is where somebody looks to find out why a field
        # is missing.
        run.method = method
        run.listings_found = len(listings)
        run.created_count = len(diff["created"])
        run.updated_count = len(diff["updated"])
        run.removed_count = len(diff["removed"])
        run.diff_json = json.dumps(diff, default=str)
        run.errors_json = json.dumps(errors)
        # 'ready' -- awaiting review. Never 'published'.
        run.status = "ready"
    except (IngestError, httpx.HTTPError) as exc:
        run.status = "failed"
        run.errors_json = json.dumps([{"error": str(exc)}])
    finally:
        run.finished_at = utcnow()
        db.commit()

    db.refresh(run)
    return run


def _photo_for(payload: dict) -> str:
    """Where a car's picture comes from, in the order that is usually right.

    **The dealer's own URL by default, and that is deliberate rather than
    lazy.** Their CDN is faster than this box and closer to the viewer, it
    costs no requests and no disk, and it stays current: a dealer who swaps a
    photo has swapped ours too, where a downloaded copy quietly goes stale.

    A stored copy wins only where one exists, which means only when somebody
    turned SCRAPER_SAVE_PHOTOS on. That is demo insurance -- a venue with bad
    wifi, or an image host that turns out to refuse off-site referrers -- and
    it has to actually take effect, or the setting is a lie: it downloaded 481
    files and every row still pointed at the internet.

    The drawn placeholder is last, and stays: a CSV-imported lot has no photos
    at all and its rows still have to render.
    """
    from app.ingest import snapshot

    vin = payload.get("vin") or ""
    if vin and snapshot.photo_path(vin) is not None:
        return f"/api/photos/{vin.upper()}"
    return payload.get("photo_url") or f"/api/photos/{vin}.svg"


def publish(db: Session, run: IngestRun) -> dict:
    if run.status != "ready":
        raise IngestError(f"Run is {run.status}; only a reviewed 'ready' run can be published.")

    diff = json.loads(run.diff_json or "{}")
    existing = {v.vin: v for v in db.query(Vehicle).all()}
    applied = {"created": 0, "updated": 0, "removed": 0, "protected": 0}

    for payload in diff.get("created", []):
        vehicle = Vehicle(
            vin=payload["vin"], year=payload.get("year") or 0,
            make=payload.get("make") or "", model=payload.get("model") or "",
            trim=payload.get("trim") or "", price=payload.get("price"),
            mileage=payload.get("mileage"), body_style=payload.get("body_style") or "",
            seats=payload.get("seats"),
            photo_url=_photo_for(payload),
            listing_url=payload.get("listing_url") or "",
            status="available", source="scrape", ingest_run_id=run.id,
            features_json=json.dumps(payload.get("features") or []),
            # Features join the keyword haystack: a buyer types "heated seats",
            # not a body style.
            keywords=" ".join(
                [str(payload.get(k) or "") for k in ("make", "model", "trim", "body_style")]
                + list(payload.get("features") or [])
            ).lower(),
        )
        db.add(vehicle)
        applied["created"] += 1

    for entry in diff.get("updated", []):
        vehicle = existing.get(entry["vin"])
        if vehicle is None:
            continue
        manual = set(json.loads(vehicle.manual_fields_json or "[]"))
        for key, change in entry.get("changes", {}).items():
            if key in manual:
                applied["protected"] += 1
                continue
            setattr(vehicle, key, change["to"])
        # A vehicle back in the feed is available again -- unless a rep said
        # otherwise. Marking a car sold is the one edit that has to outlive the
        # next import: the dealership's own website will still be listing it
        # for hours, so an unguarded reappearance puts a sold car straight back
        # in front of the model. Manual override wins here like everywhere else.
        if entry.get("reappeared") and "status" not in manual:
            vehicle.status = "available"
        elif entry.get("reappeared"):
            applied["protected"] += 1
        vehicle.last_seen_at = utcnow()
        vehicle.ingest_run_id = run.id
        applied["updated"] += 1

    for entry in diff.get("removed", []):
        vehicle = existing.get(entry["vin"])
        if vehicle is None:
            continue
        # Never hard-deleted: a vehicle that vanishes from the site may just be
        # a broken page, and the mention history has to stay intact.
        vehicle.status = "removed"
        applied["removed"] += 1

    run.status = "published"
    db.commit()
    return applied
