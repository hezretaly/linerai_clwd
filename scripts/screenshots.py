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
PENDING_ASSETS = ["live_inv_car.png"]
DEALER = [
    "/app",
    "/app/conversations",
    "/app/leads",
    "/app/calendar",
    "/app/inventory",
    "/app/inventory/import",
    "/app/assistant",
    "/app/team",
]


async def main() -> int:
    from playwright.async_api import async_playwright

    OUT.mkdir(exist_ok=True)
    failures: list[str] = []

    async with async_playwright() as p:
        executable = chromium_path()
        browser = await p.chromium.launch(
            executable_path=executable, args=["--no-sandbox"]
        )
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()

        errors: list[str] = []
        bad_urls: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on(
            "console",
            lambda msg: errors.append(msg.text) if msg.type == "error" else None,
        )
        # A 404 console message does not name the file, so the URL has to come
        # off the response itself to tell a pending asset from a real break.
        page.on(
            "response",
            lambda r: bad_urls.append(r.url) if r.status >= 400 else None,
        )

        async def shot(route: str) -> None:
            errors.clear()
            bad_urls.clear()
            await page.goto(BASE + route, wait_until="networkidle")
            await page.wait_for_timeout(700)
            name = route.strip("/").replace("/", "-") or "landing"
            await page.screenshot(path=OUT / f"{name}.png", full_page=True)

            body = await page.inner_text("body")
            if len(body.strip()) < 40:
                failures.append(f"{route}: page is empty")
            # A React crash leaves the root blank; console errors catch the rest.
            real = [e for e in errors if "favicon" not in e.lower()]

            # The landing page ships with the dealer's Yukon photo, which is not
            # in the repo yet. Surface it loudly instead of failing the gate --
            # delete PENDING_ASSETS entries as the files arrive.
            missing = [u for u in bad_urls if any(a in u for a in PENDING_ASSETS)]
            if missing and len(missing) == len(bad_urls):
                # Every failed request was an expected-missing asset, so the
                # generic "failed to load resource" noise is accounted for.
                real = [e for e in real if "failed to load resource" not in e.lower()]
                for url in dict.fromkeys(missing):
                    print(f"       PENDING ASSET not supplied yet: {url.rsplit('/', 1)[-1]}")
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
