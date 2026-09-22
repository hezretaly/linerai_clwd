import { withStore } from '../../lib/store'

/**
 * The two pages of theirs this demo has rebuilt, by URL, or '' for one it
 * has not.
 *
 * A dealership's nav links point at their real site, and two of those pages
 * -- their home and their inventory -- are the two pages this storefront *is*.
 * A prospect walking their nav should land on ours for those and leave for
 * everything else (Financing, Reviews, a page we have not built), so every
 * design decides it the same way and none decides it by a hostname written
 * into a component: against the dealership's own `website_url`. A sister store
 * on another host -- Alsbou's Riverside lot -- is exactly the link that must
 * keep leaving.
 */
export function rebuiltHere(href: string, site: string | undefined): string {
  try {
    if (!site) return ''
    const theirs = new URL(site)
    const link = new URL(href)
    if (link.host !== theirs.host) return ''
    const path = link.pathname.replace(/\/+$/, '')
    if (path === '') return withStore('/')
    if (path === '/inventory') return withStore('/showroom')
    return ''
  } catch {
    return ''
  }
}

/** A car's own page on this storefront. The VIN is the address: unique,
 *  stable across a re-import, and already in every sentence the chat writes
 *  about a car. A router path, relative to the store's basename. */
export function carPath(vin: string): string {
  return `/showroom/${encodeURIComponent(vin)}`
}

/** The query flag that says the buyer arrived from the chat, so the page opens
 *  the widget on the same conversation rather than making them find it again.
 *  One name for the link that writes it and the shells that read it. */
export const FROM_CHAT = 'chat'

/** Did this page load because somebody pressed a car in the chat? */
export function arrivedFromChat(): boolean {
  try {
    return new URLSearchParams(window.location.search).get(FROM_CHAT) === '1'
  } catch {
    return false
  }
}
