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

## 0. If something is already running on this box

Liner **serves its own copy of the landing page at `/`** — the same
`frontend/landing.html`. So whichever hostname you point at it stops being
served by whatever serves it today. Decide which you want before touching
nginx:

- **A subdomain** (`app.yourdomain.com` → Liner). Your existing site is not
  touched at all. Recommended while you are still testing.
- **The main domain** → Liner. You get the landing page *and* `/chat` and the
  dashboard as one product, which is the intended shape. Your old site stops
  being reachable — the files stay on disk, but nginx no longer serves them.

See what is there before you add anything:

```bash
ls -l /etc/nginx/sites-enabled/
sudo grep -r "server_name" /etc/nginx/sites-enabled/
```

If an existing file already claims the hostname you were about to use, nginx
will start anyway and silently pick one — the symptom is "my changes did
nothing". Use a different `server_name`, or edit the existing file rather than
adding a second.

A **catch-all** site (`listen 80 default_server; server_name _;`) is not a
conflict and does not need disabling: nginx prefers an exact `server_name` match
over the default server, so naming your hostname in the Liner block wins for
that hostname while the catch-all keeps answering everything else.

### Behind Cloudflare

If the DNS record is proxied (orange cloud), **set SSL/TLS mode to Full or Full
(strict)** in the Cloudflare dashboard before enabling the nginx site. On
*Flexible*, Cloudflare fetches your origin over plain HTTP, the port-80 server
block answers `301 https://...`, Cloudflare follows it back to port 80, and you
get `ERR_TOO_MANY_REDIRECTS` — a redirect loop that looks like the app is down.

Certbot's HTTP-01 challenge also has to reach your origin. Easiest path: set the
record to **DNS only** (grey cloud) for five minutes, run certbot, then turn the
proxy back on. Alternatively skip certbot and install a Cloudflare **Origin
Certificate**, which is what Full (strict) expects anyway.

WebSockets and SSE both pass through the Cloudflare proxy, so the live dashboard
and the streaming chat work — no extra setting needed.

## 1. Get the code onto the server

```bash
sudo adduser --system --group --home /srv/liner liner
sudo mkdir -p /srv/liner && sudo chown liner:liner /srv/liner
sudo -u liner git clone -b claude/liner-ai-implementation-8xehez \
    https://github.com/hezretaly/linerai_clwd.git /srv/liner
cd /srv/liner
```

**Every later step runs as `liner` via `sudo -u liner`** — every one, including
`make build`. Files the process writes at runtime (the SQLite database and its
WAL sidecars) must belong to the user systemd runs it as, or the first write is
a "readonly database" that surfaces as a 500 on login.

Dropping the `sudo -u liner` even once leaves root-owned files that only bite
later. The usual symptom is a build that cannot clean up after itself:

```
error during build:
EACCES, Permission denied: /srv/liner/frontend/dist/assets
    at Object.rmSync
```

That is not a build failure. `dist/` belongs to someone else, and removing a
directory needs write permission on its *parent*. Delete and rebuild as the
right user -- deleting rather than `chown`-ing, because it also clears any
stale root-owned files inside:

```bash
sudo rm -rf /srv/liner/frontend/dist
sudo -u liner make build
```

Then check nothing else is mis-owned, because the same slip breaks `git pull`
and the database just as quietly:

```bash
find /srv/liner -not -user liner -not -path '*/node_modules/*' | head -20
sudo chown -R liner:liner /srv/liner    # if that listed anything
sudo chmod 600 /srv/liner/.env          # re-assert: it holds the API key
```

Needs Python 3.11+, Node 20+ and `uv`. Install `uv` **system-wide**, not into
your own home directory — the next step runs as `liner`, and `sudo` resets
`PATH` to `secure_path`, so a `uv` in `~/.local/bin` is invisible there and
`make install` dies with `uv: command not found`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
sudo -u liner uv --version   # must print a version, not "command not found"
```

## 2. Write `.env`

`.env` is gitignored and never committed. Generate the secret rather than
inventing one:

`/srv/liner` belongs to the `liner` user, so your own shell cannot write into
it. `sudo tee` puts the redirect on the privileged side of `sudo` — a plain
`sudo cat > file` still fails, because the shell opens the file as *you* before
`sudo` ever runs.

```bash
sudo tee /srv/liner/.env >/dev/null <<EOF
ENV=production
SESSION_SECRET=$(openssl rand -hex 32)

# One password per role. Production refuses to boot if either is still
# 'liner-dev' or if the two are identical -- see §4.
MANAGER_PASSWORD=$(openssl rand -base64 12)
REP_PASSWORD=$(openssl rand -base64 12)

# CORS only, and only for cross-origin browsers. See below.
ALLOWED_ORIGINS=https://liner.example.com

# Leave this on. It is what stops a demo emailing a real prospect.
DEMO_MODE=true
EOF
sudo chown liner:liner /srv/liner/.env
sudo chmod 600 /srv/liner/.env
```

The `$(...)` must stay unquoted here: the heredoc runs them in *your* shell and
writes the results. Quoting the delimiter (`<<'EOF'`) would write the literal
text `$(openssl rand -hex 32)` as your session secret. Check with
`sudo grep SESSION_SECRET /srv/liner/.env` — you want 64 hex characters.

**`ALLOWED_ORIGINS` is CORS and nothing else.** It is read in exactly one place,
the CORS middleware in `app/main.py`. Because one process serves the site *and*
the API, every request the browser makes is same-origin, and same-origin
requests never consult CORS — so this value cannot lock you out, and getting it
wrong will not break the dashboard. It starts to matter when something on a
*different* origin calls this API: the embeddable chat widget on a real dealer's
website. Set it to your real hostname anyway, so that day needs no debugging.

Everything else keeps its default and reports itself as not-configured — the
agent runs on the stub, email goes to the outbox, voice returns a typed 503.
Nothing is simulated to cover those gaps.

## 3. Build and seed

```bash
sudo -u liner make install
sudo -u liner make build       # frontend/dist -- the API serves this
sudo -u liner make reset-db    # prints the two logins; wipes any existing data
```

**This step is not optional, and nothing else does it.** SQLite means there is no
database *server* to install, which makes it easy to assume the data takes care
of itself. It does not. The file is created on demand and `create_all()` builds
the tables at startup — so `/api/health` reports `database: ok` on a completely
empty database — but nothing writes a single row. Skip this and every login
returns 401 because there is no account to log in as. Confirm with:

```bash
sudo -u liner /srv/liner/backend/.venv/bin/python -c "
import sqlite3; c = sqlite3.connect('/srv/liner/backend/liner.db')
for t in ('users','vehicles','leads'):
    print(t, c.execute(f'select count(*) from {t}').fetchone()[0])"
```

`make reset-db` **deletes the database**. Use `make seed` only on a fresh one,
and neither once there is real data you care about — there is no migration tool
here (`create_all` only), so a schema change after that point needs one written.

**Reseeding an install that is already running: stop the service first.**

```bash
sudo systemctl stop liner
sudo -u liner make reset-db
sudo systemctl start liner
```

`reset-db` unlinks `liner.db` and its WAL sidecars, and uvicorn holds pooled
connections to that file. On Unix, unlinking a file a process has open does not
free it: the running app keeps reading the old, deleted copy while the new one
fills up on disk. The symptom is a reseed that appears to do nothing — fresh
data on disk, an app that still cannot see it, and no error anywhere.

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
system. There is still no signup and no self-service reset, so treat these as
demo credentials for people you have chosen rather than as an access control
system.

**`MANAGER_PASSWORD` and `REP_PASSWORD` are read at seed time, not at login.**
The hash lives in the `users` table, so editing `.env` afterwards changes what
the startup guard checks and nothing else. Worse, the `.env` recipe above calls
`openssl rand` each time it runs — so writing it twice mints new passwords and
leaves the database holding a generation that no longer exists anywhere. If you
cannot sign in with the value in `.env`, that is almost always why.

To fix it without losing data:

```bash
cd /srv/liner && sudo -u liner make set-password EMAIL=dana.mercer@example.invalid
```

It prompts, so the password never reaches shell history or `ps`. `make reset-db`
also works and rehashes every account, but deletes the database to do it.

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

## 6a. If nginx is already running here (including in Docker)

Check what owns port 80 before assuming the system nginx is free:

```bash
sudo ss -ltnp | grep -E ':80 |:443 '
```

If that names `docker-proxy`, a container is your front door and the system
nginx must stay stopped and disabled — `systemctl start nginx` will fail with
`bind() to 0.0.0.0:80 failed (98: Address already in use)`. Use
`deploy/liner-vhost.conf`, which drops into an existing nginx and assumes the
certificate already exists. Skip §6 entirely, and skip certbot if that proxy
already terminates TLS for the hostname (`curl -skI https://127.0.0.1/ -H 'Host:
YOUR-HOST'` returning 200 means it does).

**Two things will bite you, in this order.** Inside a container, `127.0.0.1` is
the container, so it cannot reach a uvicorn bound to the host's loopback — bind
the Docker bridge gateway instead. Then UFW, whose usual `deny (incoming)`
default drops packets from the container to that same gateway address. The
header comment in `liner-vhost.conf` has both fixes.

**Read the status code before guessing.** A `502` means the connection was
*refused* — nothing is listening, so look at the app. A `504` means packets
went out and vanished, which nothing does silently except a firewall. A 504
against an app that answers `curl` on the host is a firewall every time, and no
amount of re-reading the nginx config will show it.

Find where that nginx reads its config from:

```bash
sudo docker inspect <container> --format '{{json .Mounts}}' | python3 -m json.tool
```

A bind mount onto `/etc/nginx/conf.d` means you can add a file on the host and
reload in place — `docker exec <container> nginx -t && docker exec <container>
nginx -s reload`. That is zero downtime; **do not `docker restart`**, which
would drop every other service in the stack. If the config is baked into the
image instead, adding a mount means recreating the container, which is a
different and more disruptive change.

## 6. nginx, in two passes

**Do not apply `liner.nginx.conf` first.** It names certificate files, and nginx
refuses to load a config whose `ssl_certificate` does not exist — so before
certbot has run, `nginx -t` fails with *"cannot load certificate ... No such
file or directory"*. Meanwhile certbot needs a working HTTP vhost to answer its
challenge on. Start with the HTTP-only config, then swap.

```bash
# Pass 1 -- plain HTTP, no certificate referenced
sudo cp deploy/liner-bootstrap.nginx.conf /etc/nginx/sites-available/liner
sudo sed -i 's/liner.example.com/YOUR-HOST/g' /etc/nginx/sites-available/liner
sudo ln -sfn /etc/nginx/sites-available/liner /etc/nginx/sites-enabled/liner
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl status nginx --no-pager | head -3
curl -sI http://127.0.0.1/ -H 'Host: YOUR-HOST' | head -1   # 200
```

Two traps here, both of which look like the app failing when it isn't:

**`reload` says "nginx.service is not active, cannot reload".** nginx is
stopped, not misconfigured — almost always because an earlier config named a
certificate that did not exist, so it failed to load and stayed down. `nginx -t`
passing means the *current* config is fine; you still have to
`sudo systemctl start nginx`. Anything else this host serves has been down that
whole time too.

**Curl the origin, not the public URL, while debugging.** Behind a proxied
Cloudflare record with "Always Use HTTPS" on, `curl -sI http://YOUR-HOST/`
returns a `301` from Cloudflare's edge without ever contacting your server — so
you get a healthy-looking response from a box with nginx stopped.
`curl -H 'Host: ...' http://127.0.0.1/` goes straight to nginx and tells you the
truth. A `502` there means nginx is fine and the app is not: check
`systemctl status liner` and `curl localhost:8000/api/health`.

`ln -sfn` rather than `ln -s`, so re-running it after a failed attempt replaces
the link instead of erroring with *"File exists"*.

At this point everything works except signing in: the session cookie is `Secure`
under `ENV=production`, so a browser will not send it back over plain HTTP. The
landing page and `/chat` are fine; `/login` will not stick until pass 2. That is
the guard doing its job, not a bug.

```bash
# Pass 2 -- get the certificate, then apply the full config
sudo certbot --nginx -d YOUR-HOST
sudo cp deploy/liner.nginx.conf /etc/nginx/sites-available/liner
sudo sed -i 's/liner.example.com/YOUR-HOST/g' /etc/nginx/sites-available/liner
sudo nginx -t && sudo systemctl reload nginx
```

If `nginx -t` fails on pass 2, the certificate landed somewhere other than
`/etc/letsencrypt/live/YOUR-HOST/`. Check with `sudo certbot certificates` and
correct the two `ssl_certificate` paths.

Three things in the full file are load-bearing:

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
