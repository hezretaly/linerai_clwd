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

import logging

from app.config import settings
from app.db import current_store

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
