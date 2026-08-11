/**
 * Cloudflare Email Worker -> Liner inbound webhook.
 *
 * PLACEHOLDER(cloudflare-worker): this has never been deployed or executed.
 * Nothing in the development environment can accept mail on a real domain or
 * run a Worker, so the code below is written from the documented API and is
 * unverified. What *is* verified is the endpoint it posts to: the signature
 * check, the dedupe and the whole resolution ladder are exercised offline by
 * `make smoke`. Treat a failure here as a Worker problem, not a Liner one --
 * and check the receipts on /app/email first, because they record deliveries
 * that arrived and were refused.
 *
 * Deploy: see README.md in this directory.
 */

import PostalMime from 'postal-mime'

/** Same construction as the Python side: hex HMAC-SHA256 over the exact bytes
 *  posted. Signing a re-serialised object instead of the bytes sent is the
 *  classic way this breaks -- the two sides stringify differently and every
 *  delivery 401s. */
async function sign(secret, body) {
  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  )
  const mac = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(body))
  return [...new Uint8Array(mac)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

export default {
  async email(message, env, ctx) {
    const parsed = await PostalMime.parse(message.raw)

    const payload = {
      messageId: parsed.messageId || message.headers.get('message-id') || '',
      from: parsed.from?.address || message.from || '',
      // message.to is the envelope recipient, which is the one carrying
      // reply+<token>. parsed.to is the header, which a forward rewrites.
      to: message.to || parsed.to?.[0]?.address || '',
      subject: parsed.subject || '',
      text: parsed.text || '',
      html: parsed.html || '',
      inReplyTo: parsed.inReplyTo || message.headers.get('in-reply-to') || '',
      receivedAt: new Date().toISOString(),
    }

    const body = JSON.stringify(payload)
    const signature = await sign(env.WEBHOOK_SECRET, body)

    const response = await fetch(env.CRM_WEBHOOK_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Liner-Signature': signature,
      },
      body,
    })

    if (!response.ok) {
      // Rejecting the message tells the sender it did not arrive, which is
      // true and better than accepting mail into a void. Cloudflare will not
      // retry a Worker exception for email, so silently swallowing this is how
      // a buyer's reply disappears with nobody the wiser.
      const detail = await response.text()
      console.error(`liner inbound ${response.status}: ${detail.slice(0, 300)}`)
      message.setReject(`Could not deliver to the CRM (${response.status})`)
    }
  },
}
