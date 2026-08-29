"""The running dealership's profile, read per request.

`backend/config/dealerships/<name>.yaml`, chosen with `DEALERSHIP=<name>`. The
seed reads the same file for the rows it builds; this reads the parts that are
served rather than stored.

**Deliberately not database columns.** `create_all` adds a table to a database
that already exists and never a column, and there is no Alembic here -- so a
`brand` column would simply not appear on a deployed install, which is the one
place it matters. None of it is per-row data either: which dealership this
instance *is* is a deployment decision, and it already lives in a file.

**A fact about the dealership belongs here; a fact about the box belongs in
`.env`.** That line is what decides where each setting lives, and getting it
wrong has a specific cost. Their listing URL and their Dealer Car Search store
id were environment variables, which meant switching `DEALERSHIP=` to another
prospect and forgetting the other two lines crawled the first dealer's site
into the second one's instance -- silently, and exactly the failure the
per-dealership profile exists to prevent. `SCRAPER_BASE_URL` and
`SCRAPER_DEALER_ID` are still read, as the fallback for a profile that states
neither and for pointing at the local fixture site; but where the profile says
something, the profile wins, because switching dealership has to be one line.

Only the accent family of the brand travels. Every structural token -- greys,
borders, radii, the whole of shadcn classic -- still comes from
`styles/liner-theme.css`, so a prospect's colour cannot quietly restyle the
product into something that no longer reads.
"""

from __future__ import annotations

import re

import yaml

from app.config import settings

#: A CSS colour we are willing to interpolate into a style, and nothing else.
#:
#: This value comes from a file an operator edits, and it ends up inside a
#: `style` attribute in the buyer's browser. Anything that is not plainly a
#: hex colour is dropped rather than escaped: there is no legitimate reason for
#: a brand accent to be a `url(...)`, and a validator that tries to sanitise
#: arbitrary CSS is a validator that will eventually be wrong.
HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

#: What the buyer surfaces already use, and what an unset or rejected value
#: falls back to. Never a blank: an empty accent renders invisible text.
DEFAULT_ACCENT = "#0a84ff"
DEFAULT_INK = "#ffffff"


def _colour(value, fallback: str) -> str:
    text = str(value or "").strip()
    return text if HEX.match(text) else fallback


def _section(key: str) -> dict:
    """One top-level block of the running profile, or an empty one.

    Read per call rather than cached at import: the profile is edited during
    setup, and a colour or a headline that needs a restart to appear is one
    somebody will conclude does not work. A malformed file gives defaults
    rather than a 500 -- a YAML typo should make the page plain, not break it.
    """
    path = settings.dealership_config
    if not path.is_file():
        return {}
    try:
        return (yaml.safe_load(path.read_text()) or {}).get(key) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _section_list(key: str) -> list:
    """A top-level block that is a list rather than a mapping."""
    path = settings.dealership_config
    if not path.is_file():
        return []
    try:
        value = (yaml.safe_load(path.read_text()) or {}).get(key)
    except (OSError, yaml.YAMLError):
        return []
    return value if isinstance(value, list) else []


def _link(value) -> str:
    """A URL we are willing to put in an `href` or a `src`, and nothing else.

    Same instinct as `HEX`: these come from a file an operator edits and land
    in the buyer's browser, so `javascript:` and `data:` are refused by
    accepting only the two schemes a real dealer link ever has.
    """
    text = str(value or "").strip()
    return text if text.startswith(("https://", "/")) else ""


def _links(raw, limit: int) -> list[dict]:
    out = []
    for item in (raw or [])[:limit]:
        label = str((item or {}).get("label") or "").strip()
        href = _link((item or {}).get("href"))
        if label and href:
            out.append({"label": label[:40], "href": href})
    return out


def site() -> dict:
    """Their front page as they wrote it: headings, copy, links, hero.

    Served rather than written into `Showroom.tsx` for exactly the reason the
    dealership's *name* is served. A prospect's own sentences hardcoded in a
    component is the same bug one level up -- the next instance greets
    somebody as Craig and Landreth, in their words, on the first screen of
    somebody else's demo.

    Everything is optional. A profile with no `site:` block renders a plain
    page carrying the name, address, phone and lot, which is a perfectly
    honest storefront and is what Riverside gets.
    """
    raw = _section("site")
    return {
        "tagline": str(raw.get("tagline") or "").strip()[:160],
        "heading": str(raw.get("heading") or "").strip()[:160],
        "hero_image": _link(raw.get("hero_image")),
        "welcome": [str(p).strip() for p in (raw.get("welcome") or [])[:4] if str(p).strip()],
        "links": _links(raw.get("links"), 8),
        "social": _links(raw.get("social"), 6),
    }


def inventory() -> dict:
    """Where this dealership's cars come from, and which lot to keep.

    **These are facts about the dealership, not about the box**, which is why
    they moved here. As environment variables, switching `DEALERSHIP=` to a
    second prospect and leaving `SCRAPER_BASE_URL` alone crawled the first
    dealer's site into the second one's instance -- and the failure is silent,
    because a successful crawl of the wrong site looks exactly like a
    successful crawl. Switching dealership has to be one line.

    The environment still answers when the profile does not: that is the local
    fixture site (`make fixture-site`, then `SCRAPER_BASE_URL=http://...:8100`)
    and any deployment written before this existed. Where both speak, the
    profile wins, and `origin` says which so the import screen can print it --
    a source you did not expect is worth seeing *before* you press Publish.

    `dealer_id` is only meaningful for a platform that lists several stores on
    one page. Dealer Car Search does; most do not, and empty takes every card.
    """
    raw = _section("inventory")
    url = str(raw.get("source_url") or "").strip()
    dealer_id = str(raw.get("dealer_id") or "").strip()
    if url:
        return {"source_url": url, "dealer_id": dealer_id, "origin": "profile"}
    return {
        "source_url": settings.scraper_base_url.strip(),
        "dealer_id": settings.scraper_dealer_id.strip(),
        "origin": "env" if settings.scraper_base_url.strip() else "none",
    }


def staff() -> list[dict]:
    """The dealership's own people, so a reseed rebuilds them.

    `make add-user` puts somebody on a live box without a reseed, which is the
    common case. This is the other half: a person listed here is recreated by
    every `make reset-db`, so a prospect's own manager does not quietly
    disappear the next time the fixture is rebuilt.

    Roles are checked against the two that exist. `owner` is not one of them
    here for the same reason `add_user` refuses it -- that is us, it lives in
    `ops_users`, and a profile file is not where our own accounts come from.
    """
    out = []
    for item in (_section_list("staff"))[:12]:
        name = str((item or {}).get("name") or "").strip()
        email = str((item or {}).get("email") or "").strip().lower()
        role = str((item or {}).get("role") or "rep").strip().lower()
        if name and "@" in email and role in ("manager", "rep"):
            out.append({"name": name[:80], "email": email, "role": role})
    return out


def brand() -> dict:
    """Accent, ink and logo for whichever profile this instance is running."""
    raw = _section("brand")
    return {
        "accent": _colour(raw.get("accent"), DEFAULT_ACCENT),
        "accent_ink": _colour(raw.get("accent_ink"), DEFAULT_INK),
        # A URL, not a colour, so it is offered only when it is one we would
        # actually load. Anything else is dropped and the name is used.
        "logo_url": _link(raw.get("logo_url")),
        # `light` or `dark`, and nothing else -- it selects a stylesheet class
        # rather than carrying a value into one, so an unknown word must not
        # reach the DOM. Read only by /showroom: a dealership whose own site
        # is dark should have a dark storefront, and their reps' dashboard
        # should not change colour because of it.
        "surface": "dark" if str(raw.get("surface") or "").strip().lower() == "dark" else "light",
    }
