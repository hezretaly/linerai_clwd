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
| Their front page | `/showroom` | Their logo, colour, address, phone, hours, real lot, chat widget, Call button. **The link you send them.** |
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
heading, welcome copy, hero image, nav and social links, all copied from their
own home page. `/showroom` renders from it. A profile with no `site:` block
gets a plain storefront, which is what Riverside gets and is perfectly honest.

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

Where the seed **refuses to run** is a missing `hours`. That is deliberate:
opening times cannot be looked up from here, and an invented hour is an
appointment nobody is there for. Filling a gap with something plausible is
worse than leaving it — an invented address survives a demo and gets repeated
back to a customer.

**Optional but worth asking them for:** `knowledge:` — their doc fee, whether
they take trade-ins, their deposit. Policy answers are returned to a buyer
verbatim and never composed, so with the list empty Liner says a person will
check rather than inventing a number. Riverside's `$189` doc fee is a fixture
and is deliberately *not* inherited.

---

## Step 2 — `.env`

Copy `.env.example` to `.env` and set these. Anything not listed keeps its
default.

### Always

```dotenv
DEALERSHIP=craigandlandreth
```

This picks the profile, and it has to be in `.env` rather than only prefixed
onto the seed command: the running server reads it too — it is how
`/api/showroom` knows whose colour to serve and which folder the crawl writes
into.

### The assistant, unscripted

```dotenv
LLM_MODE=live
OPENAI_API_KEY=sk-...
```

Without these the assistant still runs — it calls the same eight tools, books
real appointments and obeys the same guards — but the wording is canned, and
the chat says so in a banner the prospect will read. **Set them.** This is the
one thing a dealer is actually judging.

### Their inventory

```dotenv
SCRAPER_BASE_URL=https://www.craigsbestcars.com/newandusedcars
SCRAPER_DEALER_ID=1123
```

`SCRAPER_DEALER_ID` is not optional for them. Their listing page mixes three
stores — Louisville (1123), Clarksville IN (1129) and Bullitt County (3833) —
and this app holds exactly one dealership. Without it Liner offers a buyer a
car two hours from the showroom it says it is standing in.

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

### Email

```dotenv
EMAIL_SENDER=resend
RESEND_API_KEY=re_...
SENDING_DOMAIN=linerai.us
SENDING_FROM=Craig and Landreth Cars <sales@linerai.us>
WEBHOOK_SECRET=<openssl rand -hex 32>
OUTBOUND_ONLY_TO=everyone
```

Five things, and each breaks differently:

- **`RESEND_API_KEY`** — sending. Breaks loudly: the next send quotes the
  provider's own error.
- **`SENDING_DOMAIN`** — the domain must be verified in Resend. It also builds
  the `Reply-To: reply+<token>@` that routes a buyer's answer back into their
  timeline.
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

## Step 3b — Give their people accounts

The seeded staff (`dana.mercer@`, `marcus.vale@`) are fixture names. A real
person at the dealership gets a real account **without a reseed**:

```bash
make add-user EMAIL=austin@theirdomain.com NAME="Austin ..." ROLE=manager
```

`manager` sees every lead, the team page, the assistant settings and can
publish; `rep` works the floor. It prints a generated password **once** —
only the bcrypt hash is stored — and running it again for the same address
changes nothing, because they may have set their own since.

Change one later with `make set-password EMAIL=...`. Both are safe on a box
with real bookings on it; `make reset-db` is not.

---

## Step 4 — Import their cars

**This has to run somewhere their site is reachable.** It cannot run in the
Claude Code sandbox — the egress proxy refuses `craigsbestcars.com` — so run
it on your laptop or the demo server.

```bash
make dev
```

Then, signed in at `/app/inventory/import`, press **Import from website**. It:

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

## What stays unavailable, and says so

Nothing below is a bug. Each reports itself rather than simulating a result,
and `/api/integrations` (or `make placeholders`) is the live list.

| If you skip | Then |
|---|---|
| `OPENAI_API_KEY` | The assistant is scripted. Same tools, same guards, canned wording — and a banner in the chat saying so. |
| `VOICE_PROVIDER` | No Call button anywhere, and `/call` says voice is off. |
| `RESEND_API_KEY` | Every send writes a real outreach row and delivers nothing. The composer says *Not delivered* and quotes the provider. |
| The Cloudflare Worker | Replies never arrive. `/app/email` is how you tell that apart from silence. |
| `SCRAPER_BASE_URL` | The import screen offers CSV only. |
| `credit_application_url` | The credit-application draft refuses with a typed `not_configured`, and the overview card says why instead of showing a zero. |
| `knowledge:` in the profile | Liner says a person will check, rather than inventing a doc fee. |

---

## Switching back

`DEALERSHIP=riverside` in `.env`, then `make reset-db`. The fixture comes
back. `make reset-dealership` rebuilds the showroom **keeping** the `ops_`
tables, so demos people booked with us are not thrown away — `make reset-db`
deletes the file and does lose them.
