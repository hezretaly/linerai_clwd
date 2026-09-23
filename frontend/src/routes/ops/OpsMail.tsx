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

import { useEffect, useId, useRef, useState } from 'react'
import clsx from 'clsx'
import { useSearchParams } from 'react-router-dom'
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
import { Icon, type IconName } from '../../components/Icon'
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

const BOXES: { id: string; label: string; icon: IconName }[] = [
  { id: 'all', label: 'Inbox', icon: 'inbox' },
  { id: 'unread', label: 'Unread', icon: 'dot' },
  { id: 'demos', label: 'Demos', icon: 'calendar' },
  { id: 'support', label: 'Support', icon: 'chat' },
  // Mail that arrived and matched nobody. It has no buyer page to appear on,
  // which is the entire reason it needs a box of its own.
  { id: 'unmatched', label: 'Unmatched', icon: 'alert' },
  // What we wrote. Drafts are the author's own -- an unfinished message is
  // not something to put in front of somebody else -- while Sent is shared,
  // because "has anyone answered these people yet" is what two people sharing
  // an inbox actually ask.
  { id: 'drafts', label: 'Drafts', icon: 'pencil' },
  { id: 'sent', label: 'Sent', icon: 'send' },
  // Defined by the mark rather than the source, so a discarded draft and a
  // binned form land in the same place a person looks for them.
  { id: 'trash', label: 'Trash', icon: 'trash' },
]

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
  /** Whether the open composer holds something not yet sent or saved. */
  const [unsaved, setUnsaved] = useState(false)
  const [box, setBox] = useState<string>('all')
  const [openId, setOpenId] = useState<string | null>(null)
  const [held, setHeld] = useState<MailMessage | null>(null)
  const [search, setSearch] = useState('')
  const { data: summary } = useOpsSummary()
  const mark = useMailMark()
  const [params, setParams] = useSearchParams()
  /** The message to open with Reply already started -- set by a link here
   *  from elsewhere on /ops, never by the list. */
  const [replyTo, setReplyTo] = useState<string | null>(null)

  /** Closing a composer with words in it asks first. It lives in the pane a
   *  message opens into, so picking a row would otherwise throw a half-written
   *  message away without a word. */
  const leaveComposer = (): boolean => {
    if (writing && unsaved && !window.confirm('Leave this message? It is not saved -- Save draft keeps it.')) {
      return false
    }
    setWriting(null)
    setUnsaved(false)
    return true
  }
  const compose = (draft: Draft) => {
    if (!leaveComposer()) return
    setWriteSeq((n) => n + 1)
    setWriting(draft)
    setOpenId(null)
    setHeld(null)
  }
  const openMessage = (message: MailMessage) => {
    if (!leaveComposer()) return
    setOpenId(message.id)
    setHeld(message)
  }
  const closeMessage = () => {
    setOpenId(null)
    setHeld(null)
  }

  const { data, isLoading } = useQuery({
    queryKey: ['ops-mail', box],
    queryFn: () => api.get<MailBox>(`/api/ops/mail?box=${box}`),
  })

  const messages = data?.messages ?? []
  /* Searched in the page, over what the box already holds. The box is the
   * newest few hundred of one kind, which is exactly the set somebody is
   * hunting through ("the dealership in Tulsa that wrote last week"), and a
   * server search would be a second definition of what each box contains. */
  const needle = search.trim().toLowerCase()
  const shown = needle
    ? messages.filter((m) =>
        [m.subject, m.from_name, m.from_address, m.to_address, m.body, m.dealership]
          .filter(Boolean)
          .some((v) => String(v).toLowerCase().includes(needle)),
      )
    : messages
  /* Held separately so reading one does not make it disappear mid-sentence.
   *
   * Opening a message marks it read, which drops it out of Unread -- and with
   * the reader derived from the list alone, the pane it was being read in
   * unmounted underneath the person reading it. The same happens on Trash.
   * The list is still the source of truth while the row is in it, so an
   * `unread` or `trashed` change is picked up; `held` only covers the moment
   * after it leaves. */
  const open = messages.find((m) => m.id === openId) ?? held

  /* **"Email them" lands here, not in somebody's mail client.** The demo
   * calendar's buttons were `mailto:` links, so answering a person who had
   * booked a demo left this system for good: the reply went out under
   * whatever address that laptop's client used, never reached Sent, and the
   * other founder could not see it had been answered. Now they link to
   * `?reply=form:<id>&to=<address>`: the request opens here with Reply
   * started, so the reply is threaded to it and sent from our own mailbox.
   * A request that is not in the list any more -- trashed, or past the
   * newest 300 -- still gets a message to them rather than a dead end. The
   * link is cleared once used, so a refresh does not start a second reply. */
  useEffect(() => {
    const wanted = params.get('reply') ?? ''
    const to = params.get('to') ?? ''
    if (!wanted && !to) return
    if (isLoading) return
    const [source, id] = wanted.split(':')
    const found = messages.find((m) => m.source === source && m.id === id)
    if (found) {
      openMessage(found)
      setReplyTo(`${found.source}-${found.id}`)
    } else if (to) {
      compose({ to, subject: '', html: '' })
    }
    setParams({}, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, isLoading])

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

  const busy = Boolean(open || writing)
  const current = BOXES.find((b) => b.id === box)

  return (
    <div className="p-4 md:p-6">
      <div className="mb-4 min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight">Email</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Everything sent to {summary?.support_email ?? 'us'} and every form on the site.
          {summary?.reply_to
            ? ` What you send from here comes back to ${summary.reply_to}.`
            : null}
        </p>
      </div>

      {/* **One surface, three panes, each scrolling on its own.** It was three
          cards on a page that scrolled as a whole, with the composer as a
          fourth card above them -- so writing pushed the mailbox off the
          screen, and a long list scrolled the message being read out of
          view. Now the composer opens where a message would, beside the list
          it came from. On a phone the panes take turns: folders and the list,
          then the message or the composer on its own.

          Every track is `minmax(0, ...)`, base included -- an `auto` track
          sizes to the min-content of a `truncate` line, which is the whole
          string, so the list would push the page sideways rather than clip. */}
      <Card className="overflow-hidden md:h-[calc(100vh-12.5rem)] md:min-h-[32rem]">
        <div className="grid h-full grid-cols-1 md:grid-cols-[20rem_minmax(0,1fr)] md:grid-rows-[auto_minmax(0,1fr)] xl:grid-cols-[13rem_22rem_minmax(0,1fr)] xl:grid-rows-1">
          {/* Folders. A row of chips under the Write button until there is
              room for a rail. Deliberately not a list of `li` buttons: the
              message list is. */}
          <nav
            aria-label="Folders"
            className={clsx(
              'flex min-w-0 flex-col gap-2 border-b border-border p-3 md:col-span-2 md:flex-row md:items-center xl:col-span-1 xl:flex-col xl:items-stretch xl:border-b-0 xl:border-r',
              busy && 'hidden md:flex',
            )}
          >
            {/* Reply could only answer somebody who wrote first, so reaching a
                dealership we want to talk to meant leaving for a mail client --
                where the message is invisible to this system for good, and goes
                out under whatever address that client is configured with rather
                than the one the deployment can prove. Same endpoint, same
                identity, same OUTBOUND_ONLY_TO: only the starting point is new. */}
            <Button
              variant="primary"
              size="sm"
              className="shrink-0 xl:w-full"
              onClick={() => compose({ to: '', subject: '', html: '' })}
            >
              <Icon name="pencil" className="h-4 w-4" />
              Write
            </Button>
            <div className="flex min-w-0 gap-1 overflow-x-auto xl:flex-col xl:overflow-visible">
              {BOXES.map((item) => (
                <button
                  key={item.id}
                  onClick={() => {
                    setBox(item.id)
                    setSearch('')
                    if (!writing) closeMessage()
                  }}
                  aria-current={box === item.id ? 'page' : undefined}
                  className={clsx(
                    'flex shrink-0 items-center gap-2 whitespace-nowrap rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors xl:w-full',
                    box === item.id
                      ? 'bg-accent text-accent-foreground'
                      : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                  )}
                >
                  <Icon name={item.icon} className="h-4 w-4 shrink-0" />
                  {item.label}
                  {/* Counted server-side from the same predicates the filter
                      uses, so a box saying 12 cannot show 9. */}
                  <span
                    className={clsx(
                      'tnum ml-auto text-xs',
                      item.id === 'unread' && (data?.counts?.unread ?? 0) > 0
                        ? 'font-semibold text-primary'
                        : 'text-muted-foreground',
                    )}
                  >
                    {data?.counts?.[item.id] ?? 0}
                  </span>
                </button>
              ))}
            </div>
          </nav>

          {/* The list. */}
          <section
            aria-label={current?.label ?? 'Messages'}
            className={clsx(
              'flex min-h-0 min-w-0 flex-col border-border',
              // Beside the message when one is open; the whole width when
              // nothing is, rather than half of it beside an empty pane.
              busy ? 'hidden md:flex md:border-r' : 'md:col-span-2 xl:col-span-1 xl:border-r',
            )}
          >
            <div className="border-b border-border p-3">
              <div className="mb-2 flex items-baseline justify-between gap-2">
                <h2 className="text-sm font-semibold">{current?.label}</h2>
                <span className="tnum text-xs text-muted-foreground">
                  {needle ? `${shown.length} of ${messages.length}` : messages.length}
                </span>
              </div>
              <div className="relative">
                <Icon
                  name="search"
                  className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                />
                <Input
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={`Search ${current?.label.toLowerCase() ?? 'mail'}`}
                  aria-label="Search this folder"
                  className="h-8 pl-8"
                />
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {isLoading ? (
                <Spinner />
              ) : !shown.length ? (
                <Empty
                  title={needle ? 'Nothing matches' : 'Nothing in here'}
                  hint={needle ? `Nothing in ${current?.label ?? 'this folder'} mentions "${search.trim()}".` : EMPTY_HINT[box] ?? 'Nothing here yet.'}
                />
              ) : (
                <ul className="divide-y divide-border">
                  {shown.map((message) => (
                    <li key={message.id}>
                      <button
                        onClick={() => openMessage(message)}
                        aria-current={openId === message.id ? 'true' : undefined}
                        className={clsx(
                          'block w-full border-l-2 px-3 py-2.5 text-left transition-colors hover:bg-accent',
                          openId === message.id ? 'border-l-primary bg-accent' : 'border-l-transparent',
                        )}
                      >
                        <Row message={message} />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>

          {/* The message, or the message being written. */}
          <section
            aria-label={writing ? 'Compose' : 'Message'}
            className={clsx(
              'min-h-0 min-w-0 overflow-y-auto',
              !busy && 'hidden xl:block',
            )}
          >
            {writing ? (
              <div className="p-4 md:p-5">
                <div className="mb-4 flex items-center justify-between gap-2">
                  <h2 className="text-lg font-semibold">
                    {writing.id ? 'Draft' : 'New message'}
                  </h2>
                </div>
                <Composer
                  key={`${writing.id ?? 'new'}-${writeSeq}`}
                  draft={writing}
                  onDirty={setUnsaved}
                  onClose={() => { setWriting(null); setUnsaved(false) }}
                />
              </div>
            ) : open ? (
              <Reader
                // One reader per message: a reply half-written to one person
                // must not still be open, addressed to them, under the next.
                key={`${open.source}-${open.id}`}
                message={open}
                startReply={replyTo === `${open.source}-${open.id}`}
                onBack={closeMessage}
                onEdit={(d) => compose(d)}
              />
            ) : (
              <div className="flex h-full items-center justify-center p-6">
                <div className="text-center">
                  <Icon name="mail" className="mx-auto h-8 w-8 text-muted-foreground/60" />
                  <p className="mt-3 text-sm font-medium">Nothing open</p>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Pick a message, or press Write to start one.
                  </p>
                </div>
              </div>
            )}
          </section>
        </div>
      </Card>
    </div>
  )
}

/** One row of the list: who, when, what it is about, and the first words of it.
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
  const draft = message.source === 'ours' && message.kind === 'draft'
  const high = message.email?.importance === 'high'
  const preview = (message.body || '').replace(/\s+/g, ' ').trim()

  return (
    <>
      <div className="flex items-center gap-2">
        <span
          className={clsx(
            'h-2 w-2 shrink-0 rounded-full',
            message.unread ? 'bg-primary' : 'bg-transparent',
          )}
        />
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
        <span className="shrink-0 text-xs text-muted-foreground" title={dateTime(message.at)}>
          {relative(message.at)}
        </span>
      </div>
      <div className="mt-0.5 flex min-w-0 items-center gap-1.5 pl-4">
        <span
          className={clsx(
            'min-w-0 flex-1 truncate text-sm',
            message.unread ? 'text-foreground' : 'text-muted-foreground',
          )}
        >
          {message.subject || '(no subject)'}
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
        {high && (
          <span title="High importance" className="shrink-0 text-xs font-semibold text-warning-foreground">
            !
          </span>
        )}
        {draft && <Badge tone="neutral" className="shrink-0">Draft</Badge>}
        {/* Red means it broke, and a send that did not go out did. Without
            this a failed message sat in Sent looking exactly like one that
            left. */}
        {failed && <Badge tone="destructive" className="shrink-0">Failed</Badge>}
      </div>
      {preview && (
        <p className="mt-0.5 truncate pl-4 text-xs text-muted-foreground">{preview}</p>
      )}
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
  startReply = false,
  onBack,
  onEdit,
}: {
  message: MailMessage
  /** Open with Reply already started, once the message has loaded. */
  startReply?: boolean
  onBack: () => void
  onEdit: (draft: Draft) => void
}) {
  const [replying, setReplying] = useState<Draft | null>(null)
  const replyBox = useRef<HTMLDivElement>(null)
  // A reply opens under the message, which on anything longer than a line is
  // below the fold of the pane -- pressing Reply and seeing nothing happen.
  useEffect(() => {
    if (replying) replyBox.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [Boolean(replying)])
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

  // Once, after the reader has the message: a reply started before that has
  // no quote and only the list's guess at who it goes to.
  const [started, setStarted] = useState(false)
  useEffect(() => {
    if (!startReply || started || waiting) return
    setStarted(true)
    setReplying(answer('reply'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startReply, started, waiting])

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

  const actions = (
    <div className="flex flex-wrap items-center gap-1.5">
      {draftRow ? (
        <Button variant="primary" size="sm" disabled={waiting} onClick={() => onEdit(resume())}>
          <Icon name="pencil" className="h-4 w-4" />
          Keep writing
        </Button>
      ) : (
        <>
          <Button
            variant="primary"
            size="sm"
            disabled={waiting || Boolean(replying)}
            onClick={() => setReplying(answer('reply'))}
          >
            <Icon name="reply" className="h-4 w-4" />
            Reply
          </Button>
          {/* Only when it would reach somebody Reply does not. */}
          {content && replyAllAddsSomeone(content) && (
            <Button size="sm" disabled={Boolean(replying)} onClick={() => setReplying(answer('reply_all'))}>
              <Icon name="replyAll" className="h-4 w-4" />
              Reply all
            </Button>
          )}
          <Button size="sm" disabled={waiting || Boolean(replying)} onClick={() => setReplying(answer('forward'))}>
            <Icon name="forward" className="h-4 w-4" />
            Forward
          </Button>
        </>
      )}

      <span className="mx-1 hidden h-5 w-px bg-border sm:block" aria-hidden="true" />

      {/* Reading is done by opening; this is the other direction, and it is
          the only way an inbox works as a queue -- "I have seen this and
          have not dealt with it" needs somewhere to live. */}
      {message.direction === 'in' && (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            mark.read.mutate({ kind, id: message.id, read: false })
            onBack()
          }}
        >
          Mark unread
        </Button>
      )}

      {/* A timestamp, never a delete: a message somebody wrote is the last
          thing to destroy on their behalf. */}
      <Button
        size="sm"
        variant="ghost"
        onClick={() => {
          mark.trash.mutate({ kind, id: message.id, trashed: !message.trashed })
          onBack()
        }}
      >
        <Icon name="trash" className="h-4 w-4" />
        {message.trashed ? 'Restore' : 'Trash'}
      </Button>
    </div>
  )

  return (
    <div className="min-w-0">
      {/* The things you do to a message, above it and in reach however far
          down it you have read. They were under the body, which on a long
          newsletter or a forwarded thread was a scroll away from every
          button. */}
      <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b border-border bg-card/95 px-4 py-2.5 backdrop-blur md:px-5">
        <button
          onClick={onBack}
          aria-label="Back to the list"
          className="-ml-1 inline-flex h-8 items-center gap-1 rounded-md px-1.5 text-sm text-muted-foreground hover:bg-accent hover:text-foreground xl:hidden"
        >
          <Icon name="back" className="h-4 w-4" />
          <span className="sm:hidden">Back</span>
        </button>
        {actions}
      </div>

      <div className="p-4 md:p-5">
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

        {replying && (
          <div ref={replyBox} className="mt-6 rounded-lg border border-border p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-medium">
              <Icon
                name={replying.reply_to_id ? (replying.cc ? 'replyAll' : 'reply') : 'forward'}
                className="h-4 w-4 text-muted-foreground"
              />
              {replying.reply_to_id ? `Reply to ${replying.to || 'the sender'}` : 'Forward'}
            </div>
            <Composer draft={replying} onClose={() => setReplying(null)} />
          </div>
        )}
      </div>
    </div>
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
 * **Its fields are found by their labels.** `make ops-ui` used to take the
 * first `<input>` on the page as To and the second as Subject, which pinned
 * Cc and Bcc below the subject line where nobody looks for them, and broke
 * the moment the list grew a search box. It reads `[data-composer]
 * input[aria-label="To"]` now, so the fields sit where a mail client puts
 * them: To, then Cc and Bcc, then Subject.
 */
function Composer({
  draft,
  onClose,
  onDirty,
}: {
  draft: Draft
  onClose: () => void
  /** Told whether this holds anything not yet sent or saved, so whatever
   *  would close it can ask first. */
  onDirty?: (dirty: boolean) => void
}) {
  const { data: summary } = useOpsSummary()
  const client = useQueryClient()
  const suggestions = useCorrespondents(summary)
  const toId = useId()
  const [id, setId] = useState(draft.id)
  const [address, setAddress] = useState(draft.to)
  const [cc, setCc] = useState(draft.cc ?? '')
  // Files still uploading hold Send, or the message goes without them.
  const [uploading, setUploading] = useState(0)
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
      setKept(JSON.stringify(fields()))
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
  /* Unsaved means different from what was last kept. A message that has
   * gone, or a draft saved and not touched since, can be closed without a
   * question; anything typed after either cannot. */
  const snapshot = JSON.stringify(fields())
  const [kept, setKept] = useState<string | null>(null)
  const dirty = Boolean(anything) && !result?.sent && snapshot !== kept
  useEffect(() => {
    onDirty?.(dirty)
  }, [dirty, onDirty])
  /* Something to send, not somebody to send it to. A missing or unreadable
   * address is the server's to name -- it quotes the entries it could not
   * read -- where a greyed-out button would only say that something, somewhere,
   * is wrong. */
  const sendable = Boolean(line.trim() || text.trim() || files.length > 0)

  return (
    <div data-composer className="space-y-3">
      {/* Who this goes out as, before it goes out. The From is not a field
          because it is not a choice -- it is whichever address the deployment
          can prove it owns -- but showing it is the difference between
          writing under your own name and finding out later that you did not. */}
      {summary?.from_address ? (
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs">
          <span className="shrink-0 text-muted-foreground">From</span>
          <span className="min-w-0 break-words font-medium">{summary.from_address}</span>
          {summary.from_is_personal && <Badge tone="success">Your own address</Badge>}
          <span className="min-w-0 break-words text-muted-foreground">
            -- replies come back to {summary.reply_to}
          </span>
          {summary.from_note && (
            <p className="basis-full text-warning-foreground">{summary.from_note}</p>
          )}
        </div>
      ) : null}
      <div className="space-y-2 rounded-md border border-border p-2.5">
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
        <CopyFields cc={cc} bcc={bcc} onCc={setCc} onBcc={setBcc} suggestions={suggestions} />
        <Field label="Subject">
          <Input aria-label="Subject" value={line} onChange={(e) => setLine(e.target.value)} />
        </Field>
      </div>
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
          minHeight={220}
        />
      </FieldGroup>
      <AttachmentPicker
        key={pickerEpoch}
        value={files}
        onChange={setFiles}
        uploadPath={OPS_ATTACHMENTS}
        removeBase={OPS_ATTACHMENTS}
        onBusy={setUploading}
      />

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
          disabled={send.isPending || !sendable || uploading > 0}
          onClick={() => send.mutate()}
        >
          {send.isPending ? 'Sending...' : 'Send'}
        </Button>
        <Button
          size="sm"
          disabled={saveDraft.isPending || !anything || uploading > 0}
          onClick={() => saveDraft.mutate(files.map((f) => f.id))}
        >
          {saveDraft.isPending ? 'Saving...' : 'Save draft'}
        </Button>
        <Button size="sm" onClick={onClose}>
          Close
        </Button>
        {/* A button rather than a checkbox: one more thing somebody may set,
            beside the buttons that act on the message rather than among its
            fields. */}
        <button
          type="button"
          aria-pressed={importance === 'high'}
          title="Sends Importance: high, which most mail clients show as a flag"
          onClick={() => setImportance((v) => (v === 'high' ? 'normal' : 'high'))}
          className={clsx(
            'inline-flex h-8 items-center rounded-md border px-2.5 text-xs font-medium transition-colors duration-150',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            importance === 'high'
              ? 'border-warning/30 bg-warning-muted text-warning-foreground'
              : 'border-border text-muted-foreground hover:bg-accent hover:text-accent-foreground',
          )}
        >
          High importance
        </button>
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
