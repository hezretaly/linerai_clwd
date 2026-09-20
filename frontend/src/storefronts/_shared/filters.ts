/**
 * The inventory list's URL contract, shared so every storefront's list page
 * reads and writes the same query string however it is designed.
 *
 * `?q=`, `?make=`, `?body_style=`, `?min_price=` and `?max_price=` are the
 * filter state. Living in the URL is what lets a front page's search box and
 * its body-style tiles land on the list already narrowed, lets the back
 * button undo a filter, and makes a filtered grid a link somebody can send.
 * One dealership's landing must be able to link into another design's list
 * without knowing how it is drawn, and this file is that agreement. Sort and
 * page depth are deliberately not here: neither is a thing a link needs to
 * carry.
 */

export interface Filters {
  q: string
  make: string
  bodyStyle: string
  min: number | null
  max: number | null
}

export const NO_FILTERS: Filters = { q: '', make: '', bodyStyle: '', min: null, max: null }

function num(value: string | null): number | null {
  if (value == null || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

/** The filters a URL carries. */
export function readFilters(params: URLSearchParams): Filters {
  return {
    q: params.get('q') ?? '',
    make: params.get('make') ?? '',
    bodyStyle: params.get('body_style') ?? '',
    min: num(params.get('min_price')),
    max: num(params.get('max_price')),
  }
}

/** The URL a set of filters is. Empty values are left out rather than written
 *  as `?make=`, so a cleared filter leaves a clean link behind. */
export function writeFilters(filters: Filters): URLSearchParams {
  const p = new URLSearchParams()
  if (filters.q) p.set('q', filters.q)
  if (filters.make) p.set('make', filters.make)
  if (filters.bodyStyle) p.set('body_style', filters.bodyStyle)
  if (filters.min != null) p.set('min_price', String(filters.min))
  if (filters.max != null) p.set('max_price', String(filters.max))
  return p
}

/** The `/api/showroom` query for a page of the list under these filters. */
export function showroomQuery(filters: Filters, limit: number, sort: string): string {
  const p = writeFilters(filters)
  p.set('limit', String(limit))
  p.set('sort', sort)
  return p.toString()
}

export function isFiltered(filters: Filters): boolean {
  return Boolean(filters.q || filters.make || filters.bodyStyle || filters.min != null || filters.max != null)
}

/** The path a landing page sends a search or a tile to. */
export function showroomPath(next: Partial<Filters>): string {
  const qs = writeFilters({ ...NO_FILTERS, ...next }).toString()
  return qs ? `/showroom?${qs}` : '/showroom'
}
