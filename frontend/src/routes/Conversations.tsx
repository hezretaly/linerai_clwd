import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'

import { api } from '../lib/api'
import { PROVENANCE_LABEL, initials, money, time, waited } from '../lib/format'
import type { Conversation, Message, TeamMember } from '../lib/types'
import { Empty, NotBacked, Spinner, Unavailable } from '../components/ui'
import { Icon, type IconName } from '../components/Icon'

/* The three-pane layout from the mockup. The list groups by what a rep is
 * deciding between -- who is stuck, who is live, who is done -- rather than by
 * time, so the top of the list is always the work. */

const CHANNEL_ICON: Record<string, IconName> = { chat: 'chat', voice: 'voice' }
const CHANNEL_LABEL: Record<string, string> = { chat: 'Website chat', voice: 'Voice call' }
const CHANNEL_STYLE: Record<string, string> = {
  chat: 'bg-muted text-foreground',
  voice: 'bg-primary/10 text-primary',
}
const SOURCE_LABEL: Record<string, string> = {
  chat: 'Website',
  phone: 'Phone',
  website: 'Website form',
}

type Group = 'Needs a person' | 'Live now' | 'Earlier today'

function groupOf(c: Conversation): Group {
  if (c.open_escalation) return 'Needs a person'
  if (c.status === 'active' || c.agent_paused) return 'Live now'
  return 'Earlier today'
}

/** Tags are derived, never stored -- each one restates something on the row. */
function tagsOf(c: Conversation): [string, string][] {
  const tags: [string, string][] = []
  if (c.open_escalation) {
    tags.push([c.open_escalation.rule?.label ?? 'Needs a person', 'destructive'])
  }
  tags.push([SOURCE_LABEL[c.lead?.source ?? 'chat'] ?? 'Website', 'muted'])
  if (!c.lead?.assigned_user_id) tags.push(['Unclaimed', 'muted'])
  if (c.lead && c.lead.contact_risk) tags.push(['No email', 'warning'])
  return tags
}

const TAG_STYLE: Record<string, string> = {
  destructive: 'border-destructive/30 bg-destructive/10 text-destructive',
  warning: 'border-warning/30 bg-warning/10 text-warning',
  muted: 'border-border text-muted-foreground',
}

export function ConversationsPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [reply, setReply] = useState('')
  const [query, setQuery] = useState('')
  // Arriving from the Overview's "Needs a person" panel should land on that
  // filter, not on All -- otherwise the queue you clicked is buried in the
  // full list and you have to find it again.
  const [params, setParams] = useSearchParams()
  const requested = params.get('filter')
  const [filter, setFilter] = useState<'all' | 'flagged' | 'live' | 'unclaimed'>(
    requested === 'flagged' || requested === 'live' || requested === 'unclaimed'
      ? requested
      : 'all',
  )

  // Keep the URL honest as the rep changes filters, so the view is linkable
  // and the back button means something.
  const chooseFilter = (next: 'all' | 'flagged' | 'live' | 'unclaimed') => {
    setFilter(next)
    setParams(next === 'all' ? {} : { filter: next }, { replace: true })
  }

  const { data, isLoading } = useQuery({
    queryKey: ['conversations'],
    queryFn: () => api.get<{ conversations: Conversation[] }>('/api/conversations'),
  })

  const conversations = useMemo(() => data?.conversations ?? [], [data])
  const selectedId = id ?? conversations[0]?.id
  const openId = Boolean(id)

  const { data: detail } = useQuery({
    queryKey: ['conversations', selectedId],
    queryFn: () => api.get<Conversation>(`/api/conversations/${selectedId}`),
    enabled: Boolean(selectedId),
  })

  useEffect(() => setReply(''), [selectedId])

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['conversations'] })
    void queryClient.invalidateQueries({ queryKey: ['overview'] })
  }

  const takeover = useMutation({
    mutationFn: () => api.post(`/api/conversations/${selectedId}/takeover`),
    onSuccess: invalidate,
  })
  const handback = useMutation({
    mutationFn: () => api.post(`/api/conversations/${selectedId}/handback`),
    onSuccess: invalidate,
  })
  const send = useMutation({
    mutationFn: () => api.post(`/api/conversations/${selectedId}/messages`, { content: reply }),
    onSuccess: () => {
      setReply('')
      invalidate()
    },
  })

  if (isLoading) return <Spinner />

  const flagged = conversations.filter((c) => c.open_escalation).length
  const live = conversations.filter((c) => c.status === 'active').length
  const unclaimed = conversations.filter((c) => !c.lead?.assigned_user_id).length

  const visible = conversations
    .filter((c) =>
      filter === 'flagged'
        ? c.open_escalation
        : filter === 'live'
          ? c.status === 'active'
          : filter === 'unclaimed'
            ? !c.lead?.assigned_user_id
            : true,
    )
    .filter((c) =>
      query
        ? `${c.lead?.name ?? ''} ${c.lead?.email ?? ''} ${c.lead?.phone ?? ''} ${c.summary}`
            .toLowerCase()
            .includes(query.toLowerCase())
        : true,
    )

  const groups: Group[] = ['Needs a person', 'Live now', 'Earlier today']

  return (
    // Desktop: the shell's top bar is h-14 and the panes take the rest of the
    // viewport, so each scrolls on its own rather than the page scrolling as a
    // whole. Phone: there is no room for two panes side by side, so it becomes
    // master/detail -- the list until you pick a thread, then the thread with a
    // way back. The URL already distinguishes them, so back is a real
    // navigation and the hardware back button works.
    <div className="flex flex-col md:h-[calc(100vh-3.5rem)] md:flex-row">
      {/* ---------- list pane ---------- */}
      <div
        className={clsx(
          'flex flex-col border-r border-border bg-background md:w-[336px] md:shrink-0',
          openId ? 'hidden md:flex' : 'flex',
        )}
      >
        <div className="border-b border-border p-4">
          <div className="mb-3 flex items-baseline gap-2">
            <h1 className="text-lg font-semibold tracking-tight">Conversations</h1>
            <span className="tnum text-xs text-muted-foreground">
              {conversations.length} open
            </span>
          </div>

          <div className="relative mb-3">
            <Icon
              name="search"
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search name, email, phone..."
              className="h-9 w-full rounded-md border border-input bg-background pl-9 pr-3 text-sm outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring"
            />
          </div>

          <div className="flex flex-wrap gap-1.5">
            <FilterChip
              label="Needs a person"
              count={flagged}
              tone="destructive"
              active={filter === 'flagged'}
              onClick={() => chooseFilter('flagged')}
            />
            <FilterChip
              label="All"
              count={conversations.length}
              active={filter === 'all'}
              onClick={() => chooseFilter('all')}
            />
            <FilterChip
              label="Live"
              count={live}
              active={filter === 'live'}
              onClick={() => chooseFilter('live')}
            />
            <FilterChip
              label="Unclaimed"
              count={unclaimed}
              active={filter === 'unclaimed'}
              onClick={() => chooseFilter('unclaimed')}
            />
            {/* "Mine" needs a rep filter the list endpoint does not take. */}
            <Unavailable
              label="Mine"
              size="sm"
              why="The conversation list cannot be filtered by assignee yet. Assignment lives on the lead, and /api/conversations takes only status and channel."
            />
          </div>
        </div>

        <div className="scroll-thin flex-1 overflow-y-auto">
          {visible.length === 0 ? (
            <Empty title="Nothing matches" hint="Try a different filter or search." />
          ) : (
            groups.map((group) => {
              const rows = visible.filter((c) => groupOf(c) === group)
              if (!rows.length) return null
              return (
                <div key={group}>
                  <div className="sticky top-0 z-10 border-b border-border bg-muted/60 px-4 py-1.5 text-xs font-medium text-muted-foreground backdrop-blur">
                    {group}
                  </div>
                  {rows.map((c) => (
                    <ListRow
                      key={c.id}
                      conversation={c}
                      active={c.id === selectedId}
                      onSelect={() => navigate(`/app/conversations/${c.id}`)}
                    />
                  ))}
                </div>
              )
            })
          )}
        </div>
      </div>

      {/* ---------- thread pane ---------- */}
      <div
        className={clsx(
          'min-w-0 flex-1 flex-col bg-canvas',
          openId ? 'flex' : 'hidden md:flex',
        )}
      >
        {!detail ? (
          <Empty title="Select a conversation" />
        ) : (
          <>
            <ThreadHeader conversation={detail} />
            <ThreadBanner
              conversation={detail}
              onTakeover={() => takeover.mutate()}
              onHandback={() => handback.mutate()}
            />
            <div className="scroll-thin flex-1 p-4 md:overflow-y-auto md:p-6">
              <div className="mx-auto flex max-w-3xl flex-col gap-1">
                {(detail.messages ?? []).map((m) => (
                  <Bubble key={m.id} message={m} />
                ))}
              </div>
            </div>
            <div className="sticky bottom-0 border-t border-border bg-background p-4 md:static">
              {detail.agent_paused ? (
                <Composer
                  name={detail.lead?.name ?? 'this buyer'}
                  source={SOURCE_LABEL[detail.lead?.source ?? 'chat'] ?? 'Website'}
                  value={reply}
                  onChange={setReply}
                  onSend={() => send.mutate()}
                  sending={send.isPending}
                />
              ) : (
                <LockedComposer onTakeover={() => takeover.mutate()} />
              )}
            </div>
          </>
        )}
      </div>

      {/* ---------- context rail ---------- */}
      {detail && <ContextRail conversation={detail} />}
    </div>
  )
}

function FilterChip({
  label,
  count,
  tone,
  active,
  onClick,
}: {
  label: string
  count: number
  tone?: 'destructive'
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors',
        active
          ? 'border-foreground bg-foreground text-background'
          : tone === 'destructive'
            ? 'border-destructive/30 bg-destructive/10 text-destructive hover:bg-destructive/15'
            : 'border-input bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground',
      )}
    >
      {label}
      <span className="tnum opacity-70">{count}</span>
    </button>
  )
}

function ListRow({
  conversation,
  active,
  onSelect,
}: {
  conversation: Conversation
  active: boolean
  onSelect: () => void
}) {
  const name = conversation.lead?.name ?? 'Unknown caller'
  const accent = conversation.open_escalation
    ? 'before:bg-destructive'
    : conversation.status === 'active'
      ? 'before:bg-success'
      : 'before:bg-transparent'

  return (
    <button
      onClick={onSelect}
      className={clsx(
        'relative flex w-full gap-3 border-b border-border px-4 py-3 text-left transition-colors',
        'before:absolute before:inset-y-0 before:left-0 before:w-[3px]',
        accent,
        active ? 'bg-muted' : 'hover:bg-muted/50',
      )}
    >
      <span
        className={clsx(
          'flex h-8 w-8 shrink-0 items-center justify-center rounded-md',
          CHANNEL_STYLE[conversation.channel],
        )}
      >
        <Icon name={CHANNEL_ICON[conversation.channel] ?? 'chat'} className="h-4 w-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="mb-0.5 flex items-center gap-1.5">
          <span className="truncate text-sm font-medium">{name}</span>
          <span className="tnum ml-auto shrink-0 text-[11px] text-muted-foreground">
            {time(conversation.started_at)}
          </span>
        </span>
        <span className="line-clamp-2 block text-xs leading-snug text-muted-foreground">
          {conversation.summary || 'No messages yet'}
        </span>
        <span className="mt-1.5 flex flex-wrap gap-1">
          {tagsOf(conversation).map(([label, tone]) => (
            <span
              key={label}
              className={clsx(
                'inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium',
                TAG_STYLE[tone],
              )}
            >
              {label}
            </span>
          ))}
        </span>
      </span>
    </button>
  )
}

function ThreadHeader({ conversation }: { conversation: Conversation }) {
  const name = conversation.lead?.name ?? 'Unknown caller'
  const vehicle = conversation.focus_vehicle?.title
  const meta = [
    vehicle,
    CHANNEL_LABEL[conversation.channel],
    SOURCE_LABEL[conversation.lead?.source ?? 'chat'],
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <div className="sticky top-14 z-10 flex h-14 shrink-0 items-center gap-3 border-b border-border bg-background px-4 md:static md:px-5">
      {/* Master/detail needs a way back on a phone. On desktop both panes are
          visible, so it would be a button that undoes nothing. */}
      <Link
        to="/app/conversations"
        aria-label="Back to conversations"
        className="-ml-2 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground md:hidden"
      >
        <Icon name="back" className="h-5 w-5" />
      </Link>
      <div className="hidden h-9 w-9 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold sm:flex">
        {initials(name)}
      </div>
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold leading-tight">{name}</div>
        <div className="truncate text-xs text-muted-foreground">{meta}</div>
      </div>
      <div className="ml-auto hidden shrink-0 items-center gap-2 lg:flex">
        {/* The mockup's three header actions. None has an endpoint: a
            conversation has no snooze or done state, and assignment is a
            property of the lead, changed from the rail below. */}
        <Unavailable
          label="Snooze"
          size="sm"
          why="Conversations have no snooze state. status is active, handoff or closed and nothing schedules a return."
        />
        <Unavailable
          label="Mark done"
          size="sm"
          why="Nothing closes a conversation from here. It closes itself when a booking completes."
        />
      </div>
    </div>
  )
}

function ThreadBanner({
  conversation,
  onTakeover,
  onHandback,
}: {
  conversation: Conversation
  onTakeover: () => void
  onHandback: () => void
}) {
  if (conversation.agent_paused) {
    return (
      <div className="flex shrink-0 flex-wrap items-center gap-3 bg-primary px-5 py-3 text-primary-foreground">
        <Icon name="user" className="h-4 w-4 shrink-0" />
        <div>
          <div className="text-sm font-medium">You are replying as Riverside Auto</div>
          <div className="text-xs opacity-80">
            Liner is paused on this conversation and will not send anything.
          </div>
        </div>
        <button
          onClick={onHandback}
          className="ml-auto inline-flex h-8 items-center rounded-md border border-white/30 bg-white/15 px-3 text-xs font-medium transition-colors hover:bg-white/25"
        >
          Hand back to Liner
        </button>
      </div>
    )
  }

  const escalation = conversation.open_escalation
  if (!escalation) return null

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-destructive/20 bg-destructive/10 px-5 py-3 text-destructive">
      <Icon name="alert" className="h-4 w-4 shrink-0" />
      <div className="min-w-0">
        <div className="text-sm font-medium">
          Liner stopped -- {escalation.rule?.label ?? 'needs a person'}
        </div>
        <div className="text-xs opacity-80">
          {escalation.reason} Waiting {waited(escalation.created_at)}.
        </div>
      </div>
      <button
        onClick={onTakeover}
        className="ml-auto inline-flex h-8 shrink-0 items-center rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground transition-opacity hover:opacity-90"
      >
        Take over
      </button>
    </div>
  )
}

function Bubble({ message }: { message: Message }) {
  const stamp = time(message.created_at)

  if (message.role === 'buyer') {
    return (
      <>
        <div className="mt-2 max-w-[80%] self-start rounded-lg rounded-bl-sm border border-border bg-background px-3.5 py-2.5 text-sm leading-relaxed">
          {message.content}
        </div>
        <div className="tnum mb-1 self-start text-[11px] text-muted-foreground">{stamp}</div>
      </>
    )
  }

  if (message.role === 'rep') {
    return (
      <>
        <div className="mt-2 max-w-[80%] self-end whitespace-pre-wrap rounded-lg rounded-br-sm bg-foreground px-3.5 py-2.5 text-sm leading-relaxed text-background">
          {message.content}
        </div>
        <div className="tnum mb-1 self-end text-[11px] text-muted-foreground">
          Sent by a person · {stamp}
        </div>
      </>
    )
  }

  return (
    <>
      {/* The mockup shows a system line whenever Liner consulted inventory.
          Here that is the tool calls actually recorded on the turn, so the
          line names what really ran rather than narrating. */}
      {message.tool_calls.length > 0 && (
        <div className="my-2 flex max-w-[90%] items-start gap-2 self-center rounded-md border border-border bg-background px-3 py-2 text-xs text-muted-foreground">
          <Icon name="box" className="h-3.5 w-3.5 shrink-0" />
          <span>Liner ran {message.tool_calls.map((t) => t.name).join(', ')}</span>
        </div>
      )}
      <div className="mt-2 max-w-[80%] self-end whitespace-pre-wrap rounded-lg rounded-br-sm bg-primary px-3.5 py-2.5 text-sm leading-relaxed text-primary-foreground">
        {message.content}
      </div>
      <div className="tnum mb-1 self-end text-[11px] text-muted-foreground">Liner · {stamp}</div>
    </>
  )
}

function LockedComposer({ onTakeover }: { onTakeover: () => void }) {
  return (
    <div className="mx-auto flex max-w-3xl items-center gap-3 rounded-lg border border-dashed border-input bg-muted/40 px-4 py-3">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border bg-background text-muted-foreground">
        <Icon name="lock" className="h-4 w-4" />
      </span>
      <div className="min-w-0">
        <div className="text-sm font-medium">Liner is holding this conversation</div>
        <div className="text-xs text-muted-foreground">
          It won't reply again until someone takes over or hands it back.
        </div>
      </div>
      <button
        onClick={onTakeover}
        className="ml-auto inline-flex h-9 shrink-0 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
      >
        Take over
      </button>
    </div>
  )
}

function Composer({
  name,
  source,
  value,
  onChange,
  onSend,
  sending,
}: {
  name: string
  source: string
  value: string
  onChange: (v: string) => void
  onSend: () => void
  sending: boolean
}) {
  return (
    <div className="mx-auto max-w-3xl overflow-hidden rounded-lg border border-input focus-within:border-ring focus-within:ring-1 focus-within:ring-ring">
      <textarea
        rows={3}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={`Reply to ${name}...`}
        className="w-full resize-none bg-background px-4 py-3 text-sm outline-none placeholder:text-muted-foreground"
      />
      <div className="flex items-center gap-2 border-t border-border bg-muted/40 px-3 py-2">
        <span className="text-xs text-muted-foreground">
          Sending as <b className="font-medium text-foreground">Riverside Auto</b> · {source}
        </span>
        <Unavailable
          label="Save as note"
          size="sm"
          className="ml-auto"
          why="There is no internal note store. Everything written here is sent to the buyer."
        />
        <button
          onClick={onSend}
          disabled={!value.trim() || sending}
          className="inline-flex h-8 items-center rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:pointer-events-none disabled:opacity-50"
        >
          {sending ? 'Sending...' : 'Send reply'}
        </button>
      </div>
    </div>
  )
}

function ContextRail({ conversation }: { conversation: Conversation }) {
  const lead = conversation.lead
  const { data: team } = useQuery({
    queryKey: ['team'],
    queryFn: () => api.get<{ members: TeamMember[] }>('/api/team'),
  })

  const contact: { icon: IconName; value: string; prov?: string }[] = [
    { icon: 'phone', value: lead?.phone || 'Not given', prov: lead?.phone ? 'caller_id' : undefined },
    { icon: 'mail', value: lead?.email || 'Not given', prov: lead?.email ? 'typed' : undefined },
    { icon: 'globe', value: SOURCE_LABEL[lead?.source ?? 'chat'] ?? 'Website' },
  ]

  const assignee = team?.members.find((m) => m.id === lead?.assigned_user_id)

  return (
    <aside className="scroll-thin hidden w-[300px] shrink-0 overflow-y-auto border-l border-border bg-background xl:block">
      <div className="border-b border-border p-5">
        <div className="text-base font-semibold leading-tight">
          {lead?.name ?? 'Unknown caller'}
        </div>
        <div className="mb-3 mt-0.5 text-xs text-muted-foreground">
          {conversation.channel === 'voice' ? 'Voice call' : 'Website chat'} ·{' '}
          {conversation.message_count ?? 0} messages
        </div>
        {contact.map((row) => (
          <div key={row.icon} className="flex items-center gap-2 py-1 text-sm">
            <Icon name={row.icon} className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className={clsx('truncate', row.value === 'Not given' && 'text-muted-foreground')}>
              {row.value}
            </span>
            {row.prov && (
              <span className="ml-auto shrink-0 rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {PROVENANCE_LABEL[row.prov]}
              </span>
            )}
          </div>
        ))}
      </div>

      {conversation.focus_vehicle && (
        <div className="border-b border-border p-5">
          <div className="mb-3 text-xs font-medium text-muted-foreground">Vehicle of interest</div>
          <div className="flex gap-3">
            <img
              src={conversation.focus_vehicle.photo_url}
              alt=""
              className="h-12 w-16 shrink-0 rounded-md border border-border object-cover"
            />
            <div className="min-w-0">
              <div className="text-sm font-medium leading-snug">
                {conversation.focus_vehicle.title}
              </div>
              <div className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                {conversation.focus_vehicle.mileage?.toLocaleString()} mi · {conversation.focus_vehicle.vin}
              </div>
              <div className="tnum mt-1 text-sm font-semibold">
                {money(conversation.focus_vehicle.price)}
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="border-b border-border p-5">
        <div className="mb-3 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          Captured by Liner
          <Icon name="check" className="h-3.5 w-3.5 text-success" strokeWidth={2.5} />
        </div>
        {lead?.captured_fields?.length ? (
          <>
            <div className="space-y-1.5">
              {lead.captured_fields.map((f) => (
                <div key={f.id} className="flex items-baseline gap-2 text-sm">
                  <span className="w-[70px] shrink-0 text-xs capitalize text-muted-foreground">
                    {f.key.replace(/_/g, ' ')}
                  </span>
                  {/* Inferred values render italic. The difference between
                      reporting what we know and laundering a guess into a fact
                      a rep repeats on the phone. */}
                  <span className={clsx('min-w-0 flex-1', !f.verified && 'italic text-muted-foreground')}>
                    {f.value}
                  </span>
                  {!f.verified && (
                    <span className="inline-flex shrink-0 items-center gap-1 rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-[10px] font-medium text-warning">
                      {PROVENANCE_LABEL[f.provenance]}
                    </span>
                  )}
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
          <p className="text-sm text-muted-foreground">Nothing captured yet.</p>
        )}
      </div>

      <div className="border-b border-border p-5">
        <div className="mb-2 text-xs font-medium text-muted-foreground">Assigned to</div>
        {/* Assignment is a property of the lead and is set when an appointment
            is assigned. Nothing reassigns from a conversation, so this reports
            rather than offering a select that would not save. */}
        <div className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm">
          {assignee ? assignee.name : 'Unclaimed -- in the pool'}
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
          Set when an appointment is assigned, from the calendar.
        </p>
      </div>

      <div className="p-5">
        <div className="mb-3 text-xs font-medium text-muted-foreground">History</div>
        <NotBacked
          title="No activity timeline"
          why="Nothing records per-lead events over time. The transcript beside this is the full history the system holds."
        />
      </div>
    </aside>
  )
}
