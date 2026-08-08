import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { useQuery } from '@tanstack/react-query'
import {
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { api, ApiError } from '../lib/api'
import { relative, waited } from '../lib/format'
import type { Appointment, Escalation, Lead, Overview } from '../lib/types'
import { Card, Empty, NotBacked, Spinner, Unavailable } from '../components/ui'
import { Icon, type IconName } from '../components/Icon'
import { PageIntro } from '../components/dashboard/AppShell'

/** Neutral ramp from the token layer -- a share of a total is not a category. */
const RAMP = [
  'var(--color-ramp-1)',
  'var(--color-ramp-2)',
  'var(--color-ramp-3)',
  'var(--color-ramp-4)',
]

const KPI_ICONS: Record<string, IconName> = {
  chat: 'chat',
  email: 'mail',
  calls: 'phone',
  appointments_set: 'calendar',
  needs_a_person: 'user',
  credit_apps: 'file',
}

/** Where each figure came from, so the card is a way in rather than a number
 *  to go and look up somewhere else. */
const KPI_LINKS: Record<string, string> = {
  chat: '/app/conversations?channel=chat',
  email: '/app/leads',
  calls: '/app/conversations?channel=voice',
  appointments_set: '/app/calendar',
  needs_a_person: '/app/conversations?filter=flagged',
  credit_apps: '/app/leads',
}

type TrendRange = 'today' | 'yesterday' | 'week' | 'month' | 'custom'

/** A named window, or two dates. `to` empty means a single day -- picking
 *  one date should not require typing it twice. */
interface TrendChoice {
  range: TrendRange
  from: string
  to: string
}

const TODAY: TrendChoice = { range: 'today', from: '', to: '' }

function trendQuery({ range, from, to }: TrendChoice): string {
  if (range !== 'custom') return `range=${range}`
  return `from=${from}${to ? `&to=${to}` : ''}`
}

interface Trend {
  range: TrendRange
  label: string
  days: number
  conversations: number
  by_hour: { hour: number; count: number; open: boolean }[]
  source_mix: { source: string; count: number }[]
}

/** The pie's hole is a circle. "Today, midnight to now" does not fit in one,
 *  so the denominator gets its own short form. */
const SHORT_RANGE: Record<TrendRange, string> = {
  today: 'leads today',
  yesterday: 'leads yesterday',
  week: 'leads this week',
  month: 'leads this month',
  custom: 'leads',
}

const RANGE_LABELS: [TrendRange, string][] = [
  ['today', 'Today'],
  ['yesterday', 'Yesterday'],
  ['week', 'Week'],
  ['month', 'Month'],
  ['custom', 'Dates'],
]

/** Title, window and the range picker, shared by both charts so the two never
 *  drift into describing their windows differently. */
function TrendHeader({
  title,
  subtitle,
  choice,
  onChoice,
  error,
  legend = false,
}: {
  title: string
  subtitle: string
  choice: TrendChoice
  onChoice: (next: TrendChoice) => void
  /** What the server said about these dates. It owns the rules -- a range that
   *  is backwards or a year long is refused there, and repeating that logic
   *  here would give two answers to the same question. */
  error?: string
  legend?: boolean
}) {
  const today = new Date().toISOString().slice(0, 10)
  return (
    <div className="p-6 pb-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-semibold leading-none tracking-tight">{title}</h3>
          <p className="mt-1.5 text-sm text-muted-foreground">{subtitle}</p>
        </div>
        <div className="inline-flex shrink-0 rounded-lg border border-border p-0.5">
          {RANGE_LABELS.map(([key, label]) => (
            <button
              key={key}
              onClick={() =>
                onChoice(
                  key === 'custom'
                    ? { range: 'custom', from: choice.from || today, to: choice.to }
                    : { range: key, from: '', to: '' },
                )
              }
              className={clsx(
                'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
                choice.range === key
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {choice.range === 'custom' && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input
            type="date"
            value={choice.from}
            max={today}
            onChange={(e) => onChoice({ ...choice, from: e.target.value })}
            className="h-8 rounded-md border border-input bg-background px-2 text-xs"
          />
          <span className="text-xs text-muted-foreground">to</span>
          <input
            type="date"
            value={choice.to}
            min={choice.from}
            max={today}
            onChange={(e) => onChoice({ ...choice, to: e.target.value })}
            className="h-8 rounded-md border border-input bg-background px-2 text-xs"
          />
          {choice.to && (
            <button
              onClick={() => onChoice({ ...choice, to: '' })}
              className="text-xs font-medium text-primary hover:underline"
            >
              One day
            </button>
          )}
        </div>
      )}
      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
      {legend && (
        <div className="mt-2 flex items-center gap-4 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm bg-primary" />
            Closed hours
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm bg-border" />
            Open hours
          </span>
        </div>
      )}
    </div>
  )
}

const SOURCE_LABELS: Record<string, string> = {
  chat: 'Website chat',
  phone: 'Phone',
  website: 'Website form',
}

export function OverviewPage() {
  const navigate = useNavigate()
  const [showAll, setShowAll] = useState(false)
  const [showAllLeads, setShowAllLeads] = useState(false)
  const [showAllFlagged, setShowAllFlagged] = useState(false)
  // One selector per chart rather than one for both: a rep looking at where
  // leads came from this month is often still looking at today's hours.
  const [hourChoice, setHourChoice] = useState<TrendChoice>(TODAY)
  const [sourceChoice, setSourceChoice] = useState<TrendChoice>(TODAY)

  const { data: hourTrend, error: hourError } = useQuery({
    queryKey: ['trends', trendQuery(hourChoice)],
    queryFn: () => api.get<Trend>(`/api/overview/trends?${trendQuery(hourChoice)}`),
    enabled: hourChoice.range !== 'custom' || Boolean(hourChoice.from),
    retry: false,
  })
  const { data: sourceTrend, error: sourceError } = useQuery({
    queryKey: ['trends', trendQuery(sourceChoice)],
    queryFn: () => api.get<Trend>(`/api/overview/trends?${trendQuery(sourceChoice)}`),
    enabled: sourceChoice.range !== 'custom' || Boolean(sourceChoice.from),
    retry: false,
  })
  const { data, isLoading } = useQuery({
    queryKey: ['overview'],
    queryFn: () => api.get<Overview>('/api/overview'),
  })

  if (isLoading || !data) return <Spinner />

  const escalations = data.queues.needs_a_person
  const unconfirmed = data.queues.unconfirmed_appointments
  // The mockup folds unconfirmed appointments into this queue as one summary
  // row rather than giving them a panel: from a rep's side it is the same
  // question -- who is waiting on a person -- so the count follows suit.
  const waitingCount = escalations.length + (unconfirmed.length ? 1 : 0)
  // The endpoint returns this queue oldest first.
  const oldest = escalations[0]

  // The server sends today's conversations newest-activity first and the
  // two-hour mark to split them at, so "now" means the same thing here as it
  // does in the query that built the list.
  const today = data.queues.active_conversations
  const recent = today.filter((c) => (c.last_activity_at ?? c.started_at) >= data.happening_now_since)
  const earlier = today.filter((c) => !recent.includes(c))
  const live = showAll ? today : recent
  const unclaimed = data.queues.unclaimed_leads

  return (
    <main className="p-4 md:p-6">
      <PageIntro
        accent
        title="Overview"
        subtitle={`${data.dealership.name} -- ${data.dealership.address}`}
        actions={
          <>
            <Unavailable
              label="Today"
              why="Every figure here is the last 24 hours. Nothing rolls conversations up by day, so there is no range to select."
            />
            <Unavailable
              label="Export"
              why="No export endpoint exists. The same data is available from /api/overview."
            />
          </>
        }
      />

      {/* ---- KPIs: four cards, the four the endpoint computes -------------- */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {data.kpis.map((kpi) => (
          <Link
            key={kpi.key}
            to={KPI_LINKS[kpi.key] ?? '/app'}
            className="rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Card className="h-full shadow-sm transition-colors hover:border-primary">
              <div className="flex items-center justify-between p-6 pb-2">
                <h3 className="text-sm font-medium text-primary">{kpi.label}</h3>
                <Icon
                  name={KPI_ICONS[kpi.key] ?? 'file'}
                  className="h-4 w-4 text-muted-foreground"
                />
              </div>
              <div className="p-6 pt-0">
                <div className="tnum text-2xl font-bold">{kpi.value}</div>
                {/* The mockup compares each figure to a 30-day average. Nothing
                    stores a daily rollup, so the card states its own window
                    rather than inventing a trend to sit under the number. */}
                <p
                  className={clsx(
                    'mt-1 text-xs',
                    kpi.unavailable ? 'text-warning-foreground' : 'text-muted-foreground',
                  )}
                >
                  {kpi.window}
                </p>
              </div>
            </Card>
          </Link>
        ))}
      </div>

      {/* ---- Needs a person ----------------------------------------------- */}
      <Card className="mt-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-6">
          <div>
            <div className="flex items-center gap-2">
              <Link
                to="/app/conversations?filter=flagged"
                className="font-semibold leading-none tracking-tight text-primary hover:underline"
              >
                Needs a person
              </Link>
              <span className="tnum inline-flex items-center rounded-full bg-primary px-2 py-0.5 text-xs font-semibold text-primary-foreground">
                {waitingCount}
              </span>
            </div>
            <p className="mt-1.5 text-sm text-muted-foreground">
              {escalations.length
                ? `Liner stopped and is holding. Oldest has waited ${waited(oldest?.created_at)}.`
                : 'Liner has not stopped on anything.'}
            </p>
          </div>
          {escalations.length > 2 && (
            <button
              onClick={() => setShowAllFlagged((was) => !was)}
              className="text-sm font-medium text-primary hover:underline"
            >
              {showAllFlagged ? 'Collapse' : 'Expand'}
            </button>
          )}
        </div>

        {waitingCount === 0 ? (
          <Empty title="Nothing waiting" hint="Liner is handling everything right now." />
        ) : (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  {['Lead', 'Why it stopped', 'Vehicle', 'Channel', 'Waiting'].map((h) => (
                    <th
                      key={h}
                      className="h-10 whitespace-nowrap px-4 text-left font-medium text-muted-foreground first:px-6"
                    >
                      {h}
                    </th>
                  ))}
                  <th className="h-10 whitespace-nowrap px-6 text-right font-medium text-muted-foreground">
                    Action
                  </th>
                </tr>
              </thead>
              <tbody>
                {(showAllFlagged ? escalations : escalations.slice(0, 2)).map((e) => (
                  <EscalationRow key={e.id} escalation={e} />
                ))}
                {unconfirmed.length > 0 && <UnconfirmedRow appointments={unconfirmed} />}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* ---- Unclaimed, then happening now: a line each -------------------- */}
      <div className="mt-4 space-y-4">
        <Card className="min-w-0 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border p-6">
            <div>
              <Link
                to="/app/conversations?filter=unclaimed"
                className="font-semibold leading-none tracking-tight text-primary hover:underline"
              >
                Unclaimed leads
              </Link>
              {/* The mockup captions this "Round robin after 12 hours". There
                  is no rotation in this system, so the panel describes what
                  the queue actually is. */}
              <p className="mt-1.5 text-sm text-muted-foreground">
                Assigned to nobody, longest waiting first
              </p>
            </div>
            <div className="flex items-center gap-3">
              {unclaimed.length > 2 && (
                <button
                  onClick={() => setShowAllLeads((was) => !was)}
                  className="text-sm font-medium text-primary hover:underline"
                >
                  {showAllLeads ? 'Collapse' : 'Expand'}
                </button>
              )}
            </div>
          </div>
          {unclaimed.length === 0 ? (
            <Empty title="All claimed" hint="Every lead has an owner." />
          ) : (
            <div className="divide-y divide-border">
              {(showAllLeads ? unclaimed : unclaimed.slice(0, 2)).map((lead) => (
                <UnclaimedRow key={lead.id} lead={lead} />
              ))}
            </div>
          )}
        </Card>

        <Card className="min-w-0 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border p-6">
            <div>
              <div className="flex items-center gap-2">
                <Link
                  to="/app/conversations"
                  className="font-semibold leading-none tracking-tight text-primary hover:underline"
                >
                  Happening now
                </Link>
                <span className="inline-flex items-center gap-1.5 rounded-full border border-success/30 bg-success-muted px-2 py-0.5 text-xs font-medium text-success">
                  <span className="h-1.5 w-1.5 rounded-full bg-success" />
                  Live
                </span>
              </div>
              <p className="mt-1.5 text-sm text-muted-foreground">
                {showAll ? 'Everything today' : 'Anything with a message in the last two hours'}
              </p>
            </div>
            <div className="flex items-center gap-3">
              {earlier.length > 0 && (
                <button
                  onClick={() => setShowAll((was) => !was)}
                  className="text-sm font-medium text-primary hover:underline"
                >
                  {showAll ? 'Collapse' : 'Expand'}
                </button>
              )}
            </div>
          </div>
          {live.length === 0 ? (
            <Empty
              title="Nothing open"
              hint={
                earlier.length
                  ? 'Nothing in the last two hours. Expand for the rest of today.'
                  : 'No conversation has been active today.'
              }
            />
          ) : (
            <div className="scroll-thin overflow-x-auto">
              <table className="w-full text-sm">
                <tbody>
                  {live.slice(0, showAll ? undefined : 2).map((c) => (
                    <tr
                      key={c.id}
                      onClick={() => navigate(`/app/conversations/${c.id}`)}
                      className="group cursor-pointer border-b border-border transition-colors last:border-0 hover:bg-muted/50"
                    >
                      <td className="px-6 py-3">
                        <div className="font-medium group-hover:underline">
                          {c.lead?.name || 'Unknown caller'}
                        </div>
                        <div className="mt-0.5 max-w-md truncate text-xs text-muted-foreground">
                          {c.summary || `${c.message_count ?? 0} messages -- ${c.stage}`}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <span className="inline-flex items-center rounded-md border border-border px-2 py-0.5 text-xs capitalize text-muted-foreground">
                          {c.channel}
                        </span>
                      </td>
                      <td className="tnum whitespace-nowrap px-4 py-3 text-xs text-muted-foreground">
                        {relative(c.started_at)}
                      </td>
                      <td className="px-6 py-3 text-right">
                        <Link
                          to={`/app/conversations/${c.id}`}
                          onClick={(e) => e.stopPropagation()}
                          className="inline-flex h-8 items-center whitespace-nowrap rounded-md border border-input bg-background px-3 text-xs font-medium transition-colors hover:bg-accent"
                        >
                          {c.status === 'handoff' ? 'Take over' : 'Open'}
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {/* ---- Charts -------------------------------------------------------- */}
      <div className="mt-4 grid gap-4 lg:grid-cols-7">
        <Card className="min-w-0 shadow-sm lg:col-span-3">
          <TrendHeader
            title="Where leads came from"
            subtitle={sourceTrend?.label ?? 'By the source on the lead'}
            choice={sourceChoice}
            onChoice={setSourceChoice}
            error={(sourceError as ApiError | null)?.message}
          />
          <div className="p-6 pt-0">
            <SourceChart
              mix={sourceTrend?.source_mix ?? data.source_mix}
              caption={SHORT_RANGE[sourceChoice.range]}
            />
          </div>
        </Card>
        <Card className="min-w-0 shadow-sm lg:col-span-4">
          <TrendHeader
            title="Conversations by hour"
            subtitle={
              hourTrend
                ? `${hourTrend.label} -- ${hourTrend.conversations} conversation${hourTrend.conversations === 1 ? '' : 's'}`
                : 'Today, midnight to now'
            }
            choice={hourChoice}
            onChoice={setHourChoice}
            error={(hourError as ApiError | null)?.message}
            legend
          />
          <div className="p-6 pt-0">
            <HourChart data={hourTrend?.by_hour ?? data.by_hour} />
          </div>
        </Card>
      </div>

    </main>
  )
}

function EscalationRow({ escalation }: { escalation: Escalation }) {
  const navigate = useNavigate()
  const lead = escalation.lead
  const urgent = Date.now() - new Date(escalation.created_at).getTime() > 6 * 3600_000
  const thread = `/app/conversations/${escalation.conversation_id}`

  // A <tr> cannot be a link, so the row navigates on click. The Take over
  // button inside it stops the event, or clicking it would fire both.
  return (
    <tr
      onClick={() => navigate(thread)}
      className="group cursor-pointer border-b border-border transition-colors last:border-0 hover:bg-muted/50"
    >
      <td className="px-6 py-3">
        <div className="font-medium group-hover:underline">
          {lead?.name || 'Unknown caller'}
        </div>
        <div className="tnum text-xs text-muted-foreground">
          {lead?.phone || lead?.email || 'No contact captured'}
        </div>
      </td>
      <td className="px-4 py-3">
        <span className="inline-flex items-center rounded-md border border-primary/30 bg-accent px-2 py-0.5 text-xs font-medium text-primary">
          {escalation.rule?.label ?? 'Handoff'}
        </span>
        <div className="mt-1 max-w-[15rem] text-xs text-muted-foreground">{escalation.reason}</div>
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-muted-foreground">
        {escalation.vehicle?.title ?? '--'}
      </td>
      <td className="px-4 py-3">
        <span className="inline-flex items-center rounded-md border border-border px-2 py-0.5 text-xs capitalize text-muted-foreground">
          {escalation.channel ?? '--'}
        </span>
      </td>
      <td className="whitespace-nowrap px-4 py-3">
        <span
          className={clsx('tnum', urgent ? 'font-semibold' : 'font-medium')}
          title={urgent ? 'Waiting longer than the rule allows' : undefined}
        >
          {waited(escalation.created_at)}
        </span>
      </td>
      <td className="px-6 py-3 text-right">
        <Link
          to={thread}
          onClick={(e) => e.stopPropagation()}
          className="inline-flex h-8 items-center rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground transition-opacity hover:opacity-90"
        >
          Take over
        </Link>
      </td>
    </tr>
  )
}

/** Booked but never confirmed: one row for the lot, as the mockup has it. */
function UnconfirmedRow({ appointments }: { appointments: Appointment[] }) {
  const navigate = useNavigate()
  const [first, ...rest] = appointments
  const oldest = appointments.reduce((a, b) => (a.created_at < b.created_at ? a : b))

  return (
    <tr
      onClick={() => navigate('/app/calendar')}
      className="group cursor-pointer border-b border-border transition-colors last:border-0 hover:bg-muted/50"
    >
      <td className="px-6 py-3">
        <div className="font-medium group-hover:underline">
          {first.lead?.name || 'Unknown caller'}
          {rest.length > 0 && <span className="text-muted-foreground"> +{rest.length}</span>}
        </div>
        <div className="text-xs text-muted-foreground">Unconfirmed appointments</div>
      </td>
      <td className="px-4 py-3">
        <span className="inline-flex items-center rounded-md border border-primary/30 bg-accent px-2 py-0.5 text-xs font-medium text-primary">
          Not confirmed
        </span>
        <div className="mt-1 max-w-[15rem] text-xs text-muted-foreground">
          Booked, and nobody has heard back
        </div>
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-muted-foreground">
        {first.vehicle?.title ?? '--'}
      </td>
      <td className="px-4 py-3">
        <span className="inline-flex items-center rounded-md border border-border px-2 py-0.5 text-xs text-muted-foreground">
          Calendar
        </span>
      </td>
      <td className="whitespace-nowrap px-4 py-3">
        <span className="tnum font-medium">{waited(oldest.created_at)}</span>
      </td>
      <td className="px-6 py-3 text-right">
        <Link
          to="/app/calendar"
          onClick={(e) => e.stopPropagation()}
          className="inline-flex h-8 items-center rounded-md border border-input bg-background px-3 text-xs font-medium transition-colors hover:bg-accent"
        >
          Confirm
        </Link>
      </td>
    </tr>
  )
}

function UnclaimedRow({ lead }: { lead: Lead }) {
  const navigate = useNavigate()
  const hours = (Date.now() - new Date(lead.created_at).getTime()) / 3600_000
  // Waiting long enough to matter, or not. A third colour in between was
  // amber, which reads as an alert next to blue everywhere else.
  const tone = hours >= 4 ? 'bg-accent font-semibold text-primary' : 'bg-muted text-muted-foreground'

  return (
    <div
      onClick={() =>
        navigate(lead.conversation_id ? `/app/conversations/${lead.conversation_id}` : '/app/leads')
      }
      className="group flex cursor-pointer items-center gap-3 px-6 py-3 transition-colors hover:bg-muted/50"
    >
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium group-hover:underline">
          {lead.name || 'Unknown caller'}
        </div>
        <div className="truncate text-xs text-muted-foreground">
          {SOURCE_LABELS[lead.source] ?? lead.source}
          {lead.contact_risk && ' -- no email on file'}
        </div>
      </div>
      <span className={`tnum rounded-md px-2 py-0.5 text-xs font-medium ${tone}`}>
        {waited(lead.created_at)}
      </span>
    </div>
  )
}

/**
 * Bars are coloured by whether the showroom was open -- which is the point the
 * chart makes: the bars standing outside the pale ones are business Liner
 * caught that a voicemail would have lost. Open/closed comes from the endpoint,
 * which reads `hours_json`; this component never decides what "open" means.
 */
function HourChart({ data }: { data: Overview['by_hour'] }) {
  const rows = data.map((d) => ({
    ...d,
    label: `${d.hour % 12 || 12}${d.hour < 12 ? 'a' : 'p'}`,
  }))

  return (
    <div className="h-[260px]">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: -22 }}>
          <XAxis
            dataKey="label"
            tickLine={false}
            interval={2}
            tick={{ fontSize: 11, fill: 'var(--color-muted-foreground)' }}
            stroke="var(--color-border)"
          />
          <YAxis
            allowDecimals={false}
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 11, fill: 'var(--color-muted-foreground)' }}
          />
          <Tooltip
            cursor={{ fill: 'var(--color-muted)' }}
            contentStyle={{
              borderRadius: 6,
              border: '1px solid var(--color-border)',
              background: 'var(--color-popover)',
              fontSize: 12,
            }}
            formatter={(value, _name, item) => [
              `${value} ${value === 1 ? 'conversation' : 'conversations'}`,
              (item?.payload as { open?: boolean } | undefined)?.open
                ? 'Showroom open'
                : 'Showroom closed',
            ]}
          />
          <Bar dataKey="count" radius={[3, 3, 0, 0]} maxBarSize={22}>
            {rows.map((row) => (
              <Cell
                key={row.hour}
                fill={row.open ? 'var(--color-border)' : 'var(--color-primary)'}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function SourceChart({
  mix,
  caption,
}: {
  mix: Overview['source_mix']
  /** The hole says what the denominator is. With a range picker above it,
   *  "leads today" was a caption that stopped being true the moment anyone
   *  chose a different window. Named `caption` rather than `window`, which is
   *  a DOM global and typechecks cleanly while meaning nothing. */
  caption: string
}) {
  const total = mix.reduce((sum, m) => sum + m.count, 0)

  if (!total) {
    return (
      <div className="flex h-[260px] items-center">
        <NotBacked
          title={`No ${caption}`}
          why="This splits the chosen window by the source recorded on each lead."
        />
      </div>
    )
  }

  const rows = [...mix].sort((a, b) => b.count - a.count)

  return (
    <div className="flex h-[260px] items-center gap-4">
      <div className="relative h-full flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={rows}
              dataKey="count"
              nameKey="source"
              innerRadius="62%"
              outerRadius="92%"
              stroke="none"
              paddingAngle={1}
            >
              {rows.map((row, i) => (
                <Cell key={row.source} fill={RAMP[i % RAMP.length]} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                borderRadius: 6,
                border: '1px solid var(--color-border)',
                background: 'var(--color-popover)',
                fontSize: 12,
              }}
              formatter={(value: number, name: string) => [
                `${value} of ${total} -- ${Math.round((value / total) * 100)}%`,
                SOURCE_LABELS[name] ?? name,
              ]}
            />
          </PieChart>
        </ResponsiveContainer>
        {/* The hole carries the denominator, so a slice is a share of something
            without the reader going to the legend for it. */}
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="tnum text-xl font-semibold">{total}</span>
          <span className="text-[11px] text-muted-foreground">{caption}</span>
        </div>
      </div>
      <ul className="shrink-0 space-y-2 text-xs">
        {rows.map((row, i) => (
          <li key={row.source} className="flex items-center gap-2">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ background: RAMP[i % RAMP.length] }}
            />
            <span className="text-muted-foreground">
              {SOURCE_LABELS[row.source] ?? row.source}
            </span>
            <span className="tnum ml-auto whitespace-nowrap pl-2 font-medium">
              {Math.round((row.count / total) * 100)}%
              <span className="ml-1.5 font-normal text-muted-foreground">({row.count})</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
