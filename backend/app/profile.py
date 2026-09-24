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


#: A bare origin -- scheme, host, optional port, and nothing after it. A path
#: is not part of an origin and a browser ignores one, so accepting it here
#: would let a profile promise a narrowing that does not exist.
_ORIGIN_RE = re.compile(r"^https://[a-z0-9.-]+(:\d{1,5})?$")


def embed_origins() -> list[str]:
    """The sites allowed to put this dealership's assistant in an iframe.

    **This is a browser control, not a security boundary, and the difference
    matters.** It becomes `Content-Security-Policy: frame-ancestors`, which a
    browser honours when deciding whether to render our page inside somebody
    else's -- so it stops another dealer, or a phishing page, standing this
    assistant up as their own. It does nothing whatsoever about a script
    posting straight to `/chat/sessions`: nothing is framed there, and no
    origin is sent. That case is what the ceilings in `ratelimit.py` are for,
    and the two are not substitutes.

    Empty means the profile has not said, which leaves `'self'` -- our own
    storefront still frames its own widget, and nobody else can. Opening it
    up is a deliberate line in a profile rather than the default, because the
    default is the one that gets shipped by accident.

    Validated hard for the reason the accent is validated to a hex: this comes
    from a file an operator edits and it lands in a security header, where a
    stray space or a `*` would widen it silently.
    """
    out: list[str] = []
    for item in (_section_list("embed_origins") or [])[:12]:
        origin = str(item or "").strip().lower().rstrip("/")
        if _ORIGIN_RE.match(origin) and origin not in out:
            out.append(origin)
    return out


#: How the website chat sits on a dealer's own page. Each is a choice between
#: a few safe values rather than a free style, because every one of them lands
#: in a stylesheet on somebody else's website.
WIDGET_SIDES = ("right", "left")

#: What the Tag Manager events are called: the car industry's own GA4 names
#: (`asc_comm_submission` and friends, which an agency already counts), ours
#: (`liner_lead`), or both for a container being moved from one to the other.
WIDGET_EVENTS = ("asc", "liner", "both")


def widget() -> dict:
    """The website chat's settings, as this dealership's profile states them.

    Served to the loader on every page load rather than written into the tag,
    which is the point of having a loader: a dealer's website provider pastes
    one line once, and the label, the side, the offset and whether lead events
    go to their Google Tag Manager can all change here without anybody editing
    their site again.

    The colour is the brand's accent, not a second setting -- two colours for
    one dealership are two things that can disagree.
    """
    raw = _section("widget") or {}
    label = str(raw.get("label") or "Chat with us").strip()[:40] or "Chat with us"
    title = str(raw.get("title") or "").strip()[:60]
    side = str(raw.get("side") or "right").strip().lower()
    events = str(raw.get("events") or "asc").strip().lower()
    try:
        offset = int(raw.get("offset", 20))
    except (TypeError, ValueError):
        offset = 20
    b = brand()
    return {
        "label": label,
        "title": title,
        "side": side if side in WIDGET_SIDES else "right",
        # Clamped: it is a number of pixels from the corner, and a typo of
        # 2000 would put the bubble off the screen.
        "offset": max(8, min(offset, 120)),
        "accent": b["accent"],
        "accent_ink": b["accent_ink"],
        # On unless the profile says otherwise: a dealer who installed the tag
        # through Tag Manager expects the events, and they carry nothing
        # personal (see docs/WIDGET.md). `gtm: false` stops the pushes.
        "gtm": raw.get("gtm", True) is not False,
        "events": events if events in WIDGET_EVENTS else "asc",
    }


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


#: A lot's key: the profile's own name for it, which goes in a link
#: (`?location=clarksville`) and is the one thing about its row that never
#: changes -- visits point at the row, so renaming a lot is an update.
_LOCATION_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _hours(value) -> tuple[dict, str]:
    """Opening hours in the shape `hours:` has, or ({}, why) when they are not.

    Every day named, each either null (closed) or an open and a close time.
    A lot whose hours are half-written is one whose hours are not known:
    offering slots from a guess is how a buyer arrives at a locked door.
    """
    if not value:
        return {}, ""
    if not isinstance(value, dict):
        return {}, "hours must be one entry per day"
    out: dict = {}
    for day in DAYS:
        window = value.get(day)
        if window is None:
            out[day] = None
            continue
        if not isinstance(window, dict):
            return {}, f"hours.{day} must be null or open/close"
        opens, closes = str(window.get("open") or ""), str(window.get("close") or "")
        if not (_CLOCK.match(opens) and _CLOCK.match(closes)) or opens >= closes:
            return {}, f"hours.{day} needs an open time before a close time, as HH:MM"
        out[day] = {"open": opens, "close": closes}
    extra = sorted(set(value) - set(DAYS))
    if extra:
        return {}, f"hours has days that do not exist: {', '.join(extra)}"
    if not any(out.values()):
        return {}, "hours closes every day"
    return out, ""


def location_problems(raw: dict) -> list[str]:
    """What is wrong with a profile's `locations:`, in words; [] when nothing.

    The seed refuses on any of these rather than seeding around them. What is
    *not* a problem is a lot with no street address or no hours: its cars are
    still real and still there, and a visit is booked at the primary until
    somebody writes them in -- the seed says so, and nothing is invented.
    """
    listed = raw.get("locations")
    if listed is None:
        return []
    if not isinstance(listed, list) or not listed:
        return ["locations must be a list with at least one lot"]
    problems: list[str] = []
    seen: set[str] = set()
    primaries = 0
    for n, entry in enumerate(listed, 1):
        where = f"locations[{n}]"
        if not isinstance(entry, dict):
            problems.append(f"{where} is not a mapping")
            continue
        key = str(entry.get("key") or "").strip()
        if not _LOCATION_KEY.match(key):
            problems.append(f"{where}.key must be lowercase letters, digits and dashes")
        elif key in seen:
            problems.append(f"{where}.key {key!r} is listed twice")
        seen.add(key)
        if not str(entry.get("name") or "").strip():
            problems.append(f"{where}.name is missing")
        match = entry.get("match", [])
        if not isinstance(match, list) or not all(isinstance(m, (str, int)) for m in match):
            problems.append(f"{where}.match must be a list of the names and store ids their feed uses")
        if entry.get("primary"):
            primaries += 1
            # One fact, one place. The primary is the address, phone and hours
            # at the top of the file; restated here, the two drift apart and
            # nothing says which one a buyer was given.
            restated = [f for f in ("address", "phone", "hours") if entry.get(f)]
            if restated:
                problems.append(
                    f"{where} is the primary, whose {', '.join(restated)} are the ones at the "
                    "top of the file -- remove them here"
                )
        else:
            _, why = _hours(entry.get("hours"))
            if why:
                problems.append(f"{where}.{why}")
    if primaries != 1:
        problems.append(
            f"exactly one lot must say `primary: true` -- the one at the address at the top "
            f"of the file (found {primaries})"
        )
    return problems


def locations() -> list[dict]:
    """The group's lots as this profile lists them, in order; [] for none.

    Most dealerships have one address and list nothing, and `app/locations.py`
    makes that address the only lot. Each entry is `key`, `name`, `address`,
    `phone`, `hours` (a dict, empty when not stated), `match` (lowercased) and
    `primary`. Read per call like every other section. A profile the seed
    would refuse (`location_problems`) reads as listing nothing, rather than
    as a half-understood list: one lot is a smaller error than a wrong one.
    """
    path = _path()
    if not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return []
    if not raw.get("locations") or location_problems(raw):
        return []
    out = []
    for entry in raw["locations"]:
        primary = bool(entry.get("primary"))
        hours, _ = ({}, "") if primary else _hours(entry.get("hours"))
        out.append({
            "key": str(entry["key"]).strip(),
            "name": str(entry["name"]).strip(),
            "address": "" if primary else str(entry.get("address") or "").strip(),
            "phone": "" if primary else str(entry.get("phone") or "").strip(),
            "hours": hours,
            "match": sorted({str(m).strip().lower() for m in entry.get("match") or [] if str(m).strip()}),
            "primary": primary,
        })
    return out


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
    .mercer@craigandlandrethcars.com` on Craig's instance. They used to be
    `@example.invalid`, which is what a real manager reads as a test account.
    A profile with its own `staff:` list never reaches this: those addresses
    are typed.

    Only the host, with `www.` dropped. A profile with no website -- the
    fixture is the one -- gets the fixture's own invented domain.
    """
    raw = str(_top("website_url") or "").strip()
    host = re.sub(r"^https?://", "", raw).split("/")[0].strip().lower()
    host = re.sub(r"^www\.", "", host)
    return host if "." in host else FIXTURE_DOMAIN


#: What a mailbox local part may look like. It lands in a From header and in
#: the Worker's recipient list, so it is validated to what an address takes.
#: No `+`: `reply+<token>@` is read off the envelope before any mailbox is,
#: so a mailbox with a plus in it could never be routed to.
_MAILBOX_RE = re.compile(r"^(?!reply$)[a-z0-9][a-z0-9._-]{0,63}$")


def mailbox() -> str:
    """The dealership's own mailbox on the shared sending domain -- the local
    part only. `alsbou` for `alsbou@linerai.us`.

    From the profile's `mailbox:` when it says, else the host of their
    `website_url` with `www.` and the last label dropped: `alsboucars.com`
    becomes `alsboucars`. A dealership can be given a shorter or different
    name by writing it in -- Alsbou's profile says `mailbox: alsbou`, which
    is what their entry in the Worker's recipient list carries, and **those
    two have to be the same string or the mail is dropped in Cloudflare with
    no receipt anywhere.** A profile with neither -- the fixture -- gets "",
    and the deployment's `SENDING_FROM` / `sales@` stands in, exactly as
    before.
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
