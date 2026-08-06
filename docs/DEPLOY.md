# Deploying to a server

For putting this on a box you already own, behind nginx, next to whatever else
is running there. Verified end to end against a real production-mode boot: `/`
serves the landing page, `/chat` and `/app/*` serve the SPA, the dealer
WebSocket connects, and the session cookie comes back `Secure`.

**Shape of it:** one Python process on `127.0.0.1:8000` serves *everything* —
the API, the WebSocket, the landing page and the built SPA. nginx has a single
`proxy_pass` and no `try_files`.

That is deliberate. The routing rule the site needs — `/` is the landing
document, `/chat` `/call` `/login` `/app/*` are the SPA, anything else is a real
file or a 404 — used to live only in the Vite dev plugin. Re-expressing it in an
nginx config means two copies that drift, and when they drift the failure is
silent: `/` serves the SPA, whose catch-all bounces to `/`, and you get a blank
page with nothing in any log. It now lives in `backend/app/static.py`, which
ships with the frontend and cannot drift from it.

---

## 1. Get the code onto the server

```bash
sudo adduser --system --group --home /srv/liner liner
sudo -u liner git clone -b claude/liner-ai-implementation-8xehez \
    https://github.com/hezretaly/linerai_clwd.git /srv/liner
cd /srv/liner
```

Needs Python 3.11+, Node 20+, and `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

## 2. Write `.env`

`.env` is gitignored and never committed. Generate the secret rather than
inventing one:

```bash
cat > /srv/liner/.env <<EOF
ENV=production
SESSION_SECRET=$(openssl rand -hex 32)

# One password per role. Production refuses to boot if either is still
# 'liner-dev' or if the two are identical -- see §4.
MANAGER_PASSWORD=$(openssl rand -base64 12)
REP_PASSWORD=$(openssl rand -base64 12)

ALLOWED_ORIGINS=https://liner.example.com

# Leave this on. It is what stops a demo emailing a real prospect.
DEMO_MODE=true
EOF
chmod 600 /srv/liner/.env && chown liner:liner /srv/liner/.env
```

Everything else keeps its default and reports itself as not-configured — the
agent runs on the stub, email goes to the outbox, voice returns a typed 503.
Nothing is simulated to cover those gaps.

## 3. Build and seed

```bash
sudo -u liner make install
sudo -u liner make build       # frontend/dist -- the API serves this
sudo -u liner make reset-db    # prints the two logins; wipes any existing data
```

`make reset-db` **deletes the database**. Use `make seed` only on a fresh one,
and neither once there is real data you care about — there is no migration tool
here (`create_all` only), so a schema change after that point needs one written.

## 4. Two logins, two roles

`make reset-db` prints them. The manager account (`dana.mercer@example.invalid`)
sees every lead, the team page and the assistant settings; the rep account
(`marcus.vale@example.invalid`) works the floor. Three more rep accounts exist
on `REP_PASSWORD` — `marcus.vale`, `priya.raman`, `trevor.osei`.

Startup **refuses to run** with `ENV=production` if either password is still
`liner-dev`, or if the two are the same:

```
RuntimeError: MANAGER_PASSWORD still set to 'liner-dev' while ENV=production.
That password is printed in the README, so anyone who found the URL would have
the dashboard and every lead in it.
```

That is a hard failure on purpose. The seeded password is published in this
repo, and the dashboard holds every lead, transcript and contact detail in the
system. There is no signup, no password reset and no per-user rotation — the
only way to change a password is to reseed, which wipes the data. Treat these
as demo credentials for people you have chosen, not as an access control system.

**The buyer surfaces are unauthenticated by design** — `/`, `/chat` and `/call`
have to be, they are the public product. Anyone with the link can start a
conversation and book an appointment against real inventory. That is the demo
working, but it does mean a stranger can create rows. If you want the whole site
private while you test, add an nginx `auth_basic` to the `location /` block.

## 5. Run it

```bash
sudo cp deploy/liner.service /etc/systemd/system/liner.service
sudo systemctl daemon-reload && sudo systemctl enable --now liner
journalctl -u liner -f
```

Expect two lines on a healthy boot:

```
INFO liner: Serving the built frontend from frontend/dist.
WARNING liner: 4 integration(s) not configured: llm, email, voice, scraper.
```

The warning is correct — those are the honest placeholders, and the dashboard
shows the same list in its amber banner.

**One worker, and leave it that way.** The WebSocket connection manager is
in-process, so a second worker serves dashboards that silently miss half their
events. The app warns if `WEB_CONCURRENCY` is raised. Going wider means moving
`events.py` to Redis pub/sub first.

## 6. nginx

```bash
sudo cp deploy/liner.nginx.conf /etc/nginx/sites-available/liner
sudo sed -i 's/liner.example.com/YOUR-HOST/g' /etc/nginx/sites-available/liner
sudo ln -s /etc/nginx/sites-available/liner /etc/nginx/sites-enabled/
sudo certbot --nginx -d YOUR-HOST
sudo nginx -t && sudo systemctl reload nginx
```

Three things in that file are load-bearing:

- **`proxy_buffering off`** — buyer chat streams over SSE. With buffering on,
  nginx holds every token until the reply finishes, which reads as the
  assistant hanging.
- **`Upgrade`/`Connection` headers on `/ws/`** — without them the dashboard
  falls into its 2s reconnect loop and never updates live.
- **`client_max_body_size 8m`** — ADF drops and inventory CSVs are uploads, and
  nginx's 1 MB default would 413 before the app's own limit ever ran.

**TLS is not optional.** The session cookie is set `Secure` whenever
`ENV=production`, so over plain HTTP nobody can stay logged in. It fails loudly
rather than sending a dealer's session in the clear.

## 7. Check it

```bash
curl -sI https://YOUR-HOST/         | head -1     # 200, the landing page
curl -s  https://YOUR-HOST/api/health | head -c 200
```

Then sign in at `/login` and open `/app/leads/import` — the sample ADF file is
downloadable from that page, so you can drive the whole import in the browser.

To watch the live path: open the dashboard in one window and `/chat` in another,
book through the chips, and the appointment counter should move with no reload.

## Updating

```bash
cd /srv/liner && sudo -u liner git pull
sudo -u liner make install && sudo -u liner make build
sudo systemctl restart liner
```

Do **not** run `make reset-db` on an update — it wipes the database.

## What this setup does not do

- **No backups.** The database is `backend/liner.db`. It is SQLite in WAL mode,
  so copy it with `sqlite3 liner.db ".backup out.db"`, not `cp` — a plain copy
  taken mid-write can be torn.
- **No migrations.** `create_all` only. A schema change against a database with
  data in it needs Alembic introduced first.
- **No rate limiting.** `/api/chat/sessions` is public and creates a row per
  call. Fine for a demo you share deliberately; add an nginx `limit_req` before
  the URL goes anywhere wide.
- **No log rotation** beyond journald's defaults.
