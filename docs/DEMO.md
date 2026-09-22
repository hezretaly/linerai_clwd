# Running a demo for a real dealership

Written for Craig and Landreth Cars, but nothing below is specific to them
except the four values in step 1 — a second prospect is a second profile.

The rule this follows is the one the whole codebase follows: **narrow, not
fake.** Every step here either turns on something real or is skipped, and a
step you skip makes a capability report itself unavailable rather than
pretending. Nothing here invents a credential, and nothing here fills a gap
with a simulated result.

---

## What you are building

| Surface | URL | What it is |
|---|---|---|
| Their front page | `/<store>` — `linerai.us/alsbou` | Their home page, in their own design: `frontend/src/storefronts/<store>/Landing.tsx`. A dealership with no folder yet gets the plain `default/` page. Chat widget in the corner. **The link you send them.** |
| Their inventory | `/<store>/showroom` | Their list, `storefronts/<store>/Showroom.tsx`. The front page's search and any tile land here already narrowed (`?q=`, `?body_style=`) — that query string is the one contract every design shares. |
| The chat | `/chat` | The same assistant, full screen. The widget is an iframe of this. |
| The call | `/call` | Real WebRTC to OpenAI Realtime. Only appears when voice is on. |
| The dashboard | `/app` | What their reps and manager would use. |
| Ours | `/ops` | Liner's own: demos booked with us, mail people sent us. Not theirs. |

---

## Step 1 — Fill in their profile

`backend/config/dealerships/craigandlandreth.yaml`. Four things are already
in it, read off their own inventory page and cross-checked against the
schema.org `AutoDealer` block in its footer: **name, address, phone,
timezone.**

**Hours are filled in** from their own page — note Fri/Sat close an hour
earlier than Mon–Thu, which matters because `check_availability` builds the
slots it offers a buyer straight out of these values.

**Their front page is in the profile too** — the `site:` block carries their
heading, welcome copy, banner images, nav and social links, all copied from
their own home page. `/showroom` renders from it. A profile with no `site:`
block gets a plain storefront, which is what Riverside gets and is perfectly
honest.

Four of its keys exist for things dealers do differently and are each optional:

| Key | What it is |
|---|---|
| `hero_images` | Their rotating banner, in their order. `hero_image` is the one-image spelling and both are read. Each is tried and a broken one leaves the rotation, so one dead URL does not take the banner down. |
| `cta` | The one nav item they draw as a *button* rather than as text — "Get Pre-Qualified" on Alsbou's. Move it out of `links:` or it renders twice. |
| `price_label` | What they call the number on a card: "Advertised price". No key, no label — never a guessed one. |
| `social` | Left empty where their markup has none. A social link that goes nowhere is worse on their own storefront than none at all. |

**Their livery is in** — orange on near-black:

```yaml
brand:
  accent: "#f26a21"
  surface: dark       # /showroom only
```

The accent was read off a screenshot of their home page **by eye, not
sampled** — the egress proxy refuses their site and their image CDN, so
nothing could fetch either. If it is off, that one line is the fix.

`surface: dark` is read **only by `/showroom`**. Their storefront should look
like their site; their reps' dashboard is a working tool and does not change
colour because a prospect's marketing site is dark.

`chrome:` is the third answer for the dealer whose header and footer are dark
over a white page — Alsbou's are black with a dark grey contact strip, which is
neither of the two things `surface` could say:

```yaml
brand:
  accent: "#bf933d"
  surface: light      # the page between the chrome
  chrome: dark        # the header, the contact strip and the footer
```

Both are two words rather than two colours, and deliberately: they pick a
palette that is already in `liner-theme.css` instead of carrying a hex into a
stylesheet, so only the accent family ever travels. `chrome:` follows
`surface:` when it is not stated, so a wholly dark site still sets one key.

Where the seed **refuses to run** is a missing `hours`. That is deliberate:
opening times cannot be looked up from here, and an invented hour is an
appointment nobody is there for. Filling a gap with something plausible is
worse than leaving it — an invented address survives a demo and gets repeated
back to a customer.

**`knowledge:` has their doc fee and nothing else yet** — $690 at Louisville
and Bullitt County, $260 at Clarksville, confirmed by Austin. Policy answers
are returned to a buyer verbatim and never composed, so every topic that is
still blank makes Liner say a person will check rather than inventing a
number. Riverside's `$189` is a fixture and is deliberately *not* inherited.

The profile carries eight more topics as commented templates with the question
to ask beside each — trade-ins, deposits, financing, warranty, test drives,
vehicle history, out-of-state buyers, payment methods. Two chips on the buyer's
screen are driven by this list and are only drawn when there is an answer
behind them, so filling in Trade-ins puts a second one there. Out-of-state is
the one to push for: they are ten minutes from Indiana and already selling
into it from Clarksville.

---

## Step 2 — `.env`

Copy `.env.example` to `.env` and set these. Anything not listed keeps its
default.

### Always — and this is the line people miss

```dotenv
DEALERSHIP=craigandlandreth
```

**It must be in `.env`, not just prefixed onto the seed command, and the
backend must be restarted after you add it.** There are two halves and they
refresh differently:

| What | Comes from | Refreshed by |
|---|---|---|
| Name, address, hours, staff | rows | `make reset-db` |
| Brand, storefront copy, crawl source | the profile **file** | a restart (the *choice* of file is read once at startup) |

Reseed without restarting and the dashboard says Craig and Landreth over a
storefront still wearing Riverside's blue. Restart without reseeding and it is
the same thing backwards. Both look like a broken page and neither is.

The boot log names the profile it loaded and **warns loudly if the two
disagree** — check `.logs/backend.log`:

```
INFO  liner: dealership: Craig and Landreth Cars (profile craigandlandreth.yaml)
```

### The assistant, unscripted

```dotenv
LLM_MODE=live
OPENAI_API_KEY=sk-...
```

Without these the assistant still runs — it calls the same eight tools, books
real appointments and obeys the same guards — but the wording is canned, and
the chat says so in a banner the prospect will read. **Set them.** This is the
one thing a dealer is actually judging.

### Their inventory — nothing to set

**Their real lot is committed and `make reset-db` rebuilds it with no network
at all.** All 486 cars across the three stores — Louisville 240, Clarksville IN
176, Bullitt County 70 — live in
`backend/fixtures/craigandlandreth/inventory.csv`, exported by hand because
their site refuses this crawler below the HTTP layer (the TCP connection never
opens, so no user agent and no header changes it — see `make ingest`). The
profile's `inventory.fixture_csv` points at it and the seed puts it through the
same CSV importer a dealer's own upload uses. Re-run `make ingest ARGS=--publish`
from a machine that *can* reach them and the crawl takes over; the VINs are the
same, so it is a diff rather than a second lot.

Two things about that lot are worth knowing before the demo:

- **119 of the 486 carry no price.** Liner will not quote or estimate one — it
  points at the dealership's own enquiry form (their listing URL with
  `?mode=inquiry`, which the card renders as a link) and offers a visit first.
  Ask it about a call-for-price car on purpose; that answer is a good one.
- **A car at Clarksville or Bullitt County says so before Liner offers a
  time**, because the appointment is at the one address in the profile.

Their listing URL and their store id are **in the profile**, not here. They are
facts about the dealership, and as environment variables they were a trap:
switch `DEALERSHIP=` to a second prospect, forget to change `SCRAPER_BASE_URL`,
and you crawl the first dealer's site into the second one's instance —
silently, because a successful crawl of the wrong site looks exactly like a
successful crawl of the right one.

`SCRAPER_BASE_URL` and `SCRAPER_DEALER_ID` are still read as the fallback, for
the local fixture site and for deployments written before this. Where the
profile says something, the profile wins, and the import screen prints which
it used before you press the button.

Optional:

```dotenv
SCRAPER_SAVE_PHOTOS=true    # download each car's photo instead of hotlinking
```

Off by default, and the default is the better answer: their CDN is faster than
your box and closer to the viewer, costs nothing, and stays current when they
swap a picture. Turn it on only as insurance against a venue with bad wifi.

### Voice

```dotenv
VOICE_PROVIDER=openai
VOICE_MODEL=gpt-realtime-mini
```

Empty `VOICE_PROVIDER` means voice is off even with an OpenAI key present —
taking calls is a decision a dealership makes, not a side effect of
configuring chat. The key is shared with the chat agent; `VOICE_PROVIDER_KEY`
exists only to bill voice to a different project.

`gpt-realtime-mini` is roughly a third of the flagship's price and is very
likely enough for qualifying a buyer and booking a slot. The cost report
re-prices itself from this line — the rates follow the model.

With `VOICE_PROVIDER` unset, `/showroom` draws no Call button at all. That is
on purpose: a button that opens a page saying voice is unavailable is worse
than no button.

### Switching a channel off without unsetting it

```dotenv
CALLING=false
TEXTING=false
```

Two switches about what is *shown*, separate from whether the provider is
set up. `CALLING=false` takes the Call button off every storefront and makes
`/call` refuse to start; `TEXTING=false` takes "Text them" off the buyer page
and refuses a send. Receiving is never gated — a call or a text that arrives
is still recorded. Use them for a line that is half set up: off the screen
rather than failing in front of a buyer, with the credentials left in place
for the day it is turned back on. `/api/integrations` reports the channel as
*switched off* and names the setting, so nobody goes looking for a missing
key that is in fact present. Both need a restart.

### Email

```dotenv
EMAIL_SENDER=resend
RESEND_API_KEY=re_...
SENDING_DOMAIN=linerai.us
SENDING_FROM=sales@linerai.us
WEBHOOK_SECRET=<openssl rand -hex 32>
OUTBOUND_ONLY_TO=everyone
```

Five things, and each breaks differently:

- **`RESEND_API_KEY`** — sending. Breaks loudly: the next send quotes the
  provider's own error.
- **`SENDING_DOMAIN`** — the domain must be verified in Resend. It also builds
  the `Reply-To: reply+<token>@` that routes a buyer's answer back into their
  timeline.
- **Each dealership has its own mailbox on that domain, from its profile.**
  `mailbox: alsbou` in `alsbou.yaml` makes Alsbou's mail go out as
  `Alsbou Motors <alsbou@linerai.us>`, and mail *to* that address is
  filed in Alsbou's store — `craigandlandreth@linerai.us` likewise for Craig
  and Landreth. Left out, the local part is the website's host without its
  last label (`alsboucars.com` → `alsboucars`), which is exactly why Alsbou's
  is written in: the shorter `alsbou` is what the Worker's list carries, and
  the two have to be the same string. The provider verifies the
  domain, so every mailbox on it sends on the one key: a new dealership is
  a line in its profile, **plus** an entry in the Worker's
  `ALLOWED_RECIPIENTS` in `wrangler.jsonc` and a `wrangler deploy`, or
  Cloudflare drops its mail before it reaches us. `make smoke` fails on a
  profile whose mailbox is missing from that list.
  - **No new Cloudflare rule per dealership**, as long as the catch-all
    route to the Worker is in place — and it has to be anyway, because
    `reply+<token>@` addresses are minted per send and cannot be enumerated
    as rules. So the catch-all already carries `alsbou@` and
    `craigandlandreth@` to the Worker; what decides whether they are *kept* is
    `ALLOWED_RECIPIENTS`, which is a Worker deploy and not a dashboard edit.
    That filter runs before any receipt is written, so an address missing
    from it leaves no trace anywhere — the `founder@` failure, one
    dealership at a time.
  - `make stores` prints each store's address beside its sign-in, which is
    the quickest answer to "does this dealership have a mailbox yet".
- **`SENDING_FROM` is an address, not a header**, and it is the fallback for
  a dealership with no mailbox — the fixture, which has no website. Do not
  put a name in it. The display name is served: outreach goes out under the
  dealership's own name read from its row, and mail from `/ops` goes out as
  `Liner`. A name written here is dropped — it used to be sent, and
  `.env.example` illustrated the line with "Riverside Auto", so anyone who
  copied that file mailed their prospect's buyers as a fixture dealership.
- **`WEBHOOK_SECRET`** — shared with the Cloudflare Worker, and the only thing
  in front of an endpoint that writes into a buyer's history. Set the identical
  value on the Worker with `wrangler secret put WEBHOOK_SECRET`.
- **`OUTBOUND_ONLY_TO`** — gates sending only. Empty refuses every send; a
  comma-separated list allows those addresses; the word `everyone` lifts the
  limit. **For a rehearsal, list your own address.** Only widen it to
  `everyone` when the demo is live, and know that you are doing it: this is
  what stops a rehearsal emailing a real prospect.
- **Inbound has no filter at all.** Anyone may write in, and a reply that
  cannot be placed is stored rather than dropped.

Receiving needs the Cloudflare Worker deployed and MX pointed at Cloudflare
Email Routing — see `backend/app/integrations/email/worker/README.md`. Sending
breaks loudly; **receiving breaks silently**, in a route configured outside
this app, and looks exactly like a buyer who did not write back. That is why
`/app/email` exists: it records every delivery, including refused ones.

### Passwords, if this is going on a real host

```dotenv
ENV=production
SESSION_SECRET=<openssl rand -base64 32>
MANAGER_PASSWORD=...
REP_PASSWORD=...
FOUNDER_PASSWORD=...
CTO_PASSWORD=...
```

With `ENV=production` startup refuses to boot until each is real and no two
match. On a laptop, skip all of this — the dev defaults are fine.

### Never, on a real prospect's instance

`PUBLIC_DEMO=true` hands anybody with the URL a dealership's buyer list —
names, phone numbers, transcripts, and call recordings, which are somebody's
actual voice. Only ever point it at `make seed-demo` data.

**Delete the line (or set it `false`) to put the login back.** It is off by
default, so an absent line is a closed door.

---

## Step 3 — Build the database

```bash
make install
make reset-db            # reads DEALERSHIP from .env
```

You should see:

```
Seeded 0 vehicles, 0 leads, 0 conversations, 0 appointments, 17 rails.

Craig and Landreth Cars carries no showroom fixture, so the lot is empty and
there is no demo history. Their cars come from their own site: ...
```

**An empty lot here is correct, not a failed seed.** Riverside's fourteen
curated vehicles, its sample CSV lot and its populated yesterday are a fixture
invented for a dealership that does not exist; seeded here they would put a
Toyota Sienna from Cedar Falls, Iowa in front of a Louisville buyer.

---

## Step 3b — Their people

Austin is **in the profile**, so `make reset-db` creates him and prints a
generated password once:

```yaml
staff:
  - { name: Austin, email: manager@craigandlandrethcars.com, role: manager }
```

**The address is on their own domain, and that is the rule.** A login sheet
is the first thing a dealership reads, and an address at a domain that is not
theirs reads as a test account — the same failure as being greeted as
Riverside Auto, in the one place nobody looks. `manager@` rather than a
person's name where we do not know one: a role mailbox at their own site is
honest about being a role, and it is the only login here that can be derived
rather than invented. A profile that names nobody gets exactly that one
account; it does **not** fall back to Riverside's nine, which used to put
`dana.mercer@` on the prospect's own domain. A profile with neither a
`staff:` list nor a `website_url` is refused, because the only domain left
would be the fixture's.

A prospect's instance no longer ships with Dana Mercer and Marcus Vale on the
roster — a real manager reading four names they have never heard of is the
same failure as being greeted as Riverside Auto.

To add somebody **without** a reseed:

```bash
make add-user EMAIL=someone@theirdomain.com NAME="Their Name" ROLE=rep
```

`manager` sees every lead, the team page, the assistant settings and can
publish; `rep` works the floor. It prints a generated password **once** —
only the bcrypt hash is stored — and running it again for the same address
changes nothing, because they may have set their own since.

Change one later with `make set-password EMAIL=...`. Both are safe on a box
with real bookings on it; `make reset-db` is not.

### Several dealerships on one box: a manager and a rep on each

**One command seeds every dealership, each with its own people:**

```bash
make reset-all          # every profile: drop, seed, print that store's logins
sudo systemctl restart liner
```

Each store's block prints its own manager and reps — the profile's `staff:`
— with passwords shown once. `make reset-db` is **one store**, whichever
`DEALERSHIP=` names; on a host serving several it seeded the one in `.env`
and left the others with no database, which is what a storefront reading
*"not set up on this host yet"* means.

Every account-writing command below acts on **one store — whichever
`DEALERSHIP=` names** — so to put extra people on two dealerships you run it
twice with the variable set each time. Nothing is copied between stores; a
person exists in exactly one dealership's `users` table and can sign in to
exactly that one.

```bash
# Alsbou Motors -- the seed already made manager@alsboucars.com; these are extra
DEALERSHIP=alsbou make add-user EMAIL=sales@alsboucars.com NAME="Their Rep" ROLE=rep

# Craig and Landreth Cars -- likewise manager@craigandlandrethcars.com
DEALERSHIP=craigandlandreth make add-user EMAIL=rep@craigandlandrethcars.com NAME="Their Rep" ROLE=rep
```

Each prints a password once. To choose one instead:
`DEALERSHIP=alsbou make set-password EMAIL=manager@alsboucars.com`. `make stores`
lists every store with its mailbox and its manager sign-in, and whether it has
a database yet; a store that has none
needs `DEALERSHIP=<slug> make reset-db` first (which also creates whoever is
in the profile's `staff:` and prints their passwords).

**If a storefront says "This dealership is not set up on this host yet"**,
that is exactly what happened: `/<slug>` is being served but that store's
database was never created there. `make stores` shows which are seeded;
`DEALERSHIP=<slug> make reset-db` creates it (and prints that profile's
staff logins), then restart. The API answers 503 with the same sentence
rather than opening the store, because connecting to SQLite creates the
file, and a request that minted an empty database used to leave `make
stores` reporting a dealership that was not there.

**Where they sign in.** One form, `/login`, and the server finds the store
that holds the address: an Alsbou manager lands on `/alsbou/app`, Craig's on
`/craigandlandreth/app`. A URL that names a store — `/alsbou/login` —
searches only that store.

**What each can do, and what is enforced rather than hidden** — every line
below was measured with four test accounts on two stores, not read off the
code:

| Signed in as | Own dashboard | The other dealership | `/api/ops` | Manager-only writes (team caps, publishing the assistant's settings) |
|---|---|---|---|---|
| A dealership's **manager** | 200, that dealership's name | 403 *"That session belongs to a different dealership"* | 403 *"Liner staff only"* | 200 |
| A dealership's **rep** | 200 | 403 | 403 | 403 *"Managers only"* |
| `founder@` / `cto@linerai.us` (owner) | — | 403 *"That account is Liner staff"* on **every** dealership, including the unprefixed default | 200 | — |

The session cookie carries the store and the realm, and a mismatch is refused
at the session rather than by any page — so it holds for a direct API call,
not only for what the sidebar happens to show. The dashboard itself is one
set of pages for every dealership, named for the store the signed-in person
belongs to; `/ops` is Liner's own and is never per-store. A dealership's
manager changes only that dealership's assistant settings, team and
inventory; nothing on `/app` can reach `ops.db`, and nothing on `/ops` can
reach a dealership's file.

---

## Step 4 — Import their cars

**This has to run somewhere their site is reachable.** It cannot run in the
Claude Code sandbox — the egress proxy refuses `craigsbestcars.com` — so run
it on your laptop or the demo server.

```bash
make dev
```

**Run it from the terminal the first time** — the web button gives you a
spinner and one line of error, which is useless when it fails:

```bash
make ingest                        # crawl, report everything, write nothing
make ingest ARGS=--publish         # ... and apply it when the numbers look right
make ingest ARGS="--pages 2 --save-html"    # short run, keep the pages to read
```

It narrates every stage — robots.txt, the HTTP status and body, which adapter
matched, per-field fill rates, the diff — and every failure names what to do
next. Without `--publish` nothing is written to the database at all, so it is
safe to run on a live box.

It also **refuses to publish a crawl that would empty the lot**: a car the
crawl did not see looks identical to a car that sold, so a run cut short by
`--pages` or by errors would take the rest off sale.

The web button at `/app/inventory/import` runs the identical pipeline. It:

1. checks `robots.txt` for our user agent, and stops if it says no;
2. recognises Dealer Car Search and reads the *list* pages — five pages of 100
   rather than 481 detail fetches, which is the difference between a polite
   crawl and one a dealer would be right to block;
3. keeps only cards whose `data-dealer-id` is `1123`;
4. writes `backend/var/inventory/craigandlandreth/snapshot.json` — every field
   it read, per car, plus the rows it could not read;
5. shows you a diff. **Nothing reaches the live table until you press
   Publish.**

Check the run before publishing. Two fields come back empty for this platform
and that is expected: **body style** and **seat count** live only in the
sidebar filters, so those two `search_inventory` filters narrow nothing for
this dealer. A missing field is a smaller error than an invented one, and
"third row" still matches through the keyword haystack.

Then open `/showroom` — their cars are on it.

**If the crawl fails**, `make capture URL=<their page>` saves the raw HTML and
tells you whether an adapter is needed at all. A CSV export from their DMS is
the other way in, at `/app/inventory/import`; cost, margin and salesperson
columns are dropped before a row is built.

**Each car's options list is what Liner answers equipment questions from** —
"is it a three-row", "does it have a tow package", "heated seats?". A dealer's
own listing prints it under *Vehicle Options*, a hundred lines per car, and
there are two ways to get it in:

- a CSV column called `options` (or `features` / `equipment`), split on `;`,
  `|` or newlines — so a cell holding that block pasted one per line imports
  as-is;
- the **Options and equipment** box on the car's drawer at `/app/inventory`,
  where the block can be pasted straight from the dealer's page. It is saved
  as a manual edit, so the next crawl does not blank it.

The list crawl does not read detail pages, so a crawled lot has no options
until one of those runs. A crawl rung for detail pages needs a real capture
of one first: `make capture URL=<a vehicle's own page>`. What the list does
not name, Liner says a colleague will confirm — it never reasons it out from
the model in general — and asks for a number so the answer can reach them.

**One car's full story — its Carfax, its service history, the whole
specification — is a file, not a column.** For the car a demo is going to be
about, write it down once:

```
backend/fixtures/<dealership>/details/<VIN>.md      # e.g. alsbou/details/WA1VABF71JD050557.md
```

The filename is the VIN, upper or lower case. Plain markdown, laid out however
reads best; `alsbou/details/WA1VABF71JD050557.md` (the 2018 Audi Q7) is the
worked example. Then reseed that store and restart:

```bash
DEALERSHIP=alsbou make reset-db     # prints "loaded written detail for N vehicle(s)"
make dev                            # a running server keeps the deleted database open
```

Liner reads it only when it looks that one car up, never in a search, and is
told to answer from it and never go beyond it. Two things are worth doing when
you write one:

- **Keep what the listing claims apart from what is confirmed.** A dealer's
  marketing copy often says a trim "typically comes with" a third row or a
  360° camera. Put those under a heading like *Not confirmed on this car*, so
  Liner says the listing mentions it and hands the buyer to a colleague,
  rather than promising equipment that may not be there.
- **A VIN the seed cannot find is reported, not guessed at.** The line says
  which file it skipped — usually a car that has sold or a typo in the name.

---

## Step 5 — Rehearse

Run each of these once before they are watching.

```bash
make smoke      # the gate: booking, confirm, assign, outreach, events
make accept     # one buyer end to end across every channel
```

Then by hand:

| Check | Where | What should happen |
|---|---|---|
| Chat | `/showroom`, press **Chat with us** | Their name in the bar, their colour, real cars, a booking card |
| The card | ask "can I come see it?" | Days and times from `check_availability`, not composed by the model |
| Call | `/showroom`, press **Call us** | A tone, then the assistant. It is recorded — the consent line is above the button |
| Email | `/app/leads/:id`, compose | With `OUTBOUND_ONLY_TO` set to your address, it arrives |
| Reply | answer that email | It appears on the buyer's timeline, and in `/app/email` |
| Dashboard | `/app` | The booking you just made, live, without a reload |

**The reply is the one to rehearse properly.** It is the half that fails
silently, and the failure is indistinguishable from a buyer who did not write
back.

---

## Step 6 — Putting the chat on their own website

The assistant goes on a dealer's site as an iframe of the real `/chat`, which
is the same thing their storefront here already does. One line, in their
page's `<body>`:

```html
<script src="https://linerai.us/alsbou/embed.js" defer></script>
```

That draws the corner bubble and opens the chat in a panel — a sheet on a
phone, a card on a laptop. Optional attributes: `data-color="#c8a04a"` for
their brand (a hex, or it is ignored), `data-side="left"`, `data-label`,
`data-title`, and `data-store` if the page cannot carry the slug in the URL.

A bare iframe still works where they would rather place it themselves:

```html
<iframe src="https://linerai.us/alsbou/chat?embed=1"
        style="border:0;width:400px;height:620px"
        title="Chat with us"></iframe>
```

**The store slug is not optional.** `/chat` without `/alsbou` loads the
*default* store's assistant, so their buyer is answered out of another
dealership's inventory — and from their page it looks completely normal. That
exact bug reached a real deployment once already.

Nothing about CORS needs configuring, and that is worth knowing rather than
discovering: the iframe loads *our* document, so every request it makes is
same-origin and our backend never sees their domain on any API call. It also
means no third-party cookie is involved — the conversation id lives in
`localStorage`, which browsers partition per *(their site × our origin)*, so a
visitor's thread is scoped to the dealer site they are on and is not shared
with our own storefront. That is the isolation you want, and it is free.

Three things to set or check:

- **`embed_origins:` in their profile.** Until it names their site, the page
  refuses to be framed anywhere but here — `frame-ancestors 'self'`, and the
  browser renders a blank frame. Both spellings of the host (`www` and the
  apex), because a dealer serves pages from both and an embed that works on
  one and is blank on the other is the worst way for this to fail. Apply it
  with a restart; `make smoke` asserts it against a real build.
- **A CSP on *their* side**, if they have one, needs `frame-src
  https://linerai.us`. Dealer platforms usually do not set one, but it is the
  first thing to check if the frame comes up empty.
- **`allow="microphone"`** on the iframe, and only if `CALLING=true`.
  Without it the Call button inside the frame fails silently.

**The slug is in the script's own URL, and that is deliberate** — a dealer
copies one line and the line already names them. The script logs which store
it resolved, because opening the wrong one is otherwise silent and looks
entirely normal.

**It is a third widget and cannot share code with the other two.** Their page
is somebody else's HTML with no build step of ours, so `frontend/public/embed.js`
is plain JavaScript with its own styles. What keeps that duplication honest is
how little it may do: a button, a panel, and an iframe of the real `/chat`.
Every rule about what the assistant may say lives on the far side of that
frame. `make smoke` fails if it grows a `fetch(`, names a dealership, or stops
using a shadow root; `make shots` mounts it on a page whose CSS is hostile on
purpose and checks their stylesheet cannot reach our button.

**The ceilings are what stands between a public embed and their model bill.**
`/chat` is public by design, so once it is on a live site a script can post to
it; `chat_max_*` in `backend/app/config.py` are sized so a real dealership
never meets them. `frame-ancestors` is a *browser* control and does not touch
that case at all — anyone can post straight to the endpoint — so the two are
not substitutes for each other.

---

## What stays unavailable, and says so

Nothing below is a bug. Each reports itself rather than simulating a result,
and `/api/integrations` (or `make placeholders`) is the live list.

| If you skip | Then |
|---|---|
| `OPENAI_API_KEY` | The assistant is scripted. Same tools, same guards, canned wording — and a banner in the chat saying so. |
| `VOICE_PROVIDER` | No Call button anywhere, and `/call` says voice is off. |
| `CALLING=false` | Same as above with the provider left configured: the button goes, `/call` refuses, and `/api/integrations` says *switched off* rather than *not configured*. |
| `TEXTING=false` | No "Text them" on the buyer page and every send refused, naming the setting. Texts that arrive are still recorded. |
| `RESEND_API_KEY` | Every send writes a real outreach row and delivers nothing. The composer says *Not delivered* and quotes the provider. |
| The Cloudflare Worker | Replies never arrive. `/app/email` is how you tell that apart from silence. |
| `SCRAPER_BASE_URL` | The import screen offers CSV only. |
| `credit_application_url` | The credit-application draft refuses with a typed `not_configured`, and the overview card says why instead of showing a zero. |
| `knowledge:` in the profile | Liner says a person will check, rather than inventing a doc fee. |

---

## Switching back: Riverside on the bare domain

On a demo host serving several dealerships by prefix, the bare domain —
`/`, `/app`, `/chat`, `/api/…` with no store in the path — should be the
Riverside fixture and nobody's real lot. That is what an *unset*
`DEALERSHIP=` means, so **delete the line** rather than pointing it at
another store:

```bash
# in .env: remove the DEALERSHIP=... line entirely
make demo-db                 # the default store: Riverside + 50 demo buyers
make build
sudo systemctl restart liner
```

`make demo-db` with no `DEALERSHIP` acts on `backend/liner.db`, the default
store's file, and touches no `var/stores/<slug>.db`. Every prefixed store —
`/alsbou/…`, `/craigandlandreth/…` — keeps its own database and its own
people exactly as they were; only what the bare domain answers changes. The
fixture logins (`dana.mercer@` / `marcus.vale@`, password `liner-dev` in
development) sign in at `/login` and land on `/app`.

`DEALERSHIP=riverside` would also work, but it reads a *second* file,
`var/stores/riverside.db`, and puts the fixture under `/riverside/…` as well
— two copies of the same showroom for no reason. `make reset-all` seeds that
file too, since it walks every profile; on a host where the bare domain is
already Riverside, `make reset-all ARGS=--only alsbou,craigandlandreth`
leaves it out.

`make reset-dealership` rebuilds the showroom **keeping** the `ops_`
tables, so demos people booked with us are not thrown away — `make reset-db`
and `make demo-db` delete the file and do lose them.
