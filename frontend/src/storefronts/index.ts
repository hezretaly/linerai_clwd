import { lazy, type ComponentType, type LazyExoticComponent } from 'react'

/**
 * Which dealership gets which storefront design.
 *
 * **A dealership's storefront is its own folder, and its design is free.**
 * Dealer sites are anything but alike -- one is a black header over a white
 * grid, the next is orange on near-black with a full-bleed video, the one
 * after that is a single long page -- and a shared page that renders every
 * one of them from a profile can only draw the shape it was written for.
 * So `storefronts/<slug>/` is that dealership's front page and inventory
 * list, laid out however their site is laid out, with their own components
 * and their own stylesheet if they want one. `default/` is the plain design
 * a dealership gets until somebody builds theirs.
 *
 * **What is not free is in `_shared/`, and it is small on purpose.** Every
 * storefront gets its cars through `useStorefront` (so it can only show what
 * `offerable` returns -- a car the assistant refuses to discuss cannot sit on
 * the page beside the widget refusing to discuss it), draws pictures through
 * `CarPhoto` (so a seeded path resolves against the right store), reaches the
 * assistant through `ChatFrame` (so the widget and `/chat` are one thread),
 * and reads and writes the list's filters through `filters.ts` (so any
 * landing can link into any list). Layout, copy placement, colour and
 * everything else is the folder's. `make smoke` reads every folder for the
 * four rules and nothing else.
 *
 * **Keyed by profile slug, resolved by URL.** `/alsbou` is Alsbou's folder;
 * a profile with no folder here gets `default/`. A copied profile has a new
 * slug and so gets the default, never another dealership's design -- the
 * same reason `showroom_fixture` is opt-in rather than inherited. Each
 * design is its own chunk: a buyer on one dealership's page does not download
 * every other dealership's.
 */

export interface StorefrontDesign {
  Landing: LazyExoticComponent<ComponentType>
  Showroom: LazyExoticComponent<ComponentType>
}

const DESIGNS: Record<string, StorefrontDesign> = {
  alsbou: {
    Landing: lazy(() => import('./alsbou/Landing').then((m) => ({ default: m.Landing }))),
    Showroom: lazy(() => import('./alsbou/Showroom').then((m) => ({ default: m.Showroom }))),
  },
}

const DEFAULT: StorefrontDesign = {
  Landing: lazy(() => import('./default/Landing').then((m) => ({ default: m.Landing }))),
  Showroom: lazy(() => import('./default/Showroom').then((m) => ({ default: m.Showroom }))),
}

/** The design for a store slug, or the default for one with no folder. */
export function designFor(store: string): StorefrontDesign {
  return DESIGNS[store] ?? DEFAULT
}

/** Every slug with a design of its own. Read by the gate, so a folder that is
 *  not registered here is found rather than silently unused. */
export const DESIGNED: readonly string[] = Object.keys(DESIGNS)
