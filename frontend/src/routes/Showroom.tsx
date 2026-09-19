import { useEffect, useMemo, useState } from 'react'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import clsx from 'clsx'

import { api } from '../lib/api'
import { applyBrand } from '../lib/brand'
import { possessive, type Dealership, type Site } from '../lib/dealership'
import { money } from '../lib/format'
import { Icon } from '../components/Icon'
import { CarPhoto } from '../components/CarPhoto'
import { withStore } from '../lib/store'

/**
 * The dealership's own front page and their inventory list, with Liner on them.
 *
 * **Why this exists.** A demo is a link you send somebody, and the question a
 * dealer actually has is *what does this look like on my website* -- which
 * `/chat` cannot answer, because `/chat` is a chat window floating on nothing.
 * This is their name, their logo, their colour, their chrome, their address and
 * phone, their real cars, and the assistant in the corner where it would really
 * sit.
 *
 * **Their copy is served, not written in here.** Headings, welcome text, hero
 * images, nav, the one nav item they draw as a button, what they call the price,
 * and their social links all come from the profile's `site:` block. Hardcoding a
 * prospect's sentences into this component is the "Riverside Auto" bug one level
 * up: the next instance greets somebody in Craig and Landreth's words. A profile
 * with no `site:` block renders a plain storefront, which is honest and is what
 * Riverside gets.
 *
 * **The chrome follows their site; the page between it does not.** A great many
 * dealers run a black header and footer over a white body -- Alsbou's are
 * `#000000` with a `#424242` contact strip -- which is neither of the two things
 * `brand.surface` could say. `brand.chrome` is that third answer, and like
 * `surface` it is two words rather than two colours: it picks the dark palette
 * already in the token layer instead of carrying a hex into a stylesheet, so a
 * prospect's file cannot restyle the product into something unreadable.
 *
 * **The layout is their inventory page's layout.** A filter sidebar on the left,
 * a results toolbar carrying the live count and a sort control, a three-column
 * card grid on the right. That is not decoration: with 69 or 486 cars, chips
 * above the grid push the cars themselves below the fold, and "Sort by" is the
 * first control a person reaches for on somebody else's lot. Below `lg` the
 * sidebar collapses behind a toggle, which is what their own page does.
 *
 * **The browse filters are real.** Their site prints "Chevrolet (74)" and four
 * price bands; both are counted from rows here and both narrow the same grid.
 * A filter that promises 74 cars and shows 9 is worse than no filter, and it
 * is the easiest thing on a demo page to get wrong. By Type is drawn only when
 * the lot has body styles at all -- a Dealer Car Search crawl leaves that field
 * empty, so ten links that all return nothing would otherwise be the first
 * thing a prospect clicked. Their footer's `?bodystyle=SUV` shortcuts are left
 * out for the same reason and no other.
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
 * **There is no contact form and there is no "I want this car", and that is the
 * pitch.** Their real page has both, plus a pre-approval widget. Reproducing any
 * of them here would be a form that posts nowhere, which is exactly what this
 * codebase refuses to build. The assistant is what stands in their place: the
 * card's button opens it asking about that exact car, which captures the same
 * fields and answers now rather than on Monday. Pre-qualification is a credit
 * decision and stays a link to their own site.
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
  /** Which of the group's lots it is standing on. Empty for a single-site
   *  dealership, which is most of them. */
  location: string
  /** The dealer's own enquiry form, and only for a car they do not price
   *  online. Derived server-side from the listing URL. */
  inquiry_url: string
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

/** The orders `/api/showroom` will sort by, and what to call each one.
 *
 *  The keys are the server's and an unknown one is a 400 there, so this list
 *  and `SORTS` in `api/showroom.py` have to agree -- `make smoke` reads both
 *  and fails if they do not. The labels are ours rather than the dealer's:
 *  every dealer's toolbar says roughly this, and "Make/Model A to Z" is
 *  their own default, which is why it is first. */
const SORTS = [
  { key: 'az', label: 'Make/Model A to Z' },
  { key: 'price_low', label: 'Price: low to high' },
  { key: 'price_high', label: 'Price: high to low' },
  { key: 'year_new', label: 'Year: newest first' },
  { key: 'year_old', label: 'Year: oldest first' },
]

/** How long each banner image holds before the next fades in. Long enough to
 *  be looked at, short enough that a demo sees it move. */
const BANNER_MS = 6000

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

/**
 * Their rotating banner.
 *
 * **Every image is tried and a broken one leaves the rotation**, rather than
 * one failure taking the banner down. These are hotlinked from the dealer's own
 * CDN: an image host that refuses an off-site referrer, a URL that has moved
 * and a venue firewall all look identical, all happen on somebody else's
 * laptop, and a torn-page icon across the top of their own front page is the
 * failure to prevent. With none of them loading the section keeps its shape and
 * the words on it stay readable, which is what has been seen here -- the egress
 * proxy refuses Alsbou's asset host, so the rotation itself is unproven against
 * their real images.
 *
 * **It holds still for anybody who has asked it to.** A banner that moves under
 * a person reading the paragraph beside it is a thing to switch off, and
 * `prefers-reduced-motion` is how they have already said so.
 */
function Banner({ images }: { images: string[] }) {
  const [broken, setBroken] = useState<string[]>([])
  const [at, setAt] = useState(0)
  const live = images.filter((url) => !broken.includes(url))

  useEffect(() => {
    if (live.length < 2) return
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    const timer = window.setInterval(() => setAt((n) => n + 1), BANNER_MS)
    return () => window.clearInterval(timer)
  }, [live.length])

  const showing = live.length ? live[at % live.length] : ''
  return (
    <div className="absolute inset-0 -z-10">
      {images.map((url) => (
        <img
          key={url}
          src={url}
          alt=""
          aria-hidden
          onError={() => setBroken((b) => (b.includes(url) ? b : [...b, url]))}
          className={clsx(
            'absolute inset-0 h-full w-full object-cover transition-opacity duration-1000',
            url === showing ? 'opacity-100' : 'opacity-0',
          )}
        />
      ))}
      {/* The scrim, and it is load-bearing rather than styling: the words over
          this are `text-foreground`, so they have to sit on the page's own
          background at the bottom whichever palette is in force. A photograph
          behind body copy with no scrim is unreadable in one of the two. */}
      <div className="absolute inset-0 bg-gradient-to-t from-background via-background/85 to-background/40" />
    </div>
  )
}

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

function CarCard({
  car,
  priceLabel,
  onAsk,
}: {
  car: Car
  priceLabel: string
  onAsk: (car: Car) => void
}) {
  return (
    <article className="flex min-w-0 flex-col overflow-hidden rounded-xl border border-border bg-card">
      <div className="aspect-[4/3] w-full overflow-hidden bg-muted">
        <CarPhoto
          vin={car.vin}
          photoUrl={car.photo_url}
          alt={car.title}
          className="h-full w-full object-cover"
        />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1 p-4">
        <h3 className="truncate text-sm font-semibold">{car.title}</h3>
        {car.trim && <p className="truncate text-xs text-muted-foreground">{car.trim}</p>}
        {/* Their own label above their own number -- "ADVERTISED PRICE" on
            Alsbou's cards. Served rather than written here, and a dealership
            that calls it nothing gets nothing rather than a guess. */}
        {priceLabel && (
          <p className="mt-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            {priceLabel}
          </p>
        )}
        {/* A car with no published price is a real listing state, not a
            failure -- it belongs on the lot, Liner simply cannot quote it.
            Their own site answers it with an enquiry form at the same URL, so
            the words become the link rather than sitting dead next to one. */}
        {car.price ? (
          <p className="text-lg font-semibold text-primary">{money(car.price)}</p>
        ) : car.inquiry_url ? (
          <a
            href={car.inquiry_url}
            target="_blank"
            rel="noreferrer"
            className="text-lg font-semibold text-primary underline-offset-4 hover:underline"
          >
            Call for price
          </a>
        ) : (
          <p className="text-lg font-semibold text-primary">Call for price</p>
        )}
        <p className="text-xs text-muted-foreground">
          {car.mileage != null ? `${car.mileage.toLocaleString()} miles` : 'Mileage not listed'}
          {car.location ? ` · ${car.location}` : ''}
        </p>
        {/* Where "I WANT THIS CAR" and "GET PRE-APPROVED" sit on their cards.
            Both are forms, and the assistant is what replaces them: one button
            that opens it already asking about this car. It prefills the
            composer rather than sending, so the sentence is the buyer's to
            press -- the rule the rails follow, for the same reason. */}
        <button
          onClick={() => onAsk(car)}
          className="mt-3 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"
        >
          Ask about this one
        </button>
      </div>
    </article>
  )
}

export function Showroom() {
  const [open, setOpen] = useState(false)
  /* What the widget should open already asking. It is part of the iframe's URL
   * rather than a message posted into it: the frame is the real `/chat`, and a
   * second channel into it would be a second way for the storefront to write
   * into a buyer's transcript. */
  const [ask, setAsk] = useState('')
  const [shown, setShown] = useState(24)
  const [sort, setSort] = useState(SORTS[0].key)
  const [filters, setFilters] = useState<Filters>(NONE)
  const [draft, setDraft] = useState('')
  /* The sidebar is a column at `lg` and a disclosure below it, which is what
   * their own page does. Closed by default on a phone: a rep or a buyer opening
   * this on a handset wants the cars, and eight filter groups above them is the
   * whole first screen spent on controls. */
  const [showFilters, setShowFilters] = useState(false)
  /* Their logo lives on their own CDN, and a broken <img> in the middle of a
   * demo is worse than not showing one: the alt text renders as a torn-page
   * icon next to the dealership's own name. Falling back to the name is a page
   * that still reads. The banner handles its own, per image. */
  const [logoBroke, setLogoBroke] = useState(false)

  const params = useMemo(() => {
    const p = new URLSearchParams({ limit: String(shown), sort })
    if (filters.q) p.set('q', filters.q)
    if (filters.make) p.set('make', filters.make)
    if (filters.bodyStyle) p.set('body_style', filters.bodyStyle)
    if (filters.min != null) p.set('min_price', String(filters.min))
    if (filters.max != null) p.set('max_price', String(filters.max))
    return p.toString()
  }, [shown, sort, filters])

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
  const site: Site | undefined = shop?.site
  const facets = data?.facets
  const filtered = Boolean(filters.q || filters.make || filters.bodyStyle || filters.max || filters.min)
  const tel = shop?.phone ? shop.phone.replace(/[^\d+]/g, '') : ''
  // "4156 Shelbyville Rd., Louisville, KY 40207" -> street / city line, the
  // way their own header stacks it.
  const [street, ...rest] = (shop?.address || '').split(',')
  const cityLine = rest.join(',').trim()
  /* Their header, contact strip, results toolbar and footer, in the dark
     palette that is already in the token layer.
   *
   * **`theme-buyer` is repeated here and that is load-bearing.** It is already
   * on the root, but a custom property is *inherited*: the nearest ancestor
   * that declares `--primary` wins, and `.dark` declares one. On the root the
   * two classes sit on the same element and `.theme-buyer` wins for coming
   * later in the stylesheet, which is where the accent comes from -- nested,
   * `.dark` is nearer, so the "GET PRE-QUALIFIED" button on the black nav came
   * out the dark palette's near-white instead of their gold. Declaring both
   * here puts the two rules back on one element and the accent back on the
   * button. Measured in a browser: the button is white without this line. */
  const chrome = shop?.brand?.chrome === 'dark' ? 'dark theme-buyer' : ''
  const heroes = site?.hero_images ?? []

  const narrow = (next: Partial<Filters>) => {
    setShown(24)
    setShowFilters(false)
    setFilters((f) => ({ ...f, ...next }))
  }

  /** Open the assistant with the question this card is for, already typed.
   *
   *  The VIN is in it because a lot with three 2019 Silverados has three cards
   *  that would otherwise send the same sentence, and `search_inventory`
   *  matches a VIN exactly. */
  const askAbout = (car: Car) => {
    setAsk(`Tell me about the ${car.title}${car.trim ? ` ${car.trim}` : ''} (VIN ${car.vin}).`)
    setOpen(true)
  }

  const clearAll = () => {
    setDraft('')
    setFilters(NONE)
    setShowFilters(false)
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
        <h3 className="px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Price
        </h3>
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
          <h3 className="px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Type
          </h3>
          <div className="mt-1">
            {facets!.body_styles.map((style) => (
              <FilterRow
                key={style.name}
                label={style.name}
                count={style.count}
                on={filters.bodyStyle === style.name}
                onClick={() =>
                  narrow({ bodyStyle: filters.bodyStyle === style.name ? '' : style.name })
                }
              />
            ))}
          </div>
        </div>
      )}

      {(facets?.makes?.length ?? 0) > 0 && (
        <div className="mt-5">
          <h3 className="px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Make
          </h3>
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
      {/* ---- their chrome: contact strip + logo + nav ------------------ */}
      <div className={chrome}>
        <div className="bg-background text-foreground">
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

          <header className="border-b border-border">
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
              {/* Their real pages, on their real site. A nav that 404s inside
                  our app mid-demo is worse than one that leaves it, and we are
                  not pretending to have rebuilt Financing or We Buy Cars. */}
              <nav className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-2 text-sm font-medium">
                {(site?.links ?? []).map((l) => (
                  <a key={l.href} href={l.href} rel="noreferrer" target="_blank" className="hover:text-primary">
                    {l.label}
                  </a>
                ))}
                {/* The one they emphasise, as a button, because they do.
                    Pre-qualification is a credit decision, so it stays a link
                    to their own host rather than becoming a form here. */}
                {site?.cta && (
                  <a
                    href={site.cta.href}
                    rel="noreferrer"
                    target="_blank"
                    className="rounded-md bg-primary px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-primary-foreground"
                  >
                    {site.cta.label}
                  </a>
                )}
              </nav>
            </div>
          </header>
        </div>
      </div>

      {/* ---- hero: their banner, with the one thing we add over it ----- */}
      <section className="relative isolate overflow-hidden border-b border-border">
        {heroes.length > 0 && <Banner images={heroes} />}
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
              onClick={() => {
                setAsk('')
                setOpen(true)
              }}
              className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
            >
              Chat with us
            </button>
            {/* Counted, never declared: with no VOICE_PROVIDER there is no
                phone to answer, and a button opening a page that says so is
                worse than no button. */}
            {data?.channels.voice && (
              <a href={withStore('/call')} className="rounded-lg border border-border bg-card px-4 py-2 text-sm font-medium">
                Call us
              </a>
            )}
          </div>
        </div>
      </section>

      {/* ---- their welcome copy, in the card their own page puts it in -- */}
      {(site?.welcome?.length ?? 0) > 0 && (
        <section className="border-b border-border bg-muted/30">
          <div className="mx-auto max-w-4xl px-5 py-10">
            <div className="rounded-2xl border border-border bg-card p-6 sm:p-10">
              <h2 className="text-center text-xl font-semibold text-primary">
                {site?.heading || `Welcome to ${shop?.name ?? ''}`}
              </h2>
              <div className="mx-auto mt-4 max-w-2xl space-y-3 text-sm leading-relaxed text-muted-foreground">
                {site!.welcome.map((p) => (
                  <p key={p.slice(0, 40)}>{p}</p>
                ))}
              </div>
              {shop?.address && (
                <div className="mt-6 text-center">
                  <a
                    href={`https://maps.google.com/maps?q=${encodeURIComponent(shop.address)}`}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-block rounded-lg border border-border px-4 py-2 text-sm font-medium"
                  >
                    {shop.address}
                  </a>
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {/* ---- the lot: sidebar + toolbar + grid, their layout ----------- */}
      <main id="inventory" className="mx-auto max-w-6xl px-5 py-8 lg:flex lg:gap-8">
        {/* A column at `lg`, a disclosure below it. `hidden lg:block` rather
            than a second copy of the markup: two sidebars is two places a
            filter group gets added to and one place it gets forgotten. */}
        <aside
          className={clsx(
            'min-w-0 shrink-0 lg:block lg:w-60',
            showFilters ? 'block' : 'hidden',
          )}
        >
          <div className="rounded-xl border border-border bg-card p-3 lg:sticky lg:top-4">{sidebar}</div>
        </aside>

        <div className="min-w-0 flex-1">
          {/* Their results toolbar, in the same grey as their nav strip. The
              count is the same number the sidebar heading shows, from the same
              request -- two counts for one grid is how a header says 16 over a
              list of 147. */}
          <div className={chrome}>
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-muted px-4 py-3 text-foreground">
              <div className="flex min-w-0 items-center gap-3">
                <button
                  onClick={() => setShowFilters((v) => !v)}
                  className="rounded-md border border-border px-3 py-1.5 text-xs font-medium lg:hidden"
                >
                  {showFilters ? 'Hide filters' : 'Show filters'}
                </button>
                <p className="min-w-0 truncate text-sm font-medium">
                  {data
                    ? `${data.total} vehicle${data.total === 1 ? '' : 's'} available`
                    : 'Loading the lot'}
                </p>
              </div>
              <label className="flex min-w-0 items-center gap-2 text-xs">
                <span className="shrink-0 text-muted-foreground">Sort by</span>
                <select
                  value={sort}
                  onChange={(e) => {
                    setShown(24)
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
            <>
              <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {data.vehicles.map((car) => (
                  <CarCard
                    key={car.vin}
                    car={car}
                    priceLabel={site?.price_label ?? ''}
                    onAsk={askAbout}
                  />
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
        </div>
      </main>

      {/* ---- their footer: where we are, when we are open, how to ask -- */}
      <div className={chrome}>
        <footer className="border-t border-border bg-background text-foreground">
          <div className="mx-auto grid max-w-6xl grid-cols-1 gap-8 px-5 py-10 sm:grid-cols-2 lg:grid-cols-4">
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
                  className="mt-3 inline-block rounded-lg border border-border px-3 py-1.5 text-xs font-medium"
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

            {/* Their footer's own link groups, minus the two we would be
                faking. `?bodystyle=SUV` returns nothing for a lot whose export
                carries no body style, and their make shortcuts are answered
                better by the counted filters in the sidebar than by a link
                that leaves the page. */}
            {(site?.links?.length ?? 0) > 0 && (
              <div className="min-w-0">
                <h2 className="text-sm font-semibold">More</h2>
                <ul className="mt-2 space-y-1.5 text-sm text-muted-foreground">
                  {site!.links.map((l) => (
                    <li key={l.href}>
                      <a href={l.href} target="_blank" rel="noreferrer" className="hover:text-primary">
                        {l.label}
                      </a>
                    </li>
                  ))}
                  {site?.cta && (
                    <li>
                      <a
                        href={site.cta.href}
                        target="_blank"
                        rel="noreferrer"
                        className="font-medium text-primary hover:underline"
                      >
                        {site.cta.label}
                      </a>
                    </li>
                  )}
                </ul>
              </div>
            )}

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
                  onClick={() => {
                    setAsk('')
                    setOpen(true)
                  }}
                  className="rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground"
                >
                  Message us
                </button>
                {data?.channels.voice && (
                  <a
                    href={withStore('/call')}
                    className="rounded-lg border border-border px-3 py-1.5 text-sm font-medium"
                  >
                    Call us
                  </a>
                )}
              </div>
            </div>
          </div>

          <div className="border-t border-border">
            <div className="mx-auto max-w-6xl px-5 py-5 text-center text-xs text-muted-foreground">
              {site?.tagline && <p className="text-sm font-semibold text-foreground">{site.tagline}</p>}
              <p className="mt-2">
                {shop?.name} · {shop?.address}
              </p>
            </div>
          </div>
        </footer>
      </div>

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
              starts a conversation for every visitor who never clicked.
              Keyed on the question so pressing a second card's button reloads
              the frame with that car's sentence in the box -- the transcript
              comes back from localStorage, so nothing said is lost. */}
          {open && (
            <iframe
              key={ask}
              src={withStore(`/chat?embed=1${ask ? `&ask=${encodeURIComponent(ask)}` : ''}`)}
              title="Chat"
              className="min-h-0 flex-1 border-0"
            />
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
