# Liner AI — working notes

An AI assistant for a single car dealership. A buyer chats or calls, Liner
searches real inventory, qualifies them and books an appointment; the dealer
dashboard handles it from there.

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
| `make smoke` | **The gate.** Full flow over HTTP, no browser, no credentials |
| `make shots` | Screenshot every route to `.artifacts/` |
| `make fixture-site` | Serve the scraper's fixture dealer site on :8100 |
| `make placeholders` | Regenerate `docs/PLACEHOLDERS.md` |
| `make stop` | Kill anything on 8000 / 5173 / 8100 |

Ports: backend **8000**, frontend **5173**, fixture site **8100**.
Login: `dana.mercer@example.invalid` / `liner-dev`.

`make dev` kills those ports first — orphaned processes across sessions are the
most common way this gets confusing.

## Verifying a change

`make smoke` is the gate and it must stay green. It drives a real booking
through rail chips, asserts the appointment row exists, confirms it, assigns
it, sends outreach, and checks the expected events arrived on the WebSocket.

`scripts/e2e_booking.py` goes further: two browser windows, buyer on the left
tapping chips, dashboard on the right, asserting the KPI moves with no reload.
Run it when you touch the frontend or the event path.

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
- **One design token layer.** `frontend/src/styles/liner-theme.css` and nowhere
  else. No component names a colour.
- **Every count comes from `/api/overview`.** No page counts for itself.
- **Hours come from `hours_json`.** No page states its own.

## What is real and what is not

Run `make placeholders` or open `/api/integrations`. As of now:

| Thing | State |
|---|---|
| Agent | **Stub by default.** A state machine over `conversations.stage` that calls the real tools and writes the real rows, building replies from tool results. `agent/loop.py` holds the live Anthropic loop — written, never executed, no key here. |
| Email | **Outbox.** A real `outreach` row, mirrored into the buyer's chat thread. Sends nothing. `GmailSender` is written and unverified. |
| Voice | **Not configured.** Session mint returns a typed 503. The tool relay and transcript endpoints are real and tested. There is no fake provider — a scripted transcript would look like it worked while proving nothing about latency, barge-in or audio. |
| Scraper | **Works, against the fixture site.** Real HTTP, real JSON-LD parsing, real diff/publish. No adapter for any real dealer site exists yet — that needs real URLs. |

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
