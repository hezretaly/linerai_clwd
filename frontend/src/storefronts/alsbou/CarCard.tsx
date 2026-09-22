import { useState } from 'react'
import clsx from 'clsx'

import { money } from '../../lib/format'
import { CarPhoto } from '../../components/CarPhoto'
import type { Car } from '../_shared/types'

/** How many columns the card's specification grid gets, by how many cells it
 *  has to put in it. Written out because Tailwind reads the source rather than
 *  the running value, so `grid-cols-${n}` is a class that never gets built. */
const COLS: Record<number, string> = { 1: 'grid-cols-1', 2: 'grid-cols-2', 3: 'grid-cols-3' }

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
 * here subtracts one from the other: which fees apply varies per car, and five
 * of Alsbou's 91 are electric and pay no smog fee, so a fixed schedule
 * subtracted from a total would be wrong on exactly the five nobody checks.
 * Their own capture states it: 84 cars carry a $158 gap and those five a $100
 * one. The hybrids are not among them -- they pay the smog fee like a petrol
 * car, which is exactly the kind of thing a rule of thumb gets wrong.
 *
 * **Where their three CTAs were, there is one.** "I WANT THIS CAR", "GET
 * PRE-APPROVED" and a Capital One pre-qualification widget are all forms, and
 * a form that posts nowhere is what this codebase will not build. The
 * assistant stands in their place and captures the same fields.
 */
export function CarCard({
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
