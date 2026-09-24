/** Which dealership this page is for, read off its own URL.
 *
 *  `/alsbou/app` is Alsbou's dashboard and `/app` is whichever store the
 *  server was started as. The prefix has to reach two places: every API call
 *  (or the request lands on the default store's database) and the router's
 *  basename (or every `<Link>` drops it and navigating once leaves the store).
 *
 *  **Expressed as "not one of ours" rather than a list of dealerships.** The
 *  browser cannot ask the server which stores exist before its first request,
 *  so something here has to decide. A hardcoded list of slugs would be a
 *  second copy of the profile directory and would go stale the day a store is
 *  added -- the failure `SPA_PREFIXES` is a standing lesson in. The app's own
 *  top-level routes are a closed set that this bundle already knows, so the
 *  test is: a first segment that is not one of mine is a store.
 */

/** The SPA's own roots, plus the paths the server owns at the top level.
 *  Kept in step with `main.tsx` by `make smoke`, which reads both. */
const OURS = new Set([
  '',          // "/" -- the marketing page
  'app',
  'chat',
  'call',
  'login',
  'ops',
  'showroom',
  'api',
  'assets',
  'r',         // the outreach click hop
  's',         // signature images
  'widget',    // the website chat, framed on a dealer's own site
])

function firstSegment(path: string): string {
  return path.split('/')[1] ?? ''
}

/** This page is the website chat, framed on a dealer's own site by
 *  `embed.js`: `/widget/<dealer>`. The one address that names its store
 *  *second*, because it goes into a tag on somebody else's website and reads
 *  there as what it is. */
export const WIDGET: boolean =
  typeof window !== 'undefined' && firstSegment(window.location.pathname) === 'widget'

/** The store slug in the current URL, or "" when there is none. */
export const STORE: string = (() => {
  if (typeof window === 'undefined') return ''
  const path = window.location.pathname
  if (WIDGET) return (path.split('/')[2] ?? '').toLowerCase().replace(/[^a-z0-9-]/g, '')
  const first = firstSegment(path)
  return OURS.has(first) ? '' : first
})()

/** The router basename for this page, or undefined for an unprefixed one.
 *  The widget has none: its route is `/widget/:dealer` as it stands, and the
 *  store reaches its API calls through `withStore` like everywhere else. */
export const BASENAME: string | undefined = STORE && !WIDGET ? `/${STORE}` : undefined

/** Put the store back on an absolute API path.
 *
 *  Only absolute paths are prefixed. A relative one, or an absolute URL to
 *  somewhere else, is left alone -- prefixing `https://...` would produce
 *  nonsense, and this runs over every request the app makes.
 */
export function withStore(path: string): string {
  if (!STORE || !path.startsWith('/')) return path
  if (path === `/${STORE}` || path.startsWith(`/${STORE}/`)) return path
  return `/${STORE}${path}`
}

/** Leave this bundle for a path in another store (or none).
 *
 *  A plain router navigation cannot do it: the basename is fixed when the app
 *  mounts, so `navigate('/alsbou/app')` from a page running under
 *  `/craigandlandreth` resolves to `/craigandlandreth/alsbou/app`. Crossing a
 *  store is a document load, which is also the honest thing -- it is a
 *  different dealership's dashboard reading a different database.
 */
export function leaveTo(path: string): void {
  window.location.assign(path)
}
