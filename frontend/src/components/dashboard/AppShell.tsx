import clsx from 'clsx'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../../lib/api'
import { useDealerEvents } from '../../lib/ws'
import { hoursLabel, initials } from '../../lib/format'
import type { IntegrationsPayload, Overview, User } from '../../lib/types'
import { Button, Unavailable } from '../ui'
import { Icon, type IconName } from '../Icon'

/**
 * Two groups, as the mockups have it: what is happening right now, and the
 * things you configure once. `badge` names a key on /api/overview's badges --
 * no page counts for itself, and `tone` is what makes the count mean
 * something: a red pill is work waiting, a grey pill is just a total.
 */
const NAV = [
  { to: '/app', label: 'Overview', icon: 'overview', end: true, group: 'Today',
    badge: null, tone: 'muted' },
  { to: '/app/conversations', label: 'Conversations', icon: 'chat', group: 'Today',
    badge: 'conversations', tone: 'destructive' },
  { to: '/app/leads', label: 'Leads', icon: 'leads', group: 'Today',
    badge: null, tone: 'muted' },
  { to: '/app/calendar', label: 'Calendar', icon: 'calendar', group: 'Today',
    badge: 'appointments', tone: 'muted' },
  { to: '/app/inventory', label: 'Inventory', icon: 'inventory', group: 'Manage',
    badge: 'inventory', tone: 'destructive' },
  { to: '/app/assistant', label: 'Liner setup', icon: 'sliders', group: 'Manage',
    badge: null, tone: 'muted' },
  { to: '/app/team', label: 'Team', icon: 'user', group: 'Manage',
    badge: null, tone: 'muted' },
  // Settings is the mockups' eighth item and lands here once the page exists.
  // Listing it before then would point at the catch-all, which bounces to the
  // landing page -- a worse answer than not offering the link.
] as const

export function AppShell() {
  useDealerEvents()

  const { data: overview } = useQuery({
    queryKey: ['overview'],
    queryFn: () => api.get<Overview>('/api/overview'),
  })

  const badges = overview?.badges

  return (
    <div className="min-h-full bg-canvas">
      <aside className="fixed inset-y-0 left-0 z-40 flex w-14 flex-col border-r border-border bg-sidebar lg:w-60">
        <div className="flex h-14 items-center gap-2.5 border-b border-border px-3 lg:px-4">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Icon name="chat" className="h-4 w-4" />
          </span>
          <div className="hidden min-w-0 lg:block">
            <div className="truncate text-sm font-semibold leading-tight">
              {overview?.dealership.name ?? 'Liner'}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              <span className="font-semibold text-primary">Liner</span> AI
            </div>
          </div>
        </div>

        <nav className="scroll-thin flex-1 overflow-y-auto px-2 py-3">
          {(['Today', 'Manage'] as const).map((group, groupIndex) => (
            <div key={group}>
              <div className="hidden px-2 pb-1.5 pt-1 text-xs font-medium text-muted-foreground lg:block lg:pt-4 lg:first:pt-1">
                {group}
              </div>
              {groupIndex > 0 && <div className="my-2 border-t border-border lg:hidden" />}
              {NAV.filter((item) => item.group === group).map((item) => {
                const count = item.badge
                  ? badges?.[item.badge as keyof typeof badges]
                  : undefined
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={'end' in item ? item.end : false}
                    title={item.label}
                    className={({ isActive }) =>
                      clsx(
                        'mb-0.5 flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium transition-colors',
                        isActive
                          ? 'bg-accent text-accent-foreground'
                          : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                      )
                    }
                  >
                    <Icon name={item.icon as IconName} className="h-4 w-4 shrink-0" />
                    <span className="hidden lg:inline">{item.label}</span>
                    {count ? (
                      <span
                        className={clsx(
                          'tnum ml-auto hidden rounded-full px-1.5 py-0.5 text-[10px] lg:inline',
                          item.tone === 'destructive'
                            ? 'bg-destructive font-semibold text-destructive-foreground'
                            : 'bg-muted font-medium text-muted-foreground',
                        )}
                      >
                        {count}
                      </span>
                    ) : null}
                  </NavLink>
                )
              })}
            </div>
          ))}
        </nav>

        <LinerStatus />
        <AccountFooter dealership={hoursLabel(overview?.dealership)} />
      </aside>

      <div className="ml-14 lg:ml-60">
        <TopBar />
        <IntegrationBanner />
        <Outlet />
      </div>
    </div>
  )
}

/**
 * The mockups put a "Liner is answering / Pause Liner" card at the foot of the
 * rail. Pausing is per conversation in this system -- `conversations.agent_paused`,
 * set when a rep takes over -- and there is no dealership-wide kill switch
 * behind it, so the button says so instead of pretending to throw one.
 */
function LinerStatus() {
  const { data } = useQuery({
    queryKey: ['overview'],
    queryFn: () => api.get<Overview>('/api/overview'),
  })
  const open = data?.badges.conversations ?? 0

  return (
    <div className="border-t border-border p-2 lg:p-3">
      <div className="hidden rounded-md border border-border bg-background p-3 lg:block">
        <div className="mb-1 flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-success" />
          </span>
          <span className="text-sm font-medium">Liner is answering</span>
        </div>
        <p className="tnum text-xs text-muted-foreground">
          {open} open conversation{open === 1 ? '' : 's'}
        </p>
        <Unavailable
          className="mt-2.5 w-full"
          label="Pause Liner"
          why="Pausing is per conversation -- take one over from its thread. There is no dealership-wide switch."
        />
      </div>
    </div>
  )
}

function AccountFooter({ dealership }: { dealership: string }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<{ user: User }>('/api/auth/me'),
    retry: false,
  })

  const logout = useMutation({
    mutationFn: () => api.post('/api/auth/logout'),
    onSuccess: () => {
      queryClient.clear()
      navigate('/login')
    },
  })

  return (
    <div className="flex items-center gap-2.5 border-t border-border p-2 lg:px-4 lg:py-3">
      <span
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground"
        title={dealership}
      >
        {initials(me?.user.name ?? '')}
      </span>
      <div className="hidden min-w-0 lg:block">
        <div className="truncate text-sm font-medium leading-tight">{me?.user.name}</div>
        <button
          onClick={() => logout.mutate()}
          className="truncate text-xs capitalize text-muted-foreground hover:text-foreground hover:underline"
        >
          {me?.user.role} -- sign out
        </button>
      </div>
    </div>
  )
}

/**
 * Search and notifications are drawn in every mockup header. Neither has an
 * endpoint: there is no search index and no notification store. They render as
 * what they are rather than as dead chrome that looks clickable.
 */
function TopBar() {
  const now = new Date()
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-4 border-b border-border bg-background/95 px-4 backdrop-blur md:px-6">
      <div className="relative w-full max-w-sm">
        <Icon
          name="search"
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
        />
        <input
          type="search"
          disabled
          placeholder="Search is not built yet"
          title="No search index exists. Filter from the Leads, Conversations or Inventory pages."
          className="h-9 w-full cursor-not-allowed rounded-md border border-input bg-muted/40 pl-9 pr-3 text-sm text-muted-foreground outline-none placeholder:text-muted-foreground"
        />
      </div>
      <div className="ml-auto flex items-center gap-2">
        <span
          title="Notifications are not built. Work waiting for a person is on the Overview."
          className="inline-flex h-9 w-9 cursor-not-allowed items-center justify-center rounded-md text-muted-foreground/40"
        >
          <Icon name="bell" className="h-4 w-4" />
        </span>
        <div className="h-5 w-px bg-border" />
        <span className="tnum hidden text-sm text-muted-foreground sm:inline">
          {now.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
          {' · '}
          {now.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}
        </span>
      </div>
    </header>
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
    <div className="border-b border-warning/30 bg-warning-muted px-4 py-2 text-warning-foreground md:px-6">
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

/**
 * The mockups' page title: in the flow of the page under the app-wide top bar,
 * not a second sticky strip. Ported pages use this.
 *
 * `PageHeader` below is the older sticky variant and is still what the pages
 * that have not been ported yet render. The two exist side by side on purpose
 * -- swapping every page's header in one commit would mean changing seven
 * layouts to verify one.
 */
export function PageIntro({
  title,
  subtitle,
  actions,
}: {
  title: string
  subtitle?: string
  actions?: React.ReactNode
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
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
    <header className="flex items-center justify-between gap-4 border-b border-border bg-background px-6 py-4">
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
