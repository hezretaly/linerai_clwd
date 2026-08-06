# Progress

One line per completed chunk. Newest last. See `HANDOFF.md` for current state
and `CLAUDE.md` for commands and conventions.

- **M1 — Skeleton, data model, seed.** FastAPI + WAL SQLite, all 17 tables from
  §4.1, integration registry driving `/api/health` and `/api/integrations`,
  deterministic per-VIN SVG vehicle photos, Riverside Auto fixture (14 vehicles,
  6 leads, 19 rails, 7 knowledge topics, 5 handoff rules).
- **M2 — Auth and dealer read APIs.** Session cookie, and `/api/overview` as the
  single source of every KPI and badge count so no two pages can disagree.
- **M3 — Agent.** Six tool executors with provenance enforcement and idempotency,
  guards that check every price/mileage/year against a tool result from the same
  turn, the rail-driven stub state machine, and the live Anthropic loop (written,
  never executed). Chat API with SSE.
- **M4 — Events + WebSocket.** `events.emit()` writes and broadcasts;
  `/ws/dealer?since=` replays from the events table on reconnect.
- **M5 — Act 2.** Confirm, round-robin auto-assign under the daily cap, drafted
  outreach mirrored into the buyer's thread, log-call, takeover/handback.
- **M6 — Inventory ingest.** discover → fetch → extract → normalise → diff →
  review → publish, JSON-LD first, CSV fallback, manual edits survive re-ingest.
  Voice endpoints: real tool relay, honest 503 on session mint.
- **M7 — Dashboard frontend.** Vite + React + Tailwind v4, seven pages on real
  data, WS-driven query invalidation.
- **M8 — Buyer chat, voice placeholder, landing.** SSE streaming with vehicle
  cards, slot chips and rail chips.
- **M9 — Integration status, docs, first full pass.** Amber not-configured
  banner, `make placeholders`, README, CLAUDE.md, `.env.example`.
- **shadcn classic theme.** Classic on dealer surfaces, `.theme-buyer` scope
  keeping iOS blue on `/chat` and `/call`. Fixed four booking bugs where Liner
  confirmed a time the buyer never asked for.
- **Real landing page.** Shipped byte-for-byte as a standalone document served at
  `/`; one approved edit (`Test your Liner AI` → `/chat`). Screenshot gate taught
  to scroll (reveal animations) and to distinguish expected gaps from breakage.
- **Handoff.** `HANDOFF.md` + `PROGRESS.md`, branch pushed to GitHub.
- **Overview, conversations and leads ported** from the supplied mockups: layout
  and hierarchy from the mockup, colour/radius/type from the token layer, and
  every control the API cannot back rendered as unavailable rather than dead.
  Backend extended where a mockup legitimately needed data (`by_hour` on
  `/api/overview`, `lead_summaries()` for the leads table) instead of faking it.
- **ADF/XML lead import + lead-level outreach.** Marketplace lead documents
  parsed with `defusedxml`, matched against real inventory and existing leads,
  reviewed, then committed; manual entry through the same path. Server-built
  follow-up and reminder drafts a rep sends from the lead drawer. Also fixed
  `api.upload()`, which had been sending multipart with a JSON content type.

## Next

Port the remaining mockups — calendar, inventory, assistant, team, settings —
then the embeddable chat widget and `/demo`. See `HANDOFF.md`.
