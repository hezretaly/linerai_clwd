import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
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
 * **It is not a copy of their site and does not pretend to be one.** Their
 * marketing pages are theirs; reproducing them would mean guessing at markup
 * nothing here can fetch, and a near-miss of somebody's own homepage looks
 * worse than a clean page that is honestly ours. What it does reproduce is the
 * only thing being demonstrated: their inventory, and how a buyer reaches
 * them through it.
 *
 * **Everything on it is a row.** The cars come from `/api/showroom`, which
 * narrows through the same `offerable` predicate `search_inventory` uses -- so
 * a car the assistant refuses to discuss cannot be sitting on the page beside
 * the chat window refusing to discuss it. With no cars imported yet the page
 * says so rather than showing an empty grid that reads as a broken build.
 *
 * **The widget is an iframe of the real `/chat`.** Not a second chat client:
 * one round of duplicated transcript logic is how the widget starts dropping
 * the booking card that the full page still renders. Same origin, same
 * conversation, same `localStorage` id -- a buyer who opens the widget and
 * then the full page is the same buyer in the same thread.
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

interface ShowroomPayload {
  dealership: Dealership
  greeting: string
  vehicles: Car[]
  total: number
  offset: number
  channels: { chat: boolean; voice: boolean }
}

const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']

/** "Mon-Fri 9:00-18:00" style, collapsing runs of identical days.
 *
 *  Written out day by day it is seven lines of near-identical text in a footer
 *  nobody reads; collapsed, it is the one line a buyer is actually looking for.
 *  Closed days are named rather than omitted -- "Sunday closed" is the answer
 *  to the question, and a missing Sunday is not. */
function openingHours(hours: Dealership['hours']): string[] {
  const rows = DAYS.map((day) => ({
    day,
    text: hours?.[day] ? `${hours[day]!.open}-${hours[day]!.close}` : 'Closed',
  }))
  const out: string[] = []
  let run = [rows[0]]
  const label = (d: string) => d.slice(0, 3).replace(/^./, (c) => c.toUpperCase())
  const flush = () => {
    const span = run.length === 1 ? label(run[0].day) : `${label(run[0].day)}-${label(run[run.length - 1].day)}`
    out.push(`${span}  ${run[0].text}`)
  }
  for (const row of rows.slice(1)) {
    if (row.text === run[run.length - 1].text) run.push(row)
    else {
      flush()
      run = [row]
    }
  }
  flush()
  return out
}

function CarCard({ car }: { car: Car }) {
  return (
    <article className="flex min-w-0 flex-col overflow-hidden rounded-xl border border-border bg-card">
      <div className="aspect-[4/3] w-full overflow-hidden bg-muted">
        {/* The dealer's own CDN, in the ordinary case. A stored copy wins when
            SCRAPER_SAVE_PHOTOS put one on disk, and a drawn placeholder is
            last -- see ingest/pipeline.py:_photo_for. */}
        <img
          src={car.photo_url}
          alt={car.title}
          loading="lazy"
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

  const { data, isLoading } = useQuery({
    queryKey: ['showroom', shown],
    queryFn: () => api.get<ShowroomPayload>(`/api/showroom?limit=${shown}`),
  })

  useEffect(() => {
    applyBrand(data?.dealership.brand)
  }, [data])

  const shop = data?.dealership
  const logo = shop?.brand?.logo_url

  return (
    <div className="theme-buyer min-h-full bg-background">
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-5 py-4">
          <div className="flex min-w-0 items-center gap-3">
            {logo ? (
              <img src={logo} alt={shop?.name} className="h-9 w-auto max-w-[180px] object-contain" />
            ) : (
              <span className="truncate text-lg font-semibold">{shop?.name || ' '}</span>
            )}
          </div>
          <div className="flex min-w-0 items-center gap-4 text-sm">
            {shop?.phone && (
              <a href={`tel:${shop.phone.replace(/[^\d+]/g, '')}`} className="whitespace-nowrap font-medium">
                {shop.phone}
              </a>
            )}
            {shop?.website_url && (
              <a
                href={shop.website_url}
                className="hidden text-muted-foreground hover:underline sm:inline"
                rel="noreferrer"
              >
                Main site
              </a>
            )}
          </div>
        </div>
      </header>

      <section className="border-b border-border bg-muted/40">
        <div className="mx-auto max-w-6xl px-5 py-10">
          <h1 className="text-2xl font-semibold sm:text-3xl">
            {shop?.name ? `Find your next car at ${shop.name}` : ' '}
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            {data ? `${data.total} vehicle${data.total === 1 ? '' : 's'} on the lot right now.` : ' '}{' '}
            Ask {shop?.name ? `${possessive(shop.name)} assistant` : 'our assistant'} anything --
            it searches this inventory and can book you in.
          </p>
          <div className="mt-5 flex flex-wrap gap-3">
            <button
              onClick={() => setOpen(true)}
              className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
            >
              Chat with us
            </button>
            {/* Counted, never declared: with no VOICE_PROVIDER there is no
                phone to answer, and a button that opens a page saying so is
                worse than no button. */}
            {data?.channels.voice && (
              <a
                href="/call"
                className="rounded-lg border border-border bg-card px-4 py-2 text-sm font-medium"
              >
                Call us
              </a>
            )}
          </div>
        </div>
      </section>

      <main className="mx-auto max-w-6xl px-5 py-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Inventory
        </h2>

        {isLoading && <p className="mt-6 text-sm text-muted-foreground">Loading the lot...</p>}

        {data && data.vehicles.length === 0 && (
          // Not an empty grid: a lot with nothing on it is almost always an
          // import that has not been run yet, and saying which is the
          // difference between a setup step and a page that looks broken.
          <div className="mt-6 rounded-xl border border-dashed border-border p-8 text-center">
            <p className="text-sm font-medium">No vehicles have been imported yet.</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Run an import from the dashboard and they appear here. The assistant will not
              offer a car it cannot find, so it says it will check rather than inventing one.
            </p>
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

      <footer className="border-t border-border bg-card">
        <div className="mx-auto grid max-w-6xl grid-cols-1 gap-6 px-5 py-8 text-sm sm:grid-cols-2">
          <div className="min-w-0">
            <p className="font-semibold">{shop?.name || ' '}</p>
            {shop?.address && <p className="mt-1 text-muted-foreground">{shop.address}</p>}
            {shop?.phone && <p className="text-muted-foreground">{shop.phone}</p>}
          </div>
          <div className="min-w-0">
            <p className="font-semibold">Opening hours</p>
            <ul className="mt-1 space-y-0.5 text-muted-foreground">
              {shop && openingHours(shop.hours).map((line) => (
                <li key={line} className="tnum whitespace-pre">
                  {line}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </footer>

      {/* The widget. An iframe of the real /chat rather than a second client:
          same origin, same conversation id in localStorage, so a buyer who
          opens this and then the full page is one buyer in one thread. */}
      <div className="fixed bottom-4 right-4 z-40 flex flex-col items-end gap-3">
        <div
          className={clsx(
            'flex w-[min(24rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-xl',
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
            <iframe
              src="/chat?embed=1"
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
          {open ? <span className="text-2xl leading-none">&times;</span> : <Icon name="chat" className="h-6 w-6" />}
        </button>
      </div>
    </div>
  )
}
