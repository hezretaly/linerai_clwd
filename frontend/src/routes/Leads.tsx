import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'

import { api } from '../lib/api'
import { initials, money, relative } from '../lib/format'
import type { Lead, TeamMember, User } from '../lib/types'
import { Button, Card, Empty, Spinner, Unavailable } from '../components/ui'
import { Icon } from '../components/Icon'
import { LeadDrawer } from '../components/dashboard/LeadDrawer'
import { PageIntro } from '../components/dashboard/AppShell'

const STAGE: Record<string, [string, string]> = {
  new: ['New', 'border-border text-muted-foreground'],
  qualifying: ['Qualifying', 'border-primary/30 bg-primary/10 text-primary'],
  qualified: ['Qualified', 'border-primary/30 bg-primary/10 text-primary'],
  appointment: ['Appointment set', 'border-success/30 bg-success/10 text-success'],
}

const SOURCE_LABEL: Record<string, string> = {
  chat: 'Website chat',
  phone: 'Phone',
  website: 'Website form',
  adf: 'Lead feed',
}

/** Age drives colour -- time is the whole product. */
function ageClass(iso: string | undefined): string {
  if (!iso) return 'text-muted-foreground'
  const hours = (Date.now() - new Date(iso).getTime()) / 3_600_000
  if (hours >= 8) return 'font-medium text-primary'
  if (hours >= 4) return 'font-medium text-warning'
  return 'text-muted-foreground'
}

type Tab = 'all' | 'unclaimed' | 'flagged' | 'mine' | 'appointment'

export function LeadsPage() {
  const [tab, setTab] = useState<Tab>('all')
  const [source, setSource] = useState('')
  const [assignee, setAssignee] = useState('')
  const [openId, setOpenId] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
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

  const leads = useMemo(() => data?.leads ?? [], [data])

  const counts = useMemo(() => {
    const unclaimed = leads.filter((l) => !l.assigned_user_id)
    return {
      unclaimed: unclaimed.length,
      // The mockup calls out how many have gone stale. Real, and the number
      // a rep is actually deciding on.
      stale: unclaimed.filter(
        (l) => (Date.now() - new Date(l.created_at).getTime()) / 3_600_000 >= 8,
      ).length,
      qualified: leads.filter((l) => l.stage === 'qualified' || l.stage === 'appointment').length,
      booked: leads.reduce((n, l) => n + (l.appointment_count ?? 0), 0),
      unconfirmed: leads.reduce((n, l) => n + (l.unconfirmed_count ?? 0), 0),
      unreachable: leads.filter((l) => l.contact_risk).length,
      flagged: leads.filter((l) => l.flagged).length,
      mine: leads.filter((l) => l.assigned_user_id === me?.user.id).length,
      appointment: leads.filter((l) => l.stage === 'appointment').length,
    }
  }, [leads, me])

  if (isLoading) return <Spinner />

  const visible = leads
    .filter((l) =>
      tab === 'unclaimed'
        ? !l.assigned_user_id
        : tab === 'flagged'
          ? l.flagged
          : tab === 'mine'
            ? l.assigned_user_id === me?.user.id
            : tab === 'appointment'
              ? l.stage === 'appointment'
              : true,
    )
    .filter((l) => (source ? l.source === source : true))
    .filter((l) =>
      assignee === ''
        ? true
        : assignee === 'unclaimed'
          ? !l.assigned_user_id
          : l.assigned_user_id === assignee,
    )

  const tabs: [Tab, string, number][] = [
    ['all', 'All', leads.length],
    ['unclaimed', 'Unclaimed', counts.unclaimed],
    ['flagged', 'Needs a person', counts.flagged],
    ['mine', 'Mine', counts.mine],
    ['appointment', 'Booked', counts.appointment],
  ]

  return (
    <main className="p-4 md:p-6">
      <PageIntro
        title="Leads"
        subtitle={`Everyone Liner has spoken to. ${counts.unclaimed} ${
          counts.unclaimed === 1 ? 'is' : 'are'
        } unclaimed.`}
        actions={
          <>
            <Unavailable
              label="Export"
              why="No export endpoint exists. The same rows are available from /api/leads."
            />
            <Link to="/app/leads/import">
              <Button size="sm" variant="primary">
                Import or add leads
              </Button>
            </Link>
          </>
        }
      />

      {/* ---- counters ---- */}
      <div className="mb-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Counter
          label="Unclaimed"
          value={counts.unclaimed}
          note={counts.stale ? `${counts.stale} over 8h` : 'none stale'}
          tone={counts.stale ? 'primary' : 'muted'}
        />
        <Counter
          label="Qualified or better"
          value={counts.qualified}
          note={`of ${leads.length} total`}
        />
        <Counter
          label="Appointments set"
          value={counts.booked}
          note={counts.unconfirmed ? `${counts.unconfirmed} unconfirmed` : 'all confirmed'}
          tone={counts.unconfirmed ? 'primary' : 'muted'}
        />
        {/* The mockup says "no phone on file". Inverted here: with SMS out of
            scope, an email is the only way this product can reach anyone. */}
        <Counter
          label="No way to reach them"
          value={counts.unreachable}
          note="no email on file"
        />
      </div>

      {/* ---- table ---- */}
      <Card className="shadow-sm">
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
          <div className="flex flex-wrap gap-1.5">
            {tabs.map(([id, label, count]) => (
              <button
                key={id}
                onClick={() => setTab(id)}
                className={clsx(
                  'inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors',
                  tab === id
                    ? 'border-foreground bg-foreground text-background'
                    : 'border-input bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                )}
              >
                {label}
                <span className="tnum opacity-70">{count}</span>
              </button>
            ))}
          </div>

          <div className="ml-auto flex items-center gap-2">
            <select
              value={source}
              onChange={(e) => setSource(e.target.value)}
              className="h-9 rounded-md border border-input bg-background px-2.5 text-sm outline-none focus:border-ring focus:ring-1 focus:ring-ring"
            >
              {/* Only the sources a lead can actually have. The mockup lists
                  AutoTrader, CarGurus and Cars.com by name; the importer keeps
                  the marketplace on a captured field and the source is 'adf'
                  for all of them, because that is what the document proves. */}
              <option value="">All sources</option>
              <option value="chat">Website chat</option>
              <option value="website">Website form</option>
              <option value="phone">Phone</option>
              <option value="adf">Lead feed (ADF)</option>
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
          <Empty title="No leads match" hint="Try a different tab or filter." />
        ) : (
          <>
          {/* Below md the same rows render as cards. A rep on the floor needs
              last touch and the vehicle at a glance, and in a scrolled table
              those are the columns that fall off the right-hand edge -- so the
              one screen they actually use would show name and stage only. */}
          <ul className="divide-y divide-border md:hidden">
            {visible.map((lead) => (
              <LeadCard key={lead.id} lead={lead} onOpen={() => setOpenId(lead.id)} />
            ))}
          </ul>
          <div className="scroll-thin hidden overflow-x-auto md:block">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  {['Lead', 'Stage', 'Vehicle of interest', 'Source', 'Assigned', 'Last touch'].map(
                    (h) => (
                      <th
                        key={h}
                        className="h-10 whitespace-nowrap px-3 text-left font-medium text-muted-foreground first:pl-4"
                      >
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {visible.map((lead) => (
                  <Row key={lead.id} lead={lead} onOpen={() => setOpenId(lead.id)} />
                ))}
              </tbody>
            </table>
          </div>
          </>
        )}

        <div className="flex flex-wrap items-center gap-3 border-t border-border px-4 py-3">
          {/* The mockup promises round-robin after 12 hours. Auto-assign is
              real but runs when a rep clicks it on an appointment -- nothing
              sweeps the queue on a timer. */}
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Icon name="clock" className="h-3.5 w-3.5 shrink-0" />
            Nothing assigns on a timer. Auto-assign is round robin, run from an appointment.
          </p>
          <span className="tnum ml-auto text-xs text-muted-foreground">
            {visible.length} of {leads.length}
          </span>
        </div>
      </Card>

      <LeadDrawer id={openId} onClose={() => setOpenId(null)} />
    </main>
  )
}

function Counter({
  label,
  value,
  note,
  tone = 'muted',
}: {
  label: string
  value: number
  note: string
  tone?: 'muted' | 'warning' | 'primary'
}) {
  return (
    <Card className="p-4 shadow-sm">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="tnum text-2xl font-bold">{value}</span>
        <span
          className={clsx(
            'text-xs',
            tone === 'primary' && 'text-primary',
            tone === 'warning' && 'text-warning',
            tone === 'muted' && 'text-muted-foreground',
          )}
        >
          {note}
        </span>
      </div>
    </Card>
  )
}

function LeadCard({ lead, onOpen }: { lead: Lead; onOpen: () => void }) {
  const [stageLabel, stageClass] = STAGE[lead.stage ?? 'new'] ?? STAGE.new

  return (
    <li>
      <button onClick={onOpen} className="w-full px-4 py-3 text-left active:bg-muted">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground">
            {initials(lead.name)}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate font-medium">{lead.name}</span>
              <span
                className={clsx(
                  'tnum shrink-0 text-xs',
                  ageClass(lead.last_touch_at),
                )}
              >
                {relative(lead.last_touch_at ?? lead.created_at)}
              </span>
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {lead.email || <span className="text-primary">No email -- call back</span>}
            </div>
            {lead.vehicle_of_interest && (
              <div className="mt-1 truncate text-xs">
                {lead.vehicle_of_interest.title}
                <span className="tnum ml-1.5 text-muted-foreground">
                  {money(lead.vehicle_of_interest.price)}
                </span>
              </div>
            )}
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <span
                className={clsx(
                  'inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium',
                  stageClass,
                )}
              >
                {stageLabel}
              </span>
              {lead.flagged && (
                <span className="inline-flex items-center rounded border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                  Needs a person
                </span>
              )}
              <span className="text-[11px] text-muted-foreground">
                {SOURCE_LABEL[lead.source] ?? lead.source}
                {' -- '}
                {lead.assigned_to ? lead.assigned_to.name : 'Unclaimed'}
              </span>
            </div>
          </div>
        </div>
      </button>
    </li>
  )
}

function Row({ lead, onOpen }: { lead: Lead; onOpen: () => void }) {
  const [stageLabel, stageClass] = STAGE[lead.stage ?? 'new'] ?? STAGE.new

  return (
    <tr
      onClick={onOpen}
      className="cursor-pointer border-b border-border transition-colors last:border-0 hover:bg-muted/50"
    >
      <td className="py-2.5 pl-4 pr-3">
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground">
            {initials(lead.name)}
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="truncate font-medium">{lead.name}</span>
              {lead.flagged && (
                <span className="inline-flex shrink-0 items-center rounded border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                  Needs a person
                </span>
              )}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {lead.email || (
                <span className="text-primary">No email -- call back</span>
              )}
            </div>
          </div>
        </div>
      </td>
      <td className="px-3 py-2.5">
        <span
          className={clsx(
            'inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium',
            stageClass,
          )}
        >
          {stageLabel}
        </span>
      </td>
      <td className="px-3 py-2.5">
        {lead.vehicle_of_interest ? (
          <div className="min-w-0">
            <div className="truncate">{lead.vehicle_of_interest.title}</div>
            <div className="tnum text-xs text-muted-foreground">
              {money(lead.vehicle_of_interest.price)}
            </div>
          </div>
        ) : (
          <span className="text-muted-foreground">--</span>
        )}
      </td>
      <td className="whitespace-nowrap px-3 py-2.5 text-muted-foreground">
        {SOURCE_LABEL[lead.source] ?? lead.source}
      </td>
      <td className="whitespace-nowrap px-3 py-2.5">
        {lead.assigned_to ? (
          lead.assigned_to.name
        ) : (
          <span className="text-muted-foreground">Unclaimed</span>
        )}
      </td>
      <td className={clsx('tnum whitespace-nowrap px-3 py-2.5', ageClass(lead.last_touch_at))}>
        {relative(lead.last_touch_at ?? lead.created_at)}
      </td>
    </tr>
  )
}
