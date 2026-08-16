/**
 * Liner's own dashboard. Not a dealership's, and deliberately not the same
 * shell: `/app` is Riverside Auto's showroom and every fact on it belongs to
 * them. This one has two pages, two users, and one thing that arrives without
 * anybody asking for it.
 */

import { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../../lib/api'
import { initials, relative } from '../../lib/format'
import type { User } from '../../lib/types'
import { useDealerEvents } from '../../lib/ws'
import { Icon, type IconName } from '../../components/Icon'
import { useDemos, useOpsSummary, useSetStatus } from './data'

const NAV: { to: string; label: string; icon: IconName; end?: boolean; badge?: 'unread' | 'unmatched_mail' }[] = [
  { to: '/ops', label: 'Demo calendar', icon: 'calendar', end: true, badge: 'unread' },
  { to: '/ops/mail', label: 'Inbox', icon: 'mail', badge: 'unmatched_mail' },
]

export function OpsShell() {
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [navOpen, setNavOpen] = useState(false)
  const [toasts, setToasts] = useState<{ id: string; title: string; body: string }[]>([])

  // Somebody filling in the form is the one event on this dashboard that
  // nobody clicked for, so it is the one that has to interrupt. `demo.updated`
  // arrives too -- from the other laptop, marking something read -- and that
  // one only refreshes the counts, which `useDealerEvents` already does.
  //
  // Live only. The socket replays the events table on connect, so without the
  // `replayed` check every page load popped a toast for every demo booked
  // this week -- including the ones already opened, which is precisely the
  // notification that will not go away.
  useDealerEvents((event) => {
    if (event.type !== 'demo.requested' || event.replayed) return
    const payload = event.payload as Record<string, string | null>
    setToasts((was) => [
      ...was.filter((t) => t.id !== payload.request_id),
      {
        id: String(payload.request_id ?? ''),
        title: payload.kind === 'support' ? 'New support request' : 'New demo booked',
        body: [payload.name, payload.dealership].filter(Boolean).join(' -- ') || 'Someone new',
      },
    ])
  })

  useEffect(() => setNavOpen(false), [location.pathname])

  const dismiss = (id: string) => setToasts((was) => was.filter((t) => t.id !== id))

  return (
    <div className="min-h-full bg-canvas">
      {navOpen && (
        <button
          aria-label="Close menu"
          onClick={() => setNavOpen(false)}
          className="fixed inset-0 z-40 bg-foreground/40 lg:hidden"
        />
      )}

      <aside
        className={clsx(
          'fixed inset-y-0 left-0 z-50 flex w-60 flex-col border-r border-border bg-sidebar',
          'transition-transform duration-200 lg:z-40 lg:translate-x-0',
          navOpen ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        <div className="flex h-14 items-center gap-2.5 border-b border-border px-4">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-foreground text-background">
            <Icon name="sliders" className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold leading-tight">Liner AI</div>
            <div className="truncate text-xs text-muted-foreground">Company dashboard</div>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto px-2 py-3">
          <div className="px-2 pb-1.5 pt-1 text-xs font-medium text-muted-foreground">Ours</div>
          {NAV.map((item) => (
            <OpsLink key={item.to} {...item} />
          ))}

          <div className="px-2 pb-1.5 pt-5 text-xs font-medium text-muted-foreground">
            The demo dealership
          </div>
          {/* The product itself, one click away. The separation runs one way
              and only one way: a dealership's staff cannot reach /ops at all,
              while an owner can read the showroom -- there is one dealership
              here and it is the demo we run. A full page load rather than a
              route, because /app has its own shell and its own queries. */}
          <a
            href="/app"
            className="mb-0.5 flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            <Icon name="overview" className="h-4 w-4 shrink-0" />
            Riverside Auto
          </a>
          <a
            href="/"
            className="mb-0.5 flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            <Icon name="globe" className="h-4 w-4 shrink-0" />
            Marketing site
          </a>
        </nav>

        <OpsFooter />
      </aside>

      <div className="lg:ml-60">
        <OpsTopBar onOpenNav={() => setNavOpen(true)} />
        <Outlet />
      </div>

      {/* Bottom-right, above everything, and each one is dismissible on its
          own. Clicking through opens the entry, which is also what marks it
          read -- so the badge and the toast go together and neither has to be
          cleared twice. */}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(22rem,calc(100vw-2rem))] flex-col gap-2">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className="pointer-events-auto animate-fade-up rounded-lg border border-border bg-card p-3 shadow-xl"
          >
            <div className="flex items-start gap-2.5">
              <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
                <Icon name="calendar" className="h-3.5 w-3.5" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-semibold">{toast.title}</div>
                <div className="truncate text-sm text-muted-foreground">{toast.body}</div>
                <button
                  onClick={() => {
                    dismiss(toast.id)
                    void queryClient.invalidateQueries({ queryKey: ['ops-demos'] })
                    navigate(`/ops?open=${toast.id}`)
                  }}
                  className="mt-1.5 text-xs font-medium text-primary hover:underline"
                >
                  Open it
                </button>
              </div>
              <button
                aria-label="Dismiss"
                onClick={() => dismiss(toast.id)}
                className="-mr-1 -mt-1 h-6 w-6 shrink-0 rounded text-muted-foreground hover:bg-accent"
              >
                &times;
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function OpsLink({
  to,
  label,
  icon,
  end,
  badge,
}: {
  to: string
  label: string
  icon: IconName
  end?: boolean
  badge?: 'unread' | 'unmatched_mail'
}) {
  const { data } = useOpsSummary()
  const count = badge ? data?.[badge] ?? 0 : 0
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        clsx(
          'mb-0.5 flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium transition-colors',
          isActive
            ? 'bg-accent text-accent-foreground'
            : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
        )
      }
    >
      <Icon name={icon} className="h-4 w-4 shrink-0" />
      <span>{label}</span>
      {count ? (
        <span className="tnum ml-auto rounded-full bg-primary px-1.5 py-0.5 text-[10px] font-semibold text-primary-foreground">
          {count}
        </span>
      ) : null}
    </NavLink>
  )
}

function OpsTopBar({ onOpenNav }: { onOpenNav: () => void }) {
  const { data } = useOpsSummary()
  const now = new Date()
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-background/95 px-4 backdrop-blur md:px-6">
      <button
        onClick={onOpenNav}
        aria-label="Open menu"
        className="-ml-1 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground lg:hidden"
      >
        <Icon name="menu" className="h-5 w-5" />
      </button>

      <div className="tnum min-w-0 truncate text-sm text-muted-foreground">
        <span className="font-medium text-foreground">{data?.upcoming ?? 0}</span> upcoming
        {data?.timezone ? <span className="hidden sm:inline"> &middot; times in {data.timezone}</span> : null}
      </div>

      <div className="ml-auto flex items-center gap-2">
        <NotificationBell />
        <div className="hidden h-5 w-px bg-border sm:block" />
        <span className="tnum hidden text-sm text-muted-foreground sm:inline">
          {now.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
        </span>
      </div>
    </header>
  )
}

/**
 * The badge counts requests in `new`, which is the state a row leaves the
 * moment somebody opens it. That is deliberately a state on the row and not a
 * per-person read receipt: there are two of us, and "I have seen it" from
 * either is the answer the other needs. A per-user flag would have the badge
 * arguing with itself across two laptops.
 */
function NotificationBell() {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const boxRef = useRef<HTMLDivElement>(null)
  const { data: summary } = useOpsSummary()
  const { data: demos } = useDemos()
  const setStatus = useSetStatus()

  const unread = (demos?.requests ?? []).filter((r) => r.unread)
  const count = summary?.unread ?? unread.length

  useEffect(() => {
    if (!open) return
    const onClick = (event: MouseEvent) => {
      if (!boxRef.current?.contains(event.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', onClick)
    return () => window.removeEventListener('mousedown', onClick)
  }, [open])

  return (
    <div className="relative" ref={boxRef}>
      <button
        onClick={() => setOpen((was) => !was)}
        aria-label={`Notifications (${count})`}
        className={clsx(
          'relative inline-flex h-9 w-9 items-center justify-center rounded-md transition-colors',
          count
            ? 'text-foreground hover:bg-accent'
            : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
        )}
      >
        <Icon name="bell" className="h-4 w-4" />
        {count ? (
          <span className="tnum absolute right-0.5 top-0.5 min-w-4 rounded-full bg-destructive px-1 text-[10px] font-semibold leading-4 text-destructive-foreground">
            {count}
          </span>
        ) : null}
      </button>

      {open && (
        <div className="absolute right-0 top-11 z-50 w-[min(22rem,calc(100vw-2rem))] overflow-hidden rounded-lg border border-border bg-card shadow-xl">
          <div className="flex items-center justify-between border-b border-border px-3 py-2">
            <span className="text-sm font-semibold">Notifications</span>
            {unread.length > 1 && (
              <button
                onClick={() => {
                  unread.forEach((r) => setStatus.mutate({ id: r.id, status: 'seen' }))
                }}
                className="text-xs text-muted-foreground hover:text-foreground hover:underline"
              >
                Clear all
              </button>
            )}
          </div>
          {!unread.length ? (
            <p className="px-3 py-6 text-center text-sm text-muted-foreground">
              Nothing new. Anything you have opened stays opened.
            </p>
          ) : (
            <ul className="max-h-80 overflow-y-auto">
              {unread.map((request) => (
                <li key={request.id}>
                  <button
                    onClick={() => {
                      setOpen(false)
                      navigate(`/ops?open=${request.id}`)
                    }}
                    className="block w-full border-b border-border px-3 py-2.5 text-left last:border-b-0 hover:bg-accent"
                  >
                    <div className="truncate text-sm font-medium">
                      {request.name || request.email || 'Someone new'}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {request.kind === 'support'
                        ? 'Support request'
                        : request.dealership || 'Demo booked'}
                      {' · '}
                      {relative(request.created_at)}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

function OpsFooter() {
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
      navigate('/login?as=owner')
    },
  })

  return (
    <div className="flex items-center gap-2.5 border-t border-border px-4 py-3">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground">
        {initials(me?.user.name ?? '')}
      </span>
      <div className="min-w-0">
        <div className="truncate text-sm font-medium leading-tight">{me?.user.name}</div>
        <button
          onClick={() => logout.mutate()}
          className="truncate text-xs text-muted-foreground hover:text-foreground hover:underline"
        >
          Liner staff &mdash; sign out
        </button>
      </div>
    </div>
  )
}
