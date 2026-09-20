import { useEffect } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'

import { api } from '../../lib/api'
import { applyBrand } from '../../lib/brand'
import type { ShowroomPayload } from './types'

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
  })

  useEffect(() => {
    applyBrand(query.data?.dealership.brand)
  }, [query.data])

  return query
}
