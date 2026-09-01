# Liner AI

An AI sales assistant for a car dealership, and the dashboard its staff work
from.

A buyer messages at 11:47 PM. Liner searches the dealership's real inventory,
answers from real listings, asks for a number in a form rather than a sentence,
and books an appointment. The dealer's dashboard reacts live — a rep confirms
it, assigns it, and picks up the thread whenever they want to.

The rule the whole codebase is built around: **narrow, not fake.** Scope is cut
hard, but everything that exists is real. Where an external dependency is
missing, the feature says so rather than simulating a result — and
`/api/integrations` reports that live, on every page.

## Run it

```bash
make install
make demo-db      # a populated dashboard: fixture + ~50 demo buyers
make dev          # backend :8000, frontend :5173
```

Open <http://localhost:5173>, sign in at `/login` with
`dana.mercer@example.invalid` / `liner-dev`.

`make reset-db` builds the **fixture only** — a handful of buyers, enough for
the tests. `make demo-db` is the one that fills the screens; `N=30 make
demo-db` for fewer.

**No `.env` is needed.** Every external dependency has a working default, and
anything unconfigured names the variable it wants.

```bash
make smoke        # the gate: the whole flow over HTTP, plus the live loop
make accept       # one buyer end to end across every channel
make shots        # every route at desktop and 390px
```

## What it does

### For the buyer

A chat that streams over SSE, with inventory cards, tappable prompt chips, a
booking card built from real open slots, and a short form when Liner needs a
way to reach them. A call screen on WebRTC. A storefront page that puts the
assistant on a dealership's own branding, so they can see what it looks like on
their site.

Liner answers, qualifies and books. It follows up once if the buyer goes quiet
and is still on the page. It hands over the questions a person owns — an
out-the-door price, anything about credit — and keeps answering everything else
while they wait, because nobody may pick the queue up for hours.

### For the dealership

The dashboard is organised **by buyer, not by channel**. Somebody who messaged
on Instagram at nine, rang the next morning and then emailed is one row and one
timeline — not three screens where a rep can call a buyer who has already
booked.

- **Overview** — KPIs, live queues, and a panel showing which buyers were
  quoted a car that has since sold.
- **Conversations** — people, with the full history behind each: every
  thread, their outreach, appointments, escalations, call recordings.
- **Calendar** — a week grid for shape and a list view for "what is next".
- **Inventory** — per-vehicle rules, a status that is never a delete, and an
  import review screen.
- **Campaigns** — reasons to go back to buyers who already talked to you: a
  car they asked about that has come down, one still sitting there, somebody
  who went quiet. Audiences are counted from real rows.
- **Liner setup** — the assistant's behaviour, its knowledge table, and the
  compiled prompt read-only. An edit is a draft until it is published.
- **Team** — the roster, daily caps, and who is carrying what.

### Underneath

Nine tools against SQLite, and the rules live in the executors rather than in
the prompt — a prompt is a request, an executor is a guarantee. A vehicle
marked do-not-discuss never reaches the model. A dishonest `typed` provenance
is downgraded. A booking refuses a clash.

Guards reject any price, mileage or vehicle the assistant cannot source from a
tool result in that same turn, and they run in every mode: if something we
wrote ourselves can slip an unsourced number through, that is a hole in the
guard and it should fail offline rather than in front of a buyer.

The prompt itself is a brief, not a script — one paragraph of intent plus the
handful of rules no executor can enforce. Under 8KB in total, and most of that
is the dealership's own facts rather than instruction.

Plus a WebSocket event bus, an inventory ingest pipeline, and an ops dashboard
for the people running Liner itself, kept behind its own role and its own
tables.

## What is real, and what isn't

The part worth reading before judging anything. `GET /api/integrations` returns
it live, and a banner names anything unconfigured — not dismissible, because
the risk of building this way is demoing on placeholders without noticing.

| | State |
|---|---|
| **Agent** | Scripted by default, and not a mock: it calls the real tools, writes the real rows, fires the real events, and every number in its replies comes from a tool result. It just cannot improvise. `LLM_MODE=live` plus `OPENAI_API_KEY` puts a real model on the same tools and the same guards. The vendor HTTP call has never run here — no key in this environment — but everything either side of it is exercised offline against a scripted provider. |
| **Email out** | Writes a real outreach row and mirrors it into the buyer's thread. **Sends nothing by default.** The Resend sender is written and unexecuted; the allow-list, reply token, request body and error path are all tested. |
| **Email in** | The endpoint is real and fully tested — HMAC, dedupe, and a resolution ladder that stores what it cannot place rather than dropping it. The Cloudflare Worker in front of it has never been deployed. |
| **Email agent** | Liner can answer a buyer's email: same loop, same tools, same guards. **Off by default behind two switches**, and every brake — loop headers, a cooldown, a per-hour ceiling that throws the kill switch itself — ships and is exercised before the thing it stops. |
| **Voice** | Built on OpenAI Realtime and off until it is turned on. `/call` is real WebRTC; the browser talks audio straight to the provider. Calls are recorded as they happen, transcribed afterwards, and priced per response against the model that billed them. |
| **Instagram / Facebook / SMS** | **Not built.** Seeded threads exist so the buyer page can show it does not care which channel a message arrived on, and the campaigns page names the app, webhook or registration each would need. Nothing is sent or received. |
| **Inventory** | The local database, and that is the real answer. Rows arrive by seed, CSV import, a crawl, or by hand. The scraper does real HTTP against real markup and knows two page shapes; a site laid out differently needs an adapter. |
| **Lead import** | Real end to end. ADF/XML parsed with `defusedxml`, matched against inventory and existing buyers, reviewed, then committed. Nothing is polled — you upload the document. |
| **Reminders** | Manual. There is no scheduler, so a follow-up is a draft a rep reviews and sends. The page says so rather than implying a drip campaign. |

## Making it yours

One dealership per instance. `backend/config/dealerships/<name>.yaml` carries
their name, address, hours, brand, staff, storefront copy and crawl source;
`DEALERSHIP=<name>` chooses it, and switching is a reseed rather than a second
tenant. There is no `dealership_id` column anywhere, deliberately.

A fact about the dealership goes in the profile. A fact about the box goes in
`.env`. Getting that line wrong is how one dealer's inventory ends up in
another's demo.

`docs/DEMO.md` walks the whole setup, `.env` line by line.
`docs/DEPLOY.md` covers a real host.

## Layout

```
backend/app/
  models/        the tables
  agent/         tools, guards, the brief, the stub, the live loop
  api/           routers; ws.py is the dealer event socket
  integrations/  email and voice behind interfaces, plus the status registry
  ingest/        discover -> extract -> diff -> review -> publish
  config/dealerships/   one YAML per dealership
frontend/src/
  routes/        every page
  components/    ui primitives, dashboard shell
  styles/        liner-theme.css -- all design tokens, and the only place for them
scripts/
  smoke.py       the gate
  acceptance.py  one buyer across every channel
  seed_demo.py   the demo population
```

Conventions, the reasoning behind each decision, and what is deliberately not
built are in [CLAUDE.md](./CLAUDE.md).
