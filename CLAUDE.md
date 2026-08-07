# Liner AI — working notes

An AI assistant for a single car dealership. A buyer chats or calls, Liner
searches real inventory, qualifies them and books an appointment; the dealer
dashboard handles it from there.

**Current state, decisions and what's next live in [`HANDOFF.md`](./HANDOFF.md).**
Read that first if you are picking this up cold; this file is commands and
conventions only.

**The rule this codebase is built around:** narrow, not fake. Scope is cut
aggressively — one dealership, six qualification fields, seeded inventory — but
everything that exists is real. Where an external dependency is missing, the
feature reports itself as unavailable rather than simulating a result.

## Commands

| Command | What it does |
|---|---|
| `make install` | Backend venv + npm install |
| `make dev` | Both servers in the background, logs to `.logs/` |
| `make backend` / `make frontend` | One at a time, in the foreground |
| `make seed` | Rebuild the Riverside Auto fixture |
| `make reset-db` | Delete the database and reseed |
| `make set-password` | Change one account's password in place: `EMAIL=someone@...` |
| `make smoke` | **The gate.** Full flow over HTTP, plus the live loop against a fake provider |
| `make agent-check` | Just the live-loop half of the gate: tools, guards, wire format, no API key |
| `make agent-ping` | **Debugging live mode.** One real call, the vendor's error printed in full |
| `make shots` | Screenshot every route at desktop **and 390px** to `.artifacts/`; fails on horizontal overflow |
| `make e2e` | Book through two browser windows, assert the dashboard reacts |
| `make fixture-site` | Serve the scraper's fixture dealer site on :8100 |
| `make placeholders` | Regenerate `docs/PLACEHOLDERS.md` |
| `make build` | Build the frontend into `frontend/dist` (the API serves it in production) |
| `make stop` | Kill anything on 8000 / 5173 / 8100 |

Ports: backend **8000**, frontend **5173**, fixture site **8100**.
Logins: `dana.mercer@example.invalid` (manager) and `marcus.vale@example.invalid`
(rep), both `liner-dev` in development. They come from `MANAGER_PASSWORD` and
`REP_PASSWORD`; with `ENV=production` startup refuses to run until each is set
to something real and the two differ.

Deploying to a real host: **[`docs/DEPLOY.md`](./docs/DEPLOY.md)**. One process
serves the API, the WebSocket, the landing page and the SPA, so nginx needs a
single `proxy_pass`.

`make dev` kills those ports first — orphaned processes across sessions are the
most common way this gets confusing.

## Verifying a change

`make smoke` is the gate and it must stay green. It drives a real booking
through rail chips, asserts the appointment row exists, confirms it, assigns
it, sends outreach, and checks the expected events arrived on the WebSocket.

`scripts/e2e_booking.py` goes further: two browser windows, buyer on the left
tapping chips, dashboard on the right, asserting the KPI moves with no reload.
Run it when you touch the frontend or the event path. **It expects a fresh
seed** -- it asserts a name appears in a queue, and after several runs the
queues fill up and truncate. `make reset-db` first. `make smoke` has no such
requirement and must stay that way.

`make shots` also runs every dealer route at 390px and **fails the run if the
page scrolls sideways**. Reps and managers work from phones, so that is a real
break: one element wider than the viewport makes the browser shrink-to-fit the
whole document, so a single wide table renders every other element tiny. The
failure names the offending element and its width.

There is no pytest suite and no Playwright suite — deliberately (see below).

## Conventions

- **String UUID primary keys, naive UTC timestamps, no SQLite-only SQL.** The
  Postgres door stays open: a connection-string change plus a data copy.
  `events.id` is the one autoincrement integer, because the WebSocket replays
  with `?since=` and needs a monotonic cursor.
- **Naive timestamps are dealership-local**, not UTC-with-conversion.
  `check_availability` builds slots straight from `hours_json` in that frame.
  Never hardcode an hour — `_next_open_slot` in `seed.py` exists because a
  hardcoded 9 PM produced an appointment the calendar could not draw.
- **Rules live in executors, not prompts.** A do-not-discuss vehicle is filtered
  in `search_inventory`, so it never reaches the model at all. Provenance is
  enforced in `save_captured_fields`, which downgrades a dishonest `typed` to
  `inferred`. A prompt is a request; an executor is a guarantee.
- **Guards run in every `LLM_MODE`.** If a stubbed turn can slip an unsourced
  price past them, the guard has a hole — that should fail offline, not live.
- **One turn loop, many vendors.** `agent/loop.py` never names a vendor; every
  wire-format difference lives in `agent/providers.py`. A second copy of the
  loop is how one vendor quietly stops running the guards.
- **The live path is testable without a key.** `run_turn` takes an injectable
  provider, and `agent/fake_provider.py` scripts one. Tool dispatch, tool
  errors, the guard retry and the exact request body are all real in
  `make agent-check`; only the HTTP call is absent.
- **One design token layer.** `frontend/src/styles/liner-theme.css` and nowhere
  else. No component names a colour. It is shadcn/ui's classic theme, written
  out by hand because `ui.shadcn.com` is unreachable through the egress proxy.
  Two things there are load-bearing:
  - Colours live on `:root` and reach Tailwind through **`@theme inline`**.
    Moving them into a plain `@theme {}` block bakes them to static values and
    silently kills the scoping below.
  - `.theme-buyer` (applied by `components/BuyerTheme.tsx` to `/chat` and
    `/call`) overrides only the accent family, so buyer surfaces keep the iOS
    blue while every structural token still comes from classic. `/login` is a
    dealer screen and stays neutral.
  - `warning` and `success` are a deliberate extension: classic ships only
    `destructive`, and a dealer has to tell confirmed from unconfirmed at a
    glance on the calendar.
- **`/` is a static document, not a React route.** `frontend/landing.html` is
  the marketing page, served at the root by a small Vite plugin and byte-for-
  byte as supplied. It carries its own reset, palette and animation JS, none of
  which may be folded into the token layer: those loops never clean up and only
  behave because the page unloads. The SPA owns `/chat`, `/call`, `/login` and
  `/app/*`.
- **Mobile is a supported surface, not an afterthought.** Reps work from
  phones. Two rules keep it that way: a `<table>` never reflows, so any table
  either scrolls inside its own `overflow-x-auto` card or has a card layout
  below `md`; and a flex or grid child needs `min-w-0` before it will shrink
  below its content. Rep-facing pages (overview, conversations, leads,
  calendar) get designed mobile layouts -- conversations is master/detail and
  calendar is an agenda. Admin pages just have to not overflow.
- **Policy answers come from `knowledge_entries`, never from the model.**
  Trade-ins, the doc fee, deposits — the dealer wrote those, and a composed
  answer is one a buyer repeats back to a rep. `answer_from_knowledge` returns
  `found: false` rather than a near-miss, because a plausible wrong answer is
  worse than none.
- **Every count comes from `/api/overview`.** No page counts for itself.
- **Hours come from `hours_json`.** No page states its own.

## What is real and what is not

Run `make placeholders` or open `/api/integrations`. As of now:

| Thing | State |
|---|---|
| Agent | **Stub by default; unscripted when a key is set.** The stub is a state machine over `conversations.stage` assembling replies from tool results — it only answers what someone anticipated. `LLM_MODE=live` puts a real model on the same six tools and the same guards. Set `OPENAI_API_KEY`. The vendor HTTP call has never run here (no key); everything either side of it is exercised by `make agent-check`. |
| Email | **Outbox.** A real `outreach` row, mirrored into the buyer's chat thread. Sends nothing. `GmailSender` is written and unverified. |
| Voice | **Not configured.** Session mint returns a typed 503. The tool relay and transcript endpoints are real and tested. There is no fake provider — a scripted transcript would look like it worked while proving nothing about latency, barge-in or audio. |
| Scraper | **Works, against the fixture site.** Real HTTP, real JSON-LD parsing, real diff/publish. No adapter for any real dealer site exists yet — that needs real URLs. |
| Lead import | **Real, end to end.** ADF/XML is parsed with `defusedxml`, matched against inventory and existing leads, reviewed, then committed. Nothing is fetched: no lead inbox is polled and no feed is subscribed to — you upload the document. |
| Reminders | **Manual.** There is no scheduler in this system, so a follow-up or reminder is a server-built draft a rep reviews and sends. Not a drip campaign; the page says so. |

## Deliberately not built

No Alembic (`create_all` + `make reset-db`), no pytest suite, no Playwright
suite, no generated OpenAPI types, no shadcn CLI. These were scoped out on
request; `smoke.py` plus screenshots is the whole verification story. If you add
migrations later, do it before there is production data to preserve.

Also out: multi-tenancy, SMS, billing, CRM/DMS sync, scheduled ingest,
model-generated rail chips. Each is additive against the current schema.

## Don't

- Don't invent a credential or flip `LLM_MODE=live` to make something pass.
- Don't change a placeholder default to reach a real service.
- Don't edit `.env` (it is gitignored) or commit any service-account JSON.
- Don't add a simulated result to fill a gap. Say what's missing instead —
  that's the whole design.
- Don't remove `DEMO_MODE`'s allow-list check. It is what stops a rehearsal
  emailing a real prospect.
