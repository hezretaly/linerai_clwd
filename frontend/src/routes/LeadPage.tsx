import { useEffect, useMemo, useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'

import { api, ApiError } from '../lib/api'
import { useDealership } from '../lib/dealership'
import { PROVENANCE_LABEL, initials, money, relative } from '../lib/format'
import type { BookingCardData } from '../components/BookingCard'
import { BookingCard } from '../components/BookingCard'
import type { Conversation, Lead, TeamMember } from '../lib/types'
import { Button, Input, Spinner, Unavailable } from '../components/ui'
import { Icon, type IconName } from '../components/Icon'
import { CHANNEL_LABEL, Timeline } from '../components/dashboard/Timeline'
import type { TimelineEntry } from '../components/dashboard/Timeline'
import { LeadComposers } from '../components/dashboard/LeadDrawer'
import { AssignTo } from '../components/dashboard/AssignTo'

/* One buyer, one page.
 *
 * The dashboard used to be organised by thread -- a chat here, a call there,
 * email somewhere else -- so a buyer who chatted at 9pm and rang back next
 * morning was three unrelated screens, and a rep could call someone who had
 * already booked. This is the whole relationship in the order it happened.
 *
 * It serves two shapes. A lead has everything: every conversation, their
 * outreach, their appointments. A conversation with no lead yet -- an
 * anonymous chat, which is most of them until someone books -- has only
 * itself, and still has to be readable and answerable. Same component, two
 * endpoints, because the difference is where the entries come from and not
 * what they look like.
 */

interface TimelinePayload {
  lead: Lead | null
  entries: TimelineEntry[]
  channels: Record<string, number>
  conversations: Conversation[]
  /** Composed from rows on the newest thread -- who, which car, what was
   *  captured, where it got to. Not `summary`, which is Liner's last reply. */
  recap: string
  /** Which thread a reply lands on, or null when every one is closed. */
  reply_to: string | null
}

interface Duplicate {
  reason: string
  lead: Lead
}

export function LeadPage({ of }: { of: 'lead' | 'conversation' }) {
  const { id } = useParams()
  // Whose name goes on the message. A rep typing into a buyer's thread is
  // writing as the dealership, and telling them it is Riverside Auto on a
  // rebranded instance is telling them the wrong thing about what they send.
  const dealership = useDealership()
  const queryClient = useQueryClient()
  const [reply, setReply] = useState('')
  const [booking, setBooking] = useState(false)
  const [emailing, setEmailing] = useState(false)
  const [channel, setChannel] = useState('')

  const base = of === 'lead' ? `/api/leads/${id}` : `/api/conversations/${id}`

  const { data, isLoading, error } = useQuery({
    queryKey: ['timeline', of, id],
    queryFn: () => api.get<TimelinePayload>(`${base}/timeline`),
    enabled: Boolean(id),
  })

  const { data: dupes } = useQuery({
    queryKey: ['duplicates', id],
    queryFn: () => api.get<{ duplicates: Duplicate[] }>(`/api/leads/${id}/duplicates`),
    enabled: of === 'lead' && Boolean(id),
  })

  const { data: team } = useQuery({
    queryKey: ['team'],
    queryFn: () => api.get<{ members: TeamMember[] }>('/api/team'),
  })

  useEffect(() => {
    setReply('')
    setChannel('')
    setEmailing(false)
    setBooking(false)
  }, [id])

  const target = data?.reply_to ?? null
  const targetConvo = useMemo(
    () => data?.conversations.find((c) => c.id === target) ?? null,
    [data, target],
  )

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['timeline'] })
    void queryClient.invalidateQueries({ queryKey: ['conversations'] })
    void queryClient.invalidateQueries({ queryKey: ['leads'] })
    void queryClient.invalidateQueries({ queryKey: ['overview'] })
  }

  // Written out rather than built by a helper: these are hooks, and a helper
  // that returns one is a rule-of-hooks violation waiting for someone to put
  // it behind an `if`.
  const takeover = useMutation({
    mutationFn: () => api.post(`/api/conversations/${target}/takeover`),
    onSuccess: invalidate,
  })
  const handback = useMutation({
    mutationFn: () => api.post(`/api/conversations/${target}/handback`),
    onSuccess: invalidate,
  })
  const decline = useMutation({
    mutationFn: () => api.post(`/api/conversations/${target}/decline`),
    onSuccess: invalidate,
  })
  const send = useMutation({
    mutationFn: () => api.post(`/api/conversations/${target}/messages`, { content: reply }),
    onSuccess: () => {
      setReply('')
      invalidate()
    },
  })

  if (isLoading) return <Spinner />
  if (error || !data) {
    return (
      <main className="p-6">
        <p className="text-sm text-destructive">
          {(error as ApiError | null)?.message ?? 'Not found.'}
        </p>
        <Link to="/app/conversations" className="text-sm text-primary hover:underline">
          Back to conversations
        </Link>
      </main>
    )
  }

  const lead = data.lead
  const name = lead?.name || 'Unnamed buyer'
  // Their last inbound email, which is what a reply answers. Read off the same
  // timeline the page is rendering rather than fetched again -- two answers to
  // "what are we replying to" is how a reply threads under the wrong message.
  const lastInbound = [...(data?.entries ?? [])]
    .reverse()
    .find((e) => e.kind === 'outreach' && e.direction === 'in' && e.channel === 'email')

  const entries = channel
    ? // Appointments and escalations carry no channel: they happened, rather
      // than being said on one, so filtering them out would hide the booking a
      // rep came to check.
      data.entries.filter((e) => !e.channel || e.channel === channel)
    : data.entries

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col lg:flex-row">
      <LeadRail
        lead={lead}
        recap={data.recap}
        team={team?.members ?? []}
        duplicates={dupes?.duplicates ?? []}
        conversation={targetConvo}
      />

      <section className="flex min-w-0 flex-1 flex-col">
        <Header
          name={name}
          lead={lead}
          conversation={targetConvo}
          conversations={data.conversations}
          onDecline={() => decline.mutate()}
          onBook={() => setBooking(true)}
          emailing={emailing}
          onEmail={() => setEmailing(!emailing)}
        />

        {/* All is the sum of the tabs beside it, not the number of rows below.
            Counting rows put `All 20` next to `Email 1` and `Voice call 17`,
            which do not add up and are not the same unit -- one is entries,
            the other is now contacts. A strip whose parts do not sum to its
            total is one a manager has to reverse-engineer. */}
        <ChannelStrip
          channels={data.channels}
          active={channel}
          total={Object.values(data.channels).reduce((n, c) => n + c, 0)}
          onPick={setChannel}
        />

        {targetConvo?.agent_paused && (
          <Banner
            tone="primary"
            title={`You are replying as ${dealership?.name || 'the dealership'}`}
            sub="Liner is paused on this thread and will not send anything."
            action={{ label: 'Hand back to Liner', onClick: () => handback.mutate() }}
          />
        )}

        {booking && target && (
          <RepBooking conversationId={target} onDone={() => { setBooking(false); invalidate() }} />
        )}

        {/* Outreach hangs off the buyer, not off a thread, so it stays
            available whether or not one is open -- a rep sending a follow-up
            to someone mid-chat is the normal case, not an edge one. */}
        {emailing && lead && (
          <div className="shrink-0 border-b border-border bg-muted/40 p-4">
            <LeadComposers
              lead={lead}
              onDone={() => { setEmailing(false); invalidate() }}
            />
            {/* Answering what they actually wrote, rather than sending one of
                the two drafts above. A buyer mid-exchange has asked a
                question; a templated follow-up is not an answer to it, and a
                rep who has to leave for their own mail client takes the reply
                out of this system for good. */}
            <EmailReply
              lead={lead}
              answering={lastInbound}
              onDone={() => { setEmailing(false); invalidate() }}
            />
          </div>
        )}

        <div className="scroll-thin flex-1 overflow-y-auto bg-muted/30">
          {entries.length === 0 ? (
            <p className="p-8 text-center text-sm text-muted-foreground">
              Nothing on this channel yet.
            </p>
          ) : (
            <Timeline entries={entries} markChannels />
          )}
        </div>

        <div className="shrink-0 border-t border-border bg-background p-4">
          {target === null ? (
            // Not a disabled box. Liner cannot open a chat with someone who is
            // not on the site, so there is no thread to reply on -- what a
            // dealer actually has is an email, and that is what is offered.
            <ClosedFooter lead={lead} />
          ) : targetConvo?.agent_paused === false ? (
            <LockedComposer onTakeover={() => takeover.mutate()} />
          ) : (
            <Composer
              name={name}
              source={CHANNEL_LABEL[targetConvo?.channel ?? 'chat'] ?? 'Website chat'}
              value={reply}
              onChange={setReply}
              onSend={() => send.mutate()}
              sending={send.isPending}
            />
          )}
        </div>
      </section>
    </div>
  )
}

function ChannelStrip({
  channels,
  active,
  total,
  onPick,
}: {
  channels: Record<string, number>
  active: string
  total: number
  onPick: (c: string) => void
}) {
  const keys = Object.keys(channels).sort()
  // One channel is not a choice. The strip appears when there is something to
  // choose between, and never lists a channel this system cannot do -- there
  // is no SMS provider, so there is no SMS tab sitting permanently at zero.
  if (keys.length < 2) return null

  const chip = (key: string, label: string, count: number) => (
    <button
      key={key}
      onClick={() => onPick(key)}
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors',
        active === key
          ? 'border-foreground bg-foreground text-background'
          : 'border-input bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground',
      )}
    >
      {label}
      <span className="tnum opacity-70">{count}</span>
    </button>
  )

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-border bg-background px-4 py-2">
      {chip('', 'All', total)}
      {keys.map((key) => chip(key, CHANNEL_LABEL[key] ?? key, channels[key]))}
    </div>
  )
}

function Header({
  name,
  lead,
  conversation,
  conversations,
  onDecline,
  onBook,
  emailing,
  onEmail,
}: {
  name: string
  lead: Lead | null
  conversation: Conversation | null
  conversations: Conversation[]
  onDecline: () => void
  onBook: () => void
  emailing: boolean
  onEmail: () => void
}) {
  const declined = conversations.some((c) => c.outcome === 'declined')
  const booked = conversations.some((c) => c.stage === 'booked')

  return (
    <div className="sticky top-14 z-10 flex h-14 shrink-0 items-center gap-3 border-b border-border bg-background px-4 md:static md:px-5">
      <Link
        to="/app/conversations"
        aria-label="Back to conversations"
        className="-ml-2 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground lg:hidden"
      >
        <Icon name="back" className="h-5 w-5" />
      </Link>
      <div className="hidden h-9 w-9 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold sm:flex">
        {initials(name)}
      </div>
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold leading-tight">{name}</div>
        {/* Which thread the buttons below act on. Without this a rep pressing
            Client declined on a page holding three threads has no idea which
            one they just closed. */}
        <div className="truncate text-xs text-muted-foreground">
          {conversation
            ? `Acting on the ${CHANNEL_LABEL[conversation.channel]?.toLowerCase() ?? 'thread'} from ${relative(conversation.started_at)}`
            : `${conversations.length} thread${conversations.length === 1 ? '' : 's'}, all closed`}
        </div>
      </div>
      <div className="ml-auto hidden shrink-0 items-center gap-2 lg:flex">
        {lead?.email ? (
          <button
            onClick={onEmail}
            className={clsx(
              'inline-flex h-8 items-center rounded-md border px-3 text-xs font-medium transition-colors',
              emailing
                ? 'border-primary bg-primary text-primary-foreground'
                : 'border-input bg-background hover:border-primary hover:bg-primary hover:text-primary-foreground',
            )}
          >
            {emailing ? 'Cancel' : 'Email them'}
          </button>
        ) : (
          lead && (
            <Unavailable
              label="Email them"
              size="sm"
              why="No email on file, and SMS is out of scope, so this product has no way to reach them. A rep has to call."
            />
          )
        )}
        {conversation && (
          <>
          {declined ? (
            <span className="inline-flex h-8 items-center rounded-md bg-muted px-3 text-xs font-medium text-muted-foreground">
              Client declined
            </span>
          ) : (
            <button
              onClick={onDecline}
              className="inline-flex h-8 items-center rounded-md border border-input bg-background px-3 text-xs font-medium transition-colors hover:border-destructive hover:bg-destructive hover:text-destructive-foreground"
            >
              Client declined
            </button>
          )}
          {booked ? (
            <span className="inline-flex h-8 items-center rounded-md bg-muted px-3 text-xs font-medium text-muted-foreground">
              Appointment set
            </span>
          ) : (
            <button
              onClick={onBook}
              className="inline-flex h-8 items-center rounded-md border border-input bg-background px-3 text-xs font-medium transition-colors hover:border-primary hover:bg-primary hover:text-primary-foreground"
            >
              Appointment set
            </button>
          )}
          </>
        )}
      </div>
    </div>
  )
}

function Banner({
  tone,
  title,
  sub,
  action,
}: {
  tone: 'primary'
  title: string
  sub: string
  action: { label: string; onClick: () => void }
}) {
  return (
    <div
      className={clsx(
        'flex shrink-0 flex-wrap items-center gap-3 px-5 py-3',
        tone === 'primary' && 'bg-primary text-primary-foreground',
      )}
    >
      <Icon name="user" className="h-4 w-4 shrink-0" />
      <div>
        <div className="text-sm font-medium">{title}</div>
        <div className="text-xs opacity-80">{sub}</div>
      </div>
      <button
        onClick={action.onClick}
        className="ml-auto inline-flex h-8 items-center rounded-md border border-white/30 bg-white/15 px-3 text-xs font-medium transition-colors hover:bg-white/25"
      >
        {action.label}
      </button>
    </div>
  )
}

function LeadRail({
  lead,
  recap,
  team,
  duplicates,
  conversation,
}: {
  lead: Lead | null
  recap: string
  team: TeamMember[]
  duplicates: Duplicate[]
  conversation: Conversation | null | undefined
}) {
  const contact: { icon: IconName; value: string; prov?: string }[] = [
    { icon: 'phone', value: lead?.phone || 'Not given', prov: lead?.phone ? 'caller_id' : undefined },
    { icon: 'mail', value: lead?.email || 'Not given', prov: lead?.email ? 'typed' : undefined },
  ]
  const assignee = team.find((m) => m.id === lead?.assigned_user_id)

  return (
    <aside className="scroll-thin hidden w-[300px] shrink-0 overflow-y-auto border-r border-border bg-background xl:block">
      <div className="border-b border-border p-5">
        <div className="text-base font-semibold leading-tight">
          {lead?.name ?? 'Unnamed buyer'}
        </div>
        <div className="mb-3" />
        {contact.map((row) => (
          <div key={row.icon} className="flex items-center gap-2 py-1 text-sm">
            <Icon name={row.icon} className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className={clsx('truncate', row.value === 'Not given' && 'text-muted-foreground')}>
              {row.value}
            </span>
            {row.prov && (
              <span className="ml-auto shrink-0 rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {PROVENANCE_LABEL[row.prov]}
              </span>
            )}
          </div>
        ))}
        {lead && <LinkedAddresses lead={lead} />}
      </div>

      {/* Detection only -- nothing here merges anything. The reason is shown
          because "we matched them, trust us" is not something a rep can check,
          and a shared household number is a real thing. */}
      {duplicates.length > 0 && (
        <div className="border-b border-border bg-warning-muted p-5">
          <div className="mb-2 text-xs font-medium text-warning-foreground">
            Possible duplicate
          </div>
          {duplicates.map((d) => (
            <p key={d.lead.id} className="text-sm leading-relaxed text-warning-foreground">
              Looks like{' '}
              <Link to={`/app/leads/${d.lead.id}`} className="font-medium underline">
                {d.lead.name || d.lead.email || 'another lead'}
              </Link>{' '}
              — {d.reason}.
            </p>
          ))}
          <p className="mt-2 text-[11px] leading-relaxed text-warning-foreground/80">
            Nothing merges them yet. Both rows stay as they are.
          </p>
        </div>
      )}

      {lead?.vehicle_of_interest && (
        <div className="border-b border-border p-5">
          <div className="mb-3 text-xs font-medium text-muted-foreground">Vehicle of interest</div>
          <div className="flex gap-3">
            <img
              src={lead.vehicle_of_interest.photo_url}
              alt=""
              className="h-12 w-16 shrink-0 rounded-md border border-border object-cover"
            />
            <div className="min-w-0">
              <div className="text-sm font-medium leading-snug">
                {lead.vehicle_of_interest.title}
              </div>
              <div className="tnum mt-1 text-sm font-semibold">
                {money(lead.vehicle_of_interest.price)}
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="border-b border-border p-5">
        <div className="mb-2 text-xs font-medium text-muted-foreground">Summary</div>
        {/* `recap`, composed on the server from rows, not `summary` -- that is
            whatever Liner said last, which is a reply and not a summary. */}
        {recap ? (
          <p className="text-sm leading-relaxed">{recap}</p>
        ) : (
          <p className="text-sm text-muted-foreground">
            Nothing said yet — the timeline beside this is empty.
          </p>
        )}
      </div>

      {/* The Captured by Liner panel stood here: every field Liner picked up,
          each wearing its provenance badge. Taken out on request -- it is the
          rail's third block of the same buyer, and the fields are read where
          they are acted on rather than as a list beside the thread.
          `captured_fields` is untouched: `save_captured_fields` still records
          provenance, `buyer_summary` still puts only `typed` fields in the
          buyer's email, and `lead_recap` above still refuses to restate any of
          them -- prose cannot carry a badge, and a guess repeated without one
          is how a rep asserts it on the phone. */}

      {/* Read-only until now, with a note saying an owner appears when an
          appointment is assigned from the calendar. So the one screen where a
          manager reads a buyer's whole history -- and forms the opinion about
          who should take them -- was the one screen that could not act on it,
          and a buyer who never booked could not be given to anybody at all.
          The same control as the overview, because it is the same act: two
          ways to assign is how one of them stops claiming the escalations. */}
      {lead && (
        <div className="border-b border-border p-5">
          <div className="mb-2 text-xs font-medium text-muted-foreground">Assigned to</div>
          {/* Stacked, not side by side. The rail is 300px, and putting the
              button next to the name squeezed it to "Unclaimed — in th…" --
              which is the one word on this panel a manager is reading. */}
          <div className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm">
            {assignee ? assignee.name : 'Unclaimed — in the pool'}
          </div>
          <div className="mt-2">
            <AssignTo
              leadId={lead.id}
              assignedTo={assignee ?? lead.assigned_to}
              conversationId={conversation?.id ?? null}
              thread={`/app/leads/${lead.id}`}
            />
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            Assigning gives them an owner and settles anything of theirs waiting for
            one. Liner keeps replying unless you take the thread over.
          </p>
        </div>
      )}
    </aside>
  )
}

/** Every thread is closed, so there is nothing to reply on. Not a disabled
 *  reply box: Liner cannot open a chat with someone who is not on the site, so
 *  a box that looked usable would be a lie. Email is what a dealer actually
 *  has left, and it is one button up in the header. */
function ClosedFooter({ lead }: { lead: Lead | null }) {
  if (!lead) {
    return (
      <p className="text-center text-sm text-muted-foreground">
        This thread is closed, and there is no lead to email.
      </p>
    )
  }
  return (
    <div className="mx-auto max-w-3xl">
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-dashed border-input bg-muted/40 px-4 py-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border bg-background text-muted-foreground">
          <Icon name="lock" className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <div className="text-sm font-medium">Every thread here is closed</div>
          <div className="text-xs text-muted-foreground">
            Liner cannot start a chat. Email is the way back to this buyer.
          </div>
        </div>
      </div>
    </div>
  )
}

function LockedComposer({ onTakeover }: { onTakeover: () => void }) {
  return (
    <div className="mx-auto flex max-w-3xl items-center gap-3 rounded-lg border border-dashed border-input bg-muted/40 px-4 py-3">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border bg-background text-muted-foreground">
        <Icon name="lock" className="h-4 w-4" />
      </span>
      <div className="min-w-0">
        <div className="text-sm font-medium">Liner is holding this conversation</div>
        <div className="text-xs text-muted-foreground">
          It won't reply again until someone takes over or hands it back.
        </div>
      </div>
      <button
        onClick={onTakeover}
        className="ml-auto inline-flex h-9 shrink-0 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
      >
        Take over
      </button>
    </div>
  )
}

function Composer({
  name,
  source,
  value,
  onChange,
  onSend,
  sending,
}: {
  name: string
  source: string
  value: string
  onChange: (v: string) => void
  onSend: () => void
  sending: boolean
}) {
  const dealership = useDealership()
  return (
    <div className="mx-auto max-w-3xl overflow-hidden rounded-lg border border-input focus-within:border-ring focus-within:ring-1 focus-within:ring-ring">
      <textarea
        rows={3}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={`Reply to ${name}...`}
        className="w-full resize-none bg-background px-4 py-3 text-sm outline-none placeholder:text-muted-foreground"
      />
      <div className="flex items-center gap-2 border-t border-border bg-muted/40 px-3 py-2">
        <span className="text-xs text-muted-foreground">
          Sending as{' '}
          <b className="font-medium text-foreground">{dealership?.name || 'the dealership'}</b> ·{' '}
          {source}
        </span>
        <Unavailable
          label="Save as note"
          size="sm"
          className="ml-auto"
          why="There is no internal note store. Everything written here is sent to the buyer."
        />
        <button
          onClick={onSend}
          disabled={!value.trim() || sending}
          className="inline-flex h-8 items-center rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:pointer-events-none disabled:opacity-50"
        >
          {sending ? 'Sending...' : 'Send reply'}
        </button>
      </div>
    </div>
  )
}

/** A rep booking on the buyer's behalf: the same day/time/contact card the
 *  buyer is shown, posted to the rep endpoint. Both land on the same
 *  `book_appointment` executor, so the hours rule and the clash check hold
 *  whoever presses the button -- only `booked_by` differs. */
function RepBooking({
  conversationId,
  onDone,
}: {
  conversationId: string
  onDone: () => void
}) {
  const { data, error } = useQuery({
    queryKey: ['availability', conversationId],
    queryFn: () => api.get<BookingCardData>(`/api/conversations/${conversationId}/availability`),
    retry: false,
  })

  return (
    <div className="shrink-0 border-b border-border bg-muted/40 p-4">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-sm font-medium">Book this buyer in</p>
        <button onClick={onDone} className="text-xs text-muted-foreground hover:text-foreground">
          Cancel
        </button>
      </div>
      {error ? (
        <p className="text-sm text-destructive">{(error as ApiError).message}</p>
      ) : !data ? (
        <Spinner label="Checking the calendar" />
      ) : (
        <BookingCard
          data={data}
          submit={async (payload) => {
            await api.post(`/api/conversations/${conversationId}/book`, payload)
            onDone()
          }}
        />
      )}
    </div>
  )
}

/** `/app/conversations/:id` predates the split by buyer, and the overview, the
 *  seed and any bookmark still point at it. A thread that has a lead belongs
 *  on that buyer's page -- one place, not two -- so this asks the API which it
 *  is rather than guessing. A thread with no lead is nobody yet, and opens on
 *  its own. */
export function LeadRedirect() {
  const { id } = useParams()
  const { data, isLoading } = useQuery({
    queryKey: ['conversations', id],
    queryFn: () => api.get<Conversation>(`/api/conversations/${id}`),
    enabled: Boolean(id),
  })

  if (isLoading) return <Spinner />
  if (data?.lead_id) return <Navigate to={`/app/leads/${data.lead_id}`} replace />
  return <LeadPage of="conversation" />
}


/**
 * Answer the email a buyer actually sent.
 *
 * Separate from `LeadComposers` above, which sends a *draft the server built*
 * from the lead's state -- a follow-up, a credit application. Those are right
 * for opening a conversation and wrong for continuing one: a buyer who asked
 * whether the Silverado is still there has not been answered by a templated
 * first touch.
 *
 * It goes through `/api/email/compose`, the same endpoint the mailbox uses, so
 * it obeys `blocked_reason` like every other send and files against the buyer.
 * `in_reply_to_outreach_id` is what makes their client keep one thread instead
 * of opening a second conversation about the same car.
 */
function EmailReply({
  lead,
  answering,
  onDone,
}: {
  lead: Lead
  answering: TimelineEntry | undefined
  onDone: () => void
}) {
  const parent = answering?.subject ?? ''
  // Not "Re: Re: Re:". A buyer who replies four times should not end up with a
  // subject line that is mostly prefix.
  const [subject, setSubject] = useState(
    parent ? (/^re:/i.test(parent) ? parent : `Re: ${parent}`) : '',
  )
  const [body, setBody] = useState('')
  const [problem, setProblem] = useState('')

  const send = useMutation({
    mutationFn: () =>
      api.post<{ status: string; error?: string; blocked?: boolean }>(
        '/api/email/compose',
        {
          to: lead.email,
          subject,
          body,
          lead_id: lead.id,
          in_reply_to_outreach_id: answering?.id,
        },
      ),
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
      onDone()
    },
    onError: (err: unknown) => setProblem(String((err as Error)?.message ?? err)),
  })

  if (!lead.email) return null
  return (
    <div className="mt-3 border-t border-border pt-3">
      <div className="mb-2 text-xs text-muted-foreground">
        Reply to <span className="font-medium text-foreground">{lead.email}</span>
        {answering
          ? ` · under "${parent || '(no subject)'}"`
          : ' · this starts a new thread in their inbox'}
      </div>
      <Input
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        placeholder="Subject"
        className="mb-2"
      />
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={4}
        placeholder="Write the reply..."
        className="w-full resize-y rounded-md border border-input bg-background p-2 text-sm outline-none focus:border-ring focus:ring-1 focus:ring-ring"
      />
      {problem && <p className="mt-1.5 text-xs text-destructive">{problem}</p>}
      <div className="mt-2 flex justify-end">
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
  )
}


/**
 * Other addresses this buyer writes from, and the control that adds one.
 *
 * `leads.email` is one column and a buyer is not. Somebody who chatted from a
 * work address and later mails from a personal one is one person that no rule
 * here can see -- matching is email exact and phone by its last ten digits,
 * deliberately, because a name is not identity and two Dave Joneses are two
 * people. So this is a rep saying "these are the same person", which is the
 * only thing that can honestly make that join.
 *
 * The count of what it claimed is shown afterwards. A link that appears to do
 * nothing is one somebody presses again.
 */
function LinkedAddresses({ lead }: { lead: Lead }) {
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [value, setValue] = useState('')
  const [note, setNote] = useState('')
  const linked = lead.linked_addresses ?? []

  const link = useMutation({
    mutationFn: () =>
      api.post<{ claimed: number }>(`/api/leads/${lead.id}/addresses`, { address: value }),
    onSuccess: (result) => {
      setNote(
        result.claimed
          ? `Linked. ${result.claimed} earlier message${result.claimed === 1 ? '' : 's'} moved onto their timeline.`
          : 'Linked. Nothing earlier was waiting under that address.',
      )
      setValue('')
      setAdding(false)
      queryClient.invalidateQueries({ queryKey: ['lead', lead.id] })
    },
    // The 409 names who already owns the address, which is the useful half:
    // merging two buyers is not something this can do on its own.
    onError: (err: unknown) => setNote(String((err as ApiError)?.message ?? err)),
  })

  return (
    <div className="mt-1">
      {linked.map((row) => (
        <div key={row.id} className="flex items-center gap-2 py-1 text-sm">
          <Icon name="mail" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate text-muted-foreground">{row.address}</span>
          <span className="ml-auto shrink-0 rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">
            linked
          </span>
        </div>
      ))}
      {adding ? (
        <div className="mt-1.5">
          <Input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="their other address"
            className="mb-1.5"
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="primary"
              disabled={!value.includes('@') || link.isPending}
              onClick={() => link.mutate()}
            >
              {link.isPending ? 'Linking...' : 'Link'}
            </Button>
            <Button size="sm" onClick={() => { setAdding(false); setValue('') }}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => { setAdding(true); setNote('') }}
          className="mt-1 text-xs font-medium text-primary hover:underline"
        >
          Link another address
        </button>
      )}
      {note && <p className="mt-1.5 text-xs text-muted-foreground">{note}</p>}
    </div>
  )
}
