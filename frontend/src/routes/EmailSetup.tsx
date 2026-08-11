import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'

import { api, ApiError } from '../lib/api'
import { dateTime } from '../lib/format'
import type { IntegrationsPayload } from '../lib/types'
import { Badge, Button, Card, Field, Input, Spinner } from '../components/ui'
import { Icon } from '../components/Icon'
import { PageIntro } from '../components/dashboard/AppShell'

/* Email, both directions, on one screen you can prove things on.
 *
 * Sending and receiving fail in completely different places and for completely
 * different reasons. Outbound breaks at Resend -- an unverified domain, a bad
 * key -- and says so loudly on the next send. Inbound breaks in Cloudflare,
 * which is configured outside this app entirely, and breaks *silently*: no
 * error, no row, just replies that never arrive. That asymmetry is why this
 * page exists and why the receipts below matter more than they look.
 */

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

  const { data: integrations } = useQuery({
    queryKey: ['integrations'],
    queryFn: () => api.get<IntegrationsPayload>('/api/integrations'),
  })
  const { data, isLoading } = useQuery({
    queryKey: ['email-receipts'],
    queryFn: () => api.get<ReceiptsPayload>('/api/email/receipts'),
  })
  const { data: sends } = useQuery({
    queryKey: ['email-replyable'],
    queryFn: () => api.get<{ sends: Send[] }>('/api/email/replyable'),
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['email-receipts'] })
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
        subtitle="Sending goes out through Resend. Replies come back through Cloudflare."
      />

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
            Goes through the real path, allow-list included. With{' '}
            <code>DEMO_MODE</code> on, an address that is not in{' '}
            <code>EMAIL_ALLOWLIST</code> is refused and the refusal is recorded —
            that guard is what stops a rehearsal mailing a prospect, so this page
            does not bypass it.
          </p>
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
    </main>
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
