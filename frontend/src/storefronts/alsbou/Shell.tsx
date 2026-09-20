import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import clsx from 'clsx'

import { Icon } from '../../components/Icon'
import { possessive } from '../../lib/dealership'
import { withStore } from '../../lib/store'
import { rebuiltHere } from '../_shared/links'
import { AssistantContext, ChatFrame, askAboutText, useAssistant, type Assistant } from '../_shared/assistant'

/** Re-exported so this folder's pages import it from their own shell. */
export { useAssistant }
import {
  chromeClass,
  telHref,
  SEARCH_PLACEHOLDER,
  type Dealership,
  type Site,
} from '../_shared/types'

/**
 * Everything a storefront page has around its content: the dealership's
 * chrome at the top, their footer at the bottom, and the assistant in the
 * corner. The front page and the inventory list both sit inside this.
 *
 * **The chrome is two bars, which is what a dealer's header is.** A black
 * identity bar carrying the logo, the address, the number and their search
 * box, then a separate flat nav strip in a lighter grey. It was one row --
 * logo left, nav right -- and one row is the single thing that made the page
 * read as "a product page with their colours on it" rather than as their
 * site.
 *
 * **The chrome follows their site; the page between it does not.** A great
 * many dealers run a black header and footer over a white body, which is
 * neither of the two things `brand.surface` could say. `brand.chrome` is that
 * third answer, and like `surface` it is two words rather than two colours:
 * it picks the dark palette already in the token layer instead of carrying a
 * hex into a stylesheet, so a prospect's file cannot restyle the product into
 * something unreadable.
 *
 * **The widget is an iframe of the real `/chat`.** Not a second chat client:
 * one round of duplicated transcript logic is how the widget starts dropping
 * the booking card the full page still renders. Same origin, same
 * conversation id in localStorage, so the widget and the full page are one
 * thread. The question a card opens it with is part of the iframe's URL
 * rather than a message posted into it, because a second channel into the
 * frame is a second way for the storefront to write into a buyer's
 * transcript.
 */


export function StorefrontShell({
  shop,
  voice,
  search,
  children,
}: {
  shop: Dealership | undefined
  /** Whether a Call button is drawn at all. Counted, never declared: with no
   *  `VOICE_PROVIDER` there is no phone to answer, and a button opening a page
   *  that says so is worse than no button. */
  voice: boolean
  /** The keyword box's current text, owned by the page: the inventory list
   *  narrows on it in place, the front page carries it to the list. */
  search: { draft: string; setDraft: (v: string) => void; submit: () => void }
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [ask, setAsk] = useState('')
  const site: Site | undefined = shop?.site
  const chrome = chromeClass(shop)
  const tel = telHref(shop?.phone)

  const assistant: Assistant = {
    open: () => {
      setAsk('')
      setOpen(true)
    },
    askAbout: (car) => {
      setAsk(askAboutText(car))
      setOpen(true)
    },
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    search.submit()
  }

  return (
    <AssistantContext.Provider value={assistant}>
      {/* `.dark` is the classic dark palette that has been in the token layer
          since the beginning; `.theme-buyer` sits after it in the file and so
          still wins for --primary, which is where their accent lands. Scoped
          to this page: the dealership's storefront follows their site, and
          their reps' dashboard does not.

          `text-foreground` is not decoration. Without it every heading
          inherits whatever colour the document body has, which in light mode
          happens to be right and in dark mode is black on black. A surface
          that sets a background must set a foreground. */}
      <div
        className={clsx(
          'theme-buyer min-h-full bg-background text-foreground',
          shop?.brand?.surface === 'dark' && 'dark',
        )}
      >
        <Chrome shop={shop} site={site} chrome={chrome} tel={tel} search={search} onSubmit={submit} />
        {children}
        <Footer shop={shop} site={site} chrome={chrome} tel={tel} voice={voice} />
        <Widget shop={shop} open={open} ask={ask} setOpen={setOpen} />
      </div>
    </AssistantContext.Provider>
  )
}

function Chrome({
  shop,
  site,
  chrome,
  tel,
  search,
  onSubmit,
}: {
  shop: Dealership | undefined
  site: Site | undefined
  chrome: string
  tel: string
  search: { draft: string; setDraft: (v: string) => void }
  onSubmit: (e: FormEvent) => void
}) {
  /* Their logo lives on their own CDN, and a broken <img> in the middle of a
   * demo is worse than not showing one: the alt text renders as a torn-page
   * icon next to the dealership's own name. Falling back to the name is a page
   * that still reads. */
  const [logoBroke, setLogoBroke] = useState(false)
  return (
    <div className={chrome}>
      <div className="bg-background text-foreground">
        <div className="mx-auto flex max-w-7xl flex-col gap-3 px-5 py-3 lg:flex-row lg:items-center lg:gap-8">
          {/* Their logo is the way home, which on a prefixed storefront is the
              store root -- withStore keeps it inside this dealership. */}
          <a href={withStore('/')} className="block shrink-0">
            {shop?.brand?.logo_url && !logoBroke ? (
              <img
                src={shop.brand.logo_url}
                alt={shop.name}
                onError={() => setLogoBroke(true)}
                /* Tall, because a dealer's logo is the largest thing in their
                   header and theirs is drawn at 100px. */
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
                submit, the way theirs is drawn -- a rounded pill here reads as
                our page rather than theirs. */}
            <form className="flex min-w-0" onSubmit={onSubmit}>
              <input
                value={search.draft}
                onChange={(e) => search.setDraft(e.target.value)}
                placeholder={SEARCH_PLACEHOLDER}
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

        {/* The nav strip. `bg-muted` is the dark palette's lighter grey, which
            is the relationship a dealer's nav bar has to the black bar above
            it -- a token rather than their two hex values, for the reason
            `brand.chrome` is two words. Spread edge to edge at `lg` because
            theirs runs the full width of the container.

            Their real pages, on their real site. A nav that 404s inside our
            app mid-demo is worse than one that leaves it, and we are not
            pretending to have rebuilt Financing or Reviews. The one exception
            is their inventory, which we *have* rebuilt: a link whose target is
            their own inventory page is pointed at ours, so a prospect walking
            their nav lands on the page this demo exists to show. */}
        {(site?.links?.length ?? 0) > 0 && (
          <nav className="bg-muted">
            <ul className="mx-auto flex max-w-7xl flex-wrap items-stretch px-5 text-xs font-medium uppercase tracking-wide lg:justify-between lg:px-8">
              {site!.links.map((l) => {
                const ours = rebuiltHere(l.href, shop?.website_url)
                const cls = clsx(
                  'flex h-full items-center px-3 py-3 hover:text-primary',
                  /* The one they emphasise, in the accent, in its own
                     position. Appended to the end as a pill it rendered sixth
                     where their nav has it third. */
                  l.href === site?.cta?.href && 'text-primary',
                )
                return (
                  <li key={l.href} className="min-w-0">
                    {ours ? (
                      <a href={ours} className={cls}>
                        {l.label}
                      </a>
                    ) : (
                      <a href={l.href} rel="noreferrer" target="_blank" className={cls}>
                        {l.label}
                      </a>
                    )}
                  </li>
                )
              })}
            </ul>
          </nav>
        )}
      </div>
    </div>
  )
}

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
 *  A dealer's Friday and Saturday often close earlier than Monday to Thursday,
 *  so a single range would be wrong for two days of the week -- and `Closed`
 *  is named rather than omitted, because "are you open Sunday?" is the
 *  question and a missing row is not an answer. */
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

function Footer({
  shop,
  site,
  chrome,
  tel,
  voice,
}: {
  shop: Dealership | undefined
  site: Site | undefined
  chrome: string
  tel: string
  voice: boolean
}) {
  const assistant = useAssistant()
  const [street] = (shop?.address || '').split(',')
  const cityLine = (shop?.address || '').split(',').slice(1).join(',').trim()
  return (
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

          {/* Their footer's own link groups, minus the two we would be faking.
              Their make shortcuts are answered better by the counted filters
              on the inventory page than by a link that leaves. */}
          {(site?.links?.length ?? 0) > 0 && (
            <div className="min-w-0">
              <h2 className="text-sm font-semibold">More</h2>
              <ul className="mt-2 space-y-1.5 text-sm text-muted-foreground">
                <li>
                  <a href={withStore('/showroom')} className="hover:text-primary">
                    All used cars for sale
                  </a>
                </li>
                {site!.links.map((l) => (
                  <li key={l.href}>
                    <a
                      href={l.href}
                      target="_blank"
                      rel="noreferrer"
                      className={clsx('hover:text-primary', l.href === site?.cta?.href && 'font-medium text-primary')}
                    >
                      {l.label}
                    </a>
                  </li>
                ))}
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
              {voice ? ' By message or by phone.' : ''}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                onClick={assistant.open}
                className="rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground"
              >
                Message us
              </button>
              {voice && (
                <a href={withStore('/call')} className="rounded-lg border border-border px-3 py-1.5 text-sm font-medium">
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
  )
}

function Widget({
  shop,
  open,
  ask,
  setOpen,
}: {
  shop: Dealership | undefined
  open: boolean
  ask: string
  setOpen: (v: boolean) => void
}) {
  // Mounted on the first open and kept. Unmounting on close meant every
  // reopen reloaded the frame's document, re-read the session and rebuilt
  // the thread -- a visible pause each time, for a conversation that had
  // not changed. Not from first paint, though: an iframe that exists before
  // anybody clicks starts a conversation for every visitor who never does.
  const [everOpened, setEverOpened] = useState(false)
  useEffect(() => {
    if (open) setEverOpened(true)
  }, [open])
  return (
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
        {/* Keyed on the question so pressing a second card's button reloads
            the frame with that car's sentence in the box -- the transcript
            comes back from localStorage, so nothing said is lost. */}
        {everOpened && <ChatFrame ask={ask} className="min-h-0 flex-1 border-0" />}
      </div>

      <button
        onClick={() => setOpen(!open)}
        className="flex h-14 w-14 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg"
        aria-label={open ? 'Close chat' : 'Chat with us'}
      >
        {open ? <span className="text-2xl leading-none">&times;</span> : <Icon name="chat" className="h-6 w-6" />}
      </button>
    </div>
  )
}
