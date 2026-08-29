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

export interface Dealership {
  id: string
  name: string
  timezone: string
  hours: Record<string, { open: string; close: string } | null>
  address: string
  phone: string
  website_url: string
  brand: Brand
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
