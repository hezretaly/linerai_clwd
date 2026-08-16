/**
 * What `/api/ops` returns, and the three hooks every ops page reads it with.
 *
 * One place, because the badge in the nav, the calendar and the inbox all
 * answer the same question -- how many of these has nobody opened -- and three
 * copies of that predicate is how a bell saying 2 sits above a list showing 3.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../../lib/api'

export interface OpsSummary {
  unread: number
  upcoming: number
  unmatched_mail: number
  support_email: string
  founder_email: string
  /** Where an answer from *this* signed-in person comes back to.
   *  Computed server-side by the same function the send uses -- a
   *  composer promising one return address while the send sets another
   *  is a lie nobody would ever catch. */
  reply_to: string
  /** What actually goes in the From header, display name included. */
  from_address: string
  /** True when mail really leaves under this person's own name. */
  from_is_personal: boolean
  /** Why it does not, when it does not -- the one thing here somebody can fix. */
  from_note: string
  sender: string
  sender_delivers: boolean
  timezone: string
}

export interface DemoEntry {
  id: string
  /** demo | support. A support request has no slot, so it is never on a day. */
  kind: string
  name: string
  dealership: string
  email: string
  phone: string
  dealership_url: string
  message: string
  slot_at: string | null
  consented_at: string | null
  consent_text: string
  status: string
  unread: boolean
  created_at: string
}

export interface MailMessage {
  id: string
  /** form (a marketing-site submission) | email (arrived and never resolved). */
  source: string
  kind: string
  from_name: string
  from_address: string
  subject: string
  body: string
  at: string | null
  unread: boolean
  status: string
  slot_at: string | null
  phone: string
  dealership: string
  dealership_url: string
}

export interface MailBox {
  box: string
  counts: Record<string, number>
  messages: MailMessage[]
}

export function useOpsSummary() {
  return useQuery({
    queryKey: ['ops-summary'],
    queryFn: () => api.get<OpsSummary>('/api/ops/summary'),
  })
}

export function useDemos() {
  return useQuery({
    queryKey: ['ops-demos'],
    queryFn: () => api.get<{ requests: DemoEntry[] }>('/api/ops/demos'),
  })
}

/**
 * Move one request between states.
 *
 * `seen` is the one that clears a notification, and it is fired by *opening*
 * an entry rather than by a button. A notification you have read and that is
 * still sitting there is one you start ignoring, and this dashboard has
 * exactly one thing on it that nobody clicked for.
 */
export function useSetStatus() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api.post<DemoEntry>(`/api/ops/demos/${id}/status`, { status }),
    onSuccess: () => {
      for (const key of ['ops-summary', 'ops-demos', 'ops-mail']) {
        void queryClient.invalidateQueries({ queryKey: [key] })
      }
    },
  })
}
