"""A headless Chromium with the shape of an `httpx.Client`, for platforms that
refuse anything else.

GMA answers httpx with 429 whatever it is sent -- a browser user agent
included -- while a headless Chromium gets the page. `crawl_list` and
`robots_verdict` only ever call `.get(url, timeout=)` and read
`.status_code` and `.text`, so a client with that shape plugs into the crawl
unchanged: one crawl, two ways of fetching, chosen per platform by
`ListAdapter.browser` in `pipeline.open_client`.

**JavaScript off, and nothing but the document.** The listing's data is in
the server-rendered HTML, so nothing else needs to load -- which is also the
polite crawl (one request per page, no images, fonts or trackers pulled from
somebody else's CDN) and the safe one: under the systemd unit Chromium runs
without its sandbox (`NoNewPrivileges` rules out the setuid helper) as the
user that can read `.env`, so the dealer's scripts, and every third party's
their page loads, never run on this box at all.

**It says who it is.** The user agent is Chromium's own with
`SCRAPER_USER_AGENT` appended, the identity the plain crawl already sends:
their filter is looking for a browser, not an anonymous one.

Playwright is imported inside `__enter__`, never at module level: the
application imports this module on every boot and must not need a browser to
start, and a box without one gets `BrowserUnavailable` naming the fix instead
of an ImportError at startup.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace

import httpx

from app.config import settings

INSTALL = ("sudo PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers "
           "/srv/liner/backend/.venv/bin/python -m playwright install --with-deps chromium")


class BrowserUnavailable(RuntimeError):
    """No browser can run here; the message names the fix."""


class BrowserFetchError(httpx.TransportError):
    """A page the browser could not load. An `httpx.HTTPError`, so every crawl
    path that already catches those catches this without learning a second
    exception."""


def browsers_path() -> str:
    return (settings.playwright_browsers_path or os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")).strip()


def problem() -> str:
    """"" when a browser fetch can run here, else why not. Launches nothing."""
    if find_spec("playwright") is None:
        return "Playwright is not installed in this environment (make install)."
    where = browsers_path()
    if not where:
        return ("PLAYWRIGHT_BROWSERS_PATH is not set, and the service cannot read a home "
                f"directory to find a browser in. Install one with: {INSTALL}")
    root = Path(where)
    if not any(root.glob("chromium*")):
        return f"No Chromium under {where}. Install one with: {INSTALL}"
    return ""


class BrowserClient:
    """`with BrowserClient() as client: client.get(url)` -- one browser for a
    whole crawl, so the platform's check is passed once, not per page."""

    def __init__(self, user_agent: str | None = None) -> None:
        self._agent = settings.scraper_user_agent if user_agent is None else user_agent
        self._pw = self._browser = self._ctx = self._page = None
        self._home = ""

    def __enter__(self) -> "BrowserClient":
        where = browsers_path()
        if where:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = where
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserUnavailable(problem() or str(exc)) from exc
        # Under ProtectHome the service's $HOME is unreadable, and Chromium
        # wants somewhere to put its profile and crash reports.
        self._home = tempfile.mkdtemp(prefix="liner-chromium-")
        env = {**os.environ, "HOME": self._home, "XDG_CONFIG_HOME": self._home,
               "XDG_CACHE_HOME": self._home}
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True, env=env)
            probe = self._browser.new_page()
            agent = probe.evaluate("navigator.userAgent")
            probe.close()
            self._ctx = self._browser.new_context(
                java_script_enabled=False, locale="en-US",
                user_agent=f"{agent} {self._agent}".strip())
            self._ctx.route("**/*", lambda route: route.continue_()
                            if route.request.resource_type == "document" else route.abort())
            self._page = self._ctx.new_page()
        except BrowserUnavailable:
            self.__exit__(None, None, None)
            raise
        except Exception as exc:  # playwright's Error is not importable before start()
            self.__exit__(None, None, None)
            raise BrowserUnavailable(f"{problem() or 'Chromium would not start'}: {exc}") from exc
        return self

    def get(self, url: str, timeout: float = 25) -> SimpleNamespace:
        try:
            response = self._page.goto(url, wait_until="domcontentloaded",
                                       timeout=max(timeout, 45) * 1000)
            if response is None:
                raise BrowserFetchError(f"no response for {url}")
            return SimpleNamespace(status_code=response.status, text=response.text(),
                                   url=response.url, headers=dict(response.headers))
        except BrowserFetchError:
            raise
        except Exception as exc:
            raise BrowserFetchError(f"{type(exc).__name__}: {exc}".splitlines()[0]) from exc

    def __exit__(self, *_exc) -> None:
        for close in (getattr(self._ctx, "close", None), getattr(self._browser, "close", None),
                      getattr(self._pw, "stop", None)):
            if close is not None:
                try:
                    close()
                except Exception:
                    pass
        self._pw = self._browser = self._ctx = self._page = None
        if self._home:
            shutil.rmtree(self._home, ignore_errors=True)
            self._home = ""
