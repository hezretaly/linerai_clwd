/**
 * Mail addressed to us.
 *
 * A different pile from `/app/email`, which is the dealership's: replies that
 * resolved to one of *their* buyers. This one is the marketing site's forms
 * plus anything that arrived at the inbound endpoint and resolved to nobody --
 * which is exactly what a stranger writing to `support@` looks like.
 *
 * Three panes rather than a mail client dependency. What a mail client brings
 * is MIME parsing, threading and a folder tree; the parsing happens in the
 * Worker, there are no folders, and a form submission has no thread. Pulling
 * one in would have meant fitting our two sources to its message shape --
 * more work than the list, and a second definition of what a box contains.
 */

import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { useMutation } from '@tanstack/react-query'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { api, ApiError } from '../../lib/api'
import { dateTime, relative } from '../../lib/format'
import { Badge, Button, Card, Empty, Field, Input, Spinner } from '../../components/ui'
import { Icon } from '../../components/Icon'
import {
  markKind,
  useMailMark,
  useOpsSummary,
  type MailBox,
  type MailMessage,
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
        <Button variant="primary" size="sm" onClick={() => compose({ to: '', subject: '', body: '' })}>
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
                    <div className="flex items-center gap-2">
                      {message.unread && (
                        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                      )}
                      <span
                        className={clsx(
                          'min-w-0 flex-1 truncate text-sm',
                          message.unread ? 'font-semibold' : 'font-medium',
                        )}
                      >
                        {/* A Sent row labelled by its sender says our own
                            name over and over; what a person scans for is who
                            it went to. */}
                        {message.direction === 'out'
                          ? `To ${message.to_address || '(no recipient yet)'}`
                          : message.from_name || message.from_address || '(no sender)'}
                      </span>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {relative(message.at)}
                      </span>
                    </div>
                    <div className="mt-0.5 truncate text-sm text-muted-foreground">
                      {message.subject}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className={clsx('min-w-0', !open && 'hidden xl:block')}>
          {open ? (
            <Reader
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

function Reader({
  message,
  onBack,
  onEdit,
}: {
  message: MailMessage
  onBack: () => void
  onEdit: (draft: Draft) => void
}) {
  const [replying, setReplying] = useState(false)
  const mark = useMailMark()
  const kind = markKind(message)

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
        <div className="min-w-0">
          <h2 className="text-lg font-semibold">{message.subject}</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {message.from_name ? `${message.from_name} · ` : ''}
            <a href={`mailto:${message.from_address}`} className="text-primary hover:underline">
              {message.from_address || '(no address)'}
            </a>
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {message.kind === 'unmatched' ? (
            <Badge tone="warning">Matched nobody</Badge>
          ) : (
            <Badge tone="neutral" className="capitalize">
              {message.kind}
            </Badge>
          )}
          <span className="text-xs text-muted-foreground">{dateTime(message.at)}</span>
        </div>
      </div>

      {message.kind === 'unmatched' && (
        <p className="mt-3 rounded-md border border-warning/30 bg-warning-muted px-3 py-2 text-xs text-warning-foreground">
          This arrived at the inbound endpoint and resolved to no buyer and no send. It is kept
          rather than dropped -- somebody really wrote in either way.
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

      <div className="mt-4 whitespace-pre-wrap break-words text-sm leading-relaxed">
        {message.body || <span className="text-muted-foreground">(empty)</span>}
      </div>

      <div className="mt-5 border-t border-border pt-4">
        {replying ? (
          <Composer
            draft={{
              to: message.from_address,
              subject: message.subject.startsWith('Re:')
                ? message.subject
                : `Re: ${message.subject}`,
              body: '',
              reply_to_kind: kind,
              reply_to_id: message.id,
            }}
            onClose={() => setReplying(false)}
          />
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            {message.source === 'ours' && message.kind === 'draft' ? (
              <Button
                variant="primary"
                size="sm"
                onClick={() =>
                  onEdit({
                    id: message.id,
                    to: message.to_address,
                    subject: message.subject === '(no subject)' ? '' : message.subject,
                    body: message.body,
                  })
                }
              >
                Keep writing
              </Button>
            ) : (
              <Button variant="primary" size="sm" onClick={() => setReplying(true)}>
                Reply
              </Button>
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

/** What the composer is editing: a blank message, a reply, or a saved draft. */
interface Draft {
  /** Present once it has been saved, so Save updates rather than duplicates. */
  id?: string
  to: string
  subject: string
  body: string
  reply_to_kind?: string
  reply_to_id?: string
}

interface ReplyResult {
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
}

/**
 * Straight through `outreach_send`, the same sender and the same
 * `OUTBOUND_ONLY_TO` limit as everything else here. A composer is exactly
 * where a rehearsal reaches a real prospect, and the refusal names the setting
 * rather than failing silently.
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
  const [id, setId] = useState(draft.id)
  const [address, setAddress] = useState(draft.to)
  const [line, setLine] = useState(draft.subject)
  const [body, setBody] = useState(draft.body)
  const [saved, setSaved] = useState<string | null>(null)

  const settle = () => {
    for (const key of ['ops-summary', 'ops-mail']) {
      void client.invalidateQueries({ queryKey: [key] })
    }
  }

  /* Saved on the server, not in the tab.
   *
   * The dealership's composer deliberately has no Drafts box, because nothing
   * there stores one -- it is built from the lead's state and lives in the
   * browser until send, and a tab that is always empty claims a feature that
   * does not exist. This is the other case: a first message to somebody we
   * want to talk to gets written over a morning, and a browser tab is the
   * wrong place for that to live. */
  const saveDraft = useMutation({
    mutationFn: () =>
      api.post<{ id: string; updated_at: string }>('/api/ops/mail/draft', {
        id, to: address, subject: line, body,
        reply_to_kind: draft.reply_to_kind ?? '',
        reply_to_id: draft.reply_to_id ?? '',
      }),
    onSuccess: (row) => {
      setId(row.id)
      setSaved(row.updated_at)
      settle()
    },
  })

  const send = useMutation({
    mutationFn: () =>
      api.post<ReplyResult>('/api/ops/mail/send', {
        to: address, subject: line, body, draft_id: id ?? null,
        reply_to_kind: draft.reply_to_kind ?? '',
        reply_to_id: draft.reply_to_id ?? '',
      }),
    onSuccess: settle,
  })

  const result = send.data

  return (
    <div className="space-y-3">
      {/* Who this goes out as, before it goes out. The From is not a field
          because it is not a choice -- it is whichever address the deployment
          can prove it owns -- but showing it is the difference between
          writing under your own name and finding out later that you did not. */}
      {summary?.from_address ? (
        <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs">
          <div>
            <span className="text-muted-foreground">From </span>
            <span className="font-medium">{summary.from_address}</span>
            {summary.from_is_personal && (
              <Badge tone="success" className="ml-2">Your own address</Badge>
            )}
          </div>
          <div className="mt-0.5 text-muted-foreground">
            Replies come back to {summary.reply_to}
          </div>
          {summary.from_note && (
            <p className="mt-1.5 text-warning-foreground">{summary.from_note}</p>
          )}
        </div>
      ) : null}
      <Field label="To">
        <Input value={address} onChange={(e) => setAddress(e.target.value)} />
      </Field>
      <Field label="Subject">
        <Input value={line} onChange={(e) => setLine(e.target.value)} />
      </Field>
      <Field label="Message">
        <textarea
          rows={7}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder={draft.reply_to_id ? 'Write the reply...' : 'Write the message...'}
          className="w-full rounded-md border border-input bg-background p-3 text-sm shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </Field>

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
            'rounded-md border px-3 py-2 text-sm',
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

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          disabled={send.isPending || !body.trim()}
          onClick={() => send.mutate()}
        >
          {send.isPending ? 'Sending...' : 'Send'}
        </Button>
        <Button
          size="sm"
          disabled={
            saveDraft.isPending || !(address.trim() || line.trim() || body.trim())
          }
          onClick={() => saveDraft.mutate()}
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
