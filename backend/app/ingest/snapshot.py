"""What a crawl found, kept on disk beside the database.

`backend/var/inventory/<dealership>/` — `snapshot.json` and `photos/<VIN>.jpg`.

The database is the product's answer to "what is on the lot"; this is the
crawl's answer to "what did the site say, when". They are different questions
and the second one has no home otherwise: an `IngestRun` keeps a diff, which
is what *changed*, so a field the adapter never managed to read leaves no
trace at all once a run is published.

Three things it buys:

* **A crawl you can read.** One JSON file per dealership, with every field the
  adapter extracted and the URL each row came from, so "why is body_style
  empty" is answered by looking rather than by re-running a crawl against
  somebody else's server.
* **A demo that survives their CDN.** Photos are otherwise hotlinked from the
  dealer's image host. That works right up until a car sells and the URL
  404s, or the host refuses an off-site referrer — mid-demo, on the screen
  they are watching. Downloaded once, they are ours to serve.
* **A way in for a machine that cannot reach the site.** The snapshot is a
  complete inventory in a file, so a crawl run where the site is reachable can
  be replayed anywhere.

Per dealership, because the folder is named after the profile this instance is
running as. Two prospects' inventories in one directory would be one
prospect's cars appearing in the other's demo, which is the failure the whole
single-dealership rule exists to prevent.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import httpx

from app.config import settings
from app.ingest.extract import Listing

#: Where a photo may come from and what it may be. A dealer's CDN serves
#: JPEGs; anything else is either a placeholder we drew ourselves or a URL
#: worth not following.
IMAGE_TYPES = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/avif": ".avif",
}

#: A photo bigger than this is not a photo of a car. Bounded because the
#: bytes come from somebody else's server.
MAX_PHOTO_BYTES = 8 * 1024 * 1024

SAFE = re.compile(r"[^a-z0-9-]+")


def slug(name: str) -> str:
    """`Craig and Landreth Cars` -> `craig-and-landreth-cars`."""
    return SAFE.sub("-", (name or "dealership").strip().lower()).strip("-") or "dealership"


def _dir(dealership_name: str = "") -> Path:
    """Where this dealership's files would be, without creating anything.

    Named from the profile when there is one, because that is what the
    operator chose and typed; the dealership row's name is the fallback.
    """
    name = settings.dealership.strip() or slug(dealership_name)
    return settings.inventory_dir / slug(name)


def folder(dealership_name: str = "") -> Path:
    """The same directory, created ready to be written into.

    Split from `_dir` because a *lookup* must not create one. `photo_path` is
    called on every request to `/api/photos/{vin}`, so with the two merged
    every placeholder a CSV-imported lot draws left an empty folder behind --
    and for a deployment with no profile set they all had the same fallback
    name, so the debris looked like a real dealership's directory.
    """
    path = _dir(dealership_name)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write(
    listings: list[Listing],
    *,
    source_url: str,
    method: str,
    errors: list[dict],
    dealership_name: str = "",
) -> Path:
    """Write `snapshot.json`, and return where it went.

    Whole-file, not appended: a snapshot is what the site said on one run, and
    a half-written one from a crawl that died mid-page is worse than the
    previous complete one. Written to a temporary name and moved into place so
    a reader never sees a partial file.
    """
    out = folder(dealership_name)
    payload = {
        "dealership": dealership_name or settings.dealership,
        "source_url": source_url,
        "method": method,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(listings),
        # Kept beside the rows rather than in a log: a row the adapter could
        # not read is the most useful thing in the file.
        "errors": errors,
        "vehicles": [asdict(listing) for listing in listings],
    }
    temp = out / "snapshot.json.tmp"
    temp.write_text(json.dumps(payload, indent=2, default=str))
    final = out / "snapshot.json"
    temp.replace(final)
    return final


def read(dealership_name: str = "") -> dict | None:
    """The last snapshot for this dealership, or None if there is not one."""
    path = _dir(dealership_name) / "snapshot.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def photo_path(vin: str, dealership_name: str = "") -> Path | None:
    """The stored photo for a VIN, whatever extension it was saved under."""
    if not vin:
        return None
    photos = _dir(dealership_name) / "photos"
    for suffix in (".jpg", ".png", ".webp", ".avif"):
        candidate = photos / f"{vin.upper()}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def fetch_photos(
    listings: list[Listing], *, dealership_name: str = "", client: httpx.Client | None = None
) -> dict:
    """Download each listing's photo once, at the crawl's own rate limit.

    **One photo per car, which is the one the listing card carries.** That is
    also the only one this crawl has: the card holds a single image, and the
    rest of a car's forty-odd photos live on its detail page. Fetching those
    would mean a detail request per vehicle -- the 481 fetches the list
    adapter exists to avoid -- and then roughly nineteen thousand images.
    Worth doing one day for a gallery; not worth it to put a picture on a
    search result, which is the only place Liner shows a car.

    Rate limited like the crawl itself, and skipped for a VIN already on disk:
    a re-run of a 481-car lot should cost a handful of requests for the cars
    that changed, not 481 for the ones that did not. Never fatal -- a photo
    that will not download leaves the remote URL in place, which is exactly
    what the behaviour was before any of this existed.
    """
    photos = folder(dealership_name) / "photos"
    photos.mkdir(parents=True, exist_ok=True)
    delay = 1.0 / max(settings.scraper_rate_limit, 0.1)
    owned = client is None
    client = client or httpx.Client(
        headers={"User-Agent": settings.scraper_user_agent},
        follow_redirects=True, timeout=25,
    )
    saved, skipped, failed = 0, 0, []
    try:
        for listing in listings:
            url = (listing.photo_url or "").strip()
            if not listing.vin or not url.startswith(("http://", "https://")):
                continue
            if photo_path(listing.vin, dealership_name) is not None:
                skipped += 1
                continue
            time.sleep(delay)
            try:
                response = client.get(url)
                if response.status_code != 200:
                    failed.append({"vin": listing.vin, "error": f"HTTP {response.status_code}"})
                    continue
                kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                suffix = IMAGE_TYPES.get(kind)
                if suffix is None:
                    failed.append({"vin": listing.vin, "error": f"not an image ({kind})"})
                    continue
                if len(response.content) > MAX_PHOTO_BYTES:
                    failed.append({"vin": listing.vin,
                                   "error": f"{len(response.content)} bytes is too big"})
                    continue
                (photos / f"{listing.vin.upper()}{suffix}").write_bytes(response.content)
                saved += 1
            except httpx.HTTPError as exc:
                failed.append({"vin": listing.vin, "error": str(exc)})
    finally:
        if owned:
            client.close()
    return {"saved": saved, "already_had": skipped, "failed": failed, "dir": str(photos)}
