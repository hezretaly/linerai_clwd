import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { showroomPath } from '../_shared/filters'
import { useStorefront } from '../_shared/useStorefront'
import { Card } from './Card'
import { Frame } from './Frame'

/** The default front page: a heading, the dealership's own welcome copy if
 *  the profile carries any, the newest of the lot, and a way to the list. */
export function Landing() {
  const navigate = useNavigate()
  const [draft, setDraft] = useState('')
  const { data } = useStorefront('limit=6&sort=year_new')
  const shop = data?.dealership
  const site = shop?.site

  return (
    <Frame
      shop={shop}
      voice={Boolean(data?.channels.voice)}
      search={{ draft, setDraft, submit: () => navigate(showroomPath({ q: draft.trim() })) }}
    >
      <section className="border-b border-border bg-muted/30">
        <div className="mx-auto max-w-6xl px-5 py-12">
          <h1 className="text-2xl font-semibold sm:text-3xl">
            {site?.heading || (shop?.name ? `Welcome to ${shop.name}` : ' ')}
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            {data ? `${data.total} vehicle${data.total === 1 ? '' : 's'} on the lot right now.` : ' '}
          </p>
          {(site?.welcome?.length ?? 0) > 0 && (
            <div className="mt-4 max-w-2xl space-y-3 text-sm leading-relaxed text-muted-foreground">
              {site!.welcome.map((p) => (
                <p key={p.slice(0, 40)}>{p}</p>
              ))}
            </div>
          )}
        </div>
      </section>

      {data && data.vehicles.length > 0 && (
        <section className="mx-auto max-w-6xl px-5 py-10">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="text-xl font-semibold">Just arrived</h2>
            <button onClick={() => navigate('/showroom')} className="text-sm font-medium text-primary hover:underline">
              See all {data.total} vehicle{data.total === 1 ? '' : 's'}
            </button>
          </div>
          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {data.vehicles.map((car) => (
              <Card key={car.vin} car={car} />
            ))}
          </div>
        </section>
      )}
    </Frame>
  )
}
