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

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

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
SPA_PREFIXES = ("/chat", "/call", "/login", "/app", "/ops", "/showroom")

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
    def root() -> FileResponse:
        # Byte-for-byte the supplied marketing page, as at /
        return FileResponse(landing)

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.split("/")[0] in RESERVED:
            raise HTTPException(404, "Not found")

        # A real file in dist wins: favicons, images, robots.txt, the sample
        # ADF -- anything Vite copied from public/.
        candidate = (DIST / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(DIST.resolve()):
            return FileResponse(candidate)

        if any(("/" + full_path).startswith(prefix) for prefix in SPA_PREFIXES):
            return FileResponse(index)
        raise HTTPException(404, "Not found")

    return True
