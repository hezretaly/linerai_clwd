/** Which dealership this page is for, and what its path says about it.
 *
 *  Two facts that are usually one. `/alsbou/app` is Alsbou's dashboard by its
 *  *path*, and `/app` is whichever store the server was started as -- except
 *  on a group's own subdomain, where `alsbou.linerai.us/app` is Alsbou's by
 *  its *host* and the path names nobody. Only the server knows which hosts are
 *  stores, so there it writes the store into the document (`STORE_META` in
 *  `backend/app/static.py`). Reading the path alone, this page thought it was
 *  nobody's while `/api/auth/me` said Alsbou, and RequireAuth reloaded /app
 *  for ever.
 *
 *  So `STORE` is which dealership -- the path's, else the host's -- and the
 *  prefix is the path's alone. The prefix has to reach two places: every API
 *  call (or the request lands on the default store's database) and the
 *  router's basename (or every `<Link>` drops it and navigating once leaves
 *  the store). On the subdomain there is none to put back, and one would name
 *  the store twice.
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

/** The store this page's *host* names -- `alsbou.linerai.us` -- as the server
 *  wrote it into the document, or "". The browser cannot work it out: which
 *  hosts are stores is `STORE_DOMAIN` and the profile directory, and
 *  `demo.linerai.us` is shaped exactly like a store's host while naming none.
 *  Absent in development, on the shared host and on the demo. */
const HOST: string = (() => {
  if (typeof document === 'undefined') return ''
  const raw = document.querySelector('meta[name="liner-store"]')?.getAttribute('content') ?? ''
  return raw.toLowerCase().replace(/[^a-z0-9-]/g, '')
})()

/** The store this page's *path* names, or "". Only this ever goes back on a
 *  path: on a group's own subdomain the host names the store already, and a
 *  prefix there would name it twice (`store_path` in api/redirect.py is the
 *  server's copy of the rule). */
const PREFIX: string = (() => {
  if (typeof window === 'undefined') return ''
  const path = window.location.pathname
  if (WIDGET) return (path.split('/')[2] ?? '').toLowerCase().replace(/[^a-z0-9-]/g, '')
  const first = firstSegment(path)
  return OURS.has(first) ? '' : first
})()

/** Which dealership this page is for: the one its path names, else the one
 *  its host does, else "" -- whichever store the server was started as. */
export const STORE: string = PREFIX || HOST

/** The router basename for this page, or undefined for an unprefixed one.
 *  The widget has none: its route is `/widget/:dealer` as it stands, and the
 *  store reaches its API calls through `withStore` like everywhere else. */
export const BASENAME: string | undefined = PREFIX && !WIDGET ? `/${PREFIX}` : undefined

/** Put the path's store back on an absolute API path.
 *
 *  Only absolute paths are prefixed. A relative one, or an absolute URL to
 *  somewhere else, is left alone -- prefixing `https://...` would produce
 *  nonsense, and this runs over every request the app makes.
 */
export function withStore(path: string): string {
  if (!PREFIX || !path.startsWith('/')) return path
  if (path === `/${PREFIX}` || path.startsWith(`/${PREFIX}/`)) return path
  return `/${PREFIX}${path}`
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
