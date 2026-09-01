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
| `make ingest` | **Crawl the dealership's own site, every step narrated.** `ARGS=--publish` applies it |
| `make fixture-site` | Serve the scraper's fixture dealer site on :8100 |
| `make placeholders` | Regenerate `docs/PLACEHOLDERS.md` |
| `make build` | Build the frontend into `frontend/dist` (the API serves it in production) |
| `make stop` | Kill anything on 8000 / 5173 / 8100 |

Ports: backend **8000**, frontend **5173**, fixture site **8100**.
Logins: `dana.mercer@example.invalid` (manager) and `marcus.vale@example.invalid`
(rep), both `liner-dev` in development — those are the **fixture's** accounts.
A profile with its own `staff:` gets those people instead, with a password
generated and printed once; the seed reports whichever it actually created,
because printing a fixed pair meant naming two logins that did not exist.

The fixture pair come from `MANAGER_PASSWORD` and
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
- **The prompt is a brief, not a script.** `BRIEF` in `prompts.py` states the
  job in a paragraph — every turn either helps the buyer more, gets a way to
  reach them, or books them in — and leaves the selling to a model that
  already knows how to sell. `OPERATING_RULES` follows with what no executor
  can enforce. Together they are about 4KB; the whole prompt is 7.6KB and most
  of the rest is *data*: the dealership's facts, its pricing posture, the
  knowledge table it wrote, the greeting already on screen.
  - **It replaced 21KB of NEPQ script, and that was the point.**
    `agent/sales_method.md` was two thirds of every prompt this system sent,
    and a model handed two thirds of a script answers like one: long, staged,
    and reluctant to just say what a car costs. The file is **kept, not
    deleted** — it is the operator's document and not ours to throw away — and
    stays reachable through `assistant: sales_method: true` in a dealership's
    profile, because an archive nobody can switch on is a dead file. Default
    off, asserted by the gate.
  - **The gate pins the length.** `make agent-check` fails over 12,000 chars.
    Without a number, "shorten the prompt" is a thing that happened once and
    drifts straight back: every rule anybody adds is another paragraph and
    nothing pushes the other way. It is a bill as well as a behaviour — the
    prompt is the cached prefix on every turn of every conversation.
  - **A rule must not cite a section the model cannot see.** The old prompt
    said "this overrides section 5"; with the script gone that names nothing,
    so each was rewritten to say what it overrides rather than where.
  - **Every placeholder is answered, including the ones we have nothing for.**
    Left in braces, a model eventually types `{{CURRENT_CAR}}` at a buyer; left
    empty, `{{VDP_VIEWS}}` is an invitation to invent the demand figure the
    sentence around it exists to forbid. So both get a plain statement of the
    fact instead, and the gate fails on any `{{` surviving in either channel —
    on the archived method too, or the profile key that restores it is a
    switch onto a crash.
  - **A capability this system lacks is refused, not merely unmentioned.**
    These used to answer the method where it asked for them; nothing asks now,
    which makes them matter more rather than less. A model with no instruction
    will happily offer to text a buyer, send them the credit application, shoot
    a walkaround video or promise to follow up next week, and every one of
    those is a promise nobody here can keep.
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
- **A chip never puts words in the buyer's mouth.** `message_text` is sent as
  the buyer's own message and `save_captured_fields` records what it contains
  as `typed` — the provenance that means *the buyer said this*. One chip's
  text was a fixture buyer's name and address, so a real person tapping "Send
  it to my email" told Liner they were Jordan Reyes, and a rep then rang
  Jordan Reyes. Nothing pre-writable belongs at `contact_capture`: the
  assistant has just asked for a name and an email and a chip cannot know
  either, so there is no chip there and the composer is the answer.
- **A chip whose meaning is fixed answers itself, with no model turn.**
  "What's under $20k?" is a button the dealership put on screen and it can
  only mean `search_inventory(max_price=20000)`. Sending that to a model asks
  it to re-derive an intent somebody already decided, and pays a round trip in
  front of a buyer for the privilege. `rails.action_json` names one of
  `agent/rail_actions.py`'s handlers; the tool runs, the sentence is built
  from its result, and the guards run on it exactly as they would on a model's
  reply. Four rules:
  - **Free text always goes to the model.** This is not a return to the
    scripted assistant: a buyer who *types* "something cheap and reliable"
    gets a model reading the sentence, which is the thing being sold. Only the
    pre-written question is short-circuited, and a chip with no action — "Tell
    me about the first one", which is a reference to resolve — behaves exactly
    as it always did.
  - **The sentence is built from the arguments the search ran with**, so the
    two cannot disagree. A lead-in reading "under $20,000" over a `max_price`
    of 25,000 is what writing both by hand produces.
  - **A relative chip must not speak its bound.** "Anything cheaper?" means
    cheaper than what is on screen, and that price came from a *previous*
    turn — which is deliberately not sourced, because a price is re-read every
    turn precisely because it can change. Written with the number in, the
    guard rejected the whole reply and the buyer read the escalation line
    instead of three cars. The lead-in says "cheaper" and every figure in it
    is one the search just returned.
  - **A chip that needs cars on screen is not offered before there are any.**
    "Anything cheaper?" as an opener is a question about nothing.
    `requires_vehicle` does not cover it: these need the *list* the buyer is
    looking at, not the one car they have narrowed to.
- **One phrasing for a list of cars**, in `agent/phrasing.py`, shared by the
  stub and the chips. Two versions of "here are three cars" is how one channel
  starts quoting a price the other rounds — the same argument as `app/recap.py`.
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
  - **Liner answers email only when every brake says so, and by default none
    does.** `app/email_reply.py` runs the same `run_turn`, the same eight tools
    and the same reply guards as chat — a second copy of the loop is how one
    channel quietly stops running them — with `EMAIL_ADDENDUM` appended the way
    voice's is, and capped the same way.
    - **Email is live-only, and says so.** The stub is a state machine whose
      replies point at a booking card and rail chips — correct on a screen,
      nonsense in an inbox. Without that check `run_turn` reached straight for
      an unconfigured provider, `NotConfigured` came back up through a
      background task, and the result was indistinguishable from a buyer who
      had not written: the exact failure the receipts exist to prevent,
      arriving through the one path that had no receipt for it. Every refusal
      is now listed on `/api/email/agent`, because "it did not reply, is that
      on purpose?" is the question a person actually has.
    - **The sender's name comes over as `fromName`, not in `from`.** The
      Worker sends `message.from`, which is the *envelope* sender and
      therefore a bare address — a mail server does not put a display name in
      an envelope. Nothing read the parsed header's name, so every real buyer
      arrived "Unnamed buyer" while the tests, which put a display name in
      `from`, passed. Where the envelope carries none the signature is used
      instead: still a guess, still recorded as `inferred` on a captured
      field, and better than a rep opening the message to find what the row
      could have told them.
    - **The name ladder runs on every delivery, not only the one that mints
      the buyer.** It lived inside `_lead_from`, which a returning buyer never
      reaches — `_resolve` finds them by token or by address first — so anyone
      already on file without a name stayed unnamed for good, however many
      later messages carried a perfectly good one. `name_from_delivery` is
      that ladder, called from both paths, and a function rather than a second
      copy for the reason this codebase keeps relearning: two versions of what
      a person is called is how the buyer page and the mailbox start
      disagreeing. It needs no backfill — the next message a buyer sends
      names them.
    - **It fills a blank and never overwrites one.** A name already on the row
      came from somewhere with more authority — a booking, a rep who typed it,
      a lead document — while a display name is free text the sender's own
      client will put anything in (`sam's work laptop` is a real one). Letting
      a later email rename a buyer a rep has confirmed is how somebody ends up
      on the phone to the wrong name, which is the failure provenance exists
      to prevent.
    - **The conversation is minted on the first reply, not on the third
      exchange.** `Conversation` carries Take over, `agent_paused`, escalation
      and the message rows; without one, for two exchanges a rep could not grab
      the thread and the kill switch would be the only brake — on the turns
      where Liner is guessing most. The three-exchange threshold governs
      *presentation* instead: below it the row is in the inbound list, at it
      the buyer appears in the conversations list.
    - **Over the cap it hands over rather than answers half a message.**
      `MAX_BODY_CHARS` keeps the top when a message is long, because a person's
      ask is at the top — but the cap is a refusal, not a trim: confidently
      replying to the first four thousand characters of something whose
      question was at the bottom is worse than a slow answer.
    - **The pause is re-checked immediately before the wire.** A model round
      trip takes seconds, and a rep pressing Take over during one must not be
      overtaken by a message that was already in flight.
- **Liner does not answer email yet, and the brakes exist anyway.** Phase 4
  shipped before Phase 5 deliberately: there must never be a build where it
  can send mail on its own and cannot be stopped, and a brake first exercised
  on the day it is needed is one nobody has seen work. `app/email_agent.py`
  holds the whole decision and `make smoke` trips every one of them.
  - **Two switches, and the stricter wins.** `EMAIL_AGENT` in `.env` is the
    deployment saying this dealership has turned it on and needs a restart;
    the `email_agent` runtime flag is the one somebody throws at three in the
    morning. Both default off, so neither alone opens the door.
    - **A switch with no way to throw it is worse than no switch.** The
      endpoint shipped a phase ahead of any control for it, and the flag
      defaults off — so a deployment that set `EMAIL_AGENT=true` and
      `LLM_MODE=live`, did everything the runbook asked and waited out the
      cooldown was never going to get a reply, with nothing on any screen
      saying why. The setting said the feature was on and the product silently
      disagreed, which is the failure mode a default-off switch is *most*
      prone to: nothing errors, nothing is logged, and the symptom is
      indistinguishable from a buyer who did not write. The card is at the top
      of `/app/email` rather than behind the setup disclosure, and `make smoke`
      reads the page for the control — the same reason `SPA_PREFIXES` is read
      out of `main.tsx`, because nothing else here can tell a control that
      exists from one that was only ever described in a plan.
    - **All three facts are named separately, never collapsed into "off".**
      `.env` needs a restart, the switch takes effect on the next delivery and
      the model is a fourth variable; one boolean over the three sends whoever
      is reading to edit the wrong file, which is exactly how the hour above
      was spent. Nothing queued while the switch was off is answered when it
      is thrown, either — `schedule` refuses at intake and records the refusal
      on the receipt, so the next message is the one that gets answered.
  - **A kill switch cannot live in `.env` or in `AssistantSettings`.** It is
    reached for while something is going wrong, so it has to take effect on
    the next request — and `create_all` adds a table to an existing database
    and never a column, so a switch in a new column cannot reach the box that
    needs it. `runtime_flags` is a table with a closed vocabulary in
    `app/flags.py`; an unknown key is refused, because a free key/value store
    on a dashboard is where configuration goes to become unfindable.
  - **Headers before timers.** A cooldown does not *stop* a loop, it slows a
    vacation responder to forty-eight real emails a day, forever. RFC 3834's
    `Auto-Submitted` and RFC 2919/2369's `List-*` are the sender declaring
    itself a machine, and honouring them ends it on turn one. The Worker
    forwards exactly those seven headers — named rather than dumped, because a
    full header set is somebody's routing metadata and spam scores travelling
    through our webhook for no reason — and `make smoke` fails if the backend
    checks one the Worker does not send.
  - **Every reply waits, including the first, and one clock does both jobs.**
    `EMAIL_REPLY_COOLDOWN_MINUTES` is how long Liner waits *before* answering,
    not only the gap between answers — a dealership replying three seconds
    after a buyer wrote is obviously a robot, and the wait buys the thing that
    matters more: a window in which a rep reads the message and takes the
    thread over first. A rep answering inside it cancels the queued reply, so
    the buyer gets one email and not two. The gap between replies falls out of
    the same number rather than being a second rule.
    - **A row with a due time, not a sleeping task.** `asyncio.sleep(3600)` in
      a handler is lost on the next deploy, and this redeploys often;
      `email_replies_due` survives one and `app/email_replies.py` is a single
      in-process ticker that drains what the clock has passed. In-process like
      `events.py`, for the same reason — one worker is already required — and
      the drain claims a row before answering it, because two drainers would
      send a buyer the same reply twice.
    - **Every brake is re-run when it fires, never trusted from queue time.**
      The wait exists so a person can get there first, and a decision taken
      when the message arrived would defeat it. A rep having answered *within* the
    window stops Liner separately and says so, because in an inbox two emails
    from one dealership minutes apart have no window that makes them legible.
  - **The hourly ceiling trips the switch rather than refusing one message.**
    Per-correspondent stops one loop; a spam run across five hundred addresses
    walks past it, since every one is a first contact. A brake that needs a
    human to pull it is not a brake overnight, and the flag records that a
    machine set it — otherwise the morning after reads as somebody having
    turned it off by hand.
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
- **`/app/email` lists messages *and* people, and they answer different
  questions.** A message list is what you want hunting one send; a list of
  correspondents is what you want deciding who to answer next, because four
  messages with one buyer are one relationship. Same split as
  `/app/conversations`, for the same reason.
  - **The exchange counter lives in `app/email_threads.py` and nowhere else.**
    It decides two things read in different places — which tab a row is in, and
    whether the badge says the buyer is waiting — and two copies of "what
    counts as a back and forth" is how a header says 3 over a row that reads
    as 2. An exchange is **an inbound we answered**: a buyer who writes three
    times and gets one reply has had one, and the two they are still owed are
    what `waiting` is for rather than something to inflate the count with.
    `EXCHANGE_THRESHOLD` is one constant, so moving it moves the badge and the
    tab together.
  - **A stranger row is never waiting.** Since a person writing to a published
    address becomes a buyer, what is left unplaced is a newsletter, an
    out-of-office and a `no-reply@` mailbox — and flagging nine rows nobody
    will ever answer as owed a reply buries the one somebody has to.
  - **Unplaced mail addressed to *us* is not a dealership's to read.**
    `support@`, `founder@` and `cto@` are Liner's boxes and `/ops` already
    lists them; the dealership's mailbox was listing every unresolved
    delivery, so a stranger writing to our support desk was readable by every
    rep. Same realm rule `_lead_from` follows, arriving through the list.
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
      fixture cannot throw away demos real people booked with us. Its list
      also has to stay **complete**: four call tables and `inbound_emails`
      were added long after it was written and none was added to it, so on any
      database that had taken a call or received a reply — every box a demo
      has been rehearsed on — a reseed died on `DELETE FROM outreach` with a
      bare `FOREIGN KEY constraint failed` naming no table. `make reset-db`
      deletes the file first and never reaches it, which is why it stayed
      invisible; `make smoke` now reads the list out of the function and fails
      on any table pointing into it from outside.
    - **A delivery receipt is detached on a reseed, never deleted.**
      `inbound_emails` is the one table caught between the two halves: it
      points at `leads` and `outreach`, but an unplaced delivery is listed in
      *our* mailbox at `/ops`, because a stranger who mails `support@` has no
      buyer page anywhere else. So `_unplace_inbound` nulls the two foreign
      keys and leaves the envelope, the body and `outcome` exactly as they
      were — rewriting a receipt to say something other than what happened is
      the one thing a receipt must never do.
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
  - **`SENDING_FROM` is an address; the display name is served.** It used to
    be a whole header, and `.env.example` illustrated it with `Riverside Auto
    <support@linerai.us>` — the one line in that file people copy verbatim. So
    every deployment started from it mailed as a fixture car dealership: a
    prospect's buyers, and our own support replies, which are not from a
    dealership at all. It is the same failure as the five surfaces that
    printed the name into a page, in the one place nobody looked, and the same
    trap `SCRAPER_BASE_URL` was moved out of `.env` for — a second copy of a
    fact that goes stale the moment `DEALERSHIP=` changes. A name left in the
    setting is now dropped rather than sent.
    - **Two realms, two mailboxes, and that one is a routing fact.** A
    dealership's buyer mail goes from `sales@`; Liner's own from `support@`.
    They shared `support@`, and `is_ours` routes anything addressed there into
    `/ops` — so a buyer who composed a *fresh* message to the address printed
    on their booking confirmation reached Liner rather than the dealership,
    silently. Pressing Reply worked, because that goes to `reply+<token>@`,
    which is exactly why it stayed invisible. `SENDING_FROM` is the
    dealership's address and overrides the derived `sales@<SENDING_DOMAIN>`;
    ops reads `SUPPORT_EMAIL`, the setting `is_ours` already reads, rather
    than a third copy. There is deliberately **no address picker** in the
    dashboard: which mailbox a send leaves from follows from whose mail it is,
    and a chooser is one more way to send from something the provider has not
    verified.
  - **Two realms, two names, decided by the caller.** Mail from the
      dealership is signed with the dealership's own name out of the row
      (`outreach_send.dealership_from`); mail from `/ops` is signed `Liner`
      (`OPS_SENDER_NAME`). A support reply wearing the reader's *own*
      dealership name is worse than one wearing a stranger's, and one
      `SENDING_FROM` cannot be right for both.
    - **A display name on our own address is always legal**, and asking
      `can_send_as` about it is asking the wrong question — there is no
      authority to check when the mailbox is ours. Gated on it, the name was
      dropped on any deployment with no `SENDING_DOMAIN` set, which is every
      one before the domain is verified and exactly when somebody is looking
      at the result. `EmailSender.from_header` separates the two cases, and it
      is one method rather than the three identical copies it replaced.
  - **A Tailwind grid needs `grid-cols-1` at the base breakpoint.** Without a
    declared track the implicit one is `auto`, which sizes to its widest
    child's *min-content* — and the min-content of a `truncate` line is the
    whole untruncated string. So the card grew past a 390px phone and the
    ellipsis never appeared. `grid-cols-N` is `minmax(0, 1fr)`, and the
    `minmax(0, …)` is the part that lets it clip. `make shots` covers `/ops`
    at 390px, signing in a second time because the role is different.
- **A real person gets an account without a reseed.** Staff arrive through
  `_seed_users`, which only runs on a fresh seed, so putting a dealership's own
  manager on the system used to mean `make reset-db` — which deletes every lead
  on the box. `make add-user` is that, in the shape `make add-owners` already
  established: it writes to `users` and never `ops_users`, because a tool that
  could write to either is a fourth way to make the mistake the split exists to
  prevent. The password is generated and printed once rather than read from
  `.env` — an environment variable per person is one somebody has to add to the
  deployment, and the seed only reads them on a fresh database anyway. Running
  it twice reports the account and stops: they may have changed their password,
  and re-hashing silently locks them out with nothing saying why.
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
- **A timestamp on the wire says which of two kinds it is.** ECMAScript parses
  a bare date-time as *browser-local*, and `utcnow()` is naive UTC, so
  `isoformat()` alone put an unmarked instant on the wire: every relative time
  on every page was computed against a clock shifted by the viewer's own
  offset, and a reply that had just arrived read `5h ago` to somebody sitting
  at UTC+5. It read correctly on a machine set to UTC, which is every box this
  has been developed on. `serialize.stamp()` marks an instant; `serialize.iso()`
  stays bare for the wall-clock half — an appointment at 10:00 means ten at the
  showroom, and a zone on it would be a claim nobody can honour. The split is
  enforced on the wire rather than in the browser, because a frontend that has
  to remember which kind it is holding will eventually forget, and `make smoke`
  fetches seven endpoints and fails on any `*_at` with no zone.
- **Somebody who has not said who they are is named for the channel they
  used.** Every anonymous row read *Unknown caller*, which is two wrong words
  on a chat: nobody called. `book_appointment` is what mints a lead, so most
  live chats have none at all — and the row describing an anonymous 9pm
  question as a phone call was the one a rep most needed to read. The contact
  line under it went the same way: *No email — call back* was printed whenever
  the address was blank, telling a rep to do the one thing they could not do
  when there was no number either.
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
  - **A fact about the dealership goes in the profile; a fact about the box
    goes in `.env`.** That line decides where each setting lives, and getting
    it wrong has a specific cost. Their listing URL and their Dealer Car
    Search store id were environment variables, so switching `DEALERSHIP=` to
    another prospect and leaving `SCRAPER_BASE_URL` alone crawled the first
    dealer's site into the second one's instance — silently, because a
    successful crawl of the wrong site looks exactly like a successful crawl
    of the right one. They live in `inventory:` now; the env vars remain the
    fallback for the fixture site and for older deployments, and where both
    speak the profile wins, because switching dealership has to be one line.
    Which store to keep is read **per crawl** (`ListAdapter.for_dealer`) and
    not baked into the registered adapter at import, where it was whoever the
    process started as.
  - **Two halves, refreshed differently, and they must not disagree
    silently.** The name, address, hours and staff are rows and need
    `make reset-db`; the brand, the storefront copy and the crawl source are
    read from the profile file per request, but *which* file is
    `settings.dealership`, read once at startup. So reseeding without
    restarting leaves a dashboard saying Craig and Landreth over a storefront
    still wearing Riverside's blue, and restarting without reseeding does it
    backwards. Both read as a broken page and neither is, so
    `_report_dealership` names the profile at every boot and warns when the
    seeded name and the loaded profile differ.
  - **A profile carries its own people.** A prospect's instance shipping with
    Dana Mercer and Marcus Vale on the roster is the same failure as greeting
    somebody as Riverside Auto — invented names in every assignment picker,
    on a page their real manager is reading. `staff:` is seeded instead, with
    a password generated and printed once; `make add-user` is the other half,
    for a live box where a reseed would take the leads with it.
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
    - **Their copy lives in the profile's `site:` block, not in the
      component.** Headings, welcome text, hero, nav and social links are the
      dealership's own sentences; hardcoded in `Showroom.tsx` they are the
      "Riverside Auto" bug one level up, and the next instance greets somebody
      in Craig and Landreth's words. Every URL is validated to `https://` or
      `/` before it reaches an `href`, for the reason the accent is validated
      to a hex: it comes from a file an operator edits and lands in a browser.
    - **`brand.surface: dark` is read only here.** A dealership whose own
      site is orange on near-black gets a storefront that looks like it; their
      reps' dashboard does not change colour, because that is a working tool
      and `destructive` still has to mean *this one broke*. It picks the
      `.dark` class already in the token layer rather than carrying a value
      into a stylesheet, so it is validated to those two words and nothing
      else. Turning it on found a real bug in the page it was applied to: the
      root set `bg-background` and never `text-foreground`, which is invisible
      in light mode and black-on-black in dark — the dealership's own name,
      every card title and the whole footer disappeared.
    - **The browse filters are counted, and every image has a fallback.**
      "Chevrolet (74)" and the four price bands come from rows, because a
      filter promising 74 cars and showing 9 is worse than no filter. By Type
      is drawn only when the lot has body styles at all — a Dealer Car Search
      crawl leaves that field empty, so it would otherwise be ten links that
      all return nothing. The logo, hero and every car photo fall back on
      `onError`: they are hotlinked from the dealer's CDN, and a torn-page
      icon next to their own name mid-demo is the failure to prevent.
    - **The search box is a keyword box, not the chat.** Every word must hit,
      because "silverado 4wd" means both; `search_inventory` *scores* the same
      words instead, because it is answering a sentence and keeps its best
      guesses. What they share is tokenising on non-alphanumerics — `"BMW X5?"`
      split on whitespace gives `x5?` and matches nothing.
    - **There is no contact form, and that is the pitch.** Their real page has
      one. Reproducing it would be a form that posts nowhere; the assistant
      stands in its place and captures the same fields, answers, and books.
  - The whole setup, `.env` line by `.env` line, is
    **[`docs/DEMO.md`](./docs/DEMO.md)**.
- **A listing page that already carries every field is crawled as one.**
  `ListAdapter` is a second rung beside the JSON-LD one: `extract` assumes a
  page is a vehicle, and Dealer Car Search puts VIN, price, mileage and a
  photo on every card of its search results. 481 vehicles is five list pages
  or 481 detail fetches, which is the difference between a polite crawl and
  one a dealer would be right to block.
  - **A page that yields no new VIN ends the crawl.** Pagination is a request
    the *site* has to honour, and one that does not simply returns page one
    again — an unknown `pagesize`, a filter that resets, a proxy cache.
    Nothing in that response says it is a repeat: it is HTTP 200 full of cars.
    So the crawl read page one twenty-one times, reported "481 vehicles", and
    handed the diff forty-two copies of two cars. Measured against a server
    that ignores query strings, not reasoned. Stopping on *no new VINs* rather
    than on identical bytes, because a real page differs by a timestamp and is
    still the same page — and the repeat is recorded as an error rather than
    swallowed, because a silently truncated crawl is the one that marks the
    rest of the lot sold.
  - **A permissive default is not an answer, and must not be reported as
    one.** `robots.txt` returns "allowed" on a timeout, a 404 and a 500 alike
    — a site that never answered has not refused. Correct as a *default*, and
    a lie to print as "robots.txt allows this path": a crawl whose very first
    request had already timed out showed a clean permission check and then
    failed one step later looking like a different problem. `robots_verdict`
    returns the reason with the verdict.
  - **A connection failure is taken apart before it is reported.** DNS, TCP
    and TLS are three different problems with three different answers, and
    `ConnectTimeout` is one word for all of them. `make ingest` resolves the
    name, opens the socket and completes the handshake separately, prints what
    each did, and offers only the fix for the layer that actually failed.
    Timed out is not refused: refused is a machine saying no, timed out is
    nobody answering.
    - **A TCP failure rules out everything above it, and saying so is the
      most useful line it prints.** A CAPTCHA, a Cloudflare challenge, a
      user-agent filter, a rate limit and `robots.txt` are all *responses* —
      they need the socket, the handshake and the request to succeed first. If
      nothing connected, none of them can be the cause and there is no point
      hunting for one.
    - **A control host separates "them" from "us".** Outbound 443 failing to
      everything is this machine's network; failing to one host is that host
      or the route to it. Without the control the two print identically.
    - **An unexpected certificate issuer is named as interception.** A
      corporate proxy or a sandbox terminating TLS makes DNS, TCP and the
      handshake all look healthy while the interceptor is the thing saying no
      — measured here, where the certificate for a dealer's site comes back
      issued by Anthropic.
  - **`make ingest` is how you run it when it goes wrong.** The web button
    runs the identical pipeline and gives you a spinner and one line; a crawl
    can fail at robots.txt, at DNS, at a 403, at "no adapter matched", at "the
    adapter matched but every card was dropped", or at "it worked and the diff
    is empty" — six failures needing six different answers. The script imports
    `crawl_list` and `publish` rather than reimplementing them, so a crawl
    that works here works in the product. It writes nothing without
    `--publish`, and refuses to publish a run that would take most of the lot
    off sale: a car the crawl did not see looks exactly like a car that sold.
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
- **A disabled button is a tab that is always empty.** The rail carried a
  "Liner is answering" card from the mockups, and all three of its parts were
  claims rather than facts: a green pulsing dot asserting a health check
  nothing performs, an open-conversation count the Conversations nav badge
  already showed, and a **Pause Liner** button permanently disabled because
  pausing is per conversation and there is no dealership-wide switch. Saying
  so in a tooltip is better than a button that lies, and better still is not
  drawing a control for a thing that does not exist. Removed rather than
  reworded.
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
  buyer can use is the one that has to work — which is the rule the rest of
  `_words`/`_hits` follows too. Three more of the same shape, each invisible on
  fourteen hand-written cars and obvious on a real 486-car lot:
  - **Whole words, because a substring match scores the sentence rather than
    the car.** "do" is inside "Dodge" and "you" is inside "Bayou", so *"do you
    have any corvettes?"* ranked a Dodge Hornet in Blu Bayou first and never
    reached a Corvette. The buyer's own filler was doing the sorting.
  - **A lone letter is dropped and a lone digit is kept.** "a" is a whole-word
    match against every A-Class and A-Spec on the lot. No car is called "a";
    several are called 3 and 5.
  - **A plural and a nickname both resolve.** Nothing on a listing is plural,
    so "corvettes" scored zero against every row; and nobody in Louisville
    types "Chevrolet", which is 74 of the cars there. `MAKE_NICKNAMES` is
    curated for the reason `ORIGIN_BY_MAKE` is — there is no column for it and
    guessing gets it wrong.
- **A car with no published price is a listing state, not a gap.** 119 of
  Craig and Landreth's 486 cars are call-for-price, and their own site answers
  it with an enquiry form at the same URL. So `inquiry_url` is **derived, never
  stored** — `?mode=inquiry` on the listing, and only where there is no price —
  and it reaches the buyer as a link on the card rather than a URL in a
  sentence. Three consequences:
  - **`no_price_note`, not `price_note`.** `rule_hold_price` already owns that
    key, and two notes writing to one field means whichever runs last silently
    wins — the one that loses being a rule the dealer set.
  - **An unpriced car is not the cheapest car.** SQLite sorts NULL before every
    number, so `price.asc()` put all five results of "what have you got?" on
    cars the assistant then had to refuse to quote. They sort last and are
    reached only when nothing priced fits.
  - **The stub says the same thing the prompt asks for.** `phrasing.money`
    is the one formatter — a second copy in `stub.py` said "priced on request"
    where it said "price on request", harmless while every car had a price.
- **A car at another of the group's lots says so before a time is offered.**
  Craig and Landreth list three stores in one feed and the appointment is at
  the one address in `dealerships`. The *note* is raised only for a car that is
  somewhere else, compared against that address: on every row it would have the
  assistant announce the store it is standing in on every reply, and noise is
  how the one row that mattered stops being read.
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
- **One matcher decides who a buyer is, and a second address is a person's
  word, not a rule.** `leads.email` is one column and a buyer is not: somebody
  who chatted from a work address and later mails from a personal one is one
  person no rule here can see. So `lead_addresses` is written from the buyer
  page by a rep who knows, and `candidates_for` reads it as a rung between the
  primary email and the phone — exact like an email, and additive, so nothing
  about who an existing address matches changes. Nothing ever adds one on its
  own, and a shared domain or a shared name never will. An address that
  already belongs to somebody is **refused and named**, never moved: taking it
  would silently merge two buyers, which is the one failure this module exists
  to prevent. `claim_unresolved` runs over every address the buyer is known by,
  so linking one moves their earlier mail onto the timeline — a link with no
  visible effect is one a rep presses twice.
- **An email exchange reads as a conversation, in the timeline it already
  has.** Buyer left, us right, the sides a chat uses. Our sends used to be
  centred, which is right for a one-off follow-up into silence and wrong the
  moment there is a back and forth: a column of centred cards gives a rep no
  way to see who wrote which without reading every one. There is deliberately
  no second messaging screen — `/app/conversations/:id` redirects to the buyer
  for the same reason, and a thread is never readable in two places.
- **How a buyer arrived is read off `source`, not assumed.** A lead with no
  conversation got "arrived as a lead document", which was true while ADF was
  the only way in and a plain untruth once an email could mint one — somebody
  who wrote to `sales@` and has been answered twice was being described as a
  marketplace form.
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
| Email agent | **Written and off.** Liner can answer a buyer's email — same loop, same eight tools, same guards, plus `EMAIL_ADDENDUM`. Every brake in `app/email_agent.py` is exercised by `make smoke` against a fake provider, and the whole turn with it. `EMAIL_AGENT=true` plus the dashboard toggle turns it on; both default off. The vendor HTTP call has never run here. |
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
