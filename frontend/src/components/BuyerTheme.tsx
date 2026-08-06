import type { ReactNode } from 'react'

/**
 * Scopes the buyer-facing palette.
 *
 * The dealer side runs shadcn classic unmodified -- near-black primary, light
 * sidebar. Buyer surfaces keep the iOS blue the product is branded with, since
 * a consumer chat should not look like an admin console. Only the accent family
 * is overridden; greys, borders and radii still come from classic, so the two
 * sides remain one system rather than two themes.
 *
 * Applied to /chat and /call only. The landing page at `/` is a standalone
 * document with its own palette and is not a React route at all.
 *
 * This works because the tokens are CSS variables consumed through
 * `@theme inline` -- see the warning in styles/liner-theme.css before changing
 * how the theme is declared.
 */
export function BuyerTheme({ children }: { children: ReactNode }) {
  return <div className="theme-buyer h-full">{children}</div>
}
