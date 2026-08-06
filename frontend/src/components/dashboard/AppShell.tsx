import clsx from 'clsx'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../../lib/api'
import { useDealerEvents } from '../../lib/ws'
import { hoursLabel, initials } from '../../lib/format'
import type { IntegrationsPayload, Overview, User } from '../../lib/types'
import { Button } from '../ui'

const NAV = [
  { to: '/app', label: 'Overview', end: true, badge: null },
  { to: '/app/conversations', label: 'Conversations', badge: 'conversations' },
  { to: '/app/leads', label: 'Leads', badge: null },
  { to: '/app/calendar', label: 'Calendar', badge: 'appointments' },
  { to: '/app/inventory', label: 'Inventory', badge: 'inventory' },
  { to: '/app/assistant', label: 'Liner setup', badge: null },
  { to: '/app/team', label: 'Team', badge: null },
] as const

export function AppShell() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  useDealerEvents()

  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<{ user: User }>('/api/auth/me'),
    retry: false,
  })
  const { data: overview } = useQuery({
    queryKey: ['overview'],
    queryFn: () => api.get<Overview>('/api/overview'),
  })

  const logout = useMutation({
    mutationFn: () => api.post('/api/auth/logout'),
    onSuccess: () => {
      queryClient.clear()
      navigate('/login')
    },
  })

  // Every badge count comes from /api/overview -- no page counts for itself.
  const badges = overview?.badges

  return (
    <div className="flex h-full">
      <nav className="flex w-56 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground">
        <div className="px-5 py-5">
          <p className="text-sm font-semibold">
            {overview?.dealership.name ?? 'Liner'}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {hoursLabel(overview?.dealership)}
          </p>
        </div>

        <div className="flex-1 space-y-0.5 px-2">
          {NAV.map((item) => {
            const count = item.badge ? badges?.[item.badge as keyof typeof badges] : undefined
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={'end' in item ? item.end : false}
                className={({ isActive }) =>
                  clsx(
                    'flex items-center justify-between rounded-md px-3 py-2 text-sm transition-colors duration-150',
                    isActive
                      ? 'bg-sidebar-accent font-medium text-sidebar-accent-foreground'
                      : 'text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground',
                  )
                }
              >
                <span>{item.label}</span>
                {count ? (
                  <span className="rounded-full bg-sidebar-primary px-1.5 py-0.5 text-[11px] font-semibold text-sidebar-primary-foreground">
                    {count}
                  </span>
                ) : null}
              </NavLink>
            )
          })}
        </div>

        <div className="border-t border-sidebar-border px-4 py-3">
          <p className="text-sm font-medium">{me?.user.name}</p>
          <p className="text-xs capitalize text-muted-foreground">{me?.user.role}</p>
          <button
            onClick={() => logout.mutate()}
            className="mt-2 text-xs text-muted-foreground underline hover:text-foreground"
          >
            Sign out
          </button>
        </div>
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">
        <IntegrationBanner />
        <main className="flex-1 overflow-y-auto bg-muted/40">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

/**
 * The risk this whole approach creates is demoing on placeholders without
 * realising it. This is the visible answer -- not dismissible, and it names the
 * exact environment variables that are missing.
 */
export function IntegrationBanner() {
  const { data } = useQuery({
    queryKey: ['integrations'],
    queryFn: () => api.get<IntegrationsPayload>('/api/integrations'),
    staleTime: 30_000,
  })

  const missing = data?.integrations.filter((i) => !i.configured) ?? []
  if (!missing.length) return null

  return (
    <div className="border-b border-warning/30 bg-warning-muted px-6 py-2 text-warning-foreground">
      <p className="text-sm">
        <span className="font-semibold">
          {missing.length} integration{missing.length > 1 ? 's are' : ' is'} not configured:
        </span>{' '}
        {missing.map((i) => i.label).join(', ')}.{' '}
        <span className="opacity-80">
          Those features report themselves as unavailable rather than simulating a result.
        </span>{' '}
        <a href="/api/integrations" className="underline" target="_blank" rel="noreferrer">
          Details
        </a>
      </p>
    </div>
  )
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string
  subtitle?: string
  actions?: React.ReactNode
}) {
  return (
    <header className="sticky top-0 z-10 flex items-center justify-between gap-4 border-b border-border bg-background px-6 py-4">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold">{title}</h1>
        {subtitle && <p className="mt-0.5 text-sm text-muted-foreground">{subtitle}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  )
}

export function Avatar({ name }: { name: string }) {
  return (
    <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent text-[11px] font-semibold text-accent-foreground">
      {initials(name)}
    </span>
  )
}

export { Button }
