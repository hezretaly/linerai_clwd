/**
 * Cloudflare Email Worker -> Liner inbound webhook.
 *
 * PLACEHOLDER(cloudflare-worker): this is the deployed Worker's source, kept
 * here so what the backend receives can be read next to the code that sends
 * it. Nothing in this repository can run or verify it -- no Worker runtime,
 * no domain accepting mail. The endpoint it posts to *is* verified: the auth
 * check, the dedupe, the parse and the whole resolution ladder are driven
 * offline by `make smoke`. When mail stops arriving, read the receipts on
 * /app/email before suspecting the backend; they record deliveries that
 * arrived and were refused, which is the case that is otherwise invisible.
 *
 * **It forwards the message; it does not read it.** An earlier version parsed
 * every message here with postal-mime and posted a JSON digest -- one address
 * for the sender, none for Cc, and a list of file *names* whose bytes never
 * left Cloudflare. Parsing also costs CPU, and the free plan gives a Worker
 * ten milliseconds of it. So now the raw bytes go to the backend exactly as
 * the mail server delivered them, with the SMTP envelope beside them in two
 * headers, and the backend -- which has the time and keeps the original on
 * disk -- does the reading. Nothing here looks inside the message at all.
 */

interface Env {
	/** The JSON intake the previous Worker posted to. Kept so an older
	 *  deployment's configuration still reads; this Worker derives the raw
	 *  URL from it when WEBHOOK_RAW_URL is not set. */
	WEBHOOK_URL: string;
	/** Where the raw message goes: `.../api/emails/inbound/raw`. */
	WEBHOOK_RAW_URL?: string;
	WEBHOOK_SECRET: string;
	/** Comma-separated local parts to accept, overriding the default below. */
	ALLOWED_RECIPIENTS?: string;
}

/**
 * The largest message the backend accepts, whole. Kept equal to `MAX_RAW` in
 * `backend/app/api/inbound_email.py`. Cloudflare itself refuses anything over
 * 25 MiB before this runs, so today this is headroom rather than a limit
 * anyone meets -- it is here so the day either number moves, a message too
 * big for the backend is bounced with a reason instead of posted and lost.
 */
const MAX_RAW = 30 * 1024 * 1024;

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
 *
 * **The var WINS over this list when it is set, and wrangler.jsonc sets it.**
 * So editing the constant on a deployment whose var is set changes nothing:
 * the mail is still dropped, still with a console.log and still with no
 * receipt on our side, which is the one failure mode that looks exactly like
 * nobody having written. Add a new mailbox to ALLOWED_RECIPIENTS in
 * wrangler.jsonc, not here -- this list is Liner's own addresses, which every
 * deployment publishes, while a dealership's mailbox belongs to one host.
 * `make smoke` pins both halves: every published address of ours is in here,
 * every profile's `mailbox:` is in the var, and the var carries everything
 * this list does so setting it cannot silently drop `founder@`.
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
 * The raw intake: WEBHOOK_RAW_URL when set, otherwise `/raw` on the end of
 * WEBHOOK_URL -- the backend serves both spellings of the JSON path with
 * `/raw` appended, so either base works.
 */
function rawUrl(env: Env): string {
	if (env.WEBHOOK_RAW_URL) return env.WEBHOOK_RAW_URL;
	return (env.WEBHOOK_URL || "").replace(/\/+$/, "") + "/raw";
}

type Outcome =
	| { delivered: true }
	| { delivered: false; status: number; permanent: boolean };

/**
 * Retrying is safe because the backend is idempotent: it dedupes on the
 * message's own Message-ID, and on a digest of these exact bytes when the
 * mail carries none. A replayed attempt is recorded as `duplicate` rather than
 * filed twice -- which is why the same `body` is re-sent each time and never
 * rebuilt. An ArrayBuffer is not a stream, so it can be posted more than once.
 */
async function postWithRetry(
	url: string,
	body: ArrayBuffer,
	headers: Record<string, string>,
	attempts = 3
): Promise<Outcome> {
	let status = 0;
	for (let i = 0; i < attempts; i++) {
		try {
			const res = await fetch(url, { method: "POST", headers, body });
			if (res.ok) return { delivered: true };
			status = res.status;

			// 4xx means the request itself is wrong -- a secret that does not
			// match, a message too large -- and retrying will not change that.
			if (res.status >= 400 && res.status < 500) {
				console.error(`CRM rejected the message: ${res.status}`);
				return { delivered: false, status, permanent: true };
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
	return { delivered: false, status, permanent: false };
}

/** Why a message is being bounced, in words the sender reads. */
function tooLarge(size: number): string {
	const mb = (n: number) => Math.ceil(n / (1024 * 1024));
	return (
		`This message is ${mb(size)} MB; this mailbox accepts up to ${mb(MAX_RAW)} MB. ` +
		"Please send the files in separate messages or share a link to them."
	);
}

export default {
	/**
	 * Nothing routes mail here, so this is not how a message arrives -- but a
	 * Worker with no fetch handler answers a browser with "No fetch handler!"
	 * in the Cloudflare log, which reads as a broken deploy and is not one.
	 * Email Routing calls `email()` below; `fetch` exists so opening the URL
	 * says that rather than erroring.
	 *
	 * It reports whether the bindings are *present*, never their values:
	 * this URL is public, and WEBHOOK_SECRET is the only thing standing in
	 * front of an endpoint that writes into a buyer's history.
	 */
	async fetch(request: Request, env: Env): Promise<Response> {
		const lines = [
			"Liner inbound email worker (raw forwarding).",
			"",
			"This is an Email Worker. Mail arrives through Cloudflare Email",
			"Routing, which calls the email() handler -- not this one, and not",
			"by visiting this URL.",
			"",
			`WEBHOOK_URL:     ${env.WEBHOOK_URL ? "set" : "MISSING"}`,
			`WEBHOOK_RAW_URL: ${env.WEBHOOK_RAW_URL ? "set" : env.WEBHOOK_URL ? "derived (WEBHOOK_URL + /raw)" : "MISSING"}`,
			`WEBHOOK_SECRET:  ${env.WEBHOOK_SECRET ? "set" : "MISSING"}`,
			`accepting:       ${allowedPrefixes(env).join(", ")}`,
			`largest message: ${Math.round(MAX_RAW / (1024 * 1024))} MB`,
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

		// Read from the headers Cloudflare already parsed, for the log line
		// only. The body is never opened here.
		const messageId = message.headers.get("message-id") || "(no Message-ID)";

		// **Too big is the sender's to know about, so it is bounced.** Unlike
		// a failed delivery below, this is a fact about the message rather
		// than about our deploy: every retry would fail the same way, and a
		// sender whose attachment silently vanished will assume it arrived.
		if (message.rawSize > MAX_RAW) {
			console.warn(
				`Rejected ${messageId} to ${message.to}: ${message.rawSize} bytes is over ${MAX_RAW}`
			);
			message.setReject(tooLarge(message.rawSize));
			return;
		}

		// Read once, into one buffer, before the first attempt: every retry
		// must post byte-for-byte the same message, because a message with no
		// Message-ID is deduplicated on a digest of exactly these bytes.
		const raw = await new Response(message.raw).arrayBuffer();

		// Metadata only — don't log message bodies in production
		console.log(
			`Inbound: ${messageId} from ${message.from} to ${message.to} (${raw.byteLength} bytes)`
		);

		const outcome = await postWithRetry(rawUrl(env), raw, {
			"Content-Type": "message/rfc822",
			// The SMTP envelope is not in the message. `to` is the recipient
			// the mail server delivered to -- the one carrying reply+<token>
			// and deciding which dealership's store this is -- and `from` is
			// the route it came by, kept for diagnostics.
			"X-Envelope-From": message.from,
			"X-Envelope-To": message.to,
			"X-Webhook-Secret": env.WEBHOOK_SECRET,
		});

		if (outcome.delivered) return;

		if (outcome.status === 413) {
			// The backend's limit is lower than this Worker thought. Same
			// reasoning as the size check above: the message itself is the
			// problem, so its sender is told rather than left to assume.
			message.setReject(tooLarge(raw.byteLength));
			return;
		}

		// Logged, not rejected. An earlier version called
		// `message.setReject()` on every failure, which bounced the mail back
		// to the buyer -- honest, but it turns one bad deploy (a rotated
		// secret, a backend that is down) into a wave of bounces at real
		// prospects. The cost of this choice is that a permanently failed
		// delivery is silent to the sender and visible only in `wrangler
		// tail`, so this line is the alarm.
		console.error(
			`DELIVERY FAILED — messageId=${messageId} from=${message.from} status=${outcome.status}`
		);
	},
} satisfies ExportedHandler<Env>;
