import { Component, Suspense, type ErrorInfo, type ReactNode } from 'react'

import { ApiError } from '../lib/api'
import { STORE } from '../lib/store'
import { designFor } from '../storefronts'

/**
 * The public routes a dealership has -- `/`, `/showroom` and one car's page
 * at `/showroom/<vin>`, under its prefix -- resolved to that dealership's own design.
 *
 * The store comes off the URL the same way every API call's prefix does. A
 * dealership with a folder in `storefronts/` gets its pages; one without gets
 * the default design, which is also what the unprefixed `/showroom` draws for
 * whichever store the server was started as. The fallback while a design's
 * chunk loads is deliberately blank: a spinner in the product's colours over
 * a page about to appear in the dealer's would be the wrong first frame.
 *
 * **A storefront that cannot load says why, in the server's words.** The
 * shared `useStorefront` throws its failure rather than leaving the page on
 * "Loading the lot"; this boundary catches it for every design at once. The
 * case it exists for is a dealership whose database was never created on
 * this host -- the API answers 503 with the command to run, and that
 * sentence is what the person reading the page needs. Plain page colours
 * rather than the dealer's, because their brand arrives with the payload
 * that failed.
 */
export function StorefrontPage({ kind }: { kind: 'landing' | 'showroom' | 'vehicle' }) {
  const design = designFor(STORE)
  const Page = kind === 'landing' ? design.Landing : kind === 'vehicle' ? design.Vehicle : design.Showroom
  return (
    <Unavailable store={STORE}>
      <Suspense fallback={<div className="min-h-full bg-background" />}>
        <Page />
      </Suspense>
    </Unavailable>
  )
}

class Unavailable extends Component<{ store: string; children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('storefront failed to load', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    const status = error instanceof ApiError ? error.status : null
    return (
      <div className="min-h-full bg-background text-foreground">
        <div className="mx-auto max-w-xl px-5 py-24">
          <h1 className="text-xl font-semibold">
            {status === 503 ? 'This dealership is not set up on this host yet' : 'This page could not load'}
          </h1>
          <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{error.message}</p>
          {status != null && <p className="mt-2 text-xs text-muted-foreground">HTTP {status}{this.props.store ? ` · store "${this.props.store}"` : ''}</p>}
        </div>
      </div>
    )
  }
}
