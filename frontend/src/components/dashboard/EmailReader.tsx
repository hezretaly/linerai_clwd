import clsx from 'clsx'
import { useId, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'

import { api, ApiError } from '../../lib/api'
import { withStore } from '../../lib/store'
import {
  fwdSubject,
  quotedBody,
  recipientCount,
  replyAllAddsSomeone,
  reSubject,
  type Attachment,
  type EmailSummary,
  type Importance,
  type MailContent,
} from '../../lib/email'
import {
  AttachmentPicker,
  CopyFields,
  EmailView,
  RecipientInput,
  RichEditor,
  type RecipientSuggestion,
} from '../email'
import { Icon } from '../Icon'
import { Badge, Button, Input, Sheet, Spinner } from '../ui'
import type { Lead } from '../../lib/types'
import type { TimelineEntry } from './Timeline'

/* One email, opened and readable, with the answer written underneath it.
 *
 * **An email is the one timeline entry that is routinely longer than the
 * timeline.** A chat message is a sentence and a call entry is a header over a
 * recording, so both fit; an email has a subject, several paragraphs, people
 * copied on it and files, and the card in the timeline is a summary of all
 * that. A rep who could not read the whole thing here went to their own mail
 * client, which is exactly where a reply becomes invisible to this system for
 * good.
 *
 * **What is read is what the server kept, not what the card carried.** The
 * body, the envelope and the files come from `GET /api/email/read/message/...`
 * -- the whole text/plain half rather than the trimmed one the card shows, the
 * HTML cleaned twice and drawn in a sandboxed frame, and every file with a
 * link to its bytes. Remote images stay off until somebody asks, because
 * loading one tells the sender the message was opened.
 *
 * **The answer is written with the message still on screen, not instead of
 * it.** A composer that replaces what it is answering makes somebody hold two
 * paragraphs in their head while they type, and the detail they were answering
 * is the one they get wrong. Reply, Reply all and Forward all open the same
 * composer here, addressed from what the server worked out (Reply-To before
 * From, ourselves never put back on a reply-all, nobody Bcc'd carried
 * forward), and all three send through `/api/email/compose`: the one endpoint
 * the mailbox uses too, so a reply from this page and one from the mailbox
 * cannot thread differently.
 */

// ------------------------------------------------------------- composing

/** A message being written: everything a send carries, held as the boxes
 *  hold it. `to`, `cc` and `bcc` are one string each -- chips and whatever is
 *  still being typed -- and the server splits them the way the chips were
 *  drawn. `html` is the editor's and `text` its plain half, kept together so
 *  a caller deciding whether Send can be pressed reads the same message the
 *  server will. */
export type MailDraft = {
  to: string
  cc: string
  bcc: string
  subject: string
  html: string
  text: string
  attachments: Attachment[]
  importance: Importance
}

export function emptyDraft(to = ''): MailDraft {
  return {
    to,
    cc: '',
    bcc: '',
    subject: '',
    html: '',
    text: '',
    attachments: [],
    importance: 'normal',
  }
}

/** Whether a draft can go: somebody to send it to, and something in it. The
 *  server's own rule -- a subject, a body or a file -- so the button and the
 *  400 never disagree about an attachment-only message. */
export function sendable(d: MailDraft): boolean {
  return (
    recipientCount(d.to) > 0 &&
    Boolean(d.subject.trim() || d.text.trim() || d.attachments.length)
  )
}

/** The fields every send endpoint takes, from a draft. `body` is the text
 *  half; the server writes the stored text from `html` when both come, so the
 *  two cannot say different things. */
export function draftPayload(d: MailDraft) {
  return {
    to: d.to,
    cc: d.cc,
    bcc: d.bcc,
    subject: d.subject,
    body: d.text,
    html: d.html,
    attachment_ids: d.attachments.map((a) => a.id),
    importance: d.importance,
  }
}

/**
 * The boxes of one email: To, Cc and Bcc, Subject, the body, the sign-off it
 * will carry, and its files -- in that order, which is the order a person
 * fills them in.
 *
 * **Shared by the footer on the buyer page and the reader's answer.** Two
 * composers written separately is how one of them grows Cc and the other
 * does not, which is how this page came to have a reply that could only ever
 * go to one address. What differs between them -- where a draft comes from,
 * which endpoint it goes to -- stays with the caller.
 *
 * **The sign-off is shown, not typed.** It is appended on the way out, so a
 * rep who could not see it typed their name again or wondered why it was
 * missing; `signature` is "" where nothing is appended.
 */
export function ComposeFields({
  draft,
  onChange,
  suggestions,
  signature = '',
  placeholder = 'Write the message...',
  minHeight = 160,
  focusTo = false,
}: {
  draft: MailDraft
  /** Only what changed. Callers merge it into the newest draft, because the
   *  editor reports its normalised text a render after a value is set. */
  onChange: (patch: Partial<MailDraft>) => void
  suggestions?: RecipientSuggestion[]
  signature?: string
  placeholder?: string
  /** px, for the editor. */
  minHeight?: number
  focusTo?: boolean
}) {
  const toId = useId()
  return (
    <div className="min-w-0 space-y-2">
      {/* The same label column CopyFields gives Cc and Bcc, so the three
          address rows line up. */}
      <div className="flex min-w-0 items-start gap-2">
        <label htmlFor={toId} className="w-8 shrink-0 pt-2 text-xs font-medium text-muted-foreground">
          To
        </label>
        <div className="min-w-0 flex-1">
          <RecipientInput
            id={toId}
            ariaLabel="To"
            value={draft.to}
            onChange={(to) => onChange({ to })}
            suggestions={suggestions}
            autoFocus={focusTo}
            placeholder="Their address"
          />
        </div>
      </div>
      <CopyFields
        cc={draft.cc}
        bcc={draft.bcc}
        onCc={(cc) => onChange({ cc })}
        onBcc={(bcc) => onChange({ bcc })}
        suggestions={suggestions}
      />
      <Input
        aria-label="Subject"
        value={draft.subject}
        onChange={(e) => onChange({ subject: e.target.value })}
        placeholder="Subject"
      />
      <RichEditor
        value={draft.html}
        onChange={(html, text) => onChange({ html, text })}
        placeholder={placeholder}
        minHeight={minHeight}
        ariaLabel="Message"
      />
      {signature && (
        <div className="rounded-md border border-dashed border-border bg-muted/30 p-2">
          <p className="text-[11px] font-medium text-muted-foreground">Sent with this sign-off</p>
          <p className="mt-1 whitespace-pre-wrap break-words text-xs text-muted-foreground">
            {signature}
          </p>
        </div>
      )}
      <AttachmentPicker
        value={draft.attachments}
        onChange={(attachments) => onChange({ attachments })}
      />
    </div>
  )
}

/** `Importance: high` on the way out -- the flag a buyer's mail client draws
 *  beside the subject. A toggle rather than a menu, because there are two
 *  states worth sending and "low" is one nobody here would choose. */
export function ImportanceToggle({
  value,
  onChange,
  className,
}: {
  value: Importance
  onChange: (value: Importance) => void
  className?: string
}) {
  const high = value === 'high'
  return (
    <button
      type="button"
      aria-pressed={high}
      onClick={() => onChange(high ? 'normal' : 'high')}
      title="Marks the message as important in the recipient's mail client."
      className={clsx(
        'inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors duration-150',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        high
          ? 'border-warning/40 bg-warning-muted text-warning-foreground'
          : 'border-border bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground',
        className,
      )}
    >
      <Icon name="alert" className="h-3.5 w-3.5 shrink-0" />
      High importance
    </button>
  )
}

/** What a send endpoint answers. A refusal is a stored failed row with the
 *  sentence that names the setting, not an HTTP error. */
export type SendResult = {
  status: string
  error?: string | null
  blocked?: boolean
  email?: EmailSummary | null
}

/** The reason a send did not happen, in the server's own words. A refusal
 *  the server wrote -- the address it could not read, the file it would not
 *  carry -- is already the error's message; a 422 is FastAPI's list of what
 *  did not validate, whose first line says more than "POST ... failed". */
export function sendProblem(err: unknown): string {
  if (err instanceof ApiError) {
    const detail = (err.payload as { detail?: unknown } | null)?.detail
    if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg)
  }
  return err instanceof Error ? err.message : String(err)
}

// ---------------------------------------------------------------- reader

type Answer = 'reply' | 'reply_all' | 'forward'

const ANSWER_TITLE: Record<Answer, string> = {
  reply: 'Reply',
  reply_all: 'Reply all',
  forward: 'Forward',
}

export function EmailReader({
  entry,
  lead,
  signature,
  suggestions,
  onClose,
  onSent,
}: {
  entry: TimelineEntry | null
  lead: Lead
  /** This person's sign-off, served rather than typed. Shown because a
   *  preview of what is actually sent is the only honest kind. */
  signature: string
  /** Addresses to offer as somebody types: the buyer's, then colleagues'. */
  suggestions?: RecipientSuggestion[]
  onClose: () => void
  onSent: () => void
}) {
  const inbound = entry?.direction === 'in'
  /** Remote images, once somebody has asked for them. Off by default: a
   *  tracking pixel is a remote image, and loading it tells the sender this
   *  was opened. */
  const [images, setImages] = useState(false)
  const [answering, setAnswering] = useState<Answer | null>(null)
  const [draft, setDraft] = useState<MailDraft>(() => emptyDraft())
  const [problem, setProblem] = useState('')
  const patch = (p: Partial<MailDraft>) => setDraft((d) => ({ ...d, ...p }))

  const id = entry?.id ?? ''
  const { data: content, error, isLoading } = useQuery({
    queryKey: ['email-read', 'message', id, images],
    queryFn: () =>
      api.get<MailContent>(
        `/api/email/read/message/${encodeURIComponent(id)}?images=${images ? 1 : 0}`,
      ),
    enabled: Boolean(id),
    // Asking for the images again must not blank the message while it
    // loads -- but only the same message's: another email's body standing in
    // for this one, even for a moment, is the wrong email on screen.
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[2] === id ? previous : undefined,
    staleTime: 60_000,
  })

  const send = useMutation({
    mutationFn: (how: Answer) =>
      api.post<SendResult>('/api/email/compose', {
        ...draftPayload(draft),
        ...(how === 'forward'
          ? // **A forward is not filed on this buyer.** It goes to somebody
            // else about them -- a finance manager, a colleague -- and on
            // their timeline it would read as the dealership answering
            // them, which is the one thing it is not: the exchange counter
            // would stop saying they are waiting. The server matches the
            // first To instead, so a forward to the buyer's own address
            // still lands here.
            { forward_of: { kind: 'message', id } }
          : { lead_id: lead.id, in_reply_to_outreach_id: id }),
      }),
    onSuccess: (result) => {
      // A refusal comes back as a stored failed row rather than an error, and
      // the sentence names the setting that would lift it. Showing it beats a
      // green tick over mail that never left the building.
      if (result.status !== 'sent') {
        setProblem(result.error || 'The provider did not accept it.')
        return
      }
      setProblem('')
      setAnswering(null)
      onSent()
      onClose()
    },
    onError: (err: unknown) => setProblem(sendProblem(err)),
  })

  const open = (how: Answer) => {
    if (!content) return
    const target = how === 'reply' ? content.reply : how === 'reply_all' ? content.reply_all : null
    const forwarded = new Set(content.forward.attachment_ids)
    setDraft({
      ...emptyDraft(target ? target.to.join(', ') : ''),
      cc: target ? target.cc.join(', ') : '',
      subject: target
        ? target.subject || reSubject(content.subject)
        : content.forward.subject || fwdSubject(content.subject),
      // A blank first paragraph, then what is being answered: the caret
      // belongs above the quote, which is where a reply is read.
      html: quotedBody(content, how === 'forward' ? 'forward' : 'reply'),
      // The files come along on a forward and not on a reply -- sending
      // somebody their own attachments back is noise. The rep can take any
      // of them off before it goes.
      attachments:
        how === 'forward' ? content.attachments.filter((a) => forwarded.has(a.id)) : [],
    })
    setProblem('')
    setAnswering(how)
  }

  if (!entry) return null

  return (
    <Sheet
      open
      onClose={onClose}
      width="w-[40rem]"
      title={<h2 className="break-words text-sm font-semibold">{entry.subject || '(no subject)'}</h2>}
    >
      <div className="min-w-0 space-y-4">
        {/* Which way it went and whether it left, before the envelope: the
            first thing a rep checks, and the one thing the envelope below
            cannot say. */}
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge tone={inbound ? 'primary' : 'neutral'}>
            {inbound ? 'From the buyer' : 'Sent by the dealership'}
          </Badge>
          {entry.status === 'failed' && <Badge tone="destructive">not sent</Badge>}
          {!inbound && entry.status !== 'failed' && entry.delivered_externally === false && (
            <span className="text-muted-foreground">Recorded here only, not delivered</span>
          )}
        </div>
        {entry.error && <p className="text-xs text-destructive">{entry.error}</p>}

        {content ? (
          <EmailView
            message={content}
            resolve={withStore}
            onShowImages={() => setImages(true)}
            showBcc
          />
        ) : isLoading ? (
          <Spinner label="Opening the message" />
        ) : (
          // The stored text is still worth reading when the full message
          // cannot be had -- it is what the timeline holds, said to be that.
          <div className="space-y-2">
            <p className="text-xs text-destructive">
              The full message could not be opened
              {error ? `: ${(error as Error).message}` : '.'} This is the text the timeline
              keeps.
            </p>
            <div className="whitespace-pre-wrap break-words text-sm leading-relaxed">
              {entry.body || <span className="text-muted-foreground">(no body)</span>}
            </div>
          </div>
        )}

        {content && !answering && (
          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
            <Button variant="primary" size="sm" onClick={() => open('reply')}>
              <Icon name="reply" className="h-4 w-4" />
              Reply
            </Button>
            {/* Only when it would reach somebody Reply does not. On a
                one-to-one message it is the same button twice, and a rep
                pressing the wrong one of two identical buttons is how a
                second person gets copied on something by habit. */}
            {replyAllAddsSomeone(content) && (
              <Button size="sm" onClick={() => open('reply_all')}>
                <Icon name="replyAll" className="h-4 w-4" />
                Reply all
              </Button>
            )}
            <Button size="sm" onClick={() => open('forward')}>
              <Icon name="forward" className="h-4 w-4" />
              Forward
            </Button>
          </div>
        )}

        {content && answering && (
          <section aria-label={ANSWER_TITLE[answering]} className="space-y-2 border-t border-border pt-3">
            <h3 className="text-sm font-medium">{ANSWER_TITLE[answering]}</h3>
            <p className="text-xs text-muted-foreground">
              {answering === 'forward'
                ? 'Passed on with its files. Sent to anyone but this buyer, it is kept in the mailbox’s Sent tab rather than on this timeline.'
                : `Under "${content.subject || '(no subject)'}", so it stays one thread in their inbox.`}
            </p>
            <ComposeFields
              draft={draft}
              onChange={patch}
              suggestions={suggestions}
              signature={signature}
              placeholder={answering === 'forward' ? 'Add a note (optional)...' : 'Write the reply...'}
              focusTo={answering === 'forward'}
            />
            {problem && <p className="text-xs text-destructive">{problem}</p>}
            <div className="flex flex-wrap items-center justify-end gap-2">
              <ImportanceToggle
                value={draft.importance}
                onChange={(importance) => patch({ importance })}
                className="mr-auto"
              />
              <Button size="sm" variant="ghost" onClick={() => setAnswering(null)}>
                Cancel
              </Button>
              <Button
                size="sm"
                variant="primary"
                disabled={!sendable(draft) || send.isPending}
                onClick={() => send.mutate(answering)}
              >
                {send.isPending ? 'Sending...' : 'Send email'}
              </Button>
            </div>
          </section>
        )}
      </div>
    </Sheet>
  )
}
