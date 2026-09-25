import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, ApiError } from '../lib/api'
import { withStore } from '../lib/store'
import type { AssistantSettings, HandoffRule, KnowledgeEntry, Rail } from '../lib/types'
import { Badge, Button, Card, Empty, Spinner, Switch, Tabs } from '../components/ui'
import { AgentSwitch } from '../components/AgentSwitch'
import { WebsiteChatCard } from '../components/WebsiteChat'
import { PageHeader } from '../components/dashboard/AppShell'

interface SettingsPayload {
  live: AssistantSettings
  draft: AssistantSettings | null
  has_unpublished_changes: boolean
  prompt: PromptPayload
}

/** The dealership's one prompt. `""` in `draft`/`live` means that version
 *  uses Liner's own, which is `default`. */
interface PromptPayload {
  default: string
  live: string
  draft: string
  max_chars: number
  /** The whole prompt Liner is handed, per channel. */
  prompt_max: number
  /** The most the box can hold on this store right now, measured by the
   *  server: the smaller of `max_chars` and what `prompt_max` leaves. */
  room: number
}

/**
 * Liner setup: what Liner is told, and how it behaves.
 *
 * **One assistant, one prompt.** Liner answers the website chat, the phone
 * and (when switched on) email, and a manager tells it how to treat their
 * buyers in one box of plain words. How it adapts to each of those -- no
 * screen on a call, no card in an inbox -- and the writing assistant a rep
 * presses Auto-generate or Polish on are the product's and not on this page.
 *
 * **Two kinds of setting, kept visibly apart.** Everything in the tabs is
 * drafted and reaches a buyer when the draft is published, so a tweak cannot
 * change behaviour mid-conversation. The two cards above them are not: the
 * email switch takes effect on the next delivery, and the credit application
 * link is a fact about the dealership that goes live on Save.
 */
export function AssistantPage() {
  const [tab, setTab] = useState('instructions')
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

      {/* Any edit in the tabs is a draft until published -- otherwise a tweak
          silently changes buyer-facing behaviour mid-conversation. */}
      {data.has_unpublished_changes && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-warning/30 bg-warning-muted px-6 py-2.5">
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

      <div className="space-y-6 p-4 sm:p-6">
        {/* **Live the moment they change**, and set apart from the drafted
            tabs below so nobody goes looking for a Publish button. Whether
            Liner answers email is also the switch somebody reaches for while
            an inbox is being hammered, so it is not behind a tab. */}
        <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
          <AgentSwitch />
          <CreditLinkCard saved={data.live.credit_application_url} />
        </div>

        {/* Live too: the switch hides the bubble on every site within a
            minute, and the rest is what the tag has reported from them. */}
        <WebsiteChatCard />

        <Card>
          <div className="px-4 pt-2">
            <Tabs
              active={tab}
              onChange={setTab}
              tabs={[
                { id: 'instructions', label: 'Instructions' },
                { id: 'behaviour', label: 'Behaviour' },
                { id: 'handoff', label: 'Handoff rules' },
                { id: 'knowledge', label: 'Knowledge' },
                { id: 'rails', label: 'Rails' },
              ]}
            />
          </div>

          <div className="p-4">
            {tab === 'instructions' && <Instructions wording={data.prompt} />}
            {tab === 'behaviour' && <Behaviour data={data} />}
            {tab === 'handoff' && <HandoffRules />}
            {tab === 'knowledge' && <Knowledge />}
            {tab === 'rails' && <Rails />}
          </div>
        </Card>

        {/* The way in to the buyer's own surfaces, from the page that decides
            how they behave. A button rather than a URL somebody has to
            remember -- `?diagnostics=1` is exactly the kind of thing that
            gets written in a runbook and then goes stale. `withStore` because
            a raw href never passes through the router, so without it both of
            these open the *default* store's assistant. */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
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

    </div>
  )
}

/** Its own card, beside the email switch rather than among the drafted
 *  fields, because it does not work like them: it is live on Save. */
function CreditLinkCard({ saved }: { saved: string }) {
  return (
    <Card className="p-4">
      <p className="text-sm font-semibold">Credit application link</p>
      <p className="mt-1 text-sm text-muted-foreground">
        The dealership's own finance application. Liner offers it to buyers who ask about
        financing, and it goes out on a lead's Credit application email. It takes effect as
        soon as you save it -- no publishing needed.
      </p>
      <CreditLink saved={saved} />
    </Card>
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

/** How near the limit the box has to be before the page mentions length at
 *  all. Below it a count is noise to somebody writing a few sentences; the
 *  limit is what `room` says, measured by the server on this store. */
const NEAR_LIMIT = 0.85

/**
 * What Liner is told about how to treat buyers: one box, in plain words.
 *
 * **One text, because there is one assistant.** It used to be six boxes -- a
 * brief, operating rules, one per channel and the writing assistant's -- and
 * a manager who is not technical was being asked to edit tool mechanics to
 * change how Liner sounds. How a call differs from the chat, what an email
 * needs, and the rules that keep Liner honest are the product's, added after
 * these words and never shown here.
 *
 * **The box starts from the text in use** -- the draft's own, or Liner's --
 * and Reset puts Liner's back. Saving Liner's verbatim stores nothing, so a
 * dealership that never edits it keeps following the default as it improves.
 * Save lands on the draft; the Publish button at the top of the page is what
 * puts it in front of buyers, like every other change here.
 *
 * **Length is mentioned only near the limit**, and the limit is `room`: what
 * this store's facts and knowledge answers leave of the whole prompt, as the
 * server measures it. A counter under every keystroke is jargon to somebody
 * writing four sentences.
 */
function Instructions({ wording }: { wording: PromptPayload }) {
  const queryClient = useQueryClient()
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<{ user: { role: string } }>('/api/auth/me'),
    staleTime: 5 * 60_000,
  })
  const manager = me?.user.role === 'manager'

  const fallback = wording.default.trim()
  /** What the box shows: the draft's own words, else Liner's. */
  const inUse = wording.draft || fallback
  const [text, setText] = useState(inUse)
  const [problem, setProblem] = useState('')
  const [saved, setSaved] = useState(false)

  const dirty = text.trim() !== inUse.trim()
  const isDefault = text.trim() === fallback
  const length = text.trim().length
  const over = length - wording.room

  const save = useMutation({
    mutationFn: () => api.put('/api/assistant-settings/prompt', { prompt: text }),
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

  return (
    <div className="min-w-0 max-w-3xl space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label htmlFor="assistant-prompt" className="text-base font-semibold">
          How Liner talks to your buyers
        </label>
        <div className="flex flex-wrap items-center gap-2">
          {wording.draft !== wording.live && <Badge tone="warning">Not published yet</Badge>}
          {wording.draft && <Badge tone="neutral">Edited</Badge>}
        </div>
      </div>
      <p className="text-sm text-muted-foreground">
        Liner uses these instructions on your website chat, on phone calls and in email. It
        adapts them for each by itself.
      </p>

      <textarea
        id="assistant-prompt"
        value={text}
        readOnly={!manager}
        rows={16}
        onChange={(e) => {
          setText(e.target.value)
          setSaved(false)
        }}
        className="w-full resize-y rounded-md border border-input bg-background p-3 text-sm leading-relaxed outline-none focus:border-ring focus:ring-1 focus:ring-ring read-only:bg-muted/40"
      />

      {over > 0 ? (
        <p className="text-sm text-destructive">
          This is {over.toLocaleString()} characters too long to save. Try shortening it a
          little.
        </p>
      ) : length >= wording.room * NEAR_LIMIT ? (
        <p className="text-sm text-muted-foreground">
          Nearly full: {length.toLocaleString()} of {wording.room.toLocaleString()} characters.
          Shorter instructions work best.
        </p>
      ) : null}
      {problem && <p className="text-sm text-destructive">{problem}</p>}

      {manager ? (
        <div className="flex flex-wrap items-center justify-end gap-3">
          {saved && !dirty && (
            <span className="text-sm text-success">
              Saved. Press Publish at the top of the page to put it live.
            </span>
          )}
          {!isDefault && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setText(fallback)
                setSaved(false)
                setProblem('')
              }}
            >
              Reset to Liner&apos;s default
            </Button>
          )}
          <Button
            size="sm"
            variant="primary"
            onClick={() => save.mutate()}
            disabled={!dirty || over > 0 || save.isPending}
          >
            {save.isPending ? 'Saving...' : 'Save'}
          </Button>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">Only a manager can change these.</p>
      )}
    </div>
  )
}
