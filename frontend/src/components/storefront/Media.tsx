import { useState } from 'react'

/**
 * The storefront's hotlinked pictures, each with its own fallback.
 *
 * Every image on these pages comes from the dealer's own CDN: an image host
 * that refuses an off-site referrer, a URL that has moved and a venue firewall
 * all look identical, all happen on somebody else's laptop, and a torn-page
 * icon on the dealership's own front page is the failure to prevent. So none
 * of these renders a broken `<img>`: the hero keeps its height and caption,
 * a tile keeps its label, a promo band simply is not drawn.
 *
 * None of them has ever loaded on the machine this was written on -- the
 * egress proxy refuses the asset host -- so what has been seen here is every
 * fallback and not one photograph. That is written down rather than implied.
 */

/** The photograph across the top of their front page.
 *
 *  **One image, and it is meant to be seen.** This was a cross-fading rotation
 *  of five drawn behind a `via-background/85` scrim -- and at 85% the scrim hid
 *  the picture entirely, so the hero rendered as a plain band of page colour
 *  with a heading on it. The five turned out not to be hero slides at all but
 *  their linked banner strip, which is `BannerTile` below. */
export function Hero({ src }: { src: string }) {
  const [broke, setBroke] = useState(false)
  return (
    <div className="absolute inset-0 -z-10 overflow-hidden bg-muted">
      {!broke && (
        <img src={src} alt="" aria-hidden onError={() => setBroke(true)} className="h-full w-full object-cover" />
      )}
      {/* Enough to carry white text on any photograph, and no more. The words
          over this declare their own colour rather than inheriting the page's,
          so this darkens the picture instead of replacing it. */}
      <div className="absolute inset-0 bg-black/45" />
    </div>
  )
}

/** One tile of their banner strip: an image that is a link.
 *
 *  **The label is what survives a broken image.** Five links whose only
 *  content is an `<img>` become five empty boxes the moment the dealer's CDN
 *  is unreachable -- indistinguishable from a page that failed to build, and
 *  unusable either way. The label is drawn under the picture and stands alone
 *  without it, so the strip still works as five calls to action. */
export function BannerTile({ image, href, label }: { image: string; href: string; label: string }) {
  const [broke, setBroke] = useState(false)
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="group block min-w-0 overflow-hidden rounded-lg border border-border bg-card"
    >
      <div className="aspect-[93/50] w-full overflow-hidden bg-muted">
        {!broke && (
          <img
            src={image}
            alt=""
            aria-hidden
            onError={() => setBroke(true)}
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
          />
        )}
      </div>
      <span className="block truncate px-3 py-2 text-center text-xs font-semibold uppercase tracking-wide">
        {label}
      </span>
    </a>
  )
}

/** A full-width promotional band that is a link -- their financing and
 *  trade-in banners, which run the width of the page between sections.
 *
 *  **Not drawn at all when the image fails.** Unlike a tile it has no label to
 *  fall back on: the words are in the picture, and a band-shaped hole with a
 *  link in it reads as a page that failed to load. A section that quietly is
 *  not there is the honest degradation. */
export function Promo({ image, href, label }: { image: string; href: string; label: string }) {
  const [broke, setBroke] = useState(false)
  if (broke) return null
  return (
    <a href={href} target="_blank" rel="noreferrer" aria-label={label} className="block w-full">
      <img src={image} alt={label} onError={() => setBroke(true)} className="h-auto w-full" />
    </a>
  )
}

/** A body-style tile: their "Explore Vehicles By Body Style" pictures, each
 *  leading to the inventory list narrowed to that style. Same fallback rule
 *  as a banner tile -- the word survives the picture. */
export function StyleTile({
  image,
  label,
  onClick,
}: {
  image: string
  label: string
  onClick: () => void
}) {
  const [broke, setBroke] = useState(false)
  return (
    <button
      onClick={onClick}
      className="group block min-w-0 overflow-hidden rounded-lg border border-border bg-card text-left"
    >
      <div className="aspect-[4/3] w-full overflow-hidden bg-muted">
        {!broke && (
          <img
            src={image}
            alt=""
            aria-hidden
            onError={() => setBroke(true)}
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
          />
        )}
      </div>
      <span className="block truncate px-3 py-2 text-center text-sm font-semibold uppercase tracking-wide">
        {label}
      </span>
    </button>
  )
}
