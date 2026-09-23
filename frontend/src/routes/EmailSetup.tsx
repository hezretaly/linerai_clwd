import { useEffect, useId, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { Link } from 'react-router-dom'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'

import { api, ApiError } from '../lib/api'
import { dateTime, relative } from '../lib/format'
import { withStore } from '../lib/store'
import type { IntegrationsPayload } from '../lib/types'
import {
  addrList,
  bareAddress,
  fwdSubject,
  looksLikeAddress,
  quotedBody,
  reSubject,
  replyAllAddsSomeone,
  splitPending,
  splitRecipients,
  type Addr,
  type Attachment,
  type EmailSummary,
  type Importance,
  type MailContent,
} from '../lib/email'
import {
  Badge,
  Button,
  Card,
  Empty,
  Field,
  FieldGroup,
  Input,
  Sheet,
  Spinner,
} from '../components/ui'
import {
  AttachmentPicker,
  CopyFields,
  EmailView,
  RecipientInput,
  RichEditor,
} from '../components/email'
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
  /** Every To and Cc, the files and the importance. `address` above stays the
   *  one person the row is about -- the sender of what arrived, the first To
   *  of what went -- and this is everybody else. Absent from a server that
   *  predates it, so every reader of it copes without. */
  email?: EmailSummary
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
  /** Each box is the one string the recipient field holds -- chips and the
   *  text still being typed, comma separated -- and goes to the server as
   *  typed, which splits it by the same rule the chips were drawn with. */
  to: string
  cc: string
  bcc: string
  subject: string
  /** The editor's HTML, "" when the box is empty. */
  html: string
  /** And its text. This is what decides whether there is anything to send,
   *  because an HTML body with nothing in it is still markup. */
  text: string
  attachments: Attachment[]
  importance: Importance
  /** The sheet's title: New email, Reply, Reply all or Forward. */
  heading: string
  /** Set when replying to mail from a buyer already on file, so the send is
   *  filed against them even if they wrote from an address the matcher has
   *  never seen. */
  lead_id?: string
  lead_name?: string
  /** The message being answered, so the buyer's client threads the reply
   *  under it instead of starting a second conversation in their inbox. A
   *  placed message is an outreach row; one nobody could place is only a
   *  receipt, and threads under that instead. */
  in_reply_to_outreach_id?: string
  in_reply_to_receipt_id?: string
  /** A forward names the message whose files came along with it. */
  forward_of?: { kind: Mail['kind']; id: string }
  /** Ids of those files: that message's, not uploads of ours, so throwing the
   *  draft away leaves them where they are. */
  forwarded?: string[]
}

function blankCompose(): Compose {
  return {
    to: '',
    cc: '',
    bcc: '',
    subject: '',
    html: '',
    text: '',
    attachments: [],
    importance: 'normal',
    heading: 'New email',
  }
}

/** A box's text as the list the server reads, without the trailing ", " the
 *  recipient field leaves while an address is still being typed. */
function tidy(value: string): string {
  return splitRecipients(value).join(', ')
}

/** Every address in some boxes, bare and lowercased, each once. */
function addressesIn(...boxes: string[]): string[] {
  const seen = new Set<string>()
  for (const box of boxes) {
    for (const entry of splitRecipients(box)) {
      const bare = bareAddress(entry).trim().toLowerCase()
      if (bare) seen.add(bare)
    }
  }
  return [...seen]
}

/** People on a row's message beyond the one address it prints.
 *
 *  For our own sends that is every other To and Cc. For mail that arrived it
 *  is the same less ourselves: a buyer's message is addressed *to* the
 *  dealership, and "+1" on every received row would be the mailbox counting
 *  itself. Ours is anything on the sending domain -- every dealership mailbox
 *  and every reply+ address lives there, which is the server's own rule in
 *  `email_envelopes.is_our_address`. With no domain configured nothing can be
 *  told apart, and the count includes whichever of ours the sender wrote to. */
function othersOn(m: Mail, ourDomain: string): Addr[] {
  if (!m.email) return []
  const skip = (m.address || '').trim().toLowerCase()
  const domain = ourDomain.trim().toLowerCase()
  const seen = new Map<string, Addr>()
  for (const a of [...(m.email.to || []), ...(m.email.cc || [])]) {
    const key = (a.address || '').trim().toLowerCase()
    if (!key || key === skip || seen.has(key)) continue
    if (
      m.direction === 'in'
      && (key.startsWith('reply+') || (domain !== '' && key.endsWith(`@${domain}`)))
    ) continue
    seen.set(key, a)
  }
  return [...seen.values()]
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

/** `heading` is false where this is a *section* rather than the page -- which
 * is everywhere now, since `/app/email` redirects and Campaigns is the only
 * caller. It renders its own `PageIntro` otherwise, and two stacked headings
 * on the screen somebody lands on reads as a page that failed to lay out. */
export function EmailSetupPage({ heading = true }: { heading?: boolean }) {
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
      {heading && (
        <PageIntro
          title="Email"
          subtitle="Everything sent and received. Out through Resend, back through Cloudflare."
        />
      )}

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
              onClick={() => setComposing(blankCompose())}
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
            {mail.messages.map((m) => {
              const others = othersOn(m, data.reply_domain || '')
              const files = m.email?.attachments ?? []
              return (
                <li key={`${m.kind}-${m.id}`}>
                  <button
                    onClick={() => setReading(reading?.id === m.id ? null : m)}
                    aria-expanded={reading?.id === m.id}
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
                      <div className="flex min-w-0 items-center gap-1.5 text-sm font-medium">
                        <span className="min-w-0 truncate">
                          {m.subject || <span className="text-muted-foreground">(no subject)</span>}
                        </span>
                        {m.email?.importance === 'high' && (
                          <Badge tone="warning" className="shrink-0">High</Badge>
                        )}
                        {files.length > 0 && (
                          <span
                            className="inline-flex shrink-0 items-center gap-0.5 text-xs font-normal text-muted-foreground"
                            title={files.map((f) => f.filename).join(', ')}
                          >
                            <Icon name="paperclip" className="h-3 w-3" />
                            <span className="tnum">{files.length}</span>
                            <span className="sr-only">{files.length === 1 ? 'file' : 'files'}</span>
                          </span>
                        )}
                      </div>
                      <div className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
                        <span className="min-w-0 truncate">
                          {m.direction === 'in' ? 'from' : 'to'} {m.address || 'unknown'}
                        </span>
                        {/* Everybody else on it, named on hover. A buyer who
                            copied in their partner is somebody a reply-all
                            reaches and a plain reply does not. */}
                        {others.length > 0 && (
                          <span
                            className="tnum shrink-0 rounded border border-border px-1"
                            title={`Also on it: ${addrList(others)}`}
                          >
                            +{others.length}
                          </span>
                        )}
                        {(m.lead_name
                          || (!m.delivered_externally && m.direction === 'out' && m.status === 'sent')) && (
                          <span className="min-w-0 truncate">
                            {m.lead_name && ` · ${m.lead_name}`}
                            {!m.delivered_externally && m.direction === 'out' && m.status === 'sent'
                              && ' · recorded locally, not delivered'}
                          </span>
                        )}
                      </div>
                      {m.error && (
                        <div className="mt-0.5 break-words text-xs text-destructive">{m.error}</div>
                      )}
                    </div>
                    <span className="tnum shrink-0 whitespace-nowrap text-xs text-muted-foreground">
                      {dateTime(m.at)}
                    </span>
                  </button>
                  {reading?.id === m.id && (
                    <div className="min-w-0 border-t border-border bg-muted/30 px-4 py-3">
                      <MailReader mail={m} onCompose={setComposing} />
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
              )
            })}
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
        onSent={() => {
          refresh()
          // The people list moves too: an answer is what turns "waiting on
          // us" into an exchange.
          void queryClient.invalidateQueries({ queryKey: ['email-threads'] })
        }}
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

/** One message, opened where it sits in the list.
 *
 *  **Read from the reader endpoint, not from the row.** The row carries the
 *  trimmed text the list searches -- the quoted thread cut off, no HTML, no
 *  files -- which is right for a list and wrong for reading. The reader has
 *  the whole message: every recipient, the HTML (cleaned on the server, and
 *  again in `EmailView`), what was attached, and who a reply or a reply-all
 *  would go to, worked out once on the server with our own addresses taken
 *  out. Building those here would be a second answer to "who does Reply all
 *  reach", which is the kind that drifts.
 *
 *  **Remote images stay off until asked for.** A tracking pixel tells the
 *  sender the message was opened, and a rep glancing at a newsletter has not
 *  decided to say so. `images=1` is a second fetch, and the previous message
 *  stays on screen while it runs.
 *
 *  **A reader that cannot load still leaves a way to answer.** The row alone
 *  is enough for the reply this page always offered -- to the address it
 *  prints, `Re:` the subject -- so a failed read costs the extras, not the
 *  reply.
 */
function MailReader({
  mail,
  onCompose,
}: {
  mail: Mail
  onCompose: (draft: Compose) => void
}) {
  const [images, setImages] = useState(false)
  const { data: content, error, isLoading } = useQuery({
    queryKey: ['email-read', mail.kind, mail.id, images],
    queryFn: () =>
      api.get<MailContent>(
        `/api/email/read/${mail.kind}/${encodeURIComponent(mail.id)}?images=${images ? 1 : 0}`,
      ),
    placeholderData: keepPreviousData,
    // A 404 is an answer -- the delivery was placed on a buyer since the list
    // loaded, or it was never ours to read -- and asking again only delays
    // the fallback below. A server error may pass; that one is retried once.
    retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 1,
  })

  // Threading names what is being answered: a placed message is an outreach
  // row, in either direction -- the server threads under our own send's
  // Message-ID where it knows one, and leaves the reply unthreaded rather
  // than under a provider's id where it does not. Mail nobody placed has only
  // a receipt.
  const answering = {
    in_reply_to_outreach_id: mail.kind === 'message' ? mail.id : undefined,
    in_reply_to_receipt_id: mail.kind === 'unmatched' ? mail.id : undefined,
    lead_id: mail.lead_id ?? content?.lead_id ?? undefined,
    lead_name: mail.lead_name || undefined,
  }

  const reply = (all: boolean) => {
    if (!content) return
    const target = all ? content.reply_all : content.reply
    onCompose({
      ...blankCompose(),
      ...answering,
      heading: all ? 'Reply all' : 'Reply',
      to: target.to.join(', '),
      cc: target.cc.join(', '),
      // Not "Re: Re: Re:". The server already added it once; this is only
      // for a server that sent none.
      subject: target.subject || reSubject(content.subject),
      html: quotedBody(content, 'reply'),
    })
  }

  const forward = () => {
    if (!content) return
    const ids = new Set(content.forward.attachment_ids)
    const files = content.attachments.filter((a) => ids.has(a.id))
    onCompose({
      ...blankCompose(),
      heading: 'Forward',
      subject: content.forward.subject || fwdSubject(content.subject),
      html: quotedBody(content, 'forward'),
      // Theirs, carried along: each can be taken off before sending, and the
      // send then carries exactly the ones left.
      attachments: files,
      forwarded: files.map((a) => a.id),
      forward_of: { kind: mail.kind, id: mail.id },
      // Deliberately not filed against this buyer, and not threaded under
      // their message: a forward goes to somebody else, and the matcher
      // decides whose timeline it belongs on from who that is.
    })
  }

  // The reply this page offered before the reader existed, from the row.
  const replyFromRow = () =>
    onCompose({
      ...blankCompose(),
      ...answering,
      heading: 'Reply',
      to: mail.address,
      subject: reSubject(mail.subject),
    })

  return (
    <div className="min-w-0 space-y-3">
      {content ? (
        // Its own scroll, so a long newsletter opened in the middle of the
        // list does not push the rest of the mailbox a screen away.
        <div className="scroll-thin max-h-[70vh] min-w-0 overflow-y-auto">
          <EmailView
            message={content}
            resolve={withStore}
            onShowImages={() => setImages(true)}
            showBcc
            compact
          />
        </div>
      ) : isLoading ? (
        <Spinner />
      ) : (
        <>
          <p className="text-xs text-destructive">
            This message could not be opened
            {error instanceof ApiError ? `: ${error.message}` : '.'}
          </p>
          <pre className="scroll-thin max-h-64 overflow-auto whitespace-pre-wrap text-xs leading-relaxed">
            {mail.body || '(no body)'}
          </pre>
        </>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {content ? (
          <>
            <Button size="sm" onClick={() => reply(false)}>
              <Icon name="reply" className="h-3.5 w-3.5 shrink-0" />
              Reply
            </Button>
            {/* Only where it would reach somebody Reply does not. On a
                message with one sender and one recipient -- most of them --
                the two buttons do the same thing, and a choice that changes
                nothing is one more thing to read. */}
            {replyAllAddsSomeone(content) && (
              <Button size="sm" onClick={() => reply(true)}>
                <Icon name="replyAll" className="h-3.5 w-3.5 shrink-0" />
                Reply all
              </Button>
            )}
            <Button size="sm" onClick={forward}>
              <Icon name="forward" className="h-3.5 w-3.5 shrink-0" />
              Forward
            </Button>
          </>
        ) : (
          !isLoading && (
            <Button size="sm" onClick={replyFromRow}>
              <Icon name="reply" className="h-3.5 w-3.5 shrink-0" />
              Reply
            </Button>
          )
        )}
      </div>
    </div>
  )
}

interface Signature {
  text: string
  fallback: string
  image_url: string
}

interface ComposeResult {
  status: string
  error: string
  provider: string
  lead_id: string | null
  delivered_externally: boolean
  blocked?: boolean
}

/** Write to anyone, from the dealership's address.
 *
 *  Deliberately not restricted to buyers on file: the case this whole page was
 *  built for is a stranger writing to sales@, and a composer that could only
 *  answer existing leads would push a rep back into their own mail client --
 *  where the reply is invisible to this system for good.
 *
 *  What it does instead of restricting is *say who it found*. Every address
 *  typed is matched as it is typed, and the line under the field is the
 *  difference between a send that lands on a buyer's timeline with a reply
 *  route home and one that sits only here. Both are allowed; only one of them
 *  is silent. A message is filed against the buyer its first To belongs to,
 *  so that is the one the line is about, and a buyer on file further along
 *  is named rather than quietly left off their own timeline.
 *
 *  The draft is held by the page and every change is a functional update. The
 *  editor, the file picker and the address boxes all report in on their own
 *  schedule -- an upload can finish in the same tick the editor settles -- and
 *  spreading a copy of the draft from one render would let the second report
 *  put back what the first had just changed. */
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
  onChange: Dispatch<SetStateAction<Compose | null>>
  onClose: () => void
  onSent: () => void
  scope: string
  recipients?: string[] | null
  delivers: boolean
}) {
  const toId = useId()
  const [result, setResult] = useState<string | null>(null)
  const sentOk = useRef(false)
  const open = draft !== null

  const update = (patch: Partial<Compose> | ((d: Compose) => Partial<Compose>)) =>
    onChange((d) => (d ? { ...d, ...(typeof patch === 'function' ? patch(d) : patch) } : d))

  const { data: book } = useQuery({
    queryKey: ['email-recipients'],
    queryFn: () => api.get<{ recipients: Recipient[] }>('/api/email/recipients'),
    enabled: open,
  })
  // What the send appends: this person's own sign-off, or their name over the
  // dealership's where they have written none. The key is the one the
  // sign-off editor writes to, so an edit there shows here at once.
  const { data: signature } = useQuery({
    queryKey: ['my-signature'],
    queryFn: () => api.get<Signature>('/api/me/signature'),
    enabled: open,
  })

  const send = useMutation({
    mutationFn: (d: Compose) => api.post<ComposeResult>('/api/email/compose', {
      to: tidy(d.to),
      cc: tidy(d.cc) || undefined,
      bcc: tidy(d.bcc) || undefined,
      subject: d.subject,
      // The text half, as the editor reads it. The server writes the text
      // from the HTML when there is HTML, so the two halves cannot differ.
      body: d.text,
      html: d.html || undefined,
      // Always the list, empty included: on a forward that is how "the rep
      // took every file off" differs from "send whatever it carried".
      attachment_ids: d.attachments.map((a) => a.id),
      importance: d.importance,
      lead_id: d.lead_id ?? null,
      in_reply_to_outreach_id: d.in_reply_to_outreach_id ?? null,
      in_reply_to_receipt_id: d.in_reply_to_receipt_id ?? null,
      forward_of: d.forward_of ?? null,
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
      setResult(r.error ? `${r.status}: ${r.error}` : `Not sent (${r.status}).`)
    },
    onError: (e) => setResult((e as ApiError).message),
  })

  useEffect(() => {
    if (open) {
      setResult(null)
      sentOk.current = false
    }
  }, [open])

  const close = () => {
    if (!draft) return onClose()
    // Nothing on the server holds this. Asking is the whole safety net.
    const written = draft.subject.trim() || draft.text.trim() || draft.attachments.length > 0
    if (
      !sentOk.current
      && written
      && !window.confirm('Discard this email? Nothing here is saved as a draft.')
    ) return
    if (!sentOk.current) {
      // Files uploaded for a message that is not going anywhere. Nothing else
      // will ever claim them, so they go now; a forwarded message's own files
      // are that message's and stay.
      const theirs = new Set(draft.forwarded ?? [])
      for (const a of draft.attachments) {
        if (!theirs.has(a.id)) {
          api.del(`/api/email/attachments/${encodeURIComponent(a.id)}`).catch(() => {})
        }
      }
    }
    onClose()
  }

  if (!draft) return null

  const toEntries = splitRecipients(draft.to)
  const primary = bareAddress(toEntries[0] ?? '').trim().toLowerCase()
  const everyone = addressesIn(draft.to, draft.cc, draft.bcc)
  const onFile = (address: string) =>
    book?.recipients.find((r) => r.email.toLowerCase() === address)

  // A hint, not the verdict. The server puts the first To through the one
  // matcher at send time -- email exact, phone by its last ten digits, a name
  // never -- and this is the same rule's easy half, shown early.
  const known = draft.lead_id
    ? { lead_id: draft.lead_id, name: draft.lead_name || 'this buyer', email: primary }
    : primary ? onFile(primary) : undefined
  const alsoOnFile = everyone
    .filter((a) => a !== primary)
    .map(onFile)
    .filter((r): r is Recipient => Boolean(r) && r?.lead_id !== known?.lead_id)
  // Finished entries only: the text after the last comma is somebody still
  // typing, and "not an address" under every keystroke of one is noise. The
  // server still judges it if Send is pressed first.
  const unreadable = [draft.to, draft.cc, draft.bcc]
    .flatMap((box) => splitPending(box).entries)
    .filter((entry) => !looksLikeAddress(entry))
  // Every recipient, Cc and Bcc included, because the server refuses the
  // whole message if any one of them is outside the limit. Unknown until the
  // integrations answer, and unknown is not a refusal.
  const permitted = Array.isArray(recipients)
    ? new Set(recipients.map((r) => bareAddress(r).trim().toLowerCase()))
    : null
  const refused = permitted
    ? everyone.filter((a) => looksLikeAddress(a) && !permitted.has(a))
    : []

  const sign = signature ? signature.text.trim() || signature.fallback : ''
  const hasRecipient = toEntries.length > 0
  const hasSomething = Boolean(
    draft.subject.trim() || draft.text.trim() || draft.attachments.length,
  )
  const high = draft.importance === 'high'

  return (
    <Sheet
      open
      onClose={close}
      width="w-[40rem]"
      title={<h2 className="text-sm font-semibold">{draft.heading}</h2>}
    >
      <div className="min-w-0 space-y-3">
        <FieldGroup label="To" htmlFor={toId}>
          <RecipientInput
            id={toId}
            ariaLabel="To"
            value={draft.to}
            onChange={(to) =>
              update((d) => {
                // A reply is filed against the buyer it answers even from an
                // address the matcher has never seen -- until the first To is
                // somebody else, and then the matcher decides again.
                const was = bareAddress(splitRecipients(d.to)[0] ?? '').toLowerCase()
                const now = bareAddress(splitRecipients(to)[0] ?? '').toLowerCase()
                return was === now ? { to } : { to, lead_id: undefined, lead_name: undefined }
              })
            }
            suggestions={book?.recipients}
            placeholder="someone@example.com"
            autoFocus={!draft.to}
          />
        </FieldGroup>

        {primary && (
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
              No buyer on file for <span className="break-all">{primary}</span>. The send and
              any reply will show here, but on nobody&apos;s timeline.
            </p>
          )
        )}
        {alsoOnFile.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Also on file:{' '}
            {alsoOnFile.map((r, i) => (
              <span key={r.lead_id}>
                {i > 0 && ', '}
                <Link to={`/app/leads/${r.lead_id}`} className="text-primary hover:underline">
                  {r.name || r.email}
                </Link>
              </span>
            ))}
            . A message is filed on its first To&apos;s timeline only
            {known ? '.' : ' -- put theirs first to file it there.'}
          </p>
        )}

        <CopyFields
          cc={draft.cc}
          bcc={draft.bcc}
          onCc={(cc) => update({ cc })}
          onBcc={(bcc) => update({ bcc })}
          suggestions={book?.recipients}
        />

        {unreadable.length > 0 && (
          <div className="rounded-md border border-warning/30 bg-warning-muted p-2.5">
            <p className="break-words text-xs leading-relaxed text-warning-foreground">
              Not an email address: {unreadable.join(', ')}. The send will be refused until
              {unreadable.length === 1 ? ' it is' : ' they are'} fixed or removed.
            </p>
          </div>
        )}

        <Field label="Subject">
          <Input
            value={draft.subject}
            onChange={(e) => update({ subject: e.target.value })}
            placeholder="What this is about"
          />
        </Field>

        {/* A group, not `Field`: that is a label, and a label hands a click
            on its caption to the toolbar's first button. */}
        <FieldGroup label="Message">
          <RichEditor
            value={draft.html}
            onChange={(html, text) => update({ html, text })}
            placeholder="Write your message. Formatting is kept, and a plain version goes with it for mail apps that show text only."
            ariaLabel="Message"
            minHeight={240}
          />
        </FieldGroup>

        <AttachmentPicker
          value={draft.attachments}
          onChange={(attachments) => update({ attachments })}
          disabled={send.isPending}
        />

        {/* Shown, not typed. The server appends it -- the sender's own words
            or their name over the dealership's -- and a rep who has not seen
            it signs off by hand and sends the block twice. */}
        {sign && (
          <div className="rounded-md border border-border bg-muted/40 p-2.5">
            <p className="text-[11px] font-medium text-muted-foreground">Sent with this sign-off</p>
            <p className="mt-1 whitespace-pre-wrap break-words text-xs text-muted-foreground">
              {sign}
            </p>
            {signature?.image_url && (
              <p className="mt-1 text-[11px] text-muted-foreground">
                Your sign-off image goes under it in the formatted version.
              </p>
            )}
          </div>
        )}

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
        ) : refused.length > 0 ? (
          <div className="rounded-md border border-warning/30 bg-warning-muted p-2.5">
            <p className="break-words text-xs leading-relaxed text-warning-foreground">
              {scope} {refused.join(', ')} {refused.length === 1 ? 'is' : 'are'} not on it, so
              this one will be refused and recorded as not sent.
            </p>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
          <Button
            variant="primary"
            size="sm"
            disabled={!hasRecipient || !hasSomething || send.isPending}
            onClick={() => {
              setResult(null)
              send.mutate(draft)
            }}
          >
            {send.isPending ? 'Sending...' : 'Send'}
          </Button>
          <Button size="sm" onClick={close}>Cancel</Button>
          {/* A flag the buyer's mail app shows, not a louder message. Off
              unless somebody decides this one earns it. */}
          <button
            type="button"
            aria-pressed={high}
            onClick={() => update({ importance: high ? 'normal' : 'high' })}
            title="Marks the message as high importance in the recipient's mail app."
            className={clsx(
              'ml-auto inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors duration-150',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              high
                ? 'border-warning/30 bg-warning-muted text-warning-foreground'
                : 'border-border text-muted-foreground hover:bg-accent hover:text-accent-foreground',
            )}
          >
            <Icon name="alert" className="h-3.5 w-3.5 shrink-0" />
            High importance
          </button>
        </div>
        {!hasRecipient && hasSomething && (
          <p className="text-xs text-muted-foreground">Add somebody to send it to.</p>
        )}

        {result && (
          <pre className="scroll-thin max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md border border-destructive/30 bg-destructive/5 p-2.5 text-xs text-destructive">
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
