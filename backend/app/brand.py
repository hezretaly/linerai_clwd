"""The prospect's colours, read off their profile.

Deliberately not a database column. `create_all` adds a table to a database
that already exists and never a column, and there is no Alembic here -- so a
`brand` column would simply not appear on a deployed install, which is the one
place it matters. It is also not per-row data: which dealership this instance
is set up as is a deployment decision, and it already lives in a file.

Only the accent family travels. Every structural token -- greys, borders,
radii, the whole of shadcn classic -- still comes from
`styles/liner-theme.css`, so a prospect's colour cannot quietly restyle the
product into something that no longer reads. That is the same rule
`.theme-buyer` already follows; this makes the accent a value rather than a
constant.
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


def brand() -> dict:
    """Accent, ink and logo for whichever profile this instance is running."""
    raw = _section("brand")
    return {
        "accent": _colour(raw.get("accent"), DEFAULT_ACCENT),
        "accent_ink": _colour(raw.get("accent_ink"), DEFAULT_INK),
        # A URL, not a colour, so it is offered only when it is one we would
        # actually load. Anything else is dropped and the name is used.
        "logo_url": _link(raw.get("logo_url")),
    }
