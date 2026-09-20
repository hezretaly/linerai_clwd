# Architecture review

A review of how the system is put together, written after building Alsbou's
storefront onto it — which is the first feature to lean on the multi-store
routing, the profile layer and the storefront at once, and so the first to
find where the seams are. Everything here was measured on this codebase; where
a recommendation is made, what it would cost is said beside it.

It is a review, not a rewrite plan. The system's central rule — narrow, not
fake; a file boundary is the isolation; an executor is a guarantee and a
prompt is a request — is sound and is not questioned below. What follows is
where the structure has been outrun by what was built on it.

## The shape today

One FastAPI process serves everything: the API, the WebSocket, Liner's own
marketing page at `/`, the built SPA, and — since the per-store work —
several dealerships told apart by the first path segment. `app/stores.py` is
a pure-ASGI middleware that reads `/alsbou/api/overview`, sets a `ContextVar`
naming the store, rewrites `scope["path"]` to `/api/overview`, and hands on.
Every handler below it is store-blind: it opens "the database", and the
`ContextVar` decides which file that is.

A dealership is two things kept in two places. Its **rows** — name, address,
hours, staff, the lot, every buyer — live in its own SQLite file under
`backend/var/stores/<slug>.db`. Its **facts about itself** — brand, site copy,
crawl source, knowledge, assistant settings — live in
`backend/config/dealerships/<slug>.yaml`, read per request through
`active_store()`. Liner's own tables (`ops_*`) are a second SQLAlchemy
metadata on a third file, `ops.db`, so a store's `create_all` cannot build
them by accident.

The frontend is one bundle. `lib/store.ts` reads the first path segment,
decides by exclusion whether it is a store, and if so sets the router's
basename and prefixes every API call. Two buyer-facing pages sit outside the
dashboard: the dealership's front page at the store root and their inventory
list at `/showroom`, both rendering from the profile's `site:` block.

## Where it broke, and why

### 1. The prefix strip made a store's root ours

`StorePrefix` strips `/alsbou` to `/`. `static.py`'s root handler served
`landing.html` — Liner's marketing page — for `/`, unconditionally. So
`linerai.us/alsbou`, the link a prospect is sent, answered with our homepage
under their URL. Every other route under the prefix worked, which is what
kept it invisible: `/alsbou/showroom`, `/alsbou/app` and `/alsbou/api/…` were
all right, and nobody types the bare root during a demo.

**The structural cause** is that the middleware erases the one fact the root
handler needed. The path is rewritten and the only trace of the prefix is a
`ContextVar` — which is the right mechanism for "which database", and
happens to also be the only mechanism for "which document". The root handler
now reads it. That is a correct fix and a small one; the review point is that
*any* handler whose answer depends on the prefix has to know to ask, and
nothing tells it. Today there are two such handlers (the root, and the SPA
catch-all, which does not care). It is worth a line in `stores.py` naming
that the `ContextVar` is the only trace, so the next handler that needs it
finds it there rather than in a bug report.

### 2. Three hand-written lists of the app's own roots

The set of paths the SPA owns, as opposed to a store slug, is written down in
three places:

| Where | Name | Read by |
|---|---|---|
| `backend/app/static.py` | `SPA_PREFIXES` | the production catch-all |
| `backend/app/stores.py` | `RESERVED` | the prefix middleware |
| `frontend/src/lib/store.ts` | `OURS` | the browser, before its first request |

`make smoke` keeps `SPA_PREFIXES` in step with `main.tsx`, because `/ops`
shipped missing from it once and the whole ops dashboard answered a JSON 404
on a real host while every gate was green. `OURS` and `RESERVED` are kept in
step by nobody. They agree today. The next top-level route added to
`main.tsx` — say `/book` — has to be added to all three, and forgetting the
browser's copy means `/book` is read as a store slug and the page prefixes
every API call with `/book/`.

**Recommendation:** one file, `frontend/src/routes.json`, listing the roots;
`store.ts` imports it, `static.py` and `stores.py` read it at import time
(the path is fixed and the file is tiny). The gate then checks `main.tsx`
against one list instead of one of three. Cost: an afternoon, and a JSON
import in two Python modules that currently import nothing from the
frontend tree — which is the objection, and the answer is that they already
depend on `frontend/dist` existing, so the dependency direction is not new.

### 3. The dev/prod routing rule lives in two places

"`/` is the marketing document; everything else is the SPA" is enforced by a
Vite plugin in development and by `static.py` in production, and they have
to say the same thing. They did not, for the store root: Vite matched only
the exact path `/`, so `/alsbou` fell through to `index.html` and *worked in
development*, while production served the marketing page. The bug was
invisible on every box this has been developed on, which is the general
shape of every routing bug this codebase has had.

There is no clean way to make one rule serve both — Vite's dev server and a
FastAPI catch-all are different programs. What can be done is what `make
smoke` now does: exercise the production path against a real build, for the
prefixed root as well as the bare one. **The review point is broader:** any
routing decision that exists only in `static.py` is one development cannot
see, and the list of those should be short and named. Today it is: the root
document, `SPA_PREFIXES`, and `RESERVED`. Three is fine. It should not grow
without a gate check landing beside it.

### 4. The default store is a fourth file for one dealership

`DEALERSHIP=riverside` with no prefix reads `backend/liner.db`. `/riverside/…`
reads `backend/var/stores/riverside.db`. Same profile, same seed, two files,
and `make stores` reports the second "not seeded" while the first is full.
`make smoke`'s price-order walk tripped over this: it created the second
file by asking for it, and the guard that exists for exactly that caught it.

This is the single-dealership era still standing under the multi-store one.
"The default store" was the only store; now it is a *mode* — "serve this slug
unprefixed" — that happens to also pick a different file. **Recommendation:**
`database_url_for("")` should resolve to the file of whichever slug
`DEALERSHIP=` names, so a dealership has one file however it is addressed.
Cost: a data copy on every deployed box (`liner.db` → `var/stores/<slug>.db`)
and a one-line change in `config.py`; risk: every existing `.env`, script and
smoke assertion assumes `liner.db`, so it is a change to make deliberately,
with `make stores` reporting both paths until the copy is done. It is the
highest-value item here and the one not to do casually.

### 5. `site:` is becoming a page schema

The profile's `site:` block started as headings and links. Building Alsbou's
front page added `hero_image`, `banners`, `body_style_tiles`, `promos`,
`sections`, `price_label`, `price_note`, `cta`. Each is a real thing on their
page with nowhere else to live, and each is validated and bounded in
`profile.site()`. That is the right place for them — a profile describes
*content*, the component decides *layout*, and nothing in the components
names a dealership.

The risk is the next dealership whose page has a section this vocabulary
cannot say. The wrong answer is a `sections: [{type: …}]` block builder in
YAML, which is a page layout language nobody asked for. **The rule to hold:**
a new key is added when a second dealership needs it, or when the first one's
page cannot be told without it; a key that serves one dealership's one
section is a sign the component should be more general, not that the schema
should be. `banners` and `promos` are the same shape twice, deliberately —
a strip and a band are different things on a page and the reader should not
have to test a `placement` field to know which it has.

### 6. The storefront was one component

`Showroom.tsx` reached 1,100 lines doing the chrome, the hero, the banner
strip, the About copy, the filter sidebar, the results toolbar, the card, the
footer and the chat widget. It is now `components/storefront/` — `Shell`
(chrome, footer, widget, and a context for opening the assistant), `CarCard`,
`Media` (hero, tile, promo, style tile, each with its own fallback),
`useStorefront` (one request for everything), `types` — and two routes,
`Storefront.tsx` and `Showroom.tsx`, each under 300 lines. The split was
forced by the root fix, since a front page and a list are two routes, and it
is the right one regardless: the header cannot drift between the two pages
because there is one header.

What the split did *not* do, on purpose: it did not introduce a layout engine
or a section registry. Each page composes its sections in order, in code.
The front page's order is Alsbou's order; if a second dealership's page runs
its About above its specials, that is a second composition, and two short
compositions are cheaper than one configurable one.

## What is right and should stay

- **The file boundary as the isolation.** No table carries a dealership id
  and none should. A `WHERE dealership_id = ?` that one query forgets is a
  buyer list leaking across dealerships; a file cannot be forgotten.
- **`ContextVar` for the active store, reset in a `finally`.** Threading an
  argument through every database call would have missed one.
- **Executors as guarantees.** Every rule about what a buyer can be told —
  do-not-discuss, provenance, the clash check, the fee disclosure that states
  rather than computes — lives in code that runs, not in a prompt or a page.
  The storefront inherits every one by reading through `offerable`.
- **The profile as the dealer's own words, served.** Five surfaces once
  printed "Riverside Auto"; none does now, and the gate reads the component
  for any dealer's string. That check caught a hardcoded "Advertised price"
  in the pricing disclosure the afternoon it was written.
- **No migrations, deliberately, with the consequence accepted.** New state is
  a new table or a `raw_json` key, never a column. The specification fields
  went to `raw_json` for exactly this reason and it cost nothing.
- **A gate that presses the thing rather than reading the code.** Every facet
  is pressed and its count compared with its grid; every hero is checked
  against every tile; the prefixed root is fetched from a real build. Two of
  the three bugs found this week were found by a check written for a
  different one.

## Order of work, if any of it is taken up

1. **Nothing** — the root fix and the split are shipped and gated.
2. **One roots list** (§2). Small, removes a standing hazard.
3. **One file per dealership** (§4). The valuable one; plan the data copy.
4. **A note in `stores.py`** that the `ContextVar` is the only trace of the
   prefix (§1). Five minutes.
5. **Hold the `site:` rule** (§5). Not work; a decision to keep making.
