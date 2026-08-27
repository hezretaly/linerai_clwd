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
import re
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


def _clear_run_mail() -> int:
    """Delete the ops_messages rows this run composed, by their fixed address.

    Straight at the database rather than through an endpoint, because there is
    no delete endpoint and there should not be: Trash is a timestamp precisely
    so nobody can destroy a message somebody wrote. A test clearing up after
    itself is a different act from a person binning their mail.
    """
    sys.path.insert(0, "backend")
    from app.db import SessionLocal
    from app.models import OpsMessage

    with SessionLocal() as db:
        rows = (
            db.query(OpsMessage)
            # Every address this script ever sends to. The reply in step 11
            # answers whichever unmatched message is on top, which is a smoke
            # fixture -- so that one accumulated a row a run too, from before
            # sends were recorded at all.
            .filter(OpsMessage.to_address.in_((
                "draft.check@example.invalid",
                "first.contact@example.invalid",
                "nobody@nowhere.invalid",
                "stranger@nowhere.invalid",
            )))
            .all()
        )
        for row in rows:
            db.delete(row)
        db.commit()
        return len(rows)


def main() -> int:
    client = httpx.Client(base_url=API, timeout=20)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=chromium_path(), args=["--no-sandbox"]
            )
            page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
            page.on("pageerror", lambda e: print(f"      [pageerror] {e}"))

            say("a stranger is sent to the sign-in this dashboard uses")
            anon = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
            anon.goto(f"{BASE}/ops")
            anon.wait_for_url("**/login?as=owner", timeout=10000)
            anon.close()

            say("a rep is sent there too, and told their session is intact")
            page.goto(f"{BASE}/login")
            page.fill("input[type=email]", "marcus.vale@example.invalid")
            page.fill("input[type=password]", "liner-dev")
            page.click("button[type=submit]")
            page.wait_for_url("**/app", timeout=15000)
            page.goto(f"{BASE}/ops")
            # The login form, not a dead end -- but a login form arriving
            # unannounced reads as "your session expired", and theirs has not.
            page.wait_for_url("**/login?as=owner&why=ops", timeout=10000)
            page.wait_for_selector("text=has not gone anywhere", timeout=8000)
            still = page.evaluate('async () => (await fetch("/api/auth/me")).status')
            assert still == 200, f"the dealer session was flushed: /api/auth/me {still}"
            page.screenshot(path=SHOTS / "01-not-ours.png")

            say("and the wall runs the other way: an owner on /app goes to /ops")
            # Not a flush. An ops session is refused by every dealership
            # endpoint, so without this the shell rendered and every panel in
            # it 403'd -- broken rather than "this is not yours".
            page.goto(f"{BASE}/login?as=owner")
            page.fill("input[type=password]", "liner-dev")
            page.click("button[type=submit]")
            page.wait_for_url("**/ops", timeout=15000)
            page.goto(f"{BASE}/app")
            page.wait_for_url("**/ops", timeout=10000)
            still = page.evaluate('async () => (await fetch("/api/auth/me")).status')
            assert still == 200, f"the ops session was flushed: /api/auth/me {still}"

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

            say("and a first message can be written to somebody who never wrote in")
            # Reply could only answer an existing message, so reaching a
            # dealership we want to talk to meant leaving for a mail client --
            # where the send is invisible to this system for good and goes out
            # under whatever address that client is configured with.
            page.click('button:has-text("Close")')       # one composer at a time
            page.wait_for_timeout(400)
            page.click('button:has-text("Write")')
            page.wait_for_selector("text=New message", timeout=5000)
            fields = page.locator("input")
            assert fields.count() >= 2, "the composer should offer To and Subject"
            assert fields.first.input_value() == "", (
                "Write opened prefilled -- this is a first message, not a reply"
            )
            fields.first.fill("first.contact@example.invalid")
            fields.nth(1).fill("About Liner")
            page.fill("textarea", "Reaching out about a demo.")
            page.click('button:has-text("Send")')
            page.wait_for_selector("text=Not delivered", timeout=10000)
            page.screenshot(path=SHOTS / "08b-write-outbox.png")

            say("mail arrives unread, and can be put back")
            # This shipped hardcoded read -- `inbound_emails` had no column
            # for it and there is no Alembic here -- so the one box holding
            # mail from strangers was the one that could never tell you which
            # of it was new. The mark lives in its own table now, which a
            # database that already exists does get.
            def box_count(label):
                import re as _re
                text = page.get_by_role(
                    "button", name=_re.compile(rf"^{label}\s")
                ).first.inner_text()
                digits = [t for t in text.split() if t.isdigit()]
                return int(digits[-1]) if digits else 0

            # The Unread box itself, so the row picked is certainly unread --
            # an earlier step in this run has already opened one of the others.
            page.get_by_role("button", name=re.compile(r"^Unread\s")).first.click()
            page.wait_for_timeout(1000)
            unread_before = box_count("Unread")
            page.locator("ul li button").first.click()
            page.wait_for_timeout(1200)
            assert box_count("Unread") == unread_before - 1, (
                "opening a message should read it"
            )
            page.get_by_role("button", name="Mark unread", exact=True).click()
            page.wait_for_timeout(1200)
            assert box_count("Unread") == unread_before, (
                "marking unread should put it back -- an inbox is a queue"
            )
            print(f"      unread {unread_before} -> read -> {box_count('Unread')}")

            say("a draft is kept, and sending moves it rather than copying it")
            drafts_before, sent_before = box_count("Drafts"), box_count("Sent")
            page.click('button:has-text("Write")')
            page.wait_for_selector("text=New message", timeout=5000)
            fields = page.locator("input")
            fields.first.fill("draft.check@example.invalid")
            fields.nth(1).fill("Half a thought")
            page.fill("textarea", "Started this, will finish later.")
            page.click('button:has-text("Save draft")')
            page.wait_for_selector("text=Draft kept", timeout=8000)
            page.wait_for_timeout(1200)   # the sidebar counts refetch after the save
            assert box_count("Drafts") == drafts_before + 1, "the draft should be kept"
            page.click('button:has-text("Send")')
            page.wait_for_selector("text=Not delivered", timeout=10000)
            page.wait_for_timeout(1000)
            # One message a person wrote must not become two rows in two boxes.
            assert box_count("Drafts") == drafts_before, "sending should empty the draft"
            assert box_count("Sent") == sent_before + 1, "and it should land in Sent"
            page.screenshot(path=SHOTS / "08c-draft-sent.png", full_page=True)

            say("trash keeps what you put in it, and restore puts it back")
            page.click('button:has-text("Sent")')
            page.wait_for_timeout(1000)
            page.locator("ul li button").first.click()
            page.wait_for_timeout(800)
            trash_before = box_count("Trash")
            # The sidebar box is also called Trash and comes first in the DOM.
            page.get_by_role("button", name="Trash", exact=True).click()
            page.wait_for_timeout(1200)
            assert box_count("Trash") == trash_before + 1, "trashing should bin it"
            page.get_by_role("button", name=re.compile(r"^Trash\s")).first.click()
            page.wait_for_timeout(1000)
            page.locator("ul li button").first.click()
            page.wait_for_timeout(800)
            assert page.locator('button:has-text("Restore")').count(), (
                "trash without restore is a delete wearing a friendlier word"
            )
            page.get_by_role("button", name="Restore", exact=True).click()
            page.wait_for_timeout(1200)
            assert box_count("Trash") == trash_before, "restore should put it back"

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
        # And the mail this run wrote. Every run composes a draft and sends
        # it, so without this Drafts and Sent grow by two a run -- and the
        # counts these very assertions read drift further from a fresh
        # database each time, which is how a check starts failing for a reason
        # that has nothing to do with the change being tested.
        binned = _clear_run_mail()
        if binned:
            print(f"Removed {binned} message(s) this run composed.")
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
