import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'

import { api } from '../../lib/api'
import { dateTime } from '../../lib/format'
import { Badge, Button, Input, Sheet } from '../ui'
import type { Lead } from '../../lib/types'
import type { TimelineEntry } from './Timeline'

/* One email, opened and readable, with the reply written underneath it.
 *
 * **An email is the one timeline entry that is routinely longer than the
 * timeline.** A chat message is a sentence and a call entry is a header over a
 * recording, so both fit; an email has a subject, several paragraphs and a
 * signature, and the card in the timeline showed the subject `truncate`d and
 * the body under `line-clamp-3` with nothing to press. A rep could not read a
 * buyer's email on the page this dashboard tells them to work from -- they had
 * to go and find it in their own mail client, which is exactly where a reply
 * becomes invisible to this system for good.
 *
 * **The reply is written with the message still on screen, not instead of it.**
 * A composer that replaces what it is answering makes somebody hold two
 * paragraphs in their head while they type, and the detail they were answering
 * is the one they get wrong.
 *
 * There is no rich text and no HTML. What goes out is what is in the box plus
 * the dealership's own sign-off, which is appended server-side -- so the
 * preview below the composer is the message, not an approximation of it.
 */

export function EmailReader({
  entry,
  lead,
  signature,
  onClose,
  onSent,
}: {
  entry: TimelineEntry | null
  lead: Lead
  /** The dealership's sign-off, served rather than typed. Shown because a
   *  preview of what is actually sent is the only honest kind. */
  signature: string
  onClose: () => void
  onSent: () => void
}) {
  const inbound = entry?.direction === 'in'
  const parent = entry?.subject ?? ''
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [problem, setProblem] = useState('')
  const [replying, setReplying] = useState(false)

  const send = useMutation({
    mutationFn: () =>
      api.post<{ status: string; error?: string }>('/api/email/compose', {
        to: lead.email,
        subject,
        body,
        lead_id: lead.id,
        in_reply_to_outreach_id: entry?.id,
      }),
    onSuccess: (result) => {
      // A refusal comes back as a stored failed row rather than an error, and
      // the sentence names the setting that would lift it. Showing it beats a
      // green tick over mail that never left the building.
      if (result.status !== 'sent') {
        setProblem(result.error || 'The provider did not accept it.')
        return
      }
      setBody('')
      setProblem('')
      setReplying(false)
      onSent()
      onClose()
    },
    onError: (err: unknown) => setProblem(String((err as Error)?.message ?? err)),
  })

  const openReply = () => {
    // Not "Re: Re: Re:". A buyer who answers four times should not end up with
    // a subject line that is mostly prefix.
    setSubject(parent ? (/^re:/i.test(parent) ? parent : `Re: ${parent}`) : '')
    setBody('')
    setProblem('')
    setReplying(true)
  }

  if (!entry) return null

  return (
    <Sheet
      open
      onClose={onClose}
      width="w-[40rem]"
      title={<h2 className="truncate text-sm font-semibold">{entry.subject || '(no subject)'}</h2>}
    >
      <div className="space-y-4 p-4">
        {/* The envelope, spelled out. Who it was between and when is the first
            thing a rep checks and the thing a chat bubble cannot carry. */}
        <div className="rounded-md border border-border bg-muted/40 p-3 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={inbound ? 'primary' : 'neutral'}>
              {inbound ? 'From the buyer' : 'Sent by the dealership'}
            </Badge>
            {entry.status === 'failed' && <Badge tone="destructive">not sent</Badge>}
            <span className="tnum ml-auto text-muted-foreground">{dateTime(entry.at)}</span>
          </div>
          <dl className="mt-2 space-y-0.5 text-muted-foreground">
            <div className="flex gap-2">
              <dt className="w-12 shrink-0">From</dt>
              <dd className="min-w-0 break-all text-foreground">
                {inbound ? entry.to_address || lead.email : 'the dealership'}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-12 shrink-0">To</dt>
              <dd className="min-w-0 break-all text-foreground">
                {inbound ? 'the dealership' : entry.to_address || lead.email}
              </dd>
            </div>
          </dl>
          {entry.error && <p className="mt-2 text-destructive">{entry.error}</p>}
        </div>

        {/* **Whole, wrapped, and never clipped.** This is the reason the sheet
            exists: `line-clamp-3` in the timeline is a summary, and there was
            nowhere the full thing could be read. */}
        <div className="whitespace-pre-wrap break-words text-sm leading-relaxed">
          {entry.body || <span className="text-muted-foreground">(no body)</span>}
        </div>

        {!replying ? (
          lead.email ? (
            <div className="border-t border-border pt-3">
              <Button variant="primary" size="sm" onClick={openReply}>
                Reply
              </Button>
            </div>
          ) : (
            <p className="border-t border-border pt-3 text-xs text-muted-foreground">
              No address on file for this buyer, so there is nowhere to reply to.
            </p>
          )
        ) : (
          <div className="border-t border-border pt-3">
            <p className="mb-2 text-xs text-muted-foreground">
              Replying to <span className="font-medium text-foreground">{lead.email}</span>
              {parent ? ` · under "${parent}"` : ' · this starts a new thread in their inbox'}
            </p>
            <Input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Subject"
              className="mb-2"
            />
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={8}
              autoFocus
              placeholder="Write the reply..."
              className="w-full resize-y rounded-md border border-input bg-background p-2 text-sm outline-none focus:border-ring focus:ring-1 focus:ring-ring"
            />
            {/* Shown, not typed. The sign-off is the dealership's own details
                and it is appended on the way out, so a rep who typed one as
                well would send two -- and a rep who typed one from memory
                would eventually get the phone number wrong. */}
            {signature && (
              <div className="mt-2 rounded-md border border-dashed border-border bg-muted/30 p-2">
                <p className="text-[11px] font-medium text-muted-foreground">
                  Sent with this sign-off
                </p>
                <p className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">
                  {signature}
                </p>
              </div>
            )}
            {problem && <p className="mt-1.5 text-xs text-destructive">{problem}</p>}
            <div className="mt-2 flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setReplying(false)}>
                Cancel
              </Button>
              <Button
                size="sm"
                variant="primary"
                disabled={!body.trim() || send.isPending}
                onClick={() => send.mutate()}
              >
                {send.isPending ? 'Sending...' : 'Send email'}
              </Button>
            </div>
          </div>
        )}
      </div>
    </Sheet>
  )
}
