import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'

export interface DealerEvent {
  id: number
  type: string
  payload: Record<string, unknown>
  created_at: string
}

/* Each event invalidates the relevant query keys rather than patching cache by
 * hand. Simpler, and the refetch is cheap against SQLite. */
const INVALIDATES: Record<string, string[]> = {
  'conversation.started': ['overview', 'conversations'],
  'conversation.message': ['conversations'],
  'lead.qualified': ['leads', 'overview'],
  'lead.imported': ['leads', 'overview'],
  'appointment.booked': ['overview', 'appointments', 'leads', 'conversations'],
  'appointment.confirmed': ['overview', 'appointments'],
  'appointment.assigned': ['overview', 'appointments', 'team'],
  'handoff.triggered': ['overview', 'conversations'],
  'outreach.sent': ['appointments', 'leads', 'conversations', 'email-messages', 'timeline'],
  // Mail arriving is the one thing on this dashboard nobody triggered, so it
  // is the one thing that must not wait for a click. `email-receipts` is here
  // as well as `email-messages` because a reply nobody could place has no
  // outreach row -- it exists only as a receipt, and it is exactly the
  // delivery a manager needs to notice.
  'email.received': ['email-messages', 'email-receipts', 'timeline', 'leads', 'conversations'],
  'call.started': ['overview', 'conversations'],
  'call.ended': ['overview', 'conversations'],
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
      socket = new WebSocket(
        `${protocol}://${window.location.host}/ws/dealer?since=${lastId.current}`,
      )

      socket.onmessage = (message) => {
        const event = JSON.parse(message.data) as DealerEvent
        if (event.id) lastId.current = Math.max(lastId.current, event.id)
        if (event.type === 'ready') return

        for (const key of INVALIDATES[event.type] ?? []) {
          void queryClient.invalidateQueries({ queryKey: [key] })
        }
        handler.current?.(event)
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
