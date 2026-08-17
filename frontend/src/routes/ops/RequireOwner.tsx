import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { api } from '../../lib/api'
import type { User } from '../../lib/types'

/**
 * `/ops` is ours, and this is the door.
 *
 * Deliberately *not* `RequireAuth`. That one walks through the public demo
 * door when there is no session, because on `/app` a stranger looking around
 * as a rep is the point. Here it would mint a dealership rep session for
 * somebody asking to see Liner's own inbox, which is a different building.
 *
 * The `['me']` key is shared with `RequireAuth` on purpose: one cache entry,
 * one answer to who is signed in. The two never mount together -- they are on
 * different route trees -- and an errored query refetches on mount, so
 * arriving at `/app` afterwards still gets the public-demo fallback.
 */
export function RequireOwner({ children }: { children: ReactNode }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<{ user: User }>('/api/auth/me'),
    retry: false,
  })

  if (isLoading) {
    return <div className="p-10 text-sm text-muted-foreground">Loading...</div>
  }
  // No session at all: straight to the sign-in this dashboard uses.
  if (isError || !data) {
    return <Navigate to="/login?as=owner" replace />
  }
  // Signed in, but as the dealership's staff. Also the login form, because
  // what they need is a different account and that is where accounts are
  // swapped -- `why=ops` is what stops it reading as an expired session,
  // which is the one thing that has not happened.
  if (data.user.role !== 'owner') {
    return <Navigate to="/login?as=owner&why=ops" replace />
  }
  return <>{children}</>
}
