.PHONY: help install build set-password agent-check agent-ping dev backend frontend seed seed-demo reset-db reset-dealership add-owners smoke accept accept-ui ops-ui e2e fixture-site stop placeholders shots

PY := backend/.venv/bin/python
# How many demo buyers `make seed-demo` adds. Override: make seed-demo N=200
N ?= 50
UVICORN := backend/.venv/bin/uvicorn
BACKEND_PORT := 8000
FRONTEND_PORT := 5173
FIXTURE_PORT := 8100

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install backend and frontend dependencies
	cd backend && uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"
	cd frontend && npm install

build: ## Build the frontend into frontend/dist (the API serves it in production)
	cd frontend && npm run build
	@echo "built -> frontend/dist. See docs/DEPLOY.md."

stop: ## Kill anything bound to our ports
	@for p in $(BACKEND_PORT) $(FRONTEND_PORT) $(FIXTURE_PORT); do \
		pid=$$(lsof -ti tcp:$$p 2>/dev/null); \
		if [ -n "$$pid" ]; then kill -9 $$pid 2>/dev/null || true; fi; \
	done
	@echo "ports clear"

backend: ## Run the API on :8000 (single worker -- see plan §5.2)
	cd backend && ../$(UVICORN) app.main:app --reload --host 127.0.0.1 --port $(BACKEND_PORT)

frontend: ## Run Vite on :5173
	cd frontend && npm run dev

dev: stop ## Run both servers in the background, logging to .logs/
	@mkdir -p .logs
	cd backend && ../$(UVICORN) app.main:app --host 127.0.0.1 --port $(BACKEND_PORT) \
		> ../.logs/backend.log 2>&1 & echo "backend  -> .logs/backend.log"
	cd frontend && npm run dev > ../.logs/frontend.log 2>&1 & echo "frontend -> .logs/frontend.log"
	@sleep 3 && echo "http://localhost:$(FRONTEND_PORT)"

set-password: ## Change one account's password in place: make set-password EMAIL=someone@...
	@test -n "$(EMAIL)" || (echo "Usage: make set-password EMAIL=dana.mercer@example.invalid" && exit 1)
	cd backend && ../$(PY) -m app.set_password $(EMAIL) $(ARGS)

add-owners: ## Move/create Liner's own rows in the ops_ tables -- safe on a live box
	cd backend && ../$(PY) -m app.add_owners

reset-dealership: ## Rebuild the dealership fixture, KEEPING our ops tables
	cd backend && ../$(PY) -m app.seed

seed: ## Wipe and rebuild the Riverside Auto fixture
	cd backend && ../$(PY) -m app.seed

seed-demo: ## Add N demo buyers on top of the fixture (N=50)
	$(PY) scripts/seed_demo.py $(N)

reset-db: ## Delete the database and reseed
	rm -f backend/liner.db backend/liner.db-wal backend/liner.db-shm
	cd backend && ../$(PY) -m app.seed

agent-ping: ## One real turn against the configured model, errors printed in full
	$(PY) scripts/agent_ping.py

agent-check: ## Drive the live loop against a fake provider (no API key needed)
	$(PY) scripts/agent_loop_check.py

accept-ui: ## The same path through the screens: two windows, real clicks.
	$(PY) scripts/browser_acceptance.py

ops-ui: ## Our own dashboard in a browser: the notification really clears.
	$(PY) scripts/ops_browser.py

accept: ## One buyer end to end -- form, chat, call, email, booking, handover.
	$(PY) scripts/acceptance.py

smoke: ## Drive the whole booking flow over HTTP. No browser, no credentials.
	$(PY) scripts/smoke.py
	@# The live model path cannot be reached over HTTP without a key, so it is
	@# exercised here against a fake provider instead of going unchecked.
	$(PY) scripts/agent_loop_check.py

e2e: ## Book through two browser windows and assert the dashboard reacts
	$(PY) scripts/e2e_booking.py

fixture-site: ## Serve the scraper fixture dealer site on :8100
	$(PY) backend/fixtures/build_site.py
	cd backend/fixtures/sites/riverside && ../../../../$(PY) -m http.server $(FIXTURE_PORT)

placeholders: ## Collect every PLACEHOLDER marker into docs/PLACEHOLDERS.md
	$(PY) scripts/placeholders.py

shots: ## Screenshot every route into .artifacts/
	$(PY) scripts/screenshots.py
