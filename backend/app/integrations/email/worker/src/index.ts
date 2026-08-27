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
	/** Comma-separated local parts to accept, overriding the default below. */
	ALLOWED_RECIPIENTS?: string;
}

/**
 * Addresses we actually accept. The catch-all sweeps up spam to random
 * addresses, so this filters before hitting the CRM.
 *
 * **Everything the product publishes has to be in here, and `founder@` was
 * not.** The landing page offers founder@ as the way to reach a person
 * directly, and founder@/cto@ are the two ops identities that answer from
 * /ops -- so mail to the one address we tell people to write to was dropped
 * here, with a console.log and nothing else. No receipt, no row, no error:
 * from the app's side it is indistinguishable from nobody having written.
 * That is the exact failure the receipts table exists to make visible, and
 * this filter sits upstream of it.
 *
 * Overridable with ALLOWED_RECIPIENTS so a third person is a `wrangler
 * secret`/var away rather than a code change and a redeploy.
 */
const DEFAULT_PREFIXES = [
	"support@",
	"sales@",
	"founder@",
	"cto@",
	"reply+",
];

function allowedPrefixes(env: Env): string[] {
	const configured = (env.ALLOWED_RECIPIENTS || "")
		.split(",")
		.map((p) => p.trim().toLowerCase())
		.filter(Boolean);
	return configured.length ? configured : DEFAULT_PREFIXES;
}

function isAcceptedRecipient(to: string, env: Env): boolean {
	const addr = (to || "").toLowerCase();
	return allowedPrefixes(env).some((p) => addr.startsWith(p));
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
	/**
	 * Nothing routes mail here, so this is not how a message arrives -- but a
	 * Worker with no fetch handler answers a browser with "No fetch handler!"
	 * in the Cloudflare log, which reads as a broken deploy and is not one.
	 * Email Routing calls `email()` below; `fetch` exists so opening the URL
	 * says that rather than erroring.
	 *
	 * It reports whether the two bindings are *present*, never their values:
	 * this URL is public, and WEBHOOK_SECRET is the only thing standing in
	 * front of an endpoint that writes into a buyer's history.
	 */
	async fetch(request: Request, env: Env): Promise<Response> {
		const lines = [
			"Liner inbound email worker.",
			"",
			"This is an Email Worker. Mail arrives through Cloudflare Email",
			"Routing, which calls the email() handler -- not this one, and not",
			"by visiting this URL.",
			"",
			`WEBHOOK_URL:    ${env.WEBHOOK_URL ? "set" : "MISSING"}`,
			`WEBHOOK_SECRET: ${env.WEBHOOK_SECRET ? "set" : "MISSING"}`,
			`accepting:      ${allowedPrefixes(env).join(", ")}`,
			"",
			"A message dropped here leaves no trace in the app at all. Check the",
			"Worker log for 'Ignored mail to ...' before suspecting the backend.",
		];
		return new Response(lines.join("\n") + "\n", {
			headers: { "Content-Type": "text/plain; charset=utf-8" },
		});
	},

	async email(
		message: ForwardableEmailMessage,
		env: Env,
		ctx: ExecutionContext
	): Promise<void> {
		if (!isAcceptedRecipient(message.to, env)) {
			// The one line that explains a missing email, so it names what
			// would have had to be true instead of just saying no.
			console.log(
				`Ignored mail to ${message.to} -- not one of ${allowedPrefixes(env).join(", ")}`
			);
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
