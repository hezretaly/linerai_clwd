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
`dana.mercer@example.invalid` / `liner-dev`. **No `.env` is needed.**

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
| Agent | **Stub by default** — a state machine over `conversations.stage` calling the real tools and writing the real rows, building replies from tool results. `agent/loop.py` holds the live Anthropic loop: **written, never executed**, no key available. |
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

## Embeddable chat widget — agreed, not built yet

`dash/hastead_motors.html` is a fictional dealer site carrying the widget design:
a launcher FAB with badge, a proactive nudge bubble, and a panel with two
screens — a home menu of action cards, and the conversation itself. ~150 CSS
rules, all `.lnr`-prefixed.

**Decisions taken:**

- **Ship it as a script tag from day one**, injecting into a **Shadow DOM** so
  the host page's CSS cannot leak in and the widget's cannot leak out. Today it
  is same-origin, so it just calls `/api/chat/*` directly and needs no CORS. When
  a real dealer's site needs it later, the same artifact works with an absolute
  URL plus their origin in `ALLOWED_ORIGINS` — no rewrite.
- **Host the copied dealer site ourselves at `/demo`**, the same way `/` serves
  the landing page: a standalone document via the `landingAtRoot` plugin pattern
  in `vite.config.ts`. We will not have access to a real dealer's site.

**What the home actions can honestly do.** Three map to real tools —
"Check a vehicle" → `search_inventory`, "Book a test drive" → `book_appointment`,
"Talk to a person" → `escalate_to_human`. **"Value my trade" has nothing behind
it**: there is no valuation anywhere in this system, no VIN lookup, no number to
give. It answers from the real trade-in `knowledge_entries` row ("bring it in,
we appraise while you wait") and offers to book. Do not simulate a figure.

Likewise the mockup's "Jordan is on call until 11" is invented staffing —
availability comes from `hours_json`, and no table records who is on shift.

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
