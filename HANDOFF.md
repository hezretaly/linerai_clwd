# Handoff

Read this first. It is written to be sufficient on its own — you should not need
any prior conversation to pick this up.

Start with `CLAUDE.md` for commands and conventions. This file covers **current
state, decisions that look arbitrary but aren't, and what's next.**

---

## What this is

An AI assistant for one car dealership. A buyer chats, Liner searches real
inventory, qualifies them and books an appointment; the dealer dashboard handles
it from there.

**The rule the codebase is built around: narrow, not fake.** Scope is cut hard —
one dealership, seeded inventory — but everything that exists is real. Where an
external dependency is missing, the feature reports itself unavailable rather
than simulating a result. Do not "fill in" a gap with a fake; say what's missing.

## Run it

```bash
make install && make reset-db && make dev
```

<http://localhost:5173> · sign in at `/login` with
`dana.mercer@riversideauto.example` / `liner-dev`. **No `.env` is needed.**

## State — all green as of the last commit

| | |
|---|---|
| Backend | Complete. 17 tables, six agent tools, guards, WebSocket event bus, Act 2 actions, inventory ingest. |
| Frontend | shadcn classic theme. Eight dashboard pages, buyer chat, voice placeholder. |
| `/` | The real marketing landing page, byte-for-byte as supplied. |
| `make smoke` | 41 checks including WebSocket assertions. **The gate.** |
| `make shots` | 13 routes at desktop and at 390px, no console errors, no horizontal overflow. |
| `make e2e` | Two browser windows: buyer books, dashboard KPI moves live. |

## Decisions that look arbitrary and get "tidied" — don't

**`@theme inline` is load-bearing.** In `frontend/src/styles/liner-theme.css`,
colours live on `:root` and reach Tailwind through `@theme inline`. Moving them
into a plain `@theme {}` block compiles them to *static values*, which silently
kills the `.theme-buyer` scope — buyer pages would stop being blue with no error
anywhere. The file says this too.

**Dealer vs buyer palette.** `/app/*` and `/login` run shadcn classic unmodified
(near-black primary, light sidebar). `/chat` and `/call` get `.theme-buyer`
(`components/BuyerTheme.tsx`), which overrides *only* the accent family so they
keep the iOS blue while every structural token still comes from classic.

**`warning` and `success` are a deliberate extension.** Classic ships only
`destructive`. A dealer has to tell confirmed from unconfirmed at a glance on the
calendar, so these exist on purpose.

**`/` is a static document, not a React route.** `frontend/landing.html` is
served at the root by a small Vite plugin (`landingAtRoot` in `vite.config.ts`).
It is *not* ported to React because its JS is written for a page load — a
`while(true)` loop, self-rescheduling animations, an interval and scroll
listeners, none with teardown. Inside a React route those leak and double-fire
under StrictMode. The SPA owns `/chat`, `/call`, `/login`, `/app/*`.

The SPA catch-all does `window.location.replace('/')`, **not** `<Navigate>` —
with no `/` route, a client-side navigate re-enters the catch-all forever.

**Naive timestamps are dealership-local**, not UTC-with-conversion.
`check_availability` builds slots straight from `hours_json` in that frame. Never
hardcode an hour; `_next_open_slot` in `seed.py` exists because a hardcoded 9 PM
produced an appointment the calendar could not draw.

**Rules live in executors, not prompts.** A do-not-discuss vehicle is filtered in
`search_inventory` so it never reaches the model. Provenance is enforced in
`save_captured_fields`, which downgrades a dishonest `typed` to `inferred`. A
prompt is a request; an executor is a guarantee.

**Guards run in every `LLM_MODE`, stub included.** If a stubbed turn can slip an
unsourced price past them, the guard has a hole — that should fail offline.

**`provenance='adf'` is a fifth value only the lead importer can write.** The
agent tool's enum is the four conversational ones, so `save_captured_fields`
cannot claim it — a field marked `adf` provably came from a document a dealer
uploaded. It counts as *verified*: the buyer did state it, just on a
marketplace's form rather than to us. Only `inferred` is a guess.

**Lead-level outreach needed no migration.** `outreach.appointment_id` was
already nullable, so a lead with no appointment gets a real `outreach` row. The
draft is built server-side from the lead's actual state (`_lead_draft` in
`api/lead_import.py`) — a booked visit produces a reminder naming the slot,
everyone else a first touch that only says a car is "still here" when it is
genuinely `status='available'` and not `rule_discuss=False`.

**Production routing lives in `app/static.py`, not in nginx.** The rule that
`/` is the landing document and `/chat` `/call` `/login` `/app/*` are the SPA
existed only in the Vite dev plugin, which does not run in production. Writing
it into a web-server config as well gives you two copies that drift, and the
drift is silent — `/` serves the SPA, whose catch-all bounces to `/`, and the
page is blank with nothing in any log. The API serves the built frontend itself;
nginx has one `proxy_pass` and no `try_files`. See `docs/DEPLOY.md`.

**OpenAI goes through the Responses API, not chat completions.** The default
model is a gpt-5.x *reasoning* model, and that rules the older endpoint out
twice over: reasoning models reject `max_tokens` outright (it is
`max_output_tokens` now), and function tools combined with a reasoning effort
are restricted on `/v1/chat/completions` for models in that family. So tool
definitions are flat, calls come back as `function_call` items, and results go
back as `function_call_output`. Do not "simplify" it to chat completions.

**Reasoning tokens come out of the reply's budget.** Too low an
`OPENAI_MAX_OUTPUT_TOKENS` returns an *empty* message, not a short one. The
provider logs a warning naming both settings when that happens rather than
letting it read as the model having nothing to say.

**The live transcript is not a list of dicts.** Entries the loop builds are
plain dicts, but a vendor's own turn goes back verbatim — and a reasoning model
returns `ResponseReasoningItem` objects, which are pydantic models with no
`.get()`. Those items have to be passed back (they carry the reasoning
context), so use `_role_of()` in `loop.py` rather than assuming a dict.

**The scraper is deferred, by decision.** No adapter for a real dealer site
will be written here — you are supplying the scraper and its output. The
contract it should hit is the CSV importer, which is already the working
inventory path:

* **Required:** `vin` (17 valid characters), `make`, `model`.
* **Expected:** `year` (or `model_year`), `price` (or `list_price`,
  `asking_price`), `mileage` (or `odometer`, `miles`), `body_style`, `seats`,
  `trim`, `features` (`;`-separated), `status`.
* **Refused:** `acquisition_cost`, `dealer_cost`, `invoice`, `margin`,
  `profit`, `sale_price`, `salesperson_*`. Dropped before a row exists — see
  `NEVER_IMPORT` in `ingest/csv_import.py`.
* Rows not clearly `available` are skipped and counted, not offered.
* A feature the body style cannot have is dropped and counted; the vehicle is
  kept.

Post it to `POST /api/ingest/csv` or hand the file to `/app/inventory/import`.
Both go through review-then-publish, so nothing reaches the live table without
a person looking at the diff.

## Bugs already fixed — don't reintroduce

- **Four booking bugs, all the same shape: Liner confirming a time the buyer
  never asked for.** A named period ignored when the day matched; a named day
  ignored entirely when no offered slot fell on it; an email at `slot_offered`
  re-offering slots instead of booking; the fallback re-querying availability so
  it could book a time never put in front of the buyer. A named day or time is
  now a *request* — if it can't be met from the offered pair, Liner goes and
  looks. See `_pick_offered_slot` in `agent/stub.py`.
- **`emit()` needs an explicit main-loop handle.** Sync endpoints run in a
  threadpool where `asyncio.get_running_loop()` raises, so events were reaching
  the database and never reaching a dashboard. `events.bind_loop()` is called at
  startup for this reason.
- **Seeded appointments outside business hours** were invisible on the calendar
  and would be rejected by `book_appointment`. Seed times now derive from
  `hours_json`.
- **"The first one" resolved to the wrong car.** `conversations.last_results_json`
  records what the buyer was actually shown, in order.
- **`.get()` on a reasoning item crashed every live turn.** The API returned
  200, the model answered, and then the loop treated `response.output` as a
  list of dicts. It read as a broken integration when the integration was
  working perfectly. The fake provider now emits a non-dict transcript entry,
  because a double that only produces tidy dicts is easier to write and lets
  exactly this reach production.
- **A failed agent turn killed the SSE stream silently.** The response has
  already started by then, so raising cannot become a 503 — FastAPI says
  "response already started" and the buyer watches a typing indicator that
  never resolves. `chat.py` now emits an `error` event naming the missing
  setting, and the buyer chat renders it.
- **Unknown tool arguments were silently ignored.** A model calling
  `search_inventory(need=...)` instead of `keywords=...` got the five cheapest
  cars and quoted a price off one the buyer never asked about. `tools.execute`
  now rejects any argument the schema does not declare, so the model is told
  and retries.
- **`api.upload()` sent multipart with `Content-Type: application/json`.** The
  `request()` wrapper set that header whenever a body existed, which strips the
  boundary only the browser knows and makes FastAPI see a body with no parts
  (422). Both file importers were affected. `request()` now leaves the header
  off for a `FormData` body.

## What is real and what is not

`make placeholders` regenerates `docs/PLACEHOLDERS.md`; `/api/integrations`
returns it live and drives the amber banner in the dashboard.

| Thing | State |
|---|---|
| Agent | **Stub by default; unscripted with a key.** `LLM_MODE=live` + `OPENAI_API_KEY` puts a real model on the same six tools and the same guards. Defaults to `gpt-5.4-nano` over the **Responses API** — it is a reasoning model, so chat completions is the wrong endpoint (see below). The HTTP call itself has never run here; everything either side of it is checked by `make agent-check`. |
| Email | **Outbox.** A real `outreach` row, mirrored into the buyer's chat thread. Sends nothing. `GmailSender` written and unverified. |
| Voice | **Not configured, and not faked.** Session mint returns a typed 503 naming the missing keys. Tool relay and transcript endpoints are real and tested. A scripted transcript would look like it worked while proving nothing about latency, barge-in or audio. |
| Scraper | **Works, against the fixture site** (`make fixture-site`). Real HTTP, real JSON-LD parsing, real diff/publish, manual edits survive re-ingest. No adapter for a real dealer site — that needs real URLs. |
| Lead import | **Real, end to end.** ADF/XML parsed with `defusedxml`, matched against inventory and existing leads, reviewed, then committed. Nothing is *fetched*: ADF normally arrives by email to a lead inbox or by HTTP POST from the marketplace, and neither is configured — you upload the document. |
| Reminders | **Manual, and said so on the page.** No scheduler exists here, so a reminder is a draft a rep sends. Nothing runs on a timer. |

### Known gaps

- **`live_inv_car.png`** — the Yukon photo the landing page references. Not
  supplied. Drop it in `frontend/public/`. `make shots` names it rather than
  hiding it; remove it from `PENDING_ASSETS` in `scripts/screenshots.py` once it
  lands.
- **Google Fonts** is unreachable from the sandbox's headless Chromium only
  (`curl` gets 200 through the agent proxy). Affects screenshots, not real
  visitors. Listed in `UNREACHABLE_HOSTS` in the same file.
- `react-router-dom` 7.18.2 carries one open advisory (GHSA-qwww-vcr4-c8h2, RSC
  mode CSRF). This is a client-only SPA with no RSC, so the path isn't
  reachable. npm's suggested "fix" is 7.11.0, which trades it for fourteen worse
  ones including an unauthenticated RCE. Staying on 7.18.2 is deliberate.

## GitHub access is split

The REST token reports `permissions.push: false` and the MCP GitHub write path
fails with *"Resource not accessible by integration"*. The **git transport** uses
a different credential and works fine. Practically:

- **Use plain git for anything that writes code** — commit, branch, push. That's
  the working path.
- **Expect API-mediated actions to fail**: opening PRs programmatically, posting
  issue comments, creating releases, touching workflow files.
- **For a PR**, push the branch and hand over a pre-filled compare URL to click,
  rather than trying to create it via the API.

Note: `claude/liner-ai-implementation-8xehez` was the first branch pushed to an
empty remote, so it is the default branch. A PR only becomes meaningful once a
separate base branch exists.

**The working tree rolled back once mid-session**, losing a whole commit from git
*and* disk. Commit in small steps and push after each meaningful chunk.

## The website chat — built, first for Alsbou

One tag on the dealer's own site, `docs/WIDGET.md` end to end:
`<script src="https://linerai.us/embed.js" data-dealer="alsbou" async>`.

**What it does:**

- It draws a bubble in a shadow root.
- On first click it frames the real chat from `/widget/<dealer>`.
- It reads its settings from `/<dealer>/api/widget/config` on every load, so
  nothing on their site is edited twice.
- It keeps the conversation id on *their* domain.
- It tells the chat which car the page is about. The server decides by the
  page's address against `listing_url` first.
- It pushes ASC-named events to their Tag Manager, and never anything
  personal.
- It reports another chat product on the page.

The Website chat card on Liner setup has the tag, the switch and what each
site has reported.

**Open, and the dealer's call:**

- Alsbou's Get My Auto site keeps Capital One's *Chat Concierge* in its
  `chatbox` slot. The loader reports it; it never removes it.
- Their `embed_origins` already lists both spellings of their host.

**Not built from the old mockup (`dash/hastead_motors.html`).** The chat
opens straight into the conversation, with no home menu of action cards and
no proactive nudge bubble. Two rules still stand if that menu is ever built:

- "Value my trade" has nothing behind it. There is no valuation, so it
  answers from the trade-in `knowledge_entries` row and offers to book.
- "Jordan is on call until 11" is invented staffing. Nothing records who is
  on shift.

## Next — the new server (agreed plan, in order)

**Alsbou only, for now** (the user, mid-way through: "we don't have to
implement the other stores yet. only alsbou"). `linerai.us` keeps ops and every
group that has not moved; Craig and Landreth and Riverside stay there at their
paths. **No new migrations**: the new server's databases are built from the
latest schema, and the machinery is only for a later change to a table that
already holds real rows.

**Alsbou's website chat needs none of the new server.** Give Get My Auto
`<script src="https://linerai.us/alsbou/embed.js" async></script>` (their
`snippets` list; their `chatbox` slot holds Capital One's Chat Concierge, and
replacing that is Alsbou's call). The path form works on every loader from
`7014d59` on, and after the move too, because the old box's redirect covers
`/alsbou/...`. The full spec -- page and VIN awareness, the session on their
domain, Tag Manager events, the other-chat check -- is commit `21558e0`, which
predates Postgres and migrations. **Never `git pull` on linerai.us**: that
brings this branch's head, which migrates every database at boot and cannot be
downgraded. Pin the commit instead, in this order:

1. Record the running commit (`git -C /srv/liner log -1`) and back up:
   `make dump-ops`, and `sqlite3 <file> ".backup ..."` for `backend/liner.db`,
   `backend/var/stores/*.db` and `backend/var/ops.db`.
2. If the box predates the ops split (`git merge-base --is-ancestor 8ea16b5
   HEAD` fails, or there is no `backend/var/ops.db`), `make dump-ops` before
   and `make restore-ops FILE=...` after, or founder@/cto@ cannot sign in.
3. `.env` must have a real `TWILIO_AUTH_TOKEN` (`openssl rand -hex 32` if the
   phone line is not used): production refuses to boot without one.
4. `git fetch origin && git checkout --detach 21558e0`, then `make build`
   (it installs the new dependencies) and restart.
5. Check: `/alsbou/api/widget/config?origin=https://www.alsboucars.com` and
   `?origin=https://alsboucars.com` both say `allowed` and `enabled`;
   `/widget/alsbou` sends `frame-ancestors` naming both; nginx adds no
   `X-Frame-Options`; `https://linerai.us/alsbou/chat` answers live.
6. Paste the tag into a live alsboucars.com page from the browser's devtools
   first -- only that browser sees it -- then hand it to Get My Auto. Tag last:
   a pre-`21558e0` bubble on their site has no off switch.

`21558e0` does **not** carry the Sunday-hours fix or the loader's `greeted`
fix (a console error on the dealer's page on first open); both are on this
branch's head only. A release ref -- `21558e0` plus those two hunks and
nothing else -- is the clean way to give linerai.us them without migrations.

Also before launch, and not code: the other chat (Capital One's Chat
Concierge) has to come out of their `chatbox` slot for the spec's "no other
chat" to hold; their Tag Manager owner has to add a GA4 tag on the `asc_`
events for the lead events to show anywhere; and Alsbou's lot is a CSV
snapshot -- a fresh export imported at `/app/inventory` keeps it true, and a
car missing from it is marked sold by hand. The domain lock is a browser
control (the frame and the config); the chat API itself is public and rate
limited, not locked to alsboucars.com.

1. **Subdomain routing — done.** `STORE_DOMAIN=linerai.us` makes the
   Host header pick the store, as the path prefix does
   (`stores.host_store`, `stores.public_link`).
2. **Postgres, one database per group — done.** `DATABASE_URL_TEMPLATE`,
   `app/pg.py`, and `make to-postgres` for the copy. The full `make smoke`
   passes against Postgres 16 with every checkout database copied in; each
   audit blocker is fixed and written up in CLAUDE.md under "What differs
   between the two engines".
3. **Alembic across every database — done.** `app/migrate.py`, a store and an
   ops history, migrated at boot and by `make migrate`. Pre-migration
   databases are adopted in place. The gate fails on a model changed without
   a revision.
4. **Deploy — done.** `docs/DEPLOY.md`, *Several dealer groups on their own
   server*: DNS and an Origin CA wildcard certificate in Cloudflare, Postgres,
   the `.env`, `make migrate ARGS=--create`, the copy from SQLite, the
   cut-over in an order that takes nothing down, and the rollback.
   `deploy/liner-groups.nginx.conf` is the new box (one wildcard block; a name
   that is not ours gets its connection closed); `deploy/liner-groups-moved.conf`
   goes on the box keeping `linerai.us` and redirects a moved group's every
   old address, a tag already on a dealer's site included. The Worker's
   `ROUTES` sends Alsbou's mail to the new box; both files name Alsbou alone. All of it was run: nginx
   1.24 in front of the app on Postgres, sign-in, the dealer socket, the chat
   stream, and an old tag followed by a browser from a dealer's page to a chat
   on the subdomain. That run found two bugs, both fixed and gated: the
   widget's settings could not be read after a redirect (`Origin: null`), and
   the printed `pg_dump` lines named the database in a form libpq misreads.
5. **Rooftops as locations inside a group — parked** on branch
   `claude/rooftops-parked`, complete and gated there (it carries migration
   0003). Not needed until Craig and Landreth move; when they do, it still
   wants the street address and hours of Clarksville and Bullitt County from
   Austin.
6. **Two fixes kept from it:** the prompt's opening hours (Alsbou's Sunday
   closing at six read as eight to the assistant on every version so far), and
   adopting a pre-migration database from the baseline rather than the models.

## Next task — port the dashboard mockups

The seven dashboard pages were built from written descriptions, not from the
actual mockups, so they are functional but almost certainly don't match the
intended layouts.

**How to send them:** push to `mockups/` on any branch —

```
mockups/
  overview.html  conversations.html  leads.html  calendar.html
  inventory.html  assistant.html  team.html
  liner-theme.css
  conv-data.js  leads-data.js  cal-data.js  inv-data.js  setup-data.js
```

Then `git fetch origin` and read them off disk **one file at a time**. Do not
paste them into the conversation — that is what crowded the context last time.

**Default approach unless overridden:** layout, hierarchy, spacing and component
composition come from the mockups; colour, radius and typography keep coming from
the classic token layer. No component reintroduces a hardcoded hex.

**Do overview first, screenshot it, and confirm the split reads right** before
spending the other six.

Known reconciliations needed: the mockups hardcode hex in their JS, disagree with
themselves on badge counts, and carry a hardcoded `TODAY = '2026-07-31'`. Counts
come from `/api/overview` and hours from `hours_json` — those stay. The `*-data.js`
files are the most useful artefact: they are a considered guess at the API
contract, so where one disagrees with `app/schemas/serialize.py`, that's a real
finding to raise rather than paper over.

## Verifying any change

`make smoke` is the gate and must stay green. `make e2e` when you touch the
frontend or the event path. `make shots` and **actually read the screenshots** —
a layout change is visual and exit codes prove nothing about it.

There is no pytest suite and no Playwright suite, deliberately.
