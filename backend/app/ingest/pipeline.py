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
from datetime import timedelta
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from selectolax.parser import HTMLParser
from sqlalchemy.orm import Session

from app import profile
from app.config import settings
from app.db import utcnow
from app.ingest import browser, snapshot
from app.ingest.csv_import import changes_for, plausible_features
from app.ingest.extract import Listing, extract, list_adapter_for, list_adapter_named
# Imported for the side effect: each module registers itself onto the ladder.
import app.ingest.sites  # noqa: F401,E402
from app.models import Dealership, IngestRun, Vehicle

log = logging.getLogger("liner.ingest")

VDP_HINTS = ("/vehicle/", "/inventory/", "/vdp", "/used/", "/detail")


class IngestError(RuntimeError):
    pass


#: A run still `pending` after this long was cut off by a restart: the work
#: lived in the process, and nothing will ever finish it.
STALE_AFTER_MINUTES = 45


def open_client(adapter=None, *, identify: bool = True):
    """How to fetch this platform: a headless Chromium where it refuses
    anything else (`ListAdapter.browser`), plain HTTP everywhere else. One
    definition for the dashboard's crawl, `make ingest` and `make capture`."""
    if adapter is not None and adapter.browser:
        return browser.BrowserClient(user_agent=None if identify else "")
    return httpx.Client(headers={"User-Agent": settings.scraper_user_agent},
                        follow_redirects=True)


def robots_verdict(client: httpx.Client, base: str, path: str) -> tuple[bool, str]:
    """(may we crawl, why we think so).

    The reason matters as much as the answer. Every branch here except one
    returns True, including the two where robots.txt was never read at all --
    a site with no robots.txt has not refused, and neither has one that timed
    out. That is the right *default*, and reporting it as "robots.txt allows
    this path" is a lie: it made a crawl whose very first request had already
    timed out print a clean permission check, and then fail one step later
    looking like a different problem.
    """
    try:
        response = client.get(urljoin(base, "/robots.txt"), timeout=10)
    except httpx.HTTPError as exc:
        return True, f"could not be read ({type(exc).__name__}) -- proceeding, which is the default"
    if response.status_code != 200:
        return True, f"HTTP {response.status_code}, so there are no rules to follow"
    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    if parser.can_fetch(settings.scraper_user_agent, path):
        return True, "allows this path for our agent"
    return False, "DISALLOWS this path for our agent"


def _robots_allows(client: httpx.Client, base: str, path: str) -> bool:
    return robots_verdict(client, base, path)[0]


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
    # A car the site still shows but calls sold or on hold is not a car to
    # offer, so it counts as not seen: gone from the lot, like one the page
    # stopped listing.
    listings = [listing for listing in listings if listing.status == "available"]
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
            # The options list and what the source said beyond the columns.
            # They were left out of this payload, so every crawled car was
            # created with no stock number, spec cells, advertised price or
            # history link, and no re-crawl ever updated them.
            "features": list(listing.features), "raw": dict(listing.raw),
        }
        if current is None:
            created.append(payload)
            continue

        # A rep's edit is never the crawl's to overwrite; raw is merged.
        manual = set(json.loads(current.manual_fields_json or "[]"))
        changes = changes_for(current, payload, manual)
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


def start_run(db: Session, base_url: str) -> IngestRun:
    """The run's row, before any request is made -- what the page polls."""
    run = IngestRun(source_url=base_url, status="pending")
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def in_progress(db: Session) -> IngestRun | None:
    """A crawl still running, or None. One at a time per store: two would
    fetch the same site twice and race to write one review.

    A run left `pending` by a restart is marked failed here, saying so, the
    first time anybody asks -- otherwise the page would wait on it for ever.
    """
    cutoff = utcnow() - timedelta(minutes=STALE_AFTER_MINUTES)
    running = None
    for run in db.query(IngestRun).filter(IngestRun.status == "pending").all():
        if run.started_at and run.started_at < cutoff:
            run.status = "failed"
            run.finished_at = utcnow()
            run.errors_json = json.dumps([{"error": "interrupted -- the server restarted while "
                                                    "this ran. Start it again."}])
        elif running is None:
            running = run
    db.commit()
    return running


def read_details(client, adapter, listings: list[Listing], db: Session, *,
                 everything: bool = False, refresh: bool = False,
                 stats: dict | None = None) -> tuple[list[Listing], list[dict]]:
    """Each car's own page, for what its listing leaves out.

    By default only where the price a buyer pays is unknown: a car new to the
    lot, or one advertised at a different price than last time (GMA's list
    states the price before the dealer's fees, and the total is on the car's
    page -- never computed, CLAUDE.md). A car whose advertised price has not
    moved keeps the price it has, so a routine refresh reads one page, not 88.
    `everything` reads every car, for the options list.

    A needed page that cannot be read never becomes a guess: a new car is left
    out of this run, and a changed advertised price is not applied without the
    total that goes with it. Both are recorded as errors tagged `detail`, which
    `removal_risk` does not count as a list cut short.

    Reads are kept per car and advertised price (`snapshot.write_detail`), so a
    second run does not fetch the same page again.
    """
    stats = stats if stats is not None else {}
    stats.update(read=0, cached=0, failed=0)
    existing = {v.vin: v for v in db.query(Vehicle).all()}
    delay = 1.0 / max(settings.scraper_rate_limit, 0.1)
    kept: list[Listing] = []
    errors: list[dict] = []
    robots_ok: bool | None = None

    for listing in listings:
        if listing.errors or listing.status != "available":
            kept.append(listing)
            continue
        current = existing.get(listing.vin)
        advertised = str(listing.raw.get("advertised_price") or "")
        stored = json.loads(current.raw_json or "{}").get("advertised_price", "") if current else ""
        # Only where the site advertises a price: a car it lists without one
        # is call-for-price there, and stays that here rather than having its
        # page read for a total it does not state.
        need_price = bool(advertised) and (current is None or current.price is None
                                           or advertised != str(stored))
        if not (need_price or everything):
            kept.append(listing)
            continue

        detail = None
        cached = None if refresh else snapshot.read_detail(listing.vin)
        if cached and str(cached.get("advertised_price") or "") == advertised and (
                not everything or cached.get("features")):
            detail = cached
            stats["cached"] += 1
        else:
            url = adapter.detail_url(listing)
            problem = ""
            if not url:
                problem = "it has no page of its own"
            else:
                if robots_ok is None:
                    parsed = urlparse(url)
                    robots_ok, _why = robots_verdict(client, f"{parsed.scheme}://{parsed.netloc}",
                                                     parsed.path or "/")
                if not robots_ok:
                    problem = "robots.txt disallows the car pages"
            if not problem:
                if stats["read"] or stats["failed"]:
                    time.sleep(delay)
                try:
                    response = client.get(url, timeout=25)
                    if response.status_code != 200:
                        problem = f"its page answered HTTP {response.status_code}"
                    else:
                        detail = adapter.parse_detail(response.text, url, listing)
                        if not detail or (need_price and detail.get("price") is None):
                            problem, detail = "its page did not state a price", None
                except httpx.HTTPError as exc:
                    problem = str(exc)
            if detail is not None:
                stats["read"] += 1
                detail = {"vin": listing.vin, "url": url, "fetched_at": utcnow().isoformat(),
                          "advertised_price": advertised, "price": detail.get("price"),
                          "features": list(detail.get("features") or [])}
                try:
                    snapshot.write_detail(listing.vin, detail)
                except OSError as exc:
                    log.warning("could not keep the page read for %s: %s", listing.vin, exc)
            else:
                stats["failed"] += 1
                title = f"{listing.year or ''} {listing.make} {listing.model}".strip()
                if current is None:
                    what = "it was left out of this run"
                elif need_price:
                    what = "its advertised price changed on the site, and nothing was changed here"
                    listing.raw.pop("advertised_price", None)
                else:
                    what = "its options were not refreshed"
                errors.append({"url": url or listing.listing_url, "stage": "detail",
                               "error": f"{title}: could not read the price including fees "
                                        f"({problem}) -- {what}. Refresh again to retry."
                               if need_price else f"{title}: could not read its page "
                                                  f"({problem}) -- {what}."})
                if current is None:
                    continue
                kept.append(listing)
                continue

        if detail.get("price") is not None:
            listing.price = int(detail["price"])
        # Options fill an empty list and never replace one. A car's own page
        # can list fewer than the dealership once pasted by hand -- Alsbou's
        # Q7 carries a hundred lines, and its page names twenty-three -- and
        # a refresh must not throw the longer answer away.
        if detail.get("features") and not (current is not None
                                           and json.loads(current.features_json or "[]")):
            listing.features, _dropped = plausible_features(list(detail["features"]),
                                                            listing.body_style)
        kept.append(listing)
    return kept, errors


def removal_risk(db: Session, diff: dict, errors: list[dict], *,
                 cut_short: bool = False) -> dict | None:
    """None, or why publishing this diff needs a person to say the cars sold.

    A car the crawl did not see is marked removed, and a crawl that stopped
    early did not see most of the lot: the diff cannot tell a sold-out week
    from a crawl that died on page two. It lived inline in `make ingest`, so
    the dashboard's Publish had no such check at all. One car's own page
    failing (`stage: detail`) does not mean the list was half read.
    """
    removed = diff.get("removed") or []
    if not removed:
        return None
    on_sale = db.query(Vehicle).filter(Vehicle.status == "available",
                                       Vehicle.source == "scrape").count()
    share = len(removed) / on_sale if on_sale else 0.0
    list_errors = [e for e in errors if e.get("stage") != "detail"]
    if share <= 0.2 and not cut_short and not list_errors:
        return None
    why = []
    if cut_short:
        why.append("the crawl was cut short, so it never saw the rest of the lot")
    if list_errors:
        why.append(f"{len(list_errors)} page(s) failed, so the crawl may be incomplete")
    return {"removed": len(removed), "on_sale": on_sale, "share": round(share, 3),
            "why": why or [f"that is {share:.0%} of the cars on sale"]}


def run_ingest(db: Session, base_url: str, *, run: IngestRun | None = None,
               details: bool = False, refresh_details: bool = False) -> IngestRun:
    if run is None:
        run = start_run(db, base_url)

    source = profile.inventory()
    try:
        named = list_adapter_named(source.get("adapter", ""))
        if source.get("adapter") and named is None:
            raise IngestError(f"The profile names the site reader {source['adapter']!r}, "
                              "which this version does not have.")
        with open_client(named, identify=source.get("identify", True)) as client:
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
            if first.status_code != 200:
                # Read as a page, a 429's body found no adapter and failed as
                # "no vehicle pages found" -- the wrong problem entirely.
                raise IngestError(f"{base_url} answered HTTP {first.status_code}"
                                  + (" -- it refuses this kind of request; its profile "
                                     "should name the site reader (inventory.adapter)"
                                     if first.status_code in (403, 429) and named is None else ""))
            adapter = named or list_adapter_for(first.text, base_url)
            if named is not None and not named.matches(first.text, base_url):
                raise IngestError(f"The profile names the {named.name} site reader, and "
                                  f"{base_url} is not one of its pages.")
            if adapter is not None:
                # Which of the stores on this page is ours, read now rather
                # than at import: the dealership is a per-deployment choice
                # and the profile is read per call, so an adapter narrowed
                # when the process started is narrowed to whoever it started
                # as.
                adapter = adapter.for_dealer(source["dealer_id"])
                listings, errors, method = crawl_list(client, adapter, base_url, first.text)
                if adapter.reads_details:
                    listings, detail_errors = read_details(
                        client, adapter, listings, db,
                        everything=details, refresh=refresh_details)
                    errors += detail_errors
                    if details:
                        method = f"{adapter.name}+details"
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
    except (IngestError, httpx.HTTPError, browser.BrowserUnavailable) as exc:
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


def _keywords(payload: dict) -> str:
    """The keyword haystack for one car. Features join it: a buyer types
    "heated seats", not a body style. The store name goes in too, so "the one
    in Clarksville" is findable. One definition for a new car and an updated
    one -- it was only ever computed on create, so an options list a re-crawl
    brought in never became searchable."""
    return " ".join(
        [str(payload.get(k) or "") for k in ("make", "model", "trim", "body_style")]
        + list(payload.get("features") or [])
        + [str((payload.get("raw") or {}).get("location") or "")]
    ).lower()


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
            # Whatever the source said and this schema has no column for --
            # which store the car is on, the stock number, that store's doc
            # fee. `create_all` adds a table to an existing database and never
            # a column, so this is where a field like that lives.
            raw_json=json.dumps(payload.get("raw") or {}),
            keywords=_keywords(payload),
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
            # Two keys are not columns. `features` and `raw` are stored as
            # JSON text under other names, and `setattr` on the payload's
            # name wrote an attribute nothing reads -- so a re-import never
            # updated an existing car's options list or its store and stock
            # data, silently, while reporting the change applied.
            if key == "features":
                vehicle.features_json = json.dumps(list(change["to"] or []))
            elif key == "raw":
                vehicle.raw_json = json.dumps(change["to"] or {})
            else:
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
        vehicle.keywords = _keywords({
            "make": vehicle.make, "model": vehicle.model, "trim": vehicle.trim,
            "body_style": vehicle.body_style,
            "features": json.loads(vehicle.features_json or "[]"),
            "raw": json.loads(vehicle.raw_json or "{}")})
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
