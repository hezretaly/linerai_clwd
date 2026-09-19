import { useState } from 'react'

import { withStore } from '../lib/store'

/** A car's picture, with the fallback and the store prefix in one place.
 *
 *  **A relative photo URL has to be resolved against the store.** Six surfaces
 *  rendered `photo_url` straight into an `<img>`, and a seeded row carries
 *  `/api/photos/<VIN>.svg` — a path, not a URL. Unprefixed it reaches the
 *  default store, which does not hold that VIN, so the endpoint drew its
 *  "no such vehicle" placeholder: **every card on a prefixed storefront read
 *  `0 Unknown vehicle`**, over a drawing of a car, with the real title sitting
 *  right underneath it. `withStore` leaves an absolute `https://` URL alone,
 *  so a crawled row that hotlinks the dealer's own CDN is untouched.
 *
 *  One component rather than `withStore()` at six call sites, for the reason
 *  this codebase keeps relearning: the seventh is the one somebody forgets.
 *  It also spreads the fallback the showroom already had to the other five,
 *  which had none — a photo host that refuses an off-site referrer showed a
 *  torn-page icon on the buyer's page and in the calendar.
 *
 *  The `onError` fall-through is **the last rung of
 *  `ingest/pipeline.py:_photo_for`, reached from the browser.** That ladder
 *  picks a stored copy, then the dealer's own URL, then a drawn placeholder —
 *  but it runs at publish time, when a hotlinked URL is only known to be
 *  *written*, not to still resolve. A car that sells and has its photo pulled,
 *  or an image host that refuses an off-site referrer, both surface here as a
 *  torn-page icon. Falling through to the same placeholder the ladder would
 *  have ended on keeps the card.
 *
 *  `alt` defaults to empty because five of the six sit directly beside the
 *  car's title, where a second copy of it is noise to a screen reader. The
 *  showroom's grid passes the title, since there the picture is the row.
 */
export function CarPhoto({
  vin,
  photoUrl,
  alt = '',
  className,
}: {
  vin: string
  photoUrl?: string | null
  alt?: string
  className?: string
}) {
  const [broke, setBroke] = useState(false)
  const drawn = withStore(`/api/photos/${vin}.svg`)
  return (
    <img
      src={broke || !photoUrl ? drawn : withStore(photoUrl)}
      alt={alt}
      loading="lazy"
      onError={() => setBroke(true)}
      className={className}
    />
  )
}
