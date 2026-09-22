import { useEffect } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'

import { api } from '../../lib/api'
import { applyBrand } from '../../lib/brand'
import type { ShowroomPayload, VehiclePayload } from './types'

/**
 * One request for everything a storefront page draws.
 *
 * The dealership, its `site:` block, the counted facets and a page of cars all
 * come back together, because they have to agree: a "Chevrolet (74)" filter
 * counted on one request beside a grid fetched on another is how a sidebar
 * promises 74 and shows 9. Both pages read this; the landing asks for a
 * handful of cars and the inventory page for a screenful, and the brand is
 * applied from whichever answered.
 *
 * `keepPreviousData` keeps the last grid on screen while a filter re-fetches.
 * A page that empties and refills on every click looks broken in a demo.
 */
export function useStorefront(params: string) {
  const query = useQuery({
    queryKey: ['showroom', params],
    queryFn: () => api.get<ShowroomPayload>(`/api/showroom?${params}`),
    placeholderData: keepPreviousData,
    // **A failed request is thrown, never swallowed into "Loading the lot".**
    // On a host where a dealership was never seeded the API answered 500 and
    // this hook simply never resolved -- so the page sat on its loading
    // copy for ever, over a header with no name in it, and nothing anywhere
    // said why. Thrown, it reaches the boundary in `StorefrontPage`, which
    // prints the server's own words (the 503 names the command to run).
    // Every design gets this without writing an error state, because it is
    // a guarantee rather than a layout.
    throwOnError: true,
  })

  useEffect(() => {
    applyBrand(query.data?.dealership.brand)
  }, [query.data])

  return query
}

/** One car's page, from `/api/showroom/vehicle/<vin>`. The same guarantee as
 *  the list: the server narrows through `offerable`, so a sold or
 *  do-not-discuss car is a 404 -- thrown, like every storefront failure, and
 *  printed by the page's boundary in the server's own words. Here, not in a
 *  design folder, because cars reach a storefront only through this file. */
export function useStorefrontCar(vin: string) {
  const query = useQuery({
    queryKey: ['showroom-car', vin],
    queryFn: () => api.get<VehiclePayload>(`/api/showroom/vehicle/${encodeURIComponent(vin)}`),
    throwOnError: true,
  })

  useEffect(() => {
    applyBrand(query.data?.dealership.brand)
  }, [query.data])

  return query
}
