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
| `make seed` | Rebuild the Riverside Auto fixture (14 curated + `dash/cars.csv`) |
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
requirement and must stay that way -- it books appointments and then cancels
them, because `book_appointment` refuses a clash and the fixture's week only
holds about twenty slots. A run that kept them would poison the next one, which
is exactly what happened once.

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
  **Sourced means three things**, and dropping any one of them makes the guard
  reject honest answers: a tool *result*, a tool *input* (`max_price=20000` —
  describing the search it ran is not inventing a price), and a number the
  *buyer* typed, but only where the reply restates it as a bound. A false
  positive here is not cosmetic: the buyer sees the escalation line instead of
  an answer, on every turn, and it reads as a dead bot.
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
  below its content. Rep-facing pages (overview, conversations, the buyer
  page, calendar) get designed mobile layouts -- the buyer page is
  master/detail and calendar is an agenda. Admin pages just have to not
  overflow. `make shots` discovers a real buyer page rather than listing one,
  because it is the screen most likely to overflow and the one a rep reads.
- **Escalating notifies a rep; it does not gag Liner.** Only a rep pressing
  Take over sets `agent_paused`. Stopping on escalation meant a buyer who asked
  one question a human had to answer got "someone is picking this up" to
  everything afterwards — and with nobody watching the queue at 9pm, that was
  the end of the conversation. The buyer ends it, via `close_conversation`.
- **Cost columns never enter the database.** `NEVER_IMPORT` in
  `ingest/csv_import.py` drops `acquisition_cost`, margin and salesperson
  fields before a row is built. A DMS export carries what the dealership paid;
  once it is a column it reaches `search_inventory`, and from there the model.
  A prompt telling the model to ignore a field it can see is a request.
- **Policy answers come from `knowledge_entries`, never from the model.**
  Trade-ins, the doc fee, deposits — the dealer wrote those, and a composed
  answer is one a buyer repeats back to a rep. `answer_from_knowledge` returns
  `found: false` rather than a near-miss, because a plausible wrong answer is
  worse than none.
- **The rail's Summary is `recap`, not `summary`.** `conversations.summary` is
  whatever Liner said last, and printing that under a heading saying Summary
  made a reply look like a recap of the thread. `app/recap.py` composes the
  real one from rows — who, which car, what was captured, the appointment, the
  open escalation — so it can be checked against the data instead of trusted.
  Deterministic on purpose: a model-written summary is a second place a fact
  can be invented, and there is no model at all in stub mode. `summary` still
  backs the one-line preview in the list.
- **The booking card is built from a tool result, never composed.** When
  `check_availability` runs, the buyer gets days, times and contact fields
  (`BookingCard.tsx`); the card can only offer what that tool returned, and its
  submit goes through `book_appointment` like any other caller. Reply text must
  not list the times as well -- the stub and the prompt both point at the card
  instead, because the same question asked twice gets answered in the worse
  place. `book_appointment` owns the clash check: a card can sit on screen for
  minutes, so "still open" has to be re-decided at submit, not at render.
- **The chat transcript is one ordered list, and a card is an entry in it.**
  Search results and the booking card used to sit in their own state, render
  under the whole thread and get cleared on every send, so three cars the buyer
  was choosing between vanished the moment they answered. `Chat.tsx` keeps
  `Item[]`; anything the buyer was shown stays where it was shown.
- **A refresh keeps the conversation.** The id lives in `localStorage` and
  `GET /api/chat/sessions/{id}` rebuilds the thread -- cars included, because
  the reply carries the `tool_calls` that produced them. Availability is the
  exception: it is looked up again, never replayed, since a slot list from ten
  minutes ago is a list of times that may be gone.
- **The guard's retry note is not the buyer talking.** It goes back as a user
  turn because that is the only role every vendor takes mid-conversation, so it
  has to say so in its own text. Without that a model opened its next reply with
  "You're right -- I shouldn't have mentioned a price", apologising to someone
  who said no such thing and never saw the rejected draft.
- **Red means something went wrong, and nothing else.** Status badges, counts
  and tags on dealer pages are `primary`; `destructive` is reserved for a
  failure a rep has to act on -- a rejected import row, a send that bounced, a
  request that errored. When a normal badge and a failed send are the same
  colour the dashboard has lost its only way to say "this one broke".
- **Nothing on the overview is a number that cannot be traced to a row.**
  `credit_apps` counts credit applications the buyer *opened*, not ones the
  dealer sent -- a click is the buyer doing something, which is the only part
  that says the outreach worked. A link to the dealer's own site is invisible
  to us, so the send rewrites it to `/r/<token>` (`api/redirect.py`) and counts
  that hop. It counts clicks, never completions: what happens on the dealer's
  form never comes back. With no `credit_application_url` configured there is
  nothing to send, so the draft refuses with a typed `not_configured` and the
  card says why instead of showing a zero that reads as a quiet day.
- **Every count comes from `/api/overview`.** No page counts for itself. The
  two charts are the exception and have their own `/api/overview/trends?range=`,
  so moving a chart's window cannot silently change what the KPI cards mean.
  An unknown range is a 400 -- answering a typo with "today" shows the wrong
  window under the right caption.
- **Hours come from `hours_json`.** No page states its own.
- **One definition of every conversation filter.** `lib/conversationFilters.ts`
  owns the seven — and `stateOf`, the badge a row wears. Chat, Calls and the
  cross-channel Conversations list all read it. They were three copies of the
  same ternary chain, which is exactly how Appointed ends up counting
  `stage === 'booked'` on one page and an appointment row on another: a manager
  gets two numbers for one question and no way to tell which is wrong.
- **The dashboard is organised by buyer, not by thread.** There is no Chat
  page, no Calls page and no Email page: a buyer who chatted at 9pm and rang
  back next morning was three unrelated screens, and a rep could call someone
  who had already booked. `/app/conversations` lists **people**, and
  `/app/leads/:id` is one buyer's whole history in the order it happened —
  every conversation, their outreach, their appointments, their escalations.
  A row is a lead, or a conversation that has no lead yet: `book_appointment`
  is what mints a lead, so most live chats have none, and an anonymous buyer
  asking a question at 9pm is exactly who a rep needs to see. The two kinds
  stay separate in the code (`Row` is a union, not a lead dressed as a
  conversation), and what cannot apply is answered false rather than fudged.
  `/app/conversations/:id` still resolves: it redirects to the buyer when the
  thread has one, so a thread is never readable in two places.
- **The timeline is composed in `app/timeline.py`, and the de-duplication is
  the whole difficulty.** `api/outreach.py` mirrors an appointment email into
  the buyer's thread as a `role="rep"` message carrying `outreach_id`, so the
  round trip lands without depending on inbox delivery. Lead-level outreach
  has no mirror. Concatenating `messages` and `outreach` therefore shows every
  confirmation twice and every follow-up once — a dealership that appears to
  have mailed you twice. A mirror and its row fold into one entry keyed on the
  outreach id. `make smoke` asserts the count.
- **The channel strip is counted, never declared.** It offers what the buyer
  actually used. There is no SMS provider in this system, so there is no SMS
  tab sitting permanently at zero — a tab that is always empty claims a
  capability that does not exist.
- **One matcher decides who a buyer is.** `app/matching.py`, used by the ADF
  importer, manual entry and `book_appointment`. Booking used to match on
  email alone while the importer matched on email *then* phone, so someone who
  booked from chat and rang back leaving a second address arrived as a second
  lead with the same number on file. Email exact, phone by its last ten
  digits, and nothing else: a name is not identity, and two Dave Joneses are
  two people. `/api/leads/{id}/duplicates` reports candidates **with the reason
  they matched** and merges nothing — a rep cannot check "trust us", and a
  shared household number is a real thing.

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
