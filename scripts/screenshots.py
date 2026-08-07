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

PUBLIC = ["/", "/chat", "/call", "/login"]

# Assets the design references but that have not been supplied yet. Their 404s
# are reported rather than failing the run; remove each one as it arrives.
PENDING_ASSETS: list[str] = []

# Hosts the page legitimately reaches that this sandbox's headless browser
# cannot. `curl` gets 200 from Google Fonts through the agent proxy, but
# Chromium does not inherit it -- so a failure here says nothing about the page.
UNREACHABLE_HOSTS = ["fonts.googleapis.com", "fonts.gstatic.com"]
DEALER = [
    "/app",
    "/app/conversations",
    "/app/leads",
    "/app/leads/import",
    "/app/calendar",
    "/app/inventory",
    "/app/inventory/import",
    "/app/assistant",
    "/app/team",
]


# Reps and managers work from phones, so a route that overflows there is a real
# break, not a cosmetic one. 390x844 is an iPhone 13/14/15 logical viewport --
# the narrowest width worth designing for in 2026.
PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}

# Overflow is measured, not eyeballed. A table wider than the screen makes the
# browser shrink-to-fit the whole document, so one wide element renders every
# other page element tiny -- which reads as "the app looks wrong on mobile"
# rather than "this table is too wide", and sends you looking in the wrong file.
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
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on(
            "console",
            lambda msg: errors.append(msg.text) if msg.type == "error" else None,
        )
        # A resource-load console message does not name the file, so the URL has
        # to come off the request itself to tell an expected gap from a real
        # break. Both signals matter: a 404 arrives as a response, a blocked
        # host as a failed request.
        page.on(
            "response",
            lambda r: bad_urls.append(r.url) if r.status >= 400 else None,
        )
        page.on("requestfailed", lambda r: bad_urls.append(r.url))

        phone = False

        async def shot(route: str) -> None:
            errors.clear()
            bad_urls.clear()
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
            # A React crash leaves the root blank; console errors catch the rest.
            real = [e for e in errors if "favicon" not in e.lower()]

            # Two kinds of expected gap, reported rather than hidden: assets the
            # design references that have not been supplied, and hosts this
            # sandbox's browser cannot reach. Anything else is a real failure.
            excused, unexplained = [], []
            for url in dict.fromkeys(bad_urls):
                if any(a in url for a in PENDING_ASSETS):
                    excused.append(f"asset not supplied yet: {url.rsplit('/', 1)[-1]}")
                elif any(h in url for h in UNREACHABLE_HOSTS):
                    excused.append(f"blocked in this sandbox: {url.split('/')[2]}")
                else:
                    unexplained.append(url)

            if excused and not unexplained:
                # Every failed request is accounted for, so the generic
                # "failed to load resource" noise it produced is too.
                real = [e for e in real if "failed to load resource" not in e.lower()]
            for note in excused:
                print(f"       NOTE  {note}")

            if real:
                failures.append(f"{route}: {real[0][:120]}")
            print(f"  {route:32} {len(body.strip()):>6} chars"
                  f"{'  ERRORS: ' + real[0][:80] if real else ''}")

        print("public routes:")
        for route in PUBLIC:
            await shot(route)

        print("\nsigning in...")
        await page.goto(BASE + "/login", wait_until="networkidle")
        await page.fill('input[type="email"]', "dana.mercer@example.invalid")
        await page.fill('input[type="password"]', "liner-dev")
        await page.click('button[type="submit"]')
        await page.wait_for_url("**/app", timeout=10_000)

        print("\ndealer routes:")
        for route in DEALER:
            await shot(route)

        # Same session, narrower window. Signing in again is unnecessary and the
        # cookie is what makes the dealer routes reachable at all.
        phone = True
        await page.set_viewport_size(PHONE)
        print(f"\nmobile ({PHONE['width']}px):")
        for route in ["/chat", *DEALER]:
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
