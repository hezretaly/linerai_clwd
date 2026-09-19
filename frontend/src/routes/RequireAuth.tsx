import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { api } from '../lib/api'
import { STORE, leaveTo } from '../lib/store'
import type { User } from '../lib/types'

/** "Who am I", plus which store this session belongs to and where its
 *  dashboard lives. The store cannot be read in the browser -- the cookie is
 *  httpOnly -- so the server has to say. */
export interface Me {
  user: User
  store?: string
  home?: string
}

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
        return await api.get<Me>('/api/auth/me')
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
        return await api.post<Me>('/api/auth/public')
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
  // Signed in, but looking at a different dealership's URL. Every panel here
  // would 403 -- `current_user` refuses a session whose store is not the
  // request's -- so the page would render broken rather than say whose it is.
  // `home` comes from the server because the cookie is httpOnly: the page has
  // no way to read which store it was minted against.
  if (data.home && data.home !== '/ops' && data.store !== STORE) {
    leaveTo(data.home)
    return null
  }
  // The mirror of `RequireOwner`. An ops session is refused by every
  // dealership endpoint, so without this the shell rendered and then every
  // panel in it 403'd -- a dashboard that looks broken rather than one that
  // says this is not yours. The session is untouched either way; /ops is
  // where this account's own dashboard is.
  if (data.user.role === 'owner') {
    // Not <Navigate>: with a store basename that resolves to `/<store>/ops`,
    // and /ops is deliberately never per-store.
    leaveTo('/ops')
    return null
  }
  return <>{children}</>
}
