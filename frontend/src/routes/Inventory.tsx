import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'

import { api } from '../lib/api'
import { dateTime, miles, money } from '../lib/format'
import type { Vehicle } from '../lib/types'
import { Badge, Button, Card, Empty, Field, Input, Sheet, Spinner, Switch, Tabs } from '../components/ui'
import { PageHeader } from '../components/dashboard/AppShell'

export function InventoryPage() {
  const [tab, setTab] = useState('all')
  const [query, setQuery] = useState('')
  const [openId, setOpenId] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['inventory'],
    queryFn: () => api.get<{ vehicles: Vehicle[] }>('/api/inventory'),
  })

  if (isLoading || !data) return <Spinner />

  const vehicles = data.vehicles
  const stale = vehicles.filter((v) => v.status !== 'available' && v.mention_count > 0)
  const filtered = vehicles
    .filter((v) => (tab === 'all' ? true : tab === 'issues' ? stale.includes(v) : v.status === tab))
    .filter((v) =>
      query ? `${v.title} ${v.vin} ${v.trim}`.toLowerCase().includes(query.toLowerCase()) : true,
    )

  return (
    <>
      <PageHeader
        title="Inventory"
        subtitle={`${vehicles.length} vehicles`}
        actions={
          <Link to="/app/inventory/import">
            <Button variant="primary" size="sm">
              Import
            </Button>
          </Link>
        }
      />

      <div className="space-y-6 p-6">
        {stale.length > 0 && (
          <Card className="border-primary/30 bg-accent p-4">
            <h2 className="text-sm font-semibold text-primary">
              Liner has been quoting vehicles that are no longer available
            </h2>
            <ul className="mt-2 space-y-1">
              {stale.map((vehicle) => (
                <li key={vehicle.id} className="text-sm">
                  <button
                    onClick={() => setOpenId(vehicle.id)}
                    className="font-medium underline"
                  >
                    {vehicle.title}
                  </button>{' '}
                  <span className="text-muted-foreground">
                    ({vehicle.status}) -- offered to {vehicle.mention_count}{' '}
                    {vehicle.mention_count === 1 ? 'buyer' : 'buyers'}
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        )}

        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3 px-4 pt-2">
            <Tabs
              active={tab}
              onChange={setTab}
              tabs={[
                { id: 'all', label: 'All', count: vehicles.length },
                {
                  id: 'available',
                  label: 'Available',
                  count: vehicles.filter((v) => v.status === 'available').length,
                },
                { id: 'sold', label: 'Sold', count: vehicles.filter((v) => v.status === 'sold').length },
                { id: 'issues', label: 'Needs attention', count: stale.length },
              ]}
            />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search VIN or model"
              className="w-56"
            />
          </div>

          {filtered.length === 0 ? (
            <Empty title="No vehicles match" />
          ) : (
            /* A table cannot reflow -- it sizes to its content whatever
               `w-full` says. Inventory is an admin screen, so on a phone it
               scrolls inside its own card rather than being redesigned. */
            <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th className="px-4 py-2 font-medium">Vehicle</th>
                  <th className="px-4 py-2 font-medium">Price</th>
                  <th className="px-4 py-2 font-medium">Mileage</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Rules</th>
                  <th className="px-4 py-2 font-medium">Quoted</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {filtered.map((vehicle) => (
                  <tr
                    key={vehicle.id}
                    onClick={() => setOpenId(vehicle.id)}
                    className="cursor-pointer transition-colors duration-150 hover:bg-muted"
                  >
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-3">
                        <img
                          src={vehicle.photo_url}
                          alt=""
                          className="h-10 w-14 rounded border border-border object-cover"
                        />
                        <div>
                          <p className="font-medium">{vehicle.title}</p>
                          <p className="text-xs text-muted-foreground">
                            {vehicle.trim} -- {vehicle.vin}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 tabular-nums">{money(vehicle.price)}</td>
                    <td className="px-4 py-2.5 tabular-nums text-muted-foreground">
                      {miles(vehicle.mileage)}
                    </td>
                    <td className="px-4 py-2.5">
                      <Badge tone={vehicle.status === 'available' ? 'success' : 'primary'}>
                        {vehicle.status}
                      </Badge>
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex flex-wrap gap-1">
                        {!vehicle.rules.discuss && <Badge tone="primary">Do not discuss</Badge>}
                        {vehicle.rules.hold_price && <Badge tone="primary">Price firm</Badge>}
                        {vehicle.rules.mention_warranty && <Badge tone="neutral">Warranty</Badge>}
                        {vehicle.manual_fields.length > 0 && (
                          <Badge tone="primary">Edited</Badge>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-2.5 tabular-nums text-muted-foreground">
                      {vehicle.mention_count}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </Card>
      </div>

      <VehicleDrawer id={openId} onClose={() => setOpenId(null)} />
    </>
  )
}

function VehicleDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const queryClient = useQueryClient()
  const { data: vehicle } = useQuery({
    queryKey: ['inventory', id],
    queryFn: () => api.get<Vehicle>(`/api/inventory/${id}`),
    enabled: Boolean(id),
  })

  const patch = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      api.patch(`/api/inventory/${id}`, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['inventory'] })
      void queryClient.invalidateQueries({ queryKey: ['overview'] })
    },
  })

  const [price, setPrice] = useState('')

  return (
    <Sheet
      open={Boolean(id)}
      onClose={onClose}
      title={
        <>
          <h2 className="text-base font-semibold">{vehicle?.title}</h2>
          <p className="text-sm text-muted-foreground">{vehicle?.vin}</p>
        </>
      }
    >
      {!vehicle ? (
        <Spinner />
      ) : (
        <div className="space-y-6">
          <img
            src={vehicle.photo_url}
            alt=""
            className="w-full rounded-lg border border-border"
          />

          <dl className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-xs text-muted-foreground">Price</dt>
              <dd className="tabular-nums">{money(vehicle.price)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Mileage</dt>
              <dd className="tabular-nums">{miles(vehicle.mileage)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Body</dt>
              <dd>{vehicle.body_style}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Source</dt>
              <dd className="capitalize">{vehicle.source}</dd>
            </div>
          </dl>

          <section className="space-y-3 rounded-lg border border-border p-3">
            <h3 className="text-sm font-semibold">What Liner may say about this one</h3>

            <label className="flex items-center justify-between gap-3 text-sm">
              <span>
                Discuss this vehicle
                <span className="block text-xs text-muted-foreground">
                  Off means it never reaches the assistant at all -- it is filtered in the
                  search tool, not asked for in the prompt.
                </span>
              </span>
              <Switch
                checked={vehicle.rules.discuss}
                onChange={(value) => patch.mutate({ rule_discuss: value })}
                label="Discuss this vehicle"
              />
            </label>

            <label className="flex items-center justify-between gap-3 text-sm">
              <span>Price is firm</span>
              <Switch
                checked={vehicle.rules.hold_price}
                onChange={(value) => patch.mutate({ rule_hold_price: value })}
                label="Price is firm"
              />
            </label>

            <label className="flex items-center justify-between gap-3 text-sm">
              <span>Always mention the warranty</span>
              <Switch
                checked={vehicle.rules.mention_warranty}
                onChange={(value) => patch.mutate({ rule_mention_warranty: value })}
                label="Always mention the warranty"
              />
            </label>

            {vehicle.rules.note && (
              <p className="rounded bg-muted px-2.5 py-2 text-xs text-muted-foreground">
                {vehicle.rules.note}
              </p>
            )}
          </section>

          <section className="space-y-2">
            <Field label="Override the price">
              <div className="flex gap-2">
                <Input
                  value={price}
                  onChange={(e) => setPrice(e.target.value)}
                  placeholder={String(vehicle.price ?? '')}
                  inputMode="numeric"
                />
                <Button
                  variant="primary"
                  disabled={!price}
                  onClick={() => {
                    patch.mutate({ price: Number(price) })
                    setPrice('')
                  }}
                >
                  Save
                </Button>
              </div>
            </Field>
            {vehicle.manual_fields.length > 0 && (
              <p className="text-xs text-muted-foreground">
                Edited by hand: {vehicle.manual_fields.join(', ')}. The next inventory import
                will not overwrite {vehicle.manual_fields.length > 1 ? 'these' : 'this'}.
              </p>
            )}
          </section>

          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Who was quoted this
            </h3>
            {vehicle.mentions?.length ? (
              <ul className="mt-2 space-y-1.5">
                {vehicle.mentions.map((mention) => (
                  <li
                    key={`${mention.conversation_id}-${mention.created_at}`}
                    className="flex items-center justify-between gap-2 text-sm"
                  >
                    <Link
                      to={`/app/conversations/${mention.conversation_id}`}
                      className={clsx('text-primary hover:underline')}
                    >
                      {mention.lead_name}
                    </Link>
                    <span className="text-xs text-muted-foreground">
                      {money(mention.quoted_price)} -- {dateTime(mention.created_at)}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-sm text-muted-foreground">Never quoted.</p>
            )}
          </section>
        </div>
      )}
    </Sheet>
  )
}
