import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'

import { api } from '../lib/api'
import { initials, money, relative } from '../lib/format'
import {
  CONVERSATION_FILTERS,
  FILTER_LABEL,
  FILTER_TONE,
  counts as countBy,
  matches,
  stateOf,
} from '../lib/conversationFilters'
import type { ConversationFilter } from '../lib/conversationFilters'
import type { Conversation, TeamMember, User } from '../lib/types'
import { Card, Empty, Spinner } from '../components/ui'
import { Icon } from '../components/Icon'
import { LeadDrawer } from '../components/dashboard/LeadDrawer'
import { PageIntro } from '../components/dashboard/AppShell'

/* Every conversation, both channels, in one list a manager can slice.
 *
 * Chat and Calls are working pages: one channel, master/detail, built around
 * reading a thread and replying to it. This is the page above them -- what
 * came in, what got booked, what was declined, what is still running -- and it
 * answers by filtering rather than by opening anything. The filters are shared
 * with Chat so the two cannot disagree about what "Appointed" means.
 */

const CHANNEL_LABEL: Record<string, string> = { chat: 'Website chat', voice: 'Voice call' }

/** Age drives colour -- time is the whole product. */
function ageClass(iso: string | undefined): string {
  if (!iso) return 'text-muted-foreground'
  const hours = (Date.now() - new Date(iso).getTime()) / 3_600_000
  if (hours >= 8) return 'font-medium text-primary'
  if (hours >= 4) return 'font-medium text-warning'
  return 'text-muted-foreground'
}

/** The four cards, and the filter each one turns on. They are the same filters
 *  as the chips below them and read the same counts -- a card is a bigger
 *  target for the four a manager opens the page for, not a second source. */
const CARDS: [ConversationFilter, string][] = [
  ['live', 'In progress'],
  ['appointed', 'Appointment set'],
  ['declined', 'Client declined'],
  ['flagged', 'Needs a person'],
]

export function ConversationListPage() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const requested = params.get('filter')
  const [filter, setFilter] = useState<ConversationFilter>(
    CONVERSATION_FILTERS.includes(requested as ConversationFilter)
      ? (requested as ConversationFilter)
      : 'all',
  )
  const [channel, setChannel] = useState('')
  const [assignee, setAssignee] = useState('')
  const [openLead, setOpenLead] = useState<string | null>(null)

  // Linkable, and the back button means something -- the same rule the Chat
  // page follows, because the Overview links into both.
  const choose = (next: ConversationFilter) => {
    setFilter(next)
    setParams(next === 'all' ? {} : { filter: next }, { replace: true })
  }

  const { data, isLoading } = useQuery({
    queryKey: ['conversations'],
    queryFn: () => api.get<{ conversations: Conversation[] }>('/api/conversations'),
  })
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<{ user: User }>('/api/auth/me'),
  })
  const { data: team } = useQuery({
    queryKey: ['team'],
    queryFn: () => api.get<{ members: TeamMember[] }>('/api/team'),
  })

  const conversations = useMemo(() => data?.conversations ?? [], [data])
  const meId = me?.user.id
  const totals = useMemo(() => countBy(conversations, meId), [conversations, meId])

  if (isLoading) return <Spinner />

  const visible = conversations
    .filter((c) => matches(c, filter, meId))
    .filter((c) => (channel ? c.channel === channel : true))
    .filter((c) =>
      assignee === ''
        ? true
        : assignee === 'unclaimed'
          ? !c.lead?.assigned_user_id
          : c.lead?.assigned_user_id === assignee,
    )

  return (
    <main className="p-4 md:p-6">
      <PageIntro
        title="Conversations"
        subtitle={`Every thread Liner has had, on both channels. ${totals.flagged} ${
          totals.flagged === 1 ? 'is' : 'are'
        } waiting on a person.`}
      />

      {/* ---- counters, which are also the filters ---- */}
      <div className="mb-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {CARDS.map(([key, label]) => (
          <CounterCard
            key={key}
            label={label}
            value={totals[key]}
            note={`of ${conversations.length} total`}
            active={filter === key}
            onClick={() => choose(filter === key ? 'all' : key)}
          />
        ))}
      </div>

      {/* ---- list ---- */}
      <Card className="shadow-sm">
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
          <div className="flex flex-wrap gap-1.5">
            {CONVERSATION_FILTERS.map((key) => (
              <button
                key={key}
                onClick={() => choose(key)}
                className={clsx(
                  'inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors',
                  filter === key
                    ? 'border-foreground bg-foreground text-background'
                    : FILTER_TONE[key] === 'primary'
                      ? 'border-primary/30 bg-primary/10 text-primary hover:bg-accent'
                      : 'border-input bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                )}
              >
                {FILTER_LABEL[key]}
                <span className="tnum opacity-70">{totals[key]}</span>
              </button>
            ))}
          </div>

          <div className="ml-auto flex items-center gap-2">
            <select
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
              className="h-9 rounded-md border border-input bg-background px-2.5 text-sm outline-none focus:border-ring focus:ring-1 focus:ring-ring"
            >
              <option value="">Both channels</option>
              <option value="chat">Website chat</option>
              <option value="voice">Voice call</option>
            </select>
            <select
              value={assignee}
              onChange={(e) => setAssignee(e.target.value)}
              className="h-9 rounded-md border border-input bg-background px-2.5 text-sm outline-none focus:border-ring focus:ring-1 focus:ring-ring"
            >
              <option value="">Anyone</option>
              <option value="unclaimed">Unclaimed</option>
              {team?.members.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        {visible.length === 0 ? (
          <Empty title="No conversations match" hint="Try a different filter." />
        ) : (
          <>
            {/* Below md the same rows render as cards. A table never reflows,
                and a manager checking the queue from a phone would otherwise
                get name and state with everything else off the right edge. */}
            <ul className="divide-y divide-border md:hidden">
              {visible.map((c) => (
                <ThreadCard
                  key={c.id}
                  conversation={c}
                  onOpen={() => navigate(`/app/conversations/${c.id}`)}
                  onLead={() => setOpenLead(c.lead_id)}
                />
              ))}
            </ul>
            <div className="scroll-thin hidden overflow-x-auto md:block">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border">
                    {['Buyer', 'Channel', 'State', 'Vehicle', 'Assigned', 'Last activity', ''].map(
                      (h, i) => (
                        <th
                          key={h || i}
                          className="h-10 whitespace-nowrap px-3 text-left font-medium text-muted-foreground first:pl-4"
                        >
                          {h}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {visible.map((c) => (
                    <ThreadRow
                      key={c.id}
                      conversation={c}
                      onOpen={() => navigate(`/app/conversations/${c.id}`)}
                      onLead={() => setOpenLead(c.lead_id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        <div className="flex flex-wrap items-center gap-3 border-t border-border px-4 py-3">
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Icon name="clock" className="h-3.5 w-3.5 shrink-0" />
            A row opens the thread on its own channel. Lead opens everything held on the buyer.
          </p>
          <span className="tnum ml-auto text-xs text-muted-foreground">
            {visible.length} of {conversations.length}
          </span>
        </div>
      </Card>

      <LeadDrawer id={openLead} onClose={() => setOpenLead(null)} />
    </main>
  )
}

function CounterCard({
  label,
  value,
  note,
  active,
  onClick,
}: {
  label: string
  value: number
  note: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button onClick={onClick} className="text-left">
      <Card
        className={clsx(
          'p-4 shadow-sm transition-colors',
          active ? 'border-primary bg-primary/5' : 'hover:border-primary',
        )}
      >
        <div
          className={clsx(
            'text-xs font-medium',
            active ? 'text-primary' : 'text-muted-foreground',
          )}
        >
          {label}
        </div>
        <div className="mt-1 flex items-baseline gap-2">
          <span className="tnum text-2xl font-bold">{value}</span>
          <span className="text-xs text-muted-foreground">{active ? 'filtering' : note}</span>
        </div>
      </Card>
    </button>
  )
}

function LeadButton({ onLead, disabled }: { onLead: () => void; disabled: boolean }) {
  return (
    <button
      disabled={disabled}
      onClick={(e) => {
        // The row itself opens the thread. Without this the drawer opens and
        // the page navigates away underneath it.
        e.stopPropagation()
        onLead()
      }}
      className="inline-flex h-8 items-center rounded-md border border-input bg-background px-3 text-xs font-medium transition-colors hover:border-primary hover:bg-primary hover:text-primary-foreground disabled:opacity-40 disabled:hover:border-input disabled:hover:bg-background disabled:hover:text-foreground"
    >
      Lead
    </button>
  )
}

function ThreadRow({
  conversation: c,
  onOpen,
  onLead,
}: {
  conversation: Conversation
  onOpen: () => void
  onLead: () => void
}) {
  const [label, style] = stateOf(c)
  const name = c.lead?.name || 'Unknown caller'

  return (
    <tr
      onClick={onOpen}
      className="cursor-pointer border-b border-border transition-colors last:border-0 hover:bg-muted/50"
    >
      <td className="py-2.5 pl-4 pr-3">
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground">
            {initials(name)}
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="truncate font-medium">{name}</span>
              {c.open_escalation && (
                <span className="inline-flex shrink-0 items-center rounded border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                  Needs a person
                </span>
              )}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {c.lead?.email || <span className="text-primary">No email -- call back</span>}
            </div>
          </div>
        </div>
      </td>
      <td className="whitespace-nowrap px-3 py-2.5 text-muted-foreground">
        {CHANNEL_LABEL[c.channel] ?? c.channel}
      </td>
      <td className="px-3 py-2.5">
        <span
          className={clsx(
            'inline-flex whitespace-nowrap items-center rounded border px-1.5 py-0.5 text-[11px] font-medium',
            style,
          )}
        >
          {label}
        </span>
      </td>
      <td className="px-3 py-2.5">
        {c.focus_vehicle ? (
          <div className="min-w-0">
            <div className="truncate">{c.focus_vehicle.title}</div>
            <div className="tnum text-xs text-muted-foreground">
              {money(c.focus_vehicle.price)}
            </div>
          </div>
        ) : (
          <span className="text-muted-foreground">--</span>
        )}
      </td>
      <td className="whitespace-nowrap px-3 py-2.5">
        {c.lead?.assigned_to ? (
          c.lead.assigned_to.name
        ) : (
          <span className="text-muted-foreground">Unclaimed</span>
        )}
      </td>
      <td
        className={clsx('tnum whitespace-nowrap px-3 py-2.5', ageClass(c.last_activity_at))}
      >
        {relative(c.last_activity_at ?? c.started_at)}
      </td>
      <td className="w-20 whitespace-nowrap px-3 py-2.5 text-right">
        <LeadButton onLead={onLead} disabled={!c.lead_id} />
      </td>
    </tr>
  )
}

function ThreadCard({
  conversation: c,
  onOpen,
  onLead,
}: {
  conversation: Conversation
  onOpen: () => void
  onLead: () => void
}) {
  const [label, style] = stateOf(c)
  const name = c.lead?.name || 'Unknown caller'

  return (
    <li>
      <div className="flex items-start gap-3 px-4 py-3">
        <button onClick={onOpen} className="flex min-w-0 flex-1 gap-3 text-left active:opacity-70">
          <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground">
            {initials(name)}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate font-medium">{name}</span>
              <span className={clsx('tnum shrink-0 text-xs', ageClass(c.last_activity_at))}>
                {relative(c.last_activity_at ?? c.started_at)}
              </span>
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {c.lead?.email || <span className="text-primary">No email -- call back</span>}
            </div>
            {c.focus_vehicle && (
              <div className="mt-1 truncate text-xs">
                {c.focus_vehicle.title}
                <span className="tnum ml-1.5 text-muted-foreground">
                  {money(c.focus_vehicle.price)}
                </span>
              </div>
            )}
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <span
                className={clsx(
                  'inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium',
                  style,
                )}
              >
                {label}
              </span>
              {c.open_escalation && (
                <span className="inline-flex items-center rounded border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                  Needs a person
                </span>
              )}
              <span className="text-[11px] text-muted-foreground">
                {CHANNEL_LABEL[c.channel] ?? c.channel}
                {' -- '}
                {c.lead?.assigned_to ? c.lead.assigned_to.name : 'Unclaimed'}
              </span>
            </div>
          </div>
        </button>
        <div className="shrink-0 pt-0.5">
          <LeadButton onLead={onLead} disabled={!c.lead_id} />
        </div>
      </div>
    </li>
  )
}
