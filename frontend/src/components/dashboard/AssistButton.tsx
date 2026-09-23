import { useMutation, useQuery } from '@tanstack/react-query'

import { api, ApiError } from '../../lib/api'
import { Button, Unavailable } from '../ui'

export type DraftChannel = 'email' | 'sms' | 'chat'

export interface DraftResult {
  mode: 'generate' | 'polish'
  subject: string
  body: string
  violations: string[]
}

/** Whether the writing assistant can write anything here, asked once and
 *  shared by every composer on the page. */
export function useDraftAvailability() {
  const { data } = useQuery({
    queryKey: ['draft-available'],
    queryFn: () => api.get<{ available: boolean; reason: string }>('/api/drafts/available'),
    staleTime: 5 * 60_000,
  })
  return data
}

/**
 * The writing assistant's one button, beside Send on every composer.
 *
 * **What it does is decided by the box, not by a second button.** Empty, it is
 * *Auto-generate*: the obvious next message from the conversation, the car and
 * the dealership's own answers. With anything typed, it is *Polish*: the rep's
 * words -- a finished draft or three words of notes -- turned into the message
 * they meant, their facts kept. The label says which before it is pressed.
 * There used to be an instruction box, a Write draft, a Rewrite mine and a New
 * draft; the words in the box were always the better steering, and to start
 * again you empty it.
 *
 * Nothing is sent. The text lands in the box, and Send is the same Send.
 * Where there is no model the button is drawn as unavailable with the reason
 * on it, rather than offered and then refused.
 */
export function AssistButton({
  channel,
  text,
  leadId,
  conversationId,
  onDraft,
  onProblem,
}: {
  channel: DraftChannel
  /** What is in the box now: empty generates, anything else is polished. */
  text: string
  leadId?: string | null
  conversationId?: string | null
  onDraft: (result: DraftResult) => void
  /** A sentence to show the rep, or '' to clear the last one. */
  onProblem: (message: string) => void
}) {
  const availability = useDraftAvailability()
  const polishing = Boolean(text.trim())

  const draft = useMutation({
    mutationFn: () =>
      api.post<DraftResult>('/api/drafts', {
        channel,
        text,
        lead_id: leadId ?? '',
        conversation_id: conversationId ?? '',
      }),
    onSuccess: (result) => {
      onProblem('')
      onDraft(result)
    },
    onError: (err: unknown) => {
      // The 503 for "no model configured" carries the setting to change as
      // `detail.detail`; everything else is the server's own sentence.
      const payload = (err as ApiError)?.payload as
        | { detail?: string | { detail?: string } }
        | undefined
      const detail = payload?.detail
      onProblem(
        typeof detail === 'string'
          ? detail
          : detail?.detail || String((err as Error)?.message ?? err),
      )
    },
  })

  const label = polishing ? 'Polish' : 'Auto-generate'
  if (availability && !availability.available) {
    return <Unavailable label={label} size="sm" why={availability.reason} />
  }
  return (
    <Button
      type="button"
      size="sm"
      variant="secondary"
      disabled={draft.isPending || !availability}
      title={
        polishing
          ? 'Liner rewrites what you have typed, keeping your facts. Nothing is sent.'
          : 'Liner writes the next message from this conversation. Nothing is sent.'
      }
      onClick={() => draft.mutate()}
    >
      {draft.isPending ? (polishing ? 'Polishing...' : 'Writing...') : label}
    </Button>
  )
}

/** The guards refused a draft, and the rep is told why rather than handed an
 *  empty box: they may know the figure to be true, and can write it. */
export function Refused({ violations }: { violations: string[] }) {
  if (!violations.length) return null
  return (
    <div className="rounded-md border border-warning/30 bg-warning-muted p-2">
      <p className="text-xs font-medium text-warning-foreground">
        Liner could not source this, so nothing was written:
      </p>
      <ul className="mt-0.5 text-xs text-warning-foreground/90">
        {violations.map((v) => <li key={v}>{v}</li>)}
      </ul>
    </div>
  )
}
