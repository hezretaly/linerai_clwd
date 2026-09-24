"""The website chat's side of the dealer's own site.

A dealership's website provider pastes one tag into their template:

    <script src="https://linerai.us/embed.js" data-dealer="alsbou" async></script>

That loader (`frontend/public/embed.js`) draws the bubble, and on the buyer's
first click frames the real chat from `/widget/<dealer>`. Everything the loader
needs to know about the dealership it asks for here, on every page load --
which is what lets the label, the side, the Tag Manager events and whether the
bubble shows at all change without anybody editing the dealer's site again.

**Who may run it is decided by the browser, not by this file.** The frame is
served with `Content-Security-Policy: frame-ancestors` listing only the
dealership's own origins (`profile.embed_origins`), so a copy of the tag on
somebody else's page gets a frame the browser refuses to draw. What this file
adds is the *answer* to "why is there no bubble": the loader asks, the verdict
comes back in words, and it is printed in the console of the page that asked.
The config itself is public -- it is the dealership's name and colour -- so it
is readable from any origin rather than failing as an opaque network error.

**It is a browser control, not a security boundary**, the same line
`embed_origins` has always drawn: a script can post to `/api/chat/sessions`
without framing anything and without sending an honest `Origin`. The chat's
own ceilings (`ratelimit.py`) are what stand in front of the bill.
"""

from __future__ import annotations

import json
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import flags, profile
from app.api.deps import current_user, require_manager
from app.db import active_store, get_db, utcnow
from app.events import emit
from app.models import Dealership, User, WidgetInstall
from app.page_context import clean_url, own_origins
from app.stores import public_origin
from app.schemas.serialize import stamp

router = APIRouter(prefix="/widget", tags=["widget"])

#: The loader this backend was written against. The loader sends its own;
#: a report from an older one says so on the setup page, which is how a site
#: still carrying a cached copy is noticed.
LOADER_VERSION = "2"

#: How long the loader may keep a config before asking again. Short: turning
#: the bubble off is the change somebody wants to see now.
CONFIG_MAX_AGE = 60

#: A name from the loader's fingerprint table. Anything else in a report is
#: dropped rather than stored -- the report comes from a browser.
_WIDGET_NAME = re.compile(r"^[A-Za-z0-9 .!()'&+-]{1,40}$")

#: One write per origin per this many seconds, however many page views a busy
#: site sends. The row says "seen live, and when", which a write a minute
#: answers as well as a write a view.
REPORT_EVERY_S = 60
_last_report: dict[tuple[str, str], tuple[float, str]] = {}


def _self_origins(request: Request) -> set[str]:
    """This deployment's own pages: the origin the request was addressed to,
    the public base URL, and the frontend origins it already lets call it.

    All three, because each is "us" from a different side. Behind a proxy the
    address this process was reached by is not the one in the browser's bar;
    in development Vite forwards to this process and rewrites the host on the
    way. The tag on one of our own pages -- the storefront, a rehearsal -- is
    allowed wherever it is served from.
    """
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    out = {f"{proto}://{host}".lower()} if host else set()
    out.update(own_origins())
    return out


def _caller_origin(request: Request, hint: str = "") -> str:
    """Whose page asked. The `Origin` header when the browser sent one.

    A same-origin GET carries no `Origin` at all, so the loader also sends its
    page's origin as `?origin=`. That is only a hint and only ever used to
    *explain* -- the verdict it produces decides whether a bubble is drawn on
    that page, and nothing a caller could gain by lying about it is more than
    it could get by ignoring the answer.
    """
    header = (request.headers.get("origin") or "").strip().lower()
    if header and header != "null":
        return header
    return (hint or "").strip().lower().rstrip("/")


def verdict(request: Request, hint: str = "") -> tuple[bool, str, str]:
    """(allowed, origin, reason) for the page that is asking."""
    origin = _caller_origin(request, hint)
    origins = profile.embed_origins()
    if origin and origin in _self_origins(request):
        return True, origin, ""
    if origin and origin in origins:
        return True, origin, ""
    if not origins:
        return False, origin, (
            "This dealership has no website listed yet, so the chat cannot be shown "
            "on any site. Liner adds the site's address to the dealership's profile "
            "(embed_origins) -- ask them to add " + (origin or "your site") + "."
        )
    return False, origin, (
        f"{origin or 'This page'} is not one of this dealership's websites "
        f"({', '.join(origins)}). If it should be -- a second domain, the www and "
        "bare versions, a staging copy -- ask Liner to add it to the dealership's "
        "embed_origins."
    )


def _cors(response: Response) -> None:
    """Readable from any page, whichever page that is.

    The body is public -- a name and a colour -- and a refusal that arrives as
    an opaque CORS failure is one nobody on the dealer's side can read, which
    is the whole reason the verdict exists.

    **`*`, not the asking page's origin echoed back.** A tag written for an
    older address -- `linerai.us/<dealer>/embed.js`, before the group moved to
    its own subdomain -- reaches this through a redirect across origins, and
    after one the browser sends `Origin: null`. An echo of the dealer's origin
    no longer matches that, so the one request that says why there is no
    bubble failed as an opaque network error: found by driving an old tag
    through the shipped redirect in a browser. The loader asks without
    credentials, which is the case `*` is allowed for. The body still depends
    on who asked, hence `Vary`.
    """
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Vary"] = "Origin"


@router.get("/config")
def config(
    request: Request,
    response: Response,
    origin: str = "",
    db: Session = Depends(get_db),
) -> dict:
    """What the loader on a dealer's page needs, asked on every page load."""
    allowed, caller, reason = verdict(request, origin)
    _cors(response)
    response.headers["Cache-Control"] = f"public, max-age={CONFIG_MAX_AGE}"

    dealership = db.query(Dealership).first()
    switched = flags.get(db, "website_chat")
    settings_block = profile.widget()
    slug = active_store()
    enabled = switched == "on"
    return {
        "dealer": slug,
        "name": dealership.name if dealership else "",
        "enabled": enabled,
        # Said separately from `allowed`, because the two have different
        # fixes: a switch on the Liner setup page, or a line in the profile.
        "switched_off": not enabled,
        "allowed": allowed,
        "origin": caller,
        "reason": reason if not allowed else (
            "" if enabled else
            "The chat is switched off for this dealership on the Liner setup page."
        ),
        # The frame's address, relative to wherever the loader came from --
        # the loader knows its own origin and this host may be reached by
        # more than one name.
        "frame": f"/widget/{slug}",
        # ...unless this dealership is served from somewhere else now: its own
        # subdomain, or the one public address. A tag written for an older
        # address reaches this through a redirect and still reads its own
        # origin as the old one; the loader moves the frame, and the pin on
        # every message, here. Empty when this deployment names no address.
        "frame_origin": public_origin(slug),
        "launcher": {
            "label": settings_block["label"],
            "title": settings_block["title"] or (dealership.name if dealership else ""),
            "side": settings_block["side"],
            "offset": settings_block["offset"],
            "accent": settings_block["accent"],
            "accent_ink": settings_block["accent_ink"],
        },
        "gtm": settings_block["gtm"],
        "events": settings_block["events"],
        # Public already: the frame's CSP header states the same list.
        "origins": profile.embed_origins(),
        "version": LOADER_VERSION,
    }


def _names(raw) -> list[str]:
    out: list[str] = []
    for item in (raw or [])[:20] if isinstance(raw, list) else []:
        name = str(item or "").strip()
        if _WIDGET_NAME.match(name) and name not in out:
            out.append(name)
    return out


@router.post("/install-report", status_code=204)
async def install_report(request: Request, db: Session = Depends(get_db)) -> Response:
    """The loader saying it is running on a page, and what else is there.

    Sent with `navigator.sendBeacon`, so the body arrives as text rather than
    JSON (a beacon cannot set a content type that avoids a preflight) and
    nobody reads the answer. Only accepted from one of the dealership's own
    origins, which is what keeps the table bounded: a stranger's page gets a
    403 and writes nothing.
    """
    origin = (request.headers.get("origin") or "").strip().lower()
    # Our own pages are not an install on anybody's site: nothing to record,
    # and nothing wrong either.
    if origin and origin in own_origins():
        return Response(status_code=204)
    if not origin or origin not in profile.embed_origins():
        raise HTTPException(403, "Not one of this dealership's websites.")
    try:
        body = json.loads((await request.body())[:8192] or b"{}")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Expected a JSON report.") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected a JSON report.")

    others = _names(body.get("others"))
    duplicate = bool(body.get("duplicate"))
    gtm = bool(body.get("gtm"))
    version = str(body.get("version") or "")[:20]
    page = clean_url(body.get("page"), profile.embed_origins())
    signature = json.dumps([others, duplicate, gtm, version])

    # Throttled per origin, unless something worth knowing changed: a second
    # chat widget appearing is news now, not in a minute.
    key = (active_store(), origin)
    now = time.monotonic()
    last = _last_report.get(key)
    if last and now - last[0] < REPORT_EVERY_S and last[1] == signature:
        return Response(status_code=204)
    _last_report[key] = (now, signature)

    row = db.query(WidgetInstall).filter_by(origin=origin).one_or_none()
    fresh = row is None
    changed = fresh or json.loads(row.other_widgets_json or "[]") != others or (
        row.duplicate_tag != duplicate
    )
    if row is None:
        row = WidgetInstall(origin=origin)
        db.add(row)
    row.last_seen_at = utcnow()
    row.last_page = page or row.last_page or ""
    row.loader_version = version
    row.other_widgets_json = json.dumps(others)
    row.duplicate_tag = duplicate
    row.gtm_present = gtm
    row.reports = (row.reports or 0) + 1
    try:
        db.commit()
    except IntegrityError:
        # Two first reports from one site at once: the other one wrote the
        # row, which is all this was for.
        db.rollback()
        return Response(status_code=204)
    # Only when there is something new to show, so a busy site does not put
    # an event on every dashboard once a minute.
    if changed:
        emit(db, "widget.report", {"origin": origin, "others": others, "duplicate": duplicate})
    return Response(status_code=204)


@router.get("/installs")
def installs(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Everything the Website card on the Liner setup page shows."""
    rows = db.query(WidgetInstall).order_by(WidgetInstall.last_seen_at.desc()).all()
    slug = active_store()
    # The address the tag loads from: the dealership's own subdomain on a
    # group server, the public base URL otherwise. Without either the page
    # uses the address it was opened at, which is the host serving this API.
    base = public_origin()
    return {
        "dealer": slug,
        "switch": flags.get(db, "website_chat"),
        "origins": profile.embed_origins(),
        "settings": profile.widget(),
        "loader": f"{base}/embed.js" if base else "",
        "version": LOADER_VERSION,
        "installs": [
            {
                "origin": r.origin,
                "first_seen_at": stamp(r.first_seen_at),
                "last_seen_at": stamp(r.last_seen_at),
                "last_page": r.last_page,
                "loader_version": r.loader_version,
                "outdated": bool(r.loader_version) and r.loader_version != LOADER_VERSION,
                "others": json.loads(r.other_widgets_json or "[]"),
                "duplicate_tag": r.duplicate_tag,
                "gtm_present": r.gtm_present,
                "reports": r.reports,
            }
            for r in rows
        ],
    }


class SwitchBody(BaseModel):
    value: str
    reason: str = ""


@router.post("/switch")
def switch(
    body: SwitchBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
) -> dict:
    """Show or hide the bubble on every one of the dealership's sites.

    Takes effect on the next page load: the loader asks for its config each
    time, and the config is cached for a minute at most.
    """
    try:
        value = flags.set(db, "website_chat", body.value, reason=body.reason, by=user.id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    emit(db, "widget.report", {"switch": value})
    return {"switch": value}
