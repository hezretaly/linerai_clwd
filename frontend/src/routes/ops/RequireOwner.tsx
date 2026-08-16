import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { api } from '../../lib/api'
import type { User } from '../../lib/types'
import { Card } from '../../components/ui'

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
  if (isError || !data) {
    return <Navigate to="/login?as=owner" replace />
  }
  if (data.user.role !== 'owner') {
    // Signed in, but as somebody else's staff. A redirect to the login form
    // would look like the session had expired, which is the one thing it has
    // not done -- so it says what happened and offers both ways out.
    return (
      <div className="flex h-full items-center justify-center bg-muted/40 px-4 py-16">
        <Card className="w-full max-w-md p-6">
          <h1 className="text-lg font-semibold">This one is ours</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            You are signed in as {data.user.name} ({data.user.role}), and{' '}
            <code className="font-mono">/ops</code> is Liner's own dashboard rather than a
            dealership's. Nothing about Riverside Auto's buyers is on it, and nothing about
            Liner's customers is on theirs.
          </p>
          <div className="mt-5 flex gap-2">
            <a
              href="/app"
              className="inline-flex h-9 items-center rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:opacity-90"
            >
              Back to the dashboard
            </a>
            <a
              href="/login?as=owner"
              className="inline-flex h-9 items-center rounded-md border border-input px-3 text-sm font-medium hover:bg-accent"
            >
              Sign in as Liner staff
            </a>
          </div>
        </Card>
      </div>
    )
  }
  return <>{children}</>
}
