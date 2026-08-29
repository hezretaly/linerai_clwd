import { useEffect, useMemo, useState } from 'react'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import clsx from 'clsx'

import { api } from '../lib/api'
import { applyBrand } from '../lib/brand'
import { possessive, type Dealership } from '../lib/dealership'
import { money } from '../lib/format'
import { Icon } from '../components/Icon'

/**
 * The dealership's own front page, with Liner sitting on it.
 *
 * **Why this exists.** A demo is a link you send somebody, and the question a
 * dealer actually has is *what does this look like on my website* -- which
 * `/chat` cannot answer, because `/chat` is a chat window floating on nothing.
 * This is their name, their logo, their colour, their address and phone, their
 * real cars, and the assistant in the corner where it would really sit.
 *
 * **Their copy is served, not written in here.** Headings, welcome text, hero,
 * nav and social links all come from the profile's `site:` block. Hardcoding a
 * prospect's sentences into this component is the "Riverside Auto" bug one
 * level up: the next instance greets somebody in Craig and Landreth's words.
 * A profile with no `site:` block renders a plain storefront, which is honest
 * and is what Riverside gets.
 *
 * **The browse filters are real.** Their site prints "Chevrolet (74)" and four
 * price bands; both are counted from rows here and both narrow the same grid.
 * A filter that promises 74 cars and shows 9 is worse than no filter, and it
 * is the easiest thing on a demo page to get wrong. By Type is drawn only when
 * the lot has body styles at all -- a Dealer Car Search crawl leaves that field
 * empty, so ten links that all return nothing would otherwise be the first
 * thing a prospect clicked.
 *
 * **Everything on it is a row.** The cars come from `/api/showroom`, which
 * narrows through the same `offerable` predicate `search_inventory` uses -- so
 * a car the assistant refuses to discuss cannot sit on the page beside the chat
 * window refusing to discuss it.
 *
 * **The widget is an iframe of the real `/chat`.** Not a second chat client:
 * one round of duplicated transcript logic is how the widget starts dropping
 * the booking card the full page still renders. Same origin, same conversation
 * id in localStorage, so the widget and the full page are one thread.
 *
 * **There is no contact form, and that is the pitch.** Their real page has one;
 * reproducing it here would be a form that posts nowhere, which is exactly what
 * this codebase refuses to build. The assistant is what stands in its place --
 * it captures the same fields, answers, and books, rather than dropping a
 * message into an inbox somebody reads on Monday.
 */

interface Car {
  vin: string
  title: string
  trim: string
  price: number | null
  mileage: number | null
  body_style: string
  features: string[]
  photo_url: string
  listing_url: string
}

interface Facets {
  makes: { name: string; count: number }[]
  body_styles: { name: string; count: number }[]
  price_bands: { label: string; min: number | null; max: number | null; count: number }[]
}

interface ShowroomPayload {
  dealership: Dealership
  greeting: string
  vehicles: Car[]
  total: number
  offset: number
  facets: Facets
  channels: { chat: boolean; voice: boolean }
}

interface Filters {
  q: string
  make: string
  bodyStyle: string
  min: number | null
  max: number | null
}

const NONE: Filters = { q: '', make: '', bodyStyle: '', min: null, max: null }

const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']

/** `09:00` -> `9:00 am`, which is how their own hours table reads. */
function clock(hhmm: string): string {
  const [h, m] = hhmm.split(':').map(Number)
  const suffix = h < 12 ? 'am' : 'pm'
  const hour = h % 12 === 0 ? 12 : h % 12
  return `${hour}:${String(m).padStart(2, '0')} ${suffix}`
}

/** Runs of identical days collapsed: `Mon-Thu  9:00 am - 8:00 pm`.
 *
 *  Their Friday and Saturday close an hour earlier than Monday to Thursday, so
 *  a single range would be wrong for two days of the week -- and `Closed` is
 *  named rather than omitted, because "are you open Sunday?" is the question
 *  and a missing row is not an answer. */
function openingHours(hours: Dealership['hours']): { days: string; text: string }[] {
  const label = (d: string) => d.slice(0, 3).replace(/^./, (c) => c.toUpperCase())
  const rows = DAYS.map((day) => ({
    day,
    text: hours?.[day] ? `${clock(hours[day]!.open)} - ${clock(hours[day]!.close)}` : 'Closed',
  }))
  const out: { days: string; text: string }[] = []
  let run = [rows[0]]
  const flush = () =>
    out.push({
      days: run.length === 1 ? label(run[0].day) : `${label(run[0].day)}-${label(run.at(-1)!.day)}`,
      text: run[0].text,
    })
  for (const row of rows.slice(1)) {
    if (row.text === run.at(-1)!.text) run.push(row)
    else {
      flush()
      run = [row]
    }
  }
  flush()
  return out
}

/** The last rung of `ingest/pipeline.py:_photo_for`, reached from the browser.
 *
 *  That ladder picks a stored copy, then the dealer's own URL, then a drawn
 *  placeholder -- but it runs at publish time, when a hotlinked URL is only
 *  known to be *written*, not to still resolve. A car that sells and has its
 *  photo pulled, or an image host that refuses an off-site referrer, both
 *  surface here as a torn-page icon in the middle of the grid. Falling through
 *  to the same placeholder the ladder would have ended on keeps the card. */
function CarCard({ car }: { car: Car }) {
  const [broke, setBroke] = useState(false)
  const drawn = `/api/photos/${car.vin}.svg`
  return (
    <article className="flex min-w-0 flex-col overflow-hidden rounded-xl border border-border bg-card">
      <div className="aspect-[4/3] w-full overflow-hidden bg-muted">
        <img
          src={broke ? drawn : car.photo_url || drawn}
          alt={car.title}
          loading="lazy"
          onError={() => setBroke(true)}
          className="h-full w-full object-cover"
        />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1 p-4">
        <h3 className="truncate text-sm font-semibold">{car.title}</h3>
        {car.trim && <p className="truncate text-xs text-muted-foreground">{car.trim}</p>}
        <p className="mt-1 text-lg font-semibold text-primary">
          {/* A car with no published price is a real listing state, not a
              failure -- it belongs on the lot, Liner simply cannot quote it. */}
          {car.price ? money(car.price) : 'Call for price'}
        </p>
        <p className="text-xs text-muted-foreground">
          {car.mileage != null ? `${car.mileage.toLocaleString()} miles` : 'Mileage not listed'}
        </p>
      </div>
    </article>
  )
}

export function Showroom() {
  const [open, setOpen] = useState(false)
  const [shown, setShown] = useState(24)
  const [filters, setFilters] = useState<Filters>(NONE)
  const [draft, setDraft] = useState('')
  /* Their logo and hero live on their own CDN, and a broken <img> in the
   * middle of a demo is worse than not showing one: the alt text renders as a
   * torn-page icon next to the dealership's own name. An image host that
   * refuses an off-site referrer, a URL that moves, a venue firewall -- all
   * look identical and all happen on somebody else's laptop. Falling back to
   * the name is a page that still reads. */
  const [logoBroke, setLogoBroke] = useState(false)
  const [heroBroke, setHeroBroke] = useState(false)

  const params = useMemo(() => {
    const p = new URLSearchParams({ limit: String(shown) })
    if (filters.q) p.set('q', filters.q)
    if (filters.make) p.set('make', filters.make)
    if (filters.bodyStyle) p.set('body_style', filters.bodyStyle)
    if (filters.min != null) p.set('min_price', String(filters.min))
    if (filters.max != null) p.set('max_price', String(filters.max))
    return p.toString()
  }, [shown, filters])

  const { data } = useQuery({
    queryKey: ['showroom', params],
    queryFn: () => api.get<ShowroomPayload>(`/api/showroom?${params}`),
    // Keep the previous grid on screen while a filter re-fetches. A page that
    // empties and refills on every click looks broken during a demo.
    placeholderData: keepPreviousData,
  })

  useEffect(() => {
    applyBrand(data?.dealership.brand)
  }, [data])

  const shop = data?.dealership
  const site = shop?.site
  const facets = data?.facets
  const filtered = Boolean(filters.q || filters.make || filters.bodyStyle || filters.max || filters.min)
  const tel = shop?.phone ? shop.phone.replace(/[^\d+]/g, '') : ''
  // "4156 Shelbyville Rd., Louisville, KY 40207" -> street / city line, the
  // way their own header stacks it.
  const [street, ...rest] = (shop?.address || '').split(',')
  const cityLine = rest.join(',').trim()

  const narrow = (next: Partial<Filters>) => {
    setShown(24)
    setFilters((f) => ({ ...f, ...next }))
  }

  return (
    /* `.dark` is the classic dark palette that has been in the token layer
       since the beginning, unused; `.theme-buyer` sits after it in the file
       and so still wins for --primary, which is where their accent lands.
       Scoped to this page: the dealership's storefront follows their site,
       and their reps' dashboard does not. */
    <div
      className={clsx(
        /* `text-foreground` is not decoration. Without it every heading on
           the page inherits whatever colour the document body has, which in
           light mode happens to be right and in dark mode is black on black:
           the dealership's own name, the card titles and the footer all
           vanished. A surface that sets a background must set a foreground. */
        'theme-buyer min-h-full bg-background text-foreground',
        shop?.brand?.surface === 'dark' && 'dark',
      )}
    >
      {/* ---- top bar: address, phone, social ------------------------- */}
      {/* Deliberately not `bg-foreground text-background`: that is an
          inversion, and on a dark surface it inverts the wrong way -- a white
          strip with white text on it. `bg-muted` is a step away from the page
          in either mode, which is what the bar is for. */}
      <div className="border-b border-border bg-muted">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-1 px-5 py-2 text-xs">
          <p className="min-w-0 truncate">
            {shop?.address}
            {tel && (
              <>
                {' · '}
                <a href={`tel:${tel}`} className="font-semibold underline-offset-2 hover:underline">
                  {shop!.phone}
                </a>
              </>
            )}
          </p>
          <div className="flex items-center gap-3">
            {(site?.social ?? []).map((s) => (
              <a key={s.href} href={s.href} rel="noreferrer" target="_blank" className="hover:underline">
                {s.label}
              </a>
            ))}
          </div>
        </div>
      </div>

      {/* ---- logo + their own nav ------------------------------------ */}
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-5 py-4">
          {shop?.brand?.logo_url && !logoBroke ? (
            <img
              src={shop.brand.logo_url}
              alt={shop.name}
              onError={() => setLogoBroke(true)}
              className="h-10 w-auto max-w-[200px] object-contain"
            />
          ) : (
            <span className="truncate text-lg font-semibold">{shop?.name || ' '}</span>
          )}
          {/* Their real pages, on their real site. A nav that 404s inside our
              app mid-demo is worse than one that leaves it, and we are not
              pretending to have rebuilt Financing or We Buy Cars. */}
          <nav className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm font-medium">
            {(site?.links ?? []).map((l) => (
              <a key={l.href} href={l.href} rel="noreferrer" target="_blank" className="hover:text-primary">
                {l.label}
              </a>
            ))}
          </nav>
        </div>
      </header>

      {/* ---- hero ----------------------------------------------------- */}
      <section className="relative isolate overflow-hidden border-b border-border">
        {site?.hero_image && !heroBroke && (
          <img
            src={site.hero_image}
            alt=""
            onError={() => setHeroBroke(true)}
            className="absolute inset-0 -z-10 h-full w-full object-cover opacity-20"
          />
        )}
        <div className="mx-auto max-w-6xl px-5 py-12">
          <h1 className="text-2xl font-semibold sm:text-3xl">
            {site?.heading || (shop?.name ? `Welcome to ${shop.name}` : ' ')}
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            {data ? `${data.total} vehicle${data.total === 1 ? '' : 's'} on the lot right now.` : ' '}{' '}
            Ask {shop?.name ? `${possessive(shop.name)} assistant` : 'our assistant'} anything — it
            searches this inventory and can book you in.
          </p>

          <form
            className="mt-6 flex max-w-xl gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              narrow({ q: draft.trim() })
            }}
          >
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Keyword search"
              aria-label="Keyword search"
              className="min-w-0 flex-1 rounded-lg border border-input bg-card px-3 py-2 text-sm outline-none focus:border-ring focus:ring-1 focus:ring-ring"
            />
            <button className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">
              Search
            </button>
          </form>

          <div className="mt-4 flex flex-wrap gap-3">
            <button
              onClick={() => setOpen(true)}
              className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
            >
              Chat with us
            </button>
            {/* Counted, never declared: with no VOICE_PROVIDER there is no
                phone to answer, and a button opening a page that says so is
                worse than no button. */}
            {data?.channels.voice && (
              <a href="/call" className="rounded-lg border border-border bg-card px-4 py-2 text-sm font-medium">
                Call us
              </a>
            )}
          </div>
        </div>
      </section>

      {/* ---- their welcome copy --------------------------------------- */}
      {(site?.welcome?.length ?? 0) > 0 && (
        <section className="border-b border-border bg-muted/30">
          <div className="mx-auto max-w-3xl space-y-3 px-5 py-8 text-sm leading-relaxed text-muted-foreground">
            {site!.welcome.map((p) => (
              <p key={p.slice(0, 40)}>{p}</p>
            ))}
          </div>
        </section>
      )}

      {/* ---- shopping options ----------------------------------------- */}
      <section className="mx-auto max-w-6xl px-5 py-8">
        <h2 className="text-lg font-semibold">Shopping options</h2>

        <h3 className="mt-5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          By price
        </h3>
        <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {(facets?.price_bands ?? []).map((band) => (
            <button
              key={band.label}
              disabled={band.count === 0}
              onClick={() => narrow({ min: band.min, max: band.max, make: '', q: '' })}
              className={clsx(
                'min-w-0 rounded-xl border p-4 text-left',
                band.count === 0 && 'opacity-40',
                filters.min === band.min && filters.max === band.max
                  ? 'border-primary bg-primary/5'
                  : 'border-border bg-card',
              )}
            >
              <p className="truncate text-sm font-semibold">{band.label}</p>
              <p className="text-xs text-muted-foreground">
                {band.count} vehicle{band.count === 1 ? '' : 's'}
              </p>
            </button>
          ))}
        </div>

        {/* Drawn only when the lot has body styles. A Dealer Car Search crawl
            leaves that field empty -- it lives in their sidebar filters and is
            not derived -- so this row would otherwise be ten links that all
            return nothing. */}
        {(facets?.body_styles?.length ?? 0) > 0 && (
          <>
            <h3 className="mt-6 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              By type
            </h3>
            <div className="mt-2 flex flex-wrap gap-2">
              {facets!.body_styles.map((style) => (
                <button
                  key={style.name}
                  onClick={() => narrow({ bodyStyle: style.name, q: '' })}
                  className={clsx(
                    'rounded-full border px-3 py-1 text-xs font-medium capitalize',
                    filters.bodyStyle === style.name
                      ? 'border-primary bg-primary/5 text-primary'
                      : 'border-border bg-card',
                  )}
                >
                  {style.name} ({style.count})
                </button>
              ))}
            </div>
          </>
        )}

        {(facets?.makes?.length ?? 0) > 0 && (
          <>
            <h3 className="mt-6 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              By make
            </h3>
            <div className="mt-2 flex flex-wrap gap-2">
              {facets!.makes.map((make) => (
                <button
                  key={make.name}
                  onClick={() => narrow({ make: make.name, q: '' })}
                  className={clsx(
                    'rounded-full border px-3 py-1 text-xs font-medium',
                    filters.make === make.name
                      ? 'border-primary bg-primary/5 text-primary'
                      : 'border-border bg-card',
                  )}
                >
                  {make.name} ({make.count})
                </button>
              ))}
            </div>
          </>
        )}
      </section>

      {/* ---- the lot --------------------------------------------------- */}
      <main className="mx-auto max-w-6xl px-5 pb-10">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="text-lg font-semibold">
            {data ? `${data.total} vehicle${data.total === 1 ? '' : 's'}` : 'Inventory'}
          </h2>
          {filtered && (
            <button
              onClick={() => {
                setDraft('')
                setFilters(NONE)
              }}
              className="text-sm font-medium text-primary hover:underline"
            >
              Clear filters
            </button>
          )}
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
          <>
            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {data.vehicles.map((car) => (
                <CarCard key={car.vin} car={car} />
              ))}
            </div>
            {data.total > data.vehicles.length && (
              <div className="mt-6 text-center">
                <button
                  onClick={() => setShown((n) => n + 24)}
                  className="rounded-lg border border-border bg-card px-4 py-2 text-sm font-medium"
                >
                  Show more ({data.total - data.vehicles.length} left)
                </button>
              </div>
            )}
          </>
        )}
      </main>

      {/* ---- hours, where we are, and how to reach a person ------------ */}
      <section className="border-t border-border bg-muted/30">
        <div className="mx-auto grid max-w-6xl grid-cols-1 gap-8 px-5 py-10 md:grid-cols-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold">{shop?.name || ' '}</h2>
            {street && <p className="mt-2 text-sm text-muted-foreground">{street}</p>}
            {cityLine && <p className="text-sm text-muted-foreground">{cityLine}</p>}
            {tel && (
              <a href={`tel:${tel}`} className="mt-1 block text-sm font-medium text-primary">
                {shop!.phone}
              </a>
            )}
            {shop?.address && (
              <a
                href={`https://maps.google.com/maps?q=${encodeURIComponent(shop.address)}`}
                target="_blank"
                rel="noreferrer"
                className="mt-3 inline-block rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium"
              >
                Get driving directions
              </a>
            )}
          </div>

          <div className="min-w-0">
            <h2 className="text-sm font-semibold">Our hours</h2>
            <table className="mt-2 text-sm text-muted-foreground">
              <tbody>
                {shop &&
                  openingHours(shop.hours).map((row) => (
                    <tr key={row.days}>
                      <td className="pr-4 font-medium text-foreground">{row.days}</td>
                      <td className="tnum whitespace-nowrap">{row.text}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>

          {/* Where their contact form sits. Not reproduced: a form that posts
              nowhere is the one thing this codebase will not build, and the
              assistant is what replaces it -- same fields, answered now. */}
          <div className="min-w-0">
            <h2 className="text-sm font-semibold">Contact us</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Ask about any car on the lot, financing, or a trade — and book a time to come in.
              {data?.channels.voice ? ' By message or by phone.' : ''}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                onClick={() => setOpen(true)}
                className="rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground"
              >
                Message us
              </button>
              {data?.channels.voice && (
                <a
                  href="/call"
                  className="rounded-lg border border-border bg-card px-3 py-1.5 text-sm font-medium"
                >
                  Call us
                </a>
              )}
            </div>
          </div>
        </div>
      </section>

      <footer className="border-t border-border bg-card">
        <div className="mx-auto max-w-6xl px-5 py-6 text-center text-xs text-muted-foreground">
          {site?.tagline && <p className="text-sm font-semibold text-foreground">{site.tagline}</p>}
          <p className="mt-2">
            {shop?.name} · {shop?.address}
          </p>
        </div>
      </footer>

      {/* ---- the widget ------------------------------------------------ */}
      <div className="fixed bottom-4 right-4 z-40 flex flex-col items-end gap-3">
        <div
          className={clsx(
            'w-[min(24rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-xl',
            'h-[min(34rem,calc(100dvh-7rem))]',
            open ? 'flex' : 'hidden',
          )}
        >
          <div className="flex items-center justify-between border-b border-border px-4 py-2">
            <span className="truncate text-sm font-semibold">
              {shop?.name ? `${possessive(shop.name)} assistant` : 'Assistant'}
            </span>
            <button
              onClick={() => setOpen(false)}
              aria-label="Close chat"
              className="rounded px-2 py-0.5 text-lg leading-none text-muted-foreground hover:bg-muted"
            >
              &times;
            </button>
          </div>
          {/* Mounted only while open. An iframe that exists from first paint
              starts a conversation for every visitor who never clicked. */}
          {open && (
            <iframe src="/chat?embed=1" title="Chat" className="min-h-0 flex-1 border-0" />
          )}
        </div>

        <button
          onClick={() => setOpen((v) => !v)}
          className="flex h-14 w-14 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg"
          aria-label={open ? 'Close chat' : 'Chat with us'}
        >
          {open ? (
            <span className="text-2xl leading-none">&times;</span>
          ) : (
            <Icon name="chat" className="h-6 w-6" />
          )}
        </button>
      </div>
    </div>
  )
}
