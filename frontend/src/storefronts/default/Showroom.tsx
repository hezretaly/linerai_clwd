import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import clsx from 'clsx'

import { isFiltered, readFilters, showroomQuery, writeFilters, type Filters } from '../_shared/filters'
import { SORTS, cityOf } from '../_shared/types'
import { useStorefront } from '../_shared/useStorefront'
import { Card } from './Card'
import { Frame } from './Frame'

const PAGE = 24

/** The default inventory list: counted filters down the side, sort across
 *  the top, a grid. The filters are the URL, per `_shared/filters.ts`. */
export function Showroom() {
  const [params, setParams] = useSearchParams()
  const filters = readFilters(params)
  const [shown, setShown] = useState(PAGE)
  const [sort, setSort] = useState(SORTS[0].key)
  const [draft, setDraft] = useState(filters.q)
  const query = useMemo(() => showroomQuery(filters, shown, sort), [filters.q, filters.make, filters.bodyStyle, filters.min, filters.max, filters.location, shown, sort])
  const { data } = useStorefront(query)
  const shop = data?.dealership
  const facets = data?.facets
  const filtered = isFiltered(filters)

  const narrow = (next: Partial<Filters>) => {
    setParams(writeFilters({ ...filters, ...next }))
    setShown(PAGE)
  }

  const row = (label: string, count: number, on: boolean, onClick: () => void) => (
    <button
      key={label}
      disabled={count === 0}
      onClick={onClick}
      className={clsx(
        'flex w-full min-w-0 items-baseline justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm',
        count === 0 && 'opacity-40',
        on ? 'bg-primary/10 font-semibold text-primary' : 'hover:bg-muted',
      )}
    >
      <span className="min-w-0 truncate">{label}</span>
      <span className="tnum shrink-0 text-xs text-muted-foreground">{count}</span>
    </button>
  )

  return (
    <Frame shop={shop} voice={Boolean(data?.channels.voice)} search={{ draft, setDraft, submit: () => narrow({ q: draft.trim() }) }}>
      <main className="mx-auto max-w-6xl px-5 py-8 lg:flex lg:gap-8">
        <aside className="min-w-0 shrink-0 lg:w-60">
          <div className="rounded-xl border border-border bg-card p-3 lg:sticky lg:top-4">
            <div className="flex items-baseline justify-between gap-2">
              <h2 className="text-sm font-semibold">{data ? `All ${data.total} vehicles` : 'Inventory'}</h2>
              {filtered && (
                <button onClick={() => { setDraft(''); setParams(new URLSearchParams()); setShown(PAGE) }} className="text-xs font-medium text-primary hover:underline">
                  Clear
                </button>
              )}
            </div>
            {(facets?.locations?.length ?? 0) > 1 && (
              <>
                <h3 className="mt-4 px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Location</h3>
                {facets!.locations!.map((l) =>
                  row(l.name, l.count, filters.location === l.key, () =>
                    narrow({ location: filters.location === l.key ? '' : l.key }),
                  ),
                )}
              </>
            )}
            <h3 className="mt-4 px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Price</h3>
            {(facets?.price_bands ?? []).map((b) =>
              row(b.label, b.count, filters.min === b.min && filters.max === b.max, () =>
                narrow(filters.min === b.min && filters.max === b.max ? { min: null, max: null } : { min: b.min, max: b.max }),
              ),
            )}
            {(facets?.body_styles?.length ?? 0) > 0 && (
              <>
                <h3 className="mt-5 px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Type</h3>
                {facets!.body_styles.map((s) =>
                  row(s.name, s.count, filters.bodyStyle.toLowerCase() === s.name.toLowerCase(), () =>
                    narrow({ bodyStyle: filters.bodyStyle.toLowerCase() === s.name.toLowerCase() ? '' : s.name }),
                  ),
                )}
              </>
            )}
            {(facets?.makes?.length ?? 0) > 0 && (
              <>
                <h3 className="mt-5 px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Make</h3>
                <div className="max-h-72 overflow-y-auto">
                  {facets!.makes.map((m) => row(m.name, m.count, filters.make === m.name, () => narrow({ make: filters.make === m.name ? '' : m.name })))}
                </div>
              </>
            )}
          </div>
        </aside>

        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold sm:text-2xl">
            Used cars for sale{cityOf(shop?.address) ? ` in ${cityOf(shop?.address)}` : ''}
          </h1>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-muted px-4 py-3">
            <p className="min-w-0 truncate text-sm font-medium">
              {data ? (filtered ? `${data.total} match${data.total === 1 ? '' : 'es'}` : `${data.total} vehicles available`) : 'Loading the lot'}
            </p>
            <label className="flex items-center gap-2 text-xs">
              <span className="text-muted-foreground">Sort by</span>
              <select
                value={sort}
                onChange={(e) => { setShown(PAGE); setSort(e.target.value) }}
                className="rounded-md border border-input bg-card px-2 py-1.5 text-xs outline-none focus:border-ring"
              >
                {SORTS.map((o) => (
                  <option key={o.key} value={o.key}>{o.label}</option>
                ))}
              </select>
            </label>
          </div>

          {data && data.vehicles.length === 0 && (
            <div className="mt-4 rounded-xl border border-dashed border-border p-8 text-center text-sm">
              {filtered ? 'Nothing on the lot matches that. Try clearing the filters.' : 'No vehicles have been imported yet.'}
            </div>
          )}
          {data && data.vehicles.length > 0 && (
            <>
              <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {data.vehicles.map((car) => (
                  <Card key={car.vin} car={car} />
                ))}
              </div>
              {data.total > data.vehicles.length && (
                <div className="mt-6 text-center">
                  <button onClick={() => setShown((n) => n + PAGE)} className="rounded-lg border border-border bg-card px-4 py-2 text-sm font-medium">
                    Show more ({data.total - data.vehicles.length} left)
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </main>
    </Frame>
  )
}
