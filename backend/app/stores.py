"""Which dealership a request is for, decided from its URL.

`/alsbou/app`, `/alsbou/api/overview`, `/alsbou/showroom` -- the first path
segment names a store, and everything after it is an ordinary path this app
already knows how to serve.

**The prefix is stripped before routing rather than added to every route.**
That is the whole reason this is one small module instead of a change to every
router in `api/`: the middleware sets the active store, rewrites
`scope["path"]` to drop the segment, and `/alsbou/api/overview` arrives at the
existing `/api/overview` handler with `current_store` already set. The SPA
catch-all, the `/assets` mount and the WebSocket routes all come along for
free, because none of them ever sees the prefix.

**Unprefixed still works and still means the configured store.** An instance
with `DEALERSHIP=alsbou` and nothing else serves `/api/overview` exactly as it
did before this existed, which is what keeps every deployment, every script
and all of `make smoke` working unchanged. The prefix is additive.

`root_path` is set as well as `path`, so anything asking the framework to
build a URL for a route gets the prefix back rather than an address that
drops the store.
"""

from __future__ import annotations

import json
import logging

from app.config import settings
from app.db import current_store, has_database

log = logging.getLogger("liner.stores")

#: Path segments that can never be a store, whatever somebody names a profile
#: file. `api` and `ws` are this app's own; `assets` and `r` belong to the
#: built frontend and the outreach click hop; `ops` is Liner's own dashboard
#: and is deliberately *not* per-store.
RESERVED = frozenset({"api", "ws", "r", "assets", "ops", "s", "static"})


def known_stores() -> list[str]:
    """Every store this deployment can serve, minus anything reserved.

    Read off the profile directory on each call rather than captured at
    import, so adding a profile does not need a restart to become routable --
    the same reason `profile._section` re-reads the file.
    """
    return [s for s in settings.store_slugs if s not in RESERVED]


def split(path: str) -> tuple[str, str]:
    """`/alsbou/api/x` -> `("alsbou", "/api/x")`. Unknown prefix -> `("", path)`.

    A bare `/alsbou` becomes `("alsbou", "/")`, so the store's root is the
    app's root and the SPA picks it up from there.
    """
    if not path.startswith("/"):
        return "", path
    head, _, rest = path[1:].partition("/")
    if not head or head in RESERVED or head not in known_stores():
        return "", path
    return head, "/" + rest


async def _not_seeded(scope, receive, send, slug: str) -> None:  # noqa: ANN001
    """503 with the fix in it, for a store whose database does not exist yet."""
    detail = (
        f"The dealership '{slug}' has not been set up on this host yet. "
        f"Run: DEALERSHIP={slug} make reset-db  (then restart), and check `make stores`."
    )
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1013})
        return
    body = json.dumps({"detail": detail, "store": slug, "seeded": False}).encode()
    await send({
        "type": "http.response.start",
        "status": 503,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


class StorePrefix:
    """Sets the active store from the URL, then hands on the stripped path."""

    def __init__(self, app) -> None:  # noqa: ANN001 - ASGI app
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope.get("type") not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        slug, rest = split(scope.get("path", "/"))
        if not slug:
            return await self.app(scope, receive, send)

        # **A store with no database is refused before anything opens it.**
        # Connecting to SQLite creates the file, so the first request to
        # `/alsbou/api/...` on a host where Alsbou was never seeded minted an
        # empty `alsbou.db`, then failed with `no such table: dealership` --
        # a 500 the page rendered as "Loading the lot" for ever, over a header
        # with no name in it, and a 4 KB file that afterwards made `make
        # stores` show a dealership that was not there. The same rule
        # `has_database` already enforces for the login walk and `/ops`,
        # arriving through the storefront.
        #
        # Only the API and the socket are refused. The SPA document still
        # serves, so the page can render the reason rather than a blank --
        # serving `index.html` opens no database. The answer names the
        # command, because the person reading it is the one who has to run it.
        if not has_database(slug) and (rest.startswith("/api/") or rest.startswith("/ws/")):
            return await _not_seeded(scope, receive, send, slug)

        scope = dict(scope)
        scope["path"] = rest
        # Starlette routes on `path`, but `raw_path` is what some servers and
        # any downstream middleware read. Leaving it pointing at the original
        # is how two parts of one request start disagreeing about where it was
        # addressed.
        raw = scope.get("raw_path")
        if raw:
            prefix = f"/{slug}".encode()
            if raw.startswith(prefix):
                scope["raw_path"] = raw[len(prefix):] or b"/"
        scope["root_path"] = scope.get("root_path", "") + f"/{slug}"

        token = current_store.set(slug)
        try:
            await self.app(scope, receive, send)
        finally:
            # Reset rather than leave it: this runs in the request's own
            # context, and a worker thread that picked the value up from a
            # previous request would read the wrong dealership's database --
            # which is exactly the failure the file split exists to prevent,
            # reintroduced one layer up.
            current_store.reset(token)
