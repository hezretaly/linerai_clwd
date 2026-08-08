#!/usr/bin/env python3
"""Drive a booking through the actual browser UI, with the dashboard open.

smoke.py proves the API works. This proves the two windows work together --
buyer taps rail chips on the left, the dealer overview updates on the right off
the WebSocket, with nobody reloading anything.
"""

from __future__ import annotations

import asyncio
import sys

from screenshots import OUT, chromium_path

BASE = "http://127.0.0.1:5173"


async def main() -> int:
    from playwright.async_api import async_playwright

    OUT.mkdir(exist_ok=True)
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=chromium_path(), args=["--no-sandbox"]
        )

        # Right-hand window: the dealer dashboard, signed in and left open.
        dealer_ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        dealer = await dealer_ctx.new_page()
        await dealer.goto(f"{BASE}/login", wait_until="networkidle")
        await dealer.fill('input[type="email"]', "dana.mercer@example.invalid")
        await dealer.fill('input[type="password"]', "liner-dev")
        await dealer.click('button[type="submit"]')
        await dealer.wait_for_url("**/app", timeout=10_000)
        await dealer.wait_for_timeout(1200)

        before = await dealer.inner_text("body")
        appointments_before = kpi(before, "Appointments set")
        print(f"\ndashboard open. Appointments set = {appointments_before}")

        # Left-hand window: a buyer, tapping chips only.
        buyer_ctx = await browser.new_context(viewport={"width": 480, "height": 900})
        buyer = await buyer_ctx.new_page()
        await buyer.goto(f"{BASE}/chat", wait_until="networkidle")
        await buyer.wait_for_timeout(800)

        print("\nbuyer taps through:")
        for label in ["third row", "Tell me about", "see it this week"]:
            await tap(buyer, label)
            print(f"  tapped: {label}")

        # The booking card: a day, a time, then the contact fields. This is the
        # path a buyer actually takes now -- typing a time still works, but the
        # card is what check_availability puts in front of them.
        await buyer.wait_for_selector("text=Pick a day", timeout=15000)
        await buyer.locator("section button").nth(1).click()
        await buyer.wait_for_timeout(400)
        await buyer.locator("section button.rounded-full").first.click()
        await buyer.wait_for_timeout(400)
        check("the card asks for details only after a time is picked",
              await buyer.locator('input[type="email"]').count() == 1)

        await buyer.fill('section input[type="text"]', "Priya Sharma")
        await buyer.fill('input[type="email"]', "priya.sharma@example.com")
        await buyer.fill('input[type="tel"]', "555-0148")
        await buyer.click('button:has-text("Book it")')
        await buyer.wait_for_timeout(3500)

        transcript = await buyer.inner_text("body")
        check("liner confirmed a booking", "booked in" in transcript, last_line(transcript))
        check("it quoted a real price", "$" in transcript)
        await buyer.screenshot(path=OUT / "e2e-buyer.png", full_page=True)

        # The dashboard should have moved on its own.
        await dealer.wait_for_timeout(2500)
        after = await dealer.inner_text("body")
        appointments_after = kpi(after, "Appointments set")
        check(
            "dashboard updated without a reload",
            appointments_after > appointments_before,
            f"{appointments_before} -> {appointments_after}",
        )
        check("the new buyer appears in a queue", "Priya Sharma" in after)
        await dealer.screenshot(path=OUT / "e2e-dealer.png", full_page=True)

        await browser.close()

    print()
    if failures:
        print(f"FAILED: {', '.join(failures)}")
        return 1
    print("Booking completed through the UI and the dashboard reacted live.")
    return 0


async def tap(page, label: str) -> None:
    await page.click(f'button:has-text("{label}")')
    await page.wait_for_timeout(2500)


def kpi(body: str, label: str) -> int:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line == label and index + 1 < len(lines) and lines[index + 1].isdigit():
            return int(lines[index + 1])
    return -1


def last_line(body: str) -> str:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return lines[-1][:80] if lines else ""


if __name__ == "__main__":
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    sys.exit(asyncio.run(main()))
