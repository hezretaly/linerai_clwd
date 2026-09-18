/**
 * Liner's own phone line.
 *
 * Three questions, and the page is laid out in the order somebody actually
 * asks them: is the line on, who answers it, and who has rung.
 *
 * **The setup half names variables rather than describing them.** "Twilio is
 * not configured" sends somebody to read three files; `TWILIO_ACCOUNT_SID is
 * missing` is a line they can act on. Same rule the email setup page follows,
 * and the same reason `/api/integrations` reports missing keys by name.
 *
 * **The webhook URLs are composed by the server, not typed here.** They have to
 * match what the signature is computed against, and a URL written into this
 * page is a second copy that drifts from the one the check uses -- which fails
 * every call with a message about signatures and no hint that an address is the
 * problem.
 */

import { useState } from 'react'
import clsx from 'clsx'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, ApiError } from '../../lib/api'
import { dateTime, relative } from '../../lib/format'
import { Badge, Button, Card, Empty, Field, Input, Spinner } from '../../components/ui'

interface PhoneCall {
  id: string
  direction: string
  from: string
  to: string
  persona: string
  status: string
  duration_sec: number
  conversation_id: string
  demo_request_id: string
  started_at: string
  ended_at: string | null
}

interface PhoneState {
  configured: boolean
  missing: string[]
  number: string
  ops_number: string
  persona: string
  personas: string[]
  model_ready: boolean
  webhooks: { voice: string; status: string; stream: string }
  greeting: string
  signature_checked: boolean
  /** Which credential outbound REST calls use: 'api_key' or 'auth_token'. */
  auth_source: string
  calls: PhoneCall[]
}

/** What each persona means, in the words somebody picking one would use.
 *  Held here rather than on the server because it is page copy; the closed
 *  vocabulary itself comes down in `personas` and is what the server enforces. */
const PERSONA: Record<string, { label: string; detail: string }> = {
  liner: {
    label: "Liner's own assistant",
    detail:
      'Answers as us. Explains what Liner does, finds out about their dealership, ' +
      'and books a demo straight into the calendar. It has no access to any ' +
      "dealership's inventory or buyers.",
  },
  dealership: {
    label: "The dealership's assistant",
    detail:
      'Answers as the showroom this install is set up for -- real inventory, real ' +
      'test-drive bookings, the same assistant a buyer gets on the website. This is ' +
      'the one to switch to when handing the number to a prospect mid-demo.',
  },
  off: {
    label: 'Nobody',
    detail:
      'Callers hear a short apology and the line hangs up. Nothing is billed.',
  },
}

function seconds(total: number): string {
  if (!total) return '--'
  const m = Math.floor(total / 60)
  return m ? `${m}m ${total % 60}s` : `${total}s`
}

export function OpsPhonePage() {
  const queryClient = useQueryClient()
  const [problem, setProblem] = useState('')
  const [dialling, setDialling] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['ops-phone'],
    queryFn: () => api.get<PhoneState>('/api/ops/phone'),
    // A call in progress changes this list without anybody clicking, and there
    // is no socket event worth adding for a page two people open occasionally.
    refetchInterval: 20_000,
  })

  const fresh = (next: PhoneState) => {
    queryClient.setQueryData(['ops-phone'], next)
    setProblem('')
  }

  const persona = useMutation({
    mutationFn: (value: string) =>
      api.post<PhoneState>('/api/ops/phone/persona', { value }),
    onSuccess: fresh,
    onError: (e: unknown) => setProblem(String((e as Error)?.message ?? e)),
  })

  const call = useMutation({
    mutationFn: (to: string) => api.post('/api/ops/phone/call', { to }),
    onSuccess: () => {
      setDialling('')
      void queryClient.invalidateQueries({ queryKey: ['ops-phone'] })
    },
    onError: (e: unknown) => {
      const err = e as ApiError
      setProblem(String((err?.payload as { detail?: string })?.detail ?? err?.message ?? e))
    },
  })

  if (isLoading || !data) return <Spinner />

  return (
    <div className="space-y-4 p-4 lg:p-6">
      <div>
        <h1 className="text-lg font-semibold">Phone</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Liner&apos;s own number. People ring it and an assistant answers; you can
          ring them from it too.
        </p>
      </div>

      {/* Status first, because every other control on the page is moot if the
          line is off -- and because "why did nothing happen" is the question
          this page exists to answer. */}
      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={data.configured ? 'success' : 'warning'}>
            {data.configured ? 'Line configured' : 'Not configured'}
          </Badge>
          {data.number && <span className="tnum text-sm font-medium">{data.number}</span>}
          {data.configured && !data.model_ready && (
            // Two separate facts, named separately. A configured line with no
            // model answers and then says nothing, and "the phone does not
            // work" has completely different answers for the two.
            <Badge tone="warning">No model -- callers reach a silent line</Badge>
          )}
          {!data.signature_checked && (
            <Badge tone="destructive">Signature check OFF</Badge>
          )}
        </div>

        {data.missing.length > 0 && (
          <div className="mt-3 rounded-md border border-warning/30 bg-warning-muted p-3">
            <p className="text-xs font-medium text-warning-foreground">
              Add these to <code className="font-mono">.env</code> and restart:
            </p>
            <ul className="mt-2 space-y-1">
              {data.missing.map((key) => (
                <li key={key} className="text-xs text-warning-foreground/90">
                  <code className="font-mono">{key}</code>
                  {HINT[key] ? <span className="ml-2 opacity-80">{HINT[key]}</span> : null}
                </li>
              ))}
            </ul>
          </div>
        )}

        {!data.model_ready && (
          <p className="mt-3 text-xs text-muted-foreground">
            The assistant also needs <code className="font-mono">LLM_MODE=live</code> and
            an API key. Without them the line answers and nobody speaks.
          </p>
        )}

        {/* Which credential outbound uses. Both work, so this is not a
            warning -- it is the one fact that decides what happens on the day
            somebody has to rotate a secret, and it is invisible otherwise. */}
        {data.configured && (
          <p className="mt-3 text-xs text-muted-foreground">
            {data.auth_source === 'api_key' ? (
              <>
                Outbound calls authenticate with an API key, so it can be revoked
                without touching the token that signs inbound webhooks.
              </>
            ) : (
              <>
                Outbound calls authenticate with the account auth token — the same
                secret that signs inbound webhooks. Setting{' '}
                <code className="font-mono">TWILIO_API_KEY_SID</code> and{' '}
                <code className="font-mono">TWILIO_API_KEY_SECRET</code> separates
                them, so revoking one does not stop the phone answering.
              </>
            )}
          </p>
        )}
      </Card>

      {/* Who answers. The control the demo actually uses. */}
      <Card className="p-4">
        <h2 className="text-sm font-semibold">Who answers</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Takes effect on the next call. A call already in progress keeps the
          assistant it started with.
        </p>
        <div className="mt-3 space-y-2">
          {data.personas.map((value) => {
            const copy = PERSONA[value] ?? { label: value, detail: '' }
            const active = data.persona === value
            return (
              <button
                key={value}
                disabled={persona.isPending}
                onClick={() => persona.mutate(value)}
                className={clsx(
                  'w-full rounded-lg border p-3 text-left transition-colors',
                  active
                    ? 'border-primary bg-primary/5'
                    : 'border-border bg-background hover:border-primary',
                )}
              >
                <span className="flex items-center gap-2">
                  <span
                    className={clsx(
                      'h-3.5 w-3.5 shrink-0 rounded-full border',
                      active ? 'border-[5px] border-primary' : 'border-border',
                    )}
                  />
                  <span className="text-sm font-medium">{copy.label}</span>
                </span>
                <span className="mt-1 block pl-[22px] text-xs leading-relaxed text-muted-foreground">
                  {copy.detail}
                </span>
              </button>
            )
          })}
        </div>
      </Card>

      {/* Where to point Twilio. Composed server-side; see the module note. */}
      <Card className="p-4">
        <h2 className="text-sm font-semibold">Point Twilio here</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          In the Twilio console, on your number, under Voice Configuration. These
          have to match exactly -- the signature covers the URL, so a trailing
          slash is a failed call.
        </p>
        <dl className="mt-3 space-y-2">
          {(
            [
              ['A call comes in (HTTP POST)', data.webhooks.voice],
              ['Call status changes (HTTP POST)', data.webhooks.status],
              ['Media stream (set for you, by the TwiML above)', data.webhooks.stream],
            ] as const
          ).map(([label, url]) => (
            <div key={label}>
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="mt-0.5 flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1 font-mono text-xs">
                  {url || '(set PUBLIC_BASE_URL)'}
                </code>
                {url && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void navigator.clipboard?.writeText(url)}
                  >
                    Copy
                  </Button>
                )}
              </dd>
            </div>
          ))}
        </dl>
      </Card>

      {/* Outbound. */}
      <Card className="p-4">
        <h2 className="text-sm font-semibold">Ring somebody</h2>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          {data.ops_number ? (
            <>
              Twilio rings <span className="tnum font-medium">{data.ops_number}</span> first
              and bridges them in when you pick up -- so they never sit listening
              to silence. No assistant is on this one.
            </>
          ) : (
            <>
              Set <code className="font-mono">TWILIO_OPS_NUMBER</code> to the handset
              that should ring first. Without it there is nothing here to bridge from.
            </>
          )}
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <div className="min-w-0 flex-1">
            <Field label="Their number">
              <Input
                value={dialling}
                onChange={(e) => setDialling(e.target.value)}
                placeholder="+15025550142"
                disabled={!data.configured || !data.ops_number}
              />
            </Field>
          </div>
          <Button
            variant="primary"
            disabled={
              !data.configured || !data.ops_number || !dialling.trim() || call.isPending
            }
            onClick={() => call.mutate(dialling.trim())}
          >
            {call.isPending ? 'Ringing you...' : 'Call'}
          </Button>
        </div>
      </Card>

      {problem && (
        <p className="text-sm text-destructive" role="alert">
          {problem}
        </p>
      )}

      {/* The log. */}
      <Card className="p-4">
        <h2 className="text-sm font-semibold">Calls</h2>
        {data.calls.length === 0 ? (
          <Empty
            title="No calls yet"
            hint="Every call on this number, in or out, lands here."
          />
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[34rem] text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th className="pb-2 pr-3 font-medium">When</th>
                  <th className="pb-2 pr-3 font-medium">Who</th>
                  <th className="pb-2 pr-3 font-medium">Answered by</th>
                  <th className="pb-2 pr-3 font-medium">Status</th>
                  <th className="pb-2 font-medium">Length</th>
                </tr>
              </thead>
              <tbody>
                {data.calls.map((row) => (
                  <tr key={row.id} className="border-b border-border/60 last:border-0">
                    <td className="py-2 pr-3 align-top">
                      <span className="block">{relative(row.started_at)}</span>
                      <span className="block text-xs text-muted-foreground">
                        {dateTime(row.started_at)}
                      </span>
                    </td>
                    <td className="py-2 pr-3 align-top">
                      <span className="tnum block">
                        {row.direction === 'in' ? row.from : row.to}
                      </span>
                      <span className="block text-xs text-muted-foreground">
                        {row.direction === 'in' ? 'called us' : 'we called'}
                      </span>
                    </td>
                    <td className="py-2 pr-3 align-top text-xs">
                      {/* Blank is meaningful: an outbound call had a person on
                          it, not an assistant, and writing a persona there
                          would put it in the same column as the ones the AI
                          handled. */}
                      {row.persona
                        ? PERSONA[row.persona]?.label ?? row.persona
                        : <span className="text-muted-foreground">a person</span>}
                    </td>
                    <td className="py-2 pr-3 align-top">
                      <Badge
                        tone={
                          row.status === 'completed'
                            ? 'success'
                            : row.status === 'failed'
                              ? 'destructive'
                              : 'neutral'
                        }
                      >
                        {row.status || 'unknown'}
                      </Badge>
                    </td>
                    <td className="tnum py-2 align-top">{seconds(row.duration_sec)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

/** One line each, for the variables somebody is least likely to guess. */
const HINT: Record<string, string> = {
  TWILIO_ACCOUNT_SID: 'Twilio console, top of the dashboard.',
  TWILIO_AUTH_TOKEN: 'Beside it. Also what inbound webhooks are signed with.',
  TWILIO_NUMBER: 'The number itself, E.164: +15025550100.',
  PUBLIC_BASE_URL: 'How this install is reached from outside. Twilio has to be able to reach it.',
  // Only ever listed when its other half is set, so the hint speaks to that.
  TWILIO_API_KEY_SID: 'The other half of the API key you started setting up.',
  TWILIO_API_KEY_SECRET: 'Shown once when the key is created. Both halves or neither.',
}
