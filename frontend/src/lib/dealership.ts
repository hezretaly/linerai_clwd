/**
 * Who this instance is, read from the server rather than typed into a page.
 *
 * Five surfaces printed the literal string "Riverside Auto": the chat header,
 * the call header, the login subtitle and two lines on the buyer page. On a
 * rebranded instance every one of them greeted a prospect's buyer as somebody
 * else's showroom -- and the buyer's very first screen is the one that has to
 * be right, because it is the one they are being shown in the demo.
 *
 * `/api/showroom/dealership` is public, which it has to be: two of the five
 * are surfaces nobody has signed in to. It carries no buyer data and no
 * inventory -- just the identity a dealer already publishes on their own site.
 */

import { useQuery } from '@tanstack/react-query'

import { api } from './api'
import type { Brand } from './brand'

/** Their front page as they wrote it, from the profile's `site:` block.
 *  Every field is optional -- a profile without one renders a plain
 *  storefront carrying the name, address, phone and lot, which is what
 *  Riverside gets and is a perfectly honest page. */
export interface Link {
  label: string
  href: string
}

export interface Site {
  tagline: string
  heading: string
  /** The photograph across the top of their front page. One image: a dealer's
   *  hero is a picture of the forecourt, not a rotation. */
  hero_image: string
  /** Kept for a profile written before `hero_image` was the spelling. The
   *  page reads `hero_image`, which is the first of these. */
  hero_images: string[]
  /** The lead paragraph of their About copy. */
  welcome: string[]
  /** The subheaded blocks that follow it. Separate from `welcome` rather than
   *  a shape it may also take: a list whose items are sometimes strings and
   *  sometimes objects is one every reader has to test before using. */
  sections: { heading: string; body: string }[]
  /** The strip of linked images across their front page, each going somewhere
   *  different. Not hero slides -- five calls to action that only mean
   *  anything next to each other. */
  banners: { image: string; href: string; label: string }[]
  links: Link[]
  /** The one nav item they emphasise -- "Get Pre-Qualified" on Alsbou's. It
   *  is also in `links`, and this only says which one to draw in the accent,
   *  so it keeps its own position in their nav. `null` for a profile that
   *  names none, which is most dealers. */
  cta: Link | null
  /** What they call the number on a card: "Advertised price". Empty is the
   *  common case and the price then simply stands on its own. */
  price_label: string
  /** What that number already includes, in the dealer's own words. Shown in
   *  the card's pricing disclosure beside the two figures the export states;
   *  nothing on the page subtracts one from the other. */
  price_note: string
  social: Link[]
}

export interface Dealership {
  id: string
  name: string
  timezone: string
  hours: Record<string, { open: string; close: string } | null>
  address: string
  phone: string
  website_url: string
  brand: Brand
  site: Site
}

/** `Riverside Auto` -> `Riverside Auto's`; `... Cars` -> `... Cars'`.
 *
 *  Small, and the buyer reads it. "Craig and Landreth Cars's assistant" is the
 *  sort of thing that makes a product look assembled rather than built. */
export function possessive(name: string): string {
  const trimmed = (name || '').trim()
  if (!trimmed) return ''
  return /s$/i.test(trimmed) ? `${trimmed}'` : `${trimmed}'s`
}

/** The dealership, with a name that is `''` rather than undefined while it
 *  loads -- a header that renders "undefined" for a beat is worse than one
 *  that renders nothing. */
export function useDealership(): Dealership | undefined {
  const { data } = useQuery({
    queryKey: ['dealership'],
    queryFn: () => api.get<Dealership>('/api/showroom/dealership'),
    // It changes on a reseed and not otherwise, so re-asking on every window
    // focus is a request that can only ever return the same answer.
    staleTime: 10 * 60_000,
  })
  return data
}
