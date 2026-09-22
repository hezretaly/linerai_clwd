/**
 * The storefront's shared vocabulary: what `/api/showroom` returns and the
 * few constants both of its pages read.
 *
 * Two pages now sit on this payload -- the dealership's front page at the
 * store root and the inventory list at `/showroom` -- and they were one
 * 1,100-line component before. Splitting them is only safe if they agree on
 * what a car *is*, so that lives here once rather than as two interfaces that
 * drift the day one page learns a field the other does not.
 */

import type { Dealership, Site } from '../../lib/dealership'

export interface Car {
  vin: string
  title: string
  year: number
  /** Cased for display, which is what the breadcrumb and a make filter link
   *  read. */
  make: string
  model: string
  trim: string
  price: number | null
  mileage: number | null
  body_style: string
  features: string[]
  photo_url: string
  listing_url: string
  /** Which of the group's lots it is standing on. Empty for a single-site
   *  dealership, which is most of them. */
  location: string
  /** The dealer's own enquiry form, and only for a car they do not price
   *  online. Derived server-side from the listing URL. */
  inquiry_url: string
  /** The card's specification cells, already cased and formatted, in the order
   *  a listing prints them. Composed server-side so a card and a sentence
   *  about the same car cannot disagree -- and short by however many fields
   *  the dealer's export did not state, because an empty cell under a label
   *  reads as a page that failed to load. */
  specs: { label: string; value: string }[]
  /** Their stock number, which is how a dealer refers to a car on the phone. */
  stock_number: string
  /** What they advertise before their own fees, where `price` already includes
   *  them. Stated by the export; `null` where it did not say. */
  advertised_price: number | null
  /** Their vehicle-history provider's report. A link to what the dealer
   *  published, never a claim about what is in it. */
  history_url: string
}

export interface Facets {
  makes: { name: string; count: number }[]
  body_styles: { name: string; count: number }[]
  price_bands: { label: string; min: number | null; max: number | null; count: number }[]
}

export interface ShowroomPayload {
  dealership: Dealership
  greeting: string
  vehicles: Car[]
  total: number
  offset: number
  facets: Facets
  channels: { chat: boolean; voice: boolean }
}

export type { Dealership, Site }

/** The orders `/api/showroom` will sort by, and what to call each one.
 *
 *  The keys are the server's and an unknown one is a 400 there, so this list
 *  and `SORTS` in `api/showroom.py` have to agree -- `make smoke` reads both
 *  and fails if they do not. The labels are ours rather than the dealer's:
 *  every dealer's toolbar says roughly this, and "Make/Model A to Z" is
 *  their own default, which is why it is first. */
export const SORTS = [
  { key: 'az', label: 'Make/Model A to Z' },
  { key: 'price_low', label: 'Price: low to high' },
  { key: 'price_high', label: 'Price: high to low' },
  { key: 'year_new', label: 'Year: newest first' },
  { key: 'year_old', label: 'Year: oldest first' },
]

/** The classes that put a dealership's chrome in the dark palette.
 *
 *  **`theme-buyer` is repeated here and that is load-bearing.** It is already
 *  on the page root, but a custom property is *inherited*: the nearest
 *  ancestor that declares `--primary` wins, and `.dark` declares one. On the
 *  root the two classes sit on the same element and `.theme-buyer` wins for
 *  coming later in the stylesheet, which is where the accent comes from --
 *  nested, `.dark` is nearer, so the accent on a black nav came out the dark
 *  palette's near-white instead of the dealer's gold. Declaring both here
 *  puts the two rules back on one element. Measured in a browser: the button
 *  is white without the second class. */
export function chromeClass(shop: Dealership | undefined): string {
  return shop?.brand?.chrome === 'dark' ? 'dark theme-buyer' : ''
}

/** A phone number the way `tel:` wants it. */
export function telHref(phone: string | undefined): string {
  return phone ? phone.replace(/[^\d+]/g, '') : ''
}

/** "4156 Shelbyville Rd., Louisville, KY 40207" -> the city line, the way
 *  their own header stacks it. The ZIP is dropped for a heading. */
export function cityOf(address: string | undefined): string {
  const [, ...rest] = (address || '').split(',')
  return rest.join(',').trim().replace(/\s+\d{5}(-\d{4})?$/, '')
}

/** The keyword box's placeholder, shared so the header's and the hero's ask
 *  for the same things. */
export const SEARCH_PLACEHOLDER = 'Search by year, make, model, VIN, stock #'

/** One car's own page: the card's fields, the whole options list, and the two
 *  tables a listing prints under the photo. Composed server-side row by row,
 *  and a table the export stated nothing for is not sent at all. */
export interface CarDetail extends Car {
  sections: { title: string; rows: { label: string; value: string }[] }[]
}

export interface VehiclePayload {
  dealership: Dealership
  vehicle: CarDetail
  similar: Car[]
  channels: { chat: boolean; voice: boolean }
}
