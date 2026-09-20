import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { CarCard } from './CarCard'
import { BannerTile, Hero, Promo, StyleTile } from './Media'
import { StorefrontShell, useAssistant } from './Shell'
import { useStorefront } from '../_shared/useStorefront'
import { SEARCH_PLACEHOLDER, type Car, type Dealership, type Facets, type Site } from '../_shared/types'
import { Icon } from '../../components/Icon'

/**
 * The dealership's own front page, with Liner on it. `/<store>`.
 *
 * **Why this is at the store root.** A demo is a link you send somebody, and
 * `linerai.us/alsbou` is the link. It used to answer with *Liner's* marketing
 * page: the prefix middleware strips `/alsbou` to `/`, and `/` served
 * `landing.html` regardless. A prospect opening their own demo saw our
 * homepage. The server now serves the SPA at a prefixed root and this route
 * is what the SPA draws there; the unprefixed root is still ours.
 *
 * **It is their home page's shape, section for section, from the profile.**
 * Hero with the search over it, the strip of five linked tiles, the
 * body-style pictures, a featured row from the lot, a promo band, the About
 * copy beside a map, a second band, then the footer. Every sentence, picture
 * and link comes from the `site:` block; nothing here names a dealership. A
 * profile that states none of it gets a hero-less page with the lot's
 * highlights and the footer, which is honest and is what Riverside gets.
 *
 * **The featured row is real cars from the real lot**, where their page runs
 * five auto-rotating carousels of "specials". There is no specials flag in any
 * export, so nothing here claims one: it is the newest of the lot, six of
 * them, through the same `offerable` predicate the assistant searches. The
 * rotation is not reproduced -- a card that changes under the reader every
 * 1.5 seconds is a thing to switch off, and six side by side is the same
 * information held still.
 *
 * **A body-style tile is drawn only where the lot has that style.** Counted
 * from the facets, so a picture of a pickup on a lot with no trucks is not a
 * link to an empty grid -- the same rule the inventory page's By Type group
 * follows by not being drawn at all.
 */

const FEATURED = 6

export function Landing() {
  const navigate = useNavigate()
  const [draft, setDraft] = useState('')
  const { data } = useStorefront(`limit=${FEATURED}&sort=year_new`)
  const shop = data?.dealership
  const site: Site | undefined = shop?.site

  /** A search typed on the front page lands on the list, already narrowed.
   *  The router keeps the store prefix, so this stays inside the dealership. */
  const search = () => {
    const q = draft.trim()
    navigate(q ? `/showroom?q=${encodeURIComponent(q)}` : '/showroom')
  }

  return (
    <StorefrontShell shop={shop} voice={Boolean(data?.channels.voice)} search={{ draft, setDraft, submit: search }}>
      <HeroSection site={site} shop={shop} draft={draft} setDraft={setDraft} onSearch={search} />

      {(site?.banners?.length ?? 0) > 0 && (
        <section className="mx-auto grid max-w-7xl grid-cols-1 gap-3 px-5 py-6 sm:grid-cols-2 lg:grid-cols-5">
          {site!.banners.map((b) => (
            <BannerTile key={b.href} {...b} />
          ))}
        </section>
      )}

      <BodyStyles site={site} facets={data?.facets} onPick={(style) => navigate(`/showroom?body_style=${encodeURIComponent(style)}`)} />

      {data && data.vehicles.length > 0 && <Featured cars={data.vehicles} site={site} total={data.total} />}

      {site?.promos?.[0] && (
        <section className="mx-auto max-w-7xl px-5 py-4">
          <Promo {...site.promos[0]} />
        </section>
      )}

      <About site={site} shop={shop} />

      {site?.promos?.[1] && (
        <section className="mx-auto max-w-7xl px-5 py-4">
          <Promo {...site.promos[1]} />
        </section>
      )}
    </StorefrontShell>
  )
}

/** Their photograph with their caption and the search box over it.
 *
 *  The image is the section, not a wash behind it. It was drawn as a
 *  background under a `from-background via-background/85` scrim, which at 85%
 *  hid the photograph completely: the hero read as a plain band of page colour
 *  with a heading on the left. Theirs is a picture of the forecourt with the
 *  dealership's name centred over it. A profile with no `hero_image` gets a
 *  plain heading band with the same search box, so the front page still opens
 *  on the thing a buyer came to do. */
function HeroSection({
  site,
  shop,
  draft,
  setDraft,
  onSearch,
}: {
  site: Site | undefined
  shop: Dealership | undefined
  draft: string
  setDraft: (v: string) => void
  onSearch: () => void
}) {
  const heading = site?.heading || (shop?.name ? `Welcome to ${shop.name}` : ' ')
  const photo = Boolean(site?.hero_image)
  return (
    <section className="relative isolate">
      {photo && <Hero src={site!.hero_image} />}
      <div className="relative mx-auto flex min-h-[220px] max-w-7xl flex-col items-center justify-center gap-5 px-5 py-14 text-center sm:min-h-[320px]">
        {/* White with a shadow, declared rather than inherited: the words sit
            on a photograph, so `text-foreground` would be black on it in light
            mode and unreadable either way once the picture loads. Their own
            caption is white over a black text-shadow. Without a photograph the
            heading is the page's own ink. */}
        <h1
          className={photo ? 'text-2xl font-bold uppercase tracking-wide text-white sm:text-4xl' : 'text-2xl font-bold sm:text-4xl'}
          style={photo ? { textShadow: '0 2px 6px rgba(0,0,0,0.75)' } : undefined}
        >
          {heading}
        </h1>
        <form
          className="flex w-full min-w-0 max-w-lg"
          onSubmit={(e) => {
            e.preventDefault()
            onSearch()
          }}
        >
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={SEARCH_PLACEHOLDER}
            aria-label="Search the inventory"
            /* Over the photograph, the way theirs is: a translucent dark field
               with an accent edge, so it reads on any picture. */
            className={
              photo
                ? 'min-w-0 flex-1 border-2 border-primary bg-black/55 px-3 py-2.5 text-sm text-white outline-none placeholder:text-white/70'
                : 'min-w-0 flex-1 border-2 border-primary bg-card px-3 py-2.5 text-sm text-foreground outline-none'
            }
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
  )
}

/** "Explore Vehicles By Body Style" -- their pictures, our counted grid. */
function BodyStyles({
  site,
  facets,
  onPick,
}: {
  site: Site | undefined
  facets: Facets | undefined
  onPick: (style: string) => void
}) {
  const held = new Map((facets?.body_styles ?? []).map((s) => [s.name.toLowerCase(), s.count]))
  const tiles = (site?.body_style_tiles ?? []).filter((t) => (held.get(t.style) ?? 0) > 0)
  if (tiles.length === 0) return null
  return (
    /* Their section is black over a white page: a band of the dark palette,
       which is the same token the chrome uses, for the reason it does. */
    <section className="dark theme-buyer bg-background text-foreground">
      <div className="mx-auto max-w-7xl px-5 py-10">
        <h2 className="text-center text-xl font-semibold uppercase tracking-wide">Explore vehicles by body style</h2>
        <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {tiles.map((t) => (
            <StyleTile key={t.style} image={t.image} label={`${t.label} (${held.get(t.style)})`} onClick={() => onPick(t.style)} />
          ))}
        </div>
      </div>
    </section>
  )
}

/** The newest of the lot, where their page runs its rotating specials. */
function Featured({ cars, site, total }: { cars: Car[]; site: Site | undefined; total: number }) {
  const assistant = useAssistant()
  const navigate = useNavigate()
  return (
    <section className="mx-auto max-w-7xl px-5 py-10">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-xl font-semibold">Just arrived</h2>
        <button onClick={() => navigate('/showroom')} className="text-sm font-medium text-primary hover:underline">
          See all {total} vehicle{total === 1 ? '' : 's'}
        </button>
      </div>
      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
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
    </section>
  )
}

/** Their About copy beside a map: two columns, which is what theirs is. The
 *  rich-text card on the left with its three subheads, the map on the right.
 *  It was one centred card with the subheads flattened out of it and no map at
 *  all, and the map is the half a buyer actually uses. The map is derived from
 *  the address rather than stored, so it cannot go stale against it and needs
 *  no key: one fact, one answer. */
function About({ site, shop }: { site: Site | undefined; shop: Dealership | undefined }) {
  if ((site?.welcome?.length ?? 0) === 0) return null
  return (
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
  )
}
