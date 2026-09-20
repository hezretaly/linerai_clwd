import { CarPhoto } from '../../components/CarPhoto'
import { money } from '../../lib/format'
import { useAssistant } from '../_shared/assistant'
import type { Car } from '../_shared/types'

/** The default design's card: picture, title, price, the specifications the
 *  export stated, and one button that opens the assistant about this car.
 *  Where a dealer's site has "I want this car" and a pre-approval widget --
 *  forms that would post nowhere here -- the assistant stands in. */
export function Card({ car }: { car: Car }) {
  const assistant = useAssistant()
  return (
    <article className="flex min-w-0 flex-col overflow-hidden rounded-xl border border-border bg-card">
      <div className="aspect-[4/3] w-full overflow-hidden bg-muted">
        <CarPhoto vin={car.vin} photoUrl={car.photo_url} alt={car.title} className="h-full w-full object-cover" />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1 p-4">
        <h3 className="truncate text-sm font-semibold">{car.title}</h3>
        {car.trim && <p className="truncate text-xs text-muted-foreground">{car.trim}</p>}
        {car.price ? (
          <p className="mt-1 text-lg font-semibold text-primary">{money(car.price)}</p>
        ) : car.inquiry_url ? (
          <a href={car.inquiry_url} target="_blank" rel="noreferrer" className="mt-1 text-lg font-semibold text-primary hover:underline">
            Call for price
          </a>
        ) : (
          <p className="mt-1 text-lg font-semibold text-primary">Call for price</p>
        )}
        {car.specs.length > 0 && (
          <p className="truncate text-xs text-muted-foreground">{car.specs.map((s) => s.value).join(' · ')}</p>
        )}
        {car.location && <p className="truncate text-xs text-muted-foreground">At our {car.location} lot</p>}
        <button
          onClick={() => assistant.askAbout(car)}
          className="mt-auto rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"
        >
          Ask about this one
        </button>
      </div>
    </article>
  )
}
