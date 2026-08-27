# Email: Resend out, Cloudflare in

Two halves that fail in different places. **Sending** breaks at Resend — a bad
key, an unverified domain — and the next send says so verbatim. **Receiving**
breaks in Cloudflare, configured outside this app entirely, and breaks
*silently*: no error, no row, just replies that never arrive.

That asymmetry is why `/app/email` exists. It records every delivery the
endpoint was handed **including the ones it refused**, which is the only way to
tell a wrong secret from a missing route from a buyer who simply has not
written back.

## `.env` on the server

```bash
# --- Sending -----------------------------------------------------------------
EMAIL_SENDER=resend
RESEND_API_KEY=re_...

# Verified in Resend AND the domain Cloudflare routes mail for. It does double
# duty: outbound builds `Reply-To: reply+<token>@here`, and the catch-all on
# the same domain is what brings those replies back. If the two differ, mail
# goes out fine and every reply bounces, and nothing in the app looks wrong.
SENDING_DOMAIN=linerai.us

# Optional. Defaults to support@$SENDING_DOMAIN. A display name is worth
# setting: it is what a buyer sees in their inbox.
SENDING_FROM=Riverside Auto <support@linerai.us>

# --- Receiving ---------------------------------------------------------------
# Must equal the Worker's WEBHOOK_SECRET exactly. This is the only thing in
# front of an endpoint that writes into a buyer's history -- no session guards
# it, because Cloudflare has none to send. Generate with:
#   openssl rand -hex 32
WEBHOOK_SECRET=...

# --- Who outbound may reach --------------------------------------------------
# Empty refuses every send; a list allows those addresses; `everyone` lifts the
# limit. Sending only -- anyone can write in regardless, and a reply that
# cannot be placed is kept rather than dropped.
OUTBOUND_ONLY_TO=you@yourdomain.com
```

`ENV=production` refuses to boot while `WEBHOOK_SECRET` is still the
development default.

## Cloudflare

1. **Email Routing** enabled on `linerai.us`, Cloudflare's MX and SPF records
   live.
2. **A catch-all rule** to this Worker. Not optional: `reply+<token>@`
   addresses are minted per send and cannot be enumerated as rules. Explicit
   `support@` / `sales@` rules to the same Worker are fine alongside it.
3. **A rule, or the catch-all, for every address you publish.** The Worker
   filters recipients before it posts anything, because a catch-all sweeps up
   spam to random addresses. It accepts `support@`, `sales@`, `founder@`,
   `cto@` and `reply+` — override with an `ALLOWED_RECIPIENTS` var
   (comma-separated local parts) rather than editing the source. **A dropped
   recipient leaves no trace anywhere in the app**: no receipt, no row, no
   error, indistinguishable from nobody having written. `founder@` was missing
   from that list while the landing page published it as the way to reach a
   person, so the one address we tell people to use was the one being thrown
   away.
4. **The secret**, which is not in `wrangler.jsonc` — `vars` are plaintext in
   the dashboard and in git:

```bash
wrangler secret put WEBHOOK_SECRET   # same value as the backend's
wrangler deploy
```

`WEBHOOK_URL` in `wrangler.jsonc` must be the **public** origin. The Worker
runs on Cloudflare's edge and cannot reach a private address.

## Which auth header

The backend accepts either, and prefers the first when both are sent:

| Header | What it proves |
|---|---|
| `X-Liner-Signature` | HMAC-SHA256 hex over the exact request bytes. Authenticates the sender **and** the body — a truncated or edited payload fails. |
| `X-Webhook-Secret` | The shared secret in plain. Authenticates the sender only. |

The deployed Worker sends `X-Webhook-Secret`, which over TLS to a known origin
is an ordinary webhook arrangement. Moving to the signature is a Worker-only
change; nothing on the backend needs touching.

Both are compared in constant time, so a mismatch fails identically to any
other wrong value — the receipts are where you find out, not the response.

## Paths and the response

`/api/emails/inbound` and `/api/inbound-email` reach the same handler. The
first is what the deployed Worker posts to; the second is what this app
documented first.

The endpoint **answers before it files the mail**. It returns
`{"outcome": "received", "receipt_id": ...}` in a few milliseconds and resolves
in the background; the receipt then settles to `accepted`, `unresolved` or
`failed`. The Worker retries a 5xx or a dropped connection, so a slow answer
costs a buyer a duplicate attempt rather than a delivery.

The claim is written *before* the response, not after, and that ordering is
load-bearing. A plain "return 200, process later" loses its own dedupe: a
retry arriving mid-processing finds nothing accepted yet and files the reply a
second time. Bodies over 10 MB are refused with a 413, matching the Worker's
own limit.

**The schema is never stricter than the wire.** A Worker writes
`inReplyTo: parsed.inReplyTo ?? null` because that is the obvious way to say
"there wasn't one", and every declared field accepts `null` for that reason.
Getting this wrong is expensive rather than noisy: a 4xx tells a retrying
Worker its payload is wrong and to stop trying, so a schema quibble becomes a
buyer's reply that is gone for good rather than delayed. `make smoke` posts the
deployed Worker's payload field for field.

**A message with no `Message-ID` header still dedupes.** `JSON.stringify` drops
the key when postal-mime found none, and the retry above would then file the
same reply twice. The digest of the exact request bytes stands in — exact
rather than heuristic, because a retry re-posts the identical body while two
real emails differ in the `receivedAt` the Worker stamps per invocation. Such
an id is written `sha256:…` on the receipt and never leaves the building: put
in an outgoing `In-Reply-To` it would name a message that never existed.

## Checking it

`/app/email` in the dashboard:

- **Status** — which sender is live, and the exact variables still missing.
- **Send a test** — the real path, allow-list included, showing Resend's error
  verbatim.
- **Receive a test** — posts a signed sample to the live endpoint. The auth
  check, the dedupe and the whole resolution ladder really run; only Cloudflare
  is absent. If this works and real mail does not, the problem is in the three
  Cloudflare points above.
- **Receipts** — everything that arrived, refusals included.

The list updates itself: the dealer socket carries `email.received` the moment
a delivery is filed, and a five-minute poll covers a dropped connection. The
"Checked *n* ago" line above the mailbox is there so a quiet morning is
distinguishable from a stuck page.

## When mail does not arrive

Work down this list. It is ordered so the invisible failures come first — the
ones where the app cannot tell you anything, because nothing reached it.

1. **`/app/email` → Receive.** Any receipt at all for the message? A receipt
   means it got here and the rest of this list does not apply: read the
   outcome. `bad_signature` is the Worker's `WEBHOOK_SECRET` differing from the
   backend's; `unresolved` means it arrived and could not be placed, which is
   working as designed and still visible.
2. **No receipt at all → it never reached the app.** Everything below is
   upstream, in Cloudflare, and none of it can leave a row here.
3. **The Worker log.** `Ignored mail to ...` means the recipient filter
   dropped it, and the line names what it would have had to start with.
   `Inbound: ...` means it was parsed and posted; a `CRM rejected payload` or
   `DELIVERY FAILED` line after it names the status the backend returned.
4. **No Worker log line at all → Email Routing never called it.** The MX
   records, the catch-all rule, or the address is not routed to this Worker.
5. **Open the Worker's URL in a browser.** It reports whether `WEBHOOK_URL`
   and `WEBHOOK_SECRET` are bound, and which recipients it accepts. It never
   prints their values. (Before there was a `fetch` handler this answered
   `No fetch handler!`, which reads like a broken deploy and is not one —
   Email Routing calls `email()`, never `fetch()`.)
6. **`WEBHOOK_URL` must be the public origin.** The Worker runs on
   Cloudflare's edge and cannot reach a private address.

## Writing one

**Write** on the mailbox, or **Reply** on any message. The address is put
through the same matcher everything else uses: a buyer on file gets the send on
their timeline with a reply route home, and an address belonging to nobody is
sent to anyway — the line under the field says which, before you press send.
Replies carry `In-Reply-To`, so they land under the original in the recipient's
client rather than starting a second thread.

Nothing stores a draft. It lives in the browser until Send.

## How a reply finds its buyer

Every outbound send mints a `reply_token` and carries it in `Reply-To`. The
Worker forwards the envelope recipient verbatim, and the backend resolves:

1. `reply+<token>@` → the send → its lead. Tokens are lowercase alphanumeric so
   a mail server rewriting the local part's case cannot break the lookup, and
   the match is case-insensitive anyway. The token is also read off the payload
   (`replyToken`, or `conversationId` — an earlier Worker's name for the same
   field) and folded into the address, so a Worker that already extracted it
   needs no edit. A hint that disagrees with the address loses; the address is
   what the mail server actually delivered to.
2. `In-Reply-To` against a stored provider message id. **This rung rarely fires
   with Resend.** Resend runs on SES, so the id a client quotes back is
   `<...@email.amazonses.com>` while `provider_message_id` holds the UUID
   Resend's API returned — two different identifiers for one message. Rungs 1
   and 3 carry the load; this one is here for a provider that returns the id it
   actually put in the header.
3. The From address through the shared lead matcher — email exact, phone by its
   last ten digits. **A name is never part of it**, so a stranger stays a
   stranger rather than being filed under whoever shares one.
4. Otherwise **stored unresolved**, never dropped. Someone really wrote in.

A reply also arrives with the entire message it is answering quoted underneath
it. Only the buyer's own words are stored on the timeline — the untrimmed body
stays on the receipt, so a quote marker that ever fires wrongly costs
presentation and not the message.

Liner does not answer email. A reply lands as an activity on the buyer's
timeline and reopens an escalation a rep had already claimed — a buyer
answering the question a rep asked is the rep's turn again.

## Status

The Worker source here matches what is deployed, but nothing in this repository
can run or verify it. The endpoint it posts to **is** verified: `make smoke`
drives both auth schemes, both paths, the dedupe, every rung of the resolution
ladder, the reopen, and the deployed Worker's payload field for field.

One deliberate gap on the Worker side: a delivery that fails all three attempts
is logged and dropped rather than bounced with `message.setReject()`. Rejecting
is the honest failure — the buyer learns their reply did not arrive — but it
turns one bad deploy into a wave of bounces at real prospects. The cost of the
choice is that the only evidence is a `DELIVERY FAILED` line in
`wrangler tail`, so that line is the alarm.
