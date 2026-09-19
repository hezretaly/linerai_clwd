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
  /** The card's specification cells, already cased and formatted, in the order
   *  a listing prints them. Composed server-side so a card and a sentence
   *  about the same car cannot disagree -- and short by however many fields
   *  the dealer's export did not state, because an empty cell under a label
   *  reads as a page that failed to load. */
  specs: { label: string; value: string }[]
  /** Their stock number, which is how a dealer refers to a car on the phone. */
  stock_number: string
  /** What they advertise before their own fees, where `price` already includes
   *  them. Stated by the export; `null` where it did not say. */
  advertised_price: number | null
  /** Their vehicle-history provider's report. A link to what the dealer
   *  published, never a claim about what is in it. */
  history_url: string
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

/** How many columns the card's specification grid gets, by how many cells it
 *  has to put in it. Written out because Tailwind reads the source rather than
 *  the running value, so `grid-cols-${n}` is a class that never gets built. */
const COLS: Record<number, string> = { 1: 'grid-cols-1', 2: 'grid-cols-2', 3: 'grid-cols-3' }

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
 * The photograph across the top of their front page.
 *
 * **One image, and it is meant to be seen.** This was a cross-fading rotation
 * of five drawn behind a `from-background via-background/85` scrim -- and at
 * 85% the scrim hid the picture entirely, so the hero rendered as a plain band
 * of page colour with a heading on it. The five turned out not to be hero
 * slides at all but their linked banner strip, which is `BannerTile` below.
 *
 * **A broken one leaves no hole.** It is hotlinked from the dealer's own CDN:
 * an image host that refuses an off-site referrer, a URL that has moved and a
 * venue firewall all look identical and all happen on somebody else's laptop.
 * With nothing loading the section keeps its height and the caption over it
 * stays readable -- which is what has been seen here, because the egress proxy
 * refuses Alsbou's asset host, so the photograph itself is unproven.
 */
function Hero({ src }: { src: string }) {
  const [broke, setBroke] = useState(false)
  return (
    <div className="absolute inset-0 -z-10 overflow-hidden bg-muted">
      {!broke && (
        <img
          src={src}
          alt=""
          aria-hidden
          onError={() => setBroke(true)}
          className="h-full w-full object-cover"
        />
      )}
      {/* Enough to carry white text on any photograph, and no more. The words
          over this declare their own colour rather than inheriting the page's,
          so this darkens the picture instead of replacing it. */}
      <div className="absolute inset-0 bg-black/45" />
    </div>
  )
}

/**
 * One tile of their banner strip: an image that is a link.
 *
 * **The label is what survives a broken image.** Five links whose only content
 * is an `<img>` become five empty boxes the moment the dealer's CDN is
 * unreachable -- indistinguishable from a page that failed to build, and
 * unusable either way. The label is drawn under the picture and stands alone
 * without it, so the strip still works as five calls to action.
 */
function BannerTile({ image, href, label }: { image: string; href: string; label: string }) {
  const [broke, setBroke] = useState(false)
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="group block min-w-0 overflow-hidden rounded-lg border border-border bg-card"
    >
      <div className="aspect-[93/50] w-full overflow-hidden bg-muted">
        {!broke && (
          <img
            src={image}
            alt=""
            aria-hidden
            onError={() => setBroke(true)}
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
          />
        )}
      </div>
      <span className="block truncate px-3 py-2 text-center text-xs font-semibold uppercase tracking-wide">
        {label}
      </span>
    </a>
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

/**
 * One car, laid out the way a used-car listing card is laid out.
 *
 * **It carries what their card carries**, because the fields are the point: a
 * card reading only "2018 Audi Q7" over a price is a placeholder, and theirs
 * prints the stock number, the price with its label, what the price includes,
 * and six specifications in a bordered grid. Mileage was on one line of small
 * grey text here and the other five were not on the page at all -- their
 * export had them and the importer was not reading them.
 *
 * **The pricing disclosure states; it does not compute.** Both figures come
 * from the export and the sentence between them is the dealer's own. Nothing
 * here subtracts one from the other: which fees apply varies per car, and four
 * of Alsbou's 69 are electric and pay no smog fee, so a fixed schedule
 * subtracted from a total would be wrong on exactly the four nobody checks.
 *
 * **Where their three CTAs were, there is one.** "I WANT THIS CAR", "GET
 * PRE-APPROVED" and a Capital One pre-qualification widget are all forms, and
 * a form that posts nowhere is what this codebase will not build. The
 * assistant stands in their place and captures the same fields.
 */
function CarCard({
  car,
  priceLabel,
  priceNote,
  onAsk,
}: {
  car: Car
  priceLabel: string
  priceNote: string
  onAsk: (car: Car) => void
}) {
  const [showPricing, setShowPricing] = useState(false)
  /* Their disclosure only has something to disclose when the export stated a
     figure behind the headline one, or the dealer wrote the sentence. Drawn
     unconditionally it is a control that opens onto nothing. */
  const pricing = Boolean(car.price && (car.advertised_price || priceNote))

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

      <div className="flex min-w-0 flex-1 flex-col p-3">
        <h3 className="truncate text-sm font-semibold uppercase">{car.title}</h3>
        {car.trim && <p className="truncate text-xs font-bold">{car.trim}</p>}

        <div className="mt-1 flex min-w-0 items-end justify-between gap-3">
          <p className="min-w-0 truncate text-xs text-muted-foreground">
            {car.stock_number ? (
              <>
                Stock #: <span className="font-bold text-foreground">{car.stock_number}</span>
              </>
            ) : (
              ''
            )}
          </p>
          {/* Their label sits *beside* the number in two stacked lines, not
              above it -- "ADVERTISED / PRICE  $19,908" reading left to right.
              Above it, the number lost the emphasis their card gives it. */}
          <div className="flex shrink-0 items-center gap-2">
            {priceLabel && car.price ? (
              <span className="text-right text-[10px] font-bold uppercase leading-tight text-muted-foreground">
                {priceLabel.split(' ').map((word) => (
                  <span key={word} className="block">
                    {word}
                  </span>
                ))}
              </span>
            ) : null}
            {/* A car with no published price is a real listing state, not a
                failure -- it belongs on the lot, Liner simply cannot quote it.
                Their own site answers it with an enquiry form at the same URL,
                so the words become the link rather than sitting dead beside
                one. */}
            {car.price ? (
              <span className="text-lg font-semibold text-primary">{money(car.price)}</span>
            ) : car.inquiry_url ? (
              <a
                href={car.inquiry_url}
                target="_blank"
                rel="noreferrer"
                className="text-lg font-semibold text-primary underline-offset-4 hover:underline"
              >
                Call
              </a>
            ) : (
              <span className="text-lg font-semibold text-primary">Call</span>
            )}
          </div>
        </div>

        {pricing && (
          <div className="mt-1 text-right text-xs text-muted-foreground">
            <button
              onClick={() => setShowPricing((v) => !v)}
              aria-expanded={showPricing}
              className="underline underline-offset-2"
            >
              Pricing details
            </button>
            {showPricing && (
              <div className="mt-1 space-y-1 text-left">
                {car.advertised_price != null && (
                  <div className="flex items-baseline justify-between gap-4">
                    {/* Their own word again, the same one over the headline
                        figure. Written out here, one dealer's phrasing for
                        their own price would appear on the next dealership's
                        storefront -- which is exactly what serving the label
                        exists to prevent, and it was written out here first.
                        The fallback is deliberately nobody's wording rather
                        than a plausible one. */}
                    <span>{priceLabel || 'Price before fees'}</span>
                    <span className="tnum">{money(car.advertised_price)}</span>
                  </div>
                )}
                {priceNote && <p className="leading-snug">{priceNote}</p>}
                <div className="flex items-baseline justify-between gap-4 border-t border-border pt-1 font-semibold text-foreground">
                  <span>Total price</span>
                  <span className="tnum">{money(car.price)}</span>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Their specification grid: three to a row, divided, label over
            value. `divide-x` draws the dividers from the cells themselves, so
            a lot whose export states four of the six draws four cells and
            three dividers rather than six cells with two holes in it. */}
        {car.specs.length > 0 && (
          <dl
            className={clsx(
              'mt-2 grid border-t border-border text-center text-[11px]',
              /* Three to a row where there are three, and fewer where the
                 export stated fewer. Fixed at three, a lot that publishes
                 only mileage drew one cell and two empty thirds beside it --
                 which is the "reads as a page that failed to load" failure
                 the whole list exists to avoid, reintroduced by the grid
                 rather than by the data. Static class names because Tailwind
                 reads the source, not the runtime value. */
              COLS[Math.min(car.specs.length, 3)],
            )}
          >
            {car.specs.map((spec, i) => (
              <div
                key={spec.label}
                className={clsx(
                  'min-w-0 px-1 py-1.5',
                  /* Every cell but the last in its row carries the divider,
                     which is what makes a partial final row correct. */
                  i % Math.min(car.specs.length, 3) !== Math.min(car.specs.length, 3) - 1 &&
                    'border-r border-border',
                  i >= Math.min(car.specs.length, 3) && 'border-t border-border',
                )}
              >
                <dt className="truncate text-muted-foreground">{spec.label}</dt>
                <dd className="truncate font-bold">{spec.value}</dd>
              </div>
            ))}
          </dl>
        )}

        {car.location && (
          <p className="mt-2 truncate text-xs text-muted-foreground">At our {car.location} lot</p>
        )}

        <div className="mt-auto pt-3">
          {/* It prefills the composer rather than sending, so the sentence is
              the buyer's to press -- the rule the rails follow, for the same
              reason. */}
          <button
            onClick={() => onAsk(car)}
            className="w-full rounded-lg bg-primary px-3 py-2 text-sm font-semibold uppercase tracking-wide text-primary-foreground"
          >
            I want this car
          </button>
          {/* Their card puts a vehicle-history badge here. This is the link
              the dealer published and nothing more: a badge reading "no
              accidents" would be us making the claim, and we have not read
              the report. */}
          {car.history_url && (
            <a
              href={car.history_url}
              target="_blank"
              rel="noreferrer"
              className="mt-2 block text-center text-xs text-muted-foreground underline underline-offset-2"
            >
              Vehicle history report
            </a>
          )}
        </div>
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

  const narrow = (next: Partial<Filters>) => {
    setShown(24)
    setShowFilters(false)
    setFilters((f) => ({ ...f, ...next }))
  }

  /** The keyword box, and there are two of them on screen for the reason their
   *  page has two: one in the header, which is where a returning visitor looks,
   *  and one over the hero, which is the first control a new one sees. Both are
   *  the same `draft` and the same submit, so they cannot disagree about what
   *  was typed -- two states would be two boxes that each forget the other. */
  const submitSearch = (e: React.FormEvent) => {
    e.preventDefault()
    narrow({ q: draft.trim() })
    document.getElementById('inventory')?.scrollIntoView({ behavior: 'smooth' })
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
      {/* ---- their chrome: two bars, which is what a dealer's header is ---
           A black identity bar carrying the logo, the address, the number and
           their search box, then a separate flat nav strip in a lighter grey.
           It was one row before -- logo left, nav right -- and one row is the
           single thing that made this read as "a product page with their
           colours on it" rather than as their site. */}
      <div className={chrome}>
        <div className="bg-background text-foreground">
          <div className="mx-auto flex max-w-7xl flex-col gap-3 px-5 py-3 lg:flex-row lg:items-center lg:gap-8">
            <a href={site?.links?.[0]?.href || shop?.website_url || '#'} className="block shrink-0">
              {shop?.brand?.logo_url && !logoBroke ? (
                <img
                  src={shop.brand.logo_url}
                  alt={shop.name}
                  onError={() => setLogoBroke(true)}
                  /* Tall, because a dealer's logo is the largest thing in
                     their header and theirs is drawn at 100px. */
                  className="h-16 w-auto max-w-[280px] object-contain object-left sm:h-20"
                />
              ) : (
                <span className="truncate text-xl font-semibold">{shop?.name || ' '}</span>
              )}
            </a>

            <div className="flex min-w-0 flex-1 flex-col gap-2">
              {/* A marker and a handset in the accent beside white bold text,
                  which is exactly what their bar does and is the one place on
                  the page their gold appears against black. */}
              <div className="flex min-w-0 flex-wrap items-center gap-x-6 gap-y-1 text-xs sm:text-sm">
                {shop?.address && (
                  <a
                    href={`https://maps.google.com/maps?q=${encodeURIComponent(shop.address)}`}
                    target="_blank"
                    rel="noreferrer"
                    className="flex min-w-0 items-center gap-2 font-semibold hover:underline"
                  >
                    <Icon name="pin" className="h-4 w-4 shrink-0 text-primary" />
                    <span className="min-w-0 truncate">{shop.address}</span>
                  </a>
                )}
                {tel && (
                  <a href={`tel:${tel}`} className="flex items-center gap-2 font-semibold hover:underline">
                    <Icon name="phone" className="h-4 w-4 shrink-0 text-primary" />
                    <span>{shop!.phone}</span>
                  </a>
                )}
                {(site?.social ?? []).map((s) => (
                  <a key={s.href} href={s.href} rel="noreferrer" target="_blank" className="hover:underline">
                    {s.label}
                  </a>
                ))}
              </div>

              {/* Their search box lives in the header, and it is the first
                  control on their page. Square and flush with a solid accent
                  submit, the way theirs is drawn -- a rounded pill here reads
                  as our page rather than theirs. */}
              <form className="flex min-w-0" onSubmit={submitSearch}>
                <input
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="Search by year, make, model, VIN, stock #"
                  aria-label="Search the inventory"
                  className="min-w-0 flex-1 border border-input bg-card px-3 py-2 text-sm text-foreground outline-none focus:border-ring"
                />
                <button
                  aria-label="Search"
                  className="flex shrink-0 items-center justify-center bg-primary px-4 text-primary-foreground"
                >
                  <Icon name="search" className="h-4 w-4" />
                </button>
              </form>
            </div>
          </div>

          {/* The nav strip. `bg-muted` is the dark palette's lighter grey,
              which is the relationship a dealer's nav bar has to the black
              bar above it -- a token rather than their two hex values, for
              the reason `brand.chrome` is two words. Spread edge to edge at
              `lg` because theirs runs the full width of the container. */}
          {(site?.links?.length ?? 0) > 0 && (
            <nav className="bg-muted">
              <ul className="mx-auto flex max-w-7xl flex-wrap items-stretch px-5 text-xs font-medium uppercase tracking-wide lg:justify-between lg:px-8">
                {site!.links.map((l) => (
                  <li key={l.href} className="min-w-0">
                    <a
                      href={l.href}
                      rel="noreferrer"
                      target="_blank"
                      className={clsx(
                        'flex h-full items-center px-3 py-3 hover:text-primary',
                        /* The one they emphasise, in the accent, in its own
                           position. Appended to the end as a pill it rendered
                           sixth where their nav has it third. */
                        l.href === site?.cta?.href && 'text-primary',
                      )}
                    >
                      {l.label}
                    </a>
                  </li>
                ))}
              </ul>
            </nav>
          )}
        </div>
      </div>

      {/* ---- hero: their photograph, with their caption over it --------
           The image is the section, not a wash behind it. It was drawn as a
           background under a `from-background via-background/85` scrim, which
           at 85% hid the photograph completely: the hero read as a plain band
           of page colour with a heading on the left. Theirs is a picture of
           the forecourt with the dealership's name centred over it. */}
      {site?.hero_image ? (
        <section className="relative isolate">
          <Hero src={site.hero_image} />
          <div className="relative mx-auto flex min-h-[220px] max-w-7xl flex-col items-center justify-center gap-5 px-5 py-14 text-center sm:min-h-[320px]">
            {/* White with a shadow, declared rather than inherited: the words
                sit on a photograph, so `text-foreground` would be black on it
                in light mode and unreadable either way once the picture loads.
                Their own caption is white over a black text-shadow. */}
            <h1
              className="text-2xl font-bold uppercase tracking-wide text-white sm:text-4xl"
              style={{ textShadow: '0 2px 6px rgba(0,0,0,0.75)' }}
            >
              {site?.heading || (shop?.name ? `Welcome to ${shop.name}` : ' ')}
            </h1>
            <form className="flex w-full max-w-lg min-w-0" onSubmit={submitSearch}>
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Search by year, make, model, VIN, stock #"
                aria-label="Search the inventory"
                /* Over the photograph, the way theirs is: a translucent dark
                   field with an accent edge, so it reads on any picture. */
                className="min-w-0 flex-1 border-2 border-primary bg-black/55 px-3 py-2.5 text-sm text-white outline-none placeholder:text-white/70"
              />
              <button
                aria-label="Search"
                className="flex shrink-0 items-center justify-center border-2 border-primary bg-primary px-5 text-primary-foreground"
              >
                <Icon name="search" className="h-4 w-4" />
              </button>
            </form>
          </div>
        </section>
      ) : null}

      {/* ---- their banner strip: five images, five destinations --------
           Not a rotation. These were being cross-faded as hero slides, which
           showed one of their five calls to action at a time and none of them
           as a link. Each is tried and a broken one keeps its label as a tile
           rather than leaving a hole -- they are hotlinked from the dealer's
           CDN, and a row of torn-page icons across their own front page is the
           failure to prevent. */}
      {(site?.banners?.length ?? 0) > 0 && (
        <section className="mx-auto grid max-w-7xl grid-cols-1 gap-3 px-5 py-6 sm:grid-cols-2 lg:grid-cols-5">
          {site!.banners.map((b) => (
            <BannerTile key={b.href} {...b} />
          ))}
        </section>
      )}

      {/* ---- their About section: their copy beside a map --------------
           Two columns, which is what theirs is: the rich-text card on the left
           with its three subheads, the map on the right. It was one centred
           card with the subheads flattened out of it and no map at all, and
           the map is the half a buyer actually uses. */}
      {(site?.welcome?.length ?? 0) > 0 && (
        <section className="border-y border-border bg-muted/30">
          <div className="mx-auto grid max-w-7xl grid-cols-1 gap-8 px-5 py-10 lg:grid-cols-2 lg:items-start">
            <div className="min-w-0">
              <h2 className="text-2xl font-semibold">
                Welcome to <span className="text-primary">{shop?.name}</span>
              </h2>
              <div className="mt-4 space-y-3 text-sm leading-relaxed text-muted-foreground">
                {site!.welcome.map((p) => (
                  <p key={p.slice(0, 40)}>{p}</p>
                ))}
              </div>
              {site!.sections.map((s) => (
                <div key={s.heading || s.body.slice(0, 40)} className="mt-5">
                  {s.heading && <h3 className="text-base font-semibold">{s.heading}</h3>}
                  <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{s.body}</p>
                </div>
              ))}
            </div>
            {/* Derived from the address rather than stored, so it cannot go
                stale against it and needs no key: one fact, one answer. */}
            {shop?.address && (
              <div className="min-w-0 overflow-hidden rounded-xl border border-border">
                <iframe
                  title={`Map to ${shop.name}`}
                  src={`https://www.google.com/maps?q=${encodeURIComponent(shop.address)}&output=embed`}
                  loading="lazy"
                  referrerPolicy="no-referrer-when-downgrade"
                  className="h-64 w-full border-0 lg:h-[26rem]"
                />
              </div>
            )}
          </div>
        </section>
      )}

      {/* ---- the lot: sidebar + toolbar + grid, their layout ----------- */}
      <main id="inventory" className="mx-auto max-w-7xl px-5 py-8 lg:flex lg:gap-8">
        {/* A column at `lg`, a disclosure below it. `hidden lg:block` rather
            than a second copy of the markup: two sidebars is two places a
            filter group gets added to and one place it gets forgotten. */}
        <aside
          className={clsx(
            'min-w-0 shrink-0 lg:block lg:w-64',
            showFilters ? 'block' : 'hidden',
          )}
        >
          <div className="rounded-xl border border-border bg-card p-3 lg:sticky lg:top-4">{sidebar}</div>
        </aside>

        <div className="min-w-0 flex-1">
          {/* Their page heads the grid with what the lot *is* and how much of
              it there is -- "Used Cars for Sale in Santa Ana, CA" over "69
              vehicles available". The city comes off the address rather than
              being a second copy of it in the profile. */}
          <h1 className="text-xl font-semibold sm:text-2xl">
            Used cars for sale{cityLine ? ` in ${cityLine.replace(/\s+\d{5}(-\d{4})?$/, '')}` : ''}
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
                  {filtered && data
                    ? `${data.total} match${data.total === 1 ? '' : 'es'}`
                    : 'All vehicles'}
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
                    priceNote={site?.price_note ?? ''}
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
          <div className="mx-auto grid max-w-7xl grid-cols-1 gap-8 px-5 py-10 sm:grid-cols-2 lg:grid-cols-4">
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
            <div className="mx-auto max-w-7xl px-5 py-5 text-center text-xs text-muted-foreground">
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
