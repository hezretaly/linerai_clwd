"""Drive /ops in a browser -- `make ops-ui`.

The one thing on this system that no HTTP check can assert: a notification
that goes away when it is read and does not come back. `make smoke` proves the
row moves `new -> seen`; only a browser can prove the badge falls, the toast
takes itself off screen, and a reload does not replay either of them -- which
is the failure this page exists to avoid, and the one that only shows up on
the *second* page load.

Self-sufficient: it books its own demos through the public endpoint rather
than relying on rows the seed happens to leave unread, and cancels them in a
`finally` so a second run behaves like the first. Same reason `make smoke`
gives its appointment slots back.
"""
from __future__ import annotations

import glob
import pathlib
import sys
import time

import httpx
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173"
API = "http://localhost:8000"
SHOTS = pathlib.Path(".artifacts/ops")
SHOTS.mkdir(parents=True, exist_ok=True)

step = 0
made: list[str] = []


def chromium_path():
    for pattern in (
        "/opt/pw-browsers/chromium-*/chrome-linux/chrome",
        "/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell",
    ):
        found = sorted(glob.glob(pattern))
        if found:
            return found[-1]
    return None


def say(msg: str) -> None:
    global step
    step += 1
    print(f"  {step:2d}. {msg}", flush=True)


def book(client: httpx.Client, name: str, dealership: str) -> str:
    days = client.get("/api/demo/slots").json()["days"]
    day = next(d for d in days if d["slots"])
    response = client.post("/api/demo/requests", json={
        "name": name, "dealership": dealership,
        "email": f"{name.split()[0].lower()}@opstest.invalid",
        "phone": "555-0199", "dealership_url": "https://opstest.invalid",
        "slot": f'{day["date"]}T{day["slots"][0]}:00', "consent": True,
    })
    response.raise_for_status()
    request_id = response.json()["id"]
    made.append(request_id)
    return request_id


def badge(page) -> int:
    label = page.locator('button[aria-label^="Notifications"]').get_attribute("aria-label")
    return int(label.split("(")[1].rstrip(")"))


def wait_for_badge(page, want: int) -> None:
    for _ in range(60):
        if badge(page) == want:
            return
        time.sleep(0.25)
    raise AssertionError(f"badge stuck at {badge(page)}, wanted {want}")


def main() -> int:
    client = httpx.Client(base_url=API, timeout=20)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=chromium_path(), args=["--no-sandbox"]
            )
            page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
            page.on("pageerror", lambda e: print(f"      [pageerror] {e}"))

            say("a rep cannot get in")
            page.goto(f"{BASE}/login")
            page.fill("input[type=email]", "marcus.vale@example.invalid")
            page.fill("input[type=password]", "liner-dev")
            page.click("button[type=submit]")
            page.wait_for_url("**/app", timeout=15000)
            page.goto(f"{BASE}/ops")
            page.wait_for_selector("text=This one is ours", timeout=10000)
            page.screenshot(path=SHOTS / "01-not-ours.png")

            say("founder signs in and lands on /ops")
            page.goto(f"{BASE}/login?as=owner")
            assert page.input_value("input[type=email]") == "founder@linerai.us"
            page.fill("input[type=password]", "liner-dev")
            page.click("button[type=submit]")
            page.wait_for_url("**/ops", timeout=15000)
            page.wait_for_selector("text=Demo calendar", timeout=10000)
            page.screenshot(path=SHOTS / "02-calendar.png", full_page=True)

            start = badge(page)
            say(f"a booking pops a toast without a reload (badge at {start})")
            book(client, "Browser Check", "Ops Test Motors")
            page.wait_for_selector("text=New demo booked", timeout=15000)
            page.wait_for_selector("text=Ops Test Motors", timeout=5000)
            wait_for_badge(page, start + 1)
            page.screenshot(path=SHOTS / "03-toast.png")

            say("the toast opens the entry, and takes both itself and the badge away")
            page.click("text=Open it")
            page.wait_for_selector("[role=dialog]", timeout=10000)
            page.wait_for_selector("text=Consent", timeout=5000)
            assert "Ops Test Motors" in page.locator("[role=dialog]").inner_text()
            assert page.locator("text=New demo booked").count() == 0, "toast survived the click"
            wait_for_badge(page, start)
            page.screenshot(path=SHOTS / "04-detail.png")
            page.click('[role=dialog] button:has-text("Close")')

            say("and it stays away across a reload -- read is a state, not a session")
            page.reload()
            page.wait_for_selector("text=Demo calendar", timeout=10000)
            wait_for_badge(page, start)
            assert page.locator("text=New demo booked").count() == 0, "replay re-popped a toast"

            say("the bell lists what is genuinely unopened")
            book(client, "Second Check", "Second Test Motors")
            wait_for_badge(page, start + 1)
            bell = page.locator('button[aria-label^="Notifications"]')
            bell.click()
            page.wait_for_selector("text=Second Test Motors", timeout=5000)
            page.screenshot(path=SHOTS / "05-bell.png")
            page.locator("text=Clear all").count()  # only shown for more than one
            page.keyboard.press("Escape")
            page.mouse.click(700, 400)

            say("the inbox lists forms and unmatched mail, and the counts add up")
            page.click('a[href="/ops/mail"]')
            page.wait_for_selector("text=Inbox", timeout=10000)
            page.wait_for_selector("text=Unmatched", timeout=5000)
            page.screenshot(path=SHOTS / "06-inbox.png", full_page=True)
            counts = client.get("/api/ops/mail?box=all", cookies=owner_cookies(client)).json()
            assert counts["counts"]["all"] == len(counts["messages"]), "a box says one thing and shows another"

            say("unmatched mail is labelled as matching nobody")
            page.click('button:has-text("Unmatched")')
            page.wait_for_timeout(800)
            page.locator("ul li button").first.click()
            page.wait_for_selector("text=Matched nobody", timeout=10000)
            page.screenshot(path=SHOTS / "07-unmatched.png")

            say("a reply says what the sender really did, not just that it worked")
            page.click('button:has-text("Reply")')
            page.fill("textarea", "Checking the composer path.")
            page.click('button:has-text("Send")')
            page.wait_for_selector("text=Not delivered", timeout=10000)
            print(f"      {page.locator('text=Not delivered').first.inner_text()}")
            page.screenshot(path=SHOTS / "08-reply-outbox.png")

            say("390px: neither page scrolls sideways")
            phone = browser.new_context(
                viewport={"width": 390, "height": 844},
                storage_state=page.context.storage_state(),
            ).new_page()
            for path, name in (("/ops", "09-phone-calendar"), ("/ops/mail", "10-phone-inbox")):
                phone.goto(f"{BASE}{path}")
                phone.wait_for_timeout(1500)
                over = phone.evaluate(
                    "() => document.documentElement.scrollWidth"
                    " - document.documentElement.clientWidth"
                )
                assert over <= 0, f"{path} overflows by {over}px"
                phone.screenshot(path=SHOTS / f"{name}.png", full_page=True)

            browser.close()
    finally:
        # Give the slots back, the same reason smoke and accept do.
        for request_id in made:
            client.post(f"/api/demo/requests/{request_id}/cancel")
        if made:
            print(f"\nCancelled {len(made)} demo request(s) held by this run.")
        client.close()

    print("\nOPS BROWSER PASS")
    return 0


def owner_cookies(client: httpx.Client) -> dict:
    response = client.post("/api/auth/login", json={
        "email": "founder@linerai.us", "password": "liner-dev",
    })
    response.raise_for_status()
    return dict(response.cookies)


if __name__ == "__main__":
    sys.exit(main())
