import PostalMime from "postal-mime";

/**
 * Cloudflare Email Worker -> Liner inbound webhook.
 *
 * PLACEHOLDER(cloudflare-worker): this file is the deployed Worker's source as
 * it should be, but nothing in this repository can run or verify it -- no
 * Worker runtime, no domain accepting mail. The endpoint it posts to *is*
 * verified: the auth check, the dedupe and the whole resolution ladder are
 * driven offline by `make smoke`. When mail stops arriving, read the receipts
 * on /app/email before suspecting the backend; they record deliveries that
 * arrived and were refused, which is the case that is otherwise invisible.
 */

interface Env {
	WEBHOOK_URL: string;
	WEBHOOK_SECRET: string;
}

/**
 * Hyphen and uppercase are both in here on purpose. Liner mints lowercase
 * alphanumeric tokens, but a mail server is entitled to rewrite the local
 * part, and an address that was forwarded may carry an older token. A regex
 * that is stricter than the addresses it meets fails silently: the reply still
 * arrives, `conversationId` is just null, and it lands by the weakest matching
 * rule instead of the exact one.
 */
const REPLY_RE = /reply\+([A-Za-z0-9_-]{6,64})@/i;

export default {
	async email(message: ForwardableEmailMessage, env: Env, ctx: ExecutionContext): Promise<void> {
		const parser = new PostalMime();
		const rawEmail = new Response(message.raw);
		const parsed = await parser.parse(await rawEmail.arrayBuffer());

		// message.to is the envelope recipient, which is what carries
		// reply+<token>. parsed.to is the header, and a forward rewrites it.
		const match = message.to.match(REPLY_RE);

		const payload = {
			messageId: parsed.messageId ?? "",
			from: message.from,
			to: message.to,
			replyToken: match ? match[1] : null,
			subject: parsed.subject ?? "",
			fromAddress: parsed.from?.address ?? "",
			fromName: parsed.from?.name ?? "",
			text: parsed.text ?? "",
			html: parsed.html ?? "",
			inReplyTo: parsed.inReplyTo ?? "",
			date: parsed.date ?? null,
			attachments: parsed.attachments?.map((a) => ({
				filename: a.filename,
				mimeType: a.mimeType,
				size: a.content?.byteLength ?? 0,
			})),
		};

		const res = await fetch(env.WEBHOOK_URL, {
			method: "POST",
			headers: {
				"Content-Type": "application/json",
				"X-Webhook-Secret": env.WEBHOOK_SECRET,
			},
			body: JSON.stringify(payload),
		});

		if (!res.ok) {
			// Rejecting tells the sender it did not arrive, which is true.
			// Cloudflare does not retry a Worker exception for email, so
			// swallowing this is how a buyer's reply disappears with nobody the
			// wiser -- the one outcome worse than a bounce.
			const detail = await res.text();
			console.error(`liner inbound ${res.status}: ${detail.slice(0, 300)}`);
			message.setReject(`Could not deliver to the CRM (${res.status})`);
		}
	},
} satisfies ExportedHandler<Env>;
