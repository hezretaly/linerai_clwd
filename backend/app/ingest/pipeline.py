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

from app.config import settings
from app.db import utcnow
from app.ingest.extract import Listing, extract
from app.models import IngestRun, Vehicle

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

            urls = discover(client, base_url)
            if not urls:
                raise IngestError(
                    "No vehicle detail pages found. Check the URL, or import a CSV instead."
                )
            listings, errors = fetch_and_extract(client, urls)

        diff = build_diff(db, listings)
        run.method = "jsonld"
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
            photo_url=payload.get("photo_url") or f"/api/photos/{payload['vin']}.svg",
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
        if entry.get("reappeared"):
            vehicle.status = "available"
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
