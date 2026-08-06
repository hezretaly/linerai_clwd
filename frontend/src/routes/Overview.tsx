import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Cell, Pie, PieChart, ResponsiveContainer } from 'recharts'

import { api } from '../lib/api'
import { dateTime, money, relative } from '../lib/format'
import type { Overview } from '../lib/types'
import { Badge, Card, Empty, Spinner } from '../components/ui'
import { PageHeader } from '../components/dashboard/AppShell'

// Greyscale ramp with the brand blue reserved for the largest slice.
const RAMP = ['var(--color-primary)', 'hsl(220 9% 55%)', 'hsl(220 14% 78%)']

export function OverviewPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['overview'],
    queryFn: () => api.get<Overview>('/api/overview'),
  })

  if (isLoading || !data) return <Spinner />

  const total = data.mix.reduce((sum, m) => sum + m.count, 0)

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle={`${data.dealership.name} -- ${data.dealership.address}`}
      />

      <div className="space-y-6 p-6">
        <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {data.kpis.map((kpi) => (
            <Card key={kpi.key} className="p-4">
              <p className="text-sm text-muted-foreground">{kpi.label}</p>
              <p className="mt-1 text-3xl font-semibold tabular-nums">{kpi.value}</p>
              <p className="mt-1 text-xs text-muted-foreground">{kpi.window}</p>
            </Card>
          ))}
        </section>

        <div className="grid gap-6 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <header className="flex items-center justify-between border-b border-border px-4 py-3">
              <h2 className="text-sm font-semibold">Needs a person</h2>
              <Badge tone={data.queues.needs_a_person.length ? 'destructive' : 'neutral'}>
                {data.queues.needs_a_person.length} open
              </Badge>
            </header>
            {data.queues.needs_a_person.length === 0 ? (
              <Empty title="Nothing waiting" hint="Liner is handling everything right now." />
            ) : (
              <ul className="divide-y divide-border">
                {data.queues.needs_a_person.map((escalation) => (
                  <li key={escalation.id} className="px-4 py-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-medium">
                          {escalation.rule?.label ?? 'Handoff'}
                        </p>
                        <p className="mt-0.5 truncate text-sm text-muted-foreground">
                          {escalation.reason}
                        </p>
                      </div>
                      <Link
                        to={`/app/conversations/${escalation.conversation_id}`}
                        className="shrink-0 text-sm text-primary hover:underline"
                      >
                        Open
                      </Link>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {relative(escalation.created_at)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <header className="border-b border-border px-4 py-3">
              <h2 className="text-sm font-semibold">Where they came from</h2>
            </header>
            <div className="relative h-48">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={data.mix}
                    dataKey="count"
                    nameKey="channel"
                    innerRadius={52}
                    outerRadius={74}
                    paddingAngle={2}
                    stroke="none"
                  >
                    {data.mix.map((entry, index) => (
                      <Cell key={entry.channel} fill={RAMP[index % RAMP.length]} />
                    ))}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                <span className="text-2xl font-semibold tabular-nums">{total}</span>
                <span className="text-xs text-muted-foreground">last 24h</span>
              </div>
            </div>
            <ul className="space-y-1 px-4 pb-4">
              {data.mix.map((entry, index) => (
                <li key={entry.channel} className="flex items-center gap-2 text-sm">
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ background: RAMP[index % RAMP.length] }}
                  />
                  <span className="capitalize">{entry.channel}</span>
                  <span className="ml-auto tabular-nums text-muted-foreground">
                    {entry.count}
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <header className="flex items-center justify-between border-b border-border px-4 py-3">
              <h2 className="text-sm font-semibold">Unconfirmed appointments</h2>
              <Link to="/app/calendar" className="text-sm text-primary hover:underline">
                Calendar
              </Link>
            </header>
            {data.queues.unconfirmed_appointments.length === 0 ? (
              <Empty title="All confirmed" />
            ) : (
              <ul className="divide-y divide-border">
                {data.queues.unconfirmed_appointments.map((appointment) => (
                  <li key={appointment.id} className="flex items-center gap-3 px-4 py-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium">{appointment.lead?.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {dateTime(appointment.starts_at)}
                        {appointment.vehicle ? ` -- ${appointment.vehicle.title}` : ''}
                      </p>
                    </div>
                    {!appointment.assigned_user_id && (
                      <Badge tone="warning">Unassigned</Badge>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <header className="flex items-center justify-between border-b border-border px-4 py-3">
              <h2 className="text-sm font-semibold">Inventory needs attention</h2>
              <Link to="/app/inventory" className="text-sm text-primary hover:underline">
                Inventory
              </Link>
            </header>
            {data.queues.inventory_issues.length === 0 ? (
              <Empty title="Inventory is clean" />
            ) : (
              <ul className="divide-y divide-border">
                {data.queues.inventory_issues.map((vehicle) => (
                  <li key={vehicle.id} className="px-4 py-3">
                    <p className="text-sm font-medium">{vehicle.title}</p>
                    {/* The blast radius: this is the screen that sells the
                        inventory page. */}
                    <p className="mt-0.5 text-sm text-destructive">
                      Liner offered this {vehicle.status} vehicle to {vehicle.quoted_to}{' '}
                      {vehicle.quoted_to === 1 ? 'buyer' : 'buyers'} at {money(vehicle.price)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </>
  )
}
