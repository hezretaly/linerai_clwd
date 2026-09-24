# The new server: everything moves, and starts fresh

This is the runbook for the server that takes over from the one serving
`linerai.us` today. **It is written for a Claude Code session running on that
server**, driven from the Claude app, and a person can follow it just as well.
Work top to bottom. Each step ends with a check; do not start the next step
until the check reads right.

## What is being built

| Name | Process | Data | Mail |
|---|---|---|---|
| `linerai.us` | production, `/srv/liner`, port 8000 | `liner_ops` (Liner's own), `liner` (events, mail to nobody) | Liner's own: `support@`, `founder@`, `cto@`, from `support@linerai.us` |
| `www.linerai.us` | nginx | — | redirects to `linerai.us` |
| `alsbou.linerai.us` | production, the same process | `liner_alsbou` | from `sales@alsbou.linerai.us`, replies to `reply+<token>@alsbou.linerai.us` |
| `demo.linerai.us/<store>` | the demo, `/srv/liner-demo`, port 8001 | `liner_demo`, `liner_demo_<store>` | none: the outbox records it and nothing leaves |

- **Fresh start.** Nothing is copied from the old server. Alsbou's store is
  seeded from its profile. `/ops` starts empty, with `founder@` and `cto@` in
  it.
- **The demo is isolated from production.** It has its own checkout, its own
  system user, its own `.env`, its own Postgres role and databases, and its
  own process. It sends no mail.
- **`linerai.us/chat`** is the landing page's *Test the chat*. It goes to the
  demo, and so does everything else the demo stores had on `linerai.us`.
  `linerai.us/alsbou/...` goes to `alsbou.linerai.us`. Both are rules in
  `deploy/linerai.nginx.conf`.
- **Twilio** settings come across from the old `.env` unchanged, untested.
  The phone line's *dealership* persona would answer from the unprefixed
  store, which is empty on this box. Keep `phone_persona` on Liner's own, or
  off.
- **The old server keeps running, untouched**, until the step that points
  `linerai.us` here. After that nothing reaches it; it stays up until the
  user decides otherwise, and pointing DNS back is the way back.

## Rules for the session

- **STOP** marks something the user does outside this box: Cloudflare,
  Resend, typing a secret. Say exactly what is needed, then wait for the user
  to say it is done.
- **Never ask for a secret in the chat, and never print one.**
  - Secrets are generated here straight into files, or typed into a file by
    the user in their own terminal.
  - Do not `cat` a `.env`. To see whether a line is filled, use the `awk` line
    in step 5, or `make live-check`. Both say *set* or *EMPTY*, never the value.
- **Everything the app touches is run as its owner:** `sudo -u liner` in
  `/srv/liner`, `sudo -u linerdemo` in `/srv/liner-demo`. Never build, seed or
  migrate as root or as `deploy`. A root-owned file under either checkout
  breaks the next build or the first write (docs/DEPLOY.md §1).
- **If a step fails, stop and show the output.** Do not work around it.
  - Never flip `LLM_MODE`, invent a credential or loosen `OUTBOUND_ONLY_TO` to
    make a check pass. CLAUDE.md, *Don't*.
  - The one exception is `OUTBOUND_ONLY_TO`, which the user sets on purpose in
    step 12.
- **`make live-check` is the verdict.** It tests this box against the real
  vendors, from this box. Every line is PASS, FAIL or SKIP, and a FAIL names
  the layer to look at (`scripts/live_check.py`).

## 0. Look first

```bash
hostname; lsb_release -ds; uname -m
free -h; df -h /
sudo ss -ltnp | grep -E ':(80|443|8000|8001|5432) ' || echo "ports free"
command -v nginx && nginx -v; command -v psql && psql --version
command -v node && node --version; python3 --version
sudo ufw status 2>/dev/null | head -5
```

**STOP** and report if any of these hold:

- something other than nginx already listens on 80 or 443, or anything holds
  8000 or 8001;
- the OS is older than Ubuntu 22.04 / Debian 12;
- the disk has less than 5 GB free.

**Swap.** With under 2 GB of memory, the frontend build can be killed for
memory. Add swap first:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile \
  && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

**Firewall.** If `ufw` is active, it must allow 22, 80 and 443:

```bash
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
```

## 1. Packages

```bash
sudo apt update
sudo apt install -y git make curl openssl nginx postgresql python3 python3-venv build-essential sqlite3
# Node 20 or later for the frontend build; Ubuntu's own is older.
node --version 2>/dev/null | grep -qE '^v(2[0-9]|[3-9][0-9])' || {
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs; }
# uv system-wide, so `sudo -u liner` can see it (docs/DEPLOY.md §1).
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
```

Check:

```bash
nginx -v             # 1.19.4 or later, for ssl_reject_handshake
psql --version
node --version       # v20 or later
uv --version
```

## 2. Two users, two checkouts

```bash
REPO=https://github.com/hezretaly/linerai_clwd.git
BRANCH=claude/liner-ai-implementation-8xehez
for pair in liner:/srv/liner linerdemo:/srv/liner-demo; do
  user=${pair%%:*}; dir=${pair#*:}
  sudo adduser --system --group --home "$dir" --no-create-home "$user"
  sudo install -d -o "$user" -g "$user" "$dir"
  sudo -u "$user" git clone -b "$BRANCH" "$REPO" "$dir"
done
sudo -u liner git -C /srv/liner log -1 --oneline
sudo -u linerdemo git -C /srv/liner-demo log -1 --oneline
```

The repository is public, so no key is needed. Both checkouts should print
the same commit; tell the user which one it is.

## 3. The two `.env` files, with every generated value filled

The templates are `deploy/production.env.example` and
`deploy/demo.env.example`. This fills each `__GENERATE__` with its own random
value, and `__PGPASS__` with one value per instance, and prints none of them:

```bash
fill() {  # fill FILE: a random value per __GENERATE__, one shared __PGPASS__
  sudo sed -i "s/__PGPASS__/$(openssl rand -hex 24)/g" "$1"
  while sudo grep -q '__GENERATE__' "$1"; do
    sudo sed -i "0,/__GENERATE__/s//$(openssl rand -hex 24)/" "$1"
  done
}
sudo install -m 600 -o liner -g liner /srv/liner/deploy/production.env.example /srv/liner/.env
sudo install -m 600 -o linerdemo -g linerdemo /srv/liner-demo/deploy/demo.env.example /srv/liner-demo/.env
fill /srv/liner/.env
fill /srv/liner-demo/.env
sudo grep -c '__' /srv/liner/.env /srv/liner-demo/.env    # 0 and 0
```

## 4. Postgres: a role per instance, and nobody else connects

Each role's password is read back out of its own `.env`, so it never exists
anywhere else:

```bash
pgpass() { sudo grep -oP '^DATABASE_URL=postgresql\+psycopg://[a-z]+:\K[0-9a-f]+' "$1"; }
P=$(pgpass /srv/liner/.env)
sudo -u postgres psql -v ON_ERROR_STOP=1 -qc "CREATE ROLE liner LOGIN CREATEDB PASSWORD '$P'"
P=$(pgpass /srv/liner-demo/.env)
sudo -u postgres psql -v ON_ERROR_STOP=1 -qc "CREATE ROLE linerdemo LOGIN CREATEDB PASSWORD '$P'"
unset P
sudo -u postgres psql -Atc "SELECT rolname FROM pg_roles WHERE rolname LIKE 'liner%'"
```

`CREATEDB` is there because the app makes its own databases, with UTF-8 and
the `C` collation (docs/DEPLOY.md, *Postgres*). The databases themselves come
in step 6.

## 5. STOP: the user's values

The user edits both files **in their own terminal**, not in this chat:

```bash
sudo nano /srv/liner/.env
sudo nano /srv/liner-demo/.env
```

What goes in, line by line:

| Line | Production (`/srv/liner/.env`) | Demo (`/srv/liner-demo/.env`) |
|---|---|---|
| `OPENAI_API_KEY` | the key | the same key |
| `OPENAI_MODEL` | only if the old box set one; add the line | the same |
| `RESEND_API_KEY` | the key; a new one for this box is better, so either box's key can be revoked alone | — |
| `WEBHOOK_SECRET` | **the value the Cloudflare Worker holds.** Copy it from the old box's `.env` (`sudo grep ^WEBHOOK_SECRET= /srv/liner/.env` there). Or choose a new one and set it on the Worker (step 9) | leave it: nothing posts mail there |
| `OUTBOUND_ONLY_TO` | leave empty for now; set in step 12 | leave empty |
| `TWILIO_*` | copy each line the old `.env` has, unchanged. The real `TWILIO_AUTH_TOKEN` replaces the generated one | leave it |

Then check, without printing a value:

```bash
for f in /srv/liner/.env /srv/liner-demo/.env; do echo "$f"
  sudo awk -F= '/^(OPENAI_API_KEY|RESEND_API_KEY|WEBHOOK_SECRET|OUTBOUND_ONLY_TO|TWILIO_[A-Z_]+)=/ {
    print "  " $1, (length($0) > length($1) + 1 ? "set" : "EMPTY") }' "$f"; done
```

`OPENAI_API_KEY` has to read *set* in both, and `RESEND_API_KEY` and
`WEBHOOK_SECRET` in production.

## 6. Install, build, and the databases

**Production.** The only store is Alsbou. The unprefixed store `liner` is
built and left empty.

```bash
cd /srv/liner
sudo -u liner make install
sudo -u liner make build
sudo -u liner make migrate ARGS=--create
# Seeds liner_alsbou. The seed prints the logins it made, once, each on a line
# with the address on it: the whole output goes to a root-only file, and only
# the lines without an address reach this session.
sudo install -m 600 /dev/null /root/liner-logins.txt
sudo -u liner env DEALERSHIP=alsbou make reset-db 2>&1 | sudo tee -a /root/liner-logins.txt | grep -v '@'
sudo -u liner make add-owners 2>&1 | sudo tee -a /root/liner-logins.txt | grep -v '@'
sudo -u liner make stores
```

`make stores` should show `alsbou` seeded, with `mail sales@alsbou.linerai.us`
and its manager's sign-in.

**Demo.** The unprefixed store is the Riverside fixture. Craig and Landreth
and Riverside are stores by path, each with 50 demo buyers.

```bash
cd /srv/liner-demo
sudo -u linerdemo make install
sudo -u linerdemo make build
sudo -u linerdemo make migrate ARGS=--create
for s in "" craigandlandreth riverside; do
  sudo -u linerdemo env DEALERSHIP=$s make demo-db 2>&1 \
    | sudo tee -a /root/liner-logins.txt | grep -v '@' | tail -5
done
sudo -u linerdemo make stores
```

**Each role's databases are closed to the other.** Postgres lets any role
*connect* to a new database by default. Tables stay the owner's either way,
but connecting is more than either needs:

```bash
sudo -u postgres psql -Atc "SELECT datname FROM pg_database WHERE datname LIKE 'liner%'" \
  | while read -r db; do sudo -u postgres psql -qc "REVOKE CONNECT ON DATABASE \"$db\" FROM PUBLIC"; done
```

Their owners keep access. Run this again after a new store is seeded later.

## 7. Two services

```bash
sudo cp /srv/liner/deploy/liner.service /etc/systemd/system/liner.service
sudo cp /srv/liner/deploy/liner-demo.service /etc/systemd/system/liner-demo.service
sudo systemctl daemon-reload
sudo systemctl enable --now liner liner-demo
sleep 5
curl -s 127.0.0.1:8000/api/health | head -c 200; echo
curl -s 127.0.0.1:8001/api/health | head -c 200; echo
```

Both should answer `"status":"ok"` with `"env":"production"`. If one does not,
`journalctl -u liner -n 60` or `-u liner-demo`: a boot that refuses names
the line of `.env` it refused.

## 8. STOP: the certificate, then nginx

The user makes the certificate in Cloudflare: **SSL/TLS → Origin Server →
Create certificate**, for `*.linerai.us` and `linerai.us`, RSA, fifteen years.
The two halves go onto this box in the user's own terminal, never through this
chat:

```bash
sudo install -d -m 700 /etc/ssl/cloudflare
sudo tee /etc/ssl/cloudflare/linerai.us.pem >/dev/null   # paste the certificate, then Ctrl-D
sudo tee /etc/ssl/cloudflare/linerai.us.key >/dev/null   # paste the private key, then Ctrl-D
sudo chmod 600 /etc/ssl/cloudflare/linerai.us.key
```

Then, once the user says both are in:

```bash
sudo openssl x509 -in /etc/ssl/cloudflare/linerai.us.pem -noout -subject -ext subjectAltName
sudo cp /srv/liner/deploy/linerai.nginx.conf /etc/nginx/sites-available/linerai
sudo ln -sfn /etc/nginx/sites-available/linerai /etc/nginx/sites-enabled/linerai
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

On a box with no IPv6, delete the `[::]` `listen` lines if `nginx -t` says
*Address family not supported*.

Check the routing on this box, before any DNS points here. `-k`, because
nothing but Cloudflare trusts an Origin CA certificate:

```bash
R() { curl -sk -o /dev/null -w "%{http_code} %{redirect_url}  <- $2$1\n" \
      --resolve "$2:443:127.0.0.1" "https://$2$1"; }
R /api/health linerai.us                 # 200
R "/chat?embed=1" linerai.us             # 307 https://demo.linerai.us/chat?embed=1
R /alsbou/embed.js linerai.us            # 308 https://alsbou.linerai.us/embed.js
R /x www.linerai.us                      # 301 https://linerai.us/x
R /api/health alsbou.linerai.us          # 200
R /api/health nobody.linerai.us          # 404: no such dealership
R /craigandlandreth/api/health demo.linerai.us   # 200
R / demo.linerai.us                      # 302 https://linerai.us/
curl -sk -o /dev/null -w "%{http_code}\n" --resolve other.com:443:127.0.0.1 https://other.com/   # 000: closed
```

## 9. STOP: the user's outside services

Hand the user this list. Nothing here moves `linerai.us` yet.

**Cloudflare DNS** (proxied, orange cloud), for this server's IP (`curl -4s ifconfig.me`):

- `A alsbou` → this IP
- `A demo` → this IP

Leave `linerai.us` and `www` pointing at the old server until step 11.

**Cloudflare SSL/TLS: Full (strict).** It is one setting for the whole zone.
After switching, check that the old server still loads, because until step 11
it still serves `linerai.us`. A 526 means its certificate, not this box.

**Resend**, for Alsbou's own mail domain:

1. **Domains → Add domain → `alsbou.linerai.us`.**
2. Add the records Resend lists to Cloudflare DNS: the DKIM TXT, the SPF TXT
   and the `send.alsbou` MX. They must be **DNS only** (grey cloud).
3. Press **Verify**.

`linerai.us` is already verified there. `make live-check` asks Resend about
both.

**Cloudflare Email Routing**, so mail to `alsbou.linerai.us` reaches the
Worker:

1. **Email → Email Routing → Settings → Subdomains → add `alsbou`.** Cloudflare
   adds that subdomain's MX and SPF records itself.
2. Route it to the Worker (`crm-inbound-worker`), the way `linerai.us` is
   routed. Use a catch-all for the subdomain if the dashboard offers one.
   Otherwise:
   - add rules for `sales@alsbou.linerai.us` and `reply@alsbou.linerai.us`,
     both *Send to a Worker*;
   - turn on **subaddressing**, so `reply+<token>@` matches the `reply@` rule.

The Worker keeps mail by local part, and `sales@` and `reply+` are already on
its list.

**The Worker's secret:**

- If `WEBHOOK_SECRET` in production's `.env` is the old box's value, nothing
  changes at the Worker.
- If it is new, set the same value there from wherever `wrangler` is logged
  in:
  ```bash
  cd backend/app/integrations/email/worker && npx wrangler secret put WEBHOOK_SECRET
  ```
- While there, `npx wrangler deploy` from this checkout installs the current
  Worker. It posts each message whole, so attachments arrive.
- Either way it keeps posting to `https://linerai.us/api/emails/inbound...`,
  which becomes this box in step 11.

## 10. Test everything that does not need `linerai.us` yet

```bash
cd /srv/liner && sudo -u liner make live-check ARGS="--only chat,voice,resend,widget"
cd /srv/liner-demo && sudo -u linerdemo make live-check ARGS="--demo --store craigandlandreth"
```

- **Production** goes through Cloudflare to `https://alsbou.linerai.us`. It
  runs one real chat turn and one voice session with a Realtime exchange, asks
  Resend about both domains, and fetches the website chat's loader and
  settings as `alsboucars.com` and as a stranger.
- **intake** posts a reply the way the Worker would, to the Worker's URL --
  which is still the old box until step 11, and posting there would file a
  test message in the old box's database. So before the cut-over it is
  pointed at this box directly, which proves the app half here:
  ```bash
  cd /srv/liner && sudo -u liner make live-check \
    ARGS="--only intake --intake http://127.0.0.1:8000/api/emails/inbound/raw"
  ```
- **The demo** checks that it sends no mail and is served by path, then runs a
  real turn and the website chat.
- **Every FAIL is fixed before step 11.**
  - The `config` section's *runs on the .env this check reads* means a
    restart is owed: `sudo systemctl restart liner`.
  - Everything the checks create is removed afterwards.

Then in a browser, which the user does:

- `https://alsbou.linerai.us/login` with Alsbou's manager sign-in, from
  `sudo cat /root/liner-logins.txt` in their terminal.
- **Liner setup → Website chat** shows the tag:
  `<script src="https://alsbou.linerai.us/embed.js" data-dealer="alsbou" async></script>`.
- `https://demo.linerai.us/craigandlandreth` opens the demo storefront, with
  its chat.

## 11. STOP: the cut-over

The user points **`linerai.us`** (the apex, `A`) and **`www`** at this server's
IP in Cloudflare DNS, proxied. Email Routing's MX records and Resend's records
are not touched. Proxied changes take effect within seconds. Then confirm the
requests arrive here:

```bash
sudo tail -f /var/log/nginx/access.log   # in one pane
curl -s https://linerai.us/api/health | head -c 120; echo   # in another: the line appears above
```

Now `linerai.us/login?as=owner` is `/ops` on this box. The owners sign in
with `founder@linerai.us` and `cto@linerai.us`. Their passwords are
`FOUNDER_PASSWORD` and `CTO_PASSWORD` in `/srv/liner/.env`: the user reads
them in their own terminal, or sets their own with
`sudo -u liner make set-password EMAIL=founder@linerai.us`.

## 12. The whole thing, mail included

**STOP**: ask the user which inbox to send the test mail to. It must be
listed in `OUTBOUND_ONLY_TO` in `/srv/liner/.env`. The user types it there,
e.g. `OUTBOUND_ONLY_TO=them@example.com`. Then:

```bash
sudo systemctl restart liner
cd /srv/liner && sudo -u liner make live-check INBOX=them@example.com
```

This is every section:

- **inbox**: one email from `Alsbou Motors <sales@alsbou.linerai.us>` and one
  from `Liner <support@linerai.us>` to the user's inbox. Ask the user whether
  both arrived, and not in spam.
- **mail**: a real reply to `reply+<token>@alsbou.linerai.us` through Resend,
  Cloudflare Email Routing and the Worker, filed in Alsbou's store by its
  token. Then a message to `support@linerai.us`, filed for `/ops`. Each waits
  up to three minutes (`--timeout`) and is removed afterwards.

**When the intake passes and the whole route does not**, the app is fine.
Look at:

- Email Routing for that domain;
- the Worker's recipient list, its secret, and its URL;
- `make mail-check TO=sales@alsbou.linerai.us`, which lists every receipt for
  an address across every store.

## 13. Hand over

Tell the user, in this order:

1. **What passed**: the last `make live-check` summary line for production and
   for the demo.
2. **Where the logins are**: `sudo cat /root/liner-logins.txt` for Alsbou's
   and the demo's, and `FOUNDER_PASSWORD` / `CTO_PASSWORD` in
   `/srv/liner/.env` for `/ops`. Suggest they delete the file once saved.
3. **Alsbou's tag**:
   `<script src="https://alsbou.linerai.us/embed.js" data-dealer="alsbou" async></script>`,
   copied from Liner setup → Website chat. A tag already pasted as
   `linerai.us/alsbou/embed.js` keeps working through the redirect.
4. **`OUTBOUND_ONLY_TO=everyone`** is the user's decision, for when Alsbou's
   buyers should get their confirmations. Then `sudo systemctl restart liner`.
5. **The setup sudo rule** comes out last, by the user, because it is what
   this session runs with: `sudo rm /etc/sudoers.d/deploy-setup`.
6. **The old server** gets no traffic now. It is untouched and still the way
   back: point `linerai.us` and `www` at it again. Its data was not copied, as
   agreed.

## Later

**Updating either instance:**

```bash
cd /srv/liner && sudo -u liner git pull && sudo -u liner make build && sudo systemctl restart liner
cd /srv/liner-demo && sudo -u linerdemo git pull && sudo -u linerdemo make build && sudo systemctl restart liner-demo
```

The boot migrates every database. Back up production before a pull that
brings a migration: `sudo -u liner make dump-ops ARGS=--files` prints a
`pg_dump` line per database.

**A new dealership** on production:

1. Its profile in `backend/config/dealerships/`, pushed and pulled here.
2. Seed it: `sudo -u liner env DEALERSHIP=<slug> make reset-db`.
3. Add a proxied `A <slug>` record.
4. Add its mailbox's local part to the Worker's `ALLOWED_RECIPIENTS`, then
   `wrangler deploy`.
5. With a `mail_domain` of its own: that domain in Resend and in Email
   Routing, as in step 9.
6. Run the `REVOKE CONNECT` line from step 6 again.

Nothing changes in nginx, and no restart is needed.

**Which layer, by symptom:**

| Symptom | Where |
|---|---|
| 502 from nginx | the service: `journalctl -u liner -n 60` |
| 521 / 522 from Cloudflare | nginx or the firewall on this box |
| 525 / 526 | the certificate, or SSL mode |
| `live-check` config FAIL *runs on the .env this check reads* | `sudo systemctl restart liner` |
| chat FAIL with 401 / 404 / 429 in the detail | the key, the model name, the account's credit (`make agent-ping`) |
| intake PASS, mail FAIL | Cloudflare Email Routing or the Worker, not this app |
