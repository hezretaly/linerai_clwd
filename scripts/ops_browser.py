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
import tempfile
import time
from datetime import datetime, timedelta, timezone

import httpx
from playwright.sync_api import Error as PlaywrightError, sync_playwright

BASE = "http://localhost:5173"
API = "http://localhost:8000"
SHOTS = pathlib.Path(".artifacts/ops")
SHOTS.mkdir(parents=True, exist_ok=True)

#: The composer's body. A rich-text editor is a `contenteditable`, not a
#: `<textarea>`, and Playwright's `fill` types into either -- replacing what
#: was there, which on a reply is the quoted message.
EDITOR = '[contenteditable="true"]'

#: The file the copies-and-files step attaches. A fixed name, because the
#: picker's row and the reader's list are both found by it.
ATTACHMENT = "ops-check.txt"

#: Every address this script ever sends to. The reply in step 11 answers
#: whichever unmatched message is on top, which is a smoke fixture -- so that
#: one accumulated a row a run too, from before sends were recorded at all.
RUN_ADDRESSES = (
    "draft.check@example.invalid",
    "first.contact@example.invalid",
    "copies.check@example.invalid",
    "nobody@nowhere.invalid",
    "stranger@nowhere.invalid",
)

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


def _clear_run_mail(started: datetime) -> int:
    """Delete the ops_messages rows this run composed, and what hangs off them.

    Straight at the database rather than through an endpoint, because there is
    no delete endpoint and there should not be: Trash is a timestamp precisely
    so nobody can destroy a message somebody wrote. A test clearing up after
    itself is a different act from a person binning their mail.

    **By address, and by what this run wrote to a test domain.** The fixed
    addresses are the ones this script types. The reply in step 11 goes to
    whoever is on top of Unmatched, which is whichever smoke fixture arrived
    last -- `stranger.<stamp>@nowhere.invalid`, `no-reply@billing.example` --
    and no list can name those in advance, so each run used to leave that
    one behind. What the founder wrote during this run to an RFC 2606 domain
    is this run's by definition: nobody's real mail goes to `.invalid`.

    **Children first.** A message's envelope and its files point at it, and
    the database refuses to delete a row something still points at. A file
    this run uploaded and never sent -- a run that failed between the pick
    and the send -- goes too, rather than waiting out the server's own
    clear-up of stale uploads.
    """
    sys.path.insert(0, "backend")
    # Liner's own database: `ops_messages` moved out of the stores, so a
    # dealership session no longer carries the table.
    from sqlalchemy import and_, false, or_
    from app.db import ops_session
    from app.models import OpsMailAttachment, OpsMailEnvelope, OpsMessage, OpsUser

    with ops_session() as db:
        me = db.query(OpsUser).filter(OpsUser.email == "founder@linerai.us").one_or_none()
        written_now = (
            and_(
                OpsMessage.author_id == me.id,
                OpsMessage.created_at >= started,
                or_(
                    OpsMessage.to_address.like("%.invalid"),
                    OpsMessage.to_address.like("%.example"),
                ),
            )
            if me is not None
            else false()
        )
        rows = (
            db.query(OpsMessage)
            .filter(or_(OpsMessage.to_address.in_(RUN_ADDRESSES), written_now))
            .all()
        )
        ids = [row.id for row in rows]
        if ids:
            db.query(OpsMailAttachment).filter(
                OpsMailAttachment.message_id.in_(ids)
            ).delete(synchronize_session=False)
            db.query(OpsMailEnvelope).filter(
                OpsMailEnvelope.message_id.in_(ids)
            ).delete(synchronize_session=False)
        if me is not None:
            db.query(OpsMailAttachment).filter(
                OpsMailAttachment.message_id.is_(None),
                OpsMailAttachment.uploaded_by == me.id,
                OpsMailAttachment.created_at >= started,
            ).delete(synchronize_session=False)
        for row in rows:
            db.delete(row)
        db.commit()
        return len(rows)


def main() -> int:
    client = httpx.Client(base_url=API, timeout=20)
    # Naive UTC like every stored timestamp, a little early so a row written
    # in the first second of the run is not missed by the clear-up.
    started = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=5)
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
            page.fill("input[type=email]", "marcus.vale@riversideauto.example")
            page.fill("input[type=password]", "liner-dev")
            page.click("button[type=submit]")
            page.wait_for_url("**/app", timeout=15000)
            # The same door check, the dealership's side: a rep who opens the
            # login page again is already in, and gets their dashboard rather
            # than a form prefilled with somebody's address.
            #
            # `goto` is allowed to be *interrupted* here, because the
            # interruption is the assertion. The `['me']` answer is already in
            # the query cache by this point, so the redirect renders on the
            # first paint rather than after a fetch, and Playwright reports
            # "Navigation to /login is interrupted by another navigation to
            # /app" -- which is the redirect working, arriving one tick sooner
            # than it used to. `wait_for_url` below is what actually checks it.
            try:
                page.goto(f"{BASE}/login")
            except PlaywrightError as exc:
                assert "interrupted by another navigation" in str(exc), exc
            page.wait_for_url("**/app", timeout=10000)
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

            say("and an owner who opens their own sign-in again is already in")
            # The fourth door, and the one that was missing: /login had no
            # session check at all, so a signed-in owner got the form. Asked
            # per door -- bare /login is the dealership's, and an owner asking
            # for that one has not signed in to it, which is what the sidebar's
            # "Dealership sign-in" link is for.
            page.goto(f"{BASE}/login?as=owner")
            page.wait_for_url("**/ops", timeout=10000)
            page.goto(f"{BASE}/login")
            page.wait_for_selector("input[type=password]", timeout=8000)
            assert "/login" in page.url, f"the dealership door bounced an owner: {page.url}"

            say("founder signs in and lands on /ops")
            page.evaluate('async () => { await fetch("/api/auth/logout", {method: "POST"}) }')
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
            page.click('[role=dialog] button:text-is("Close")')

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
            # Escape closes it -- measured, no dialog and no overlay left. There
            # used to be a `mouse.click(700, 400)` after this to make sure, and
            # a blind coordinate is a time bomb on a calendar that fills up: once
            # enough demos had accumulated it landed *on* one, opened its detail
            # dialog, and the overlay then blocked every nav link for the rest of
            # the run. The failure named the mail tab, three sections away.
            page.wait_for_selector("[role=dialog]", state="detached", timeout=5000)

            say("the inbox lists forms and unmatched mail, and the counts add up")
            page.click('a[href="/ops/mail"]')
            page.wait_for_selector("text=Inbox", timeout=10000)
            page.wait_for_selector("text=Unmatched", timeout=5000)
            page.screenshot(path=SHOTS / "06-inbox.png", full_page=True)
            counts = client.get("/api/ops/mail?box=all", cookies=owner_cookies(client)).json()
            assert counts["counts"]["all"] == len(counts["messages"]), "a box says one thing and shows another"

            say("unmatched mail is labelled as matching nobody")
            page.click('button:text-is("Unmatched")')
            page.wait_for_timeout(800)
            page.locator("ul li button").first.click()
            page.wait_for_selector("text=Matched nobody", timeout=10000)
            page.screenshot(path=SHOTS / "07-unmatched.png")

            say("a reply says what the sender really did, not just that it worked")
            # `:text-is`, not `:has-text`, on every control in this file.
            # `has-text` is a case-insensitive *substring* over the whole
            # subtree, so `button:has-text("Reply")` matched five message rows
            # from `no-reply@billing.example` before it matched the Reply
            # button, and clicked one of those. Every label here is exact and
            # every one of them is also an ordinary English word that turns up
            # in somebody's mail -- send, close, write, sent.
            page.click('button:text-is("Reply")')
            # The editor opens on the quoted message; `fill` replaces it.
            page.fill(EDITOR, "Checking the composer path.")
            page.click('button:text-is("Send")')
            page.wait_for_selector("text=Not delivered", timeout=10000)
            print(f"      {page.locator('text=Not delivered').first.inner_text()}")
            page.screenshot(path=SHOTS / "08-reply-outbox.png")

            say("and a first message can be written to somebody who never wrote in")
            # Reply could only answer an existing message, so reaching a
            # dealership we want to talk to meant leaving for a mail client --
            # where the send is invisible to this system for good and goes out
            # under whatever address that client is configured with.
            page.click('button:text-is("Close")')       # one composer at a time
            page.wait_for_timeout(400)
            page.click('button:text-is("Write")')
            page.wait_for_selector("text=New message", timeout=5000)
            fields = page.locator("input")
            assert fields.count() >= 2, "the composer should offer To and Subject"
            assert fields.first.input_value() == "", (
                "Write opened prefilled -- this is a first message, not a reply"
            )
            fields.first.fill("first.contact@example.invalid")
            fields.nth(1).fill("About Liner")
            page.fill(EDITOR, "Reaching out about a demo.")
            page.click('button:text-is("Send")')
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
            page.click('button:text-is("Write")')
            page.wait_for_selector("text=New message", timeout=5000)
            fields = page.locator("input")
            fields.first.fill("draft.check@example.invalid")
            fields.nth(1).fill("Half a thought")
            page.fill(EDITOR, "Started this, will finish later.")
            page.click('button:text-is("Save draft")')
            page.wait_for_selector("text=Draft kept", timeout=8000)
            page.wait_for_timeout(1200)   # the sidebar counts refetch after the save
            assert box_count("Drafts") == drafts_before + 1, "the draft should be kept"
            page.click('button:text-is("Send")')
            page.wait_for_selector("text=Not delivered", timeout=10000)
            page.wait_for_timeout(1000)
            # One message a person wrote must not become two rows in two boxes.
            assert box_count("Drafts") == drafts_before, "sending should empty the draft"
            assert box_count("Sent") == sent_before + 1, "and it should land in Sent"
            page.screenshot(path=SHOTS / "08c-draft-sent.png", full_page=True)

            say("trash keeps what you put in it, and restore puts it back")
            page.click('button:text-is("Sent")')
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
            assert page.locator('button:text-is("Restore")').count(), (
                "trash without restore is a delete wearing a friendlier word"
            )
            page.get_by_role("button", name="Restore", exact=True).click()
            page.wait_for_timeout(1200)
            assert box_count("Trash") == trash_before, "restore should put it back"

            say("a message carries a copy and a file, and Reply answers who it went to")
            # Cc is behind a link *after* Subject, so To and Subject stay the
            # first two fields every step above finds them as; the file goes
            # through the picker's own input, exactly as a person's pick does.
            page.click('button:text-is("Write")')
            page.wait_for_selector("text=New message", timeout=5000)
            fields = page.locator("input")
            assert fields.first.input_value() == "", "Write opened prefilled"
            fields.first.fill("copies.check@example.invalid")
            fields.nth(1).fill("Two people and a file")
            page.click('button:text-is("Cc")')
            page.locator('input[aria-label="Cc"]').fill("second.person@example.invalid")
            page.fill(EDITOR, "One for you both, with a file.")
            with tempfile.TemporaryDirectory() as scratch:
                upload = pathlib.Path(scratch) / ATTACHMENT
                upload.write_text("A small file the ops browser check attaches.\n")
                page.locator('input[type="file"]').first.set_input_files(str(upload))
                # Uploaded when it is picked, not when the message is sent --
                # so Send pressed before its row appears goes without it.
                page.wait_for_selector(f'[aria-label="Remove {ATTACHMENT}"]', timeout=10000)
            page.click('button:text-is("Send")')
            page.wait_for_selector("text=Not delivered", timeout=10000)
            page.screenshot(path=SHOTS / "08d-copies-and-file.png", full_page=True)
            # One composer at a time: the reply below opens its own, and two
            # on the page would be two To boxes.
            page.click('button:text-is("Close")')
            page.wait_for_timeout(400)

            page.click('button:text-is("Sent")')
            # Waited for rather than slept on: the box may draw what it held
            # before the send for a moment while it refetches.
            page.wait_for_selector(
                'ul li:first-child button:has-text("copies.check@example.invalid")',
                timeout=10000,
            )
            row = page.locator("ul li button").first
            listed = row.inner_text()
            assert "copies.check@example.invalid" in listed, (
                f"the newest Sent row should be the message just sent: {listed!r}"
            )
            # The row is one line per message however many people and files
            # it carries, so it has to say so itself.
            assert row.locator('[aria-label="1 attached file"]').count() == 1, (
                f"the Sent row should say it carries a file: {listed!r}"
            )
            assert "+1" in listed, f"and that somebody else was on it: {listed!r}"

            row.click()
            page.wait_for_selector(f"text={ATTACHMENT}", timeout=10000)
            # The reply to a message *of ours* goes to the people it went to.
            # It used to be addressed to the row's sender -- which on a Sent
            # message is us, so answering a thread we started wrote to
            # ourselves.
            page.click('button:text-is("Reply")')
            to_box = page.get_by_role("group", name="To", exact=True)
            to_box.wait_for(timeout=5000)
            addressed = to_box.inner_text()
            assert "copies.check@example.invalid" in addressed, (
                f"Reply on a Sent message should go to its recipient: {addressed!r}"
            )
            for ours in ("founder@linerai.us", "cto@linerai.us"):
                assert ours not in addressed, f"Reply on a Sent message wrote to us: {addressed!r}"
            page.click('button:text-is("Close")')
            page.wait_for_timeout(300)
            # Reply all keeps the Cc -- and is only offered because there is one.
            page.click('button:text-is("Reply all")')
            page.wait_for_selector(
                '[aria-label="Remove second.person@example.invalid"]', timeout=5000
            )
            page.screenshot(path=SHOTS / "08e-reply-all.png", full_page=True)
            page.click('button:text-is("Close")')

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
        # it, and sends three more, so without this Drafts and Sent grow with
        # every run -- and the
        # counts these very assertions read drift further from a fresh
        # database each time, which is how a check starts failing for a reason
        # that has nothing to do with the change being tested.
        binned = _clear_run_mail(started)
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
