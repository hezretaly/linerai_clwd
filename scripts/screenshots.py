#!/usr/bin/env python3
"""Screenshot every route into .artifacts/.

The only way to check a page actually rendered rather than throwing. Catches
"did it render" reliably; "does it look right" still needs a human.

    make shots
"""

from __future__ import annotations

import asyncio
import glob
import pathlib
import sys

BASE = "http://127.0.0.1:5173"
OUT = pathlib.Path(__file__).resolve().parent.parent / ".artifacts"


def chromium_path() -> str | None:
    """Use the browser already on the machine rather than downloading one.

    The preinstalled build under /opt/pw-browsers may not match the revision
    this playwright package expects, so point at it explicitly.
    """
    for pattern in (
        "/opt/pw-browsers/chromium-*/chrome-linux/chrome",
        "/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell",
    ):
        found = sorted(glob.glob(pattern))
        if found:
            return found[-1]
    return None

# `/showroom` is the demo page: the dealership's own front page with the
# assistant on it. It is here rather than only in the desktop pass because a
# fixed widget in a corner and a grid of car cards are the two things most
# likely to push a 390px viewport sideways.
PUBLIC = ["/", "/chat", "/call", "/login", "/showroom"]

# Assets the design references but that have not been supplied yet. Their 404s
# are reported rather than failing the run; remove each one as it arrives.
PENDING_ASSETS: list[str] = []

# A request to any host but our own is excused, and named. This began as a
# list -- Google Fonts, which `curl` reaches through the agent proxy and
# Chromium does not -- and the storefronts made a list wrong: they hotlink
# the dealer's logo, banners and every car photo from *their* CDN, and the
# next dealership's CDN is a host this file has never heard of. Naming a
# dealer's host here would be their name written into product tooling. What
# this check exists to catch is a request to *us* that failed -- an API
# path that 404s, a route the server does not serve -- and those all go to
# BASE. An off-origin failure in a sandbox with no egress says nothing about
# the page, so it is reported as a NOTE rather than hidden, and never fails
# the run.
OWN_HOST = BASE.split("//", 1)[-1]


def off_origin(url: str) -> str:
    """The host, when it is not ours; '' for a request to this app."""
    host = url.split("/")[2] if "//" in url else ""
    return "" if (not host or host == OWN_HOST or host.startswith("127.0.0.1") or host.startswith("localhost")) else host
DEALER = [
    "/app",
    "/app/conversations",
    "/app/leads/import",
    "/app/calendar",
    "/app/inventory",
    "/app/inventory/import",
    "/app/campaigns",
    "/app/assistant",
    "/app/team",
]

# Liner's own dashboard, behind the `owner` role -- a different sign-in, so it
# is a separate list rather than two more entries above. It gets the same
# 390px rule as everything else: two people run this company and both of them
# will read a new demo on a phone.
OPS = ["/ops", "/ops/mail", "/ops/phone"]


# Reps and managers work from phones, so a route that overflows there is a real
# break, not a cosmetic one. 390x844 is an iPhone 13/14/15 logical viewport --
# the narrowest width worth designing for in 2026.
def car_page(slug: str) -> str:
    """The first car on a store's lot, as its page's path, or '' for an empty
    lot. Asked of the API rather than written down, because the car pages are
    the ones most likely to overflow at 390px -- a long engine line in a
    two-column table -- and a hardcoded VIN goes stale the day it sells."""
    import json as _json
    import urllib.request as _req

    try:
        with _req.urlopen(f"{BASE}/{slug}/api/showroom?limit=1", timeout=10) as r:
            cars = _json.load(r).get("vehicles") or []
    except Exception:
        return ""
    return f"/{slug}/showroom/{cars[0]['vin']}" if cars else ""


def stores_with_a_file() -> list[str]:
    """Every store slug whose database exists -- asked of the file, never by
    opening it. Same guard `make smoke` uses, for the same reason: connecting
    to SQLite creates the file, and a screenshot run that minted an empty
    `riverside.db` would trip the gate that exists for exactly that."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))
    from app.config import settings

    from app import pg

    def present(slug: str) -> bool:
        url = settings.database_url_for(slug)
        if pg.is_postgres(url):
            return pg.exists(url)
        return pathlib.Path(url.split("///", 1)[-1]).exists()

    return [slug for slug in settings.store_slugs if present(slug)]


PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}

# Overflow is measured, not eyeballed. A table wider than the screen makes the
# browser shrink-to-fit the whole document, so one wide element renders every
# other page element tiny -- which reads as "the app looks wrong on mobile"
# rather than "this table is too wide", and sends you looking in the wrong file.
# Warm colour on the overview. The dashboard's rule is that a warm hue means
# something is wrong -- everything else is blue -- and this drifted back twice
# because a status pill in amber reads as red next to the rest of the page and
# nobody notices in a diff. Hue is read off the computed oklch, so a token
# renamed or re-tinted still gets caught.
#
# The integration banner is exempt: "not configured" is exactly the case warm
# is reserved for, and it is the honesty mechanism this whole system runs on.
WARM_JS = """() => {
    const warm = (v) => {
        const m = v.match(/oklch\\(([\\d.]+) ([\\d.]+) ([\\d.]+)/);
        if (!m) return false;
        const [, l, c, h] = m.map(Number);
        // Chroma below this is grey with a hue that means nothing.
        return c > 0.04 && (h < 100 || h > 340) && l < 0.85;
    };
    const exempt = (el) => el.closest('[data-warm-ok]') !== null;
    const out = [];
    document.querySelectorAll('main *').forEach(el => {
        if (el.children.length || exempt(el)) return;
        const s = getComputedStyle(el);
        if (!warm(s.color) && !warm(s.backgroundColor)) return;
        const t = (el.textContent || '').trim().slice(0, 30);
        if (t) out.push(t);
    });
    return [...new Set(out)];
}"""

# A waiting time is a stopwatch, and a stopwatch stops being readable long
# before it stops counting. `1349h 36m` shipped on the overview -- a real
# two-month-old row in the Needs a person queue, rendered as a number nobody
# can take in at a glance with minutes of precision on the end. `waited()`
# turns to days past 48h; this is the assertion that it stays that way.
#
# Scoped to the exact shape that formatter produces, so a three-digit number
# followed by an `h` somewhere else on a page cannot false-positive it.
STOPWATCH_JS = """() => {
    const bad = [];
    document.querySelectorAll('main *, body *').forEach(el => {
        if (el.children.length) return;
        const t = (el.textContent || '').trim();
        if (/^\\d{3,}h \\d{2}m$/.test(t)) bad.push(t);
    });
    return [...new Set(bad)].slice(0, 5);
}"""

OVERFLOW_JS = """() => {
    const root = document.documentElement;
    const vw = root.clientWidth;
    if (root.scrollWidth <= vw + 1) return {vw, worst: []};

    // Only elements that actually push the *document* wider count. A wide table
    // inside its own overflow-x-auto card is a deliberate choice, not a break,
    // and flagging it sends you editing a file that is already correct.
    const scrolls = (el) => {
        for (let n = el.parentElement; n; n = n.parentElement) {
            const o = getComputedStyle(n).overflowX;
            if (o === 'auto' || o === 'scroll' || o === 'hidden') return true;
        }
        return false;
    };

    const worst = [];
    root.querySelectorAll('*').forEach(el => {
        const r = el.getBoundingClientRect();
        // Too wide, or pushed past the right edge by a margin or an absolute
        // offset. The second kind leaves every element narrower than the
        // viewport while the document still scrolls, which is the harder one
        // to find by eye.
        if ((r.width > vw + 1 || r.right > vw + 1) && !scrolls(el)) {
            const cls = el.className && el.className.baseVal !== undefined
                ? el.className.baseVal : String(el.className || '');
            worst.push({tag: el.tagName.toLowerCase(), cls: cls.slice(0, 60),
                        w: Math.round(r.width), right: Math.round(r.right)});
        }
    });
    worst.sort((a, b) => b.right - a.right);
    return {vw, worst: worst.slice(0, 3), doc: root.scrollWidth};
}"""


async def main() -> int:
    from playwright.async_api import async_playwright

    OUT.mkdir(exist_ok=True)
    (OUT / "mobile").mkdir(exist_ok=True)
    failures: list[str] = []

    async with async_playwright() as p:
        executable = chromium_path()
        browser = await p.chromium.launch(
            executable_path=executable, args=["--no-sandbox"]
        )
        context = await browser.new_context(viewport=DESKTOP)
        page = await context.new_page()

        errors: list[str] = []
        bad_urls: list[str] = []
        signed_out_probe: list[str] = []
        # Which route is being shot, for the one excuse below that depends on it.
        here = [""]
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on(
            "console",
            lambda msg: errors.append(msg.text) if msg.type == "error" else None,
        )
        # A resource-load console message does not name the file, so the URL has
        # to come off the request itself to tell an expected gap from a real
        # break. Both signals matter: a 404 arrives as a response, a blocked
        # host as a failed request.

        def on_response(r) -> None:
            if r.status < 400:
                return
            # `/login` asks who is signed in so it can send somebody who already
            # is to their own dashboard, and for a visitor who is not the honest
            # answer is 401. Excused *only there*: every dealer and ops route is
            # shot with a session, so a 401 from this endpoint anywhere else is
            # a real break and still fails the run.
            if r.status == 401 and r.url.endswith("/api/auth/me") and here[0] == "/login":
                signed_out_probe.append(r.url)
                return
            bad_urls.append(r.url)

        page.on("response", on_response)
        # **The chat's live stream is cut whenever its page is left**, and
        # that is what a stream is: `GET .../live` stays open until the
        # document goes, so leaving it reports `ERR_ABORTED` -- landing on
        # the *next* route, because the abort happens during its `goto`. The
        # embed shot leaves one open in an iframe right before `/alsbou`, which
        # then failed with a "failed to load resource" that named nothing on
        # that page. Only an abort of that one stream is excused; the stream
        # failing any other way is still a real break.
        cut_streams: list[str] = []

        def on_failed(r) -> None:
            if r.url.split("?")[0].endswith("/live") and "ERR_ABORTED" in (r.failure or ""):
                cut_streams.append(r.url)
                return
            bad_urls.append(r.url)

        page.on("requestfailed", on_failed)

        phone = False

        async def shot(route: str) -> None:
            errors.clear()
            bad_urls.clear()
            signed_out_probe.clear()
            cut_streams.clear()
            here[0] = route
            await page.goto(BASE + route, wait_until="networkidle")
            await page.wait_for_timeout(700)

            # Scroll the whole page before capturing. The landing page hides
            # every section behind `.reveal { opacity: 0 }` until an
            # IntersectionObserver fires, and a full-page screenshot does not
            # scroll -- without this the artifact looks like a broken page.
            await page.evaluate(
                """async () => {
                    // The landing page sets `scroll-behavior: smooth`, which
                    // makes scrollTo animate -- the page then lags far behind
                    // the loop and the lower sections are never reached at all.
                    const root = document.documentElement;
                    const previous = root.style.scrollBehavior;
                    root.style.scrollBehavior = 'auto';

                    const step = window.innerHeight * 0.6;
                    const height = () => document.documentElement.scrollHeight;
                    for (let y = 0; y < height(); y += step) {
                        window.scrollTo(0, y);
                        await new Promise(r => setTimeout(r, 120));
                    }
                    window.scrollTo(0, 0);
                    await new Promise(r => setTimeout(r, 300));
                    root.style.scrollBehavior = previous;
                }"""
            )
            await page.wait_for_timeout(600)

            name = route.strip("/").replace("/", "-") or "landing"
            await page.screenshot(
                path=(OUT / "mobile" if phone else OUT) / f"{name}.png", full_page=True
            )

            if phone:
                if route == "/app":
                    warm = await page.evaluate(WARM_JS)
                    if warm:
                        failures.append(
                            f"{route}: warm colour where the dashboard is blue -- "
                            + ", ".join(repr(w) for w in warm[:4])
                        )
                over = await page.evaluate(OVERFLOW_JS)
                if over["worst"]:
                    w = over["worst"][0]
                    failures.append(
                        f"{route}: page scrolls sideways ({over['doc']}px in a "
                        f"{over['vw']}px viewport) -- <{w['tag']}> is {w['w']}px "
                        f"ending at {w['right']}px ({w['cls']})"
                    )
                elif over.get("doc", 0) > over["vw"] + 1:
                    failures.append(
                        f"{route}: page scrolls sideways ({over['doc']}px in a "
                        f"{over['vw']}px viewport), no single element to blame"
                    )

            body = await page.inner_text("body")
            if len(body.strip()) < 40:
                failures.append(f"{route}: page is empty")
            # **Liner setup has one box, and the rendered page is what says
            # so.** The source can name the right endpoint and still draw the
            # old list of assistants -- a page wired correctly and showing the
            # wrong thing is how the closed-thread bug was found. The
            # Instructions tab is the one it opens on.
            if route == "/app/assistant":
                boxes = await page.locator("textarea").count()
                prompt_box = await page.locator("textarea#assistant-prompt").count()
                retired = [w for w in ("Every assistant", "Writing assistant",
                                       "Phone call instructions") if w in body]
                if boxes != 1 or prompt_box != 1 or retired:
                    failures.append(
                        f"{route}: expected one prompt box, found {boxes} textarea(s), "
                        f"#assistant-prompt x{prompt_box}, old labels {retired}"
                    )
            # A React crash leaves the root blank; console errors catch the rest.
            real = [e for e in errors if "favicon" not in e.lower()]

            # Two kinds of expected gap, reported rather than hidden: assets the
            # design references that have not been supplied, and hosts this
            # sandbox's browser cannot reach. Anything else is a real failure.
            excused, unexplained = [], []
            for url in dict.fromkeys(bad_urls):
                if any(a in url for a in PENDING_ASSETS):
                    excused.append(f"asset not supplied yet: {url.rsplit('/', 1)[-1]}")
                elif off_origin(url):
                    excused.append(f"blocked in this sandbox: {off_origin(url)}")
                else:
                    unexplained.append(url)
            if signed_out_probe:
                excused.append("nobody signed in, so /api/auth/me answers 401")
            if cut_streams:
                excused.append("the previous page's chat stream closed as it was left")

            if excused and not unexplained:
                # Every failed request is accounted for, so the generic
                # "failed to load resource" noise it produced is too.
                real = [e for e in real if "failed to load resource" not in e.lower()]
            for note in excused:
                print(f"       NOTE  {note}")

            stopwatch = await page.evaluate(STOPWATCH_JS)
            if stopwatch:
                real.append(
                    f"unreadable waiting time {stopwatch[0]} -- `waited()` should "
                    f"turn to days past 48h"
                )

            if real and unexplained:
                # Name the request, not only the console line: "failed to load
                # resource" says nothing about which one.
                real[0] = f"{real[0]} [{unexplained[0][:80]}]"
            if real:
                failures.append(f"{route}: {real[0][:120]}")
            print(f"  {route:32} {len(body.strip()):>6} chars"
                  f"{'  ERRORS: ' + real[0][:80] if real else ''}")

        print("public routes:")
        for route in PUBLIC:
            await shot(route)

        # **The buyer's call page must not read like a console.**
        #
        # `/call` is one route serving two audiences -- a car buyer and
        # whoever is setting the line up -- because two call pages is how one
        # of them quietly stops doing the hard part (`silence()` before
        # teardown, the AudioContext inside the click gesture, ordered chunk
        # upload, the consent line). `?diagnostics=1` *adds* panels and the
        # buyer's version is the default.
        #
        # Asserted on what is **rendered**, not on the source: the guard is a
        # `diagnostics &&` in JSX and a reader cannot tell by eye which branch
        # a string ends up in. Voice is unconfigured on this box, which is
        # what makes it checkable with no key at all -- the "not configured"
        # panel is itself one of the four leaks, and it printed
        # `OPENAI_API_KEY` in a warning box on a dealership's own site.
        print("\nthe call page's two audiences:")
        INTERNALS = ["VOICE_TRANSCRIBE", "VOICE_PROVIDER", "OPENAI_API_KEY"]

        async def call_text(query: str) -> str:
            """The call page after pressing Start, which is when it says
            anything at all about how it is configured.

            The button is pressed because the "not configured" panel does not
            exist until a session is attempted -- loading the route and
            reading it proves nothing, which is what the first version of this
            check did. It needs no microphone and no key: `POST
            /api/voice/sessions` is awaited *before* `getUserMedia`, so on a
            box with voice unconfigured the refusal is the first thing back.
            """
            await page.goto(BASE + "/call" + query, wait_until="networkidle")
            await page.wait_for_timeout(400)
            await page.click("button:text-is('Start a call')")
            await page.wait_for_timeout(900)
            return await page.inner_text("body")

        plain = await call_text("")
        leaked = [name for name in INTERNALS if name in plain]
        if leaked:
            failures.append(
                f"/call: a buyer is shown this deployment's internals: {', '.join(leaked)}"
            )
        # Not vacuous: the buyer has to be told the line is unavailable, or
        # "no internals" is satisfied by a page that said nothing at all.
        if "not available" not in plain.lower():
            failures.append(
                "/call: a buyer pressed Start on an unconfigured line and was "
                "told nothing -- the refusal has to say something they can act on"
            )
        # `text-transform: uppercase` reaches `inner_text`, so the badge comes
        # back DIAGNOSTICS however it is written in the JSX. Compared folded,
        # or this check fails on a CSS class.
        if "diagnostics" in plain.lower():
            failures.append("/call: the buyer's page is wearing the diagnostics badge")

        flagged = await call_text("?diagnostics=1")
        # The other direction, or the check passes by the panel having been
        # deleted rather than moved -- the failure mode a one-sided assertion
        # always drifts into.
        if not any(name in flagged for name in INTERNALS):
            failures.append(
                f"/call?diagnostics=1: names none of {INTERNALS} -- the panel "
                "was removed rather than gated behind the flag"
            )
        if "diagnostics" not in flagged.lower():
            failures.append("/call?diagnostics=1: nothing says which page this is")
        print(f"  buyer    {len(plain.strip()):>5} chars, no internals")
        print(f"  flagged  {len(flagged.strip()):>5} chars, names them")

        # **The bubble a dealership pastes onto its own site**, driven on a
        # page that is not ours and from another origin -- `localhost` hosts
        # the page, `127.0.0.1` serves the tag and the chat, which are two
        # origins to a browser exactly as a dealer's site and ours are.
        # Asserted in a browser because every claim it makes is a browser
        # claim: that their stylesheet cannot reach our button, that the
        # iframe is not created until somebody clicks, that the conversation
        # is kept on *their* domain, that the loader and the chat really talk
        # across the two origins, and that a closed panel is out of the tab
        # order rather than merely transparent. `make smoke` reads the file;
        # only this runs it.
        #
        # **Their page is written into a real document on the host origin**
        # rather than fulfilled by the router: a route-fulfilled document
        # stalls cross-origin subresources in this Chromium, so the tag never
        # loaded -- measured, and a check that cannot load the thing it
        # checks passes nothing. Their CSS is deliberately hostile: every
        # button pink on lime.
        print("\nthe website chat, on somebody else's page:")
        dealers = stores_with_a_file()
        host = BASE.replace("127.0.0.1", "localhost")
        if not dealers:
            print("  NOTE: no dealership is seeded, so there is no tag to install")
        else:
            dealer = dealers[0]
            hostile = (
                "<!doctype html><html><head><meta charset='windows-1252'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                "<title>dealer</title><style>"
                "button{background:hotpink!important;border:4px dotted lime!important}"
                "</style></head><body><h1>Their site</h1><button>Theirs</button>"
                f'<script src="{BASE}/embed.js" data-dealer="{dealer}" async></script>'
                "</body></html>"
            )

            async def their_page(tab):
                await tab.goto(f"{host}/embed.js", wait_until="load")
                await tab.evaluate("localStorage.clear(); sessionStorage.clear()")
                await tab.set_content(hostile, wait_until="load")
                await tab.wait_for_timeout(1500)

            def chat_frames(tab):
                return [f for f in tab.frames if f"/widget/{dealer}" in f.url]

            await their_page(page)
            widget = page.locator("[data-liner-embed]")
            if await widget.count() != 1:
                failures.append("/embed.js: the widget did not mount on the host page")
            else:
                bubble = widget.locator("button.bubble")
                colour = await bubble.evaluate("el => getComputedStyle(el).backgroundColor")
                if "255, 105, 180" in colour:  # hotpink, i.e. their CSS reached in
                    failures.append(
                        f"/embed.js: the host page's CSS restyled our button ({colour}) -- "
                        "the shadow root is not isolating it"
                    )
                before = len(chat_frames(page))
                if before:
                    failures.append(
                        "/embed.js: a chat frame exists before anybody clicked -- that starts "
                        "a conversation for every visitor who never does"
                    )
                await bubble.click()
                await page.wait_for_timeout(2500)
                frames = chat_frames(page)
                if not frames:
                    failures.append("/embed.js: clicking the bubble loaded no chat frame")
                kept = await page.evaluate(f"localStorage.getItem('liner.{dealer}.conversation')")
                if not kept:
                    failures.append(
                        "/embed.js: the loader kept no conversation on the dealer's own domain -- "
                        "the chat and the page never shook hands across the two origins"
                    )
                # A page served as windows-1252 reads an undeclared script in
                # that encoding: a literal multiplication sign on the close
                # button arrived as two letters.
                close = widget.locator("button[aria-label='Close chat']")
                glyph = (await close.inner_text()).strip()
                if glyph != "×":
                    failures.append(f"/embed.js: the close button reads {glyph!r} on a windows-1252 page")
                await close.click()
                await page.wait_for_timeout(500)
                shut = await widget.locator(".panel").evaluate(
                    "el => getComputedStyle(el).visibility"
                )
                if shut != "hidden":
                    failures.append(
                        f"/embed.js: a closed panel is {shut}, so a keyboard can still tab "
                        "into an invisible chat"
                    )
                print(f"  mounted, bubble {colour}, frames {before} -> {len(frames)}, "
                      f"conversation kept on their domain: {bool(kept)}, closed {shut}")

            # **A tag written for an older address follows the chat to where
            # it lives now.** A tag pasted as `linerai.us/<dealer>/embed.js`
            # still loads once the group has its own subdomain -- the old box
            # redirects it (deploy/linerai.nginx.conf) -- but a redirect
            # does not change `currentScript.src`, so the loader has to take
            # the config's `frame_origin` over its own: for the frame's
            # address *and* for the origin every message is pinned to. Half of
            # that is a frame that loads and a buyer nobody hears. The redirect
            # is nginx's and was driven there; what is ours is the loader, so
            # the config's answer is rewritten to name the only other origin
            # this box has, and the handshake has to survive the move.
            moved_ctx = await browser.new_context()
            moved = await moved_ctx.new_page()

            async def elsewhere(route):
                answer = await route.fetch()
                body = await answer.json()
                body["frame_origin"] = host
                await route.fulfill(response=answer, json=body)

            await moved.route("**/api/widget/config**", elsewhere)
            await their_page(moved)
            if await moved.locator("[data-liner-embed]").count():
                await moved.locator("[data-liner-embed] button.bubble").click()
                await moved.wait_for_timeout(2500)
                where = [f.url for f in chat_frames(moved)]
                shook = await moved.evaluate(f"localStorage.getItem('liner.{dealer}.conversation')")
                if not where or not where[0].startswith(f"{host}/widget/{dealer}"):
                    failures.append(
                        f"/embed.js: the config named {host} as the chat's home and the frame "
                        f"loaded from {where or 'nowhere'} -- an old tag would frame the old address"
                    )
                elif not shook:
                    failures.append(
                        "/embed.js: the frame moved to the config's origin but the loader never "
                        "heard it -- messages are still pinned to the tag's own origin"
                    )
                print(f"  moved: frame at {where and where[0].split('?')[0]}, handshake {bool(shook)}")
            await moved_ctx.close()

            # **The whole screen on a phone.** A 380px card on a 390px screen
            # is a chat sharing its width with the page behind it.
            # `handset`, never `phone`: that name is the flag `shot()` reads to
            # decide which folder a picture goes in and which checks run, and
            # a Page is truthy -- so every desktop shot after this one went to
            # `mobile/`, to be overwritten by the real phone pass, and the
            # desktop store and dealer pictures silently stopped being taken.
            phone_ctx = await browser.new_context(viewport=PHONE, is_mobile=True, has_touch=True)
            handset = await phone_ctx.new_page()
            await their_page(handset)
            if await handset.locator("[data-liner-embed]").count():
                await handset.locator("[data-liner-embed] button.bubble").tap()
                await handset.wait_for_timeout(2000)
                box = await handset.locator("[data-liner-embed] .panel").bounding_box()
                if not box or box["width"] < PHONE["width"] - 1 or box["height"] < PHONE["height"] - 1:
                    failures.append(f"/embed.js: the chat is not the whole screen on a phone: {box}")
                print(f"  phone: panel {box and round(box['width'])}x{box and round(box['height'])}")
            await phone_ctx.close()

        # **A dealership's own two pages, under its prefix.** `/` unprefixed is
        # Liner's marketing document and `/showroom` unprefixed is the default
        # store's list, so neither exercises the front page a prospect is
        # actually sent -- `/alsbou` -- which is a React route reached only
        # with a store in the URL. Shot for every store that has a file, and
        # asked before opening one, because requesting `/<slug>/` creates the
        # database (the lesson `db.has_database` exists for).
        print("\nstore pages:")
        for slug in stores_with_a_file():
            await shot(f"/{slug}")
            await shot(f"/{slug}/showroom")
            # One car's own page, discovered rather than listed: a VIN written
            # here is a car that sells and turns the shot into a 404 page.
            car = car_page(slug)
            if car:
                await shot(car)

        print("\nsigning in...")
        await page.goto(BASE + "/login", wait_until="networkidle")
        await page.fill('input[type="email"]', "dana.mercer@riversideauto.example")
        await page.fill('input[type="password"]', "liner-dev")
        # Signing in is a *document load* now: the server decides which store
        # the address belongs to, and crossing into one reloads so the router
        # mounts with the right basename. So wait for the load the click
        # causes, rather than for the URL -- `wait_for_url` matches the instant
        # `location.assign` sets it, measurably before the new document exists
        # (1723ms against a load event at 1799ms), and the next
        # `page.evaluate` then ran in a context about to be destroyed.
        async with page.expect_event("load"):
            await page.click('button[type="submit"]')
        await page.wait_for_url("**/app", timeout=10_000)
        await page.wait_for_load_state("networkidle")

        # The buyer page needs a real id, so it is discovered rather than
        # listed. It is the page most likely to overflow -- a timeline, a rail
        # and a channel strip -- so leaving it out of the phone check would
        # leave the one screen a rep actually reads unguarded.
        routes = list(DEALER)
        picked = await page.evaluate(
            """async () => {
                const r = await fetch('/api/leads', {credentials: 'include'})
                const {leads} = await r.json()
                // The busiest buyer: most channels, then most threads. A lead
                // with nothing on it would pass a check it never exercised.
                const best = leads
                  .filter(l => l.conversation_count)
                  .sort((a, b) => (b.channels?.length ?? 0) - (a.channels?.length ?? 0)
                                  || b.conversation_count - a.conversation_count)[0]
                return best ? best.id : null
            }"""
        )
        if picked:
            routes.insert(2, f"/app/leads/{picked}")
        else:
            print("  (no lead with a conversation -- skipping the buyer page)")

        print("\ndealer routes:")
        for route in routes:
            await shot(route)

        # Same session, narrower window. Signing in again is unnecessary and the
        # cookie is what makes the dealer routes reachable at all.
        phone = True
        await page.set_viewport_size(PHONE)
        print(f"\nmobile ({PHONE['width']}px):")
        store_pages = [
            p for slug in stores_with_a_file()
            for p in (f"/{slug}", f"/{slug}/showroom", car_page(slug)) if p
        ]
        for route in ["/chat", "/showroom", *store_pages, *routes]:
            await shot(route)

        # Ours. A second sign-in because `owner` is a third role and a
        # dealership's session gets a 403 on every one of these.
        phone = False
        await page.set_viewport_size(DESKTOP)
        await page.goto(BASE + "/login?as=owner", wait_until="networkidle")
        await page.fill('input[type="password"]', "liner-dev")
        async with page.expect_event("load"):
            await page.click('button[type="submit"]')
        await page.wait_for_url("**/ops", timeout=10_000)
        await page.wait_for_load_state("networkidle")
        print("\nops routes:")
        for route in OPS:
            await shot(route)

        phone = True
        await page.set_viewport_size(PHONE)
        print(f"\nops mobile ({PHONE['width']}px):")
        for route in OPS:
            await shot(route)

        await browser.close()

    print()
    if failures:
        print(f"FAILED: {len(failures)}")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print(f"All routes rendered. Screenshots in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
