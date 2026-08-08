import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../lib/api'
import type { AssistantSettings, HandoffRule, KnowledgeEntry, Rail } from '../lib/types'
import { Badge, Button, Card, Empty, Spinner, Switch, Tabs } from '../components/ui'
import { PageHeader } from '../components/dashboard/AppShell'

interface SettingsPayload {
  live: AssistantSettings
  draft: AssistantSettings | null
  has_unpublished_changes: boolean
  compiled_prompt: string
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
            {tab === 'advanced' && <Advanced prompt={data.compiled_prompt} />}
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

      <div>
        <p className="text-sm font-medium">Credit application link</p>
        <p className="mt-1 text-sm text-muted-foreground">
          The dealership's own finance application. Until this is set there is nothing
          to send, so the action on a lead says so rather than mailing an invitation
          to apply nowhere -- and the overview's Credit applications card says the same.
        </p>
        <input
          type="url"
          defaultValue={current.credit_application_url}
          placeholder="https://..."
          onBlur={(e) => {
            const next = e.target.value.trim()
            if (next !== current.credit_application_url) {
              patch.mutate({ credit_application_url: next })
            }
          }}
          className="mt-2 h-9 w-full rounded-md border border-input bg-background px-3 text-sm shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>
    </div>
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

function Advanced({ prompt }: { prompt: string }) {
  return (
    <>
      <p className="mb-3 text-sm text-muted-foreground">
        Read-only. This is literally what the assistant is told, assembled from the settings
        above plus your knowledge base.
      </p>
      <pre className="max-h-[32rem] overflow-auto rounded-lg bg-muted p-4 text-xs leading-relaxed whitespace-pre-wrap">
        {prompt}
      </pre>
    </>
  )
}
