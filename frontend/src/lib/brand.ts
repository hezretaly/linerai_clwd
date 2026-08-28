/**
 * The prospect's colour, applied to the buyer surfaces at runtime.
 *
 * Set on `<html>` rather than passed down as props because `BuyerTheme` wraps
 * the route from outside -- it is mounted in main.tsx and never sees the chat
 * session payload the colour arrives in. A CSS variable is the one channel
 * that reaches a stylesheet from a component that is not its parent.
 *
 * `.theme-buyer` reads these with a fallback, so nothing here running late,
 * failing, or not running at all leaves a surface unstyled: it simply stays
 * the blue the product has always used.
 */

export interface Brand {
  accent: string
  accent_ink: string
  logo_url: string
}

/** The server validates these to a hex colour and drops anything else. This
 *  is the second gate, not the first: a value that reached the DOM unchecked
 *  would be a stylesheet built from a config file. */
const HEX = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/

export function applyBrand(brand: Brand | undefined | null): void {
  if (!brand) return
  const root = document.documentElement
  if (HEX.test(brand.accent || '')) {
    root.style.setProperty('--brand-accent', brand.accent)
  }
  if (HEX.test(brand.accent_ink || '')) {
    root.style.setProperty('--brand-accent-ink', brand.accent_ink)
  }
}
