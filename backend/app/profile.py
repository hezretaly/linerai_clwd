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
from pathlib import Path

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


def _path() -> Path:
    """The profile file for whichever store this request is for.

    Read through `active_store()` rather than `settings.dealership`, which is
    fixed at startup. The two agree in a single-store deployment -- the
    ContextVar is empty and falls back to the configured name -- so nothing
    changes for an instance that serves one dealership. Where they differ is
    the whole point: `/alsbou/showroom` has to answer with Alsbou's brand and
    Alsbou's copy, and reading the startup value there would serve their cars
    wearing somebody else's livery, which is the "Riverside Auto" bug with a
    second dealership's name on it.
    """
    from app.db import active_store

    return settings.config_for(active_store())


def _section(key: str) -> dict:
    """One top-level block of the running profile, or an empty one.

    Read per call rather than cached at import: the profile is edited during
    setup, and a colour or a headline that needs a restart to appear is one
    somebody will conclude does not work. A malformed file gives defaults
    rather than a 500 -- a YAML typo should make the page plain, not break it.
    """
    path = _path()
    if not path.is_file():
        return {}
    try:
        return (yaml.safe_load(path.read_text()) or {}).get(key) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _top(key: str):
    """One top-level scalar of the running profile, or None."""
    path = _path()
    if not path.is_file():
        return None
    try:
        return (yaml.safe_load(path.read_text()) or {}).get(key)
    except (OSError, yaml.YAMLError):
        return None


def _section_list(key: str) -> list:
    """A top-level block that is a list rather than a mapping."""
    path = _path()
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
    # Their rotating banner, and `hero_image` is the one-image spelling of the
    # same thing. Both are read and the list is the union, so a profile written
    # before this existed keeps its hero and a profile with five gets five --
    # a second key that silently overrode the first would make the older
    # spelling look broken rather than superseded.
    heroes: list[str] = []
    for value in [raw.get("hero_image"), *(raw.get("hero_images") or [])]:
        url = _link(value)
        if url and url not in heroes:
            heroes.append(url)
    return {
        "tagline": str(raw.get("tagline") or "").strip()[:160],
        "heading": str(raw.get("heading") or "").strip()[:160],
        # The first one, kept so nothing that already reads a single hero has
        # to learn about the list.
        "hero_image": heroes[0] if heroes else "",
        "hero_images": heroes[:6],
        "welcome": [str(p).strip() for p in (raw.get("welcome") or [])[:4] if str(p).strip()],
        # The subheadings under their About copy, each with its own paragraph.
        # Alsbou's page runs "Your Trusted Source for Used Cars", "Quality and
        # Affordability" and "Visit Us Today!" -- flattened into one column of
        # undifferentiated prose, which is what `welcome` alone could hold,
        # their About section stops looking like their About section. Separate
        # from `welcome` rather than a shape it may also take: a list whose
        # items are sometimes strings and sometimes objects is one every
        # reader has to test before it can use.
        "sections": [
            {"heading": str(s.get("heading") or "").strip()[:120],
             "body": str(s.get("body") or "").strip()}
            for s in (raw.get("sections") or [])[:6]
            if isinstance(s, dict) and str(s.get("body") or "").strip()
        ],
        # The strip of linked images across their front page -- five on
        # Alsbou's, each going somewhere different (inventory, financing,
        # trade-in, contact, their sister store). These are NOT hero slides
        # and rotating them as one was wrong: a hero is one picture of the
        # forecourt, and these are five separate calls to action that only
        # mean anything next to each other.
        "banners": [
            {"image": _link(b.get("image")),
             "href": _link(b.get("href")),
             "label": str(b.get("label") or "").strip()[:60]}
            for b in (raw.get("banners") or [])[:6]
            if isinstance(b, dict) and _link(b.get("image")) and _link(b.get("href"))
        ],
        # Their front page's body-style pictures -- "Explore Vehicles By Body
        # Style" on Alsbou's -- each naming the `body_style` value it narrows
        # the inventory to. The page draws a tile only where the lot actually
        # holds that style, counted, so a picture of a pickup never leads to an
        # empty grid: same rule the sidebar's By Type group follows.
        "body_style_tiles": [
            {"image": _link(t.get("image")),
             "label": str(t.get("label") or "").strip()[:40],
             "style": str(t.get("style") or "").strip().lower()[:40]}
            for t in (raw.get("body_style_tiles") or [])[:8]
            if isinstance(t, dict) and _link(t.get("image")) and str(t.get("style") or "").strip()
        ],
        # Their full-width promotional bands -- a financing banner, a trade-in
        # banner -- each an image that is a link, drawn between the front
        # page's sections in the order given here. Same shape as a banner tile
        # and a separate key because it is a different thing on the page: a
        # strip is a row of five, a promo runs the whole width on its own.
        "promos": [
            {"image": _link(b.get("image")),
             "href": _link(b.get("href")),
             "label": str(b.get("label") or "").strip()[:60]}
            for b in (raw.get("promos") or [])[:4]
            if isinstance(b, dict) and _link(b.get("image")) and _link(b.get("href"))
        ],
        "links": _links(raw.get("links"), 8),
        # The one link their nav draws as a button rather than as text --
        # "GET PRE-QUALIFIED" on Alsbou's, in the accent. It is a link like the
        # others and goes through the same validation; what it buys is that the
        # page does not have to guess which of eight nav items they emphasise,
        # and a profile that names none simply gets no button.
        "cta": (_links([raw.get("cta")], 1) or [None])[0],
        # What they call the number on a card: "Advertised price" at Alsbou.
        # Their word rather than ours, because a dealer reading their own
        # storefront notices the label before they notice the layout -- and a
        # profile that states none gets no label, not a guessed one.
        "price_label": str(raw.get("price_label") or "").strip()[:40],
        # What that number already includes, in the dealer's own words. Alsbou
        # headline a total and disclose the four fees inside it, and which of
        # them applies varies per car -- their four electric vehicles pay no
        # smog fee. So this is a sentence they wrote, shown beside the two
        # figures the export actually states, and nothing here subtracts one
        # number from another to arrive at a third.
        "price_note": str(raw.get("price_note") or "").strip()[:240],
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
    # A file in the repository, imported by the seed. It is the answer when a
    # crawl cannot run -- a dealer whose site refuses us, or a network that
    # cannot reach it -- and it goes through the same CSV importer a dealer
    # would upload to, so nothing here is a private path.
    fixture = str(raw.get("fixture_csv") or "").strip()
    if url:
        return {"source_url": url, "dealer_id": dealer_id,
                "fixture_csv": fixture, "origin": "profile"}
    return {
        "source_url": settings.scraper_base_url.strip(),
        "dealer_id": settings.scraper_dealer_id.strip(),
        "fixture_csv": fixture,
        "origin": "env" if settings.scraper_base_url.strip() else "none",
    }


#: The invented showroom's own invented domain. `.example` is reserved by RFC
#: 2606 like `.invalid`, so mail to it can never leave the building -- but it
#: reads as a dealership's address rather than as a placeholder, which is what
#: the login sheet a prospect is shown has to do. The fixture's finance link
#: already lived here.
FIXTURE_DOMAIN = "riversideauto.example"


def staff_domain() -> str:
    """The domain the seeded staff sign in with.

    A dealership's people have addresses at the dealership's own site, so the
    seed builds the fixture roster's addresses from `website_url` -- `dana
    .mercer@craigandlandrethcars.com` on Craig's instance, not `@example
    .invalid`, which is what a real manager reads as a test account. A profile
    with its own `staff:` list never reaches this: those addresses are typed.

    Only the host, with `www.` dropped. A profile with no website -- the
    fixture is the one -- gets the fixture's own invented domain.
    """
    raw = str(_top("website_url") or "").strip()
    host = re.sub(r"^https?://", "", raw).split("/")[0].strip().lower()
    host = re.sub(r"^www\.", "", host)
    return host if "." in host else FIXTURE_DOMAIN


#: What a mailbox local part may look like. It lands in a From header and in
#: the Worker's recipient list, so it is validated to what an address takes.
_MAILBOX_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,63}$")


def mailbox() -> str:
    """The dealership's own mailbox on the shared sending domain -- the local
    part only. `alsboucars` for `alsboucars@linerai.us`.

    From the profile's `mailbox:` when it says, else the host of their
    `website_url` with `www.` and the last label dropped: `alsboucars.com`
    becomes `alsboucars`. A dealership can be given a different name
    (`craigsbestcars` for craigandlandrethcars.com) by writing it in. A
    profile with neither -- the fixture -- gets "", and the deployment's
    `SENDING_FROM` / `sales@` stands in, exactly as before.
    """
    stated = str(_top("mailbox") or "").strip().lower()
    if stated:
        return stated if _MAILBOX_RE.match(stated) else ""
    host = staff_domain()
    if host == FIXTURE_DOMAIN:
        return ""
    local = host.rsplit(".", 1)[0] if "." in host else host
    local = local.replace(".", "-")
    return local if _MAILBOX_RE.match(local) else ""


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
    surface = "dark" if str(raw.get("surface") or "").strip().lower() == "dark" else "light"
    chrome = str(raw.get("chrome") or "").strip().lower()
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
        "surface": surface,
        # The header, the contact strip above it and the footer, which a lot of
        # dealers run dark over a white page -- Alsbou's are black (#000000)
        # with a dark grey strip, and the body between them is white. That is
        # not `surface: dark`, which would produce a storefront that looks
        # nothing like theirs, and it is not `light` either: the chrome was the
        # first thing anybody named about their site.
        #
        # Two words rather than two colours, for the reason `surface` is two
        # words: it picks the palette already in the token layer instead of
        # carrying #000000 into a stylesheet, so a prospect's file cannot
        # restyle the product into something unreadable. It follows `surface`
        # by default, so a wholly dark site needs one key rather than two that
        # can disagree.
        "chrome": chrome if chrome in ("light", "dark") else surface,
    }


def assistant() -> dict:
    """What this dealership wants of the assistant beyond `AssistantSettings`.

    One key so far. `sales_method` puts the operator's full 21KB method back in
    front of every prompt; it is **off by default**, because a model handed two
    thirds of a script answers like one -- long, staged and reluctant to just
    say what a car costs. `agent/prompts.BRIEF` replaced it.

    The file is kept rather than deleted and this is what keeps it reachable:
    an archive nobody can switch on is a dead file, and it is the operator's
    document rather than ours to throw away. It lives in the profile because it
    is a fact about a dealership -- one of them may genuinely want the method,
    and switching dealership must stay one line.
    """
    raw = _section("assistant")
    return {"sales_method": bool(raw.get("sales_method"))}
