"""Which dealership a request is for, decided from its URL -- its path, or,
on a server with `STORE_DOMAIN` set, its host (`alsbou.linerai.us`).

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

from urllib.parse import urlsplit

from app.config import settings
from app.db import current_host, current_store, has_database

log = logging.getLogger("liner.stores")

#: Path segments that can never be a store, whatever somebody names a profile
#: file. `api` and `ws` are this app's own; `assets` and `r` belong to the
#: built frontend and the outreach click hop; `ops` is Liner's own dashboard
#: and is deliberately *not* per-store.
RESERVED = frozenset({"api", "ws", "r", "assets", "ops", "s", "static", "widget"})


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


def widget_store(path: str) -> str:
    """`/widget/alsbou` -> `alsbou`: the dealership a website chat frame is for.

    The website chat is served at `/widget/<dealer>` rather than under the
    usual `/<dealer>/` prefix, because it is the one address that goes into a
    tag on somebody else's website and it reads as what it is. The path is
    left exactly as it came -- the page reads the dealer back off it -- and
    only the active store is set, so that whatever serves the document can ask
    that dealership's profile who may frame it.
    """
    if not path.startswith("/widget/"):
        return ""
    slug = path.split("/")[2] if path.count("/") >= 2 else ""
    return slug if slug and slug not in RESERVED and slug in known_stores() else ""


#: Subdomains of `STORE_DOMAIN` that are never a dealership: the marketing
#: site's usual alias. The bare domain is not a subdomain at all.
NOT_A_STORE = frozenset({"www"})


def host_store(scope) -> str | None:  # noqa: ANN001 - ASGI scope
    """`alsbou.linerai.us` -> `"alsbou"`: the dealership this host names.

    None when the host names none -- host routing is off, or it is the bare
    domain, `www.` or some other host entirely -- and the path decides as it
    always has. `""` for a subdomain of ours that is not a dealership this
    process serves: a typo'd subdomain is answered as nobody's rather than
    falling through to the default store, which would show one dealership's
    storefront under a name that looks like another's.
    """
    domain = settings.store_domain.strip().lower().strip(".")
    if not domain:
        return None
    raw = ""
    for name, value in scope.get("headers") or []:
        if name == b"host":
            raw = value.decode("latin-1")
            break
    host = raw.split(":", 1)[0].strip().lower().rstrip(".")
    if not host.endswith("." + domain):
        return None
    label = host[: -len(domain) - 1]
    if not label or label in NOT_A_STORE:
        return None
    if "." in label or label in RESERVED or label not in known_stores():
        return ""
    return label


def _scheme() -> str:
    return urlsplit(settings.public_base_url).scheme or "https"


def public_origin(slug: str | None = None) -> str:
    """Where `slug`'s pages are reached from outside, as an origin, or "".

    Its own subdomain where the deployment serves one per group; otherwise the
    public base URL, which serves every store under a path. "" when neither is
    set -- the caller falls back to the address the request arrived at.
    """
    slug = current_store.get() if slug is None else slug
    if settings.store_domain.strip() and slug:
        return f"{_scheme()}://{slug}.{settings.store_domain.strip().lower().strip('.')}"
    parts = urlsplit(settings.public_base_url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def public_link(path: str, slug: str | None = None) -> str:
    """An absolute link to `path` in `slug`'s store, or "" when this
    deployment does not say how it is reached from outside.

    **A link that leaves the building has to name its store**, and there are
    two ways to: the store's own subdomain, or the path under the shared
    host. `/r/<token>` unprefixed on the shared host is looked up in the
    *default* store's file -- which is how every non-default dealership's
    emailed link was a 404 once -- and on a store's own subdomain a prefix
    would name it twice.
    """
    slug = current_store.get() if slug is None else slug
    if settings.store_domain.strip() and slug:
        return public_origin(slug) + path
    base = settings.public_base_url.rstrip("/")
    if not base:
        return ""
    return f"{base}/{slug}{path}" if slug else f"{base}{path}"


async def _elsewhere(scope, receive, send, detail: str) -> None:  # noqa: ANN001
    """404 for a request its host and its path cannot both be right about."""
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 4404})
        return
    body = json.dumps({"detail": detail}).encode()
    await send({
        "type": "http.response.start",
        "status": 404,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


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

        host = host_store(scope)
        if host == "":
            return await _elsewhere(scope, receive, send, "No dealership is served at this address.")
        if host:
            return await self._on_host(scope, receive, send, host)

        widget = widget_store(scope.get("path", "/"))
        if widget:
            token = current_store.set(widget)
            try:
                return await self.app(scope, receive, send)
            finally:
                current_store.reset(token)

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

    async def _on_host(self, scope, receive, send, host: str) -> None:  # noqa: ANN001
        """A request to a dealership's own subdomain: that store, and only it.

        The path may still carry the store -- a link written for the shared
        host, a bookmark, the login's redirect from before -- and naming the
        same store twice is harmless, so the prefix is dropped. Naming a
        *different* one is a request no answer can be right for, and it is
        refused rather than served from either.

        **Nothing of Liner's own is on a dealership's subdomain.** `/ops` and
        its API are ours and are served where ours is; a group's address shows
        that group and nothing else, so a buyer or a rep never meets our
        dashboard there, even as a login form.
        """
        path = scope.get("path", "/")
        widget = widget_store(path)
        if widget and widget != host:
            return await _elsewhere(scope, receive, send, f"This address is {host}'s.")
        slug, rest = split(path)
        if slug and slug != host:
            return await _elsewhere(scope, receive, send, f"This address is {host}'s.")
        if slug == host:
            scope = dict(scope)
            scope["path"] = rest
            raw = scope.get("raw_path")
            prefix = f"/{slug}".encode()
            if raw and raw.startswith(prefix):
                scope["raw_path"] = raw[len(prefix):] or b"/"
            path = rest
        if path == "/ops" or path.startswith(("/ops/", "/api/ops")):
            return await _elsewhere(scope, receive, send, "Not found")
        if not has_database(host) and (path.startswith("/api/") or path.startswith("/ws/")):
            return await _not_seeded(scope, receive, send, host)

        token = current_store.set(host)
        named = current_host.set(host)
        try:
            await self.app(scope, receive, send)
        finally:
            current_host.reset(named)
            current_store.reset(token)
