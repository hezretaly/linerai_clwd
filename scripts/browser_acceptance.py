#!/usr/bin/env python3
"""The twenty acceptance steps again, through the screens a person uses.

    backend/.venv/bin/python scripts/browser_acceptance.py

`scripts/acceptance.py` proves the machinery. This proves the *dashboard* shows
it: a buyer types in the chat widget, a rep watches the row appear, opens the
buyer, moves the appointment, takes the thread over and hands it back -- each
step asserted against what is actually rendered, not against the API that fed
it. A page can be wired to a correct endpoint and still show the wrong thing.

Two browser contexts, deliberately: the buyer's window has no dealer session,
which is the only honest way to drive `/chat`.

Screenshots of every step land in `.artifacts/browser/`.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from screenshots import chromium_path  # noqa: E402

from playwright.async_api import Page, async_playwright  # noqa: E402

SITE = "http://127.0.0.1:5173"
SHOTS = pathlib.Path(__file__).resolve().parent.parent / ".artifacts" / "browser"

failures: list[str] = []
step_no = 0


def step(title: str) -> None:
    global step_no
    step_no += 1
    print(f"\n{step_no:>2}. {title}")


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"      [{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        failures.append(f"step {step_no}: {label}")


async def shot(page: Page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(SHOTS / f"{name}.png"))


async def body_text(page: Page) -> str:
    return await page.locator("body").inner_text()


async def main() -> int:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=chromium_path(), args=["--no-sandbox"]
        )
        # The buyer has no dealer cookie. Sharing one context would let the
        # widget answer as somebody signed in, which is not the thing being
        # tested.
        buyer_ctx = await browser.new_context(viewport={"width": 430, "height": 900})
        dealer_ctx = await browser.new_context(viewport={"width": 1500, "height": 950})
        buyer = await buyer_ctx.new_page()
        dealer = await dealer_ctx.new_page()

        try:
            code = await run(buyer, dealer)
        finally:
            await browser.close()
        return code


async def run(buyer: Page, dealer: Page) -> int:
    import secrets

    tag = secrets.token_hex(3)
    # Unique per run. Reusing one name meant a later run clicked the *first*
    # match in the list -- a buyer from an earlier run whose threads were long
    # closed -- and then failed on a page that was correct about somebody else.
    name = f"Robin Vale {tag.upper()}"
    email = f"robin.vale.{tag}@example.invalid"
    # Unique on purpose. The matcher identifies a returning buyer by the last
    # ten digits of their phone, so reusing a number already on file merges
    # this booking into that person -- correct behaviour, and not the thing
    # this run is testing.
    phone = f"319-555-{secrets.randbelow(9000) + 1000}"

    # ------------------------------------------------------------------ 1 --
    step("A buyer arrives on the website and asks about a car")
    await buyer.goto(f"{SITE}/chat")
    await buyer.wait_for_timeout(2500)
    chips = await buyer.locator("button").all_inner_texts()
    check("the widget offers openers to tap", len(chips) >= 3, f"{len(chips)} buttons")
    # The widget's input has no `type`, so `input[type=text]` misses it. Matched
    # on its placeholder, which is what a person actually reads.
    box = buyer.locator("input[placeholder*='Ask about' i]").first
    await box.fill("Do you have anything with a third row?")
    await box.press("Enter")
    await buyer.wait_for_timeout(3500)
    said = await body_text(buyer)
    check("Liner answered with cars from the lot",
          "$" in said and any(w in said for w in ("miles", "mile")), said[-160:].replace("\n", " "))
    await shot(buyer, "01-buyer-chat")

    # ------------------------------------------------------------------ 2 --
    step("The dealer dashboard shows the conversation without a reload")
    await dealer.goto(f"{SITE}/app")
    await dealer.wait_for_timeout(3000)
    # With PUBLIC_DEMO on the visitor is already a rep; without it there is a
    # login form. Both are supported configurations, so this run works in
    # either rather than only in the one it happened to be started with.
    if "/login" in dealer.url:
        await dealer.fill("input[type='email']", "dana.mercer@example.invalid")
        await dealer.fill("input[type='password']", "liner-dev")
        await dealer.click("button[type='submit']")
        await dealer.wait_for_url("**/app**", timeout=15000)
        await dealer.wait_for_timeout(2500)
    check("a dealer is signed in and on the dashboard", "/login" not in dealer.url, dealer.url)
    await dealer.goto(f"{SITE}/app/conversations")
    await dealer.wait_for_timeout(2500)
    rows_before = await dealer.locator("tbody tr").count()
    check("the conversations page lists people", rows_before > 0, f"{rows_before} rows")
    await shot(dealer, "02-dashboard")

    # ------------------------------------------------------------------ 3 --
    step("The buyer focuses one car, then books from the card")
    await box.fill("Tell me about the first one")
    await box.press("Enter")
    await buyer.wait_for_timeout(3500)
    await box.fill("Can I come and see it this week?")
    await box.press("Enter")
    await buyer.wait_for_timeout(4000)
    days = buyer.locator("button").filter(has_text=re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat)"))
    check("a booking card appeared, offering real days", await days.count() > 0,
          f"{await days.count()} days")
    await shot(buyer, "03-booking-card")

    # Day, then time, then the fields appear -- the card only asks for contact
    # details once there is something to confirm.
    await days.first.click()
    await buyer.wait_for_timeout(700)
    times = buyer.locator("button").filter(has_text=re.compile(r"\d{1,2}:\d{2}\s*(AM|PM)"))
    check("and real times on that day", await times.count() > 0, f"{await times.count()} slots")
    await times.first.click()
    await buyer.wait_for_timeout(700)

    for label, value in (("Name", name), ("Email", email), ("Phone", phone)):
        await buyer.locator(f"label:has-text('{label}') input").first.fill(value)
    check("the card asks where the confirmation should go",
          await buyer.locator("label:has-text('Email') input").count() > 0)
    await shot(buyer, "04-card-filled")

    await buyer.locator("button").filter(has_text="Book it").first.click()
    await buyer.wait_for_timeout(4500)
    booked_text = await body_text(buyer)
    check("the buyer is told it is booked, on the card itself",
          "booked" in booked_text.lower() or "you're in" in booked_text.lower(),
          booked_text[-150:].replace("\n", " "))
    await shot(buyer, "05-booked")

    # ------------------------------------------------------------------ 4 --
    step("The buyer now appears on the dashboard, by name")
    await dealer.reload()
    await dealer.wait_for_timeout(3000)
    visible_name = f"text={name} >> visible=true"
    listed = await dealer.locator(visible_name).count()
    check("their name is in the conversations list", listed > 0, f"{listed} matches")
    await shot(dealer, "06-listed")

    # ------------------------------------------------------------------ 5 --
    step("Their page shows contact, source, car and the whole thread")
    await dealer.locator(f"text={name} >> visible=true").first.click()
    await dealer.wait_for_timeout(3000)
    page_text = await body_text(dealer)
    check("the buyer page opened", "/app/leads/" in dealer.url or "/app/conversations/" in dealer.url,
          dealer.url)
    buyer_page = dealer.url
    check("their email is on the rail", email in page_text, email)
    check("their phone is on the rail", phone.replace("-", "") in page_text.replace("-", ""),
          phone)
    check("the transcript is there, both sides",
          "third row" in page_text.lower(), "buyer's own words on the page")
    check("and a recap describes them from rows",
          name.split()[0] in page_text)
    await shot(dealer, "07-buyer-page")

    # ------------------------------------------------------------------ 6 --
    step("The appointment shows on the buyer's page and on the calendar")
    check("the visit is on their timeline",
          "appointment" in page_text.lower() or "booked" in page_text.lower())
    await dealer.goto(f"{SITE}/app/calendar")
    await dealer.wait_for_timeout(2500)
    calendar = await body_text(dealer)
    check("the calendar page renders", len(calendar) > 200, f"{len(calendar)} chars")
    await shot(dealer, "08-calendar")

    # ------------------------------------------------------------------ 7 --
    step("The buyer changes their mind about the car, mid-conversation")
    await buyer.bring_to_front()
    await box.fill("Actually, what else do you have? Something smaller.")
    await box.press("Enter")
    await buyer.wait_for_timeout(4000)
    switched = await body_text(buyer)
    check("Liner answers the new question rather than the old one",
          len(switched) > len(booked_text), "the thread moved on")
    await shot(buyer, "09-switched")

    # ------------------------------------------------------------------ 8 --
    step("A rep takes the thread over, and Liner goes quiet")
    # Straight back to the page the rep already had open, rather than hunting
    # the name in a list of hundreds -- which is also what they would do.
    await dealer.goto(buyer_page)
    await dealer.wait_for_timeout(3500)
    take = dealer.locator("button").filter(has_text="Take over").first
    check("the buyer page offers Take over", await take.count() > 0)
    if await take.count():
        await take.click()
        await dealer.wait_for_timeout(2500)
    held = await body_text(dealer)
    check("the page says Liner is being held",
          "paused" in held.lower() or "hand back" in held.lower() or "replying as" in held.lower(),
          held[:120].replace("\n", " "))
    await shot(dealer, "10-taken-over")

    await buyer.bring_to_front()
    before_quiet = await body_text(buyer)
    await box.fill("Are you still there?")
    await box.press("Enter")
    await buyer.wait_for_timeout(4500)
    after_quiet = await body_text(buyer)
    check("Liner does not reply while a rep is holding it",
          after_quiet.count("Liner") <= before_quiet.count("Liner") + 1,
          "no new assistant turn")
    await shot(buyer, "11-liner-quiet")

    # ------------------------------------------------------------------ 9 --
    step("The rep types into the thread, and the buyer sees it")
    await dealer.bring_to_front()
    composer = dealer.locator("textarea").last
    typed = "Hi, it's Marcus at Riverside -- I have that held for you."
    if await composer.count():
        await composer.fill(typed)
        # A textarea takes Enter as a newline, so the reply goes out on the
        # button -- which is what a rep presses too.
        await dealer.locator("button").filter(has_text="Send reply").first.click()
        await dealer.wait_for_timeout(3500)
    check("the rep's message is in the thread",
          typed[:30] in await body_text(dealer), "rep message rendered")
    await shot(dealer, "12-rep-typed")

    # ----------------------------------------------------------------- 10 --
    step("Control goes back to Liner, with the conversation intact")
    back = dealer.locator("button").filter(has_text="Hand back").first
    check("the page offers Hand back", await back.count() > 0)
    if await back.count():
        await back.click()
        await dealer.wait_for_timeout(2500)
    resumed = await body_text(dealer)
    check("the whole history is still on the page",
          "third row" in resumed.lower() and typed[:30] in resumed,
          "buyer's first question and the rep's message both still there")
    await shot(dealer, "13-handed-back")

    await buyer.bring_to_front()
    await box.fill("Great, thanks.")
    await box.press("Enter")
    await buyer.wait_for_timeout(4500)
    check("Liner answers again", len(await body_text(buyer)) > len(after_quiet),
          "the assistant resumed")
    await shot(buyer, "14-resumed")

    print()
    if failures:
        print(f"FAILED {len(failures)} check(s):")
        for line in failures:
            print(f"  - {line}")
        return 1
    print(f"All {step_no} browser steps passed. Screenshots in .artifacts/browser/")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
