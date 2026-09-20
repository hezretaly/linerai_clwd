import { Suspense } from 'react'

import { STORE } from '../lib/store'
import { designFor } from '../storefronts'

/**
 * The two public routes a dealership has -- `/` and `/showroom` under its
 * prefix -- resolved to that dealership's own design.
 *
 * The store comes off the URL the same way every API call's prefix does. A
 * dealership with a folder in `storefronts/` gets its pages; one without gets
 * the default design, which is also what the unprefixed `/showroom` draws for
 * whichever store the server was started as. The fallback while a design's
 * chunk loads is deliberately blank: a spinner in the product's colours over
 * a page about to appear in the dealer's would be the wrong first frame.
 */
export function StorefrontPage({ kind }: { kind: 'landing' | 'showroom' }) {
  const design = designFor(STORE)
  const Page = kind === 'landing' ? design.Landing : design.Showroom
  return (
    <Suspense fallback={<div className="min-h-full bg-background" />}>
      <Page />
    </Suspense>
  )
}
