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

export function CalendarPage() {
  const [weekOffset, setWeekOffset] = useState(0)
  const [openId, setOpenId] = useState<string | null>(null)

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
        subtitle={`${days[0].toLocaleDateString('en-US', { month: 'long', day: 'numeric' })} -- ${days[6].toLocaleDateString('en-US', { month: 'long', day: 'numeric' })}`}
        actions={
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
        }
      />

      <div className="p-6">
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
                            : 'border-warning/30 bg-warning-muted text-warning-foreground',
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
                className="pointer-events-none absolute left-14 right-0 border-t-2 border-destructive"
                style={{ top: nowTop }}
              >
                <span className="absolute -top-2 -left-2 h-3 w-3 rounded-full bg-destructive" />
              </div>
            )}
          </div>
        </Card>
      </div>

      <AppointmentDrawer id={openId} onClose={() => setOpenId(null)} />
    </>
  )
}

/** Act 2 lives here: confirm, assign, reach out. */
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
            <Badge tone={appointment.status === 'confirmed' ? 'success' : 'warning'}>
              {appointment.status}
            </Badge>
            <Badge tone="neutral">booked by {appointment.booked_by}</Badge>
            {appointment.assigned_to ? (
              <Badge tone="primary">{appointment.assigned_to.name}</Badge>
            ) : (
              <Badge tone="warning">Unassigned</Badge>
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
                      <Badge tone={item.status === 'sent' ? 'success' : 'destructive'}>
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
