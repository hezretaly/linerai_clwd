import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { api } from '../lib/api'
import type { User } from '../lib/types'

export function RequireAuth({ children }: { children: ReactNode }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<{ user: User }>('/api/auth/me'),
    retry: false,
  })

  if (isLoading) {
    return <div className="p-10 text-sm text-muted-foreground">Loading...</div>
  }
  if (isError || !data) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}
