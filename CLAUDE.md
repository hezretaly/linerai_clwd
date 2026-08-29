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
| `make seed-demo` | Add 50 demo buyers **on top of** the fixture (`N=200` for more) |
| `make reset-db` | Delete the database and reseed — **loses the `ops_` tables too** |
| `make reset-dealership` | Rebuild the showroom fixture in place, keeping `ops_users` and `ops_demo_requests` |
| `make add-owners` | Put `founder@`/`cto@` in `ops_users` on an **existing** database — no reseed, no data loss |
| `make set-password` | Change one account's password in place: `EMAIL=someone@...` |
| `make smoke` | **The gate.** Full flow over HTTP, plus the live loop against a fake provider |
| `make ops-ui` | `/ops` in a browser: the notification clears **and stays cleared** |
| `make cal-ui` | The calendar's Week/List views, and that no waiting time is unreadable |
| `make accept` | One buyer end to end: web form → chat → call → email → book → reschedule → cancel → rebook → handover |
| `make accept-ui` | The same path through the screens — buyer window and dealer window, real clicks |
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
to something real and they all differ. `founder@linerai.us`
(`FOUNDER_PASSWORD`) and `cto@linerai.us` (`CTO_PASSWORD`) are **ours**, in
`ops_users` rather than the dealership's table: they sign in at
`/login?as=owner` and land on `/ops`, and their session is refused by every
dealership endpoint just as a rep's is refused by `/api/ops`. One key per
person — a shared password cannot be traced or revoked per person.

Setting this up for a real dealership — every `.env` line, in order, and what
each one buys: **[`docs/DEMO.md`](./docs/DEMO.md)**.

Deploying to a real host: **[`docs/DEPLOY.md`](./docs/DEPLOY.md)**. One process
serves the API, the WebSocket, the landing page and the SPA, so nginx needs a
single `proxy_pass`.

`make dev` kills those ports first — orphaned processes across sessions are the
most common way this gets confusing.

## Verifying a change

`make smoke` is the gate and it must stay green. It drives a real booking
through rail chips, asserts the appointment row exists, confirms it, assigns
it, sends outreach, and checks the expected events arrived on the WebSocket.

`make accept` answers a different question from `make smoke`. Smoke proves each
part works; this proves they are the **same buyer** throughout — a website form,
then a chat, then a call, then an email, then a booking moved and cancelled and
retaken, then a rep taking over and handing back. That is the failure the
dashboard was reorganised to prevent and the one no per-feature test can catch,
because every step passes in isolation. It gives its slots back in a `finally`
like smoke does, and a failure names the step number a person was on.

`make accept-ui` runs that path again through the browser, in two contexts —
the buyer's window has no dealer cookie, which is the only honest way to drive
`/chat`. It asserts what is *rendered*, because a page can be wired to a
correct endpoint and still show the wrong thing: that is how the closed-thread
bug below was found, with the API perfectly right and the screen telling a rep
the conversation was over.

`scripts/e2e_booking.py` goes further: two browser windows, buyer on the left
tapping chips, dashboard on the right, asserting the KPI moves with no reload.
Run it when you touch the frontend or the event path. **It expects a fresh
seed** -- it asserts a name appears in a queue, and after several runs the
queues fill up and truncate. `make reset-db` first. `make smoke` has no such
requirement and must stay that way -- it books appointments and then cancels
them, because `book_appointment` refuses a clash and the fixture's week only
holds about twenty slots. A run that kept them would poison the next one, which
is exactly what happened once.

**The release of those slots runs in a `finally`, and that is not tidiness.**
`book_appointment` refuses a clash and the fixture week holds about twenty
slots, so a run that keeps a booking leaves fewer for the next one. Two ways
that happened: the rail-driven booking at the top of the script was never
recorded for release at all, so *every* run leaked one; and the release used to
be the last section, so any run that failed part-way kept everything it had
taken. Both make failure self-reinforcing — each bad run leaves fewer slots, so
the next fails earlier — and it surfaces as `0 times on the card` in a section
that has nothing to do with the change being tested. It took thirty-six
stranded appointments to notice, and by then the failure named the wrong
culprit.

`make shots` also **fails on a waiting time nobody can read**. `waited()` is a
stopwatch and a stopwatch stops being legible long before it stops counting:
a genuinely two-month-old row in Needs a person rendered as `1349h 36m`, a
number too long to take in at a glance with minutes of precision on the end.
It turns to days past 48h, and `STOPWATCH_JS` asserts that across every route.
The guard was checked by putting the bug back -- a check that has only ever
been seen to pass is not a check.

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
- **The selling method is the operator's file; the operating rules are ours.**
  `agent/sales_method.md` is supplied and stored byte-for-byte — it is how to
  sell, and not this codebase's to edit. `prompts.py` fills its
  `{{VARIABLES}}` (which its own second line asks for) and appends what it
  cannot know: that there are eight tools, that a chat buyer is looking at a
  booking card, that a policy answer comes from a table. Ours is appended last,
  so where the two genuinely disagree ours is what was read most recently — and
  it says which section it is overriding rather than contradicting it silently.
  `make agent-check` asserts the filled method appears in both prompts whole.
  - **Every placeholder is answered, including the ones we have nothing for.**
    Left in braces, a model eventually types `{{CURRENT_CAR}}` at a buyer; left
    empty, `{{VDP_VIEWS}}` is an invitation to invent the demand figure the
    sentence around it exists to forbid. So both get a plain statement of the
    fact instead, and the gate fails on any `{{` surviving in either channel.
  - **A capability the method assumes and this system lacks becomes a
    refusal, not a silence.** The personalized video, the follow-up cadence and
    sending the credit application are all things it tells the assistant to do
    and nothing here can do. Each is answered where the method asks for it —
    there is no scheduler, a rep composes the follow-ups, and video is off — so
    the assistant never offers a buyer something nobody will deliver.
- **The greeting is already on the buyer's screen, and the prompt has to say
  so.** It is client-side only and never a message row, so the model cannot
  see that anything was said — and the method's own section 1 tells it to
  disclose it is an AI *at the start*. Told nothing, it obediently opens with
  "I'm Liner, Riverside Auto's AI assistant" directly under "Hi! I'm Liner,
  Riverside Auto's assistant": the buyer is greeted twice by something that
  cannot remember saying hello. So the prompt quotes the greeting back and
  says it has already been sent. Asked outright whether it is a bot, it still
  answers yes — that is a question, not an opening.
- **A budget you cannot convert is still a budget.** "Around $300 a month" is
  not a price and must never become one — there is no rate, no term and no
  payment maths here — but a model told only what it may not say answers with
  no cars at all, which is what a real buyer got. The rule is therefore
  written as a positive: any question about what they could have is a search,
  every time, before a word is written, and the monthly number is a person's
  job. A buyer who asks what they can get and receives a paragraph about why
  you cannot say has been told nothing and has nothing to look at.
- **A car the model names must be a car a tool returned.** `search_inventory`
  is a real guarantee — only `available`, discussable rows come back, so a
  sold car cannot arrive through a tool — but nothing stopped the model
  *mentioning* one out of what it knows about cars in general. "We've got a
  Lexus RX too" carries no price, no mileage and no year, so every other guard
  waved it through and the buyer drove over for a car that was never on the
  forecourt. `check_unsourced_vehicles` closes that.
  - **Makes only, deliberately.** Escape, Focus, Soul and Fit are model names
    and ordinary English; matching them would reject honest sentences, and a
    false positive here costs the buyer their answer. Dodge, Ram, Mini, MG and
    Smart are dropped for the same reason — and Ram loses nothing, since the
    make it ships under is still watched. You cannot get a buyer to the wrong
    forecourt without naming a make.
  - **Grounding is wider than for numbers, and on purpose.** A price is
    re-read every turn because it can change; a car found earlier in the
    thread was really found and is still on the buyer's screen. So any tool
    result in the conversation counts — `tools.earlier_results`, the same one
    the after-the-fact voice guard reads, because two copies of "what has this
    conversation been told" is how one channel starts flagging a car the other
    accepts. A make the *buyer* typed counts too: "nothing from Alfa Romeo
    right now" is an honest answer, not a claim about a car.
  - It caught an invented car in `agent_loop_check.py` the day it was written
    — a hand-authored fixture had been offering a Toyota Corolla this lot has
    never carried, and passing, for months. That is the argument for it.
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
  behave because the page unloads. The SPA owns `/chat`, `/call`, `/login`,
  `/app/*` and `/ops/*`.
  - **`SPA_PREFIXES` is a hand-written list that development cannot check.**
    Vite's history fallback serves `index.html` for any path, so every browser
    gate here passes against `:5173` whatever the list says; only the built
    bundle enforces it. `/ops` shipped missing and the whole ops dashboard
    answered `{"detail":"Not found"}` on a real host — a JSON 404 from the
    catch-all, so the request never reached React and no session or redirect
    logic ran at all. It reads as a broken login and is nothing of the kind.
    `make smoke` reads the top-level routes out of `main.tsx`, fails on one
    the API would not serve, and fetches each against a real build.
- **Mobile is a supported surface, not an afterthought.** Reps work from
  phones. Two rules keep it that way: a `<table>` never reflows, so any table
  either scrolls inside its own `overflow-x-auto` card or has a card layout
  below `md`; and a flex or grid child needs `min-w-0` before it will shrink
  below its content. Rep-facing pages (overview, conversations, the buyer
  page, calendar) get designed mobile layouts -- the buyer page is
  master/detail and calendar is an agenda. Admin pages just have to not
  overflow. `make shots` discovers a real buyer page rather than listing one,
  because it is the screen most likely to overflow and the one a rep reads.
- **Booking does not close the thread.** It used to: the stage reached `booked`
  and the turn ended the conversation. But a buyer who has just booked very
  often keeps going — financing, a trade, a second car — so Liner went on
  answering while the dashboard said *"every thread here is closed"* and
  offered the rep no composer and no Take over. The one person who could have
  helped had no way in, and the buyer could not tell. The buyer ends it, via
  `close_conversation`, and `record_buyer_message` reopens a closed chat that
  somebody types into again — chat only, because a voice call that ended really
  did end and its `ended_at` is how long it ran.
- **Escalating notifies a rep; it does not gag Liner.** Only a rep pressing
  Take over sets `agent_paused`. Stopping on escalation meant a buyer who asked
  one question a human had to answer got "someone is picking this up" to
  everything afterwards — and with nobody watching the queue at 9pm, that was
  the end of the conversation. The buyer ends it, via `close_conversation`.
- **Handing a buyer to somebody else is the manager's call; taking one
  yourself is not.** Who works which lead is how a floor is run, and a rep
  quietly moving buyers off a colleague — or off themselves, back into the
  pool — is reassignment either way. Taking one over is the exception and
  stays open to everyone, because that is a rep saying *I have this*, which is
  the thing the queues are asking for. `POST /leads/{id}/assign` enforces
  exactly that split and `AssignTo` renders it: a rep sees Take over and no
  roster, so the control on screen is the one the server will accept rather
  than a menu that can only 403.
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
  real one from rows — who, which car, the appointment, the open escalation —
  so it can be checked against the data instead of trusted. Deterministic on
  purpose: a model-written summary is a second place a fact can be invented,
  and there is no model at all in stub mode. `summary` still backs the
  one-line preview in the list. Two rules keep it honest:
  - **It never restates the captured fields.** Prose cannot carry provenance:
    "Financing: likely financing" reads as something the buyer said when the
    row says `inferred`, and repeating a guess without the badge marking it a
    guess is how a rep ends up asserting it on the phone. The Captured by
    Liner panel that used to carry those badges beside the recap was taken off
    the rail on request — the rule outlives it, and matters more without it:
    `save_captured_fields` still records provenance and `buyer_summary` still
    puts only `typed` fields in the buyer's email, so the recap is not the
    place to leak an inference back in.
  - **`lead_recap` is not `conversation_recap` on the newest thread.** Devon
    booked on the website and rang back next morning, so the newest thread is
    the call while the appointment hangs off the chat — the rail told a rep
    "nothing booked yet" about a booked buyer. Anything that can span threads
    is asked across all of them.
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
- **The calendar answers two different questions, so it has two views.** The
  week grid lays out a shape — where the gaps are, what clashes — and is bad
  at "what is next", which needs one glance down a column rather than paging
  week by week to find the booking twelve days out. `List` is that, grouped by
  day and running from now forward. The count above it and the rows in it come
  from **one predicate**: they were two, and the header said 16 over a list of
  147. Cancelled and past are excluded by default and each button says how many
  it would add — "nothing booked" and "they cancelled" are different facts, and
  only one of them needs a phone call.
- **Every count comes from `/api/overview`.** No page counts for itself. The
  two charts are the exception and have their own `/api/overview/trends?range=`,
  so moving a chart's window cannot silently change what the KPI cards mean.
  An unknown range is a 400 -- answering a typo with "today" shows the wrong
  window under the right caption.
- **A vehicle is never deleted, only taken off the lot.** `status` goes
  `available | sold | removed`, and it has its own endpoint
  (`POST /api/inventory/{id}/status`) rather than riding along in the PATCH
  that edits mileage — taking a car off the lot is a different kind of act,
  and a second way to set it is how one of them stops emitting the event.
  `vehicle_mentions` and `appointments` both point at the row, so a delete
  either errors or takes the quote history with it, and that history is the
  only answer to "who was told about this car?" — the question a rep has the
  moment one sells. `ingest/pipeline.py` reached the same conclusion for a
  listing that vanishes from the feed. Two rules keep it working:
  - **The blast radius is shown before the decision, not after.** Who was
    quoted it and who is booked in to see it is the whole reason the moment
    matters; a confirmation that arrives once it is too late to act is a speed
    bump. Their appointments are **not** cancelled — that is a call a rep
    makes, and a visit that quietly vanishes from the calendar is worse than
    one against a car that has gone.
  - **A hand-set status is marked manual and survives the next import.** The
    dealership's own site will still be listing a car that sold an hour ago,
    so ingest sees it "reappear". That branch used to set it back to available
    outside the manual-override check every other field respects, which put a
    sold car straight back in front of the model.
- **Email goes out through Resend and comes back through Cloudflare, and the
  two halves fail differently.** Sending breaks loudly at the provider — a bad
  key, an unverified domain — and the next send says so. Receiving breaks
  *silently*, in a Cloudflare route configured outside this app, and looks
  exactly like a buyer who did not write back. That asymmetry is why
  `/app/email` exists and why `inbound_emails` records every delivery
  **including the refused ones**: a 401 into the void is unfalsifiable.
  Three rules:
  - **`reply+<token>@` keys on the send, not on a conversation.** Most
    outreach here is composed against a lead and has no conversation to name,
    so a conversation-keyed address could not be put on a follow-up at all.
    The token sits on the `outreach` row, next to `click_token`, which already
    solved the same problem for links. Tokens are **lowercase alphanumeric**:
    a mail server may rewrite the case of a local part, SQLite's `=` is
    case-sensitive, and the resulting miss is invisible — the reply just lands
    by the loosest matching rule instead of the exact one.
  - **The intake answers before it files.** The Worker rejects the message to
    the sender on a non-2xx, so a slow CRM bounces a real buyer's reply. The
    receipt is claimed *before* the response and resolved after — that
    ordering is the dedupe: a plain "return 200, process later" lets a retry
    arriving mid-flight file the same reply twice.
  - **The intake's schema is never stricter than the wire.** Every declared
    field on `InboundBody` accepts `null`, because a Worker writes
    `inReplyTo: parsed.inReplyTo ?? null` and means "there wasn't one".
    Declaring it `str = ""` made every unthreaded reply a 400 — and a Worker
    reasonably reads 4xx as *my payload is wrong, retrying will not help*, so
    a schema quibble became replies lost rather than delayed. Nothing
    downstream reads the attributes anyway; they are documentation of a shape,
    and `make smoke` posts the deployed Worker's payload field for field.
  - **A message with no `Message-ID` dedupes on its bytes.** `JSON.stringify`
    drops the key when there was no header, which leaves a retrying Worker
    free to file the same reply twice. The digest of the exact request body
    stands in, and that is precise rather than heuristic: a retry re-posts an
    identical body, while two real emails differ in the `receivedAt` the
    Worker stamps per invocation. Written `sha256:…`, and never echoed into an
    outgoing `In-Reply-To`, where it would name a message that never existed.
  - **The intake takes either credential and one of two paths.**
    `X-Liner-Signature` (HMAC over the exact bytes, so it covers the body) or
    `X-Webhook-Secret` (plain, what the deployed Worker sends), on
    `/api/inbound-email` or `/api/emails/inbound`. Both are live
    configuration, so `make smoke` drives both rather than trusting either.
  - **The Worker's recipient filter is upstream of every receipt, so what it
    drops leaves no trace at all.** It filters before posting, because a
    catch-all sweeps up spam — and `founder@` was missing from that list while
    `landing.html` published it as the way to reach a person directly. Mail to
    the one address we tell people to use was thrown away in Cloudflare with a
    `console.log`: no receipt, no row, no error, indistinguishable from nobody
    having written. That is exactly the failure `inbound_emails` exists to make
    visible, and it sits one step above it. `make smoke` reads the Worker's
    list and fails on any address the product publishes that it would refuse.
  - **A reply that cannot be placed is stored, never dropped.** Resolution
    goes token → `In-Reply-To` → the shared lead matcher, and stops there: a
    name is never part of it, so a stranger stays a stranger instead of being
    attached to whoever shares one. Someone really wrote in either way.
  - **Liner does not answer email.** A reply lands as an activity and reopens
    an escalation a rep had claimed. An autonomous email sender needs its own
    guards, a rate limit and a loop-breaker for auto-responders; none of that
    exists, so neither does the capability.
- **`OUTBOUND_ONLY_TO` gates sending, never receiving.** One setting whose
  name is the rule: empty refuses every send, a list allows those addresses,
  the word `everyone` lifts the limit. One call site,
  `outreach_send.blocked_reason`, and the refusal names the setting and the
  value that changes it. Inbound has no filter at all — anyone may write in,
  and mail that cannot be placed is kept.
  - It replaced `DEMO_MODE` + `EMAIL_ALLOWLIST`, which took two values to say
    one thing and read like an inbound access list. The old pair is still read
    so an existing `.env` keeps its behaviour: **widening who can be emailed
    is the one direction an upgrade must never do silently.**
  - `everyone` is a word, not an empty string. Empty meaning "mail anybody" is
    the default that goes wrong quietly — a deleted line, a mis-copied file —
    and the failure is a rehearsal reaching real prospects.
  - `outbound_recipients` returns `None` for no limit and `[]` for nobody.
    Callers must tell them apart; collapsing the two is how an empty list
    starts meaning unrestricted.
- **`/app/email` is a union, and that is the point.** It lists `outreach` rows
  both directions *plus* unresolved `inbound_emails`. A reply nobody could
  place has no outreach row and no buyer page, so listing one table would
  leave a stranger writing to `sales@` visible only on a diagnostics strip.
  `_in_box` defines each tab once, for the counts and the filter both — two
  copies is how a tab says 12 and shows 9.
- **There is no Drafts tab, because nothing stores a draft.** One is composed
  from the lead's state when the composer opens and lives in the browser until
  the rep presses send. A tab that is always empty claims a feature that does
  not exist. Closing the composer with text in it asks first, which is the
  whole safety net there is.
- **The composer takes any address, and says who it found rather than
  restricting.** The case `/app/email` exists for is a stranger writing to
  `sales@`; a composer that could only answer existing leads would push a rep
  back into their own mail client, where the reply is invisible to this system
  for good. So `POST /api/email/compose` puts the address through the one
  matcher, files the send against a buyer when there is one, and sends anyway
  when there is not — with the line under the field saying which happened
  *before* the send. What it will not do is skip `blocked_reason`: a composer
  is exactly where a rehearsal reaches a real prospect.
  - **The refusal warning is only shown when there is something to refuse.**
    `blocked_reason` deliberately does not bite on a sender that delivers
    nothing, so with the outbox the composer says no mail will leave the
    building rather than warning about a limit that will not fire. A warning
    that turns out to be wrong is worse than none — the next one gets ignored
    too.
  - **A reply carries `In-Reply-To` and `References`.** The sender interface
    takes a provider message id, not a rendered header, so each vendor maps it
    its own way (`headers` for Resend, MIME for Gmail). Without it a reply
    opens a second conversation in the buyer's inbox, and a buyer answering
    four times ends up with four threads about one car.
- **The mailbox refreshes itself two ways, and they are not redundant.**
  `email.received` on the socket is the one that matters — mail arriving is the
  only thing on this dashboard nobody clicked for — and it fires for an
  unresolved delivery as well as an accepted one, because a stranger's reply
  has no buyer page to appear on instead. The five-minute poll is the backstop
  for a dropped socket, and the "Checked 3m ago" line exists because a list
  that refreshes silently is indistinguishable from one that is stuck.
  Refusals stay silent: their receipt is written before authentication, so
  emitting there would let anyone who found the URL grow the events table.
- **`accepted` on a receipt means fully filed.** The escalation reopen happens
  before the receipt is stamped, not after. Stamped first, it failed about one
  run in three — the receipt read filed while the thread this reply reopens was
  still sitting claimed.
- **`WEBHOOK_SECRET` has a development default and production refuses to boot
  on it.** Same shape as `MANAGER_PASSWORD`. Without a default the inbound
  path could only ever be tested by asserting the 503, and the signature
  check, the dedupe and the whole resolution ladder would ship untested.
- **A call runs the same executors, and cannot run the same guard.** Audio
  goes browser-to-OpenAI directly — proxying it would add a round trip to every
  syllable, and latency is the product on a phone call — so the model is on the
  far side of a connection this server is not in. Everything enforced *inside*
  an executor still holds: `search_inventory` filters a do-not-discuss vehicle,
  `book_appointment` refuses a clash, `save_captured_fields` downgrades a
  dishonest `typed`. The reply guard cannot: by the time a transcript reaches
  `/api/voice/transcript` the words are in someone's ear. It runs there anyway
  and raises a handoff — it cannot unsay a number, and that difference is
  written down rather than implied.
- **One response at a time on a call.** A turn that calls two tools fires
  `response.function_call_arguments.done` twice, and answering each with its
  own `response.create` puts two responses on the same audio track — which
  sounds like the assistant changing voice mid-sentence. Submit every
  outstanding result, wait for the `response.done` of the response that asked
  for them, then request exactly one.
- **A call is one timeline entry, and it carries its own audio.** Not a header
  over forty transcript lines: a rep scanning a buyer's history wants "an
  eight-minute call on Tuesday" as something they can press play on. Duration
  comes from the conversation row and not from the recording, so a call whose
  audio failed still reports how long it ran — and the two disagreeing is
  itself worth seeing. `end_call` stamps `ended_at` as well as
  `close_conversation` does, because a call the buyer simply dropped otherwise
  had no length at all.
- **The recorder's `AudioContext` is created inside the click, before any
  `await`.** Built after one — after `getUserMedia`, which is where it read
  naturally — it is outside the user gesture, and a browser with an autoplay
  policy starts it *suspended*. A suspended context feeds the mix nothing, so
  `MediaRecorder` captures zero bytes and the upload is skipped for having
  nothing to send: a finished call with no audio and no error anywhere.
  Measured, not reasoned — same pipeline, same Chromium, context outside the
  gesture gives `chunks: 0, bytes: 0` and inside it gives 24 kB in two seconds.
- **Call audio is written as it is spoken, not uploaded at the end.** The end
  is the least reliable moment there is — a closed tab, a crashed browser and a
  dropped connection all skip it — so two-second slices go to
  `/recording/{id}/chunk` and are appended to the file on disk. What a crash
  costs is one slice. `/recording/{id}/complete` stamps the length, and
  `duration_ms = 0` is what "still being written, or abandoned" looks like: the
  timeline offers such a file but says the audio stops before the call did.
  - **Slices must arrive in order, and only the browser can promise that.**
    A webm or mp4 out of `MediaRecorder` is a header followed by continuation
    clusters; reorder them and nothing plays. One promise chain in `Call.tsx`
    is that guarantee — the server appends what it is given, because tracking
    an expected sequence would need a column this codebase has no migration
    for.
  - **The filename is built after the flush.** `id` comes from the column
    default at flush time; read a moment earlier it is `None`, and *every*
    recording on the system lands in one file called `None.webm`. That shipped,
    and was invisible for two commits because a single upload per call meant
    the last writer won. Rows from that era are refused rather than served —
    handing one buyer's call to another buyer's page is the version of this bug
    that matters.
- **Every way a call can end closes the recording off.** The red button,
  `close_conversation`, the idle timeout and a dropped connection all go
  through `hangUp`; it is re-entrant-guarded because closing the peer
  connection fires the state change that calls it. A closed tab beacons only
  the unsent slice and the end marker, which is small enough to fit where a
  whole call never was.
- **Recording a call is somebody's voice, so three things are fixed.** The
  buyer is told before the microphone opens — the line is on `/call`, above the
  button, and several US states require every party to consent, which is a
  decision for the dealership to take with its own counsel. The audio never
  leaves the server without a dealer session. And the format is recorded
  alongside the bytes: Safari's `MediaRecorder` emits mp4 and Chrome's emits
  webm, so serving one as the other plays silence. Files live under
  `backend/var/` — gitignored, and never in the database, where a table growing
  by megabytes a row ruins every backup.
- **A realtime call bills the whole conversation on every turn.** Not the
  latest exchange — all of it, audio included, as input, each time the model
  answers. So cost climbs with call length no matter how short the replies get,
  and the per-minute average hides it. Three brakes and one instrument:
  `max_output_tokens` caps the dearest stream (output audio is twice the price
  of input and generated about twice as fast as a person talks),
  `truncation: retention_ratio` stops the history growing without limit, and
  `VOICE_TRANSCRIBE=false` drops a bill that buys a readable record and nothing
  else — the model hears the audio directly and never reads the transcript.
  - **The cache hit ratio *is* the bill.** Cached input is discounted roughly
    eighty-fold, so the same call costs three to four times more when caching
    stops hitting, with nothing else different. That is why the retention ratio
    is gentle: every truncation invalidates the cache from that point, and
    truncating often costs more than the tokens it saves.
  - **Shortening the prompt is not the fix, and can make it worse.** On a call
    of any length the accumulated audio dwarfs the instructions, and a smaller
    prefix means less of that history sits behind a cached boundary.
  - **`/api/voice/cost/{id}` reports what the provider charged, per response.**
    The counts come off `response.done` and are relayed by the browser, and the
    figure is labelled an estimate. Per response rather than per call: a total
    hides that the eleventh turn cost six times the second, which is the
    finding.
  - **Rates follow `VOICE_MODEL`, they are not pinned beside it.** Switching to
    `gpt-realtime-mini` is one line of `.env`; if the prices were a separate
    setting, that line would leave the dashboard charging flagship rates for
    mini traffic — three times over, with nothing on the page to contradict it.
    `VOICE_PRICE_*` still overrides, for when a vendor moves a price faster
    than this repository. A row is priced against the model that *billed* it,
    so changing model tomorrow does not re-price yesterday.
  - **A model with no published rates is reported unpriced, never guessed at.**
    Matching is on the family plus a *version* suffix, so `gpt-realtime-2.1` is
    a `gpt-realtime` but a future `gpt-realtime-nano` is not. A plain prefix
    match would charge that nano at the flagship's rate, silently — and a cost
    report that is confidently wrong is worse than one that says it does not
    know.
- **The greeting is a pre-roll the browser plays, not a turn the model takes.**
  Asking a model to open a call is asking it to improvise, and a smaller one
  improvises the *customer's* line: a real call on `gpt-realtime-mini` began
  "Hi! I'm looking for a compact SUV" in the assistant's voice and then answered
  itself for four turns. So the browser plays `VOICE_GREETING_AUDIO` (a two-note
  tone until a real recording exists), *then* opens the microphone, and the
  model's first turn answers something the buyer actually said. It is also the
  same words every call, cannot be cut off by the connection settling, and
  generates no output audio — the dearest thing on a realtime call.
- **The voice addendum stays short.** Every token of it is re-read on every turn
  of every call, and the chat prompt it appends to is already long.
  `make agent-check` fails if it grows past 1500 characters.
- **A half-transcript must say it is half.** `VOICE_TRANSCRIBE=false` saves a
  separate bill and costs the buyer's side of every call — which then renders
  as Liner talking to nobody, indistinguishable from the failure above. The
  call entry says so, and points at the recording, which has both halves
  regardless.
- **A greeting the buyer has not heard yet cannot be interrupted.** The
  microphone is held shut until the first `output_audio_buffer.stopped`. Open
  from the start, the connection settling triggers the turn detector, which
  cancels the greeting mid-word — and the silence afterwards is a completed
  turn, so the server generates a second greeting to nobody. A real call opened
  with two.
- **Saying goodbye is not hanging up.** The line and the buyer's microphone
  stay open until something closes them, and a realtime provider bills by the
  minute. `close_conversation` — the same tool chat uses — is what puts the
  phone down, and the client hangs up once the goodbye has finished playing.
  Two minutes of silence in both directions does it too, because a forgotten
  tab is a live microphone.
- **The buyer's summary email is composed from rows, like the rail's recap.**
  `close_conversation` takes a model-written `summary` and that used to be the
  entire email. A real call mailed *"John Doe is all set … A summary will be
  sent to john@outlook.com"* — a status line about the reader, in the third
  person, telling them that what they are holding is on its way to them. The
  argument still backs `conversations.summary`; the email is `buyer_summary`,
  built from captured fields, vehicle mentions and the appointment. The rail
  already decided this, and it matters more here: that copy is the one the
  buyer keeps and reads back to a rep. Only `typed` fields go in — an inferred
  guess repeated back as fact is how a buyer arrives arguing about a budget
  they never gave.
- **The transcript is a side channel, not what the model heard.** The model
  gets the raw audio; `conversation.item.input_audio_transcription` is a
  parallel service for our records. So a garbled transcript means a poor
  microphone, not an assistant that misunderstood — and changing the
  transcription model fixes the record, never the comprehension. Both symptoms
  do share one cause, which is why the session sets `noise_reduction`, a
  `language` hint and `semantic_vad` rather than defaults: on the
  telephone-quality audio a Bluetooth headset produces, a fixed silence
  threshold cuts the buyer off mid-sentence and the language is inferred wrong.
  Two more levers, both ours: `delay` buys accuracy for latency this channel
  does not pay — the model never waits on this text — and `keywords` feeds the
  transcriber the dealership's own makes, models and trims, which is exactly
  the vocabulary that comes back mangled ("E-Class" arrived as 比克拉斯). Only
  some transcription models accept keywords, so they are withheld from the rest
  rather than 400-ing the session.
- **A call is recorded twice, and the second one is not a copy.** The mix is
  what a rep plays back; the buyer's microphone alone is written to
  `call_buyer_tracks` so the call can be transcribed properly after it ends.
  Transcribing the mix is not an option — one track carrying two speakers gives
  an undifferentiated stream of words with no way to tell who said which, which
  is worse than the half-transcript it would replace. Both recorders start in
  the same statement so the two files share an origin.
  - **Liner's half is never transcribed, because it was never audio to us
    first.** `response.output_audio_transcript.done` is the model's own text,
    emitted alongside the audio it spoke, so it is exact by construction.
    Sending it to a transcriber would spend money to make a worse copy of
    something already known. Only the buyer's half is a guess, so only the
    buyer's half gets a second pass.
  - **The two halves are joined on a clock stamped in the browser.**
    `call_segments` carries an offset from the first slice of audio, because
    the browser is the only place that can see both halves happen. Server
    receipt time cannot do it: the live transcriber runs with `delay: high`, so
    the buyer's question arrives *after* the answer to it, and a transcript
    ordered by arrival shows Liner replying before it was asked.
  - **The buyer's spans come from the provider's own turn detector**
    (`input_audio_buffer.speech_started`/`stopped`), not from a second detector
    in the page. A second opinion about when someone started talking is a
    second thing that can disagree with the model. They arrive wordless — that
    is a stage, not a missing value — and the words are recovered from the
    audio afterwards.
  - **The cross-talk filter only bites when there is something to bite on.** A
    transcribed span overlapping no detected speech is Liner's voice returning
    through a laptop speaker, so it is dropped; a call whose marks never
    arrived keeps every line instead, because a worse transcript beats an empty
    one. The padding is deliberately generous for the same reason.
  - **Rewriting the transcript is all or nothing.** Replacing only the buyer's
    lines would leave the two halves stamped from different clocks, which is
    the misordering the whole exercise exists to fix, reintroduced at the join.
    So `_rebuild_messages` refuses unless Liner's marks are there too, and a
    `rep` message is never touched — that is a person typing after the fact.
  - **`transcribed_at` is the once-only guard.** A second pass would delete the
    lines the first produced and pay the provider to make them again.
- **`language` is a preference at the vendor and a rule here.** Every session
  names `en`, and the model family still mis-detects English as Chinese — 嗯
  for an "mm", 比克拉斯 for "E-Class". A hint that cannot be enforced where it
  is sent is enforced where it lands: `_is_noise` in `api/voice.py` drops a
  buyer line carrying no Latin letter, because an English-only channel did not
  produce one. Bare digits stay (a year, a price, a phone number) and so does
  anything with a word in it, however short — dropping something a buyer really
  said is far worse than keeping something they did not. It costs a buyer who
  genuinely speaks another language, and they are already being answered in
  English by a prompt that says so; the recording keeps every word regardless,
  which is what makes dropping safer than guessing.
- **`channel="voice"` appends to the prompt; it does not fork it.** The chat
  rules assume a screen — a booking card, a rail of chips, a price you can
  re-read — and a model given them out loud says "asterisk asterisk dollar
  twenty-four thousand" and offers a card nobody can see. `VOICE_ADDENDUM`
  covers spoken words only, numbers said the way people say them, one car at a
  time, and taking the email by ear because there is no card. Appended, because
  two full prompts is how the price rule ends up stricter on one channel.
- **A key alone does not answer the phone.** `VOICE_PROVIDER` empty means voice
  is off even with `OPENAI_API_KEY` present. Taking calls is a decision a
  dealership makes, not a side effect of configuring the chat agent. The key
  itself is shared — `VOICE_PROVIDER_KEY` exists only to bill voice to a
  different project, and asking for the same secret twice is how the two drift.
- **The marketing site has its own back end, and its own table.** `/api/demo`
  serves the booking sheet on `landing.html`. Its customer is a dealership
  buying Liner, not a buyer buying a car, so a `DemoRequest` is not a `Lead` —
  folding them together would put prospects into the list a rep works from,
  which is the one list here that has to mean exactly one thing.
  - **The consent wording is served, not hardcoded in the page, and stored on
    the row.** A boolean records that a box was ticked; the timestamp plus the
    exact text records what somebody agreed to, at a moment. The page will be
    edited one day, and a consent record pointing at whatever it says *now* is
    not a record of what they agreed to *then*.
  - **The slot is re-decided at submit.** The sheet sits on screen while
    somebody types their details, so "still open" a minute ago is not an
    answer — the same reason `book_appointment` re-checks a clash. A 409 sends
    them back to pick again rather than leaving a form that cannot be sent.
  - **Two addresses on purpose, and they are not two support channels.** The
    form is the way in and `support@` backs it from the footer; `founder@` is
    one person's address, offered on the Support section and again in the
    reply after somebody writes. It is styled as ink rather than white
    because on this page black already means a person — the handoff avatar
    that takes over from the assistant is the same colour — and the address
    itself is the link rather than a button standing in front of one. Somebody
    writing to a founder is taking down an address, not clicking a call to
    action.
  - **The consent wording is per form, and both are served.** Somebody
    reporting a fault is not booking a demo, and the support form takes no
    phone number — so the booking wording ("phone, text … about your demo …
    reply STOP") describes something that did not happen, which is the one
    thing a consent record is for. `SUPPORT_CONSENT` is the second one, sent
    down `/api/demo/slots` beside the first, because a wording hardcoded in
    the page is a second copy that drifts from the row.
- **"Readonly database" takes three things, not one.** SQLite needs the
  database file, the directory (WAL mode writes `liner.db-wal` and
  `liner.db-shm` beside it) and those sidecars, all owned by the running user;
  any one of them fails every write, and they go wrong independently, so a
  directory that looks right tells you nothing about the file in it. Measured
  as an unprivileged user *inserting a row* — an earlier probe only called
  `create_all()` on a database whose tables already existed, wrote nothing,
  and passed vacuously, which is how the rule got written down backwards
  once. `create_all` catches the error and prints who it is running as and who
  owns each, because the bare SQLAlchemy traceback is sixty lines with the
  cause nowhere in it.
- **A new role password breaks an existing deployment, and only the boot says
  so.** `OWNER_PASSWORD` has a development default, so production refuses to
  boot on it — correct, and it means an install whose `.env` predates the
  variable stops starting after an upgrade with a message that reads as *you
  misconfigured this*. The error names it as the newer one and gives the two
  commands; the second is `make add-owners`, which exists because
  `_seed_users` only runs on a fresh seed and `make reset-db` would take the
  leads with it. Anything added to the stale-password list later inherits the
  same trap.
- **`/ops` is Liner's own dashboard, and `owner` is a third role.** Not a
  senior manager: a manager runs a showroom and has every reason to read its
  buyer list, which is exactly what these two have no business reading. So
  `require_owner` guards `/api/ops`, a dealership's staff get a 403 there
  however senior, and nothing under it touches `leads`, `conversations` or a
  recording. It has the two things a two-person company actually has —
  a calendar of the demos people booked with us, and the mail they sent.
  - **Our tables are our tables: `ops_users` and `ops_demo_requests`.** They
    started as a role string on `users`, and that meant every unfiltered
    `query(User)` was a place we could surface inside somebody else's
    showroom — three did, putting us on the team roster, in the assignment
    pickers and behind the public demo door. Each was fixed with a predicate
    and the next missing one would have brought them all back. A separate
    table cannot be queried by accident, which is the whole argument.
    - **Still one database.** Two would mean two connections, two backups, two
      `create_all`s and no way to read both sides in a request — and `events`,
      which the socket replays from, sits on the other side of that line.
      These are the only tables that would move if it ever changes.
    - **The session names its realm.** Two tables mean a bare `uid` is
      ambiguous, and an ambiguous id is one that can be looked up in the wrong
      table. The cookie carries `realm`; a cookie without one predates the
      split and reads as the dealership's, which is what it was.
    - **The split is symmetric, and both halves are enforced at the session.**
      An ops session is refused by every dealership endpoint exactly as a
      rep's is refused by `/api/ops`. `/api/auth/me` and the event socket are
      the two deliberate exceptions — "who am I" has to answer for either, and
      the socket carries both sides' events.
    - **The wrong door redirects; it never signs anybody out.** No session on
      `/ops` goes to `/login?as=owner`; a dealership session there goes to the
      same place with `why=ops`, which is what stops a login form arriving
      unannounced from reading as *your session expired* — it has not. An
      owner on `/app` goes to `/ops`, because `RequireAuth` passes (`/auth/me`
      answers for either realm) and the shell would otherwise render with
      every panel in it 403ing: broken, rather than "this is not yours".
      `make ops-ui` asserts all three, and that `/api/auth/me` still answers
      200 afterwards each time.
    - **`_clear` never touches an `ops_` table**, so rebuilding the showroom
      fixture cannot throw away demos real people booked with us.
      `make reset-dealership` is that; `make reset-db` deletes the file and
      does lose them, which is why it is not the one to reach for on a box
      with anything real on it.
  - **A notification is cleared by opening the thing, not by a button.** One
    left sitting after it has been read is one people learn to ignore, and this
    is the only dashboard here with something on it that nobody clicked for.
    `status` goes `new → seen` on open, which is deliberately a state on the
    row rather than a per-user receipt: there are two of us, and "I have seen
    it" from either is the answer the other needs. A per-person flag would have
    the badge arguing with itself across two laptops.
  - **A replayed event must not raise a notification.** The socket sends the
    backlog on every connect, so without `DealerEvent.replayed` — set for
    everything before the server's `ready` frame — each page load popped a
    toast for every demo booked that week, including the ones already answered.
    Invalidating a query key on a replayed event is still right: the data
    really did change. Interrupting somebody about it is not.
  - **The ops inbox is a union too, for the same reason `/app/email` is.** The
    marketing site's forms plus `inbound_emails` that resolved to nobody — a
    stranger writing to `support@` has no buyer page anywhere, and listing one
    table would leave them visible only on a diagnostics strip. `boxes` defines
    each tab once, for the counts and the filter both.
  - **The ops mailbox is a mailbox: Drafts, Sent, Trash, and mail that
    arrives unread.** Two new tables rather than new columns, because that is
    what a database which already exists actually gets — `create_all` adds a
    table and never a column, and there is no Alembic here by design.
    `ops_messages` is what we wrote, one row that starts as a draft and
    becomes the sent message rather than being copied on send; `ops_mail_state`
    carries read and trash marks for what we did not write.
    - **A delivery that resolved to nobody used to arrive already read**, and
      hardcoded so — there was no column for it and a migration was judged not
      worth it. So the one box holding mail from strangers was the one that
      could never say which of it was new. Reading is still done by *opening*,
      never a button; what the button does is the other direction, because "I
      have seen this and not dealt with it" is the only thing that makes an
      inbox a queue.
    - **Forms answer from `ops_demo_requests.status`, not from the new
      table.** That column is already this fact and the notification bell
      reads it; a second copy is how the bell and the mailbox start
      disagreeing about the same message. One function computes `unread` for
      both sources so the two cannot drift.
    - **Trash is a timestamp, never a delete**, and Restore is the same call
      with `false`. A message somebody wrote is the last thing to destroy on
      their behalf, and a Trash that cannot be undone is a delete button
      wearing a friendlier word. There is deliberately no delete endpoint —
      `make ops-ui` clears its own rows at the database, which is a different
      act from a person binning their mail.
    - **A refused send is kept as `failed`, not discarded.** It is the one a
      person most needs to find again, and dropping the row loses what they
      typed with it.
    - **Drafts are the author's own; Sent is shared.** An unfinished message
      is not something to put in front of somebody else, while "has anyone
      answered these people yet" is exactly what two people sharing an inbox
      ask.
  - **Write and Reply are one act and one endpoint.** Reaching a dealership
    we want to talk to is the same thing as answering one that wrote in, and
    the composer could only ever do the second — so the first meant leaving
    for a mail client, where the message is invisible to this system for good
    and goes out under whatever address that client is configured with rather
    than the one the deployment can prove it owns. A second endpoint for it is
    how one of the two stops going through `blocked_reason`.
  - **A reply reports the provider, not a green tick.** With the default outbox
    sender `sent: true` means recorded and nothing left the building, so the
    composer says *Not delivered* and quotes the provider's own words. It goes
    through `outreach_send.blocked_reason` like every other send: a composer is
    exactly where a rehearsal reaches a real prospect.
  - **Both halves of the envelope are the person's, and `SENDING_DOMAIN` is
    what makes that safe.** A provider verifies the *domain*, not the mailbox,
    so one verified `linerai.us` makes `founder@` and `cto@` both legal to
    send as on one key — a third person is a row in `users` and no new
    credential, which is the point: a per-mailbox password is a thing to leak.
    `outreach_send.identity_for` decides it once, for the send and for the
    line the composer shows, so the promise and the header cannot disagree.
    - **An address it cannot prove falls back, and says so.** Putting an
      unverified address in a `From` gets the whole message rejected, so
      guessing costs the send; swapping it quietly leaves somebody believing
      they wrote from an address they did not. The `Reply-To` is theirs
      either way — with two of us, a reply that always routed to `founder@`
      sent half of them to the wrong one.
    - **The check is on the bare address, never the display name.** A name is
      text somebody typed and it can contain an `@`; matching a domain
      against the rendered header is how a permission check says yes to the
      wrong thing. `formataddr` builds the header, because a name with a
      comma in it otherwise reads as two recipients.
    - **`can_send_as` is a method, not one shared function.** Resend
      authorises a verified domain and Gmail authorises Workspace
      impersonation, and those are different questions — the shared rule
      would have to be the loosest of the two.
    - **Ops only.** A dealership's outreach is from the dealership rather
      than from a person, and its `Reply-To` is the `reply+<token>@` address
      that routes an answer back into the buyer's timeline — not a header a
      rep's own address may take over.
  - **A Tailwind grid needs `grid-cols-1` at the base breakpoint.** Without a
    declared track the implicit one is `auto`, which sizes to its widest
    child's *min-content* — and the min-content of a `truncate` line is the
    whole untruncated string. So the card grew past a 390px phone and the
    ellipsis never appeared. `grid-cols-N` is `minmax(0, 1fr)`, and the
    `minmax(0, …)` is the part that lets it clip. `make shots` covers `/ops`
    at 390px, signing in a second time because the role is different.
- **The login form is rate limited per account, never per IP.** It is the one
  unauthenticated endpoint where guessing repeatedly gets you something, and
  the password is all that stands in front of a dealership's buyer list and of
  `/ops`. Keyed on the address being tried because behind nginx or Cloudflare
  every request carries the *proxy's* IP unless `--proxy-headers` is set — an
  IP key would let one bot lock out every real person at once, which is a
  worse outage than the attack. The limit bites identically on an address
  nobody owns, or it would answer "this account exists" to anyone who counted
  the 401s. A correct password clears the count, so four mistypes and a
  success do not leave somebody four attempts from a lockout. In process like
  `events.py`, for the same reason: one worker is already required.
- **The clock in the header is the showroom's, and it ticks in the browser.**
  Two faults in one line. `const now = new Date()` during render froze at
  whatever minute the page loaded, because nothing on a quiet dashboard
  re-renders; and formatting with no `timeZone` uses the *viewer's* device,
  while every timestamp under it is naive and means dealership-local. A
  manager checking in from another state read a clock an hour off the
  appointments below it, and the calendar's now-line was drawn at the wrong
  height for the same reason. `useNow()` schedules to the next minute boundary
  — no fetch, no poll, nothing reaches the server — and `zonedStamp` formats
  in `dealership.timezone`. The zone is named only when the viewer is
  somewhere else: always is noise, never is an hour-wrong clock.
  - **A hook cannot go next to the thing it is for.** `useNow()` was put
    beside the now-line calculation, which sits after `if (isLoading) return
    <Spinner />` — so the hook count changed between renders and React threw
    *Rendered more hooks than during the previous render*, blanking the page.
    `tsc` is silent on it; only opening the page finds it.
- **`PUBLIC_DEMO` opens the dashboard to anybody, as a rep, and it is off.**
  The point of a demo is being able to send someone a link, and the point of
  this setting is that it is the one line in `.env` that hands a stranger a
  dealership's buyer list — names, phone numbers, transcripts, and call
  recordings, which are somebody's actual voice. So: off by default (asserted
  on the class default, not on the running config, because a flipped default
  is invisible in a diff), a warning naming what is exposed at every boot, and
  only ever pointed at `make seed-demo` data.
  - **It mints a real session for a real rep account.** Not a bypass in
    `current_user`, and not a synthetic user: those would give every role check
    in the system a second path through it, and the one that matters —
    `require_manager`, which guards the team page and publishing the
    assistant's instructions — is the one nobody would think to test on that
    path. A public visitor is a rep in exactly the sense the rest of the code
    already means, and `make agent-check` asserts the door never opens as a
    manager.
  - **Opening the door does not authenticate anyone by itself.** A request with
    no cookie is still a 401; the visitor is signed in only by asking to be.
    That keeps one notion of who is signed in for the whole system, and it is
    what `make smoke` checks in both configurations.
- **One dealership at a time, one profile per prospect.**
  `backend/config/dealerships/<name>.yaml`, chosen with `DEALERSHIP=<name>`.
  There is still no dealership id on any table and multi-tenancy is still out
  of scope — switching is a reseed, not a second tenant. What the directory
  buys is not losing the prospect you are not currently demoing.
  - **A half-filled profile is refused, never seeded around.** A prospect's
    real address, phone and hours cannot be looked up from here, so a template
    is the expected state of a new one — and seeding from it would mint a
    dealership with a blank address that every surface then prints at a buyer.
    Filling the gaps with something plausible is worse: an invented address
    survives a demo and gets repeated back to a customer. `_check_profile`
    fails and names the fields.
  - **Brand is served, not stored.** `app/brand.py` reads the accent off the
    profile per request. Not a column, because `create_all` adds a table to a
    database that already exists and never a column; not per-row data, because
    which dealership this instance *is* already lives in a file. Only the
    accent family travels — `.theme-buyer` takes `--brand-accent` with the
    existing blue as its fallback, so every structural token still comes from
    the one theme layer and a prospect's colour cannot restyle the product
    into something unreadable. Validated to a hex and dropped otherwise: the
    value lands in a stylesheet.
  - **The made-up showroom is Riverside's, and does not travel.** Fourteen
    curated vehicles, the sample CSV lot, a populated yesterday and thirteen
    written policy answers are a fixture invented so no screen is ever empty
    on first run. Seeded into a prospect's instance that is not a head start,
    it is wrong data: their buyer searches the lot and is offered a Toyota
    Sienna from Cedar Falls, Iowa, then asks the doc fee and is told $189 —
    quoted verbatim, because that is exactly what `knowledge_entries` are for.
    `showroom_fixture` is opt-in and read from the profile rather than from
    its filename, so a file copied from `riverside.yaml` cannot inherit it by
    accident. A profile with no `knowledge:` of its own gets none, and the two
    rail chips that promise an answer are dropped with it: "What's your doc
    fee?" is a chip the dealership put on screen, and one that produces "I'll
    have to check" is a question it is asking on their behalf and cannot
    answer.
  - **The dealership's name is served, never written into a page.** Five
    surfaces printed the literal string — the chat header, the call header,
    the login subtitle and two lines on the buyer page — so a rebranded
    instance greeted a prospect's buyer as somebody else's showroom on the
    first screen of the demo. `/api/showroom/dealership` is public because two
    of the five are surfaces nobody has signed in to, and the greeting is
    built from the same name. Doing it through one hook fixed two more: the
    chat applied its brand from the session payload, which the *resume* path
    returns before, so a refresh dropped the accent back to the product blue;
    and the call applied its own on connect, so the page a buyer decides on
    was still wearing ours.
  - **`/showroom` is the link you send a prospect.** `/chat` is a chat window
    floating on nothing and cannot answer the question a dealer actually has,
    which is what this looks like on their site. Their logo, colour, address,
    phone, hours and real lot, with the assistant in the corner. Three rules:
    the cars come through `tools.offerable` — extracted rather than copied,
    because a car Liner refuses to discuss sitting on the page beside the chat
    window refusing to discuss it is the whole failure; the payload is
    composed rather than filtered from `vehicle_out`, which carries
    `rule_note` and `mention_count`, and a serializer that has to remember to
    drop a field will eventually forget; and the widget is an iframe of the
    real `/chat` (`?embed=1`), never a second chat client. It is not a copy of
    their marketing site and does not pretend to be one — a near-miss of
    somebody's own homepage looks worse than a clean page that is honestly
    ours.
  - The whole setup, `.env` line by `.env` line, is
    **[`docs/DEMO.md`](./docs/DEMO.md)**.
- **A listing page that already carries every field is crawled as one.**
  `ListAdapter` is a second rung beside the JSON-LD one: `extract` assumes a
  page is a vehicle, and Dealer Car Search puts VIN, price, mileage and a
  photo on every card of its search results. 481 vehicles is five list pages
  or 481 detail fetches, which is the difference between a polite crawl and
  one a dealer would be right to block.
  - **Written against a real capture, never a guess.** The `Adapter` docstring
    has said since the beginning that this needs a real site; the trimmed
    fixture in `ingest/fixtures/dealercarsearch_list.html` is that site, and
    `make smoke` parses it. `make capture URL=…` is how the next one is
    obtained — it saves the raw pages and says whether an adapter is needed at
    all, since a site emitting JSON-LD needs none.
  - **The price is read by class, not by label.** `price-0` is the asking
    price; the words next to it are "Craig's Best Price" at one store and
    "Internet Value Price" at another, and matching those breaks the day a
    dealer renames their own pricing.
  - **`SCRAPER_DEALER_ID` keeps one lot.** A DCS site can list three stores on
    one page, each card carrying its own dealer id — and this app holds one
    dealership. Without it Liner offers a buyer a car two hours from the
    showroom it says it is standing in.
  - **Body style and seat count are left empty rather than derived.** They
    live only in the sidebar filters, so those two `search_inventory` filters
    narrow nothing for such a dealer. A missing field is a smaller error than
    an invented one, and the keyword haystack still matches.
  - `run.method` records which rung read the pages. It was hardcoded
    `"jsonld"` — true while that was the only rung, and a lie the moment it
    was not, on the record somebody reads to find out why a field is missing.
- **A crawl keeps its own record, per dealership.**
  `backend/var/inventory/<dealership>/` holds `snapshot.json` and
  `photos/<VIN>.jpg`. The database answers "what is on the lot"; this answers
  "what did the site say, when", and an `IngestRun` cannot: it keeps a *diff*,
  so a field the adapter never read leaves no trace once a run is published.
  Per dealership because two prospects' cars in one folder is one prospect's
  inventory turning up in the other's demo.
  - **A car's picture comes from the dealer's own URL, and downloading it is
    the exception.** Their CDN is faster than this box and closer to the
    viewer, it costs no requests and no disk, and it stays current: a dealer
    who swaps a photo has swapped ours. `SCRAPER_SAVE_PHOTOS` is off, because
    a copy is a request per car, and what it buys is narrow — a demo that
    survives a venue's wifi or an image host that turns out to refuse
    off-site referrers. `_photo_for` in `ingest/pipeline.py` is the order:
    stored copy where one exists, the dealer's URL otherwise, the drawn
    placeholder last, which a CSV-imported lot still needs.
    - **The setting has to actually change what is served.** `publish()` kept
      the remote URL unconditionally and every surface renders `photo_url`,
      so turning it on downloaded 481 files that nothing ever read — a
      feature that costs a crawl and delivers nothing, which is worse than
      not having it. `make smoke` pins all three branches.
  - **One photo per car** — the one on the listing card, which is the only one
    a list crawl sees. The rest live on each detail page: 481 extra fetches
    and ~19,000 images, for pictures nothing in this product displays.
  - **A lookup must not create a directory.** `photo_path` runs on every
    `/api/photos` request, so while it shared `folder()` every placeholder
    ever drawn left an empty folder behind — all under the same fallback name,
    so the debris read as a real dealership's. `_dir` computes, `folder`
    creates, and `make smoke` fails if a lookup makes one.
- **Hours come from `hours_json`.** No page states its own.
- **Live means still being said, not merely still open.** Only the buyer
  closes a thread, so an abandoned tab stays open for ever — and *In progress*
  counted every chat anybody ever walked away from, which is a number a
  manager reads as *fourteen conversations happening right now*.
  `LIVE_AFTER_MINUTES` is thirty, and it is a conversation's own patience
  rather than a business rule: a buyer comparing two cars pauses for minutes,
  and a buyer who has gone is gone. The badge is split on the **same**
  predicate as the chip — a row badged In progress that the In progress filter
  does not contain is a page arguing with itself — so an open thread gone
  quiet reads *Gone quiet*, which is a third thing and not Closed: it still
  has an owner and still takes a reply.
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
  - **It counts conversations, not turns.** One eight-minute call with sixteen
    transcript lines read `Voice call 17`, directly under a header saying
    `1 thread`. Nobody reads that as lines of transcript — it says seventeen
    phone calls to the manager deciding whether this buyer has been chased
    enough. The unit is a time somebody made contact: one per conversation, one
    per email. `All` is the sum of the tabs beside it, not the row count, so
    the strip's parts add up to its total.
- **One fact, one answer, wherever it is read.** The overview's queues were
  three unconnected facts about the same people; the same shape turned up in
  four more places once looked for, and each is a fact written in one row and
  read from another with nothing keeping the two in step.
  - **An escalation belongs to whoever owns the buyer.** `assign_lead` decided
    this — giving somebody an owner claims everything of theirs that was
    waiting, because *Needs a person* asks whether a person has been **found**
    — and then only ever applied it at the moment of assigning. Two later
    events walked round it: `raise_handoff` on a buyer who already had a rep,
    and an inbound reply un-claiming one. Both put an owned buyer back in the
    queue, so a row wore *Needs a person* beside the name of the person who
    had them, and a manager could not tell a failed assignment from a lying
    badge. The rule lives in `app/escalations.py` and every writer calls it,
    the demo seed included — a fixture that breaks the invariant is a bug
    report about the product, and this one generated three in four.
    - **Claimed is not silenced.** The thread still sits at `handoff`,
      `handoff.triggered` still fires and now names the owner, and the
      escalation is on their timeline wearing its claim. What stops is asking
      for a person who is already there.
  - **Every way a call ends follows the same closing rule.** `close_conversation`
    would not close a thread at `handoff` — a buyer who has gone is still owed
    a call back — but `end_call` closed unconditionally, and hanging up is how
    most calls end. The row read Closed while sitting in Needs a person.
  - **A cancelled visit is not an appointment set.** `conversations.stage` is
    written once by `book_appointment`, so cancelling left the thread claiming
    a booking forever while the lead beside it derived the truth from
    appointment rows. It walks back to `contact_capture` — not to `opening`,
    which would have the rails greet a buyer who has already given their
    details — and only when nothing else of theirs is still standing.
  - **Somebody leaving hands their buyers back.** Deactivating dropped a rep
    off the roster and left their leads pointing at them: not unclaimed, so no
    queue asked anyone to pick them up, and not workable, because the owner was
    gone. Appointments are un-assigned, never cancelled. Escalations they
    claimed reopen only on threads that are still open — on a closed one,
    claimed is history, and reopening years of it would bury the live queue.
  - **The resolution ladder runs in both directions.** A stranger who writes to
    `sales@` before they are anyone is stored unresolved, correctly — but
    resolution ran once, at delivery, so when they chatted and booked the next
    day with that same address their earlier mail stayed a stranger's for good.
    `matching.claim_unresolved` re-runs `_place` when a buyer comes into
    existence: the same ladder, not a second copy of it.
- **A buyer changing their mind about the car is the thing a script breaks
  on.** The rails are a state machine, so once a thread reached
  `contact_capture`, "actually, tell me about the X5" was read as an answer to
  "what is your email?" — and the appointment was booked against the car they
  had explicitly moved off. Naming a different car now re-focuses at any stage.
  - **Only when there is a focus to change from.** With none, the opening turn
    should still search and offer a shortlist; jumping straight to the one car
    they named skips the three the lot actually has.
  - **A car they already own is not a car they are asking to see.** "I'm
    trading in my old X5" must not re-target onto ours, and that guard belongs
    in both places a make or model can move the focus — it was added to the new
    path first and the older `_referenced_vin` walked straight round it.
  - **A booked thread stays booked.** The appointment is real; only the focus
    follows them. Moving the stage back would make the row stop claiming a
    visit that exists, which is the disagreement the cancel path was fixed for.
- **Keywords split on non-alphanumerics, not on spaces.** `"Do you have a BMW
  X5?"` tokenised to `x5?`, matched nothing, and returned the three cheapest
  cars on the lot — while `"BMW X5"` and `"tell me about the BMW X5"` both
  worked, which is exactly what kept it invisible. The most natural phrasing a
  buyer can use is the one that has to work.
- **An appointment can be moved without being destroyed.** There was no
  reschedule, so a rep shifting somebody by an hour had to cancel and rebook —
  which mints a new row, losing the id, the assigned salesperson and the
  outreach sent against it, and leaves a timeline showing a cancellation beside
  a fresh booking rather than a move. A confirmed appointment drops back to
  booked when it moves: a buyer who confirmed Tuesday has not confirmed
  Wednesday.
- **Idempotency keys must only match live rows.** The booking card's key is
  deterministic on the slot, so a buyer who booked a time, cancelled, and asked
  for it again matched the *cancelled* appointment and got it back as
  `already_booked`. The card said booked, the calendar showed a cancellation,
  and nothing had been booked at all.
- **Every appointment event names the buyer it is about.** `assigned` and
  `confirmed` carried only an appointment id, so a buyer's page had no way to
  tell the event concerned them and no reason to refresh — the two panels
  showing the same visit drifted apart until somebody reloaded.
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
| Agent | **Stub by default; unscripted when a key is set.** The stub is a state machine over `conversations.stage` assembling replies from tool results — it only answers what someone anticipated. `LLM_MODE=live` puts a real model on the same eight tools and the same guards. Set `OPENAI_API_KEY`. The vendor HTTP call has never run here (no key); everything either side of it is exercised by `make agent-check`. |
| Email out | **Outbox by default.** A real `outreach` row, mirrored into the buyer's chat thread. Sends nothing. `ResendSender` is written and **never executed** — no `RESEND_API_KEY` here. Everything either side of the HTTP call is exercised by `make smoke`: the allow-list, the reply token, the row, the error path, the request body. |
| Email in | **Endpoint real and tested; the route in front of it is not.** `POST /api/inbound-email` verifies an HMAC, dedupes on message id, resolves by token → `In-Reply-To` → lead match, and stores what it cannot place. `make smoke` drives all of it. The Cloudflare Worker that feeds it has never been deployed. |
| Voice | **Built on OpenAI Realtime; off until `VOICE_PROVIDER=openai`.** `/call` is real WebRTC: the browser mints an ephemeral secret from us and talks audio straight to OpenAI. The mint call has never run here — no key, and `api.openai.com` is refused by the egress proxy — but the session body, the tool conversion and the voice-only prompt are asserted by `make agent-check`, and the relay, transcript and after-the-fact guard by `make smoke`. Still no fake provider. |
| Post-call transcription | **Written and never executed** — same missing key, same blocked host. The buyer's own track is recorded, the marks are stored, and the merge, cross-talk filter and transcript rewrite all run in `make agent-check` against a transcription handed over rather than fetched. Only the request to `/v1/audio/transcriptions` is unproven. |
| Inventory source | **The local database, and that is the real answer.** Rows arrive by seed, by CSV import or by hand, and `search_inventory` reads them. The scraper works against the fixture site but has no adapter for any real dealer site — no two are laid out alike, so that needs real URLs. An optional second source nobody has chosen is not a missing dependency, and it is not in the banner. |
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
- Don't remove the `OUTBOUND_ONLY_TO` check. It is what stops a rehearsal
  emailing a real prospect, and it is one setting away from being lifted
  deliberately (`OUTBOUND_ONLY_TO=everyone`) — which is the supported way to
  turn it off, rather than deleting the check.
