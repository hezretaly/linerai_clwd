import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { CarPhoto } from '../../components/CarPhoto'
import { money } from '../../lib/format'
import { useAssistant } from '../_shared/assistant'
import { showroomPath } from '../_shared/filters'
import { useStorefrontCar } from '../_shared/useStorefront'
import type { Car, CarDetail } from '../_shared/types'
import { Card } from './Card'
import { Frame } from './Frame'

/** The default car page: the photograph and the price, one way to ask about
 *  it, the tables the export stated, the options list and three others like
 *  it. Plain and ours, the same stance as the rest of `default/` -- a guess at
 *  a dealership's own car page looks worse than an honest plain one. */
export function Vehicle() {
  const { vin = '' } = useParams()
  const navigate = useNavigate()
  const [draft, setDraft] = useState('')
  const { data } = useStorefrontCar(vin)

  return (
    <Frame
      shop={data?.dealership}
      voice={Boolean(data?.channels.voice)}
      search={{ draft, setDraft, submit: () => navigate(showroomPath({ q: draft.trim() })) }}
    >
      <main className="mx-auto max-w-6xl px-5 py-8">
        {data ? (
          <Body car={data.vehicle} similar={data.similar} />
        ) : (
          <p className="py-24 text-center text-sm text-muted-foreground">Loading the car</p>
        )}
      </main>
    </Frame>
  )
}

function Body({ car, similar }: { car: CarDetail; similar: Car[] }) {
  const assistant = useAssistant()
  return (
    <>
      <Link to="/showroom" className="text-sm font-medium text-primary hover:underline">
        ← All vehicles
      </Link>
      <div className="mt-4 grid grid-cols-1 gap-6 md:grid-cols-2">
        <div className="aspect-[4/3] min-w-0 overflow-hidden rounded-xl bg-muted">
          <CarPhoto vin={car.vin} photoUrl={car.photo_url} alt={car.title} className="h-full w-full object-cover" />
        </div>
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold">{car.title}</h1>
          {car.trim && <p className="text-muted-foreground">{car.trim}</p>}
          <p className="mt-3 text-3xl font-semibold text-primary">{car.price ? money(car.price) : 'Call for price'}</p>
          {car.mileage != null && <p className="mt-1 text-sm">{car.mileage.toLocaleString()} miles</p>}
          <button
            onClick={() => assistant.askAbout(car)}
            className="mt-5 w-full rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground sm:w-auto"
          >
            Ask about this one
          </button>
          {car.history_url && (
            <a href={car.history_url} target="_blank" rel="noreferrer" className="mt-3 block text-sm underline underline-offset-2">
              Vehicle history report
            </a>
          )}
        </div>
      </div>

      {car.sections.map((section) => (
        <section key={section.title} className="mt-8">
          <h2 className="text-lg font-semibold">{section.title}</h2>
          <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
            {section.rows.map((row) => (
              <div key={row.label} className="flex min-w-0 justify-between gap-3 border-b border-border py-1.5">
                <dt className="text-muted-foreground">{row.label}</dt>
                <dd className="min-w-0 break-words text-right font-medium">{row.value}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}

      {car.features.length > 0 && (
        <section className="mt-8">
          <h2 className="text-lg font-semibold">Options and equipment</h2>
          <ul className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
            {car.features.map((feature, i) => (
              <li key={`${feature}-${i}`} className="min-w-0 break-words">• {feature}</li>
            ))}
          </ul>
        </section>
      )}

      {similar.length > 0 && (
        <section className="mt-10">
          <h2 className="text-lg font-semibold">Similar vehicles</h2>
          <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {similar.map((other) => (
              <Card key={other.vin} car={other} />
            ))}
          </div>
        </section>
      )}
    </>
  )
}
