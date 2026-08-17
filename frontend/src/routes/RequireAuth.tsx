import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { api } from '../lib/api'
import type { User } from '../lib/types'

export interface PublicDemo {
  available: boolean
  name?: string
  role?: string
}

/** Whether this deployment lets a stranger in, and as whom.
 *
 *  Its own query so the login page and the app shell read one answer. Asked
 *  before signing in, which is the only reason it is unauthenticated. */
export function usePublicDemo() {
  return useQuery({
    queryKey: ['public-demo'],
    queryFn: () => api.get<PublicDemo>('/api/auth/public'),
    staleTime: Infinity,
    retry: false,
  })
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['me'],
    queryFn: async () => {
      try {
        return await api.get<{ user: User }>('/api/auth/me')
      } catch (err) {
        // No session. On a deployment that has opened the door, walking
        // through it *is* the expected path -- sending a visitor to a login
        // form they have no password for would make a public demo a dead end,
        // and asking them to click "continue as a guest" is a step whose only
        // possible answer is yes.
        //
        // Still a real session cookie for a real rep account when it succeeds,
        // so nothing downstream has a second notion of who is signed in.
        const demo = await api.get<PublicDemo>('/api/auth/public').catch(() => null)
        if (!demo?.available) throw err
        return await api.post<{ user: User }>('/api/auth/public')
      }
    },
    retry: false,
  })

  if (isLoading) {
    return <div className="p-10 text-sm text-muted-foreground">Loading...</div>
  }
  if (isError || !data) {
    return <Navigate to="/login" replace />
  }
  // The mirror of `RequireOwner`. An ops session is refused by every
  // dealership endpoint, so without this the shell rendered and then every
  // panel in it 403'd -- a dashboard that looks broken rather than one that
  // says this is not yours. The session is untouched either way; /ops is
  // where this account's own dashboard is.
  if (data.user.role === 'owner') {
    return <Navigate to="/ops" replace />
  }
  return <>{children}</>
}
