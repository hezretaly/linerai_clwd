/**
 * Mail addressed to us.
 *
 * A different pile from `/app/email`, which is the dealership's: replies that
 * resolved to one of *their* buyers. This one is the marketing site's forms
 * plus anything that arrived at the inbound endpoint and resolved to nobody --
 * which is exactly what a stranger writing to `support@` looks like.
 *
 * Three panes rather than a mail client dependency. What a mail client brings
 * is MIME parsing, threading and a folder tree; the parsing happens on the
 * server, there are no folders, and a form submission has no thread. Pulling
 * one in would have meant fitting our two sources to its message shape --
 * more work than the list, and a second definition of what a box contains.
 * The pieces a message is written and read with are the same ones the
 * dealership's pages use (`components/email`), so a Cc or an attachment is
 * one thing on every screen.
 */

import { useEffect, useId, useState } from 'react'
import clsx from 'clsx'
import { useMutation } from '@tanstack/react-query'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { api, ApiError } from '../../lib/api'
import { dateTime, relative } from '../../lib/format'
import {
  addrList,
  bareAddress,
  fwdSubject,
  otherRecipients,
  parseEntry,
  quotedBody,
  reSubject,
  replyAllAddsSomeone,
  textToHtml,
  type Attachment,
  type EmailSummary,
  type Importance,
  type MailContent,
  type MailKind,
} from '../../lib/email'
import { Badge, Button, Card, Empty, Field, FieldGroup, Input, Spinner } from '../../components/ui'
import {
  AttachmentPicker,
  CopyFields,
  EmailView,
  RecipientInput,
  RichEditor,
  type RecipientSuggestion,
} from '../../components/email'
import { Icon } from '../../components/Icon'
import {
  markKind,
  OPS_ATTACHMENTS,
  useMailContent,
  useMailMark,
  useOpsSummary,
  type MailBox,
  type MailMessage,
  type OpsSummary,
} from './data'

const BOXES = [
  { id: 'all', label: 'Inbox' },
  { id: 'unread', label: 'Unread' },
  { id: 'demos', label: 'Demos' },
  { id: 'support', label: 'Support' },
  // Mail that arrived and matched nobody. It has no buyer page to appear on,
  // which is the entire reason it needs a box of its own.
  { id: 'unmatched', label: 'Unmatched' },
  // What we wrote. Drafts are the author's own -- an unfinished message is
  // not something to put in front of somebody else -- while Sent is shared,
  // because "has anyone answered these people yet" is what two people sharing
  // an inbox actually ask.
  { id: 'drafts', label: 'Drafts' },
  { id: 'sent', label: 'Sent' },
  // Defined by the mark rather than the source, so a discarded draft and a
  // binned form land in the same place a person looks for them.
  { id: 'trash', label: 'Trash' },
] as const

/** Why a box is empty, which is not the same sentence for all of them --
 *  an empty Unmatched is the good outcome, an empty Drafts is just tidy. */
const EMPTY_HINT: Record<string, string> = {
  all: 'Forms on the marketing site land here the moment they are sent.',
  unread: 'Everything here has been opened.',
  demos: 'Nobody has booked a demo yet.',
  support: 'Nobody has written in for help.',
  unmatched: 'Mail that arrives and matches nobody lands here. Empty is the good outcome.',
  drafts: 'Nothing half-written. Write starts one, and Save draft keeps it.',
  sent: 'Nothing has gone out from here yet.',
  trash: 'Nothing binned. Trash keeps what you put in it -- Restore puts it back.',
}

/** The query keys a write here can change. `ops-mail-read` is in it because
 *  saving a draft changes the draft a reader may have open. */
const MAIL_KEYS = ['ops-summary', 'ops-mail', 'ops-mail-read']

export function OpsMailPage() {
  const [writing, setWriting] = useState<Draft | null>(null)
  /* Bumped every time a composer is opened, and part of its key.
   *
   * Without it, opening Write twice in a row reuses the same component: React
   * keys on `id ?? 'new'`, which is 'new' both times, so the fields still hold
   * the last message and the "Sent" line from it sits above a blank one. Two
   * different acts have to be two different components. */
  const [writeSeq, setWriteSeq] = useState(0)
  const compose = (draft: Draft) => {
    setWriteSeq((n) => n + 1)
    setWriting(draft)
  }
  const [box, setBox] = useState<string>('all')
  const [openId, setOpenId] = useState<string | null>(null)
  const [held, setHeld] = useState<MailMessage | null>(null)
  const { data: summary } = useOpsSummary()
  const mark = useMailMark()

  const { data, isLoading } = useQuery({
    queryKey: ['ops-mail', box],
    queryFn: () => api.get<MailBox>(`/api/ops/mail?box=${box}`),
  })

  const messages = data?.messages ?? []
  /* Held separately so reading one does not make it disappear mid-sentence.
   *
   * Opening a message marks it read, which drops it out of Unread -- and with
   * the reader derived from the list alone, the pane it was being read in
   * unmounted underneath the person reading it. The same happens on Trash.
   * The list is still the source of truth while the row is in it, so an
   * `unread` or `trashed` change is picked up; `held` only covers the moment
   * after it leaves. */
  const open = messages.find((m) => m.id === openId) ?? held

  // Same rule as the calendar: reading it is what clears it, not a button.
  // Every kind now, rather than forms only -- an unresolved delivery used to
  // arrive already marked read, so the one box holding mail from strangers
  // was the one that could never tell you which of it was new.
  useEffect(() => {
    if (open && open.unread) {
      mark.read.mutate({ kind: markKind(open), id: open.id, read: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open?.id, open?.unread])

  return (
    <div className="p-4 md:p-6">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight">Inbox</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Everything sent to {summary?.support_email ?? 'us'} and every form on the site.
            {summary?.reply_to
              ? ` What you send from here comes back to ${summary.reply_to}.`
              : null}
          </p>
        </div>
        {/* Reply could only answer somebody who wrote first, so reaching a
            dealership we want to talk to meant leaving for a mail client --
            where the message is invisible to this system for good, and goes
            out under whatever address that client is configured with rather
            than the one the deployment can prove. Same endpoint, same
            identity, same OUTBOUND_ONLY_TO: only the starting point is new. */}
        <Button variant="primary" size="sm" onClick={() => compose({ to: '', subject: '', html: '' })}>
          Write
        </Button>
      </div>

      {writing && (
        <Card className="mb-4 p-4 md:p-5">
          <div className="mb-3 text-sm font-medium">
            {writing.id ? 'Draft' : 'New message'}
          </div>
          <Composer
            key={`${writing.id ?? 'new'}-${writeSeq}`}
            draft={writing}
            onClose={() => setWriting(null)}
          />
        </Card>
      )}

      {/* Three panes at xl, two at md (boxes collapse to a row of chips), one
          on a phone -- where opening a message replaces the list rather than
          squeezing beside it. */}
      {/* Every track is `minmax(0, ...)`, base included -- an `auto` track
          sizes to the min-content of a `truncate` line, which is the whole
          string, so the list would push the page sideways rather than clip. */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-[18rem_minmax(0,1fr)] xl:grid-cols-[11rem_20rem_minmax(0,1fr)]">
        <Card className="h-fit p-2 xl:sticky xl:top-20">
          <div className="flex gap-1 overflow-x-auto xl:flex-col xl:overflow-visible">
            {BOXES.map((item) => (
              <button
                key={item.id}
                onClick={() => {
                  setBox(item.id)
                  setOpenId(null)
                  setHeld(null)
                }}
                className={clsx(
                  'flex shrink-0 items-center gap-2 whitespace-nowrap rounded-md px-2.5 py-2 text-sm font-medium transition-colors xl:w-full',
                  box === item.id
                    ? 'bg-accent text-accent-foreground'
                    : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                )}
              >
                {item.label}
                {/* Counted server-side from the same predicates the filter
                    uses, so a box saying 12 cannot show 9. */}
                <span className="tnum ml-auto text-xs text-muted-foreground">
                  {data?.counts?.[item.id] ?? 0}
                </span>
              </button>
            ))}
          </div>
        </Card>

        <Card className={clsx('min-w-0 overflow-hidden', open && 'hidden md:block')}>
          {isLoading ? (
            <Spinner />
          ) : !messages.length ? (
            <Empty
              title="Nothing in here"
              hint={EMPTY_HINT[box] ?? 'Nothing here yet.'}
            />
          ) : (
            <ul className="max-h-[70vh] divide-y divide-border overflow-y-auto">
              {messages.map((message) => (
                <li key={message.id}>
                  <button
                    onClick={() => { setOpenId(message.id); setHeld(message) }}
                    className={clsx(
                      'block w-full px-3 py-2.5 text-left transition-colors hover:bg-accent',
                      openId === message.id && 'bg-accent',
                    )}
                  >
                    <Row message={message} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className={clsx('min-w-0', !open && 'hidden xl:block')}>
          {open ? (
            <Reader
              // One reader per message: a reply half-written to one person
              // must not still be open, addressed to them, under the next.
              key={`${open.source}-${open.id}`}
              message={open}
              onBack={() => { setOpenId(null); setHeld(null) }}
              onEdit={(d) => { compose(d); setOpenId(null); setHeld(null) }}
            />
          ) : (
            <Card className="hidden xl:block">
              <Empty title="Nothing open" hint="Pick a message on the left." />
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}

/** One line of the list.
 *
 *  "+N" only on what we sent. The row names who a Sent message went to, so
 *  "+2" beside it answers "was anybody else on this"; on received mail the
 *  row names the sender, and the other people on it are mostly us -- the
 *  address it was delivered to -- which is a number that says nothing. The
 *  reader shows every name either way. */
function Row({ message }: { message: MailMessage }) {
  const out = message.direction === 'out'
  const others = out ? otherRecipients(message.email, message.to_address) : 0
  const files = message.email?.attachments?.length ?? 0
  const failed = message.source === 'ours' && message.kind === 'failed'

  return (
    <>
      <div className="flex items-center gap-2">
        {message.unread && (
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
        )}
        <span className="flex min-w-0 flex-1 items-center gap-1">
          <span
            className={clsx(
              'min-w-0 truncate text-sm',
              message.unread ? 'font-semibold' : 'font-medium',
            )}
          >
            {/* A Sent row labelled by its sender says our own name over and
                over; what a person scans for is who it went to. */}
            {out
              ? `To ${message.to_address || '(no recipient yet)'}`
              : message.from_name || message.from_address || '(no sender)'}
          </span>
          {others > 0 && (
            <span
              className="tnum shrink-0 text-xs text-muted-foreground"
              title={`And ${others} more ${others === 1 ? 'person' : 'people'} on To or Cc`}
            >
              +{others}
            </span>
          )}
        </span>
        {files > 0 && (
          <span
            role="img"
            aria-label={attachedLabel(files)}
            title={attachedLabel(files)}
            className="inline-flex shrink-0 items-center gap-0.5 text-xs text-muted-foreground"
          >
            <Icon name="paperclip" className="h-3.5 w-3.5" />
            <span className="tnum">{files}</span>
          </span>
        )}
        <span className="shrink-0 text-xs text-muted-foreground">
          {relative(message.at)}
        </span>
      </div>
      <div className="mt-0.5 flex min-w-0 items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-sm text-muted-foreground">
          {message.subject}
        </span>
        {/* Red means it broke, and a send that did not go out did. Without
            this a failed message sat in Sent looking exactly like one that
            left. */}
        {failed && <Badge tone="destructive" className="shrink-0">Failed</Badge>}
      </div>
    </>
  )
}

function attachedLabel(n: number): string {
  return n === 1 ? '1 attached file' : `${n} attached files`
}

/**
 * What the list row already knows, in the reader's shape.
 *
 * The reader's own answer is always preferred -- it has the HTML, every file
 * and the reply rules, which are the server's to decide. This stands in only
 * while it has not answered or when it cannot (a message filed before
 * envelopes were kept on a store that has not restarted, a server older than
 * this page), so the pane shows the words rather than an error, and Reply
 * still goes where it should: to the people a message of ours went to, and
 * otherwise to Reply-To or the sender. Reply all is deliberately *not*
 * guessed here -- working out who else was on a message means knowing which
 * of those addresses are ours, and a guess would copy us in on our own
 * reply. Without the server's answer the button is simply not offered.
 */
function fromRow(message: MailMessage): MailContent {
  const email = message.email
  const out = message.direction === 'out'
  // Our own rows carry the whole From header (`Name <address>`), a
  // stranger's the bare address; either way the reader wants the two apart.
  const sender = parseEntry(message.from_address || '')
  const from = { name: sender.name || message.from_name || '', address: sender.address }
  const to = email?.to?.length
    ? email.to
    : message.to_address
      ? [{ name: '', address: message.to_address }]
      : []
  const replyTo = email?.reply_to ?? []
  const attachments = email?.attachments ?? []
  const target = out ? to : replyTo.length ? replyTo : from.address ? [from] : []
  const reply = { to: target.map((a) => a.address), cc: [], subject: reSubject(message.subject) }
  return {
    kind: message.source as MailKind,
    id: message.id,
    direction: out ? 'out' : 'in',
    subject: message.subject,
    from,
    to,
    cc: email?.cc ?? [],
    bcc: email?.bcc ?? [],
    reply_to: replyTo,
    date: message.at,
    text: message.body,
    html: '',
    images_held: 0,
    importance: email?.importance ?? 'normal',
    message_id: '',
    attachments,
    lead_id: null,
    reply,
    reply_all: reply,
    forward: {
      subject: fwdSubject(message.subject),
      attachment_ids: attachments.filter((a) => a.url && !a.refused).map((a) => a.id),
    },
  }
}

function Reader({
  message,
  onBack,
  onEdit,
}: {
  message: MailMessage
  onBack: () => void
  onEdit: (draft: Draft) => void
}) {
  const [replying, setReplying] = useState<Draft | null>(null)
  const [images, setImages] = useState(false)
  const mark = useMailMark()
  const kind = markKind(message)
  const read = useMailContent(message, images)
  const content = read.data ?? null
  // Anything that writes from this message waits for the reader: a reply
  // opened a moment early would have no quote and the list's guess at who it
  // goes to. On a failure it goes ahead with what the row carried.
  const waiting = read.isPending
  const shown = content ?? fromRow(message)
  const draftRow = message.source === 'ours' && message.kind === 'draft'

  /* Reply and Reply all take the server's addresses verbatim. That is the
   * fix for Reply on a Sent message: it used to be addressed to the row's
   * `from_address`, which on a message of ours is *us*, so answering a
   * thread we started wrote to ourselves. The server answers a message of
   * ours with the people it went to. */
  const answer = (mode: 'reply' | 'reply_all' | 'forward'): Draft => {
    if (mode === 'forward') {
      const ids = new Set(shown.forward.attachment_ids)
      return {
        to: '',
        subject: shown.forward.subject,
        html: quotedBody(shown, 'forward'),
        attachments: shown.attachments.filter((a) => ids.has(a.id)),
      }
    }
    const target = shown[mode]
    return {
      to: target.to.join(', '),
      cc: target.cc.join(', '),
      subject: target.subject || reSubject(message.subject),
      html: quotedBody(shown, 'reply'),
      reply_to_kind: kind,
      reply_to_id: message.id,
    }
  }

  /** A saved draft back in a composer, with everything it was saved with --
   *  its Cc and Bcc, its formatting and its files, not only the words. */
  const resume = (): Draft => ({
    id: message.id,
    to: addrList(shown.to),
    cc: addrList(shown.cc),
    bcc: addrList(shown.bcc),
    subject: shown.subject === '(no subject)' ? '' : shown.subject,
    html: (shown.html || '').trim() ? shown.html : textToHtml(shown.text),
    attachments: shown.attachments,
    importance: shown.importance,
  })

  return (
    <Card className="min-w-0 p-4 md:p-5">
      <button
        onClick={onBack}
        className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground xl:hidden"
      >
        <Icon name="back" className="h-4 w-4" />
        Back to the list
      </button>

      <div className="flex flex-wrap items-start justify-between gap-2">
        <h2 className="min-w-0 break-words text-lg font-semibold">
          {message.subject || '(no subject)'}
        </h2>
        <div className="flex shrink-0 items-center gap-2">
          {message.kind === 'unmatched' ? (
            <Badge tone="warning">Matched nobody</Badge>
          ) : (
            <Badge tone={message.kind === 'failed' ? 'destructive' : 'neutral'} className="capitalize">
              {message.kind}
            </Badge>
          )}
        </div>
      </div>

      {message.kind === 'unmatched' && (
        <p className="mt-3 rounded-md border border-warning/30 bg-warning-muted px-3 py-2 text-xs text-warning-foreground">
          This arrived at the inbound endpoint and resolved to no buyer and no send. It is kept
          rather than dropped -- somebody really wrote in either way.
        </p>
      )}

      {/* What the provider said about one of ours that did not go out. The
          composer shows it at the moment of sending; this is where it is
          found again the next morning. */}
      {message.kind === 'failed' && message.detail && (
        <p className="mt-3 rounded-md border border-destructive/30 bg-destructive-muted px-3 py-2 text-xs text-destructive">
          This did not go out. {message.detail}
        </p>
      )}

      {(message.phone || message.dealership || message.dealership_url || message.slot_at) && (
        <div className="mt-4 grid gap-2 rounded-md border border-border bg-muted/30 p-3 sm:grid-cols-2">
          {message.dealership && <Fact label="Dealership" value={message.dealership} />}
          {message.phone && <Fact label="Phone" value={message.phone} href={`tel:${message.phone}`} />}
          {message.slot_at && <Fact label="Demo booked for" value={dateTime(message.slot_at)} />}
          {message.dealership_url && (
            <Fact label="Site" value={message.dealership_url} href={message.dealership_url} />
          )}
        </div>
      )}

      <div className="mt-4">
        {waiting ? (
          <Spinner />
        ) : (
          <>
            {read.isError && (
              <p className="mb-2 text-xs text-muted-foreground">
                The full message could not be loaded
                {read.error instanceof ApiError ? ` (${read.error.message})` : ''}, so this is
                what the list carried: the text, without formatting.
              </p>
            )}
            {/* Ours are never per store, so a file's path is its link as
                the server gave it. Bcc is shown on our own mail only -- it
                is only ever known for mail we sent. */}
            <EmailView
              message={shown}
              resolve={(url) => url}
              showBcc={message.source === 'ours'}
              onShowImages={images ? undefined : () => setImages(true)}
            />
          </>
        )}
      </div>

      <div className="mt-5 border-t border-border pt-4">
        {replying ? (
          <Composer draft={replying} onClose={() => setReplying(null)} />
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            {draftRow ? (
              <Button variant="primary" size="sm" disabled={waiting} onClick={() => onEdit(resume())}>
                Keep writing
              </Button>
            ) : (
              <>
                <Button
                  variant="primary"
                  size="sm"
                  disabled={waiting}
                  onClick={() => setReplying(answer('reply'))}
                >
                  <Icon name="reply" className="h-4 w-4" />
                  Reply
                </Button>
                {/* Only when it would reach somebody Reply does not. */}
                {content && replyAllAddsSomeone(content) && (
                  <Button size="sm" onClick={() => setReplying(answer('reply_all'))}>
                    <Icon name="replyAll" className="h-4 w-4" />
                    Reply all
                  </Button>
                )}
                <Button size="sm" disabled={waiting} onClick={() => setReplying(answer('forward'))}>
                  <Icon name="forward" className="h-4 w-4" />
                  Forward
                </Button>
              </>
            )}

            {/* Reading is done by opening; this is the other direction, and
                it is the only way an inbox works as a queue -- "I have seen
                this and have not dealt with it" needs somewhere to live. */}
            {message.direction === 'in' && (
              <Button
                size="sm"
                onClick={() => {
                  mark.read.mutate({ kind, id: message.id, read: false })
                  onBack()
                }}
              >
                Mark unread
              </Button>
            )}

            {/* A timestamp, never a delete: a message somebody wrote is the
                last thing to destroy on their behalf. */}
            <Button
              size="sm"
              onClick={() => {
                mark.trash.mutate({ kind, id: message.id, trashed: !message.trashed })
                onBack()
              }}
            >
              {message.trashed ? 'Restore' : 'Trash'}
            </Button>
          </div>
        )}
      </div>
    </Card>
  )
}

function Fact({ label, value, href }: { label: string; value: string; href?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="truncate text-sm">
        {href ? (
          <a href={href} target="_blank" rel="noreferrer" className="text-primary hover:underline">
            {value}
          </a>
        ) : (
          value
        )}
      </div>
    </div>
  )
}

/** What the composer is editing: a blank message, a reply, a forward, or a
 *  saved draft. Addresses are the recipient boxes' own strings; the body is
 *  HTML, because that is what the editor holds. */
interface Draft {
  /** Present once it has been saved, so Save updates rather than duplicates. */
  id?: string
  to: string
  cc?: string
  bcc?: string
  subject: string
  html: string
  /** Files it starts with: a draft's own, or the ones a forward carries on. */
  attachments?: Attachment[]
  importance?: Importance
  reply_to_kind?: string
  reply_to_id?: string
}

interface ReplyResult {
  message_id?: string
  sent: boolean
  reason?: string
  status?: string
  provider?: string
  from_address?: string
  from_is_personal?: boolean
  from_note?: string
  reply_to?: string
  detail?: string
  error?: string
  missing?: string[]
  email?: EmailSummary
}

interface SavedDraft {
  id: string
  updated_at: string
  email?: EmailSummary
}

/**
 * People this inbox has written to or heard from, for the recipient boxes.
 *
 * Read out of the boxes already fetched this session rather than asked for:
 * the two of us write to the dealerships that wrote in and to the ones we
 * have written to before, and those are exactly the rows on screen. Our own
 * addresses are left out -- offering `support@` as somebody to write to is
 * offering to mail ourselves.
 */
function useCorrespondents(summary: OpsSummary | undefined): RecipientSuggestion[] {
  const client = useQueryClient()
  const ours = new Set(
    [summary?.support_email, summary?.founder_email, summary?.reply_to, summary?.from_address]
      .filter(Boolean)
      .map((a) => bareAddress(a as string).toLowerCase()),
  )
  const found = new Map<string, RecipientSuggestion>()
  const add = (name: string, email: string) => {
    const key = (email || '').trim().toLowerCase()
    if (!key || !key.includes('@') || ours.has(key) || /^reply\+/.test(key)) return
    const known = found.get(key)
    if (!known || (!known.name && name)) found.set(key, { name: name || '', email: email.trim() })
  }
  for (const [, box] of client.getQueriesData<MailBox>({ queryKey: ['ops-mail'] })) {
    for (const m of box?.messages ?? []) {
      if (m.direction === 'in') add(m.from_name, m.from_address)
      for (const a of [...(m.email?.to ?? []), ...(m.email?.cc ?? [])]) add(a.name, a.address)
      if (m.direction === 'out' && !m.email) add('', m.to_address)
    }
  }
  return [...found.values()]
}

/**
 * Straight through `outreach_send`, the same sender and the same
 * `OUTBOUND_ONLY_TO` limit as everything else here -- over every recipient,
 * Cc and Bcc included. A composer is exactly where a rehearsal reaches a real
 * prospect, and the refusal names the setting rather than failing silently.
 *
 * **The order of its fields is a contract.** To is the first `<input>` in it
 * and Subject the second, because `make ops-ui` finds them that way; so Cc
 * and Bcc sit behind their links *after* Subject, the editor is a
 * `contenteditable` rather than a textarea, and the attachment picker's file
 * input comes after everything else.
 */
function Composer({
  draft,
  onClose,
}: {
  draft: Draft
  onClose: () => void
}) {
  const { data: summary } = useOpsSummary()
  const client = useQueryClient()
  const suggestions = useCorrespondents(summary)
  const toId = useId()
  const [id, setId] = useState(draft.id)
  const [address, setAddress] = useState(draft.to)
  const [cc, setCc] = useState(draft.cc ?? '')
  const [bcc, setBcc] = useState(draft.bcc ?? '')
  const [line, setLine] = useState(draft.subject)
  const [html, setHtml] = useState(draft.html)
  // The editor reports the text alongside the HTML once it has read a value
  // in, so a composer opened on a quote knows it is not empty.
  const [text, setText] = useState('')
  const [files, setFiles] = useState<Attachment[]>(draft.attachments ?? [])
  const [importance, setImportance] = useState<Importance>(draft.importance ?? 'normal')
  const [saved, setSaved] = useState<string | null>(null)
  /* The attachment picker is drawn afresh after every save. It removes from
   * the server only the uploads it made itself, and only while they are
   * pending -- and a save hands them to the draft, after which that delete
   * is a 404. A fresh picker knows it made none of them, so taking a file
   * off a saved draft is left to the next save, which drops what it is not
   * given. */
  const [pickerEpoch, setPickerEpoch] = useState(0)

  const settle = () => {
    for (const key of MAIL_KEYS) {
      void client.invalidateQueries({ queryKey: [key] })
    }
  }

  const fields = () => ({
    to: address,
    cc,
    bcc,
    subject: line,
    // The text half as the editor wrote it. When there is HTML the server
    // writes its own from that instead, so the two halves cannot disagree.
    body: text,
    // "" is what an empty editor reports, and the server then sends the
    // text alone, as it always has.
    html: html || '',
    attachment_ids: files.map((f) => f.id),
    importance,
    reply_to_kind: draft.reply_to_kind ?? '',
    reply_to_id: draft.reply_to_id ?? '',
  })

  /* Saved on the server, not in the tab.
   *
   * The dealership's composer deliberately has no Drafts box, because nothing
   * there stores one -- it is built from the lead's state and lives in the
   * browser until send, and a tab that is always empty claims a feature that
   * does not exist. This is the other case: a first message to somebody we
   * want to talk to gets written over a morning, and a browser tab is the
   * wrong place for that to live. Its Cc, Bcc, formatting and files are kept
   * with it -- a draft that comes back without the attachment somebody spent
   * a minute finding has to be written twice. */
  const saveDraft = useMutation({
    mutationFn: (sent: string[]) =>
      api.post<SavedDraft>('/api/ops/mail/draft', { id, ...fields(), attachment_ids: sent }),
    onSuccess: (row, sent) => {
      setId(row.id)
      setSaved(row.updated_at)
      /* The draft's own rows from now on. A forwarded file is copied onto the
       * draft under a new id, so sending the original id again would copy it
       * a second time; anything uploaded while the save was in flight was
       * not in it and is kept as it is. */
      const kept = row.email?.attachments
      if (kept) {
        const asked = new Set(sent)
        setFiles((current) => [...kept, ...current.filter((f) => !asked.has(f.id))])
      }
      setPickerEpoch((n) => n + 1)
      settle()
    },
  })

  const send = useMutation({
    mutationFn: () =>
      api.post<ReplyResult>('/api/ops/mail/send', { ...fields(), draft_id: id ?? null }),
    onSuccess: settle,
  })

  const result = send.data
  const anything =
    address.trim() || cc.trim() || bcc.trim() || line.trim() || text.trim() || files.length > 0
  /* Something to send, not somebody to send it to. A missing or unreadable
   * address is the server's to name -- it quotes the entries it could not
   * read -- where a greyed-out button would only say that something, somewhere,
   * is wrong. */
  const sendable = Boolean(line.trim() || text.trim() || files.length > 0)

  return (
    <div className="space-y-3">
      {/* Who this goes out as, before it goes out. The From is not a field
          because it is not a choice -- it is whichever address the deployment
          can prove it owns -- but showing it is the difference between
          writing under your own name and finding out later that you did not. */}
      {summary?.from_address ? (
        <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs">
          <div className="min-w-0 break-words">
            <span className="text-muted-foreground">From </span>
            <span className="font-medium">{summary.from_address}</span>
            {summary.from_is_personal && (
              <Badge tone="success" className="ml-2">Your own address</Badge>
            )}
          </div>
          <div className="mt-0.5 break-words text-muted-foreground">
            Replies come back to {summary.reply_to}
          </div>
          {summary.from_note && (
            <p className="mt-1.5 text-warning-foreground">{summary.from_note}</p>
          )}
        </div>
      ) : null}
      <FieldGroup label="To" htmlFor={toId}>
        <RecipientInput
          id={toId}
          ariaLabel="To"
          value={address}
          onChange={setAddress}
          suggestions={suggestions}
          placeholder="name@dealership.com -- separate several with commas"
        />
      </FieldGroup>
      <Field label="Subject">
        <Input value={line} onChange={(e) => setLine(e.target.value)} />
      </Field>
      <CopyFields cc={cc} bcc={bcc} onCc={setCc} onBcc={setBcc} suggestions={suggestions} />
      {/* A group, not a <label>: a label hands a click on its caption to the
          first button inside it, which here is the toolbar's Bold. */}
      <FieldGroup label="Message">
        <RichEditor
          value={html}
          onChange={(nextHtml, nextText) => {
            setHtml(nextHtml)
            setText(nextText)
          }}
          placeholder={draft.reply_to_id ? 'Write the reply...' : 'Write the message...'}
          ariaLabel="Message"
          minHeight={180}
        />
      </FieldGroup>
      <AttachmentPicker
        key={pickerEpoch}
        value={files}
        onChange={setFiles}
        uploadPath={OPS_ATTACHMENTS}
        removeBase={OPS_ATTACHMENTS}
      />
      {/* A button rather than a checkbox: it is one more thing somebody may
          set, not a field, and an <input> here would be counted among the
          composer's fields by the gate that finds To and Subject. */}
      <div>
        <button
          type="button"
          aria-pressed={importance === 'high'}
          title="Sends Importance: high, which most mail clients show as a flag"
          onClick={() => setImportance((v) => (v === 'high' ? 'normal' : 'high'))}
          className={clsx(
            'inline-flex h-7 items-center rounded-md border px-2.5 text-xs font-medium transition-colors duration-150',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            importance === 'high'
              ? 'border-warning/30 bg-warning-muted text-warning-foreground'
              : 'border-border text-muted-foreground hover:bg-accent hover:text-accent-foreground',
          )}
        >
          High importance
        </button>
      </div>

      {result && !result.sent && (
        <p className="rounded-md border border-warning/30 bg-warning-muted px-3 py-2 text-sm text-warning-foreground">
          Not sent. {result.reason || result.detail || 'The sender refused it.'}
          {result.missing?.length ? ` Missing: ${result.missing.join(', ')}.` : ''}
        </p>
      )}
      {/* The provider's own words, not a green tick. With the default outbox
          sender `sent` means recorded and nothing left the building, and a
          composer that reported that as delivered is how a reply sits unread
          for a week while the person who wrote it believes they answered. */}
      {result?.sent && (
        <p
          className={clsx(
            'break-words rounded-md border px-3 py-2 text-sm',
            result.provider === 'outbox'
              ? 'border-warning/30 bg-warning-muted text-warning-foreground'
              : 'border-success/30 bg-success-muted text-success',
          )}
        >
          {result.provider === 'outbox'
            ? 'Not delivered. '
            : `Sent through ${result.provider} as ${result.from_address}. `}
          {result.detail}
        </p>
      )}
      {send.isError && (
        <p className="text-sm text-destructive">{(send.error as ApiError).message}</p>
      )}
      {saveDraft.isError && (
        <p className="text-sm text-destructive">
          The draft was not kept. {(saveDraft.error as ApiError).message}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          disabled={send.isPending || !sendable}
          onClick={() => send.mutate()}
        >
          {send.isPending ? 'Sending...' : 'Send'}
        </Button>
        <Button
          size="sm"
          disabled={saveDraft.isPending || !anything}
          onClick={() => saveDraft.mutate(files.map((f) => f.id))}
        >
          {saveDraft.isPending ? 'Saving...' : 'Save draft'}
        </Button>
        <Button size="sm" onClick={onClose}>
          Close
        </Button>
        {saved && !result && (
          <span className="text-xs text-muted-foreground">
            Draft kept {relative(saved)}
          </span>
        )}
        {summary && !summary.sender_delivers && (
          <span className="ml-auto text-xs text-muted-foreground">
            Sender is {summary.sender} -- nothing leaves the building
          </span>
        )}
      </div>
    </div>
  )
}
