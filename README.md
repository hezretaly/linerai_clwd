# Liner AI

An AI assistant for a car dealership. A buyer messages at 11:47 PM, Liner
searches the dealership's real inventory, answers from real listings, books an
appointment, and the dealer's dashboard reacts live. A rep confirms it, assigns
it and sends the follow-up.

## Run it

```bash
make install
make reset-db
make dev          # backend :8000, frontend :5173
```

Then open <http://localhost:5173>. Sign in at `/login` with
`dana.mercer@example.invalid` / `liner-dev`.

**No `.env` is needed.** Every external dependency has a working default, and
anything unconfigured says so rather than pretending.

Verify the whole thing end to end:

```bash
make smoke
```

## What's here

**Buyer side** — a landing page, a chat that streams over SSE with inventory
cards and tappable prompt chips, and a call screen.

**Dealer side** — overview with four KPIs and a panel showing which buyers were
quoted a car that has since sold; a three-pane conversations view with takeover;
leads with provenance on every captured field; a week calendar with overlap
packing; inventory with per-vehicle rules; an inventory import review screen;
and a settings page showing the assistant's compiled prompt read-only.

**Underneath** — six agent tools against SQLite, guards that reject any price or
mileage the assistant can't source from a tool result in that same turn, a
WebSocket event bus, and an inventory ingest pipeline that parses JSON-LD.

## What is real, and what isn't

This is the part worth reading before judging anything.

| | State |
|---|---|
| **Agent** | Runs a scripted state machine by default. It is not a mock — it calls the real tools, writes the real rows and fires the real events, and every number in its replies comes from a tool result. It just can't improvise. Set `ANTHROPIC_API_KEY` and `LLM_MODE=live` for the real loop, which has **never been executed** — there was no key in the build environment. |
| **Email** | Records a real outreach row and mirrors the message into the buyer's chat thread. **Sends no mail.** The Gmail sender is written and unverified. |
| **Voice** | **Not configured, and not faked.** The call UI, session mint and tool relay all exist; the relay is tested. No vendor has been chosen, so `/call` says exactly that and names the missing variables. A scripted transcript would have looked like it worked while proving nothing about the only things a voice provider decides. |
| **Inventory ingest** | Works. Real HTTP, real JSON-LD parsing, real diff and publish — against a fixture dealer site (`make fixture-site`) since no real dealer URL was available. Manual edits survive re-imports; that is tested. CSV import needs no configuration at all. |

`GET /api/integrations` returns this live, and a banner across the dashboard
names anything unconfigured. It isn't dismissible, on purpose: the risk of
building this way is demoing on placeholders without noticing.

## Making it yours

Edit `backend/config/dealership.yaml` — name, address, phone, hours, timezone —
then `make reset-db`. That is the one file per dealership; there is no
`dealership_id` column anywhere, so the app holds one at a time.

## Layout

```
backend/app/
  models/        the 17 tables
  agent/         tools, guards, the stub state machine, the live loop, prompts
  api/           routers; ws.py is the dealer event socket
  integrations/  email and voice behind interfaces, plus the status registry
  ingest/        discover -> extract -> diff -> review -> publish
frontend/src/
  routes/        every page
  components/    ui primitives, dashboard shell
  styles/        liner-theme.css -- all design tokens, and the only place for them
scripts/
  smoke.py       the gate
  e2e_booking.py two browser windows, buyer and dealer
```

More detail, including conventions and what's deliberately not built, is in
[CLAUDE.md](./CLAUDE.md).
