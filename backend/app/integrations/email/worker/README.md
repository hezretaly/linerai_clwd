# Inbound email: Cloudflare Email Routing → Worker → Liner

Receiving is configured outside this application, which is exactly why it
fails quietly. A deployment can send perfectly and drop every reply, and
nothing in the app would look wrong. `/app/email` exists to make that visible:
it shows every delivery the endpoint was handed, **including the ones it
refused**.

**Status: unverified.** No part of this directory has been deployed or
executed. The endpoint it posts to is verified — signature check, dedupe and
the whole resolution ladder are exercised offline by `make smoke`.

## What has to be true

1. **Email Routing enabled** on the sending domain, with Cloudflare's MX and
   SPF records live. Use the same domain as `SENDING_DOMAIN` in the backend:
   outbound builds `Reply-To: reply+<token>@SENDING_DOMAIN`, and if the routed
   domain differs, every reply bounces and the app cannot tell.
2. **Rules** — `support@` and `sales@` to this Worker, plus a **catch-all** to
   the same Worker. The catch-all is not optional: `reply+<token>@` addresses
   are minted per send and cannot be enumerated as rules.
3. **Secrets** — `wrangler secret put CRM_WEBHOOK_URL` (the public
   `https://…/api/inbound-email`) and `wrangler secret put WEBHOOK_SECRET`,
   matching the backend's.

```
npm i postal-mime
wrangler deploy
```

## Checking it

`/app/email` in the dashboard, or:

```
curl -s https://your-host/api/integrations | jq '.integrations[] | select(.key=="inbound_email")'
```

The setup page can post a signed sample through the live endpoint, which
exercises everything except Cloudflare itself. If that works and real mail
does not, the problem is in the three points above rather than in the app.

## How a reply finds its buyer

Every outbound send mints a `reply_token` and carries it in `Reply-To`. A
reply arrives on the catch-all, the Worker forwards the envelope recipient
verbatim, and the backend resolves in this order:

1. `reply+<token>@` → the send → its lead;
2. `In-Reply-To` matched against a stored provider message id;
3. the From address through the shared lead matcher (email exact, phone by its
   last ten digits — a name is never part of it);
4. otherwise **stored unresolved**, never dropped. Someone really wrote in.

Liner does not answer email. A reply lands as an activity on the buyer's
timeline, and re-opens an escalation a rep had already claimed — a buyer
answering the question a rep asked is the rep's turn again.
