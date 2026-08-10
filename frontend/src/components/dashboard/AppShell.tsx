import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
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
  // Everyone Liner has heard from, on every channel, in one list. The badge is
  // blue, not red: it counts messages waiting to be read, and red is this
  // dashboard's word for something going wrong.
  { to: '/app/conversations', label: 'Conversations', icon: 'inbox', group: 'Today',
    badge: 'conversations', tone: 'primary' },
  { to: '/app/calendar', label: 'Calendar', icon: 'calendar', group: 'Today',
    badge: 'appointments', tone: 'muted' },
  { to: '/app/inventory', label: 'Inventory', icon: 'inventory', group: 'Manage',
    badge: 'inventory', tone: 'primary' },
  { to: '/app/assistant', label: 'Liner setup', icon: 'sliders', group: 'Manage',
    badge: null, tone: 'muted' },
  { to: '/app/team', label: 'Team', icon: 'user', group: 'Manage',
    badge: null, tone: 'muted' },
  // Settings is the mockups' eighth item and lands here once the page exists.
  // Listing it before then would point at the catch-all, which bounces to the
  // landing page -- a worse answer than not offering the link.
] as const

/** Remembered, because a rail you have to re-collapse on every page load is
 *  not collapsible. Below lg it is a drawer and this does not apply.
 *
 *  Collapsed is a 14-unit icon strip, not nothing: hiding the rail entirely
 *  cost the badge counts, which are the one thing on it that changes -- a rep
 *  glances at the strip to see whether anything is waiting. */
const RAIL_KEY = 'liner.nav.collapsed'

export function AppShell() {
  useDealerEvents()
  const location = useLocation()
  const [navOpen, setNavOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(RAIL_KEY) !== '0',
  )
  // Pointing at the strip opens it; leaving closes it again. Collapsed stays
  // the remembered state -- hovering does not un-collapse it, so the rail is
  // back to a strip the moment the pointer moves away.
  const [peeking, setPeeking] = useState(false)
  const open = !collapsed || peeking

  const toggleRail = () => {
    setCollapsed((was) => {
      localStorage.setItem(RAIL_KEY, was ? '0' : '1')
      return !was
    })
  }

  const { data: overview } = useQuery({
    queryKey: ['overview'],
    queryFn: () => api.get<Overview>('/api/overview'),
  })

  const badges = overview?.badges

  // Tapping a link has to close the drawer. Without this the destination
  // renders behind a sheet that is still covering it, which reads as the tap
  // having done nothing.
  useEffect(() => setNavOpen(false), [location.pathname])

  return (
    <div className="min-h-full bg-canvas">
      {/* Below lg the rail is a drawer, not a permanent strip: 56px of a 390px
          phone is 14% of the screen given up on every page, and the icons are
          unlabelled at that width anyway. */}
      {navOpen && (
        <button
          aria-label="Close menu"
          onClick={() => setNavOpen(false)}
          className="fixed inset-0 z-40 bg-foreground/40 lg:hidden"
        />
      )}
      <aside
        onMouseEnter={() => collapsed && setPeeking(true)}
        onMouseLeave={() => setPeeking(false)}
        className={clsx(
          'fixed inset-y-0 left-0 z-50 flex flex-col border-r border-border bg-sidebar',
          'transition-all duration-200 lg:z-40 lg:translate-x-0',
          navOpen ? 'w-60 translate-x-0' : 'w-60 -translate-x-full',
          collapsed ? (peeking ? 'lg:w-60 lg:shadow-xl' : 'lg:w-14') : 'lg:w-60',
        )}
      >
        <div
          className={clsx(
            'flex h-14 items-center gap-2.5 border-b border-border px-4',
            !open && 'lg:justify-center lg:px-0',
          )}
        >
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Icon name="chat" className="h-4 w-4" />
          </span>
          <div className={clsx('min-w-0', !open && 'lg:hidden')}>
            <div className="truncate text-sm font-semibold leading-tight">
              {overview?.dealership.name ?? 'Liner'}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              <span className="font-semibold text-primary">Liner</span> AI
            </div>
          </div>
          <button
            onClick={toggleRail}
            aria-label="Pin or unpin the menu"
            aria-pressed={!collapsed}
            title={
              collapsed
                ? 'Keep the menu open'
                : 'Let the menu collapse to a strip that opens on hover'
            }
            className={clsx(
              'ml-auto hidden h-6 w-6 shrink-0 items-center justify-center rounded transition-colors',
              collapsed
                ? 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
                : 'bg-accent text-accent-foreground',
              open ? 'lg:inline-flex' : 'lg:hidden',
            )}
          >
            <Icon name="sidebar" className="h-3.5 w-3.5" />
          </button>
        </div>

        <nav className="scroll-thin flex-1 overflow-y-auto px-2 py-3">
          {(['Today', 'Manage'] as const).map((group) => (
            <div key={group}>
              {!open && <div className="mx-2 my-3 hidden border-t border-border lg:block" />}
              <div
                className={clsx(
                  'px-2 pb-1.5 pt-4 text-xs font-medium text-muted-foreground first:pt-1',
                  !open && 'lg:hidden',
                )}
              >
                {group}
              </div>
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
                        'relative mb-0.5 flex items-center gap-2.5 rounded-md py-2 text-sm font-medium transition-colors',
                        open ? 'px-2.5' : 'px-2.5 lg:justify-center lg:px-0',
                        isActive
                          ? 'bg-accent text-accent-foreground'
                          : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                      )
                    }
                  >
                    <Icon name={item.icon as IconName} className="h-4 w-4 shrink-0" />
                    <span className={clsx(!open && 'lg:hidden')}>{item.label}</span>
                    {count ? (
                      <span
                        className={clsx(
                          'tnum rounded-full px-1.5 py-0.5 text-[10px]',
                          item.tone === 'primary'
                            ? 'bg-primary font-semibold text-primary-foreground'
                            : 'bg-muted font-medium text-muted-foreground',
                          // At strip width it rides the corner of the icon.
                          open
                            ? 'ml-auto'
                            : 'ml-auto lg:absolute lg:right-0.5 lg:top-0.5 lg:ml-0 lg:px-1 lg:leading-tight',
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

        <LinerStatus collapsed={!open} />
        <AccountFooter dealership={hoursLabel(overview?.dealership)} collapsed={!open} />
      </aside>

      <div className={clsx('transition-[margin] duration-200', collapsed ? 'lg:ml-14' : 'lg:ml-60')}>
        <TopBar onOpenNav={() => setNavOpen(true)} />
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
function LinerStatus({ collapsed }: { collapsed: boolean }) {
  const { data } = useQuery({
    queryKey: ['overview'],
    queryFn: () => api.get<Overview>('/api/overview'),
  })
  const open = data?.badges.conversations ?? 0

  if (collapsed) {
    // At strip width the card has nowhere to go, but the fact it carries --
    // Liner is answering -- is worth one dot.
    return (
      <div className="hidden justify-center border-t border-border py-3 lg:flex">
        <span className="relative flex h-2.5 w-2.5" title={`Liner is answering -- ${open} open`}>
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-60" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-success" />
        </span>
      </div>
    )
  }

  return (
    <div className="border-t border-border p-3">
      <div className="rounded-md border border-border bg-background p-3">
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

function AccountFooter({ dealership, collapsed }: { dealership: string; collapsed: boolean }) {
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
    <div
      className={clsx(
        'flex items-center gap-2.5 border-t border-border px-4 py-3',
        collapsed && 'lg:justify-center lg:px-0',
      )}
    >
      <span
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground"
        title={collapsed ? `${me?.user.name ?? ''} -- ${dealership}` : dealership}
      >
        {initials(me?.user.name ?? '')}
      </span>
      <div className={clsx('min-w-0', collapsed && 'lg:hidden')}>
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
function TopBar({ onOpenNav }: { onOpenNav: () => void }) {
  const now = new Date()
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-background/95 px-4 backdrop-blur md:gap-4 md:px-6">
      {/* Mobile only. At lg and up the rail pins itself from its own header --
          one control, next to the thing it controls. */}
      <button
        onClick={onOpenNav}
        aria-label="Open menu"
        className="-ml-1 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground lg:hidden"
      >
        <Icon name="menu" className="h-5 w-5" />
      </button>

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
  accent = false,
}: {
  title: string
  subtitle?: string
  actions?: React.ReactNode
  /** Title in the brand colour with the subtitle in body text, rather than the
   *  other way round. The address under it is a fact about the dealership, not
   *  a caption, so it reads at full contrast. */
  accent?: boolean
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1
          className={clsx(
            'text-2xl font-semibold tracking-tight',
            accent && 'text-primary',
          )}
        >
          {title}
        </h1>
        {subtitle && (
          <p className={clsx('mt-1 text-sm', accent ? 'text-foreground' : 'text-muted-foreground')}>
            {subtitle}
          </p>
        )}
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
