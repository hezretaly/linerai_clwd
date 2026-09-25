import { useEffect, useRef } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { withStore } from './store'

export interface DealerEvent {
  id: number
  type: string
  payload: Record<string, unknown>
  created_at: string
  /**
   * This arrived in the `?since=` backlog rather than live.
   *
   * Invalidating a query key is the same either way -- the data really did
   * change. Interrupting somebody is not. Without this flag every page load
   * replays the events table and pops a notification for each demo booked
   * this week, including the ones already opened and answered, which is
   * exactly the tray that teaches people to ignore it.
   *
   * A reconnect after a dropped socket replays too, so a booking made during
   * those two seconds goes uncounted as an interruption. The badge still
   * carries it: that one is a stored state on the row, and it is the channel
   * that is allowed to persist.
   */
  replayed: boolean
}

/* Each event invalidates the relevant query keys rather than patching cache by
 * hand. Simpler, and the refetch is cheap against SQLite.
 *
 * **Every registered event type has a line here, even an empty one.** A buyer
 * writing into a thread emitted `conversation.message`, and that invalidated
 * `conversations` and nothing else -- so the list moved while the buyer page a
 * rep was actually reading, whose timeline is `timeline`, stayed exactly as it
 * was until somebody reloaded it. The page looked like a conversation that had
 * gone quiet while the buyer was still typing. Half the registered types had no
 * line at all and so refreshed nothing. `make smoke` now reads this map against
 * `EVENT_TYPES`, because a type left off it fails silently: the event arrives
 * and nothing on screen moves. */
/**
 * Every query key `/app/email` (`EmailSetup.tsx`) reads from, in one place.
 *
 * `INVALIDATES['outreach.sent']` and `EmailSetup.tsx`'s own `refresh()` each
 * kept a separate hand-written list, and both left out `email-threads` --
 * only `INVALIDATES['email.received']` had it, and the Composer's `onSent`
 * patched the gap for the *sending* tab alone with its own extra
 * invalidate. So a Liner auto-reply, or a colleague's send in a second tab,
 * moved the Sent count and the "Checked just now" line (both read from
 * `email-messages` alone) while the People tabs (`email-threads`) sat
 * frozen -- "Waiting on us" kept counting an already-answered buyer under a
 * freshness line that said the page was current (item 37).
 */
export const MAIL_PAGE_KEYS = ['email-messages', 'email-receipts', 'email-threads', 'timeline']

const INVALIDATES: Record<string, string[]> = {
  'conversation.started': ['overview', 'conversations'],
  'conversation.message': ['conversations', 'timeline', 'overview'],
  'conversation.declined': ['overview', 'conversations', 'timeline'],
  'lead.qualified': ['leads', 'overview', 'timeline'],
  'lead.imported': ['leads', 'overview'],
  'lead.created': ['leads', 'conversations', 'overview'],
  'lead.updated': ['leads', 'lead', 'conversations', 'timeline', 'reach', 'duplicates'],
  'lead.assigned': ['overview', 'leads', 'conversations', 'timeline', 'team'],
  'appointment.booked': ['overview', 'appointments', 'leads', 'conversations', 'timeline'],
  'appointment.confirmed': ['overview', 'appointments', 'timeline'],
  'appointment.cancelled': ['overview', 'appointments', 'leads', 'conversations', 'timeline'],
  'appointment.rescheduled': ['overview', 'appointments', 'timeline'],
  'appointment.assigned': ['overview', 'appointments', 'team', 'timeline'],
  'handoff.triggered': ['overview', 'conversations', 'timeline'],
  'team.deactivated': ['team', 'overview', 'leads', 'conversations', 'appointments'],
  'outreach.sent': ['appointments', 'leads', 'conversations', ...MAIL_PAGE_KEYS],
  'outreach.opened': ['overview', 'leads', 'timeline'],
  // Mail arriving is the one thing on this dashboard nobody triggered, so it
  // is the one thing that must not wait for a click. The ops keys as well: a
  // delivery nobody could place is listed in *our* inbox, and the Unmatched
  // box and the unread count sat stale until somebody clicked, on the one
  // dashboard where mail arriving is the whole point of having it open.
  'email.received': [...MAIL_PAGE_KEYS, 'leads', 'conversations', 'ops-mail', 'ops-summary'],
  'email.agent': ['email-agent'],
  'vehicle.status_changed': ['inventory', 'overview'],
  // Ours, not a dealership's: somebody asking Liner for a demo. Every ops
  // surface reads the same three keys, so a booking made while the calendar is
  // open moves the badge, the day and the inbox together.
  'demo.requested': ['ops-summary', 'ops-demos', 'ops-mail'],
  'demo.updated': ['ops-summary', 'ops-demos', 'ops-mail'],
  'call.started': ['overview', 'conversations', 'timeline'],
  'call.ended': ['overview', 'conversations', 'timeline'],
  'call.transcribed': ['conversations', 'timeline'],
  'phone.started': ['ops-phone'],
  'phone.ended': ['ops-phone'],
  'phone.persona': ['ops-phone'],
  'sms.sent': ['timeline', 'lead-sms', 'conversations', 'leads'],
  'sms.received': ['timeline', 'lead-sms', 'conversations', 'leads', 'ops-phone'],
  'sms.status': ['timeline', 'lead-sms'],
  'sms.opt_out': ['reach', 'lead-sms', 'timeline', 'ops-phone'],
  'sms.resumed': ['reach', 'lead-sms', 'timeline', 'ops-phone'],
  'widget.report': ['widget-installs'],
}

/**
 * Lead rows are a projection of conversation (and escalation, and message)
 * rows -- every field `lead_summaries` computes (`flagged`, `open`, `live`,
 * `last_touch_at`, `conversation_count`, ...) is derived from the same
 * threads an event about a conversation just changed. So an event that
 * invalidates `conversations` has to invalidate `leads` too, or the two
 * pages holding one buyer's data go stale independently.
 *
 * Before this, each `INVALIDATES` line had to remember to list both keys by
 * hand, and several did not -- `handoff.triggered` (a takeover) is the one
 * that matters most: after it, /app/conversations' own rows (`['conversations']`)
 * refreshed correctly, but the *lead* rows the same page also renders
 * (`['leads']`) sat stale until the page was reloaded, so the "Needs a
 * person" flag and the "Unclaimed" chip briefly disagreed with the Overview,
 * which *had* refreshed. One rule here, applied by `invalidateKeys` below,
 * closes it for every event type at once rather than one at a time.
 */
const IMPLIES: Record<string, string[]> = {
  conversations: ['leads'],
}

export function invalidateKeys(queryClient: QueryClient, keys: string[]): void {
  const expanded = new Set(keys)
  for (const key of keys) {
    for (const implied of IMPLIES[key] ?? []) expanded.add(implied)
  }
  for (const key of expanded) {
    void queryClient.invalidateQueries({ queryKey: [key] })
  }
}

/**
 * Dealer event socket. Reconnects with `?since=` so a dashboard that was closed
 * during a booking catches up from the events table instead of refetching all.
 */
export function useDealerEvents(onEvent?: (event: DealerEvent) => void): void {
  const queryClient = useQueryClient()
  const lastId = useRef(0)
  const handler = useRef(onEvent)
  handler.current = onEvent

  useEffect(() => {
    let socket: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout> | undefined
    let closed = false

    const connect = () => {
      if (closed) return
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      // The store prefix goes on the socket too. `/alsbou/ws/dealer` is
      // rewritten by the same middleware as `/alsbou/api/...`; unprefixed, the
      // handler opened the *default* store, could not find this user in it,
      // and closed with 4401 -- so a prefixed dashboard reconnected forever
      // and never received a live event.
      socket = new WebSocket(
        `${protocol}://${window.location.host}${withStore('/ws/dealer')}?since=${lastId.current}`,
      )
      // The server sends the backlog first and `ready` after it, so this flips
      // exactly once per connection and is the only thing that can tell a
      // replayed event from a live one.
      let live = false

      socket.onmessage = (message) => {
        const event = JSON.parse(message.data) as DealerEvent
        if (event.id) lastId.current = Math.max(lastId.current, event.id)
        if (event.type === 'ready') {
          live = true
          return
        }

        invalidateKeys(queryClient, INVALIDATES[event.type] ?? [])
        handler.current?.({ ...event, replayed: !live })
      }

      socket.onclose = () => {
        if (!closed) retry = setTimeout(connect, 2000)
      }
    }

    connect()
    return () => {
      closed = true
      if (retry) clearTimeout(retry)
      socket?.close()
    }
  }, [queryClient])
}
