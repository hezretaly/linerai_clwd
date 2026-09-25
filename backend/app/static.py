"""Serve the built frontend from the API process.

In development Vite owns :5173 and proxies /api and /ws to :8000. In production
there is no Vite, and the routing rule it enforced -- `/` is the landing
document, everything else is the SPA -- exists nowhere else. Reproducing that
rule in a web-server config is the step most likely to be got wrong, and getting
it wrong is silent: `/` serves the SPA, which renders a blank page because the
catch-all bounces to `/`, which serves the SPA...

So the API serves it instead. One process, one port. Whatever sits in front
(nginx, Caddy, a tunnel) needs a single proxy_pass and nothing else.

This mounts only when a build exists, so `make dev` is untouched.
"""

from __future__ import annotations

import html
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app import profile
from app.db import current_host, current_store

DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

#: The `<meta name>` a document served on a dealership's own subdomain names
#: that store in. Read by `frontend/src/lib/store.ts`; `make smoke` checks
#: both spell it this way.
STORE_META = "liner-store"

# The buyer surfaces, which are the only ones a dealership has any reason to
# put in an iframe on its own website. Everything else -- the dashboard, our
# own ops pages, the login form -- gets `'self'` and nothing more: a dealer
# page has no business being framed anywhere, and a login form inside somebody
# else's page is the classic clickjack.
EMBEDDABLE = ("/chat", "/call", "/widget/")


def _frame_ancestors(path: str) -> str:
    """Who may put this document in an iframe.

    **Framing was allowed here by omission, not by decision**, which is the
    state worth naming: neither shipped nginx config sets `X-Frame-Options` or
    a CSP, so every page of this app could be framed by anyone, and the next
    person to add a security-header block would have broken every dealership's
    embedded assistant with a blank white frame and nothing in any log we own.
    Deciding it in the app settles both halves at once.

    Per store, read from the profile per request like the brand is -- a
    dealership's own domains are a fact about that dealership.
    """
    allowed = ["'self'"]
    if any(path.startswith(prefix) for prefix in EMBEDDABLE):
        allowed += profile.embed_origins()
    return "frame-ancestors " + " ".join(allowed)


def _framed(response: Response, path: str) -> Response:
    """Set it on a document. Only `frame-ancestors`, deliberately: a full CSP
    over this SPA is a real piece of work (hashes or a nonce for every inline
    style Vite emits) and shipping a broad one now -- `default-src *` with a
    frame rule bolted on -- would read like a policy while being none."""
    response.headers["Content-Security-Policy"] = _frame_ancestors(path)
    return response


def _document(index: Path, path: str) -> Response:
    """The SPA's document -- naming its store when the *host* chose it.

    The page read its store off the path, and on `alsbou.linerai.us/app`
    there is none to read: it thought it was nobody's while `/api/auth/me`
    said Alsbou, and RequireAuth reloaded /app for ever (911 `/me` in fifteen
    minutes on the real host); the store's front page drew the default design.
    Only the server knows which hosts are stores, so the document says.

    Only there: `current_host` is set by `StorePrefix._on_host` and nowhere
    else, so the shared host, a path-named store and the demo (no
    STORE_DOMAIN) get the built file untouched, byte for byte. A `<meta>`
    rather than an inline script: data, not code, so a future `script-src`
    has nothing to hash. Read per request like the file it replaces, so a
    `make build` under a running process is picked up; no ETag, because these
    bytes are not the file's.
    """
    host = current_host.get()
    if not host:
        return _framed(FileResponse(index), path)
    tag = f'<meta name="{STORE_META}" content="{html.escape(host, quote=True)}" />'
    body = index.read_bytes().replace(b"</head>", tag.encode() + b"</head>", 1)
    return _framed(Response(body, media_type="text/html"), path)

# Paths the SPA owns. Anything else that is not a real file is a 404, rather
# than index.html -- a mistyped API path should say so, not return a page.
#
# **This list has to match the top-level routes in `frontend/src/main.tsx`,
# and nothing in development will tell you when it does not.** Vite's history
# fallback serves index.html for any path at all, so every browser check here
# -- `make ops-ui` included -- passes against :5173 whatever this says. Only
# the built bundle enforces it, and only in production. `/ops` shipped missing
# from here: the whole dashboard answered `{"detail":"Not found"}` on a real
# host while every gate was green. `make smoke` now reads main.tsx and fails
# on a route that is not listed.
SPA_PREFIXES = ("/chat", "/call", "/login", "/app", "/ops", "/showroom", "/widget")

#: How long a browser or Cloudflare may keep the loader before asking again.
#: It is the one file on somebody else's website, pasted once and never
#: edited, so a fix to it reaches every dealer only as fast as this lets it --
#: five minutes, which is short enough to roll a fix out in a coffee break and
#: long enough that a busy site is not asking on every page view.
LOADER_MAX_AGE = 300

# Never let a request walk out of dist/ via the catch-all.
# /r is the outreach click hop -- a real route, not an SPA path.
RESERVED = ("api", "ws", "r")


def mount_frontend(app: FastAPI) -> bool:
    """Returns False (and changes nothing) when the frontend is not built."""
    index = DIST / "index.html"
    landing = DIST / "landing.html"
    if not index.is_file() or not landing.is_file():
        return False

    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def root() -> Response:
        # **A store's root is the store's page, and only the bare root is
        # ours.** `StorePrefix` strips `/alsbou` to `/` before this runs, and
        # this handler served `landing.html` for `/` unconditionally -- so
        # `linerai.us/alsbou` answered with Liner's own marketing page under
        # Alsbou's URL, on the link a prospect is sent. The prefix survives in
        # `current_store`, which is what the middleware exists to set, and
        # that is the only thing here that can tell the two apart.
        #
        # Unprefixed `/` stays the marketing document byte for byte, which
        # `make smoke` asserts; the prefixed one is the SPA, whose `/` route
        # is the dealership's front page.
        if current_store.get():
            return _document(index, "/")
        return _framed(FileResponse(landing), "/")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> Response:
        if full_path.split("/")[0] in RESERVED:
            raise HTTPException(404, "Not found")

        # A real file in dist wins: favicons, images, robots.txt, the sample
        # ADF -- anything Vite copied from public/.
        candidate = (DIST / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(DIST.resolve()):
            if candidate.name == "embed.js":
                return FileResponse(candidate, headers={
                    "Cache-Control": f"public, max-age={LOADER_MAX_AGE}",
                })
            return FileResponse(candidate)

        # `/widget/<dealer>` for a dealer this host does not serve is a 404,
        # not the SPA: the page would render a chat for nobody, framed on
        # whichever site asked. `StorePrefix` sets the store only for a
        # dealer it knows.
        if full_path.startswith("widget/") and not current_store.get():
            raise HTTPException(404, "No such dealership")

        if any(("/" + full_path).startswith(prefix) for prefix in SPA_PREFIXES):
            return _document(index, "/" + full_path)
        raise HTTPException(404, "Not found")

    return True
