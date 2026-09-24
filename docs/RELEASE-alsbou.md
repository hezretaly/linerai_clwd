# Release: Alsbou's website chat on linerai.us

Branch `claude/alsbou-release`. It is commit `21558e0` (the website chat) with
four small fixes on top. Deploy it to the box that serves `linerai.us`
**as it runs today**: SQLite, stores under a path prefix (`/alsbou/...`),
`create_all` at boot.

This release **does not contain** anything for the new server: no subdomains,
no Postgres and no migrations. Those live on
`claude/liner-ai-implementation-8xehez` for later. The multi-lot work for
Craig and Landreth is parked on `claude/rooftops-parked`.

## Contents

- [What the buyer gets](#what-the-buyer-gets)
- [How it is built](#how-it-is-built)
- [What this release adds on top of 21558e0](#what-this-release-adds-on-top-of-21558e0)
- [Deploying it to linerai.us](#deploying-it-to-linerai-us)
- [Checking it before Alsbou's site gets the tag](#checking-it-before-alsbous-site-gets-the-tag)
- [Installing on Alsbou's site](#installing-on-alsbous-site)
- [Launch checklist that is not code](#launch-checklist-that-is-not-code)
- [Rollback](#rollback)
- [Known limits](#known-limits)
- [How it was verified](#how-it-was-verified)

## What the buyer gets

A chat bubble in the corner of every page on `www.alsboucars.com` (and the bare
`alsboucars.com`). Pressing it opens Liner, the same assistant as the
storefront at `linerai.us/alsbou`: real inventory, the booking card and the
contact card, with the same guards on prices and cars.

- **It knows which car they are looking at.** On a car page, Liner opens
  already knowing the car.
- **It keeps the conversation.** A page load or a return visit within 30 days
  picks up the same thread.
- **On a phone it takes the whole screen.**
- **It tells Alsbou's Google Tag Manager when a chat becomes a lead or a
  booking.** It uses the car industry's standard event names, and nothing
  personal is sent.
- **It reports any other chat product running on the page.** It never removes
  one.

## How it is built

Four parts. Each one is the only place its job is done.

### 1. The loader: `frontend/public/embed.js`

This is what the dealer's site loads. It is plain JavaScript with no build
step of ours on their side. It is minified when served (`minifyLoader` in
`vite.config.ts`) and cached for five minutes (`static.py`, `LOADER_MAX_AGE`).

- **Which dealer.** The loader reads it from `data-dealer`, then
  `data-store`, then `?dealer=`, then the script's own path
  (`/alsbou/embed.js`), then `window.LinerWidget.dealer`. If the tag is pasted
  twice, the second copy stands down and reports itself.
- **Settings are fetched, not pasted.** On every page load the loader asks
  `GET /alsbou/api/widget/config?origin=<page origin>` (without credentials).
  The response carries:
  - the label, the title, the side, the offset and the colour;
  - whether Tag Manager events are on;
  - the on/off switch;
  - a verdict on whether this page's origin is one of Alsbou's.

  Everything except the verdict is read from `alsbou.yaml` (`widget:`) on each
  request, and the switch is the `website_chat` runtime flag. So a change on
  Liner reaches their site within a minute or so, and nobody edits their site
  again.
- **Shadow DOM.** The bubble and the panel live in a shadow root under
  `:host { all: initial; }`. Their stylesheet cannot reach ours, and ours
  cannot touch their page.
- **The chat loads on the first click, not before.** Only then does the
  loader create the iframe of `/widget/alsbou?parent=<page origin>`. The frame
  is kept after that; a closed panel is `visibility: hidden`, so a keyboard
  cannot tab into it.
- **Page awareness.** `readPage()` sends the URL (with the fragment removed)
  and the title. `findVin()` looks for the VIN, in this order:
  1. an explicit `LinerWidget.vin`, or `meta name="liner:vin"`, or
     `data-liner-vin`;
  2. Get My Auto's `script#at-vehicle-<VIN>`;
  3. JSON-LD, but only for the car whose URL is this page;
  4. a `data-vin` carried by exactly one VIN on the page;
  5. the URL or the page text, accepted only with a valid VIN check digit.

  Navigations through `pushState` are noticed and sent as they happen.
- **The session lives on the dealer's domain.** The conversation id is kept in
  *their* `localStorage` under `liner.alsbou.conversation` for 30 days, and
  handed to the frame over a `postMessage` handshake. A frame from another
  site gets partitioned storage, which Safari clears after a week.
- **Tag Manager.** Events are pushed to their `dataLayer`, each with
  `event_owner: "liner"` and the car's `item_*` keys:

  | Event | When |
  |---|---|
  | `asc_comm_engagement` | the chat starts |
  | `asc_comm_submission` and `asc_comm_submission_sales` | a lead |
  | `asc_comm_submission_sales_appt` | a booking |
  | `asc_cta_interaction` | the finance application |

  Lead and booking are decided by the server from the database rows after each
  turn (the stream's `reached` event), never from the wording of a reply.
- **Other chats.** A table of about 36 vendors' fingerprints (Capital One Chat
  Concierge included) is matched against the page's scripts, elements and
  network requests. A match is reported to the browser console and to the
  Liner setup card.
- **Phones.** Below 640px the panel is the whole screen, the page underneath
  stops scrolling, and an open chat is not reopened on the next page.

### 2. The frame: `/widget/alsbou`

This is the real chat bundle (`Chat.tsx`), not a second chat client, so the
booking card, the contact card and every guard are the storefront's own.

- `stores.py` treats `widget` as a reserved first segment and sets the store
  from the second.
- `static.py` serves the frame, and 404s for a dealer that does not exist.
- `lib/widgetBridge.ts` is the frame's half of the handshake. It pins every
  message to the parent's origin. If no loader answers within 1.5 seconds, the
  chat carries on as the plain `/chat`.

### 3. The server: `backend/app/api/widget.py` and `backend/app/page_context.py`

- `GET /api/widget/config` returns the settings and the verdict described
  above. It is readable cross-origin, so a refusal is a readable sentence in
  the dealer's console rather than an opaque error.
- `POST /api/widget/install-report` records, per site:
  - which origin loaded the tag, and when;
  - the last page seen;
  - the loader version;
  - whether Tag Manager is present;
  - any other chat heard on the page.

  It answers 403 for an origin that is not one of Alsbou's. Rows go to
  `widget_installs`, a new table made by `create_all` at boot.
- `page_context.record()` turns the reported page into a car.
  1. It keeps the page only if the page is on one of Alsbou's sites (or one of
     ours).
  2. It matches the URL path against each car's `listing_url`. Alsbou's
     export carries one per car, so this is the rung that works on their
     pages.
  3. Failing that, it uses the VIN.
  4. Either way, a car is accepted only through `tools.offerable`, so a sold
     or do-not-discuss car is never given.

  The model is told the car's identity and **never** its price. Prices come
  only from tools. Rows go to `conversation_pages`, another new table.

### 4. Who may frame it: `static.py`

`/widget/...` is sent with `Content-Security-Policy: frame-ancestors 'self'
https://www.alsboucars.com https://alsboucars.com`, taken from `embed_origins`
in `alsbou.yaml`. A browser refuses to draw the chat inside any other site.

## What this release adds on top of `21558e0`

| # | Change | Where | Why |
|---|---|---|---|
| 1 | **Opening hours grouped only where days agree.** `hours_sentence()` replaces the line that printed the first open day's hours for the whole week. | `backend/app/agent/prompts.py` | The assistant was told Alsbou is "Open Monday-Sunday, 10:00 to 20:00". They close at **18:00 on Sunday**, so "are you open Sunday evening?" got a wrong yes. It now reads "Open Monday-Saturday 10:00 to 20:00, Sunday 10:00 to 18:00." Booking times were always right, because `check_availability` reads the hours itself; only this sentence was wrong. The chat, the voice line and email all use it. |
| 2 | **The loader waits for the chat to say hello** before posting anything to it (the `greeted` flag). | `frontend/public/embed.js` | Until then the frame is still a blank document with the *dealer's* origin, so a message pinned to ours was refused with an error in Alsbou's own browser console on every first open. Nothing was lost, because the `init` message carries the page and the open state, but an error blamed on our tag is the first thing their web team would see. |
| 3 | **The Liner setup card hands out the path form**: `https://linerai.us/alsbou/embed.js`, still with `data-dealer`. | `backend/app/api/widget.py` (`installs`), `frontend/src/components/WebsiteChat.tsx` | See the next section. It is the one tag that works on every loader this box has served, and after a future move to `alsbou.linerai.us`, because the old box redirects `/alsbou/...` but not a bare `/embed.js`. |
| 4 | **`make shots` takes the desktop pictures again.** | `scripts/screenshots.py` | The widget check reused the variable that decides desktop or phone, so every later desktop picture was saved as a phone one and then overwritten. Test tooling only; nothing a buyer sees. |
| 5 | **The development server serves `/<dealer>/embed.js`.** | `frontend/vite.config.ts` | Production serves the loader under any store's prefix, but Vite served it only at `/embed.js` and answered the card's own tag with the dashboard's HTML. Development only. |

`make smoke` gains three checks:
- the hours sentence;
- the loader posting nothing before the hello;
- the card's tag naming the dealer in its path.

They run on every box. They sit ahead of the stores section, which skips
everything after it when fewer than two stores are seeded.

Nothing here changes the schema, a setting, a dependency or a boot check.

## Deploying it to linerai.us

**Never `git pull`.** On this box a pull brings whichever branch is checked
out; a checkout of `claude/liner-ai-implementation-8xehez` would run
migrations at boot, and they cannot be undone. Check out this branch by name.

1. **Record the current state and back it up.**
   ```bash
   cd /srv/liner
   sudo -u liner git log -1 --oneline            # write this down: the way back
   sudo -u liner git status --short              # any local edits? keep a copy
   sudo -u liner make dump-ops
   sudo apt install sqlite3                      # if it is not there
   for f in backend/liner.db backend/var/stores/*.db backend/var/ops.db; do
     [ -f "$f" ] && sudo -u liner sqlite3 "$f" ".backup $f.before-release"
   done
   ```
2. **Check Liner's own sign-ins will survive.** If the box's commit predates
   the split of Liner's own tables into `ops.db` (commit `8ea16b5`), then:
   - run `make dump-ops` before the upgrade (step 1 did);
   - run `make restore-ops FILE=<that dump>` after it, then restart.

   Otherwise `founder@` and `cto@` cannot sign in to `/ops`. Test with
   `git merge-base --is-ancestor 8ea16b5 HEAD`, or by whether
   `backend/var/ops.db` exists.
3. **Check `.env`.** Production refuses to boot on a development default of:
   - `SESSION_SECRET`;
   - `WEBHOOK_SECRET`;
   - `TWILIO_AUTH_TOKEN`;
   - the manager, rep, founder and cto passwords.

   `TWILIO_AUTH_TOKEN` arrived with the phone line (`00a68a0`). A box that
   already serves `/alsbou/` has it; if not, set it to `openssl rand -hex 32`.
   Nothing else in `.env` changes.
4. **Switch, build, restart.**
   ```bash
   sudo -u liner git fetch origin
   sudo -u liner git checkout --detach origin/claude/alsbou-release
   sudo -u liner make build              # installs nh3 and the new npm packages, then builds
   sudo systemctl restart liner
   journalctl -u liner -n 50             # no RuntimeError; "Liner is serving ..."
   curl -s localhost:8000/api/health
   ```
   `make build` needs `uv` and `npm` on the box, which it already uses. Boot
   runs `create_all`, which adds the two new tables (`widget_installs`,
   `conversation_pages`) and changes no existing one. Nothing at boot deletes
   anything.

## Checking it before Alsbou's site gets the tag

1. **Endpoints**, from anywhere:
   ```bash
   curl -sI https://linerai.us/alsbou/embed.js | grep -i cache-control      # public, max-age=300
   curl -s 'https://linerai.us/alsbou/api/widget/config?origin=https://www.alsboucars.com' | head -c 300
   curl -s 'https://linerai.us/alsbou/api/widget/config?origin=https://alsboucars.com'     | head -c 300
   curl -sI https://linerai.us/widget/alsbou | grep -i 'frame-ancestors\|x-frame-options'
   ```
   - Both configs should say `"allowed": true` and `"enabled": true`.
   - The frame should list both Alsbou origins in `frame-ancestors`.
   - There must be **no** `X-Frame-Options`. An nginx or Cloudflare rule adding
     one would blank the chat on their site.
   - No Cloudflare "Cache Everything" rule may cover `/alsbou/api/widget/`,
     because the answer depends on which site asked.
2. **The assistant is live:** open `https://linerai.us/alsbou/chat` and ask
   something. `?diagnostics=1` shows a banner if it is the scripted stand-in.
3. **Liner setup → Website chat.** It should show:
   - the tag, in the path form;
   - the switch **On**;
   - both `alsboucars.com` spellings listed.
4. **On their real site, from your own browser only.** Open a car page on
   `https://www.alsboucars.com` and, in devtools, run:
   ```js
   var s = document.createElement('script'); s.src = 'https://linerai.us/alsbou/embed.js'; document.body.appendChild(s)
   ```
   Then confirm:
   - the console says `[liner] Alsbou Motors chat ready`;
   - the bubble appears and the chat opens;
   - asking "is this one still available?" names the car on the page;
   - the setup card shows a row for `www.alsboucars.com`, with any other chat
     it heard.

   Nothing is installed; a reload removes it. Repeat on the bare host.

## Installing on Alsbou's site

Give Get My Auto exactly this line, for every page, just before `</body>`:

```html
<script src="https://linerai.us/alsbou/embed.js" async></script>
```

- **Get My Auto** has a `snippets` list for scripts like this one. Their
  `chatbox` slot holds Capital One's Chat Concierge. Putting Liner in that slot
  instead replaces it, which is Alsbou's decision (see the checklist).
- **Through Google Tag Manager** instead: a *Custom HTML* tag with the same
  line, firing on *All Pages*.
- **Why this form and not `https://linerai.us/embed.js` with
  `data-dealer="alsbou"`:**
  - the first loader this box served (`7014d59`) read the dealer only from the
    path;
  - a future move to `alsbou.linerai.us` redirects `/alsbou/...` and not a
    bare `/embed.js`.

  The path form works in every case.
- **If their platform refuses scripts**, the fallback is an iframe of
  `https://linerai.us/alsbou/chat?embed=1`. It has no bubble, no page
  awareness and no Tag Manager events, and like the frame it is allowed only on
  their two domains.

## Launch checklist that is not code

- [ ] **The other chat comes off.** The spec says no other chat should run on
  the site. The loader can only report one, so Alsbou or Get My Auto has to
  take Capital One's Chat Concierge out of the `chatbox` slot, or put Liner
  there. Done when the setup card's row for each site says no other chat.
- [ ] **Their Tag Manager is set up to receive the events.** Whoever owns
  Alsbou's Tag Manager adds a GA4 Event tag on a Custom Event trigger matching
  `^asc_` and registers the parameters as custom dimensions. Check in Tag
  Manager's Preview mode that `asc_comm_engagement` and
  `asc_comm_submission_sales` arrive with `event_owner = liner`.
- [ ] **The lot is current.** Alsbou's inventory is a CSV snapshot:
  - import a fresh export at `/app/inventory` (this keeps leads);
  - mark cars that have gone as sold on the same page;
  - agree how often the export comes.

  A car missing from Liner makes the chat unsure about the page's car, and a
  sold car still listed gets offered.
- [ ] **Their finance application link is set.** On Liner setup, enter
  `https://www.alsboucars.com/financing` if it is empty. It takes effect on
  Save, and it is what the chat's application button opens.

## Rollback

In order of speed:

1. **Liner setup → Website chat → Switch off.** Within about a minute every
   page stops drawing the bubble. The tag stays on their site, doing nothing.
2. **Back to the recorded commit:**
   ```bash
   git checkout --detach <recorded commit> && make build && systemctl restart liner
   ```
   The two new tables stay behind, unused, and do no harm.
3. **Only if data went wrong:** restore the `.before-release` copies taken in
   step 1, with the service stopped.

Do not roll back to a build **before `21558e0`** while the tag is on their
site. That loader still draws a bubble, but it has no switch and none of the
page awareness, and the only way to stop it is to take the tag off their site.

## Known limits

- **The domain lock is a browser control.** The frame's `frame-ancestors`, the
  config verdict and the install report are locked to Alsbou's sites. The
  chat API (`/alsbou/api/chat/...`) is public, as it must be for the frame to
  use it; what protects the bill is the chat's rate limits.
- **Get My Auto's `at-vehicle-<VIN>` marker has not been seen on a live
  page.** Page awareness does not depend on it: the listing-URL match is the
  rung that works for Alsbou. It is still worth a look during the devtools
  test.
- **Signature images in Alsbou's outgoing email** break on this version when
  all three of these are true:
  - `PUBLIC_BASE_URL` is set;
  - Alsbou is not the box's unprefixed store;
  - a rep uploads a signature image.

  The image link leaves out `/alsbou`. It is fixed on the main branch and
  untouched here, because it is not the widget.
- **After a future move to `alsbou.linerai.us`**, `linerai.us` must redirect
  `/alsbou/...` (`deploy/liner-groups-moved.conf` on the main branch). The tag
  then loads the new server's loader, which follows the chat to its new home.

## How it was verified

- **`make smoke` on this branch**, against freshly seeded databases in a
  separate checkout: all checks pass, the two new ones included.
- **`make shots`**: every route at desktop and at 390px. The dealer-site tag
  is driven from another origin with a hostile stylesheet, the phone
  full-screen check is run, and the page must not scroll sideways.
- **The frontend type check.**
- **A read-only review of every claim here against the commits.**
- **An adversarial review of this branch's changes.** It found four problems,
  all fixed before the commit:
  - the new checks were placed where a single-store run skipped them;
  - nothing checked the card's tag;
  - development could not serve that tag;
  - the release folder's links to its tools were not ignored by git.
    Committed carelessly, they would have replaced the server's Python
    environment on checkout. This commit names its files one by one.
- **A gap the review found that predates this release:** the gate's whole
  website-chat section sits after that same early exit. It needs at least two
  seeded store files to run. This release's gate had three, so it ran.
