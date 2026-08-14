import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'

import { api } from '../../lib/api'
import { initials } from '../../lib/format'
import type { TeamMember, User } from '../../lib/types'
import { Icon } from '../Icon'

/**
 * Give a buyer to somebody.
 *
 * This replaced a Take over button that only navigated. Navigating is not an
 * act -- it left the buyer unowned, so the same person kept appearing in Needs
 * a person, in What's happening and in Unclaimed leads, and no amount of
 * clicking through to the thread moved them out of any of the three. A rep
 * could work a queue all morning and watch it stay exactly the same length.
 *
 * Assigning is the act, and the server makes it one thing: an owner on the
 * lead, and their open escalations claimed with it, so all three panels settle
 * together.
 *
 * **Assigning is not taking over, and the menu keeps them apart.** Handing a
 * buyer to a rep does not stop Liner -- it goes on answering the other nine
 * questions while that rep gets to them, which is the whole reason escalating
 * does not gag it either. Take over myself is the one entry that does stop it,
 * because that is what the word already means everywhere else here.
 */
export function AssignTo({
  leadId,
  assignedTo,
  thread,
  conversationId,
}: {
  leadId: string | null
  assignedTo?: User | null
  thread: string
  /** The live thread to pause when someone takes it over personally. */
  conversationId?: string | null
}) {
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState({ top: 0, left: 0 })
  const box = useRef<HTMLDivElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const client = useQueryClient()

  const { data: team } = useQuery({
    queryKey: ['team'],
    queryFn: () => api.get<{ members: TeamMember[] }>('/api/team'),
    // Every row on the overview renders one of these, and the roster does not
    // change while a rep triages a queue.
    staleTime: 5 * 60_000,
    enabled: open,
  })
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<{ user: User }>('/api/auth/me'),
    staleTime: 5 * 60_000,
  })

  const assign = useMutation({
    mutationFn: async ({ userId, take }: { userId: string | null; take?: boolean }) => {
      if (leadId) await api.post(`/api/leads/${leadId}/assign`, { user_id: userId })
      // Taking over is the second half and it is a different act: it is what
      // actually stops Liner replying. Ordered after the assignment so a buyer
      // is never left paused and ownerless if the second call fails.
      if (take && conversationId) await api.post(`/api/conversations/${conversationId}/takeover`)
    },
    onSuccess: (_data, variables) => {
      void client.invalidateQueries()
      setOpen(false)
      if (variables.take) navigate(thread)
    },
  })

  /* Placed from the button's own rectangle, every time it opens.
   *
   * The menu is portalled to <body> rather than positioned inside the row,
   * because every panel it appears in scrolls sideways on a narrow screen --
   * `overflow-x: auto` is what stops a wide table shrinking the whole page --
   * and an overflow container clips its children in *both* directions. Sitting
   * in the row, the menu was cut off at the edge of the card: the first entry
   * showed and the reps did not. */
  useLayoutEffect(() => {
    if (!open) return
    const place = () => {
      const anchor = box.current?.getBoundingClientRect()
      if (!anchor) return
      const height = menu.current?.offsetHeight ?? 260
      const WIDTH = 224
      // Upwards when there is no room below -- these rows are often near the
      // bottom of a long queue, which is exactly where a menu drawn downwards
      // runs off the screen.
      const below = window.innerHeight - anchor.bottom
      setAt({
        top: below < height + 8 && anchor.top > height ? anchor.top - height - 4 : anchor.bottom + 4,
        left: Math.max(8, Math.min(anchor.right - WIDTH, window.innerWidth - WIDTH - 8)),
      })
    }
    place()
    // Fixed coordinates go stale the moment anything moves, and both of these
    // happen while a menu is open: the panel behind it scrolls, or the window
    // is resized.
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open])

  // Clicking away closes it. Without this the menu survives the row navigation
  // underneath it and hangs over the next screen.
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => {
      const target = e.target as Node
      if (!box.current?.contains(target) && !menu.current?.contains(target)) setOpen(false)
    }
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  // An anonymous thread has nobody to assign -- a lead is minted when something
  // books, so most live chats have none. Saying "Assign to" over a menu that
  // could only fail is worse than offering the one thing that does work.
  if (!leadId) {
    return (
      <button
        onClick={(e) => { e.stopPropagation(); navigate(thread) }}
        className="inline-flex h-8 items-center whitespace-nowrap rounded-md border border-input px-3 text-xs font-medium transition-colors hover:bg-muted"
      >
        Open
      </button>
    )
  }

  const reps = team?.members ?? []
  const mine = assignedTo?.id && me?.user.id === assignedTo.id

  return (
    <div ref={box} className="relative inline-block text-left" onClick={(e) => e.stopPropagation()}>
      <button
        onClick={() => setOpen((was) => !was)}
        disabled={assign.isPending}
        aria-haspopup="menu"
        aria-expanded={open}
        className={clsx(
          'inline-flex h-8 max-w-[8.5rem] items-center gap-1.5 whitespace-nowrap rounded-md px-3 text-xs font-medium transition-opacity hover:opacity-90 disabled:opacity-60',
          assignedTo
            ? 'border border-input bg-background text-foreground'
            : 'bg-primary text-primary-foreground',
        )}
      >
        {assignedTo ? (
          <>
            <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-muted text-[9px] font-semibold text-muted-foreground">
              {initials(assignedTo.name)}
            </span>
            <span className="truncate">{mine ? 'You' : assignedTo.name.split(' ')[0]}</span>
          </>
        ) : (
          'Assign to'
        )}
        <svg viewBox="0 0 20 20" className="h-3 w-3 shrink-0 opacity-60" aria-hidden>
          <path d="M5 8l5 5 5-5" fill="none" stroke="currentColor" strokeWidth="2" />
        </svg>
      </button>

      {open && createPortal(
        <div
          ref={menu}
          role="menu"
          style={{ top: at.top, left: at.left }}
          onClick={(e) => e.stopPropagation()}
          className="fixed z-50 w-56 overflow-hidden rounded-md border border-border bg-background py-1 text-left shadow-lg"
        >
          {conversationId && (
            <>
              <button
                role="menuitem"
                onClick={() => assign.mutate({ userId: me?.user.id ?? null, take: true })}
                className="flex w-full items-center gap-2 px-3 py-2 text-xs font-medium hover:bg-muted"
              >
                <Icon name="user" className="h-3.5 w-3.5 shrink-0" />
                Take over myself
              </button>
              {/* Said here rather than discovered afterwards. Every other entry
                  leaves Liner working; this one is the only thing on the page
                  that stops it. */}
              <div className="px-3 pb-1.5 text-[11px] leading-tight text-muted-foreground">
                Assigns it to you and stops Liner replying.
              </div>
              <div className="my-1 border-t border-border" />
            </>
          )}

          <div className="px-3 pb-1 pt-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Assign to
          </div>
          {reps.length === 0 && (
            <div className="px-3 py-2 text-xs text-muted-foreground">Loading the team…</div>
          )}
          {reps.map((member) => (
            <button
              key={member.id}
              role="menuitem"
              onClick={() => assign.mutate({ userId: member.id })}
              className="flex w-full items-center gap-2 px-3 py-2 text-xs hover:bg-muted"
            >
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-muted text-[9px] font-semibold text-muted-foreground">
                {initials(member.name)}
              </span>
              <span className="min-w-0 flex-1 truncate text-left">{member.name}</span>
              {/* Their load today, because "assign to whoever" is a decision a
                  rep makes better with it than without. Not a rule -- there is
                  no round robin here, and a full rep is still assignable. */}
              <span className="tnum shrink-0 text-[11px] text-muted-foreground">
                {member.todays_appointments}
                {member.at_capacity && ' · full'}
              </span>
              {assignedTo?.id === member.id && (
                <Icon name="check" className="h-3.5 w-3.5 shrink-0 text-primary" />
              )}
            </button>
          ))}

          {assignedTo && (
            <>
              <div className="my-1 border-t border-border" />
              <button
                role="menuitem"
                onClick={() => assign.mutate({ userId: null })}
                className="flex w-full items-center gap-2 px-3 py-2 text-xs text-muted-foreground hover:bg-muted"
              >
                <Icon name="back" className="h-3.5 w-3.5 shrink-0" />
                Put back in the queue
              </button>
            </>
          )}
        </div>,
        document.body,
      )}
    </div>
  )
}
