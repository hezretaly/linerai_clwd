import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, ApiError } from '../lib/api'
import { withStore } from '../lib/store'
import type { AssistantSettings, HandoffRule, KnowledgeEntry, Rail } from '../lib/types'
import { Badge, Button, Card, Empty, Spinner, Switch, Tabs } from '../components/ui'
import { AgentSwitch } from '../components/AgentSwitch'
import { PageHeader } from '../components/dashboard/AppShell'

interface SettingsPayload {
  live: AssistantSettings
  draft: AssistantSettings | null
  has_unpublished_changes: boolean
  compiled_prompt: string
  prompt: PromptPayload
}

/** The assistant's wording: ours, and this dealership's where it has its own.
 *  `""` in `draft`/`live` means that version uses the default. */
interface PromptPayload {
  defaults: { brief: string; rules: string }
  live: { brief: string; rules: string }
  draft: { brief: string; rules: string }
  max_chars: number
}

export function AssistantPage() {
  const [tab, setTab] = useState('behaviour')
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['assistant-settings'],
    queryFn: () => api.get<SettingsPayload>('/api/assistant-settings'),
  })

  const publish = useMutation({
    mutationFn: () => api.post('/api/assistant-settings/publish'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['assistant-settings'] }),
  })

  if (isLoading || !data) return <Spinner />

  return (
    <>
      <PageHeader
        title="Liner setup"
        subtitle={`Liner is running version ${data.live.version}`}
      />

      {/* Any edit is a draft until published -- otherwise a tweak silently
          changes buyer-facing behaviour mid-conversation. */}
      {data.has_unpublished_changes && (
        <div className="flex items-center justify-between gap-4 border-b border-warning/30 bg-warning-muted px-6 py-2.5">
          <p className="text-sm text-warning-foreground">
            You have unpublished changes in version {data.draft?.version}. Liner is still
            running version {data.live.version}.
          </p>
          <Button
            variant="primary"
            size="sm"
            onClick={() => publish.mutate()}
            disabled={publish.isPending}
          >
            Publish version {data.draft?.version}
          </Button>
        </div>
      )}

      <div className="p-6">
        {/* **Whether Liner answers email is a decision about the assistant**,
            so it belongs on the page that decides how the assistant behaves --
            not in the mailbox, which is for reading mail. Above the tabs
            rather than inside one: it is also the switch somebody reaches for
            while an inbox is being hammered, and one behind a tab is one they
            have to already know about. */}
        <div className="mb-6">
          <AgentSwitch />
        </div>

        {/* The way in to the buyer's own surfaces, from the page that decides
            how they behave. A button rather than a URL somebody has to
            remember -- `?diagnostics=1` is exactly the kind of thing that
            gets written in a runbook and then goes stale.
            `withStore` because a raw href never passes through the router, so
            without it both of these open the *default* store's assistant. */}
        <div className="mb-6 flex flex-wrap items-center gap-3">
          <span className="text-sm text-muted-foreground">Try it as a buyer:</span>
          {/* Carries the flag too: the scripted-assistant banner is what
              tells somebody setting this up that a canned reply is canned,
              and a buyer on a dealership's own website must never read it. */}
          <a
            href={withStore('/chat?diagnostics=1')}
            target="_blank"
            rel="noreferrer"
            className="text-sm font-medium text-primary hover:underline"
          >
            Open the chat (with diagnostics)
          </a>
          {/* Carries the flag: a manager checking setup wants the transcript
              and, if the line is not answering, the variables that are unset.
              A buyer opening the same page gets neither. */}
          <a
            href={withStore('/call?diagnostics=1')}
            target="_blank"
            rel="noreferrer"
            className="text-sm font-medium text-primary hover:underline"
          >
            Try a call (with diagnostics)
          </a>
        </div>

        <Card>
          <div className="px-4 pt-2">
            <Tabs
              active={tab}
              onChange={setTab}
              tabs={[
                { id: 'behaviour', label: 'Behaviour' },
                { id: 'handoff', label: 'Handoff rules' },
                { id: 'knowledge', label: 'Knowledge' },
                { id: 'rails', label: 'Rails' },
                { id: 'advanced', label: 'Advanced' },
              ]}
            />
          </div>

          <div className="p-4">
            {tab === 'behaviour' && <Behaviour data={data} />}
            {tab === 'handoff' && <HandoffRules />}
            {tab === 'knowledge' && <Knowledge />}
            {tab === 'rails' && <Rails />}
            {tab === 'advanced' && (
              <Advanced prompt={data.compiled_prompt} wording={data.prompt} />
            )}
          </div>
        </Card>
      </div>
    </>
  )
}

const OPTIONS = {
  tone: ['warm', 'neutral', 'energetic'],
  push_level: ['gentle', 'balanced', 'assertive'],
  price_mode: ['listed_only', 'range_ok'],
  financing_mode: ['refer_to_rep', 'general_info'],
}

function Behaviour({ data }: { data: SettingsPayload }) {
  const queryClient = useQueryClient()
  const current = data.draft ?? data.live

  const patch = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      api.patch('/api/assistant-settings', payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['assistant-settings'] }),
  })

  return (
    <div className="max-w-2xl space-y-5">
      {(Object.keys(OPTIONS) as (keyof typeof OPTIONS)[]).map((key) => (
        <div key={key}>
          <p className="text-sm font-medium capitalize">{key.replace('_', ' ')}</p>
          <div className="mt-1.5 inline-flex rounded-lg border border-border p-0.5">
            {OPTIONS[key].map((option) => (
              <button
                key={option}
                onClick={() => patch.mutate({ [key]: option })}
                className={
                  current[key] === option
                    ? 'rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground'
                    : 'rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground'
                }
              >
                {option.replace('_', ' ')}
              </button>
            ))}
          </div>
        </div>
      ))}

      <div>
        <p className="text-sm font-medium">Greeting</p>
        <p className="mt-1 rounded-lg bg-muted px-3 py-2 text-sm">{current.greeting}</p>
      </div>

      <div>
        <p className="text-sm font-medium">Appointment length</p>
        <p className="mt-1 text-sm text-muted-foreground">
          {current.booking_slot_length} minutes
        </p>
      </div>

      {/* Set apart from the fields above, because it does not work like them:
          those are drafted and published, and this is live on Save. */}
      <div className="border-t border-border pt-5">
        <p className="text-sm font-medium">Credit application link</p>
        <p className="mt-1 text-sm text-muted-foreground">
          The dealership's own finance application. Liner offers it to buyers who ask about
          financing, and it goes out on a lead's Credit application email. It takes effect
          as soon as you save it -- no publishing needed. Until it is set there is nothing to
          send, and the overview's Credit applications card says so.
        </p>
        <CreditLink saved={data.live.credit_application_url} />
      </div>
    </div>
  )
}

/** The finance application link: Save and it is live.
 *
 *  It used to save on blur and say nothing, and then it saved into the draft
 *  and said "publish to put it in front of buyers" -- a manager who wanted to
 *  set a link was being asked to publish a version of the assistant. It is a
 *  fact about the dealership, not a change to how Liner talks, so it has its
 *  own endpoint and goes live on Save. `saved` is the live value, because that
 *  is the one a buyer is sent to.
 *
 *  **A manager's**, like Publish: it takes effect at once. A rep sees the link
 *  and no button, rather than a Save that can only come back 403. */
function CreditLink({ saved }: { saved: string }) {
  const queryClient = useQueryClient()
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<{ user: { role: string } }>('/api/auth/me'),
    staleTime: 5 * 60_000,
  })
  const manager = me?.user.role === 'manager'
  const [value, setValue] = useState(saved)
  const [done, setDone] = useState(false)
  useEffect(() => setValue(saved), [saved])

  const save = useMutation({
    mutationFn: (next: string) =>
      api.put('/api/assistant-settings/credit-application-url', { url: next }),
    onSuccess: () => {
      setDone(true)
      queryClient.invalidateQueries({ queryKey: ['assistant-settings'] })
      // The overview's Credit applications card reads whether one is set.
      queryClient.invalidateQueries({ queryKey: ['overview'] })
    },
  })

  const next = value.trim()
  const valid = next === '' || /^https:\/\/\S+$/i.test(next)
  const changed = next !== saved

  return (
    <form
      className="mt-2"
      onSubmit={(e) => {
        e.preventDefault()
        if (valid && changed) save.mutate(next)
      }}
    >
      <div className="flex gap-2">
        <input
          type="url"
          aria-label="Credit application link"
          value={value}
          placeholder="https://..."
          readOnly={!manager}
          onChange={(e) => {
            setValue(e.target.value)
            setDone(false)
          }}
          className="h-9 min-w-0 flex-1 rounded-md border border-input bg-background px-3 text-sm shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring read-only:bg-muted"
        />
        {manager && (
          <Button type="submit" size="sm" disabled={!valid || !changed || save.isPending}>
            {save.isPending ? 'Saving...' : 'Save'}
          </Button>
        )}
      </div>
      {!manager ? (
        <p className="mt-1.5 text-xs text-muted-foreground">Only a manager can change it.</p>
      ) : !valid ? (
        <p className="mt-1.5 text-xs text-destructive">Use a full https:// address.</p>
      ) : save.isError ? (
        <p className="mt-1.5 text-xs text-destructive">
          Not saved: {save.error instanceof Error ? save.error.message : 'the request failed'}
        </p>
      ) : done && !changed ? (
        <p className="mt-1.5 text-xs text-success">
          {saved
            ? 'Saved. Liner offers this link to buyers from now on.'
            : 'Removed. Liner stops offering a credit application.'}
        </p>
      ) : null}
    </form>
  )
}

function HandoffRules() {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ['handoff-rules'],
    queryFn: () => api.get<{ rules: HandoffRule[] }>('/api/handoff-rules'),
  })

  const patch = useMutation({
    mutationFn: ({ id, ...payload }: { id: string } & Record<string, unknown>) =>
      api.patch(`/api/handoff-rules/${id}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['handoff-rules'] }),
  })

  if (!data) return <Spinner />

  return (
    <ul className="space-y-3">
      {data.rules.map((rule) => (
        <li key={rule.id} className="rounded-lg border border-border p-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="text-sm font-medium">{rule.label}</p>
              <p className="mt-0.5 text-sm text-muted-foreground">{rule.description}</p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Badge tone="neutral">Routes to {rule.route_target.replace('_', ' ')}</Badge>
                <Badge tone="neutral">
                  {rule.notify === 'email_dashboard' ? 'Email + dashboard' : 'Dashboard only'}
                </Badge>
                {rule.threshold_value && (
                  <Badge tone="neutral">
                    Within {rule.threshold_value} {rule.threshold_unit}
                  </Badge>
                )}
                <span className="text-xs text-muted-foreground">
                  Fired {rule.fired_count} times
                </span>
              </div>
            </div>
            <Switch
              checked={rule.enabled}
              onChange={(value) => patch.mutate({ id: rule.id, enabled: value })}
              label={rule.label}
            />
          </div>
          {!rule.enabled && (
            <p className="mt-3 rounded bg-warning-muted px-3 py-2 text-sm text-warning-foreground">
              Liner will keep going in those situations instead of stopping and asking for a
              person.
            </p>
          )}
        </li>
      ))}
    </ul>
  )
}

function Knowledge() {
  const { data } = useQuery({
    queryKey: ['knowledge'],
    queryFn: () => api.get<{ entries: KnowledgeEntry[] }>('/api/knowledge'),
  })
  if (!data) return <Spinner />

  return (
    <>
      <p className="mb-3 text-sm text-muted-foreground">
        What the listings don't cover. These are injected into the assistant's instructions, so
        it answers from your policy instead of guessing.
      </p>
      <ul className="space-y-3">
        {data.entries.map((entry) => (
          <li key={entry.id} className="rounded-lg border border-border p-4">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-medium">{entry.topic}</p>
              <span className="text-xs text-muted-foreground">Used {entry.use_count}x</span>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">{entry.answer}</p>
          </li>
        ))}
      </ul>
    </>
  )
}

function Rails() {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ['rails'],
    queryFn: () => api.get<{ rails: Rail[] }>('/api/rails'),
  })

  const patch = useMutation({
    mutationFn: ({ id, ...payload }: { id: string } & Record<string, unknown>) =>
      api.patch(`/api/rails/${id}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['rails'] }),
  })

  if (!data) return <Spinner />
  if (!data.rails.length) return <Empty title="No rails" />

  const groups = data.rails.reduce<Record<string, Rail[]>>((acc, rail) => {
    const key = rail.kind === 'knowledge' ? 'knowledge' : `${rail.kind}: ${rail.stage}`
    ;(acc[key] ??= []).push(rail)
    return acc
  }, {})

  return (
    <>
      <p className="mb-3 text-sm text-muted-foreground">
        Tappable prompts under the buyer's composer. Tapping one sends its text as an ordinary
        message, so the transcript reads the same whether the buyer typed or tapped.
      </p>
      <div className="space-y-5">
        {Object.entries(groups).map(([group, rails]) => (
          <section key={group}>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {group.replace('_', ' ')}
            </h3>
            <ul className="mt-2 space-y-1.5">
              {rails.map((rail) => (
                <li
                  key={rail.id}
                  className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{rail.label}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      Sends: "{rail.message_text}"
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {rail.advances_to && (
                      <Badge tone="neutral">to {rail.advances_to.replace('_', ' ')}</Badge>
                    )}
                    <Switch
                      checked={rail.enabled}
                      onChange={(value) => patch.mutate({ id: rail.id, enabled: value })}
                      label={rail.label}
                    />
                  </div>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </>
  )
}

/** The assistant's own instructions, editable, and what they compile to.
 *
 *  **Two boxes, because they are two different things.** The brief is the
 *  job -- who Liner is and what every turn is for -- and the rules are what
 *  the tools cannot hold on their own. Both start from the product's text,
 *  and emptying one (Reset to default) hands it back to that text, which
 *  keeps improving; a saved copy would not.
 *
 *  Saved to the draft like everything else on this page, so nothing reaches
 *  a buyer until it is published. And the page says plainly what no wording
 *  here can change, because the answer to "can I make it quote a discount?"
 *  is no, and that is a feature. */
function Advanced({ prompt, wording }: { prompt: string; wording: PromptPayload }) {
  const queryClient = useQueryClient()
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<{ user: { role: string } }>('/api/auth/me'),
    staleTime: 5 * 60_000,
  })
  const manager = me?.user.role === 'manager'
  const [brief, setBrief] = useState(wording.draft.brief || wording.defaults.brief.trim())
  const [rules, setRules] = useState(wording.draft.rules || wording.defaults.rules.trim())
  const [problem, setProblem] = useState('')
  const [saved, setSaved] = useState(false)

  const save = useMutation({
    mutationFn: () => api.put('/api/assistant-settings/prompt', { brief, rules }),
    onSuccess: () => {
      setProblem('')
      setSaved(true)
      queryClient.invalidateQueries({ queryKey: ['assistant-settings'] })
    },
    onError: (err: unknown) => {
      setSaved(false)
      const payload = (err as ApiError)?.payload as { detail?: string } | undefined
      setProblem(payload?.detail || String((err as Error)?.message ?? err))
    },
  })

  const used = (brief === wording.defaults.brief.trim() ? 0 : brief.trim().length)
    + (rules === wording.defaults.rules.trim() ? 0 : rules.trim().length)
  const box = (
    label: string,
    hint: string,
    value: string,
    set: (v: string) => void,
    fallback: string,
  ) => (
    <div className="min-w-0">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <label className="text-sm font-medium">{label}</label>
        {value.trim() !== fallback.trim() ? (
          <button
            type="button"
            disabled={!manager}
            onClick={() => { set(fallback.trim()); setSaved(false) }}
            className="text-xs text-primary hover:underline disabled:opacity-50"
          >
            Reset to default
          </button>
        ) : (
          <span className="text-xs text-muted-foreground">Liner&apos;s default</span>
        )}
      </div>
      <p className="mb-1.5 text-xs text-muted-foreground">{hint}</p>
      <textarea
        value={value}
        readOnly={!manager}
        onChange={(e) => { set(e.target.value); setSaved(false) }}
        rows={14}
        className="w-full resize-y rounded-md border border-input bg-background p-2 font-mono text-xs leading-relaxed outline-none focus:border-ring focus:ring-1 focus:ring-ring"
      />
    </div>
  )

  return (
    <>
      <p className="mb-3 text-sm text-muted-foreground">
        What Liner is told at the start of every conversation. Changes are saved to the draft
        and reach buyers when the draft is published.
        {!manager && ' Only a manager can change it.'}
      </p>
      <div className="mb-3 rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
        What no wording here can change: Liner still quotes only prices and cars its tools
        return, never offers a sold car, never books a clashing time, and marks anything a buyer
        did not say as a guess. Those are enforced in code, so a rewrite changes how Liner
        talks, not what it may claim. Placeholders such as {'{{DEALER_NAME}}'} are filled in.
      </div>
      <div className="grid grid-cols-1 gap-4">
        {box(
          'Brief',
          'The job: who Liner is and what every turn is for.',
          brief, setBrief, wording.defaults.brief,
        )}
        {box(
          'Operating rules',
          'What Liner must and must not do here, beyond what the tools enforce.',
          rules, setRules, wording.defaults.rules,
        )}
      </div>
      {problem && <p className="mt-2 text-xs text-destructive">{problem}</p>}
      {manager && (
        <div className="mt-3 flex flex-wrap items-center justify-end gap-3">
          <span
            className={
              used > wording.max_chars ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'
            }
          >
            {used > 0
              ? `${used.toLocaleString()} of ${wording.max_chars.toLocaleString()} characters of your own`
              : "Using Liner's default wording"}
          </span>
          {saved && <span className="text-xs text-muted-foreground">Saved to the draft</span>}
          <Button size="sm" variant="primary" onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending ? 'Saving...' : 'Save to draft'}
          </Button>
        </div>
      )}

      <details className="mt-6">
        <summary className="cursor-pointer text-sm font-medium">
          The whole prompt Liner is running on now
        </summary>
        <p className="mb-2 mt-2 text-xs text-muted-foreground">
          The published version, assembled from the wording above plus the dealership&apos;s facts,
          its settings and its knowledge base. Read-only.
        </p>
        <pre className="max-h-[32rem] overflow-auto rounded-lg bg-muted p-4 text-xs leading-relaxed whitespace-pre-wrap">
          {prompt}
        </pre>
      </details>
    </>
  )
}
