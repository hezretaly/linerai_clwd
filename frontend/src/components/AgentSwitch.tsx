import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'

import { api } from '../lib/api'
import { relative } from '../lib/format'
import { Badge, Button, Card } from './ui'
import { Icon } from './Icon'

/* Whether Liner answers email, and the switch that decides it.
 *
 * **On the Liner setup page, not in the mailbox.** It is a decision about how
 * the assistant behaves, which is what that page is for -- the mailbox is for
 * reading mail. It sits at the top of the page rather than behind a
 * disclosure, because it is also the control somebody reaches for while the
 * inbox is being hammered, and one they have to go looking for is one they
 * will not find at three in the morning.
 */

/** Whether Liner is answering email, and every reason it might not be.
 *
 *  Three separate facts rather than one boolean, because they are fixed in
 *  three different places: `.env` needs a restart, the switch takes effect on
 *  the next delivery, and the model is a fourth variable entirely. Collapsed
 *  into "off" it sends whoever is reading to edit the wrong one -- which is
 *  exactly what happened: a deployment with `EMAIL_AGENT=true` and
 *  `LLM_MODE=live` set never replied, because the switch this card throws had
 *  no control anywhere on the dashboard and defaults off. */
export interface AgentState {
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
function Switch({
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


/** The card, and the queries behind it. Self-contained so the page that hosts
 *  it is one line -- it moved once already and should be able to move again. */
export function AgentSwitch() {
  const queryClient = useQueryClient()
  // Polled, because what it reports moves on its own: a queued reply comes
  // due, and the hourly ceiling can throw the switch with nobody touching it.
  const { data } = useQuery({
    queryKey: ['email-agent'],
    queryFn: () => api.get<AgentState>('/api/email/agent'),
    refetchInterval: 5 * 60 * 1000,
    refetchOnWindowFocus: true,
  })
  const toggle = useMutation({
    mutationFn: (value: 'on' | 'off') => api.post<AgentState>('/api/email/agent', { value }),
    onSuccess: (state) => queryClient.setQueryData(['email-agent'], state),
  })
  return (
    <Switch
      state={data}
      busy={toggle.isPending}
      onToggle={(value) => toggle.mutate(value)}
    />
  )
}
