import PostalMime from "postal-mime";

/**
 * Cloudflare Email Worker -> Liner inbound webhook.
 *
 * PLACEHOLDER(cloudflare-worker): this is the deployed Worker's source, kept
 * here so the payload the backend parses can be read next to the code that
 * builds it. Nothing in this repository can run or verify it -- no Worker
 * runtime, no domain accepting mail. The endpoint it posts to *is* verified:
 * the auth check, the dedupe and the whole resolution ladder are driven
 * offline by `make smoke`, against this exact payload shape. When mail stops
 * arriving, read the receipts on /app/email before suspecting the backend;
 * they record deliveries that arrived and were refused, which is the case that
 * is otherwise invisible.
 */

interface Env {
	WEBHOOK_URL: string;
	WEBHOOK_SECRET: string;
}

// Addresses we actually accept. Catch-all sweeps up spam to random
// addresses, so filter before hitting the CRM.
const ALLOWED_PREFIXES = ["support@", "sales@", "reply+"];

function isAcceptedRecipient(to: string): boolean {
	const addr = to.toLowerCase();
	return ALLOWED_PREFIXES.some((p) => addr.startsWith(p));
}

/**
 * Retrying is safe because the backend is idempotent: it dedupes on the
 * message id, and on a digest of these exact bytes when the mail carries no
 * Message-ID header. A replayed attempt is recorded as `duplicate` rather than
 * filed twice -- which is why the same serialised `body` must be re-sent each
 * time rather than rebuilt.
 */
async function postWithRetry(
	url: string,
	body: string,
	secret: string,
	attempts = 3
): Promise<boolean> {
	for (let i = 0; i < attempts; i++) {
		try {
			const res = await fetch(url, {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
					"X-Webhook-Secret": secret,
				},
				body,
			});

			if (res.ok) return true;

			// 4xx means our payload is wrong — retrying won't help
			if (res.status >= 400 && res.status < 500) {
				console.error(`CRM rejected payload: ${res.status}`);
				return false;
			}

			console.warn(`CRM error ${res.status}, attempt ${i + 1}/${attempts}`);
		} catch (err) {
			console.warn(`Network error, attempt ${i + 1}/${attempts}`);
		}

		// backoff: 1s, 2s, 4s — skip the wait after the last attempt
		if (i < attempts - 1) {
			await new Promise((r) => setTimeout(r, 2 ** i * 1000));
		}
	}
	return false;
}

export default {
	async email(
		message: ForwardableEmailMessage,
		env: Env,
		ctx: ExecutionContext
	): Promise<void> {
		if (!isAcceptedRecipient(message.to)) {
			console.log(`Ignored mail to ${message.to}`);
			return;
		}

		const parser = new PostalMime();
		const rawEmail = new Response(message.raw);
		const parsed = await parser.parse(await rawEmail.arrayBuffer());

		// message.to is the envelope recipient, which is what carries
		// reply+<token>. parsed.to is the header, and a forward rewrites it.
		const match = message.to.match(/^reply\+([a-z0-9_-]+)@/i);

		const payload = {
			messageId: parsed.messageId,
			from: message.from,
			to: message.to,
			conversationId: match ? match[1] : null,
			subject: parsed.subject,
			fromAddress: parsed.from?.address ?? "",
			fromName: parsed.from?.name ?? "",
			text: parsed.text,
			html: parsed.html,
			inReplyTo: parsed.inReplyTo ?? null,
			references: parsed.references ?? null,
			date: parsed.date,
			// Stamped once, before the retry loop, so every attempt sends
			// byte-identical JSON. That is what lets the backend recognise a
			// retry of a message with no Message-ID header.
			receivedAt: new Date().toISOString(),
			attachments: parsed.attachments?.map((a) => ({
				filename: a.filename,
				mimeType: a.mimeType,
				size: a.content?.byteLength ?? 0,
			})) ?? [],
		};

		// Metadata only — don't log message bodies in production
		console.log(
			`Inbound: ${parsed.messageId} from ${message.from} to ${message.to}`
		);

		const delivered = await postWithRetry(
			env.WEBHOOK_URL,
			JSON.stringify(payload),
			env.WEBHOOK_SECRET
		);

		if (!delivered) {
			// Logged, not rejected. An earlier version called
			// `message.setReject()` here, which bounced the mail back to the
			// buyer -- honest, but it turns one bad deploy into a wave of
			// bounces at real prospects. The cost of this choice is that a
			// permanently failed delivery is silent to the sender and visible
			// only in `wrangler tail`, so this line is the alarm.
			console.error(
				`DELIVERY FAILED — messageId=${parsed.messageId} from=${message.from}`
			);
		}
	},
} satisfies ExportedHandler<Env>;
