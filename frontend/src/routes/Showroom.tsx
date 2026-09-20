import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import clsx from 'clsx'

import { CarCard } from '../components/storefront/CarCard'
import { StorefrontShell, useAssistant } from '../components/storefront/Shell'
import { useStorefront } from '../components/storefront/useStorefront'
import { chromeClass, cityOf, SORTS, type Car, type Site } from '../components/storefront/types'

/**
 * The dealership's inventory list, with Liner on it. `/showroom`, or
 * `/<store>/showroom`.
 *
 * **This page is the list and nothing else.** It used to be their whole site
 * in one component -- hero, banner strip, About copy, then the lot -- and the
 * landing half now lives at the store root in `Storefront.tsx`. What is left
 * is the page their INVENTORY nav item leads to: a heading naming the lot, a
 * filter sidebar, a results toolbar and a grid. The chrome, the footer and the
 * assistant come from `StorefrontShell`, which both pages share so the header
 * cannot drift between them.
 *
 * **The filters live in the URL.** `?q=`, `?body_style=`, `?make=`,
 * `?min_price=` and `?max_price=` are the state, which is what lets the front
 * page's search box and its body-style tiles land here already narrowed, lets
 * the back button undo a filter, and makes a filtered grid a link somebody
 * can send. Sort and page depth stay local: neither is a thing a link needs
 * to carry.
 *
 * **The layout is their inventory page's layout**, and that is not
 * decoration. A filter sidebar on the left, a results toolbar carrying the
 * live count and a sort control, a three-column card grid on the right. With
 * 69 or 486 cars, chips above the grid push the cars themselves below the
 * fold, and "Sort by" is the first control a person reaches for on somebody
 * else's lot. Below `lg` the sidebar collapses behind a toggle, which is what
 * their own page does.
 *
 * **The browse filters are real.** Their site prints "Chevrolet (74)" and four
 * price bands; both are counted from rows here and both narrow the same grid.
 * A filter that promises 74 cars and shows 9 is worse than no filter, and it
 * is the easiest thing on a demo page to get wrong -- `make smoke` presses
 * every one. By Type is drawn only when the lot has body styles at all.
 *
 * **Everything on it is a row.** The cars come from `/api/showroom`, which
 * narrows through the same `offerable` predicate `search_inventory` uses -- so
 * a car the assistant refuses to discuss cannot sit on the page beside the
 * chat window refusing to discuss it.
 */

interface Filters {
  q: string
  make: string
  bodyStyle: string
  min: number | null
  max: number | null
}

const PAGE = 24

/** One row of a sidebar filter: a label, the count, and whether it is on.
 *
 *  A count of zero is shown and disabled rather than hidden -- "we have none of
 *  those" is an answer, and a band that disappears when you narrow makes the
 *  sidebar look like it is losing controls. */
function FilterRow({
  label,
  count,
  on,
  onClick,
}: {
  label: string
  count: number
  on: boolean
  onClick: () => void
}) {
  return (
    <button
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
}

function num(value: string | null): number | null {
  if (value == null || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

export function Showroom() {
  const [params, setParams] = useSearchParams()
  const filters: Filters = {
    q: params.get('q') ?? '',
    make: params.get('make') ?? '',
    bodyStyle: params.get('body_style') ?? '',
    min: num(params.get('min_price')),
    max: num(params.get('max_price')),
  }
  const [shown, setShown] = useState(PAGE)
  const [sort, setSort] = useState(SORTS[0].key)
  /* The header box's text, seeded from the URL so a search carried here from
   * the front page is still in the box when the grid it narrowed appears. */
  const [draft, setDraft] = useState(filters.q)
  /* The sidebar is a column at `lg` and a disclosure below it, which is what
   * their own page does. Closed by default on a phone: a rep or a buyer opening
   * this on a handset wants the cars, and eight filter groups above them is the
   * whole first screen spent on controls. */
  const [showFilters, setShowFilters] = useState(false)

  const query = useMemo(() => {
    const p = new URLSearchParams({ limit: String(shown), sort })
    if (filters.q) p.set('q', filters.q)
    if (filters.make) p.set('make', filters.make)
    if (filters.bodyStyle) p.set('body_style', filters.bodyStyle)
    if (filters.min != null) p.set('min_price', String(filters.min))
    if (filters.max != null) p.set('max_price', String(filters.max))
    return p.toString()
  }, [shown, sort, filters.q, filters.make, filters.bodyStyle, filters.min, filters.max])

  const { data } = useStorefront(query)
  const shop = data?.dealership
  const site: Site | undefined = shop?.site
  const facets = data?.facets
  const filtered = Boolean(filters.q || filters.make || filters.bodyStyle || filters.max || filters.min)
  const chrome = chromeClass(shop)

  /** Change one filter, in the URL, and start the grid from the top again.
   *  Empty values are removed rather than written as `?make=`, so a cleared
   *  filter leaves a clean link behind. */
  const narrow = (next: Partial<Filters>) => {
    const merged = { ...filters, ...next }
    const p = new URLSearchParams()
    if (merged.q) p.set('q', merged.q)
    if (merged.make) p.set('make', merged.make)
    if (merged.bodyStyle) p.set('body_style', merged.bodyStyle)
    if (merged.min != null) p.set('min_price', String(merged.min))
    if (merged.max != null) p.set('max_price', String(merged.max))
    setParams(p)
    setShown(PAGE)
    setShowFilters(false)
  }

  const clearAll = () => {
    setDraft('')
    narrow({ q: '', make: '', bodyStyle: '', min: null, max: null })
  }

  const sidebar = (
    <>
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">
          {data ? `All ${data.total} vehicle${data.total === 1 ? '' : 's'}` : 'Inventory'}
        </h2>
        {filtered && (
          <button onClick={clearAll} className="text-xs font-medium text-primary hover:underline">
            Clear filter
          </button>
        )}
      </div>

      <div className="mt-4">
        <h3 className="px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Price</h3>
        <div className="mt-1">
          {(facets?.price_bands ?? []).map((band) => (
            <FilterRow
              key={band.label}
              label={band.label}
              count={band.count}
              on={filters.min === band.min && filters.max === band.max}
              onClick={() =>
                narrow(
                  filters.min === band.min && filters.max === band.max
                    ? { min: null, max: null }
                    : { min: band.min, max: band.max },
                )
              }
            />
          ))}
        </div>
      </div>

      {/* Drawn only when the lot has body styles. A Dealer Car Search crawl
          leaves that field empty -- it lives in their sidebar filters and is
          not derived -- so this group would otherwise be ten rows that all
          return nothing. */}
      {(facets?.body_styles?.length ?? 0) > 0 && (
        <div className="mt-5">
          <h3 className="px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Type</h3>
          <div className="mt-1">
            {facets!.body_styles.map((style) => (
              <FilterRow
                key={style.name}
                label={style.name}
                count={style.count}
                on={filters.bodyStyle.toLowerCase() === style.name.toLowerCase()}
                onClick={() =>
                  narrow({
                    bodyStyle: filters.bodyStyle.toLowerCase() === style.name.toLowerCase() ? '' : style.name,
                  })
                }
              />
            ))}
          </div>
        </div>
      )}

      {(facets?.makes?.length ?? 0) > 0 && (
        <div className="mt-5">
          <h3 className="px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Make</h3>
          {/* A 486-car lot has thirty makes. Scrolling the group rather than
              the page keeps the grid beside it reachable, and `overflow-y`
              needs a height to scroll within. */}
          <div className="mt-1 max-h-72 overflow-y-auto">
            {facets!.makes.map((make) => (
              <FilterRow
                key={make.name}
                label={make.name}
                count={make.count}
                on={filters.make === make.name}
                onClick={() => narrow({ make: filters.make === make.name ? '' : make.name })}
              />
            ))}
          </div>
        </div>
      )}
    </>
  )

  return (
    <StorefrontShell
      shop={shop}
      voice={Boolean(data?.channels.voice)}
      search={{ draft, setDraft, submit: () => narrow({ q: draft.trim() }) }}
    >
      <main id="inventory" className="mx-auto max-w-7xl px-5 py-8 lg:flex lg:gap-8">
        {/* A column at `lg`, a disclosure below it. `hidden lg:block` rather
            than a second copy of the markup: two sidebars is two places a
            filter group gets added to and one place it gets forgotten. */}
        <aside className={clsx('min-w-0 shrink-0 lg:block lg:w-64', showFilters ? 'block' : 'hidden')}>
          <div className="rounded-xl border border-border bg-card p-3 lg:sticky lg:top-4">{sidebar}</div>
        </aside>

        <div className="min-w-0 flex-1">
          {/* Their page heads the grid with what the lot *is* and how much of
              it there is -- "Used Cars for Sale in Santa Ana, CA" over "69
              vehicles available". The city comes off the address rather than
              being a second copy of it in the profile. */}
          <h1 className="text-xl font-semibold sm:text-2xl">
            Used cars for sale{cityOf(shop?.address) ? ` in ${cityOf(shop?.address)}` : ''}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {data ? `${data.total} vehicle${data.total === 1 ? '' : 's'} available` : 'Loading the lot'}
          </p>

          {/* Their results toolbar, in the same grey as their nav strip. The
              count is the same number the sidebar heading shows, from the same
              request -- two counts for one grid is how a header says 16 over a
              list of 147. */}
          <div className={clsx(chrome, 'mt-3')}>
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-muted px-4 py-3 text-foreground">
              <div className="flex min-w-0 items-center gap-3">
                <button
                  onClick={() => setShowFilters((v) => !v)}
                  className="rounded-md border border-border px-3 py-1.5 text-xs font-medium lg:hidden"
                >
                  {showFilters ? 'Hide filters' : 'Show filters'}
                </button>
                <p className="min-w-0 truncate text-sm font-medium">
                  {filtered && data ? `${data.total} match${data.total === 1 ? '' : 'es'}` : 'All vehicles'}
                </p>
              </div>
              <label className="flex min-w-0 items-center gap-2 text-xs">
                <span className="shrink-0 text-muted-foreground">Sort by</span>
                <select
                  value={sort}
                  onChange={(e) => {
                    setShown(PAGE)
                    setSort(e.target.value)
                  }}
                  className="min-w-0 rounded-md border border-input bg-card px-2 py-1.5 text-xs text-foreground outline-none focus:border-ring"
                >
                  {SORTS.map((option) => (
                    <option key={option.key} value={option.key}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>

          {data && data.vehicles.length === 0 && (
            // Two different facts, and only one of them is a setup step. An
            // empty grid on first run reads as a broken build; an empty grid
            // after a filter reads as a broken filter.
            <div className="mt-4 rounded-xl border border-dashed border-border p-8 text-center">
              {filtered ? (
                <p className="text-sm">Nothing on the lot matches that. Try clearing the filters.</p>
              ) : (
                <>
                  <p className="text-sm font-medium">No vehicles have been imported yet.</p>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Run an import from the dashboard and they appear here. The assistant will not
                    offer a car it cannot find — it says it will check rather than inventing one.
                  </p>
                </>
              )}
            </div>
          )}

          {data && data.vehicles.length > 0 && (
            <Grid cars={data.vehicles} site={site} total={data.total} onMore={() => setShown((n) => n + PAGE)} />
          )}
        </div>
      </main>
    </StorefrontShell>
  )
}

/** The grid, as its own component so it can read the assistant from the
 *  shell's context -- `useAssistant` has to be called under the provider. */
function Grid({
  cars,
  site,
  total,
  onMore,
}: {
  cars: Car[]
  site: Site | undefined
  total: number
  onMore: () => void
}) {
  const assistant = useAssistant()
  return (
    <>
      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {cars.map((car) => (
          <CarCard
            key={car.vin}
            car={car}
            priceLabel={site?.price_label ?? ''}
            priceNote={site?.price_note ?? ''}
            onAsk={assistant.askAbout}
          />
        ))}
      </div>
      {total > cars.length && (
        <div className="mt-6 text-center">
          <button onClick={onMore} className="rounded-lg border border-border bg-card px-4 py-2 text-sm font-medium">
            Show more ({total - cars.length} left)
          </button>
        </div>
      )}
    </>
  )
}
