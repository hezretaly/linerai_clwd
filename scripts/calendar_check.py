"""Drive the calendar's two views, and the waiting-time format -- `make cal-ui`.

Two things no HTTP check can reach. The list view's count and its rows have to
come from one predicate, which is only visible by counting what is actually on
screen -- they were two predicates once, and the header said 16 over a list of
147. And `waited()` is a formatter: `1349h 36m` is a perfectly correct number
that no person can read, which only a rendered page can tell you.

Read-only. It books nothing and cancels nothing, so it needs no teardown and
can be run against any database.
"""

import glob
import re

from playwright.sync_api import sync_playwright
def cp():
    for pat in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome",
                "/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell"):
        f = sorted(glob.glob(pat))
        if f: return f[-1]
STOPWATCH = re.compile(r"\d{3,}h \d{2}m")
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=cp(), args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    p = ctx.new_page()
    p.on("pageerror", lambda e: print(f"  [pageerror] {e}"))
    p.goto("http://localhost:5173/login")
    p.fill("input[type=email]", "dana.mercer@example.invalid")
    p.fill("input[type=password]", "liner-dev")
    p.click("button[type=submit]")
    p.wait_for_url("**/app", timeout=15000)
    p.wait_for_timeout(2500)

    print("1. no unreadable stopwatch anywhere on the overview")
    body = p.locator("body").inner_text()
    bad = STOPWATCH.findall(body)
    assert not bad, f"still showing {bad[:3]}"
    ages = sorted(set(re.findall(r"\b\d+d\b", body)))[:6]
    print(f"   day-scale readings now: {ages}")
    p.screenshot(path=".artifacts/waited-overview.png", full_page=True)

    print("2. the calendar opens on the week grid")
    p.goto("http://localhost:5173/app/calendar")
    p.wait_for_selector("h1:has-text(\"Calendar\")", timeout=10000)
    p.wait_for_timeout(1200)
    assert p.locator('button[aria-pressed="true"]:has-text("week")').count() == 1

    print("3. switching to the list shows every booking in order")
    p.click('button:has-text("list")')
    p.wait_for_selector("text=in the order it happens", timeout=8000)
    p.wait_for_timeout(900)
    times = p.locator("ul li button:visible .tnum").all_inner_texts()
    print(f"   {len(times)} rows, first three: {times[:3]}")
    assert len(times) > 1
    p.screenshot(path=".artifacts/calendar-list.png", full_page=True)

    print("4. the count above the list is the number of rows in it")
    header = p.locator("text=still to come").first.inner_text()
    stated = int(header.split()[0])
    print(f"   header says {stated}, list renders {len(times)}")
    assert stated == len(times), f"{stated} stated vs {len(times)} rendered"

    print("5. each toggle says what it would add, and adds exactly that")
    for label in ("past", "cancelled"):
        button = p.locator(f'button:has-text("Show ") >> text=/Show \\d+ {label}/')
        if not button.count():
            continue
        offered = int(button.first.inner_text().split()[1])
        before = len(p.locator("ul li button:visible .tnum").all_inner_texts())
        button.first.click()
        p.wait_for_timeout(900)
        after = len(p.locator("ul li button:visible .tnum").all_inner_texts())
        assert after == before + offered, f"{label}: offered {offered}, {before} -> {after}"
        now_stated = int(p.locator("p.tnum").first.inner_text().split()[0])
        assert now_stated == after, f"{label}: header {now_stated} vs {after} rows"
        print(f"   {label}: offered {offered}, {before} -> {after}, header agrees")
        p.click(f'button:has-text("Hide {label}")')
        p.wait_for_timeout(600)

    print("6. a row opens the same detail drawer the grid uses")
    p.locator("ul li button").first.click()
    p.wait_for_selector("[role=dialog]", timeout=8000)
    p.wait_for_timeout(600)
    p.screenshot(path=".artifacts/calendar-list-open.png")
    p.click('[role=dialog] button:has-text("Close")')

    print("7. the choice is remembered across a reload")
    p.reload()
    p.wait_for_selector("text=in the order it happens", timeout=10000)

    print("8. 390px: no sideways scroll in either view")
    phone = b.new_context(viewport={"width": 390, "height": 844},
                          storage_state=ctx.storage_state()).new_page()
    for mode in ("list", "week"):
        phone.goto("http://localhost:5173/app/calendar")
        phone.wait_for_timeout(1500)
        if phone.locator(f'button[aria-pressed="false"]:has-text("{mode}")').count():
            phone.click(f'button:has-text("{mode}")')
            phone.wait_for_timeout(1000)
        over = phone.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert over <= 0, f"{mode} overflows by {over}px"
        phone.screenshot(path=f".artifacts/calendar-{mode}-phone.png", full_page=True)
        print(f"   {mode}: 0px overflow")
    b.close()
print("\nCALENDAR PASS")
