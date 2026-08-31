import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'

import { api, ApiError } from '../lib/api'
import { dateTime, relative } from '../lib/format'
import type { IntegrationsPayload } from '../lib/types'
import { Badge, Button, Card, Empty, Field, Input, Sheet, Spinner } from '../components/ui'
import { Icon } from '../components/Icon'
import { PageIntro } from '../components/dashboard/AppShell'

/* Every email this dealership has sent or received, and below it the tools to
 * work out why one did not arrive.
 *
 * The mailbox is first because it is the daily thing. The diagnostics are
 * second but not optional: sending and receiving fail in different places for
 * different reasons, and only one of them is loud. Outbound breaks at Resend
 * -- a bad key, an unverified domain -- and the next send says so. Inbound
 * breaks in Cloudflare, configured outside this app entirely, and breaks
 * *silently*: no error, no row, just replies that never arrive.
 *
 * There is no Drafts tab. Nothing on the server stores a draft -- the composer
 * below keeps one in the browser and nowhere else -- and a tab that is always
 * empty claims a feature that does not exist.
 *
 * Two things keep the list current, and they are not redundant. The socket
 * carries `email.received` the instant a delivery is filed, which is the
 * refresh that matters: mail arriving is the only thing on this dashboard
 * nobody clicked for. The poll below is the backstop for when the socket is
 * not there to carry it -- a dropped connection, a laptop that slept -- and it
 * is deliberately slow, because it is covering an outage rather than doing the
 * work.
 */

/** Five minutes. Fast enough that a missed socket message is a wait and not a
 *  dead page, slow enough to be free. The socket is what makes it feel live. */
const POLL_MS = 5 * 60 * 1000

/** Matches the server's default page. One screenful and then some. */
const PAGE = 100

/** `relative()` takes an ISO string; TanStack hands back an epoch. Same idea,
 *  different input, and 0 means nothing has been fetched yet rather than 1970. */
function sinceChecked(at: number): string {
  if (!at) return 'never'
  const minutes = Math.round((Date.now() - at) / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  return `${Math.round(minutes / 60)}h ago`
}

interface Receipt {
  id: string
  outcome: string
  message_id: string
  from_address: string
  to_address: string
  subject: string
  matched_by: string
  lead_id: string | null
  detail: string
  created_at: string
}

interface ReceiptsPayload {
  receipts: Receipt[]
  reply_domain: string
  endpoint: string
  signature_header: string
}

interface Mail {
  id: string
  kind: 'message' | 'unmatched'
  direction: 'in' | 'out'
  address: string
  subject: string
  body: string
  status: string
  error: string
  delivered_externally: boolean
  lead_id: string | null
  lead_name: string
  at: string
}

type Box = 'all' | 'received' | 'sent' | 'failed' | 'unmatched'

interface Mailbox {
  messages: Mail[]
  counts: Record<Box, number>
  /** How many match the current tab and search, which is not the same as how
   *  many were returned. The tab counting every row while the list showed the
   *  first two hundred is exactly the "says 12, shows 9" bug the one filter
   *  definition exists to prevent. */
  matching: number
  offset: number
  has_more: boolean
}

/** One correspondent, and how far the exchange with them has got.
 *
 *  Everything here is computed server-side in `app/email_threads.py`, which is
 *  where the counter lives. Recomputing "what counts as a back and forth" in
 *  the browser is how a header ends up saying 3 over a row that reads as 2. */
interface Thread {
  key: string
  kind: 'buyer' | 'stranger'
  lead_id: string | null
  name: string
  address: string
  subject?: string
  last_subject: string
  last_body: string
  last_direction: string
  exchanges: number
  inbound: number
  outbound: number
  waiting: boolean
  graduated: boolean
  at: string | null
}

/** Whether Liner is answering email, and every reason it might not be.
 *
 *  Three separate facts rather than one boolean, because they are fixed in
 *  three different places: `.env` needs a restart, the switch takes effect on
 *  the next delivery, and the model is a fourth variable entirely. Collapsed
 *  into "off" it sends whoever is reading to edit the wrong one -- which is
 *  exactly what happened: a deployment with `EMAIL_AGENT=true` and
 *  `LLM_MODE=live` set never replied, because the switch this card throws had
 *  no control anywhere on the dashboard and defaults off. */
interface AgentState {
  on: boolean
  reason: string
  detail: string
  allowed_by_env: boolean
  flag: string
  live_model: boolean
  cooldown_minutes: number
  hourly_ceiling: number
  declined: { id: string; from_address: string; subject: string; detail: string; at: string }[]
  waiting: { id: string; lead_id: string | null; due_at: string; created_at: string }[]
  recent: { id: string; lead_id: string | null; state: string; detail: string; at: string }[]
  flags: { key: string; value: string; reason: string; updated_at: string }[]
}

type ThreadBox = 'open' | 'waiting' | 'graduated' | 'strangers' | 'all'

/** Open first, because it is the working list: everyone this dealership is
 *  mid-conversation with who has not yet become one. */
const THREAD_BOXES: [ThreadBox, string][] = [
  ['open', 'Open'],
  ['waiting', 'Waiting on us'],
  ['graduated', 'Conversations'],
  ['strangers', 'No buyer'],
  ['all', 'Everyone'],
]

const BOXES: [Box, string][] = [
  ['all', 'All'],
  ['received', 'Received'],
  ['sent', 'Sent'],
  ['failed', 'Not sent'],
  ['unmatched', 'No buyer'],
]

/** What the composer holds while it is open, and nothing else holds ever.
 *
 *  There is no draft row behind this. It lives in the browser until Send is
 *  pressed, which is why closing the sheet asks first -- a confirm is cheap,
 *  and a schema for saved drafts is a decision nobody has taken. */
interface Compose {
  to: string
  subject: string
  body: string
  /** Set when replying to mail from a buyer already on file, so the send is
   *  filed against them even if they wrote from an address the matcher has
   *  never seen. */
  lead_id?: string
  lead_name?: string
  /** The message being answered, so the buyer's client threads the reply
   *  under it instead of starting a second conversation in their inbox. */
  in_reply_to_outreach_id?: string
}

interface Recipient {
  lead_id: string
  name: string
  email: string
}

interface Send {
  id: string
  reply_token: string
  subject: string
  to_address: string
  lead_id: string
  lead_name: string
  created_at: string
}

const OUTCOME_TONE: Record<string, string> = {
  accepted: 'border-success/30 bg-success/10 text-success',
  duplicate: 'border-border text-muted-foreground',
  unresolved: 'border-warning/30 bg-warning/10 text-warning',
  // The only two that mean something is actually broken.
  bad_signature: 'border-destructive/30 bg-destructive/10 text-destructive',
  malformed: 'border-destructive/30 bg-destructive/10 text-destructive',
}

export function EmailSetupPage() {
  const queryClient = useQueryClient()
  const [to, setTo] = useState('')
  const [target, setTarget] = useState('')
  const [sendResult, setSendResult] = useState<string | null>(null)
  const [box, setBox] = useState<Box>('all')
  const [people, setPeople] = useState<ThreadBox>('open')
  const [query, setQuery] = useState('')
  const [openSetup, setOpenSetup] = useState(false)
  const [reading, setReading] = useState<Mail | null>(null)
  const [composing, setComposing] = useState<Compose | null>(null)
  // How far down the list goes. Grown rather than paged, because a mailbox is
  // read by scrolling -- page two of an inbox is somewhere nobody goes back to.
  const [shown, setShown] = useState(PAGE)

  // The clock above the list counts up between fetches, so something has to
  // re-render it. Thirty seconds is finer than the label's own resolution.
  const [, tick] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => tick((n) => n + 1), 30_000)
    return () => clearInterval(timer)
  }, [])

  const { data: mail, dataUpdatedAt, isFetching } = useQuery({
    queryKey: ['email-messages', box, query, shown],
    queryFn: () => api.get<Mailbox>(
      `/api/email/messages?box=${box}&q=${encodeURIComponent(query)}&limit=${shown}`,
    ),
    refetchInterval: POLL_MS,
    // A tab left open all morning is the case this page is for. Coming back to
    // it should not show yesterday's mailbox while the timer runs down.
    refetchOnWindowFocus: true,
  })

  const { data: threads } = useQuery({
    queryKey: ['email-threads', people],
    queryFn: () => api.get<{
      threads: Thread[]
      counts: Record<ThreadBox, number>
      threshold: number
    }>(`/api/email/threads?box=${people}`),
    refetchInterval: POLL_MS,
    refetchOnWindowFocus: true,
  })

  // Polled on the same clock as the mailbox, because what it reports moves on
  // its own: a queued reply comes due, and the hourly ceiling can throw the
  // switch with nobody touching it.
  const { data: agent } = useQuery({
    queryKey: ['email-agent'],
    queryFn: () => api.get<AgentState>('/api/email/agent'),
    refetchInterval: POLL_MS,
    refetchOnWindowFocus: true,
  })

  const toggleAgent = useMutation({
    mutationFn: (value: 'on' | 'off') => api.post<AgentState>('/api/email/agent', { value }),
    onSuccess: (state) => queryClient.setQueryData(['email-agent'], state),
  })

  const { data: integrations } = useQuery({
    queryKey: ['integrations'],
    queryFn: () => api.get<IntegrationsPayload>('/api/integrations'),
  })
  const { data, isLoading } = useQuery({
    queryKey: ['email-receipts'],
    queryFn: () => api.get<ReceiptsPayload>('/api/email/receipts'),
    refetchInterval: POLL_MS,
    refetchOnWindowFocus: true,
  })
  const { data: sends } = useQuery({
    queryKey: ['email-replyable'],
    queryFn: () => api.get<{ sends: Send[] }>('/api/email/replyable'),
  })

  useEffect(() => setShown(PAGE), [box, query])

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['email-receipts'] })
    void queryClient.invalidateQueries({ queryKey: ['email-messages'] })
    void queryClient.invalidateQueries({ queryKey: ['timeline'] })
  }

  const sendTest = useMutation({
    mutationFn: () => api.post<{ status: string; error: string; provider: string }>(
      '/api/email/test-send', { to },
    ),
    onSuccess: (r) => {
      // Verbatim, including the failure. This is the screen that answers "why
      // did nothing arrive?", and a summarised error sends you looking in the
      // wrong place.
      setSendResult(
        r.status === 'sent'
          ? `Accepted by ${r.provider}. Delivery is not confirmed -- there is no delivery webhook yet.`
          : `${r.status}: ${r.error}`,
      )
      refresh()
    },
    onError: (e) => setSendResult((e as ApiError).message),
  })

  const replay = useMutation({
    mutationFn: () => api.post('/api/email/test-inbound', { outreach_id: target }),
    onSuccess: refresh,
  })

  if (isLoading || !data) return <Spinner />

  const email = integrations?.integrations.find((i) => i.key === 'email')
  const inbound = integrations?.integrations.find((i) => i.key === 'inbound_email')
  const chosen = sends?.sends.find((s) => s.id === target)

  return (
    <main className="p-4 md:p-6">
      <PageIntro
        title="Email"
        subtitle="Everything sent and received. Out through Resend, back through Cloudflare."
      />

      {/* ---- the switch ----
          Above the fold and outside the setup disclosure, deliberately. This
          is the control somebody reaches for while the inbox is being
          hammered, and one folded behind "Setup and diagnostics" is one they
          will not find at three in the morning. */}
      <AgentSwitch
        state={agent}
        busy={toggleAgent.isPending}
        onToggle={(value) => toggleAgent.mutate(value)}
      />

      {/* ---- who this dealership is talking to ----
          The list below is messages, which is what you want when hunting a
          particular send. This is people, which is what you want when
          deciding who to answer next -- four messages with one buyer are one
          relationship, not four things to read. It is the same split the
          conversations list makes, for the same reason.

          An exchange is an inbound we answered, counted in
          `app/email_threads.py` and nowhere else. At three it has graduated:
          that buyer is in /app/conversations too, and this is where you see
          why. */}
      <Card className="mb-6 min-w-0">
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
          <div className="flex flex-wrap gap-1.5">
            {THREAD_BOXES.map(([key, label]) => (
              <button
                key={key}
                onClick={() => setPeople(key)}
                className={clsx(
                  'inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors',
                  people === key
                    ? 'border-foreground bg-foreground text-background'
                    : key === 'waiting' && (threads?.counts.waiting ?? 0) > 0
                      ? 'border-primary/30 bg-primary/10 text-primary hover:bg-accent'
                      : 'border-input bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                )}
              >
                {label}
                <span className="tnum opacity-70">{threads?.counts[key] ?? 0}</span>
              </button>
            ))}
          </div>
          <span className="ml-auto shrink-0 text-xs text-muted-foreground">
            a conversation at {threads?.threshold ?? 3} exchanges
          </span>
        </div>

        {!threads?.threads.length ? (
          <Empty
            title="Nobody here"
            hint={
              people === 'waiting'
                ? 'Every buyer who has written has been answered.'
                : 'Mail this dealership exchanges will be listed here, one row per person.'
            }
          />
        ) : (
          <ul className="divide-y divide-border">
            {threads.threads.map((row) => (
              <li key={row.key}>
                <ThreadRow row={row} threshold={threads.threshold} />
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* ---- the mailbox ---- */}
      <Card className="mb-6 min-w-0">
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
          <div className="flex flex-wrap gap-1.5">
            {BOXES.map(([key, label]) => (
              <button
                key={key}
                onClick={() => setBox(key)}
                className={clsx(
                  'inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors',
                  box === key
                    ? 'border-foreground bg-foreground text-background'
                    : key === 'failed' && (mail?.counts.failed ?? 0) > 0
                      ? 'border-destructive/30 bg-destructive/10 text-destructive hover:bg-accent'
                      : 'border-input bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                )}
              >
                {label}
                <span className="tnum opacity-70">{mail?.counts[key] ?? 0}</span>
              </button>
            ))}
          </div>
          <div className="ml-auto flex min-w-0 items-center gap-2">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search address, subject, text..."
              className="w-full sm:w-64"
            />
            <Button
              variant="primary"
              size="sm"
              className="shrink-0"
              onClick={() => setComposing({ to: '', subject: '', body: '' })}
            >
              <Icon name="mail" className="h-3.5 w-3.5 shrink-0" />
              Write
            </Button>
          </div>
        </div>

        {/* A list that refreshes itself and never says so is indistinguishable
            from one that is stuck. The clock is the difference, and the button
            is for whoever does not want to trust it. */}
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-1.5 text-xs text-muted-foreground">
          <span>
            {isFetching ? 'Checking...' : `Checked ${sinceChecked(dataUpdatedAt)}`} · live on
            new mail, and again every 5 minutes
          </span>
          <button
            onClick={refresh}
            className="ml-auto shrink-0 font-medium text-primary hover:underline"
          >
            Check now
          </button>
        </div>

        {!mail ? (
          <Spinner />
        ) : mail.messages.length === 0 ? (
          <p className="p-8 text-center text-sm text-muted-foreground">
            {query ? 'Nothing matches that search.' : 'Nothing here yet.'}
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {mail.messages.map((m) => (
              <li key={m.id}>
                <button
                  onClick={() => setReading(reading?.id === m.id ? null : m)}
                  className="flex w-full flex-wrap items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50"
                >
                  <span
                    className={clsx(
                      'mt-0.5 inline-flex shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium',
                      m.status === 'unmatched'
                        ? 'border-warning/30 bg-warning/10 text-warning'
                        : m.direction === 'in'
                          ? 'border-primary/30 bg-primary/10 text-primary'
                          : m.status !== 'sent'
                            ? 'border-destructive/30 bg-destructive/10 text-destructive'
                            : 'border-border text-muted-foreground',
                    )}
                  >
                    <Icon
                      name={m.direction === 'in' ? 'back' : 'mail'}
                      className="h-3 w-3 shrink-0"
                    />
                    {m.status === 'unmatched'
                      ? 'no buyer'
                      : m.direction === 'in'
                        ? 'received'
                        : m.status === 'sent'
                          ? 'sent'
                          : m.status}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">
                      {m.subject || <span className="text-muted-foreground">(no subject)</span>}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {m.direction === 'in' ? 'from' : 'to'} {m.address || 'unknown'}
                      {m.lead_name && ` · ${m.lead_name}`}
                      {!m.delivered_externally && m.direction === 'out' && m.status === 'sent'
                        && ' · recorded locally, not delivered'}
                    </div>
                    {m.error && (
                      <div className="mt-0.5 text-xs text-destructive">{m.error}</div>
                    )}
                  </div>
                  <span className="tnum shrink-0 whitespace-nowrap text-xs text-muted-foreground">
                    {dateTime(m.at)}
                  </span>
                </button>
                {reading?.id === m.id && (
                  <div className="border-t border-border bg-muted/30 px-4 py-3">
                    <pre className="scroll-thin max-h-64 overflow-auto whitespace-pre-wrap text-xs leading-relaxed">
                      {m.body || '(no body)'}
                    </pre>
                    <div className="mt-2 flex flex-wrap items-center gap-3">
                      <Button
                        size="sm"
                        onClick={() => setComposing({
                          to: m.address,
                          // Not "Re: Re: Re:". A buyer who replies four times
                          // should not end up with a subject line that is
                          // mostly prefix.
                          subject: /^re:/i.test(m.subject) ? m.subject : `Re: ${m.subject}`,
                          body: '',
                          lead_id: m.lead_id ?? undefined,
                          lead_name: m.lead_name || undefined,
                          // Only when answering something that arrived. Our own
                          // send has a provider id the buyer's client never saw.
                          in_reply_to_outreach_id:
                            m.direction === 'in' && m.kind === 'message' ? m.id : undefined,
                        })}
                      >
                        <Icon name="back" className="h-3.5 w-3.5 shrink-0" />
                        Reply
                      </Button>
                    </div>
                    {m.lead_id ? (
                      <Link
                        to={`/app/leads/${m.lead_id}`}
                        className="mt-2 inline-block text-xs text-primary hover:underline"
                      >
                        Open {m.lead_name || 'buyer'}
                      </Link>
                    ) : (
                      // Not an error. Nobody claimed to know who wrote in, which
                      // is the honest answer -- a name is never used to match,
                      // so a stranger stays a stranger.
                      <p className="mt-2 text-xs text-muted-foreground">
                        No buyer on file for {m.address || 'this address'}. Add them from
                        Conversations to give this a home.
                      </p>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        {/* Never a silent cut. A list that stops at a round number without
            saying so reads as a mailbox with nothing older in it. */}
        {mail?.has_more && (
          <div className="border-t border-border p-3 text-center">
            <button
              onClick={() => setShown((n) => n + PAGE)}
              className="text-sm font-medium text-primary hover:underline"
            >
              Show older
            </button>
            <p className="mt-1 text-xs text-muted-foreground">
              Showing {mail.messages.length} of {mail.matching}
            </p>
          </div>
        )}
      </Card>

      {/* ---- setup and diagnostics ---- */}
      <button
        onClick={() => setOpenSetup(!openSetup)}
        className="mb-4 inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline"
      >
        <Icon name={openSetup ? 'back' : 'sliders'} className="h-3.5 w-3.5 shrink-0" />
        {openSetup ? 'Hide setup and diagnostics' : 'Setup and diagnostics'}
      </button>

      {!openSetup ? null : (
      <>
      <div className="mb-6 grid min-w-0 gap-4 lg:grid-cols-2">
        <StatusCard
          title="Sending"
          configured={Boolean(email?.configured)}
          impl={email?.impl ?? '?'}
          detail={email?.detail ?? ''}
          missing={email?.missing ?? []}
        />
        <StatusCard
          title="Receiving"
          configured={Boolean(inbound?.configured)}
          impl={inbound?.impl ?? '?'}
          detail={inbound?.detail ?? ''}
          missing={inbound?.missing ?? []}
        />
      </div>

      <div className="grid min-w-0 gap-6 lg:grid-cols-2">
        {/* ---- outbound ---- */}
        <Card className="min-w-0 p-5">
          <h2 className="text-sm font-semibold">Send a test</h2>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            Goes through the real path, limits included — this page does not
            bypass them, or it would prove the bypass works.
          </p>
          <OutboundScope
            scope={integrations?.outbound_scope ?? ''}
            recipients={integrations?.outbound_recipients}
          />
          <div className="mt-3 space-y-2">
            <Field label="To">
              <Input
                value={to}
                onChange={(e) => setTo(e.target.value)}
                placeholder="you@example.com"
                type="email"
              />
            </Field>
            <Button
              variant="primary"
              size="sm"
              disabled={!to.trim() || sendTest.isPending}
              onClick={() => {
                setSendResult(null)
                sendTest.mutate()
              }}
            >
              {sendTest.isPending ? 'Sending...' : 'Send'}
            </Button>
          </div>
          {sendResult && (
            <pre className="scroll-thin mt-3 max-h-40 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-muted/40 p-2.5 text-xs">
              {sendResult}
            </pre>
          )}
        </Card>

        {/* ---- inbound ---- */}
        <Card className="min-w-0 p-5">
          <h2 className="text-sm font-semibold">Receive a test</h2>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            Posts a signed sample to the live endpoint. Not a simulation: the
            signature check, the dedupe and the whole resolution ladder really
            run. It lands on a real buyer's timeline, so you pick which one.
          </p>
          <div className="mt-3 space-y-2">
            <Field label="Reply to which send">
              <select
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                className="h-9 w-full rounded-md border border-input bg-background px-2.5 text-sm outline-none focus:border-ring focus:ring-1 focus:ring-ring"
              >
                <option value="">Pick a send...</option>
                {sends?.sends.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.lead_name} — {s.subject || '(no subject)'}
                  </option>
                ))}
              </select>
            </Field>
            {chosen && (
              <p className="text-xs text-muted-foreground">
                Arrives as{' '}
                <code>
                  reply+{chosen.reply_token}@{data.reply_domain || '<SENDING_DOMAIN>'}
                </code>{' '}
                and lands on{' '}
                <Link to={`/app/leads/${chosen.lead_id}`} className="text-primary hover:underline">
                  {chosen.lead_name}
                </Link>
                .
              </p>
            )}
            <Button
              size="sm"
              disabled={!target || replay.isPending}
              onClick={() => replay.mutate()}
            >
              {replay.isPending ? 'Posting...' : 'Post a signed reply'}
            </Button>
            {replay.error && (
              <p className="text-xs text-destructive">{(replay.error as ApiError).message}</p>
            )}
          </div>

          <div className="mt-4 border-t border-border pt-3">
            <p className="text-xs font-medium">Or by hand</p>
            <pre className="scroll-thin mt-1.5 overflow-x-auto rounded-md border border-border bg-muted/40 p-2.5 text-[11px] leading-relaxed">
{`BODY='{"messageId":"<test-1>","from":"buyer@example.com",
  "to":"reply+TOKEN@${data.reply_domain || 'your-domain'}","subject":"Re:","text":"hello"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" -r | cut -d' ' -f1)
curl -X POST ${data.endpoint} \\
  -H 'Content-Type: application/json' \\
  -H "${data.signature_header}: $SIG" \\
  -d "$BODY"`}
            </pre>
            <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
              The signature is over the exact bytes posted. Signing a
              re-serialised object instead is the usual reason every delivery
              returns 401.
            </p>
          </div>
        </Card>
      </div>

      {/* ---- receipts ---- */}
      <Card className="mt-6">
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-4">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold">Everything the endpoint was handed</h2>
            <p className="text-xs text-muted-foreground">
              Refusals included. A reply that never arrives looks the same from the
              dashboard whether the secret is wrong, the Cloudflare route was never
              created, or the buyer simply has not written back — this is how you tell.
            </p>
          </div>
          <Button size="sm" className="ml-auto" onClick={refresh}>
            Refresh
          </Button>
        </div>

        {data.receipts.length === 0 ? (
          <p className="p-8 text-center text-sm text-muted-foreground">
            Nothing has reached the endpoint yet.
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {data.receipts.map((r) => (
              <li key={r.id} className="flex flex-wrap items-start gap-3 px-4 py-3">
                <span
                  className={clsx(
                    'inline-flex shrink-0 items-center whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-medium',
                    OUTCOME_TONE[r.outcome] ?? 'border-border text-muted-foreground',
                  )}
                >
                  {r.outcome.replace('_', ' ')}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm">
                    {r.subject || <span className="text-muted-foreground">(no subject)</span>}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">
                    {r.from_address || 'unknown sender'} → {r.to_address || 'unknown recipient'}
                    {r.matched_by && ` · matched by ${r.matched_by.replace('_', ' ')}`}
                  </div>
                  {r.detail && (
                    <div className="mt-0.5 text-xs text-muted-foreground">{r.detail}</div>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {r.lead_id && (
                    <Link
                      to={`/app/leads/${r.lead_id}`}
                      className="text-xs text-primary hover:underline"
                    >
                      Open buyer
                    </Link>
                  )}
                  <span className="tnum whitespace-nowrap text-xs text-muted-foreground">
                    {dateTime(r.created_at)}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
      </>
      )}

      <Composer
        draft={composing}
        onChange={setComposing}
        onClose={() => setComposing(null)}
        onSent={refresh}
        scope={integrations?.outbound_scope ?? ''}
        recipients={integrations?.outbound_recipients}
        // The outbox delivers nothing, and `blocked_reason` deliberately does
        // not bite on a sender that cannot reach anyone. Without this the
        // composer warned "this will be refused" about a send that was about
        // to succeed -- a warning that is wrong is worse than none, because
        // the next one gets ignored too.
        delivers={Boolean(email?.configured)}
      />
    </main>
  )
}

/** Write to anyone, from the dealership's address.
 *
 *  Deliberately not restricted to buyers on file: the case this whole page was
 *  built for is a stranger writing to sales@, and a composer that could only
 *  answer existing leads would push a rep back into their own mail client --
 *  where the reply is invisible to this system for good.
 *
 *  What it does instead of restricting is *say who it found*. The address is
 *  matched as it is typed, and the line under the field is the difference
 *  between a send that lands on a buyer's timeline with a reply route home and
 *  one that sits only here. Both are allowed; only one of them is silent. */
function Composer({
  draft,
  onChange,
  onClose,
  onSent,
  scope,
  recipients,
  delivers,
}: {
  draft: Compose | null
  onChange: (draft: Compose) => void
  onClose: () => void
  onSent: () => void
  scope: string
  recipients?: string[] | null
  delivers: boolean
}) {
  const [result, setResult] = useState<string | null>(null)
  const sentOk = useRef(false)

  const { data: book } = useQuery({
    queryKey: ['email-recipients'],
    queryFn: () => api.get<{ recipients: Recipient[] }>('/api/email/recipients'),
    enabled: Boolean(draft),
  })

  const send = useMutation({
    mutationFn: (d: Compose) => api.post<{
      status: string
      error: string
      provider: string
      lead_id: string | null
      delivered_externally: boolean
    }>('/api/email/compose', {
      to: d.to.trim(),
      subject: d.subject,
      body: d.body,
      lead_id: d.lead_id ?? null,
      in_reply_to_outreach_id: d.in_reply_to_outreach_id ?? null,
    }),
    onSuccess: (r) => {
      onSent()
      if (r.status === 'sent') {
        sentOk.current = true
        // Closed on success, kept open on failure. A refusal names the setting
        // that lifts it, and throwing the body away to show that message would
        // mean retyping the email to act on it.
        onClose()
        return
      }
      setResult(`${r.status}: ${r.error}`)
    },
    onError: (e) => setResult((e as ApiError).message),
  })

  useEffect(() => {
    if (draft) {
      setResult(null)
      sentOk.current = false
    }
  }, [draft])

  const close = () => {
    // Nothing on the server holds this. Asking is the whole safety net.
    if (
      !sentOk.current
      && (draft?.body.trim() || draft?.subject.trim())
      && !window.confirm('Discard this email? Nothing here is saved as a draft.')
    ) return
    onClose()
  }

  if (!draft) return null

  const to = draft.to.trim().toLowerCase()
  // A hint, not the verdict. The server puts the address through the one
  // matcher at send time -- email exact, phone by its last ten digits, a name
  // never -- and this is the same rule's easy half, shown early.
  const known = draft.lead_id
    ? { lead_id: draft.lead_id, name: draft.lead_name || 'this buyer', email: to }
    : book?.recipients.find((r) => r.email.toLowerCase() === to)
  const allowed = recipients === null || (recipients ?? []).includes(to)

  return (
    <Sheet open onClose={close} title={<h2 className="text-sm font-semibold">New email</h2>}>
      <div className="space-y-3">
        <Field label="To">
          <Input
            value={draft.to}
            onChange={(e) => onChange({ ...draft, to: e.target.value, lead_id: undefined })}
            placeholder="someone@example.com"
            type="email"
            list="liner-recipients"
            autoFocus
          />
        </Field>
        <datalist id="liner-recipients">
          {book?.recipients.map((r) => (
            <option key={r.lead_id} value={r.email}>
              {r.name}
            </option>
          ))}
        </datalist>

        {to && (
          known ? (
            <p className="text-xs text-muted-foreground">
              Goes on{' '}
              <Link to={`/app/leads/${known.lead_id}`} className="text-primary hover:underline">
                {known.name || known.email}
              </Link>
              &apos;s timeline, and their reply comes back to it.
            </p>
          ) : (
            /* Not a warning. Writing to someone who is not a buyer yet is a
               normal thing to do, and the send is recorded either way -- it
               simply has no timeline to sit on until they are one. */
            <p className="text-xs text-muted-foreground">
              No buyer on file for this address. The send and any reply will show
              here, but on nobody&apos;s timeline.
            </p>
          )
        )}

        <Field label="Subject">
          <Input
            value={draft.subject}
            onChange={(e) => onChange({ ...draft, subject: e.target.value })}
            placeholder="What this is about"
          />
        </Field>

        <Field label="Message">
          <textarea
            value={draft.body}
            onChange={(e) => onChange({ ...draft, body: e.target.value })}
            rows={12}
            placeholder="Plain text. It goes out as paragraphs, not wrapped in a template -- what you write is what they read."
            className="scroll-thin w-full rounded-md border border-input bg-background px-2.5 py-2 text-sm leading-relaxed outline-none focus:border-ring focus:ring-1 focus:ring-ring"
          />
        </Field>

        {/* Before the button, not after the refusal -- worth reading while
            there is still time to stop. Which of the two applies depends on
            the sender: with the outbox nothing is mailed to anyone, so the
            outbound limit has nothing to bite on and saying otherwise would
            be a warning about something that is not going to happen. */}
        {!delivers ? (
          <div className="rounded-md border border-border bg-muted/40 p-2.5">
            <p className="text-xs leading-relaxed text-muted-foreground">
              No mail will leave the building. The sender is the local outbox:
              this is recorded here and on the buyer&apos;s timeline, and nothing
              is delivered. Set <code>EMAIL_SENDER=resend</code> to send for real.
            </p>
          </div>
        ) : !allowed && to ? (
          <div className="rounded-md border border-warning/30 bg-warning-muted p-2.5">
            <p className="text-xs leading-relaxed text-warning-foreground">
              {scope} This one will be refused and recorded as not sent.
            </p>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
          <Button
            variant="primary"
            size="sm"
            disabled={!draft.to.trim() || send.isPending}
            onClick={() => {
              setResult(null)
              send.mutate(draft)
            }}
          >
            {send.isPending ? 'Sending...' : 'Send'}
          </Button>
          <Button size="sm" onClick={close}>Cancel</Button>
        </div>

        {result && (
          <pre className="scroll-thin max-h-40 overflow-auto whitespace-pre-wrap rounded-md border border-destructive/30 bg-destructive/5 p-2.5 text-xs text-destructive">
            {result}
          </pre>
        )}
      </div>
    </Sheet>
  )
}

/** Who outbound may reach, in a sentence, with the line that changes it.
 *
 *  This used to be DEMO_MODE plus EMAIL_ALLOWLIST -- two settings to express
 *  one rule, and a name that read like an inbound access list. Nothing here
 *  has ever filtered incoming mail. */
/** How long until a queued reply fires.
 *
 *  `relative` is deliberately past-only -- a future instant comes out of it as
 *  "just now", which for a reply that has not been sent yet reads as one that
 *  has. This is the other direction and says so. */
function dueIn(iso: string): string {
  const minutes = Math.round((new Date(iso).getTime() - Date.now()) / 60000)
  if (minutes <= 0) return 'due now'
  if (minutes < 60) return `in ${minutes}m`
  return `in ${Math.round(minutes / 60)}h`
}

/** Whether Liner is answering email, why not, and the switch itself.
 *
 *  The three checks are named individually because they are fixed in three
 *  different places, and a single "off" sends whoever is reading to the wrong
 *  one. That is not hypothetical: `EMAIL_AGENT=true` and `LLM_MODE=live` were
 *  both set on a real deployment and nothing was ever answered, because the
 *  runtime flag defaults off and the control for it did not exist. A switch
 *  with no way to throw it is worse than no switch at all -- the setting says
 *  the feature is on and the product silently disagrees.
 *
 *  Open to any rep, like the endpoint behind it: this is what somebody reaches
 *  for while the inbox is being hammered, and a manager-only control is one
 *  the person watching it happen cannot use. */
function AgentSwitch({
  state,
  busy,
  onToggle,
}: {
  state: AgentState | undefined
  busy: boolean
  onToggle: (value: 'on' | 'off') => void
}) {
  if (!state) return null
  const flagOn = state.flag === 'on'
  // Named checks, in the order they are cheapest to fix. Each says where it
  // lives, because "not configured" costs an hour of looking in the wrong file.
  const checks: [boolean, string, string][] = [
    [state.allowed_by_env, 'Turned on for this deployment', 'EMAIL_AGENT=true in .env, then restart'],
    [flagOn, 'Switched on here', 'the button on this card -- it takes effect on the next delivery'],
    [state.live_model, 'A model to write with', 'LLM_MODE=live and OPENAI_API_KEY'],
  ]
  // The flag's own note. Set by the hourly ceiling when it trips itself, so
  // the morning after does not read as somebody having switched it off by hand.
  const why = state.flags.find((f) => f.key === 'email_agent')?.reason ?? ''

  return (
    <Card className="mb-6 min-w-0 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">Liner answers email</h2>
        <Badge tone={state.on ? 'success' : 'warning'}>{state.on ? 'on' : 'off'}</Badge>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          <Button
            variant={flagOn ? 'ghost' : 'primary'}
            size="sm"
            disabled={busy}
            onClick={() => onToggle(flagOn ? 'off' : 'on')}
          >
            {busy ? 'Saving...' : flagOn ? 'Switch off' : 'Switch on'}
          </Button>
        </div>
      </div>

      <ul className="mt-3 grid gap-1.5 sm:grid-cols-3">
        {checks.map(([ok, label, fix]) => (
          <li key={label} className="flex min-w-0 items-start gap-1.5">
            <Icon
              name={ok ? 'check' : 'alert'}
              className={clsx('mt-0.5 h-3.5 w-3.5 shrink-0', ok ? 'text-success' : 'text-warning')}
            />
            <span className="min-w-0 text-xs leading-relaxed">
              <span className={clsx('font-medium', !ok && 'text-warning-foreground')}>{label}</span>
              {!ok && <span className="block text-muted-foreground">{fix}</span>}
            </span>
          </li>
        ))}
      </ul>

      {!state.on && state.detail && (
        <p className="mt-3 rounded-md border border-warning/30 bg-warning-muted p-2.5 text-xs leading-relaxed text-warning-foreground">
          {state.detail}
        </p>
      )}
      {!flagOn && why && (
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{why}</p>
      )}

      <p className="mt-3 text-xs text-muted-foreground">
        {/* Every reply waits, including the first -- the wait is the window in
            which a rep can get there first. Said here because otherwise a few
            quiet minutes are indistinguishable from the agent being off, which
            is the exact confusion this card exists to end. */}
        Every reply waits {state.cooldown_minutes} minutes before it goes, so a rep can answer
        first. At most {state.hourly_ceiling} an hour, after which this switches itself off.
      </p>

      {state.waiting.length > 0 && (
        <div className="mt-3 rounded-md border border-border bg-muted/40 p-2.5">
          <p className="text-[11px] font-medium text-muted-foreground">
            Queued ({state.waiting.length})
          </p>
          <ul className="mt-1 space-y-0.5">
            {state.waiting.slice(0, 5).map((r) => (
              <li key={r.id} className="flex items-center gap-2 text-xs">
                <span className="tnum shrink-0 text-muted-foreground">{dueIn(r.due_at)}</span>
                {r.lead_id ? (
                  <Link to={`/app/leads/${r.lead_id}`} className="truncate text-primary hover:underline">
                    open the buyer
                  </Link>
                ) : (
                  <span className="truncate text-muted-foreground">no buyer on file</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {state.recent.length > 0 && (
        <div className="mt-3">
          <p className="text-[11px] font-medium text-muted-foreground">Last few</p>
          <ul className="mt-1 space-y-0.5">
            {state.recent.slice(0, 5).map((r) => (
              <li key={r.id} className="flex min-w-0 items-start gap-2 text-xs">
                <span
                  className={clsx(
                    'mt-0.5 shrink-0 rounded border px-1.5 py-0.5 text-[11px] font-medium',
                    r.state === 'sent'
                      ? 'border-success/30 bg-success/10 text-success'
                      : r.state === 'failed'
                        ? 'border-destructive/30 bg-destructive/10 text-destructive'
                        : 'border-border text-muted-foreground',
                  )}
                >
                  {r.state}
                </span>
                {/* A sent reply carries no detail, and there is nothing wrong
                    with that -- but a dash reads as a missing value. The state
                    already says what happened, so it says it in words. */}
                <span className="min-w-0 flex-1 truncate text-muted-foreground">
                  {r.detail || (r.state === 'sent' ? 'Answered.' : r.state)}
                </span>
                <span className="tnum shrink-0 text-muted-foreground">{relative(r.at)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {state.declined.length > 0 && (
        <div className="mt-3">
          {/* "It did not reply, is that on purpose?" is the question a person
              actually has, and the answer used to live only on a receipt in a
              diagnostics strip nobody opens until they already suspect
              something. */}
          <p className="text-[11px] font-medium text-muted-foreground">Not answered, and why</p>
          <ul className="mt-1 space-y-0.5">
            {state.declined.map((r) => (
              <li key={r.id} className="min-w-0 text-xs">
                <span className="font-medium">{r.from_address}</span>
                <span className="text-muted-foreground"> · {r.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}

function OutboundScope({
  scope,
  recipients,
}: {
  scope: string
  recipients?: string[] | null
}) {
  const unrestricted = recipients === null
  const nobody = Array.isArray(recipients) && recipients.length === 0
  return (
    <div
      className={clsx(
        'mt-3 rounded-md border p-2.5',
        unrestricted
          ? 'border-warning/30 bg-warning-muted'
          : 'border-border bg-muted/40',
      )}
    >
      <p
        className={clsx(
          'text-xs leading-relaxed',
          unrestricted ? 'text-warning-foreground' : 'text-muted-foreground',
        )}
      >
        {unrestricted && <strong>No limit. </strong>}
        {scope}
      </p>
      {!unrestricted && (
        <pre className="scroll-thin mt-1.5 overflow-x-auto rounded border border-border bg-background p-2 text-[11px]">
{nobody
  ? 'OUTBOUND_ONLY_TO=you@yourdomain.com     # or: everyone'
  : 'OUTBOUND_ONLY_TO=everyone               # to lift the limit'}
        </pre>
      )}
    </div>
  )
}

function StatusCard({
  title,
  configured,
  impl,
  detail,
  missing,
}: {
  title: string
  configured: boolean
  impl: string
  detail: string
  missing: string[]
}) {
  return (
    <Card className="min-w-0 p-5">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        <Badge tone={configured ? 'success' : 'warning'}>
          {configured ? impl : 'not configured'}
        </Badge>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{detail}</p>
      {missing.length > 0 && (
        <div className="mt-3">
          {/* Named, not "check your configuration". The whole cost of an
              unconfigured integration is the hour spent finding out which
              variable it wanted. */}
          <p className="text-[11px] font-medium text-muted-foreground">Set these:</p>
          <ul className="mt-1 space-y-0.5">
            {missing.map((key) => (
              <li key={key} className="flex items-center gap-1.5 text-xs">
                <Icon name="alert" className="h-3 w-3 shrink-0 text-warning" />
                <code>{key}</code>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}

/** One correspondent. A buyer opens their page; a stranger has none to open,
 *  which is the whole reason they are listed here rather than nowhere. */
function ThreadRow({ row, threshold }: { row: Thread; threshold: number }) {
  const body = (
    <div className="flex min-w-0 items-start gap-3 px-4 py-3 text-left">
      <span
        className={clsx(
          'mt-0.5 inline-flex shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium',
          row.waiting
            ? 'border-primary/30 bg-primary/10 text-primary'
            : row.graduated
              ? 'border-border text-muted-foreground'
              : 'border-border text-muted-foreground',
        )}
      >
        {row.waiting ? 'waiting on us' : row.graduated ? 'conversation' : 'open'}
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">
          {row.name || row.address || 'Unnamed buyer'}
        </div>
        <div className="truncate text-xs text-muted-foreground">
          {row.last_subject || '(no subject)'}
        </div>
        <div className="mt-0.5 truncate text-xs text-muted-foreground">
          {/* Counted, never declared. `exchanges` decides the badge above and
              the tab this row is in, so it is the number that is shown. */}
          {row.exchanges} of {threshold} exchanges · {row.inbound} in, {row.outbound} out
          {row.kind === 'stranger' && ' · no buyer on file'}
        </div>
      </div>
      <span className="tnum shrink-0 whitespace-nowrap text-xs text-muted-foreground">
        {relative(row.at ?? undefined)}
      </span>
    </div>
  )
  return row.lead_id ? (
    <Link to={`/app/leads/${row.lead_id}`} className="block hover:bg-accent/50">
      {body}
    </Link>
  ) : (
    <div className="opacity-90">{body}</div>
  )
}
