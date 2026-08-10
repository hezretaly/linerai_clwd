import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'

import { api, ApiError } from '../../lib/api'
import { PROVENANCE_LABEL, dateTime, relative } from '../../lib/format'
import type { Lead, LeadDraft, Outreach } from '../../lib/types'
import { Button, NotBacked, Sheet, Spinner, Unavailable } from '../ui'

/** Everything the dealership holds on one buyer, over a conversation list that
 *  is about threads rather than people. Opened from a row's Lead button.
 *
 *  It lives here rather than in a route because it is the only place a rep can
 *  send a follow-up or a credit application, and the page it used to belong to
 *  is no longer the one they will be looking at.
 */
export function LeadDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  // Which draft is open, not merely whether one is: a rep can be writing a
  // follow-up or a credit application, and they are different documents.
  const [composing, setComposing] = useState<string | null>(null)
  const { data: lead } = useQuery({
    queryKey: ['leads', id],
    queryFn: () => api.get<Lead>(`/api/leads/${id}`),
    enabled: Boolean(id),
  })

  return (
    <Sheet
      open={Boolean(id)}
      onClose={() => {
        setComposing(null)
        onClose()
      }}
      title={
        <>
          <h2 className="text-base font-semibold">{lead?.name ?? 'Lead'}</h2>
          <p className="text-sm text-muted-foreground">
            {lead?.email || 'No email on file'}
            {lead?.phone ? ` -- ${lead.phone}` : ''}
          </p>
        </>
      }
    >
      {!lead ? (
        <Spinner />
      ) : (
        <div className="space-y-6">
          <section>
            <h3 className="text-xs font-medium text-muted-foreground">Captured by Liner</h3>
            {lead.captured_fields?.length ? (
              <>
                <div className="mt-2 space-y-1.5">
                  {lead.captured_fields.map((f) => (
                    <div key={f.id} className="flex items-baseline gap-2 text-sm">
                      <span className="w-[86px] shrink-0 text-xs capitalize text-muted-foreground">
                        {f.key.replace(/_/g, ' ')}
                      </span>
                      <span
                        className={clsx(
                          'min-w-0 flex-1',
                          !f.verified && 'italic text-muted-foreground',
                        )}
                      >
                        {f.value}
                      </span>
                      <span
                        className={clsx(
                          'shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium',
                          f.verified
                            ? 'border-border text-muted-foreground'
                            : 'border-warning/30 bg-warning/10 text-warning',
                        )}
                      >
                        {PROVENANCE_LABEL[f.provenance]}
                      </span>
                    </div>
                  ))}
                </div>
                {lead.captured_fields.some((f) => !f.verified) && (
                  <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
                    Italic fields were inferred, not stated. Check before using them on a call.
                  </p>
                )}
              </>
            ) : (
              <p className="mt-1 text-sm text-muted-foreground">Nothing captured yet.</p>
            )}
          </section>

          <section>
            <h3 className="text-xs font-medium text-muted-foreground">Appointments</h3>
            <ul className="mt-2 space-y-2">
              {lead.appointments?.length ? (
                lead.appointments.map((a) => (
                  <li key={a.id} className="rounded-md border border-border p-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium">{dateTime(a.starts_at)}</span>
                      <span
                        className={clsx(
                          'rounded border px-1.5 py-0.5 text-[11px] font-medium',
                          a.status === 'confirmed'
                            ? 'border-success/30 bg-success/10 text-success'
                            : 'border-warning/30 bg-warning/10 text-warning',
                        )}
                      >
                        {a.status}
                      </span>
                    </div>
                    {a.vehicle && (
                      <p className="text-sm text-muted-foreground">{a.vehicle.title}</p>
                    )}
                  </li>
                ))
              ) : (
                <li className="text-sm text-muted-foreground">None yet.</li>
              )}
            </ul>
          </section>

          <section>
            <h3 className="text-xs font-medium text-muted-foreground">Conversations</h3>
            <ul className="mt-2 space-y-1">
              {lead.conversations?.length ? (
                lead.conversations.map((c) => (
                  <li key={c.id}>
                    <Link
                      to={`/app/conversations/${c.id}`}
                      className="text-sm text-primary hover:underline"
                    >
                      {c.channel === 'voice' ? 'Voice call' : 'Website chat'} --{' '}
                      {relative(c.started_at)}
                    </Link>
                  </li>
                ))
              ) : (
                <li className="text-sm text-muted-foreground">None.</li>
              )}
            </ul>
          </section>

          <section>
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-xs font-medium text-muted-foreground">Outreach</h3>
              {lead.email ? (
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    onClick={() => setComposing(composing === 'followup' ? null : 'followup')}
                  >
                    {composing === 'followup' ? 'Cancel' : 'Write to them'}
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      setComposing(composing === 'credit_application' ? null : 'credit_application')
                    }
                  >
                    {composing === 'credit_application' ? 'Cancel' : 'Credit application'}
                  </Button>
                </div>
              ) : (
                <Unavailable
                  label="Write to them"
                  why="No email on file, and SMS is out of scope, so this product has no way to reach them. A rep has to call."
                />
              )}
            </div>

            {composing && (
              <Composer lead={lead} kind={composing} onDone={() => setComposing(null)} />
            )}

            <ul className="mt-2 space-y-2">
              {lead.outreach?.length ? (
                lead.outreach.map((o) => (
                  <li key={o.id} className="rounded-md border border-border p-3">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-sm font-medium">{o.subject}</p>
                      {/* Only for a send that carried a link we can count. An
                          email with nothing trackable in it is not "unopened". */}
                      {o.trackable && (
                        <span
                          title={
                            o.opened
                              ? `Followed the link ${o.click_count} time${o.click_count === 1 ? '' : 's'}. Whether they finished the form happens on the dealership's own site and does not come back here.`
                              : 'The link has not been followed yet.'
                          }
                          className={clsx(
                            'shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium',
                            o.opened
                              ? 'border-success/30 bg-success-muted text-success'
                              : 'border-border text-muted-foreground',
                          )}
                        >
                          {o.opened ? 'Link opened' : 'Not opened'}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {o.channel === 'phone_logged' ? 'Call logged' : 'Email'} --{' '}
                      {dateTime(o.sent_at ?? o.created_at)}
                      {!o.delivered_externally && o.channel === 'email' && (
                        <> -- recorded locally, not delivered</>
                      )}
                    </p>
                    {o.error && <p className="mt-1 text-xs text-destructive">{o.error}</p>}
                  </li>
                ))
              ) : (
                <li className="text-sm text-muted-foreground">Nothing sent.</li>
              )}
            </ul>
          </section>

          <section>
            <h3 className="text-xs font-medium text-muted-foreground">History</h3>
            <div className="mt-2">
              <NotBacked
                title="No activity timeline"
                why="Nothing records per-lead events over time. The conversations and outreach above are the whole history the system holds."
              />
            </div>
          </section>
        </div>
      )}
    </Sheet>
  )
}

/** The two drafts a rep can send to a buyer, with the pick-one control.
 *
 *  Exported because the lead page needs it too: when every thread on a buyer
 *  is closed there is nothing to reply on -- Liner cannot open a chat with
 *  someone who is not on the site -- and email is the only way back to them.
 *  A second copy of the composer is how one of them quietly stops sending the
 *  server's draft and starts composing its own. */
export function LeadComposers({ lead, onDone }: { lead: Lead; onDone: () => void }) {
  const [kind, setKind] = useState<string>('followup')
  return (
    <div className="mt-2">
      <div className="flex gap-2">
        {(
          [
            ['followup', 'Follow-up'],
            ['credit_application', 'Credit application'],
          ] as const
        ).map(([key, label]) => (
          <Button
            key={key}
            size="sm"
            variant={kind === key ? 'primary' : 'secondary'}
            onClick={() => setKind(key)}
          >
            {label}
          </Button>
        ))}
      </div>
      <Composer lead={lead} kind={kind} onDone={onDone} />
    </div>
  )
}

/**
 * Lead-level outreach. The draft comes from the server, not from here -- it is
 * built out of the lead's real state, so a lead with a booked visit gets a
 * reminder naming the slot and everyone else gets a first touch that only
 * claims a car is available when it actually is.
 *
 * The rep can edit it before sending. What they send is what is stored.
 */
function Composer({
  lead,
  kind = 'followup',
  onDone,
}: {
  lead: Lead
  /** followup | credit_application. The server builds a different draft for
   *  each and stores which one was sent, so the overview can count credit
   *  applications without reading subject lines. */
  kind?: string
  onDone: () => void
}) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<{ subject: string; body: string } | null>(null)

  const { data: seed, error: draftError } = useQuery({
    queryKey: ['leads', lead.id, 'draft', kind],
    queryFn: () =>
      api.get<LeadDraft>(`/api/leads/${lead.id}/outreach?draft=1&kind=${kind}`),
    retry: false,
  })

  const send = useMutation({
    mutationFn: () =>
      api.post<Outreach>(`/api/leads/${lead.id}/outreach`, {
        subject: draft?.subject ?? seed?.subject,
        body: draft?.body ?? seed?.body,
        appointment_id: seed?.appointment_id ?? null,
        kind,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['leads', lead.id] })
      void queryClient.invalidateQueries({ queryKey: ['leads'] })
      onDone()
    },
  })

  // A credit application with no link configured cannot be drafted at all.
  // The server says which setting is missing; repeating that is more use
  // than a spinner that never resolves.
  const missing = (draftError as ApiError | null)?.notConfigured
  if (missing) {
    return (
      <div className="mt-2 rounded-md border border-warning/30 bg-warning-muted p-3">
        <p className="text-sm text-warning-foreground">{missing.detail}</p>
      </div>
    )
  }
  if (!seed) return <Spinner label="Drafting" />

  const value = draft ?? { subject: seed.subject, body: seed.body }
  const error = send.error as ApiError | null

  return (
    <div className="mt-2 rounded-md border border-border bg-muted/40 p-3">
      <p className="text-xs text-muted-foreground">
        {seed.kind === 'reminder'
          ? 'Reminder for their booked visit. Sent when you click, not on a schedule.'
          : seed.kind === 'credit_application'
            ? "The dealership's own finance application. It quotes no rate, term or approval -- none of that exists here."
            : 'First follow-up. It only offers a car that is genuinely on the lot.'}
      </p>

      <input
        value={value.subject}
        onChange={(e) => setDraft({ ...value, subject: e.target.value })}
        className="mt-2 h-9 w-full rounded-md border border-input bg-background px-3 text-sm shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />
      <textarea
        value={value.body}
        onChange={(e) => setDraft({ ...value, body: e.target.value })}
        rows={9}
        className="mt-2 w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />

      {error && <p className="mt-2 text-sm text-destructive">{error.message}</p>}

      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1">
        <Button
          variant="primary"
          size="sm"
          onClick={() => send.mutate()}
          disabled={send.isPending}
        >
          {send.isPending ? 'Sending...' : 'Send email'}
        </Button>
        <span className="min-w-0 truncate text-xs text-muted-foreground">to {lead.email}</span>
        <p className="w-full text-xs text-muted-foreground">
          Recorded either way -- delivery depends on the email integration.
        </p>
      </div>
    </div>
  )
}
