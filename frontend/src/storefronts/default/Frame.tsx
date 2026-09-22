import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import clsx from 'clsx'

import { Icon } from '../../components/Icon'
import { possessive } from '../../lib/dealership'
import { withStore } from '../../lib/store'
import { AssistantContext, ChatFrame, askAboutText, type Assistant } from '../_shared/assistant'
import { arrivedFromChat, rebuiltHere } from '../_shared/links'
import { telHref, type Dealership } from '../_shared/types'

/**
 * The default storefront's frame: a plain header, a plain footer, the
 * assistant in the corner. What a dealership gets until somebody builds
 * their design in a folder of their own.
 *
 * Plain on purpose. It carries the dealership's name, logo, accent, address,
 * phone and hours -- the facts every dealership has -- and nothing that
 * pretends to be their layout. A clean page that is honestly ours is a better
 * placeholder than a near-miss of somebody's homepage, and the day theirs is
 * built this is not what they see.
 */
export function Frame({
  shop,
  voice,
  search,
  children,
}: {
  shop: Dealership | undefined
  voice: boolean
  search: { draft: string; setDraft: (v: string) => void; submit: () => void }
  children: ReactNode
}) {
  // Open from the first frame when the buyer got here by pressing a car in
  // the chat: the conversation carries on on this page, and a widget they
  // have to find and reopen reads as the conversation having ended.
  const [open, setOpen] = useState(arrivedFromChat)
  // The frame is mounted on the first open and kept through closes: a
  // reopen used to reload its document and rebuild the thread every time.
  // Never from first paint -- that would start a conversation for every
  // visitor who never clicks.
  const [everOpened, setEverOpened] = useState(false)
  useEffect(() => {
    if (open) setEverOpened(true)
  }, [open])
  const [ask, setAsk] = useState('')
  const [logoBroke, setLogoBroke] = useState(false)
  const tel = telHref(shop?.phone)
  const links = shop?.site?.links ?? []

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
      <div className={clsx('theme-buyer min-h-full bg-background text-foreground', shop?.brand?.surface === 'dark' && 'dark')}>
        <header className="border-b border-border">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-5 py-4">
            <a href={withStore('/')} className="block min-w-0">
              {shop?.brand?.logo_url && !logoBroke ? (
                <img
                  src={shop.brand.logo_url}
                  alt={shop.name}
                  onError={() => setLogoBroke(true)}
                  className="h-10 w-auto max-w-[220px] object-contain"
                />
              ) : (
                <span className="truncate text-lg font-semibold">{shop?.name || ' '}</span>
              )}
            </a>
            <nav className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-2 text-sm font-medium">
              <a href={withStore('/showroom')} className="hover:text-primary">
                Inventory
              </a>
              {/* Their links, minus the two pages this storefront already is:
                  a profile's "Home" and "Inventory" would sit beside the
                  ones above pointing at the same place. */}
              {links
                .filter((l) => !rebuiltHere(l.href, shop?.website_url))
                .map((l) => (
                  <a key={l.href} href={l.href} rel="noreferrer" target="_blank" className="hover:text-primary">
                    {l.label}
                  </a>
                ))}
              {tel && (
                <a href={`tel:${tel}`} className="flex items-center gap-1.5 hover:text-primary">
                  <Icon name="phone" className="h-4 w-4 text-primary" />
                  {shop!.phone}
                </a>
              )}
            </nav>
          </div>
          <div className="mx-auto max-w-6xl px-5 pb-4">
            <form className="flex max-w-xl gap-2" onSubmit={submit}>
              <input
                value={search.draft}
                onChange={(e) => search.setDraft(e.target.value)}
                placeholder="Search the inventory"
                aria-label="Search the inventory"
                className="min-w-0 flex-1 rounded-lg border border-input bg-card px-3 py-2 text-sm outline-none focus:border-ring"
              />
              <button className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">Search</button>
            </form>
          </div>
        </header>

        {children}

        <footer className="border-t border-border">
          <div className="mx-auto grid max-w-6xl grid-cols-1 gap-8 px-5 py-10 sm:grid-cols-3">
            <div className="min-w-0">
              <h2 className="text-sm font-semibold">{shop?.name || ' '}</h2>
              <p className="mt-2 text-sm text-muted-foreground">{shop?.address}</p>
              {tel && (
                <a href={`tel:${tel}`} className="mt-1 block text-sm font-medium text-primary">
                  {shop!.phone}
                </a>
              )}
            </div>
            <div className="min-w-0">
              <h2 className="text-sm font-semibold">Hours</h2>
              <table className="mt-2 text-sm text-muted-foreground">
                <tbody>
                  {shop &&
                    Object.entries(shop.hours ?? {}).map(([day, h]) => (
                      <tr key={day}>
                        <td className="pr-4 font-medium capitalize text-foreground">{day.slice(0, 3)}</td>
                        <td className="tnum whitespace-nowrap">{h ? `${h.open} - ${h.close}` : 'Closed'}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
            <div className="min-w-0">
              <h2 className="text-sm font-semibold">Contact us</h2>
              <p className="mt-2 text-sm text-muted-foreground">Ask about any car on the lot and book a time to come in.</p>
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
        </footer>

        <div className="fixed bottom-4 right-4 z-40 flex flex-col items-end gap-3">
          <div
            className={clsx(
              // A sheet on a phone and a corner panel on a laptop -- see the
              // same block in `storefronts/alsbou/Shell.tsx`. The two are
              // deliberately separate files: a storefront designs its own
              // bubble, panel and placement, and the next dealership's may be
              // a bar across the bottom or a tab down the side. Only the
              // frame inside it is not theirs to redesign.
              'fixed inset-x-3 bottom-20 top-3 flex flex-col overflow-hidden',
              'sm:static sm:inset-auto sm:h-[min(38rem,calc(100dvh-7rem))] sm:w-[26rem]',
              'rounded-2xl border border-border bg-card shadow-2xl',
              'origin-bottom-right transition-all duration-200 ease-out motion-reduce:transition-none',
              open
                ? 'visible translate-y-0 scale-100 opacity-100'
                : 'invisible translate-y-3 scale-95 opacity-0',
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
            {everOpened && <ChatFrame ask={ask} className="min-h-0 flex-1 border-0" />}
          </div>
          <button
            onClick={() => setOpen(!open)}
            className={clsx(
              'flex h-14 w-14 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg',
              'transition-transform duration-150 hover:scale-105 active:scale-95 motion-reduce:transition-none',
            )}
            aria-label={open ? 'Close chat' : 'Chat with us'}
            aria-expanded={open}
          >
            {open ? <span className="text-2xl leading-none">&times;</span> : <Icon name="chat" className="h-6 w-6" />}
          </button>
        </div>
      </div>
    </AssistantContext.Provider>
  )
}
