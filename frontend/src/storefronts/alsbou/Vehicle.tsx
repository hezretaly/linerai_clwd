import { useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import clsx from 'clsx'

import { CarPhoto } from '../../components/CarPhoto'
import { money } from '../../lib/format'
import { CarCard, PricingDetails } from './CarCard'
import { StorefrontShell, useAssistant } from './Shell'
import { showroomPath } from '../_shared/filters'
import { useStorefrontCar } from '../_shared/useStorefront'
import type { Car, CarDetail, Dealership } from '../_shared/types'

/**
 * One car's own page: `/alsbou/showroom/<vin>`, laid out the way their car
 * page is laid out.
 *
 * **Their shape, from their capture.** A breadcrumb, the photograph on the
 * left with a sticky card on the right -- title, trim, miles, the price under
 * their own label, the pricing disclosure, the call to action, then how to
 * reach them -- and under the photograph their sections in their order:
 * buying tools, basics, performance, specifications, similar vehicles. Below
 * `lg` the card drops under the photograph, which is where their phone layout
 * puts it.
 *
 * **What is left out, and why.** Their DESCRIPTION is marketing copy that no
 * export carries -- and on this very car it quotes $19,999 against an
 * advertised $18,608, so reproducing it would put two prices on one page.
 * Their four buttons (I WANT THIS CAR, GET ME PRE-APPROVED, NO SSN PRE-QUAL,
 * CHECK AVAILABILITY) are forms or a credit decision, and a form that posts
 * nowhere is what this codebase will not build; the assistant stands in their
 * place, the way it does on the card. Their photo carousel is one picture
 * here, because one is what a list crawl sees.
 *
 * **A buyer who got here from the chat is still in the chat.** The shell
 * opens the widget on arrival when the link says so, and the widget is the
 * same `/chat` with the same conversation id, so the thread they were in is
 * the thread they see.
 */

/** Their side card's sentence under the price, as their page prints it. It
 *  is Alsbou's own words and lives in Alsbou's folder, which is where a
 *  dealership's sentences belong. */
const SIDE_NOTE = 'Premium and Luxury Vehicles. Unbeatable Value. Orange County’s Choice.'

/** Their "Drive with confidence!" band, in their words. */
const CONFIDENCE = {
  title: 'Drive with confidence!',
  lines: [
    'Hand-Selected Luxury, Rigorously Inspected, and Ready for the Road. Discover the Alsbou Difference!',
    'Browse Our Quality Selection.',
  ],
}

export function Vehicle() {
  const { vin = '' } = useParams()
  const navigate = useNavigate()
  const [draft, setDraft] = useState('')
  const { data } = useStorefrontCar(vin)
  const shop = data?.dealership
  const car = data?.vehicle

  return (
    <StorefrontShell
      shop={shop}
      voice={Boolean(data?.channels.voice)}
      search={{
        draft,
        setDraft,
        submit: () => navigate(showroomPath({ q: draft.trim() })),
      }}
    >
      <main className="mx-auto max-w-7xl px-5 py-6">
        {!car ? (
          <p className="py-24 text-center text-sm text-muted-foreground">Loading the car</p>
        ) : (
          <Page car={car} shop={shop} similar={data!.similar} />
        )}
      </main>
    </StorefrontShell>
  )
}

function Page({
  car,
  shop,
  similar,
}: {
  car: CarDetail
  shop: Dealership | undefined
  similar: Car[]
}) {
  const assistant = useAssistant()
  const site = shop?.site
  const priceLabel = site?.price_label ?? ''
  const priceNote = site?.price_note ?? ''
  const pricing = Boolean(car.price && (car.advertised_price || priceNote))

  return (
    <>
      {/* Their breadcrumb: home, used cars, the make, the model, the car. The
          make and model narrow the list the same way the sidebar does. */}
      <nav aria-label="Breadcrumb" className="mb-4 min-w-0 truncate text-xs uppercase text-muted-foreground">
        <Link to="/" className="hover:underline">Home</Link>
        <span className="mx-1.5">/</span>
        <Link to="/showroom" className="hover:underline">Used cars</Link>
        {car.make && (
          <>
            <span className="mx-1.5">/</span>
            <Link to={showroomPath({ make: car.make })} className="hover:underline">{car.make}</Link>
          </>
        )}
        <span className="mx-1.5">/</span>
        <span className="text-foreground">{car.title}</span>
      </nav>
      <h1 className="sr-only">{car.title}</h1>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="min-w-0 overflow-hidden rounded-xl bg-muted lg:col-start-1">
          <div className="aspect-[4/3] w-full">
            <CarPhoto vin={car.vin} photoUrl={car.photo_url} alt={car.title} className="h-full w-full object-cover" />
          </div>
        </div>

        {/* The side card. A column that stays in view on a laptop; under the
            photograph on a phone, which is where their own layout puts it. */}
        <aside className="min-w-0 lg:col-start-2 lg:row-span-2 lg:row-start-1">
          <div className="rounded-xl border border-border bg-card py-4 shadow-sm lg:sticky lg:top-4">
            <p className="px-3 text-center text-lg font-bold uppercase">{car.title}</p>
            {car.trim && <h2 className="px-3 text-center text-base font-semibold">{car.trim}</h2>}
            {car.mileage != null && (
              <p className="px-3 text-center text-sm">{car.mileage.toLocaleString()} miles</p>
            )}
            <hr className="my-3 border-border" />
            <div className="px-5">
              <div className="flex items-center justify-between gap-3">
                {priceLabel && car.price ? (
                  <span className="text-xs font-bold uppercase leading-tight">
                    {priceLabel.split(' ').map((word) => (
                      <span key={word} className="block">{word}</span>
                    ))}
                  </span>
                ) : <span />}
                <span className="text-2xl font-semibold text-primary">
                  {car.price ? money(car.price) : 'Call for price'}
                </span>
              </div>
              {pricing && (
                <div className="mt-3 border-t border-border pt-2 text-right text-xs text-muted-foreground">
                  <PricingDetails car={car} priceLabel={priceLabel} priceNote={priceNote} />
                </div>
              )}
              <p className="mt-3 text-center text-sm font-semibold">{SIDE_NOTE}</p>
              <hr className="my-3 border-border" />
              <button
                onClick={() => assistant.askAbout(car)}
                className="w-full rounded-lg bg-primary px-3 py-2.5 text-sm font-semibold uppercase tracking-wide text-primary-foreground"
              >
                I want this car
              </button>
              <button
                onClick={assistant.open}
                className="mt-2 w-full rounded-lg border border-primary px-3 py-2.5 text-sm font-semibold uppercase tracking-wide text-primary"
              >
                Ask a question
              </button>
            </div>
            <hr className="my-3 border-border" />
            <div className="space-y-1 px-3 text-center text-sm">
              {shop?.phone && (
                <p>
                  Call or text{' '}
                  <a href={`tel:${shop.phone.replace(/[^\d+]/g, '')}`} className="font-bold hover:underline">
                    {shop.phone}
                  </a>
                </p>
              )}
              {shop?.name && <p className="uppercase text-muted-foreground">{shop.name}</p>}
              {shop?.address && <p className="uppercase text-muted-foreground">{shop.address}</p>}
            </div>
          </div>
        </aside>

        <div className="min-w-0 space-y-10 lg:col-start-1">
          <section className="rounded-xl border border-border bg-card p-5 shadow-sm">
            <h3 className="text-sm font-bold uppercase text-primary">{CONFIDENCE.title}</h3>
            {CONFIDENCE.lines.map((line) => (
              <p key={line} className="mt-2 text-sm">{line}</p>
            ))}
          </section>

          {/* Their buying tools are a Carfax badge and two third-party
              widgets. The badge is the dealer's own history link here, and
              nothing more: a badge reading "no accidents" would be us making
              the claim. */}
          {car.history_url && (
            <Section title="Buying tools">
              <div className="rounded-xl border border-border bg-card p-5 text-center shadow-sm">
                <a
                  href={car.history_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-block rounded-lg border border-border px-4 py-2 text-sm font-semibold hover:bg-muted"
                >
                  Vehicle history report
                </a>
              </div>
            </Section>
          )}

          {car.sections.map((section) => (
            <Section key={section.title} title={section.title}>
              <Table rows={section.rows} />
            </Section>
          ))}

          {car.features.length > 0 && (
            <Section title="Specifications">
              <ul className="overflow-hidden rounded-xl border border-border shadow-sm">
                {car.features.map((feature, i) => (
                  <li
                    key={`${feature}-${i}`}
                    className={clsx(
                      'flex min-w-0 items-center justify-between gap-3 px-4 py-2 text-sm font-semibold uppercase',
                      i % 2 === 0 ? 'bg-muted' : 'bg-card',
                    )}
                  >
                    <span className="min-w-0 break-words">{feature}</span>
                    <span aria-hidden className="shrink-0 font-bold">✓</span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {similar.length > 0 && (
            <Section title="Similar vehicles">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {similar.map((other) => (
                  <CarCard
                    key={other.vin}
                    car={other}
                    priceLabel={priceLabel}
                    priceNote={priceNote}
                    onAsk={assistant.askAbout}
                  />
                ))}
              </div>
            </Section>
          )}
        </div>
      </div>
    </>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="min-w-0">
      <h3 className="mb-3 text-lg font-bold uppercase">{title}</h3>
      {children}
    </section>
  )
}

/** Their two-column table, striped. A `<table>` never reflows, so the value
 *  column wraps rather than widening the page on a phone. */
function Table({ rows }: { rows: { label: string; value: string }[] }) {
  return (
    <div className="overflow-hidden rounded-xl border border-border shadow-sm">
      <table className="w-full table-fixed text-sm font-semibold">
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.label} className={i % 2 === 0 ? 'bg-muted' : 'bg-card'}>
              <td className="w-1/2 px-4 py-2">{row.label}:</td>
              <td className="w-1/2 break-words px-4 py-2">{row.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

