# Deploying to a server

For putting this on a box you already own, behind nginx, next to whatever else
is running there. Verified end to end against a real production-mode boot: `/`
serves the landing page, `/chat` and `/app/*` serve the SPA, the dealer
WebSocket connects, and the session cookie comes back `Secure`.

**Shape of it:** one Python process on `127.0.0.1:8000` serves *everything* —
the API, the WebSocket, the landing page and the built SPA. nginx has a single
`proxy_pass` and no `try_files`.

That is deliberate. The routing rule the site needs — `/` is the landing
document, `/chat` `/call` `/login` `/app/*` `/ops/*` are the SPA, anything else
is a real file or a 404 — used to live only in the Vite dev plugin. Re-expressing it in an
nginx config means two copies that drift, and when they drift the failure is
silent: `/` serves the SPA, whose catch-all bounces to `/`, and you get a blank
page with nothing in any log. It now lives in `backend/app/static.py`, which
ships with the frontend and cannot drift from it.

**One trap survived that move, and it bit.** `SPA_PREFIXES` in that file is
still a hand-written list, and *nothing in development disagrees with it*: Vite
serves `index.html` for any path at all, so every browser check in this repo
passes against `:5173` whatever the list says. `/ops` shipped missing from it,
and the entire ops dashboard answered `{"detail":"Not found"}` on a real host —
FastAPI's JSON 404 from the catch-all, which means the request never reached
React and none of its redirect logic ever ran. It reads as an auth problem and
is not one. `make smoke` now parses the top-level routes out of `main.tsx` and
fails on any that the API would not serve, and — when `frontend/dist` exists —
fetches each one to check it really answers with the app.

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

### "attempt to write a readonly database"

The same slip, seen from the database side. It surfaces as a 500 on login, or
as a sixty-line SQLAlchemy traceback from `make add-owners` / `make seed`, and
it means the OS refused the write — not that anything is corrupt.

**It takes three things, not one.** SQLite needs write permission on the
database file, on the *directory* (WAL mode creates `liner.db-wal` and
`liner.db-shm` beside it), and on those sidecars once they exist. Any one of
them owned by somebody else fails every write, and they go wrong
independently — a directory that looks fine tells you nothing about the file
inside it.

Measured as an unprivileged user against a real database, inserting a row
rather than only opening it: a root-owned file in a writable directory fails,
and a writable file in a root-owned directory fails.

```bash
ls -la /srv/liner/backend/liner.db*   # the file AND its -wal/-shm
ls -ld /srv/liner/backend             # and the directory
sudo chown -R liner:liner /srv/liner  # all three; one is never enough
sudo systemctl restart liner
```

If it comes back, the service is writing as somebody else. Check `User=`:

```bash
systemctl show liner -p User -p ExecStart
```

A unit with no `User=` runs as **root**, and then every file the app touches at
runtime is recreated root-owned — so this returns the next time anything runs
as `liner`. Set `User=liner`, chown once more, and it stops happening.

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

# One password per role. Production refuses to boot if any is still
# 'liner-dev' or if two of them are identical -- see §4.
MANAGER_PASSWORD=$(openssl rand -base64 12)
REP_PASSWORD=$(openssl rand -base64 12)
# Liner's own two accounts, which reach /ops and nothing of the dealership's.
# One key per person: a shared password cannot be traced or revoked per person.
FOUNDER_PASSWORD=$(openssl rand -base64 12)
CTO_PASSWORD=$(openssl rand -base64 12)

# CORS only, and only for cross-origin browsers. See below.
ALLOWED_ORIGINS=https://liner.example.com

# How this install is reached from outside. Only links that leave the
# building need it -- the tracked link in a credit application email. The
# shipped nginx config passes Host through, so leaving this empty works;
# set it and there is nothing left to infer.
PUBLIC_BASE_URL=https://liner.example.com

# Who outbound email may reach. Empty refuses every send, which is the safe
# way to start; a list allows those addresses; the word `everyone` lifts the
# limit entirely. It gates sending only -- anyone can always write in.
OUTBOUND_ONLY_TO=

# The only thing in front of /api/inbound-email, which writes into a buyer's
# history and has no session to check because Cloudflare has none to send.
# Startup refuses to run on the development default when ENV=production.
# Set the same value as a secret on the Cloudflare Worker.
WEBHOOK_SECRET=$(openssl rand -hex 32)

# What signs the phone line's webhooks. Startup refuses the development
# default here too, phone or no phone. Until there is a Twilio account, a
# random one seals /api/phone/incoming -- nobody can sign a request with it --
# and the line reads "not configured" for want of an account and a number.
# Replace it with the real token when you set the phone up (below).
TWILIO_AUTH_TOKEN=$(openssl rand -hex 32)
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

### Email, when you want it

Two independent halves, and the second one fails silently — a deployment can
send perfectly and drop every reply, with nothing in the app looking wrong.
`/app/email` in the dashboard is where you check both, and it records every
delivery the inbound endpoint was handed **including the ones it refused**.

```bash
EMAIL_SENDER=resend
RESEND_API_KEY=re_...
# Verified in Resend, and the same domain Cloudflare routes mail for.
# Outbound builds `Reply-To: reply+<token>@here`; if the two differ, replies
# bounce and the app has no way to know.
SENDING_DOMAIN=linerai.us
```

Receiving needs Cloudflare Email Routing on that domain, with a **catch-all**
rule to the Worker in
`backend/app/integrations/email/worker/` — its README has the records, the
rules and the two secrets. The catch-all is not optional: `reply+<token>@`
addresses are minted per send and cannot be enumerated as rules.

The Worker has never been deployed from here, so treat its first run as
untested. The endpoint it posts to is covered by `make smoke`.

**Redeploy the Worker to receive files, copies and formatting.** The current
Worker posts the whole message as it arrived (`message/rfc822`) to
`/api/emails/inbound/raw`, and the backend reads the Cc list, the HTML, inline
images and attachments out of it. A Worker deployed before this change posts a
JSON digest instead, which the backend still accepts -- but it never carried
the bytes of a file, so those arrive as *"the relay forwarded only this file's
name"* until you run `wrangler deploy` from
`backend/app/integrations/email/worker/`. The raw intake accepts up to 30 MB,
so nginx's `client_max_body_size` has to be at least that (the shipped configs
say 32m); received messages are kept under `backend/var/mail/` and attachments
under `backend/var/attachments/` -- both are data, so back them up with the
databases.

### Sending under your own name

`SENDING_DOMAIN` also decides who may put their own address in a `From`.
Resend verifies the **domain**, not the mailbox, so once `linerai.us` is
verified both `founder@linerai.us` and `cto@linerai.us` are legal to send as on
the same API key — a reply from the ops inbox goes out as
`Liner CTO <cto@linerai.us>` and comes back to them. Adding a third person is a
row in the `users` table and no new credential; there is deliberately no
per-mailbox password anywhere in this, because that is a thing to leak.

An account whose address is **not** on `SENDING_DOMAIN` falls back to
`SENDING_FROM` and the composer says why, rather than putting an unverified
address in the header — a provider rejects the whole message for that, so the
fallback is the difference between a mail that arrives under the wrong name and
one that does not arrive at all. The `Reply-To` is their own address either
way.

This applies to `/ops` only. A dealership's outreach is from the dealership
rather than from a person, and its `Reply-To` is the `reply+<token>@` address
that routes an answer back into the buyer's timeline — not a header a rep's
own address may take over.

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
and neither once there is real data you care about. A schema change after that
point arrives as a migration (`backend/migrations/`), which the boot applies to
every database it serves — `make migrate` does the same on demand.

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

## 4. Three roles, and only two of them belong to the dealership

`make reset-db` prints them. The manager account (`dana.mercer@riversideauto.example`)
sees every lead, the team page and the assistant settings; the rep account
(`marcus.vale@riversideauto.example`) works the floor. Three more rep accounts exist
on `REP_PASSWORD` — `marcus.vale`, `priya.raman`, `trevor.osei`.

`founder@linerai.us` and `cto@linerai.us` are **ours**, on `OWNER_PASSWORD`.
They sign in at `/login?as=owner` and land on `/ops` — the demos dealerships
booked with us and the mail they sent, and nothing of a dealership's. `owner`
is a third role rather than a senior manager on purpose: `/api/ops` is closed
to a dealership's staff however senior, which is the half of the separation
that matters if you ever run this for someone else's showroom.

Startup **refuses to run** with `ENV=production` if any password is still
`liner-dev`, or if two of them are the same:

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

### Upgrading an install that predates `OWNER_PASSWORD`

`OWNER_PASSWORD` arrived after the first deployments did, and it has a
development default like the other two — so an install whose `.env` has no line
for it **refuses to boot** after the upgrade, with `OWNER_PASSWORD still set to
'liner-dev' while ENV=production`. That is the guard doing its job on a
published password, but it reads as a misconfiguration when nothing was
misconfigured. Two commands:

```bash
printf 'FOUNDER_PASSWORD=%s\nCTO_PASSWORD=%s\n' \
    "$(openssl rand -base64 12)" "$(openssl rand -base64 12)" \
    | sudo tee -a /srv/liner/.env
sudo -u liner make add-owners      # into ops_users -- no reseed, no data loss
sudo systemctl restart liner
```

`make add-owners` exists because the two accounts are only created by a fresh
seed. Without it the only way onto a database that is already taking bookings
is `make reset-db`, which deletes the leads. It is idempotent, and it never
re-hashes an account that already exists — somebody may have changed a password
with `make set-password`, and doing that silently would lock them out.

It also **moves** any account left in `users` with `role='owner'`, from the
brief period when ours lived in the dealership's table. Those rows are both a
stale login and something that can walk back onto the dealership's roster.

### Two tables, two sign-ins

`users` is the dealership's staff; `ops_users` is ours. The session cookie
records which, so an ops session is refused by every dealership endpoint and a
rep's is refused by `/api/ops` — `/api/auth/me` and the event socket are the
only two that answer for both, because "who am I" has to, and the socket
carries both sides' events.

`make reset-dealership` rebuilds the showroom fixture **in place**, leaving
`ops_users` and `ops_demo_requests` alone. `make reset-db` deletes the whole
database file and does lose them, so it is not the one to reach for on a box
with anything real on it.

**`MANAGER_PASSWORD`, `REP_PASSWORD` and `OWNER_PASSWORD` are read at seed
time, not at login.**
The hash lives in the `users` table, so editing `.env` afterwards changes what
the startup guard checks and nothing else. Worse, the `.env` recipe above calls
`openssl rand` each time it runs — so writing it twice mints new passwords and
leaves the database holding a generation that no longer exists anywhere. If you
cannot sign in with the value in `.env`, that is almost always why.

To fix it without losing data:

```bash
cd /srv/liner && sudo -u liner make set-password EMAIL=dana.mercer@riversideauto.example
```

It prompts, so the password never reaches shell history or `ps`. `make reset-db`
also works and rehashes every account, but deletes the database to do it.

### Showing the dashboard to people without accounts

`PUBLIC_DEMO=true` lets anybody with the URL straight into `/app` as a sales
rep, no password, with a **Log in as a sales manager** button in the header for
the other half. Set `PUBLIC_DEMO_EMAIL` to pick which rep; it defaults to the
first active one.

Read the sentence that follows before you turn it on. Everything a rep can see,
a stranger can see: every buyer's name, phone number and email address, every
chat and call transcript, and every call recording — which is somebody's actual
voice, and in several US states was recorded under a consent rule that assumed
a dealership would hold it. **Only ever point this at demo data.**

```bash
make reset-db && make seed-demo     # every address @example.invalid, every phone 555-01xx
```

The backend logs a warning naming what is exposed at every boot, so a box that
still has it on says so in `journalctl`. Managers still need a password, so the
team page, the assistant's instructions and publishing stay shut — and the door
opens as a rep account, never a manager, which `make agent-check` asserts.

Turning it off is deleting the line and restarting. Sessions already handed out
survive their cookie's two weeks, so rotate `SESSION_SECRET` too if you need
everyone out now.

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
- **`client_max_body_size 32m`** — ADF drops, inventory CSVs and email
  attachments (15 MB a file) are uploads, and the Cloudflare Worker posts every
  received message whole (Cloudflare accepts up to 25 MiB). nginx's 1 MB default
  would 413 before the app's own limit ever ran; each endpoint still enforces
  its own, smaller one. A config left at the old `8m` loses any received email
  over 8 MB — the Worker rejects it back to the sender, but it never arrives.

**TLS is not optional.** The session cookie is set `Secure` whenever
`ENV=production`, so over plain HTTP nobody can stay logged in. It fails loudly
rather than sending a dealer's session in the clear.

### The phone line, when you want it

Twilio, and it is the one integration whose inbound half is a **public URL on
your own host** rather than an API you call. That changes what can go wrong,
so it is worth doing in this order.

One thing has to be true before any of it: `PUBLIC_BASE_URL` must be set to the
address in the certificate, and Twilio must be able to reach it. The signature
on every inbound webhook is computed over the full URL, so a value that differs
from what you paste into the Twilio console — a trailing slash, `http` against
`https`, a bare IP — fails every call with a message about signatures and no
hint that an address is the problem.

```bash
# In /srv/liner/.env
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your-real-auth-token      # production refuses to boot on the dev default
TWILIO_NUMBER=+15025550100                  # the number you bought, E.164
PUBLIC_BASE_URL=https://YOUR-HOST           # must match the console exactly

# Optional but recommended: an API Key for outbound REST calls, so the token
# that signs inbound webhooks is not also the REST password. Both halves or
# neither -- one on its own is reported as missing rather than falling back.
TWILIO_API_KEY_SID=SKxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_API_KEY_SECRET=shown-once-when-you-create-it

# Optional: the handset an outbound click-to-call rings first
TWILIO_OPS_NUMBER=+15025550111
```

Create the key under **Account → API keys & tokens** in the Twilio console.
Keep `TWILIO_ACCOUNT_SID` set alongside it — the account names the request URL
and only the password changes, so a key without it looks right and 401s.
`/ops/phone` says which credential outbound is using.

Restart, then open `/ops/phone` as `founder@`. It says which of those is still
missing, by name, and composes the two URLs to paste — copy them from there
rather than typing them, because they are the ones the signature is checked
against.

In the Twilio console, on your number, under **Voice Configuration**:

| Twilio field | Value |
|---|---|
| A call comes in | `https://YOUR-HOST/api/phone/incoming`, HTTP POST |
| Call status changes | `https://YOUR-HOST/api/phone/status`, HTTP POST |
| A message comes in (under Messaging) | `https://YOUR-HOST/api/phone/sms`, HTTP POST |

The media stream needs no configuration: the TwiML returned by the first URL
tells Twilio where to open it, which is why it is derived from
`PUBLIC_BASE_URL` rather than set separately.

**The media socket is at `/ws/phone/media`, deliberately.** The shipped nginx
config carries the `Upgrade` and `Connection` headers on `location /ws/` and
sets `Connection ""` on everything else, so a socket under `/api/` would
connect, play the greeting and then go silent — with nothing in the app's log,
because the app never saw the upgrade. If you wrote your own nginx config,
`/ws/` needs those headers.

**The assistant needs a model as well as a line.** `LLM_MODE=live`, an API key
and `VOICE_MODEL`. Without them the number answers and nobody speaks, and
`/ops/phone` reports that as a separate fault from "no Twilio" — they have
completely different fixes.

Then choose who answers, on the same page: Liner's own assistant (pitches
Liner, books a demo) or the dealership's (real inventory, real test drives).
The switch takes effect on the next call and needs no restart, so the same
number can be handed to a prospect mid-demo.

### Texting

SMS needs no extra credentials — same account, same number, same signature
check. The third webhook row above is the whole setup.

**No assistant is connected to SMS.** Every text is written by a rep from the
buyer's page and their replies land on that buyer's timeline. `OUTBOUND_ONLY_TO`
gates texts exactly as it gates mail, so a rehearsal cannot reach a real
prospect's phone.

**STOP is honoured on both sides.** Twilio blocks a number that texted STOP and
answers a send to it with error 21610; this system also records the opt-out and
refuses before attempting, so a rep sees a reason rather than a send that looks
like it went. The record lives in `ops_sms_opt_outs`, which `make
reset-dealership` deliberately does not touch — an opt-out a rehearsal wiped is
somebody who said stop and got texted again.

**Bulk texting still needs A2P 10DLC registration.** One-to-one works today;
sending the same message to a list from an unregistered number gets it filtered
by the carriers rather than refused by Twilio, so it is billed and arrives
nowhere. `/app/campaigns` says so rather than offering a button.

**Never set `TWILIO_VALIDATE_SIGNATURE=false` on a real host.** It exists for a
local tunnel, where the URL Twilio signed is not the one this process sees;
production refuses to boot with it off, because that signature is the only
thing standing in front of a URL that answers by opening a paid call.

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
# (make build alone now installs whatever the pull added, but install is still the full reset)
sudo systemctl stop liner
sudo -u liner make migrate     # the schema, before the new code starts; prints each database's revision
sudo systemctl start liner
```

The boot migrates too, so `make migrate` is not what makes it work — it is
what puts a failing migration on your screen rather than in the journal of a
unit that will not start. Stopped first, so no older process is writing to a
table while a migration rebuilds it; the gap is the migration's few seconds.

Do **not** run `make reset-db` on an update — it wipes the database.

## What this setup does not do

- **No backups.** There is a database per store under `backend/var/stores/`
  plus Liner's own `backend/var/ops.db` — `make stores` lists them and
  `make dump-ops ARGS=--files` prints the exact paths including sidecars. Copy
  each with `sqlite3 <file> ".backup out.db"`, not `cp`: these run in WAL mode
  and a plain copy taken mid-write can be torn, or miss writes still sitting
  in `-wal`.
- **Migrations run forward only.** Every database is brought to the newest
  revision at boot (`app/migrate.py`); there is no downgrade path, so the way
  back from a bad deploy is the backup above, not an older checkout.
- **No rate limiting.** `/api/chat/sessions` is public and creates a row per
  call. Fine for a demo you share deliberately; add an nginx `limit_req` before
  the URL goes anywhere wide.
- **No log rotation** beyond journald's defaults.

## Several dealerships on one host

One process can serve more than one, told apart by the first path segment:

```
https://linerai.us/craigandlandreth/app     Craig's dashboard
https://linerai.us/alsbou/app               Alsbou's dashboard
https://linerai.us/alsbou/showroom          the link you send Alsbou
https://linerai.us/ops                      ours, never prefixed
https://linerai.us/app                      whichever store DEALERSHIP= names
```

Nothing extra goes in `.env` and nginx needs no new rule — it is still one
`proxy_pass`, because the prefix is stripped inside the application rather
than by the proxy.

**Each store has its own SQLite file** at `backend/var/stores/<slug>.db`. That
is the isolation: no table carries a dealership id, and `create_all` adds a
table to an existing database but never a column, so a shared file could not
keep two dealerships' buyers apart. The file boundary is enforced by the OS.

### How many databases, and where

Four kinds of file, and `make stores` prints the real paths on any given box:

| File | Holds | When it exists |
|---|---|---|
| `backend/liner.db` | the **default store** — whatever `DEALERSHIP=` names when it names nothing | always; this is the original single database |
| `backend/var/stores/<slug>.db` | one named dealership | one per profile you have seeded |
| `backend/var/ops.db` | Liner's own six `ops_` tables | always, built at boot |
| `backend/var/` (not a database) | call recordings, crawl snapshots, photos | as they are written |

So a single-dealership install is **two** databases: `liner.db` and `ops.db`.
Each prospect you add is one more. Every one of them is SQLite in WAL mode, so
each is really three files — `.db`, `.db-wal`, `.db-shm` — and a backup that
takes only the `.db` can lose recent writes.

Two asymmetries worth knowing before you write any script against this:

- **The default store is not under `var/stores/`.** It stayed at
  `backend/liner.db` deliberately, so an existing deployment keeps working
  untouched — but it means `cp backend/var/stores/*.db` backs up your prospects
  and misses your actual dealership.
- **`ops.db` is not under `var/stores/` either**, and it is the one file here
  that cannot be rebuilt from a seed: it holds the demo requests and support
  mail real people sent you. `make dump-ops ARGS=--files` prints the correct
  `cp` line for every file including both of these.

### Moving from one database to many

Going from a single-database install to this layout. **The dealership's own data
does not move** — `liner.db` carries on being the default store, which is why
`/app` and every existing URL keep working. Only the `ops_` tables relocate.

```bash
# 0. Back up first. Both commands: the JSON is re-importable, the file copy is
#    the real backup. Do this before touching anything.
make dump-ops ARGS=--files     # prints the cp commands; run them
make dump-ops                  # -> backend/var/ops-dump-<stamp>.json

# 1. Deploy the new code and restart. Boot creates backend/var/ops.db with the
#    six tables in it, empty.
git pull && make install && make build
sudo systemctl restart liner

# 2. Move the ops rows into it. Existing rows win, so this is safe to re-run,
#    and ops_users de-duplicates on the address.
make restore-ops FILE=backend/var/ops-dump-<stamp>.json

# 3. Restart again, because the process caches its engine per database and is
#    still holding the file as it was at step 1.
sudo systemctl restart liner

# 4. Check /ops in a browser: sign in at /login?as=owner and confirm the demo
#    requests and mail are there. THEN drop the old copies.
make prune-ops                 # reports what it would remove
make prune-ops ARGS=--apply
```

Step 4 is last on purpose: `prune-ops` refuses a store holding a row `ops.db`
does not have, but "the rows are present" and "the dashboard reads them" are
two different facts and only a browser settles the second.

**To add a prospect** after that, write the profile and seed it — nothing about
the existing store changes:

```bash
DEALERSHIP=<slug> make reset-db          # creates var/stores/<slug>.db
DEALERSHIP=<slug> make seed-demo         # optional demo buyers
```

And give their mailbox to the Cloudflare Worker, or their mail is dropped at
the edge before it reaches us: add `<mailbox>@` (the profile's `mailbox:`,
e.g. `alsbou@`) to `ALLOWED_RECIPIENTS` in
`backend/app/integrations/email/worker/wrangler.jsonc` and run
`wrangler deploy`. `make smoke` fails on a profile whose mailbox is missing
from that list.

**To make the default store a named one** (so every dealership is symmetric
under `var/stores/` and `liner.db` goes away), it is a file copy plus one `.env`
line. Stop the service first — replacing a database under a running process
leaves it writing to the unlinked inode, and the writes are lost silently:

```bash
sudo systemctl stop liner
cp backend/liner.db     backend/var/stores/<slug>.db
cp backend/liner.db-wal backend/var/stores/<slug>.db-wal   # if present
cp backend/liner.db-shm backend/var/stores/<slug>.db-shm   # if present
# add DEALERSHIP=<slug> to .env
sudo systemctl start liner
```

Every URL gains the prefix when you do that: `/app` becomes `/<slug>/app`.
Keep `liner.db` until you have signed in and looked — then remove it, or a
later run with `DEALERSHIP` unset quietly reads a stale copy of the dealership
you thought you had moved.

Add one:

```bash
# 1. Write the profile: backend/config/dealerships/<slug>.yaml
#    (copy an existing one; it refuses to seed until the real address,
#    phone and hours are filled in)

# 2. Seed that store, and only that store
DEALERSHIP=<slug> make reset-db

# 3. Check it
make stores
```

The seed prints each account's generated password once. Nothing needs a
restart to become routable — the store list is read off the profile directory
per request.

**Signing in is one form for every store.** `/login` looks the address up
across the seeded stores and redirects to whichever holds it; a signed-in user
who opens another dealership's URL is sent back to their own. A session is
refused outright at a store it was not minted against, so a cookie cannot be
carried from one dealership to another.

**`/ops` is deliberately not per-store** and reads `backend/var/ops.db`, which
is Liner's own database and not any dealership's. It used to read the default
store, with the six `ops_` tables also created — empty and unread — in every
other store's file. That is what one file per store made untenable: the copies
were not empty for long, so `founder@` existed once per dealership and a demo
somebody booked with us landed in whichever file happened to be active.

`OpsBase` is a second SQLAlchemy metadata rather than the same one pointed at
another engine, so a store's `create_all` cannot build an ops table by
accident. The tables in files seeded before the split are **not removed on
boot** — deleting something that might be the only copy of a demo request is
not a migration to run silently. Three commands cover them:

```bash
make dump-ops                 # every ops_ row, from ops.db AND every store
make dump-ops ARGS=--files    # prints the cp commands for the files themselves
make restore-ops FILE=backend/var/ops-dump-<stamp>.json
make prune-ops                # report what it would drop from the store files
make prune-ops ARGS=--apply   # drop them
```

Run them in that order on an upgrade: dump, restore, then prune. `prune-ops`
checks every row against `ops.db` first and **refuses a whole store** that holds
one it cannot find there, exiting non-zero and naming the row — there is no flag
to override it, because the answer is to restore first. It reports by default and
writes only with `--apply`.

`dump-ops` walks every store because that is where the strays are. `restore-ops`
reads it all into `ops.db`: rows already present by primary key are skipped, so
running it twice is safe, and `ops_users` is de-duplicated on the **address** —
three stores each seeded `founder@` with a different id, so the id says nothing
about whether it is the same person. Add `ARGS=--dry-run` to see what it would
do without writing.

**Stop the service before you replace a database file.** The engine is cached
for the life of the process, so a running uvicorn that had already opened
`ops.db` keeps writing to the old inode after the file is unlinked — the writes
succeed, the API answers 200, and nothing reads them back. Measured here: a
demo request was created over HTTP and `NoResultFound` came straight back on
the row that had just been written. `systemctl restart liner` after any restore.

**Back up `ops.db` separately.** It is outside `backend/var/stores/`, so a
backup script that globs the stores directory misses it — and it holds the
demo requests and support mail real people sent, which is the one thing here
that cannot be rebuilt from a seed.

**`make reset-db` only ever deletes the store `DEALERSHIP=` names**, sidecars
included. `rm -f backend/liner.db*` is no longer the right command and will
destroy the wrong file.

## Several dealer groups on their own server

The shape this is heading for. **`linerai.us` stays where it is**: the landing
page, `/ops`, and Liner's own mail (`support@`, `founder@`, `cto@`). **Each
dealer group gets a subdomain on a second server**, with Postgres behind it
and a database per group:

| Address | Box | Its data |
|---|---|---|
| `linerai.us` | the one running now | its SQLite files, unchanged |
| `alsbou.linerai.us` | the group server | `liner_alsbou` |
| `craigandlandreth.linerai.us` | the group server | `liner_craigandlandreth` |
| `riverside.linerai.us` | the group server | `liner_riverside` |

A group is one database, so a manager works across all of its lots in one
dashboard. Adding a group later is a profile, a DNS record and a line in the
Worker's config; nginx names no group and routing needs no restart.

**The subdomain is the store.** With `STORE_DOMAIN=linerai.us` the Host header
picks the store exactly as a `/alsbou/` prefix does, and every link composed for
that group's buyers — an emailed application, a signature image, the website
chat tag — names the subdomain. A subdomain that is no group's answers 404
rather than falling back to some default store, and nothing of Liner's own
(`/ops`, owner sign-in) is served on a group's host.

Everything below was run against the shipped files: `deploy/liner-groups.nginx.conf`
under nginx 1.24 in front of the app in this mode, the old box's redirects, and
a tag written for the old address, followed by a browser from a dealer's page
to a chat on the subdomain.

### 1. DNS and the certificate, in Cloudflare

1. **DNS:** an `A` record per group (`alsbou`, `craigandlandreth`, `riverside`)
   to the new server's IP, **proxied**. Leave `linerai.us` and `www` alone. Use
   records rather than a `*` wildcard, so only names you created reach the box.
2. **Certificate:** SSL/TLS → Origin Server → *Create certificate*, for
   `*.linerai.us` and `linerai.us`, RSA, the default fifteen years. Put both
   halves on the new server:
   ```bash
   sudo install -d -m 700 /etc/ssl/cloudflare
   sudo tee /etc/ssl/cloudflare/linerai.us.pem >/dev/null   # paste the certificate, then Ctrl-D
   sudo tee /etc/ssl/cloudflare/linerai.us.key >/dev/null   # paste the private key, then Ctrl-D
   sudo chmod 600 /etc/ssl/cloudflare/linerai.us.key
   ```
   Only Cloudflare connects to this box, and it trusts its own Origin CA; a
   browser sees Cloudflare's edge certificate, which covers one level of
   subdomain on every plan. Nothing to renew and no DNS challenge — a Let's
   Encrypt wildcard needs DNS-01, which means a token on this box that can edit
   the zone.
3. **SSL/TLS mode: Full (strict).** It is one setting for the whole zone, so the
   current box has to present a valid certificate as well (Let's Encrypt and
   Origin CA both are). Check it still answers after switching; if it shows
   Cloudflare's 526, its certificate is the problem, not the new box.

### 2. Postgres

```bash
sudo apt install postgresql                  # 13 or later
PGPASS=$(openssl rand -hex 24)               # hex: nothing to escape inside a URL
sudo -u postgres psql -c "CREATE ROLE liner LOGIN CREATEDB PASSWORD '$PGPASS'"
```

Keep that shell open: `.env` below is written from it. `CREATEDB` because the
app makes each group's database itself — `make migrate ARGS=--create`,
`make to-postgres` and `make reset-db` all do — with the settings it needs:
UTF-8, and the `C` collation, so text sorts byte by byte as it did in SQLite and
a list comes back in the same order it always did. To keep that right away from
the role, make each database by hand instead (one per group, plus `liner` and
`liner_ops`) and leave `CREATEDB` off:

```sql
CREATE DATABASE liner_alsbou OWNER liner TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C';
```

### 3. The code and `.env`

The code goes on as in §1: the `liner` user, `/srv/liner`, `uv` system-wide.
Then, in the shell that set `PGPASS`:

```bash
sudo tee /srv/liner/.env >/dev/null <<EOF
ENV=production
SESSION_SECRET=$(openssl rand -hex 32)

# The subdomain is the store: alsbou.linerai.us is Alsbou's.
STORE_DOMAIN=linerai.us

# One database per group; {slug} is the group's profile name.
DATABASE_URL_TEMPLATE=postgresql+psycopg://liner:$PGPASS@127.0.0.1:5432/liner_{slug}
# Two more that every boot opens and that stay empty here: the unprefixed
# default store, which this box serves to nobody, and Liner's own, which is
# on linerai.us.
DATABASE_URL=postgresql+psycopg://liner:$PGPASS@127.0.0.1:5432/liner
OPS_DATABASE_URL=postgresql+psycopg://liner:$PGPASS@127.0.0.1:5432/liner_ops

# Empty on purpose -- see below.
ALLOWED_ORIGINS=

# Production refuses to boot on the development default of any of these.
# Everybody's real password comes across with the rows; these are only what
# a fresh seed would use.
MANAGER_PASSWORD=$(openssl rand -base64 12)
REP_PASSWORD=$(openssl rand -base64 12)
FOUNDER_PASSWORD=$(openssl rand -base64 12)
CTO_PASSWORD=$(openssl rand -base64 12)
# Liner's number is answered on linerai.us, not here. A random token seals
# this box's phone webhook: nobody can sign a request with it.
TWILIO_AUTH_TOKEN=$(openssl rand -hex 32)

# The same value as on linerai.us: one Worker posts to both boxes, with one secret.
WEBHOOK_SECRET=paste-the-current-box-value-here
EOF
sudo chown liner:liner /srv/liner/.env && sudo chmod 600 /srv/liner/.env
```

Then copy these from the current box's `.env` as they are: `OUTBOUND_ONLY_TO`,
the email lines (`EMAIL_SENDER`, `RESEND_API_KEY`, `SENDING_DOMAIN`) and the
model lines (`LLM_MODE`, `OPENAI_API_KEY` and whatever else you set there). A
second Resend key for this box is better than the same one twice: either can
then be revoked without stopping the other box's mail.

Three things are left out on purpose:

- **`PUBLIC_BASE_URL`.** Every link this box composes is for a group and names
  that group's subdomain; the one other reader is the phone line, which is not
  here.
- **`DEALERSHIP`.** Nothing is served unprefixed on this box.
- **Any address in `ALLOWED_ORIGINS`.** Every page here calls its own host, and
  the website chat's two public endpoints answer cross-origin by themselves.
  Listing the group subdomains there would let each read the others' API with
  a signed-in person's cookie: `alsbou.linerai.us` and
  `craigandlandreth.linerai.us` are one *site* to a browser, so the cookie goes
  along, and CORS would be all that said no.

### 4. The databases, then the data

```bash
cd /srv/liner
sudo -u liner make install && sudo -u liner make build
sudo -u liner make migrate ARGS=--create
```

`--create` makes `liner` and `liner_ops`, the two every boot opens; a group's
own database is made by the copy. `migrate` then prints each one at its
revision.

**On the current box**, see what there is, then take copies. The files run in
WAL mode, so `.backup` rather than `cp` — a copy taken mid-write can be torn or
miss what is still in `-wal` — and it is safe with the service running:

```bash
cd /srv/liner && sudo -u liner make stores
sudo apt install sqlite3                     # if it is not there
sudo -u liner mkdir -p move/var/stores
for g in alsbou craigandlandreth; do         # each group with a file under var/stores/
  sudo -u liner sqlite3 backend/var/stores/$g.db ".backup move/var/stores/$g.db"
done
# Only if a group is the unprefixed store -- backend/liner.db, the one the box
# serves when .env names no DEALERSHIP:
sudo -u liner sqlite3 backend/liner.db ".backup move/liner.db"
```

Run as `liner` so nothing root-owned is left beside a live database. The rest
of `backend/var/` — received mail, attachments, recordings, signature images,
crawl snapshots — is files that rows point at by a path relative to `var/`, so
it needs only the same layout:

```bash
sudo tar -C /srv/liner/backend/var --exclude=./stores --exclude='./ops.db*' \
    --exclude=./attachments/ops -czf /srv/liner/move/var-files.tgz .
```

Liner's own (`ops.db`, and the attachments of our own mail) stays behind. Move
`/srv/liner/move` to the same place on the new box however you move files
between the two, owned by `liner`, then there:

```bash
cd /srv/liner
sudo -u liner tar -C backend/var -xzf move/var-files.tgz
sudo -u liner make to-postgres ARGS="--from /srv/liner/move"           # the plan: what goes where
sudo -u liner make to-postgres ARGS="--apply --from /srv/liner/move"   # the copy
sudo -u liner make stores
```

With a group in `liner.db`, add `--default-as <that group>` to both: that file
is copied into the group's database, and a `var/stores/` file of the same name,
if there is one, is not. `make stores` on the current box says which of the two
it was serving.

The copy builds each database from the migrations first, reads every row
through the models, keeps `events` ids (a dashboard replays from them), and
counts both sides afterwards: `COUNTS DIFFER` and a non-zero exit if they do
not match. It refuses a database that already holds rows; `--replace` drops and
rebuilds it, which is what the final copy over a trial one needs.

### 5. Run it

```bash
sudo cp deploy/liner.service /etc/systemd/system/liner.service
sudo systemctl daemon-reload && sudo systemctl enable --now liner
journalctl -u liner -f

sudo cp deploy/liner-groups.nginx.conf /etc/nginx/sites-available/liner-groups
sudo ln -sfn /etc/nginx/sites-available/liner-groups /etc/nginx/sites-enabled/liner-groups
sudo rm -f /etc/nginx/sites-enabled/default    # it claims default_server too, and nginx -t refuses two
sudo nginx -t && sudo systemctl reload nginx
```

nginx 1.19.4 or later, for `ssl_reject_handshake`: Ubuntu 22.04 ships 1.18,
while 24.04 and Debian 12 are fine. A name that is not `*.linerai.us` has its
connection closed without an answer, so a scanner on the bare IP is never
shown one of the dealerships. The unit starts after `postgresql.service`,
because the boot migrates every database and fails if the server is not up.

### 6. Check it, before anything points at it

The records are live, but nothing sends anybody there yet: there is no
redirect on the current box and no mail route. All of this is safe to repeat.

```bash
curl -s https://alsbou.linerai.us/api/health | head -c 120                    # "status":"ok"
curl -s -o /dev/null -w '%{http_code}\n' https://alsbou.linerai.us/ops        # 404: ours is not here
sudo -u liner make stores     # every group seeded, each with its mailbox and manager sign-in
```

Then in a browser:

- **Sign in** at `https://alsbou.linerai.us/login` with an Alsbou account. The
  password is the one they already had; everybody signs in once more, because
  a session belongs to the host it was made on.
- **Liner setup → Website:** the tag reads
  `<script src="https://alsbou.linerai.us/embed.js" data-dealer="alsbou" async></script>`.
  That is the one to hand to Get My Auto (docs/WIDGET.md).
- `https://alsbou.linerai.us/` is their storefront, with the chat on it.

### 7. The cut-over

The copy you checked is a trial; the real one is taken when you move. In this
order, and nothing on either box goes down:

1. **Current box:** take the copies again (step 4's `.backup` lines and the
   `tar`), and note the time.
2. **New box:** copy them in over the trial —
   `make to-postgres ARGS="--apply --replace --from /srv/liner/move"`, unpack
   the files again — then `sudo systemctl restart liner`.
3. **Current box: send the groups' addresses to their subdomains** with
   `deploy/liner-groups-moved.conf` (its header says where it goes). Every
   address a group ever had keeps working at the same path on the subdomain —
   a bookmark, an emailed application link, a saved car page, and a website
   chat tag already on a dealer's site. That last one follows the redirect for
   the script, then follows the chat itself, because its settings name the
   origin the chat lives at now (`frame_origin`) — which needs this version of
   the code on the current box as well.
4. **Worker:** route the groups' mail to the new box (below).
5. **Anything written for a group on the current box after step 1 exists only
   there** — a chat before the redirect, a mail before the route. On a quiet
   evening that is nothing. Mail is the part to check:
   `make mail-check TO=alsbou@linerai.us` on the current box lists every
   delivery, with its time.

**Keep the SQLite files on the current box.** They are the way back: take the
include out of nginx and `ROUTES` out of the Worker and it serves them again —
without anything written on the new box since.

**If a group came from `liner.db`**, the current box's unprefixed `/app`,
`/chat`, `/call`, `/showroom` and `/r/` are that group's stale copy. Uncomment
the last block of `liner-groups-moved.conf` so they follow it. The landing
page's *Test the chat* link is `/chat`, so it then opens that group's chat on
its subdomain. `/login`, `/ops` and `/api` stay: Liner's own sign-in, dashboard
and mail intake.

### Mail: one Worker, two boxes

Email Routing hands every address on `linerai.us` to one Worker, and the Worker
posted to one URL. So the groups' addresses now go to the new box and
everything else — Liner's own — where it always went. In
`backend/app/integrations/email/worker/wrangler.jsonc`, after
`ALLOWED_RECIPIENTS` (with a comma after that line):

```jsonc
"ROUTES": "alsbou@=https://alsbou.linerai.us/api/emails/inbound/raw,craigandlandreth@=https://craigandlandreth.linerai.us/api/emails/inbound/raw,reply+=https://alsbou.linerai.us/api/emails/inbound/raw"
```

then `wrangler deploy` from that directory. `prefix=url` pairs, first match
wins, everything else to `WEBHOOK_URL` as before. Opening the Worker's own URL
in a browser prints where each route goes (`routed elsewhere:`), which is the
quickest way to see what is actually deployed.

- **`reply+` goes to the new box whole.** A reply token names no group, but the
  send that minted it is a row in exactly one group's database and the intake
  asks all of them, so any group's host will do. Every token the current box
  minted for a group came across with the copy. What must not happen after the
  move is the current box minting new ones — a reply would reach the new box,
  match nothing, and be filed as a stranger's mail with the group whose host
  received it. The redirects are what prevent that: nobody sends a group's mail
  from a dashboard that is somewhere else.
- **Riverside has no mailbox of its own** — the fixture writes as `sales@` —
  so its fresh mail goes wherever `sales@` does, which is the current box
  unless you route it. Replies to what it sends come back by token like
  everyone's.
- **Both boxes need the same `WEBHOOK_SECRET`.** There is one secret on the
  Worker, and a mismatch is a 401 it treats as permanent: the mail is lost,
  not delayed.
- `make smoke` fails on a route that is not https, not the raw intake, or for
  an address the recipient list drops before any route is read.

### Adding a group later

1. Its profile, `backend/config/dealerships/<slug>.yaml` (docs/DEMO.md), pushed
   and pulled onto the new box.
2. Its database: `sudo -u liner env DEALERSHIP=<slug> make reset-db` there —
   on Postgres that creates `liner_<slug>` and seeds it, printing the logins.
3. A proxied `A` record for `<slug>` in Cloudflare.
4. Its mailbox in `ALLOWED_RECIPIENTS` and in `ROUTES`, then `wrangler deploy`.

Nothing in nginx, and no restart: the store list is read off the profile
directory per request.

### Backups on Postgres

One `pg_dump` per database. `make dump-ops ARGS=--files` prints the exact lines
for every database this box names — they carry the database password, so run
them rather than pasting them anywhere — and `backend/var/` is the rest.
`pg_restore` reads them back. The copy from SQLite is not a backup: the SQLite
files stop being current the moment the new box takes over.
