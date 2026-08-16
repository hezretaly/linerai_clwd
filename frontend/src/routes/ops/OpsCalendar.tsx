/**
 * The demos people have booked with us, by day, plus everything about one of
 * them when you open it.
 *
 * Not the dealership's calendar and not built on it: `/app/calendar` draws
 * appointments against vehicles inside a showroom's opening hours, which is a
 * different table with a different frame. Sharing the layout would have meant
 * one hour grid answering to two sets of hours.
 *
 * Seven day-columns rather than a positioned hour grid: two people take a few
 * demos a week, and stacking them by time would spend most of the screen on
 * empty afternoons while making a 3pm and a 3:30 unreadable at 44px wide.
 */

import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import clsx from 'clsx'

import { dateTime, relative, time } from '../../lib/format'
import { Badge, Button, Card, Empty, Sheet, Spinner } from '../../components/ui'
import { PageIntro } from '../../components/dashboard/AppShell'
import { Icon } from '../../components/Icon'
import { useDemos, useOpsSummary, useSetStatus, type DemoEntry } from './data'

/** Monday. These are business meetings, so a week that starts on Sunday puts
 *  the weekend at both ends and the working week in the middle. */
function startOfWeek(date: Date): Date {
  const copy = new Date(date)
  copy.setHours(0, 0, 0, 0)
  copy.setDate(copy.getDate() - ((copy.getDay() + 6) % 7))
  return copy
}

function sameDay(a: Date, b: Date): boolean {
  return a.toDateString() === b.toDateString()
}

const STATUS_TONE: Record<string, 'primary' | 'neutral' | 'success' | 'destructive'> = {
  new: 'primary',
  seen: 'neutral',
  done: 'success',
  cancelled: 'destructive',
}

export function OpsCalendarPage() {
  const [weekOffset, setWeekOffset] = useState(0)
  const [params, setParams] = useSearchParams()
  const openId = params.get('open')

  const { data, isLoading } = useDemos()
  const { data: summary } = useOpsSummary()
  const setStatus = useSetStatus()

  const requests = data?.requests ?? []
  const open = requests.find((r) => r.id === openId) ?? null

  // Opening one is what marks it read. Not a button: a notification you have
  // read and that is still sitting in the tray is one you learn to ignore,
  // and this dashboard has exactly one thing on it nobody clicked for.
  useEffect(() => {
    if (open && open.status === 'new') {
      setStatus.mutate({ id: open.id, status: 'seen' })
    }
    // Only when the opened row changes -- `setStatus` is a new object each
    // render, and depending on it would re-fire the mutation on every one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open?.id, open?.status])

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

  const scheduled = requests.filter((r) => r.slot_at && r.status !== 'cancelled')
  const cancelled = requests.filter((r) => r.slot_at && r.status === 'cancelled')
  // A support request has no time attached, and a message with no time still
  // has to be somewhere -- otherwise it is only ever visible in the inbox.
  const unscheduled = requests.filter((r) => !r.slot_at)

  const byDay = (day: Date, rows: DemoEntry[]) =>
    rows
      .filter((r) => sameDay(new Date(r.slot_at as string), day))
      .sort((a, b) => (a.slot_at ?? '').localeCompare(b.slot_at ?? ''))

  if (isLoading) return <Spinner />

  const weekEnd = days[6]
  const label = `${days[0].toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} -- ${weekEnd.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`

  return (
    <div className="p-4 md:p-6">
      <PageIntro
        title="Demo calendar"
        subtitle={`${label} · ${summary?.upcoming ?? 0} upcoming · times shown in ${summary?.timezone ?? 'the dealership frame'}`}
        actions={
          <>
            <Button size="sm" onClick={() => setWeekOffset((w) => w - 1)}>
              Previous
            </Button>
            <Button size="sm" onClick={() => setWeekOffset(0)}>
              This week
            </Button>
            <Button size="sm" onClick={() => setWeekOffset((w) => w + 1)}>
              Next
            </Button>
          </>
        }
      />

      {/* Seven columns is roughly 90px each on a phone, which renders a
          dealership name as three letters and an ellipsis. Below lg the same
          week is an agenda: empty days drop out and each entry gets the width
          to say who it is. */}
      <div className="hidden gap-3 lg:grid lg:grid-cols-7">
        {days.map((day) => {
          const rows = byDay(day, scheduled)
          const today = sameDay(day, new Date())
          return (
            <div key={day.toISOString()} className="min-w-0">
              <div
                className={clsx(
                  'mb-2 rounded-md px-2 py-1.5 text-center',
                  today ? 'bg-primary text-primary-foreground' : 'bg-muted/50',
                )}
              >
                <div className="text-xs font-medium uppercase tracking-wide">
                  {day.toLocaleDateString('en-US', { weekday: 'short' })}
                </div>
                <div className="tnum text-sm font-semibold">{day.getDate()}</div>
              </div>
              <div className="space-y-2">
                {rows.map((request) => (
                  <SlotCard
                    key={request.id}
                    request={request}
                    onOpen={() => setParams({ open: request.id })}
                  />
                ))}
                {!rows.length && (
                  <div className="rounded-md border border-dashed border-border py-4 text-center text-xs text-muted-foreground">
                    &mdash;
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>

      <div className="space-y-4 lg:hidden">
        {days
          .map((day) => ({ day, rows: byDay(day, scheduled) }))
          .filter(({ rows }) => rows.length)
          .map(({ day, rows }) => (
            <div key={day.toISOString()}>
              <div className="mb-2 text-sm font-semibold">
                {day.toLocaleDateString('en-US', {
                  weekday: 'long',
                  month: 'short',
                  day: 'numeric',
                })}
                {sameDay(day, new Date()) && (
                  <span className="ml-2 text-xs font-normal text-primary">Today</span>
                )}
              </div>
              <div className="space-y-2">
                {rows.map((request) => (
                  <SlotCard
                    key={request.id}
                    request={request}
                    onOpen={() => setParams({ open: request.id })}
                  />
                ))}
              </div>
            </div>
          ))}
        {!days.some((day) => byDay(day, scheduled).length) && (
          <Card>
            <Empty
              title="Nothing booked this week"
              hint="Demos booked on the marketing site land here the moment the form is sent."
            />
          </Card>
        )}
      </div>

      {(unscheduled.length > 0 || cancelled.length > 0) && (
        // `grid-cols-1` is not redundant with the default. Without a declared
        // track the implicit one is `auto`, which sizes to the *min-content*
        // of its widest child -- and the min-content of a `truncate` line is
        // the whole untruncated string, so the card grew past the phone and
        // the ellipsis never appeared. Tailwind's `grid-cols-N` is
        // `minmax(0, 1fr)`, which is the part that lets it clip.
        <div className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-2">
          {unscheduled.length > 0 && (
            <Card className="p-4">
              <h2 className="text-sm font-semibold">No time picked</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Support requests, and anyone who wrote in without booking. They are on no day,
                so they would otherwise be visible only in the inbox.
              </p>
              <ul className="mt-3 space-y-2">
                {unscheduled.slice(0, 10).map((request) => (
                  <li key={request.id}>
                    <button
                      onClick={() => setParams({ open: request.id })}
                      className="flex w-full items-center gap-2 rounded-md border border-border px-3 py-2 text-left hover:bg-accent"
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">
                          {request.name || request.email || 'Someone'}
                        </span>
                        <span className="block truncate text-xs text-muted-foreground">
                          {request.message || request.email}
                        </span>
                      </span>
                      {request.unread && <Badge tone="primary">New</Badge>}
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {relative(request.created_at)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </Card>
          )}
          {cancelled.length > 0 && (
            <Card className="p-4">
              <h2 className="text-sm font-semibold">Cancelled</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Kept rather than deleted -- the slot is free again, and somebody cancelling is
                itself worth being able to see.
              </p>
              <ul className="mt-3 space-y-2">
                {cancelled.slice(0, 10).map((request) => (
                  <li key={request.id}>
                    <button
                      onClick={() => setParams({ open: request.id })}
                      className="flex w-full items-center gap-2 rounded-md border border-border px-3 py-2 text-left hover:bg-accent"
                    >
                      <span className="min-w-0 flex-1 truncate text-sm">
                        {request.name || request.email}
                      </span>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {dateTime(request.slot_at)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      )}

      <DetailSheet request={open} onClose={() => setParams({})} />
    </div>
  )
}

function SlotCard({ request, onOpen }: { request: DemoEntry; onOpen: () => void }) {
  return (
    <button
      onClick={onOpen}
      className={clsx(
        'block w-full rounded-md border p-2 text-left transition-colors hover:bg-accent',
        request.unread ? 'border-primary bg-primary/5' : 'border-border bg-card',
        request.status === 'done' && 'opacity-70',
      )}
    >
      <div className="tnum flex items-center gap-1.5 text-xs font-semibold text-primary">
        {request.unread && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />}
        {time(request.slot_at)}
      </div>
      <div className="mt-0.5 truncate text-sm font-medium">{request.name || request.email}</div>
      <div className="truncate text-xs text-muted-foreground">
        {request.dealership || 'No dealership given'}
      </div>
    </button>
  )
}

/** Everything on the row, including the two fields nobody thinks to keep:
 *  what they agreed to and when. */
function DetailSheet({ request, onClose }: { request: DemoEntry | null; onClose: () => void }) {
  const setStatus = useSetStatus()
  const { data: summary } = useOpsSummary()

  return (
    <Sheet
      open={Boolean(request)}
      onClose={onClose}
      title={
        request && (
          <>
            <div className="text-base font-semibold">{request.name || 'Unnamed'}</div>
            <div className="truncate text-sm text-muted-foreground">
              {request.dealership || 'No dealership given'}
            </div>
          </>
        )
      }
    >
      {request && (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={STATUS_TONE[request.status] ?? 'neutral'} className="capitalize">
              {request.status}
            </Badge>
            <Badge tone="neutral" className="capitalize">
              {request.kind}
            </Badge>
            <span className="text-xs text-muted-foreground">
              Submitted {relative(request.created_at)}
            </span>
          </div>

          <div className="rounded-md border border-border bg-muted/30 p-3">
            <div className="text-xs font-medium text-muted-foreground">When</div>
            <div className="mt-0.5 text-sm font-semibold">
              {request.slot_at ? dateTime(request.slot_at) : 'No time picked'}
            </div>
            {request.slot_at && summary?.timezone && (
              <div className="mt-0.5 text-xs text-muted-foreground">{summary.timezone}</div>
            )}
          </div>

          <dl className="space-y-2.5">
            <Row icon="mail" label="Email">
              <a href={`mailto:${request.email}`} className="text-primary hover:underline">
                {request.email || '--'}
              </a>
            </Row>
            <Row icon="phone" label="Phone">
              {request.phone ? (
                <a href={`tel:${request.phone}`} className="text-primary hover:underline">
                  {request.phone}
                </a>
              ) : (
                <span className="text-muted-foreground">Not given</span>
              )}
            </Row>
            <Row icon="globe" label="Dealership site">
              {request.dealership_url ? (
                <a
                  href={request.dealership_url}
                  target="_blank"
                  rel="noreferrer"
                  className="break-all text-primary hover:underline"
                >
                  {request.dealership_url}
                </a>
              ) : (
                <span className="text-muted-foreground">Not given</span>
              )}
            </Row>
          </dl>

          {request.message && (
            <div>
              <div className="text-xs font-medium text-muted-foreground">What they wrote</div>
              <p className="mt-1 whitespace-pre-wrap rounded-md border border-border bg-card p-3 text-sm">
                {request.message}
              </p>
            </div>
          )}

          {/* The wording will be edited on the page one day, and a consent
              record pointing at whatever it says *then* is not a record of what
              this person agreed to *now*. So the row keeps the text, and this
              is where it can be read. */}
          <div>
            <div className="text-xs font-medium text-muted-foreground">Consent</div>
            {request.consented_at ? (
              <div className="mt-1 rounded-md border border-border bg-card p-3">
                <p className="text-sm">{request.consent_text || '(no wording stored)'}</p>
                <p className="mt-2 text-xs text-muted-foreground">
                  Agreed {dateTime(request.consented_at)}
                </p>
              </div>
            ) : (
              <p className="mt-1 text-sm text-muted-foreground">
                No consent recorded on this row.
              </p>
            )}
          </div>

          <div className="flex flex-wrap gap-2 border-t border-border pt-4">
            {request.status !== 'done' && (
              <Button
                variant="primary"
                size="sm"
                onClick={() => setStatus.mutate({ id: request.id, status: 'done' })}
              >
                Mark done
              </Button>
            )}
            {request.status !== 'cancelled' ? (
              <Button
                size="sm"
                onClick={() => setStatus.mutate({ id: request.id, status: 'cancelled' })}
              >
                Cancelled
              </Button>
            ) : (
              <Button
                size="sm"
                onClick={() => setStatus.mutate({ id: request.id, status: 'seen' })}
              >
                Put it back
              </Button>
            )}
            <a
              href={`mailto:${request.email}`}
              className="inline-flex h-8 items-center rounded-md border border-input px-3 text-sm font-medium hover:bg-accent"
            >
              Email them
            </a>
          </div>
        </div>
      )}
    </Sheet>
  )
}

function Row({
  icon,
  label,
  children,
}: {
  icon: 'mail' | 'phone' | 'globe'
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="flex items-start gap-2.5">
      <Icon name={icon} className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0">
        <dt className="text-xs text-muted-foreground">{label}</dt>
        <dd className="text-sm">{children}</dd>
      </div>
    </div>
  )
}
