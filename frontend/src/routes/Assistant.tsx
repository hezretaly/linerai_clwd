import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, ApiError } from '../lib/api'
import { withStore } from '../lib/store'
import type { AssistantSettings, HandoffRule, KnowledgeEntry, Rail } from '../lib/types'
import { Badge, Button, Card, Empty, Spinner, Switch, Tabs } from '../components/ui'
import { AgentSwitch } from '../components/AgentSwitch'
import { WebsiteChatCard } from '../components/WebsiteChat'
import { Icon, type IconName } from '../components/Icon'
import { PageHeader } from '../components/dashboard/AppShell'

interface SettingsPayload {
  live: AssistantSettings
  draft: AssistantSettings | null
  has_unpublished_changes: boolean
  compiled_prompt: string
  /** What each assistant is told now, from the published version. */
  compiled: Record<'chat' | 'voice' | 'email' | 'composer', string>
  prompt: PromptPayload
}

type PartKey = 'brief' | 'rules' | 'chat' | 'voice' | 'email' | 'composer'
type OwnPart = 'chat' | 'voice' | 'email' | 'composer'

/** The assistants' wording: ours, and this dealership's where it has its own.
 *  `""` in `draft`/`live` means that version uses the default. */
interface PromptPayload {
  defaults: Record<PartKey, string>
  live: Record<PartKey, string>
  draft: Record<PartKey, string>
  /** The brief and rules together. */
  max_chars: number
  part_max: Record<OwnPart, number>
  /** Every assistant's whole assembled prompt. */
  prompt_max: number
}

/**
 * Liner setup: what the assistants are told, and how they behave.
 *
 * **Four assistants, one page.** Liner answers the website chat, the phone
 * and (when switched on) email, and it writes for the team when a rep presses
 * Auto-generate or Polish. They share a brief and its rules; each has
 * instructions of its own on top, and each of those is editable here -- they
 * used to be constants, so a manager could rewrite what every conversation
 * starts from but not how Liner talks on the phone.
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
            {tab === 'instructions' && (
              <Instructions wording={data.prompt} compiled={data.compiled} />
            )}
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

type Channel = 'chat' | 'voice' | 'email'

/** The assistants, in the order a manager thinks of them. `channels` is which
 *  assembled prompts the wording lands in, for the length shown beside it. */
const ASSISTANTS: {
  key: string
  label: string
  icon: IconName
  where: string
  parts: PartKey[]
  channels: Channel[]
}[] = [
  {
    key: 'shared',
    label: 'Every assistant',
    icon: 'sliders',
    where:
      'The brief and rules the website chat, phone calls and email replies all start from. ' +
      'Each of them adds its own instructions below.',
    parts: ['brief', 'rules'],
    channels: ['chat', 'voice', 'email'],
  },
  {
    key: 'chat',
    label: 'Website chat',
    icon: 'chat',
    where:
      'Added for the chat on your website and storefront: keeping replies short, the ' +
      'contact form and the booking card on the buyer’s screen.',
    parts: ['chat'],
    channels: ['chat'],
  },
  {
    key: 'voice',
    label: 'Phone calls',
    icon: 'phone',
    where:
      'Added when Liner answers a call: words only, one question at a time, reading a ' +
      'number back before saving it, and hanging up.',
    parts: ['voice'],
    channels: ['voice'],
  },
  {
    key: 'email',
    label: 'Email replies',
    icon: 'mail',
    where:
      'Added when Liner answers a buyer’s email on its own -- only while the Email ' +
      'replies switch above is on.',
    parts: ['email'],
    channels: ['email'],
  },
  {
    key: 'composer',
    label: 'Writing assistant',
    icon: 'pencil',
    where:
      'What your team gets from Auto-generate and Polish on emails, texts and chat ' +
      'replies. It writes as the person pressing the button, under their name.',
    parts: ['composer'],
    channels: [],
  },
]

const PART_LABEL: Record<PartKey, { label: string; hint: string }> = {
  brief: { label: 'Brief', hint: 'The job: who Liner is and what every turn is for.' },
  rules: {
    label: 'Operating rules',
    hint: 'What Liner must and must not do here, beyond what the tools enforce.',
  },
  chat: { label: 'Website chat instructions', hint: '' },
  voice: { label: 'Phone call instructions', hint: '' },
  email: { label: 'Email reply instructions', hint: '' },
  composer: { label: 'Writing assistant instructions', hint: '' },
}

const CHANNEL_NAME: Record<Channel, string> = {
  chat: 'Website chat',
  voice: 'Phone calls',
  email: 'Email replies',
}

/**
 * What each assistant is told, one editor per assistant.
 *
 * **A list on the left and one assistant at a time on the right**, because
 * the texts are long and a page of six stacked boxes is one nobody reads. The
 * list says which ones are the product's wording and which are yours, and
 * which have an edit waiting to be published.
 *
 * **Every box starts from the text in use** -- the draft's own wording, or
 * ours -- and Reset to default puts ours back. Saving ours verbatim stores
 * nothing, so an untouched box keeps following the product as it improves.
 *
 * **Lengths are said in the unit that costs.** Each box has its own ceiling,
 * and under it the whole prompt that assistant would be handed, against the
 * 12,000 every turn re-reads. The server measures that exactly on save; the
 * figure here is the published prompt adjusted by what you have typed.
 */
function Instructions({
  wording,
  compiled,
}: {
  wording: PromptPayload
  compiled: SettingsPayload['compiled']
}) {
  const queryClient = useQueryClient()
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<{ user: { role: string } }>('/api/auth/me'),
    staleTime: 5 * 60_000,
  })
  const manager = me?.user.role === 'manager'
  const [active, setActive] = useState(ASSISTANTS[0].key)
  const assistant = ASSISTANTS.find((a) => a.key === active) ?? ASSISTANTS[0]

  /** What a box shows: the draft's own wording, else ours. */
  const inUse = (part: PartKey) => wording.draft[part] || wording.defaults[part].trim()
  const [texts, setTexts] = useState<Record<PartKey, string>>(() => ({
    brief: inUse('brief'),
    rules: inUse('rules'),
    chat: inUse('chat'),
    voice: inUse('voice'),
    email: inUse('email'),
    composer: inUse('composer'),
  }))
  const [problem, setProblem] = useState('')
  const [saved, setSaved] = useState('')

  const edited = (part: PartKey) => texts[part].trim() !== inUse(part).trim()
  const dirty = assistant.parts.some(edited)

  const save = useMutation({
    mutationFn: () =>
      api.put('/api/assistant-settings/prompt', Object.fromEntries(
        assistant.parts.map((part) => [part, texts[part]]),
      )),
    onSuccess: () => {
      setProblem('')
      setSaved(assistant.key)
      queryClient.invalidateQueries({ queryKey: ['assistant-settings'] })
    },
    onError: (err: unknown) => {
      setSaved('')
      const payload = (err as ApiError)?.payload as { detail?: string } | undefined
      setProblem(payload?.detail || String((err as Error)?.message ?? err))
    },
  })

  /** Ours, theirs, and whether an edit is waiting to be published. */
  const state = (a: (typeof ASSISTANTS)[number]) => {
    const own = a.parts.some((p) => wording.draft[p])
    const pending = a.parts.some((p) => wording.draft[p] !== wording.live[p])
    return { own, pending }
  }

  /** The assembled prompt this assistant would be handed, after this edit. */
  const projected = (channel: Channel) => {
    const effective = (part: PartKey, which: 'live' | 'now') => {
      const text = which === 'now' ? texts[part] : (wording.live[part] || wording.defaults[part])
      return text.trim().length
    }
    const touched: PartKey[] = ['brief', 'rules', channel]
    return touched.reduce(
      (n, part) => n - effective(part, 'live') + effective(part, 'now'),
      compiled[channel].length,
    )
  }

  const pick = (key: string) => {
    if (key === active) return
    if (dirty && !window.confirm('Leave this assistant? Your unsaved edits here are kept until you reload.')) {
      return
    }
    setProblem('')
    setSaved('')
    setActive(key)
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[15rem_minmax(0,1fr)]">
      {/* The list. A wrapping row of pills on a phone, a column beside the
          editor from `lg`. */}
      <nav aria-label="Assistants" className="flex flex-wrap gap-1.5 lg:flex-col lg:gap-1">
        {ASSISTANTS.map((a) => {
          const { own, pending } = state(a)
          return (
            <button
              key={a.key}
              type="button"
              onClick={() => pick(a.key)}
              aria-current={a.key === active ? 'true' : undefined}
              className={clsx(
                'flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-left text-sm transition-colors lg:w-full lg:py-2',
                a.key === active
                  ? 'border-primary bg-primary/5 font-medium text-foreground'
                  : 'border-border text-muted-foreground hover:bg-muted hover:text-foreground',
              )}
            >
              <Icon name={a.icon} className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span className="min-w-0 flex-1 truncate">{a.label}</span>
              {pending ? (
                <Badge tone="warning" className="hidden sm:inline-flex">Unpublished</Badge>
              ) : own ? (
                <Badge tone="primary" className="hidden sm:inline-flex">Yours</Badge>
              ) : null}
            </button>
          )
        })}
        <p className="mt-2 hidden text-xs leading-relaxed text-muted-foreground lg:block">
          Saved to the draft; buyers get it when the draft is published. What no wording can
          change: Liner quotes only prices and cars its tools return, never offers a sold car,
          never books a clashing time, and marks anything a buyer did not say as a guess --
          those are enforced in code. Placeholders such as {'{{DEALER_NAME}}'} are filled in.
        </p>
      </nav>

      <section className="min-w-0 space-y-4" aria-label={assistant.label}>
        <div>
          <h3 className="text-base font-semibold">{assistant.label}</h3>
          <p className="mt-0.5 text-sm text-muted-foreground">{assistant.where}</p>
          {!manager && (
            <p className="mt-1 text-xs text-muted-foreground">Only a manager can change these.</p>
          )}
        </div>

        {assistant.parts.map((part) => {
          const fallback = wording.defaults[part].trim()
          const isDefault = texts[part].trim() === fallback
          const limit = part === 'brief' || part === 'rules'
            ? wording.max_chars
            : wording.part_max[part as OwnPart]
          // The brief and rules share one allowance, said once under both.
          const own = isDefault ? 0 : texts[part].trim().length
          return (
            <div key={part} className="min-w-0">
              <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
                <label htmlFor={`part-${part}`} className="text-sm font-medium">
                  {PART_LABEL[part].label}
                </label>
                {isDefault ? (
                  <span className="text-xs text-muted-foreground">Liner&apos;s default</span>
                ) : (
                  <button
                    type="button"
                    disabled={!manager}
                    onClick={() => { setTexts((t) => ({ ...t, [part]: fallback })); setSaved('') }}
                    className="text-xs text-primary hover:underline disabled:opacity-50"
                  >
                    Reset to default
                  </button>
                )}
              </div>
              {PART_LABEL[part].hint && (
                <p className="mb-1.5 text-xs text-muted-foreground">{PART_LABEL[part].hint}</p>
              )}
              <textarea
                id={`part-${part}`}
                value={texts[part]}
                readOnly={!manager}
                onChange={(e) => {
                  const value = e.target.value
                  setTexts((t) => ({ ...t, [part]: value }))
                  setSaved('')
                }}
                rows={part === 'brief' || part === 'rules' ? 12 : 16}
                className="w-full resize-y rounded-md border border-input bg-background p-2 font-mono text-xs leading-relaxed outline-none focus:border-ring focus:ring-1 focus:ring-ring read-only:bg-muted/40"
              />
              {part !== 'brief' && part !== 'rules' && (
                <p className={clsx('mt-1 text-xs', own > limit ? 'text-destructive' : 'text-muted-foreground')}>
                  {own > 0
                    ? `${own.toLocaleString()} of ${limit.toLocaleString()} characters`
                    : "Using Liner's default wording"}
                </p>
              )}
            </div>
          )
        })}

        {assistant.key === 'shared' && (() => {
          const used = (texts.brief.trim() === wording.defaults.brief.trim() ? 0 : texts.brief.trim().length)
            + (texts.rules.trim() === wording.defaults.rules.trim() ? 0 : texts.rules.trim().length)
          return (
            <p className={clsx('text-xs', used > wording.max_chars ? 'text-destructive' : 'text-muted-foreground')}>
              {used > 0
                ? `${used.toLocaleString()} of ${wording.max_chars.toLocaleString()} characters of your own, brief and rules together`
                : "Using Liner's default wording"}
            </p>
          )
        })()}

        {/* The whole prompt each affected assistant would be handed. */}
        {assistant.channels.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {assistant.channels.map((channel) => {
              const n = projected(channel)
              const over = n > wording.prompt_max
              return (
                <span
                  key={channel}
                  className={clsx(
                    'rounded-md border px-2 py-1 text-xs',
                    over ? 'border-destructive/40 text-destructive' : 'border-border text-muted-foreground',
                  )}
                >
                  {CHANNEL_NAME[channel]}: about {n.toLocaleString()} of{' '}
                  {wording.prompt_max.toLocaleString()} characters in all
                </span>
              )
            })}
          </div>
        )}

        {problem && <p className="text-xs text-destructive">{problem}</p>}
        {manager && (
          <div className="flex flex-wrap items-center justify-end gap-3">
            {saved === assistant.key && !dirty && (
              <span className="text-xs text-success">
                Saved to the draft. Publish it at the top of the page to put it live.
              </span>
            )}
            <Button
              size="sm"
              variant="primary"
              onClick={() => save.mutate()}
              disabled={!dirty || save.isPending}
            >
              {save.isPending ? 'Saving...' : 'Save to draft'}
            </Button>
          </div>
        )}

        <details className="rounded-md border border-border">
          <summary className="cursor-pointer px-3 py-2 text-sm font-medium">
            {assistant.key === 'composer'
              ? 'What the writing assistant is told now'
              : 'The whole prompt, as Liner is running it now'}
          </summary>
          <div className="space-y-3 border-t border-border p-3">
            {assistant.key === 'composer' ? (
              <>
                <p className="text-xs text-muted-foreground">
                  The published instructions. Each draft then adds who is writing, the buyer,
                  the car in question and your knowledge answers, and asks for the shape the
                  channel needs -- a subject line for an email, a sentence or two for a text.
                </p>
                <pre className="max-h-[28rem] overflow-auto rounded-lg bg-muted p-3 text-xs leading-relaxed whitespace-pre-wrap">
                  {compiled.composer}
                </pre>
              </>
            ) : (
              assistant.channels.map((channel) => (
                <div key={channel}>
                  {assistant.channels.length > 1 && (
                    <p className="mb-1 text-xs font-medium">{CHANNEL_NAME[channel]}</p>
                  )}
                  <pre className="max-h-[28rem] overflow-auto rounded-lg bg-muted p-3 text-xs leading-relaxed whitespace-pre-wrap">
                    {compiled[channel]}
                  </pre>
                </div>
              ))
            )}
            <p className="text-xs text-muted-foreground">
              Read-only: the published version, assembled from the wording here plus your
              dealership&apos;s facts, its settings and its knowledge base.
            </p>
          </div>
        </details>
      </section>
    </div>
  )
}
