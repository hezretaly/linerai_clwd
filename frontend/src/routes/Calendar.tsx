import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'

import { api, ApiError } from '../lib/api'
import { dateTime, isOpenOn, money, openWindow, time } from '../lib/format'
import type { Appointment, Overview, TeamMember } from '../lib/types'
import { Badge, Button, Card, Empty, Field, Input, Sheet, Spinner } from '../components/ui'
import { PageHeader } from '../components/dashboard/AppShell'

const HOUR_PX = 56

/** Overlap packing: appointments sharing a time slot split the column.
 *  Ported from the mockups -- the only genuinely tricky bit of layout here. */
function packLanes(appointments: Appointment[]): (Appointment & { lane: number; lanes: number })[] {
  const sorted = [...appointments].sort(
    (a, b) => new Date(a.starts_at).getTime() - new Date(b.starts_at).getTime(),
  )
  const packed: (Appointment & { lane: number; lanes: number })[] = []
  let cluster: (Appointment & { lane: number; lanes: number })[] = []
  let clusterEnd = 0

  const flush = () => {
    const lanes = cluster.reduce((max, item) => Math.max(max, item.lane + 1), 0)
    cluster.forEach((item) => {
      item.lanes = lanes
      packed.push(item)
    })
    cluster = []
  }

  for (const appointment of sorted) {
    const start = new Date(appointment.starts_at).getTime()
    const end = start + appointment.duration_min * 60_000
    if (cluster.length && start >= clusterEnd) {
      flush()
      clusterEnd = 0
    }
    const taken = new Set(
      cluster
        .filter((item) => new Date(item.starts_at).getTime() + item.duration_min * 60_000 > start)
        .map((item) => item.lane),
    )
    let lane = 0
    while (taken.has(lane)) lane += 1
    cluster.push({ ...appointment, lane, lanes: 1 })
    clusterEnd = Math.max(clusterEnd, end)
  }
  flush()
  return packed
}

function startOfWeek(date: Date): Date {
  const copy = new Date(date)
  copy.setHours(0, 0, 0, 0)
  copy.setDate(copy.getDate() - copy.getDay())
  return copy
}

/** Remembered, because whichever of the two you work from is a habit, not a
 *  per-visit decision. */
const VIEW_KEY = 'liner.calendar.view'

export function CalendarPage() {
  const [weekOffset, setWeekOffset] = useState(0)
  const [openId, setOpenId] = useState<string | null>(null)
  const [view, setView] = useState<'week' | 'list'>(
    () => (localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'week'),
  )

  const setMode = (next: 'week' | 'list') => {
    localStorage.setItem(VIEW_KEY, next)
    setView(next)
  }

  const { data: overview } = useQuery({
    queryKey: ['overview'],
    queryFn: () => api.get<Overview>('/api/overview'),
  })
  const { data, isLoading } = useQuery({
    queryKey: ['appointments'],
    queryFn: () => api.get<{ appointments: Appointment[] }>('/api/appointments'),
  })

  const dealership = overview?.dealership
  const [openHour, closeHour] = openWindow(dealership)

  const weekStart = useMemo(() => {
    const base = startOfWeek(new Date())
    base.setDate(base.getDate() + weekOffset * 7)
    return base
  }, [weekOffset])

  const days = useMemo(
    () =>
      Array.from({ length: 7 }, (_, index) => {
        const day = new Date(weekStart)
        day.setDate(day.getDate() + index)
        return day
      }),
    [weekStart],
  )

  if (isLoading || !data) return <Spinner />

  const now = new Date()
  const showNowLine = days.some((d) => d.toDateString() === now.toDateString())
  const nowTop = (now.getHours() + now.getMinutes() / 60 - openHour) * HOUR_PX

  return (
    <>
      <PageHeader
        title="Calendar"
        subtitle={
          view === 'week'
            ? `${days[0].toLocaleDateString('en-US', { month: 'long', day: 'numeric' })} -- ${days[6].toLocaleDateString('en-US', { month: 'long', day: 'numeric' })}`
            : 'Everything booked, in the order it happens'
        }
        actions={
          <>
            <ViewToggle view={view} onChange={setMode} />
            {/* Only the week is paged. The list runs from now to the end of
                what is booked, so there is nothing to page through -- and
                leaving dead Previous/Next buttons beside it would be three
                controls where one of them does nothing. */}
            {view === 'week' && (
              <>
                <Button size="sm" onClick={() => setWeekOffset((w) => w - 1)}>
                  Previous
                </Button>
                <Button size="sm" onClick={() => setWeekOffset(0)}>
                  Today
                </Button>
                <Button size="sm" onClick={() => setWeekOffset((w) => w + 1)}>
                  Next
                </Button>
              </>
            )}
          </>
        }
      />

      {view === 'list' && (
        <div className="p-4 md:p-6">
          <BookedList appointments={data.appointments} onOpen={setOpenId} />
        </div>
      )}

      {/* A seven-day grid in 390px gives every appointment about 44px, which
          renders as "De..." -- present, unreadable, unusable. On a phone the
          same week becomes an agenda: the question a rep is asking there is
          "what is on today", not "how does my week lay out". */}
      <div className={clsx('p-4 md:hidden', view !== 'week' && 'hidden')}>
        <Agenda
          days={days}
          appointments={data.appointments}
          dealership={dealership}
          onOpen={setOpenId}
        />
      </div>

      <div className={clsx('hidden p-6', view === 'week' && 'md:block')}>
        <Card className="overflow-hidden">
          <div className="grid grid-cols-[3.5rem_repeat(7,1fr)] border-b border-border">
            <div />
            {days.map((day) => {
              const open = isOpenOn(dealership, day)
              return (
                <div
                  key={day.toISOString()}
                  className={clsx(
                    'border-l border-border px-2 py-2 text-center',
                    !open && 'bg-muted/60',
                  )}
                >
                  <p className="text-xs text-muted-foreground">
                    {day.toLocaleDateString('en-US', { weekday: 'short' })}
                  </p>
                  <p
                    className={clsx(
                      'text-sm font-medium',
                      day.toDateString() === now.toDateString() && 'text-primary',
                    )}
                  >
                    {day.getDate()}
                  </p>
                  {!open && <p className="text-[10px] text-muted-foreground">Closed</p>}
                </div>
              )
            })}
          </div>

          <div className="relative grid grid-cols-[3.5rem_repeat(7,1fr)]">
            <div>
              {Array.from({ length: closeHour - openHour }, (_, index) => (
                <div
                  key={index}
                  style={{ height: HOUR_PX }}
                  className="pr-2 pt-0.5 text-right text-[11px] text-muted-foreground"
                >
                  {((openHour + index) % 12 || 12) + (openHour + index < 12 ? 'a' : 'p')}
                </div>
              ))}
            </div>

            {days.map((day) => {
              const dayAppointments = packLanes(
                data.appointments.filter(
                  (a) => new Date(a.starts_at).toDateString() === day.toDateString(),
                ),
              )
              const open = isOpenOn(dealership, day)
              return (
                <div
                  key={day.toISOString()}
                  className={clsx('relative border-l border-border', !open && 'bg-muted/40')}
                >
                  {Array.from({ length: closeHour - openHour }, (_, index) => (
                    <div
                      key={index}
                      style={{ height: HOUR_PX }}
                      className="border-b border-border/60"
                    />
                  ))}

                  {dayAppointments.map((appointment) => {
                    const start = new Date(appointment.starts_at)
                    const top =
                      (start.getHours() + start.getMinutes() / 60 - openHour) * HOUR_PX
                    return (
                      <button
                        key={appointment.id}
                        onClick={() => setOpenId(appointment.id)}
                        style={{
                          top,
                          height: (appointment.duration_min / 60) * HOUR_PX - 2,
                          left: `${(appointment.lane / appointment.lanes) * 100}%`,
                          width: `${100 / appointment.lanes}%`,
                        }}
                        className={clsx(
                          'absolute overflow-hidden rounded-md border px-1.5 py-1 text-left text-[11px] animate-cell-fill',
                          appointment.status === 'confirmed'
                            ? 'border-success/30 bg-success-muted text-success'
                            : 'border-primary/30 bg-accent text-primary',
                        )}
                      >
                        <p className="truncate font-medium">{appointment.lead?.name}</p>
                        <p className="truncate opacity-80">{time(appointment.starts_at)}</p>
                      </button>
                    )
                  })}
                </div>
              )
            })}

            {showNowLine && nowTop > 0 && nowTop < (closeHour - openHour) * HOUR_PX && (
              <div
                className="pointer-events-none absolute left-14 right-0 border-t-2 border-primary"
                style={{ top: nowTop }}
              >
                <span className="absolute -top-2 -left-2 h-3 w-3 rounded-full bg-primary" />
              </div>
            )}
          </div>
        </Card>
      </div>

      <AppointmentDrawer id={openId} onClose={() => setOpenId(null)} />
    </>
  )
}

function ViewToggle({
  view,
  onChange,
}: {
  view: 'week' | 'list'
  onChange: (next: 'week' | 'list') => void
}) {
  return (
    <div className="inline-flex overflow-hidden rounded-md border border-input">
      {(['week', 'list'] as const).map((mode) => (
        <button
          key={mode}
          onClick={() => onChange(mode)}
          aria-pressed={view === mode}
          className={clsx(
            'h-8 px-3 text-sm font-medium capitalize transition-colors',
            view === mode
              ? 'bg-accent text-accent-foreground'
              : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
          )}
        >
          {mode}
        </button>
      ))}
    </div>
  )
}

/**
 * Every booking in the order it happens, which is the question the grid is
 * bad at: "what is next" needs one glance down a column, not a week laid out
 * spatially and then paged through to find the one in twelve days.
 *
 * It answers from now forward and says how many it is not showing rather than
 * silently starting at today -- a list that quietly drops the past is one a
 * rep cannot use to check what happened this morning. Cancelled and no-show
 * are here too, greyed: "nothing booked" and "they cancelled" are different
 * facts and only one of them needs a phone call.
 */
function BookedList({
  appointments,
  onOpen,
}: {
  appointments: Appointment[]
  onOpen: (id: string) => void
}) {
  const [showPast, setShowPast] = useState(false)
  const [showCancelled, setShowCancelled] = useState(false)
  const now = Date.now()

  // An appointment is "past" once it has finished, not once it has started --
  // a rep looking at the list mid-visit should still find the one they are in.
  const isPast = (a: Appointment) =>
    new Date(a.starts_at).getTime() + a.duration_min * 60_000 < now
  const isOff = (a: Appointment) => a.status === 'cancelled' || a.status === 'no_show'

  const { shown, hiddenPast, hiddenOff } = useMemo(() => {
    const sorted = [...appointments].sort((a, b) => a.starts_at.localeCompare(b.starts_at))
    // One predicate, used for the rows and for the count above them. Two
    // copies is how a heading says 16 over a list of 147 -- which this did,
    // because the count filtered to live bookings and the list did not.
    const keep = (a: Appointment) =>
      (showPast || !isPast(a)) && (showCancelled || !isOff(a))
    return {
      shown: sorted.filter(keep),
      // What each toggle would add, so a button can say what it is for.
      hiddenPast: sorted.filter((a) => isPast(a) && (showCancelled || !isOff(a))).length,
      hiddenOff: sorted.filter((a) => isOff(a) && (showPast || !isPast(a))).length,
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appointments, showPast, showCancelled, now])

  if (!appointments.length) {
    return (
      <Card>
        <Empty
          title="Nothing booked"
          hint="Appointments appear here the moment Liner books one, or a rep does."
        />
      </Card>
    )
  }

  // Grouped by day, because a bare list of forty rows makes somebody read
  // every date to find where tomorrow starts.
  const days: { key: string; date: Date; rows: Appointment[] }[] = []
  for (const appointment of shown) {
    const date = new Date(appointment.starts_at)
    const key = date.toDateString()
    if (days.at(-1)?.key !== key) days.push({ key, date, rows: [] })
    days.at(-1)!.rows.push(appointment)
  }

  const today = new Date().toDateString()

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="tnum text-sm text-muted-foreground">
          <span className="font-medium text-foreground">{shown.length}</span>{' '}
          {shown.length === 1 ? 'appointment' : 'appointments'}
          {showPast ? '' : ' still to come'}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {(hiddenPast > 0 || showPast) && (
            <Button size="sm" onClick={() => setShowPast((was) => !was)}>
              {showPast ? 'Hide past' : `Show ${hiddenPast} past`}
            </Button>
          )}
          {/* Kept off by default: the ask was the booked ones, and a
              cancellation counted among them would overstate the day. Offered
              rather than dropped, because "nothing booked" and "they
              cancelled" are different facts and only one needs a phone call. */}
          {(hiddenOff > 0 || showCancelled) && (
            <Button size="sm" onClick={() => setShowCancelled((was) => !was)}>
              {showCancelled ? 'Hide cancelled' : `Show ${hiddenOff} cancelled`}
            </Button>
          )}
        </div>
      </div>

      {!shown.length ? (
        <Card>
          <Empty
            title="Nothing still to come"
            hint="Everything booked has already happened. The buttons above bring the rest back."
          />
        </Card>
      ) : (
        days.map(({ key, date, rows }) => (
          <Card key={key} className="overflow-hidden">
            <div className="flex items-baseline justify-between border-b border-border bg-muted/40 px-4 py-2">
              <span
                className={clsx('text-sm font-medium', key === today && 'text-primary')}
              >
                {date.toLocaleDateString('en-US', {
                  weekday: 'long', month: 'short', day: 'numeric',
                })}
                {key === today && ' -- today'}
              </span>
              <span className="tnum text-xs text-muted-foreground">{rows.length}</span>
            </div>
            <ul className="divide-y divide-border">
              {rows.map((appointment) => (
                <li key={appointment.id}>
                  <button
                    onClick={() => onOpen(appointment.id)}
                    className={clsx(
                      'flex w-full items-baseline gap-3 px-4 py-3 text-left transition-colors hover:bg-accent',
                      isOff(appointment) && 'opacity-55',
                    )}
                  >
                    <span className="tnum w-16 shrink-0 text-sm font-medium">
                      {time(appointment.starts_at)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {appointment.lead?.name ?? 'Unknown'}
                      </span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {appointment.vehicle?.title ?? 'No vehicle'}
                        {appointment.assigned_to
                          ? ` -- ${appointment.assigned_to.name}`
                          : ' -- unassigned'}
                      </span>
                    </span>
                    {/* `destructive` is this dashboard's word for something
                        that went wrong and needs a person; a cancellation is a
                        fact, not a failure, so it stays neutral. */}
                    <Badge
                      tone={
                        appointment.status === 'confirmed'
                          ? 'success'
                          : isOff(appointment)
                            ? 'neutral'
                            : 'primary'
                      }
                      className="shrink-0"
                    >
                      {appointment.status === 'no_show' ? 'no show' : appointment.status}
                    </Badge>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
        ))
      )}
    </div>
  )
}

/** Act 2 lives here: confirm, assign, reach out. */
/**
 * The phone view of the same week. One card per appointment under a day
 * heading, in time order, with closed days stated rather than drawn as empty
 * columns -- hours come from `hours_json`, so a closed Sunday says Closed
 * instead of looking like a day with nothing booked.
 */
function Agenda({
  days,
  appointments,
  dealership,
  onOpen,
}: {
  days: Date[]
  appointments: Appointment[]
  dealership: Overview['dealership'] | undefined
  onOpen: (id: string) => void
}) {
  const today = new Date().toDateString()

  return (
    <div className="space-y-3">
      {days.map((day) => {
        const mine = appointments
          .filter((a) => new Date(a.starts_at).toDateString() === day.toDateString())
          .sort((a, b) => a.starts_at.localeCompare(b.starts_at))
        const open = isOpenOn(dealership, day)
        // A closed day with nothing booked is not worth a row of its own.
        if (!open && mine.length === 0) return null

        return (
          <Card key={day.toISOString()} className="overflow-hidden">
            <div className="flex items-baseline justify-between border-b border-border bg-muted/40 px-4 py-2">
              <span
                className={clsx(
                  'text-sm font-medium',
                  day.toDateString() === today && 'text-primary',
                )}
              >
                {day.toLocaleDateString('en-US', {
                  weekday: 'long', month: 'short', day: 'numeric',
                })}
                {day.toDateString() === today && ' -- today'}
              </span>
              <span className="text-xs text-muted-foreground">
                {!open ? 'Closed' : mine.length === 0 ? 'Nothing booked' : `${mine.length} booked`}
              </span>
            </div>

            {mine.length > 0 && (
              <ul className="divide-y divide-border">
                {mine.map((appointment) => (
                  <li key={appointment.id}>
                    <button
                      onClick={() => onOpen(appointment.id)}
                      className="flex w-full items-baseline gap-3 px-4 py-3 text-left active:bg-muted"
                    >
                      <span className="tnum w-16 shrink-0 text-sm font-medium">
                        {time(appointment.starts_at)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">
                          {appointment.lead?.name ?? 'Unknown'}
                        </span>
                        <span className="block truncate text-xs text-muted-foreground">
                          {appointment.vehicle?.title ?? 'No vehicle'}
                          {appointment.assigned_to
                            ? ` -- ${appointment.assigned_to.name}`
                            : ' -- unassigned'}
                        </span>
                      </span>
                      <Badge
                        tone={appointment.status === 'confirmed' ? 'success' : 'primary'}
                        className="shrink-0"
                      >
                        {appointment.status}
                      </Badge>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )
      })}
    </div>
  )
}

function AppointmentDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [composing, setComposing] = useState(false)

  const { data: appointment } = useQuery({
    queryKey: ['appointments', id],
    queryFn: () => api.get<Appointment>(`/api/appointments/${id}`),
    enabled: Boolean(id),
  })
  const { data: team } = useQuery({
    queryKey: ['team'],
    queryFn: () => api.get<{ members: TeamMember[] }>('/api/team'),
  })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['appointments'] })
    void queryClient.invalidateQueries({ queryKey: ['overview'] })
  }

  const confirm = useMutation({
    mutationFn: () => api.post(`/api/appointments/${id}/confirm`),
    onSuccess: invalidate,
  })
  const assign = useMutation({
    mutationFn: (payload: { user_id?: string; auto?: boolean }) =>
      api.post(`/api/appointments/${id}/assign`, payload),
    onSuccess: invalidate,
  })
  const draft = useMutation({
    mutationFn: () =>
      api.get<{ subject: string; body: string }>(`/api/appointments/${id}/outreach?draft=1`),
    onSuccess: (data) => {
      setSubject(data.subject)
      setBody(data.body)
      setComposing(true)
    },
  })
  const send = useMutation({
    mutationFn: () => api.post(`/api/appointments/${id}/outreach`, { subject, body }),
    onSuccess: () => {
      setComposing(false)
      invalidate()
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
  })

  const reps = team?.members.filter((m) => m.role === 'rep') ?? []

  return (
    <Sheet
      open={Boolean(id)}
      onClose={onClose}
      title={
        <>
          <h2 className="text-base font-semibold">{appointment?.lead?.name ?? 'Appointment'}</h2>
          <p className="text-sm text-muted-foreground">
            {dateTime(appointment?.starts_at)}
          </p>
        </>
      }
    >
      {!appointment ? (
        <Spinner />
      ) : (
        <div className="space-y-6">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={appointment.status === 'confirmed' ? 'success' : 'primary'}>
              {appointment.status}
            </Badge>
            <Badge tone="neutral">booked by {appointment.booked_by}</Badge>
            {appointment.assigned_to ? (
              <Badge tone="primary">{appointment.assigned_to.name}</Badge>
            ) : (
              <Badge tone="primary">Unassigned</Badge>
            )}
          </div>

          {appointment.vehicle && (
            <div className="flex gap-3">
              <img
                src={appointment.vehicle.photo_url}
                alt=""
                className="h-20 w-28 rounded-lg border border-border object-cover"
              />
              <div>
                <p className="text-sm font-medium">{appointment.vehicle.title}</p>
                <p className="text-sm text-muted-foreground">
                  {money(appointment.vehicle.price)}
                </p>
              </div>
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              disabled={appointment.status !== 'booked' || confirm.isPending}
              onClick={() => confirm.mutate()}
            >
              Confirm appointment
            </Button>
            <Button onClick={() => assign.mutate({ auto: true })} disabled={assign.isPending}>
              Auto-assign
            </Button>
            <Button onClick={() => draft.mutate()} disabled={draft.isPending}>
              Draft outreach
            </Button>
          </div>

          {assign.isError && (
            <p className="text-sm text-destructive">{(assign.error as ApiError).message}</p>
          )}

          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Assign to
            </h3>
            <ul className="mt-2 space-y-1">
              {reps.map((rep) => (
                <li key={rep.id}>
                  <button
                    onClick={() => assign.mutate({ user_id: rep.id })}
                    className={clsx(
                      'flex w-full items-center justify-between rounded-lg border px-3 py-2 text-sm transition-colors duration-150',
                      appointment.assigned_user_id === rep.id
                        ? 'border-primary bg-accent'
                        : 'border-border hover:bg-muted',
                    )}
                  >
                    <span>{rep.name}</span>
                    <span className="text-xs text-muted-foreground">
                      {rep.todays_appointments}/{rep.daily_cap} today
                      {rep.at_capacity && ' -- at cap'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>

          {composing && (
            <section className="space-y-3 rounded-lg border border-border p-3">
              <h3 className="text-sm font-semibold">Reach out</h3>
              <Field label="To">
                <Input value={appointment.lead?.email ?? ''} readOnly />
              </Field>
              <Field label="Subject">
                <Input value={subject} onChange={(e) => setSubject(e.target.value)} />
              </Field>
              <Field label="Message">
                <textarea
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  rows={10}
                  className="w-full rounded-lg border border-input bg-background p-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </Field>
              <div className="flex items-center gap-2">
                <Button variant="primary" onClick={() => send.mutate()} disabled={send.isPending}>
                  Send email
                </Button>
                <Button onClick={() => setComposing(false)}>Cancel</Button>
              </div>
              <p className="text-xs text-muted-foreground">
                Email delivery is not configured, so this is recorded locally and mirrored into
                the buyer's chat thread. No mail leaves the machine.
              </p>
            </section>
          )}

          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Outreach
            </h3>
            {appointment.outreach?.length ? (
              <ul className="mt-2 space-y-2">
                {appointment.outreach.map((item) => (
                  <li key={item.id} className="rounded-lg border border-border p-3">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-sm font-medium">{item.subject}</p>
                      <Badge tone={item.status === 'sent' ? 'success' : 'primary'}>
                        {item.status}
                      </Badge>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {item.to_address} -- {dateTime(item.sent_at ?? item.created_at)}
                    </p>
                    {!item.delivered_externally && item.channel === 'email' && (
                      <p className="mt-1 text-xs text-warning-foreground">
                        Recorded in the local outbox. No mail was delivered.
                      </p>
                    )}
                    {item.error && (
                      <p className="mt-1 text-xs text-destructive">{item.error}</p>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <Empty title="Nothing sent yet" />
            )}
          </section>
        </div>
      )}
    </Sheet>
  )
}
