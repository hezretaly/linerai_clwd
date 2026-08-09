import { useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'

import { api } from '../lib/api'
import { initials, money, relative } from '../lib/format'
import {
  CONVERSATION_FILTERS,
  FILTER_LABEL,
  FILTER_TONE,
  leadMatches,
  leadStateOf,
  matches,
  stateOf,
} from '../lib/conversationFilters'
import type { ConversationFilter } from '../lib/conversationFilters'
import type { Conversation, Lead, TeamMember, User } from '../lib/types'
import { Button, Card, Empty, Spinner } from '../components/ui'
import { Icon } from '../components/Icon'
import { LeadDrawer } from '../components/dashboard/LeadDrawer'
import { PageIntro } from '../components/dashboard/AppShell'

/* Everyone Liner has heard from, in one list a manager can slice.
 *
 * Chat and Calls are working pages: one channel, master/detail, built around
 * reading a thread and replying. This is the page above them -- what came in,
 * what booked, what was declined, what is still running -- and it answers by
 * filtering rather than by opening anything. The filters are shared with Chat
 * so the two cannot disagree about what Appointed means.
 *
 * Two kinds of row, because there are two kinds of thing. A conversation is a
 * thread someone had. A lead imported from an ADF document never had one --
 * it is a document a marketplace sent -- and it would be invisible everywhere
 * if this page only listed threads. They are kept apart in the code and shown
 * together, rather than dressing a lead up as a conversation it never was.
 */

const CHANNEL_LABEL: Record<string, string> = { chat: 'Website chat', voice: 'Voice call' }
const SOURCE_LABEL: Record<string, string> = {
  chat: 'Website chat',
  phone: 'Phone',
  website: 'Website form',
  adf: 'Lead feed (ADF)',
}

type Row =
  | { kind: 'thread'; id: string; c: Conversation }
  | { kind: 'lead'; id: string; l: Lead }

/** What every row has to answer, whichever kind it is. Computed once so the
 *  table, the phone cards, the sort and the counts all read the same values. */
interface RowView {
  row: Row
  name: string
  email: string
  origin: string
  state: [string, string]
  vehicle: { title: string; price: number | null } | null
  assignedTo: string | null
  assignedId: string | null
  flagged: boolean
  activeAt: string
  /** Where a click goes. A lead with no thread has nothing to open, so its
   *  row opens the drawer instead of navigating to a page that is not there. */
  thread: string | null
}

function view(row: Row): RowView {
  if (row.kind === 'thread') {
    const c = row.c
    return {
      row,
      name: c.lead?.name || 'Unknown caller',
      email: c.lead?.email ?? '',
      origin: CHANNEL_LABEL[c.channel] ?? c.channel,
      state: stateOf(c),
      vehicle: c.focus_vehicle
        ? { title: c.focus_vehicle.title, price: c.focus_vehicle.price }
        : null,
      assignedTo: c.lead?.assigned_to?.name ?? null,
      assignedId: c.lead?.assigned_user_id ?? null,
      flagged: Boolean(c.open_escalation),
      activeAt: c.last_activity_at ?? c.started_at,
      thread: `/app/conversations/${c.id}`,
    }
  }
  const l = row.l
  return {
    row,
    name: l.name || 'Unnamed lead',
    email: l.email ?? '',
    origin: SOURCE_LABEL[l.source] ?? l.source,
    state: leadStateOf(l),
    vehicle: l.vehicle_of_interest
      ? { title: l.vehicle_of_interest.title, price: l.vehicle_of_interest.price }
      : null,
    assignedTo: l.assigned_to?.name ?? null,
    assignedId: l.assigned_user_id ?? null,
    flagged: Boolean(l.flagged),
    activeAt: l.last_touch_at ?? l.created_at,
    thread: null,
  }
}

function rowMatches(row: Row, filter: ConversationFilter, meId: string | undefined): boolean {
  return row.kind === 'thread'
    ? matches(row.c, filter, meId)
    : leadMatches(row.l, filter, meId)
}

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
  const [origin, setOrigin] = useState('')
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
  const { data: leadData } = useQuery({
    queryKey: ['leads'],
    queryFn: () => api.get<{ leads: Lead[] }>('/api/leads'),
  })
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<{ user: User }>('/api/auth/me'),
  })
  const { data: team } = useQuery({
    queryKey: ['team'],
    queryFn: () => api.get<{ members: TeamMember[] }>('/api/team'),
  })

  const meId = me?.user.id

  const rows = useMemo<RowView[]>(() => {
    const threads: Row[] = (data?.conversations ?? []).map((c) => ({
      kind: 'thread', id: c.id, c,
    }))
    // Only the ones with nothing to open. A lead that did have a conversation
    // is already in the list above as that conversation, and listing it twice
    // would double every count on the page.
    const orphans: Row[] = (leadData?.leads ?? [])
      .filter((l) => !l.conversation_id)
      .map((l) => ({ kind: 'lead', id: l.id, l }))
    return [...threads, ...orphans]
      .map(view)
      .sort((a, b) => b.activeAt.localeCompare(a.activeAt))
  }, [data, leadData])

  const totals = useMemo(() => {
    const out = {} as Record<ConversationFilter, number>
    for (const key of CONVERSATION_FILTERS) {
      out[key] = rows.filter((r) => rowMatches(r.row, key, meId)).length
    }
    return out
  }, [rows, meId])

  if (isLoading) return <Spinner />

  const origins = [...new Set(rows.map((r) => r.origin))].sort()

  const visible = rows
    .filter((r) => rowMatches(r.row, filter, meId))
    .filter((r) => (origin ? r.origin === origin : true))
    .filter((r) =>
      assignee === ''
        ? true
        : assignee === 'unclaimed'
          ? !r.assignedId
          : r.assignedId === assignee,
    )

  const open = (r: RowView) => {
    if (r.thread) navigate(r.thread)
    else setOpenLead(r.row.id)
  }

  return (
    <main className="p-4 md:p-6">
      <PageIntro
        title="Conversations"
        subtitle={`Every thread Liner has had, and every lead waiting for one. ${
          totals.flagged
        } ${totals.flagged === 1 ? 'is' : 'are'} waiting on a person.`}
        actions={
          <Link to="/app/leads/import">
            <Button size="sm" variant="primary">
              Import or add leads
            </Button>
          </Link>
        }
      />

      {/* ---- counters, which are also the filters ---- */}
      <div className="mb-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {CARDS.map(([key, label]) => (
          <CounterCard
            key={key}
            label={label}
            value={totals[key]}
            note={`of ${rows.length} total`}
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
            {/* Built from what is actually in the list. A hardcoded list of
                marketplaces would offer filters that match nothing, and miss
                a channel the moment one is added. */}
            <select
              value={origin}
              onChange={(e) => setOrigin(e.target.value)}
              className="h-9 rounded-md border border-input bg-background px-2.5 text-sm outline-none focus:border-ring focus:ring-1 focus:ring-ring"
            >
              <option value="">Everywhere</option>
              {origins.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
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
          <Empty title="Nothing matches" hint="Try a different filter." />
        ) : (
          <>
            {/* Below md the same rows render as cards. A table never reflows,
                and a manager checking the queue from a phone would otherwise
                get name and state with everything else off the right edge. */}
            <ul className="divide-y divide-border md:hidden">
              {visible.map((r) => (
                <PhoneRow
                  key={r.row.id}
                  r={r}
                  onOpen={() => open(r)}
                  onLead={() => setOpenLead(leadIdOf(r))}
                />
              ))}
            </ul>
            <div className="scroll-thin hidden overflow-x-auto md:block">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border">
                    {['Buyer', 'Came from', 'State', 'Vehicle', 'Assigned', 'Last activity', ''].map(
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
                  {visible.map((r) => (
                    <TableRow
                      key={r.row.id}
                      r={r}
                      onOpen={() => open(r)}
                      onLead={() => setOpenLead(leadIdOf(r))}
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
            {visible.length} of {rows.length}
          </span>
        </div>
      </Card>

      <LeadDrawer id={openLead} onClose={() => setOpenLead(null)} />
    </main>
  )
}

/** A lead row *is* the lead; a thread row points at one, and may not have it
 *  (a chat that never gave a name has no lead until it books). */
function leadIdOf(r: RowView): string | null {
  return r.row.kind === 'lead' ? r.row.id : r.row.c.lead_id
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

/** Only on rows that have a thread to open: on a lead row the row click
 *  already opens the drawer, and a button that repeats it is noise. */
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

function Flag() {
  return (
    <span className="inline-flex shrink-0 items-center rounded border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
      Needs a person
    </span>
  )
}

function NoEmail() {
  return <span className="text-primary">No email -- call back</span>
}

function TableRow({
  r,
  onOpen,
  onLead,
}: {
  r: RowView
  onOpen: () => void
  onLead: () => void
}) {
  const [label, style] = r.state

  return (
    <tr
      onClick={onOpen}
      className="cursor-pointer border-b border-border transition-colors last:border-0 hover:bg-muted/50"
    >
      <td className="py-2.5 pl-4 pr-3">
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground">
            {initials(r.name)}
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="truncate font-medium">{r.name}</span>
              {r.flagged && <Flag />}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {r.email || <NoEmail />}
            </div>
          </div>
        </div>
      </td>
      <td className="whitespace-nowrap px-3 py-2.5 text-muted-foreground">{r.origin}</td>
      <td className="px-3 py-2.5">
        <span
          className={clsx(
            'inline-flex items-center whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-medium',
            style,
          )}
        >
          {label}
        </span>
      </td>
      <td className="px-3 py-2.5">
        {r.vehicle ? (
          <div className="min-w-0">
            <div className="truncate">{r.vehicle.title}</div>
            <div className="tnum text-xs text-muted-foreground">{money(r.vehicle.price)}</div>
          </div>
        ) : (
          <span className="text-muted-foreground">--</span>
        )}
      </td>
      <td className="whitespace-nowrap px-3 py-2.5">
        {r.assignedTo ?? <span className="text-muted-foreground">Unclaimed</span>}
      </td>
      <td className={clsx('tnum whitespace-nowrap px-3 py-2.5', ageClass(r.activeAt))}>
        {relative(r.activeAt)}
      </td>
      <td className="w-20 whitespace-nowrap px-3 py-2.5 text-right">
        {r.thread && <LeadButton onLead={onLead} disabled={!leadIdOf(r)} />}
      </td>
    </tr>
  )
}

function PhoneRow({
  r,
  onOpen,
  onLead,
}: {
  r: RowView
  onOpen: () => void
  onLead: () => void
}) {
  const [label, style] = r.state

  return (
    <li>
      <div className="flex items-start gap-3 px-4 py-3">
        <button onClick={onOpen} className="flex min-w-0 flex-1 gap-3 text-left active:opacity-70">
          <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground">
            {initials(r.name)}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate font-medium">{r.name}</span>
              <span className={clsx('tnum shrink-0 text-xs', ageClass(r.activeAt))}>
                {relative(r.activeAt)}
              </span>
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {r.email || <NoEmail />}
            </div>
            {r.vehicle && (
              <div className="mt-1 truncate text-xs">
                {r.vehicle.title}
                <span className="tnum ml-1.5 text-muted-foreground">
                  {money(r.vehicle.price)}
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
              {r.flagged && <Flag />}
              <span className="text-[11px] text-muted-foreground">
                {r.origin}
                {' -- '}
                {r.assignedTo ?? 'Unclaimed'}
              </span>
            </div>
          </div>
        </button>
        {r.thread && (
          <div className="shrink-0 pt-0.5">
            <LeadButton onLead={onLead} disabled={!leadIdOf(r)} />
          </div>
        )}
      </div>
    </li>
  )
}
