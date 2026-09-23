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
import { Button, Input, Spinner } from '../components/ui'
import { Icon, type IconName } from '../components/Icon'
import { CHANNEL_LABEL, Timeline } from '../components/dashboard/Timeline'
import {
  ComposeFields,
  EmailReader,
  ImportanceToggle,
  draftPayload,
  emptyDraft,
  sendProblem,
  sendable,
  type MailDraft,
  type SendResult,
} from '../components/dashboard/EmailReader'
import type { TimelineEntry } from '../components/dashboard/Timeline'
import type { RecipientSuggestion } from '../components/email'
import { textToHtml } from '../lib/email'
import { AssignTo } from '../components/dashboard/AssignTo'
import { CarPhoto } from '../components/CarPhoto'
import { AssistButton, Refused } from '../components/dashboard/AssistButton'

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
  /** The dealership's sign-off, appended on send. Shown in the composer so a
   *  rep sees the message rather than an approximation of it. */
  email_signature: string
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
  /* **One channel at a time, and it is one value.** This was two booleans
   * kept exclusive by hand -- `setEmailing(!emailing); setTexting(false)` at
   * each of two call sites -- which is two things that can disagree, and a
   * third channel would have made it three. The composer slot has room for
   * one composer, so the state that drives it is one word. */
  const [mode, setMode] = useState<Channel>('chat')
  // The email currently open in the reader, or null.
  const [reading, setReading] = useState<TimelineEntry | null>(null)
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

  /* **How this buyer can be reached, asked once.** The page used to answer
   * it in six places in three wordings -- and only the text button consulted
   * whether the provider was set up at all, so Email was offered on a
   * deployment that sends nothing and a phone-only buyer got an empty
   * toolbar. `/reach` is the one answer; the server owns it because the
   * provider, the TEXTING switch and an SMS opt-out are all facts the
   * browser has no business re-deriving. */
  // `/reach` is a fact about a buyer, and only the lead route has one in the
  // URL -- a conversation with a lead redirects to it further down, and one
  // without a lead has nobody to reach.
  const leadId = of === 'lead' ? id : ''

  const { data: reach } = useQuery({
    queryKey: ['reach', leadId],
    queryFn: () => api.get<Reach>(`/api/leads/${leadId}/reach`),
    enabled: Boolean(leadId),
  })

  useEffect(() => {
    setReply('')
    setChannel('')
    setMode('chat')
    setBooking(false)
  }, [id])

  const target = data?.reply_to ?? null
  const targetConvo = useMemo(
    () => data?.conversations.find((c) => c.id === target) ?? null,
    [data, target],
  )

  /* Who an email from this page is likely to go to, offered as somebody
   * types into To or Cc: every address the buyer is known by -- `/reach`'s
   * list, which is the one on their row and each a rep has linked -- and
   * then the people on this floor, because "pass this to finance" is the
   * other thing an email from a buyer's page is for. Suggestions only: the
   * box takes any address. */
  const suggestions = useMemo<RecipientSuggestion[]>(() => {
    const out: RecipientSuggestion[] = []
    const seen = new Set<string>()
    const add = (name: string, email: string) => {
      const key = email.trim().toLowerCase()
      if (!key || seen.has(key)) return
      seen.add(key)
      out.push({ name, email: email.trim() })
    }
    const buyer = data?.lead
    const known = reach?.email.addresses?.length
      ? reach.email.addresses.map((a) => a.address)
      : [buyer?.email ?? '', ...(buyer?.linked_addresses ?? []).map((a) => a.address)]
    for (const address of known) add(buyer?.name ?? '', address)
    for (const m of team?.members ?? []) if (m.active !== false) add(m.name, m.email)
    return out
  }, [data?.lead, reach, team])

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
  // There is no `lastInbound` any more, deliberately. Guessing which email a
  // rep meant to answer is the thing the reader removed: they press Open on
  // the one they are reading, and it is on screen while they write.

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
          conversation={targetConvo}
          conversations={data.conversations}
          onDecline={() => decline.mutate()}
          onBook={() => setBooking(true)}
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

        {/* The composers used to sit here, between the header and the
            timeline, as a `shrink-0` band above a `flex-1` list -- so opening
            one squeezed the buyer's history to nothing on a page whose whole
            job is showing that history. They are in the footer now, where
            they replace the reply box rather than stacking on top of it. */}

        <div className="scroll-thin flex-1 overflow-y-auto bg-muted/30">
          {entries.length === 0 ? (
            <p className="p-8 text-center text-sm text-muted-foreground">
              Nothing on this channel yet.
            </p>
          ) : (
            <Timeline entries={entries} markChannels onOpenEmail={setReading} />
          )}
        </div>

        {/* One email, in full. The timeline card is a summary -- clamped to
            three lines -- and until this existed there was nowhere the whole
            thing could be read, so a rep went to their own mail client and the
            reply left this system. The reader owns answering, too: Reply,
            Reply all and Forward are written under the message they answer,
            which is what threads them. Keyed on the email, so opening another
            one starts clean rather than carrying a half-written answer to the
            first into it. */}
        {lead && (
          <EmailReader
            key={reading?.id ?? 'none'}
            entry={reading}
            lead={lead}
            signature={data?.email_signature ?? ''}
            suggestions={suggestions}
            onClose={() => setReading(null)}
            onSent={invalidate}
          />
        )}

        {/* **The reach-out slot.** One picker, one composer, and a ceiling on
            how much of the page either may take. Replying is a choice here
            rather than an assumption: a buyer who rang in and left a number
            has no chat to reply on and may have no address either, and the
            old footer told them "email is the way back" regardless. */}
        <div className="shrink-0 border-t border-border bg-background">
          <ChannelPicker
            reach={reach}
            mode={mode}
            onPick={setMode}
            hasThread={target !== null}
            threadLabel={CHANNEL_LABEL[targetConvo?.channel ?? 'chat'] ?? 'Website chat'}
          />
          {/* Without a ceiling the composer reproduces the squeeze one edge
              lower: the email box is nine rows plus a subject plus a hint. */}
          <div
            className={clsx(
              'scroll-thin overflow-y-auto p-4',
              // An email is subject, a body worth reading, the draft controls
              // and Send. At 45vh Send sat below the fold of the footer's own
              // scroll, which read as a composer with no way to send.
              mode === 'email' ? 'max-h-[75vh]' : 'max-h-[45vh]',
            )}
          >
            {mode === 'email' && lead ? (
              <EmailReply
                lead={lead}
                signature={data?.email_signature ?? ''}
                suggestions={suggestions}
                onDone={() => { setMode('chat'); invalidate() }}
              />
            ) : mode === 'sms' && lead ? (
              <SmsComposer
                lead={lead}
                onDone={() => { setMode('chat'); invalidate() }}
              />
            ) : target === null ? (
              // Not a disabled box. Liner cannot open a chat with someone who
              // is not on the site, so there is no thread to reply on -- what
              // a dealer has is whatever the picker above found.
              <ClosedFooter lead={lead} reach={reach} onPick={setMode} />
            ) : targetConvo?.agent_paused === false ? (
              <LockedComposer onTakeover={() => takeover.mutate()} />
            ) : (
              <Composer
                name={name}
                leadId={lead?.id ?? null}
                conversationId={target}
                source={CHANNEL_LABEL[targetConvo?.channel ?? 'chat'] ?? 'Website chat'}
                value={reply}
                onChange={setReply}
                onSend={() => send.mutate()}
                sending={send.isPending}
              />
            )}
          </div>
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
  // choose between, and only ever lists what this buyer actually used: a
  // channel with no entries has no tab, so nothing sits permanently at zero.
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
  conversation,
  conversations,
  onDecline,
  onBook,
}: {
  name: string
  conversation: Conversation | null
  conversations: Conversation[]
  onDecline: () => void
  onBook: () => void
}) {
  const declined = conversations.some((c) => c.outcome === 'declined')
  const booked = conversations.some((c) => c.stage === 'booked')
  // **The channel buttons moved to the footer**, beside the composer they
  // open, so the control and the thing it controls are in one place and the
  // page has one answer to "how can this buyer be reached" instead of a
  // header that asked `/api/integrations` and a footer that asked nothing.

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
            <CarPhoto
              vin={lead.vehicle_of_interest.vin}
              photoUrl={lead.vehicle_of_interest.photo_url}
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
/** One buyer's reachable channels, from `GET /api/leads/{id}/reach`. */
export interface Reach {
  email: {
    /** The address on their row: still the one string it always was. */
    to: string
    /** Every address they are known by, the row's first and then each a rep
     *  has linked -- the composer's suggestions. */
    addresses?: { address: string; label: string }[]
    available: boolean
    reason: string
    delivers: boolean
    /** Whether Draft with Liner can write anything on this deployment. */
    draft: { available: boolean; reason: string }
  }
  sms: { to: string; available: boolean; reason: string; segment: number; max_body: number }
  call: { to: string; available: boolean; reason: string }
}

export type Channel = 'chat' | 'email' | 'sms'

/**
 * Which way to reach this buyer, offering only the ways that exist.
 *
 * **A channel nobody can use is not drawn.** No address means no Email, no
 * number means no Text and no Call, and a deployment that has not set Twilio
 * up means no Text for anybody -- the three are different facts and the
 * server keeps them apart, so the reason under an empty picker says which
 * one it is rather than a shrug.
 *
 * **Call is a `tel:` link and nothing more.** The Twilio number this system
 * holds is Liner's own -- `/ops/phone` rings prospects from it, behind
 * `require_owner` -- and a dealership has no outbound line here. So this
 * hands the number to the rep's own handset instead of implying a call this
 * product would place.
 */
function ChannelPicker({
  reach,
  mode,
  onPick,
  hasThread,
  threadLabel,
}: {
  reach: Reach | undefined
  mode: Channel
  onPick: (c: Channel) => void
  hasThread: boolean
  threadLabel: string
}) {
  const options: { key: Channel; label: string; hint: string }[] = []
  // **Text, not Reply**: it sits beside Email and a phone number, and a
  // row of ways to reach somebody reads as channels -- Reply is a verb that
  // could mean any of them. It is the box that types into their thread.
  if (hasThread) options.push({ key: 'chat', label: 'Text', hint: threadLabel })
  if (reach?.email.available) {
    options.push({
      key: 'email',
      label: 'Email',
      // The address, so a rep sees where it is going before they type.
      hint: reach.email.to,
    })
  }
  // SMS says so, because Text above is the thread: two buttons with one
  // label is a rep guessing which one reaches the buyer's phone.
  if (reach?.sms.available) options.push({ key: 'sms', label: 'SMS', hint: reach.sms.to })

  // Every reason the server gave, for the channels it did not offer. Shown
  // only when there is nothing at all to offer: listed beside three working
  // buttons it is noise, and with none it is the whole answer.
  const why = [reach?.email.reason, reach?.sms.reason, reach?.call.reason].filter(Boolean)

  if (!options.length && !reach?.call.available) {
    return (
      <div className="border-b border-border bg-muted/40 px-4 py-2.5">
        <p className="text-sm font-medium">No way to reach this buyer</p>
        {why.length > 0 && (
          <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
            {why.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5 border-b border-border bg-muted/40 px-4 py-2">
      {options.map((o) => (
        <button
          key={o.key}
          onClick={() => onPick(o.key)}
          title={o.hint}
          className={clsx(
            'rounded-md border px-2.5 py-1 text-sm font-medium transition-colors',
            mode === o.key
              ? 'border-foreground bg-foreground text-background'
              : 'border-input bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground',
          )}
        >
          {o.label}
        </button>
      ))}
      {reach?.call.available && (
        // An anchor rather than a button, because it is not a mode: nothing
        // opens in the slot below and this system places no call.
        <a
          href={`tel:${reach.call.to.replace(/[^\d+]/g, '')}`}
          className="rounded-md border border-input bg-background px-2.5 py-1 text-sm font-medium text-muted-foreground hover:bg-accent hover:text-accent-foreground"
          title={`Dial ${reach.call.to} from your own phone`}
        >
          Call {reach.call.to}
        </a>
      )}
      {reach?.email.available && !reach.email.delivers && (
        // The warning is only shown where there is something to refuse --
        // the rule `blocked_reason` already follows by not biting on a
        // sender that delivers nothing.
        <span className="ml-auto text-xs text-warning-foreground">
          Recorded only — no mail provider is configured.
        </span>
      )}
    </div>
  )
}

function ClosedFooter({
  lead,
  reach,
  onPick,
}: {
  lead: Lead | null
  reach: Reach | undefined
  onPick: (c: Channel) => void
}) {
  if (!lead) {
    return (
      <p className="text-center text-sm text-muted-foreground">
        This thread is closed, and there is no buyer to reach.
      </p>
    )
  }
  // **It used to say "Email is the way back" to everybody**, including a
  // buyer who rang in and left a number and no address -- telling a rep to
  // do the one thing they could not. What is offered is what the picker
  // above actually found.
  const ways: { key: Channel; label: string }[] = []
  if (reach?.email.available) ways.push({ key: 'email', label: 'Write them an email' })
  if (reach?.sms.available) ways.push({ key: 'sms', label: 'Send them an SMS' })

  return (
    <div className="mx-auto max-w-3xl">
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-dashed border-input bg-muted/40 px-4 py-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border bg-background text-muted-foreground">
          <Icon name="lock" className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">Every thread here is closed</div>
          <div className="text-xs text-muted-foreground">
            {ways.length > 0
              ? 'Liner cannot start a chat. Pick a way to reach them above.'
              : reach?.call.available
                ? 'Liner cannot start a chat, and there is no address on file. Their number is above.'
                : 'Liner cannot start a chat, and there is no way to reach this buyer on file.'}
          </div>
        </div>
        {ways.map((w) => (
          <Button key={w.key} size="sm" variant="secondary" onClick={() => onPick(w.key)}>
            {w.label}
          </Button>
        ))}
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

/** The reply box on a thread a rep has taken over. Two buttons: the writing
 *  assistant (Auto-generate, or Polish once there is text) and Send. The
 *  "Save as note" that sat between them was a placeholder for a note store
 *  that does not exist, and a control for nothing is not a third button. */
function Composer({
  name,
  leadId,
  conversationId,
  source,
  value,
  onChange,
  onSend,
  sending,
}: {
  name: string
  leadId: string | null
  conversationId: string | null
  source: string
  value: string
  onChange: (v: string) => void
  onSend: () => void
  sending: boolean
}) {
  const dealership = useDealership()
  const [problem, setProblem] = useState('')
  const [refused, setRefused] = useState<string[]>([])
  return (
    <div className="mx-auto max-w-3xl space-y-1.5">
    <div className="overflow-hidden rounded-lg border border-input focus-within:border-ring focus-within:ring-1 focus-within:ring-ring">
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
        <span className="ml-auto" />
        <AssistButton
          channel="chat"
          text={value}
          leadId={leadId}
          conversationId={conversationId}
          onProblem={setProblem}
          onDraft={(result) => {
            setRefused(result.violations ?? [])
            if (result.body) onChange(result.body)
          }}
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
    {problem && <p className="text-xs text-destructive">{problem}</p>}
    <Refused violations={refused} />
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


/** The two drafts the *server* builds from a lead's state, by `kind`.
 *
 *  Not templates in the browser: `_lead_draft` reads whether this buyer has a
 *  booked visit and names the slot, and `_credit_draft` refuses outright when
 *  no application URL is configured rather than mailing somebody an invitation
 *  to apply nowhere. The kind is also what makes the overview's credit-
 *  application count possible, because the send rewrites the link to
 *  `/r/<token>` and records which kind it was. */
const PRESETS = [
  ['followup', 'Follow-up'],
  ['credit_application', 'Credit application'],
] as const

type Preset = '' | (typeof PRESETS)[number][0]

/**
 * One email composer on the buyer page: write, or load a built draft.
 *
 * **Three ways to fill one box, and one Send.** These used to be two separate
 * composers -- a band for the server's drafts and this one for a reply --
 * stacked above the timeline, and the pick-one control was in the wrong place:
 * a rep answering a buyer's email had to know which of two boxes was the one
 * that knew what it was answering. So the presets are a *draft source* here,
 * beside "Draft with Liner", and what leaves is whatever is in the box when
 * Send is pressed. The band's own file went with it rather than being left
 * exported and unrendered: it also carried a line telling a rep that SMS was
 * out of scope and a call was the only way to reach a buyer, which stopped
 * being true and would have been read as current.
 *
 * **Answering one of their emails is the reader's job, not this box's.** This
 * took an `answering` entry for threading, and it was mounted with `undefined`
 * hard-coded, so the branch never ran and every email from here opened a new
 * thread whether or not the rep meant it to. Reply, Reply all and Forward now
 * live under the message they answer (`EmailReader`), which is where the
 * threading headers come from, and this says plainly that it starts a new
 * thread and points there.
 *
 * **The same boxes as every other composer**: To as chips, Cc and Bcc, a
 * formatted body, files. What stays here is where a draft comes from. Liner's
 * drafts and the built ones arrive as plain text -- the draft endpoint and the
 * guards behind it only ever write text -- and go into the editor through
 * `textToHtml`, so their paragraphs survive; the rep formats from there.
 *
 * **The send picks its endpoint by what the message is, not by which button
 * was pressed.** A hand-written note goes through `/api/email/compose`, the
 * same endpoint the mailbox uses. A preset goes through
 * `POST /api/leads/{id}/outreach` with its `kind`, because that is where the
 * credit application's link is rewritten to a countable one -- in the text
 * and the HTML both. Both go through `blocked_reason`, over every recipient:
 * there is one guard against a rehearsal mailing a real prospect and neither
 * path may skip it.
 */
function EmailReply({
  lead,
  signature,
  suggestions,
  onDone,
}: {
  lead: Lead
  /** What the send appends: this person's own sign-off, or their name and
   *  title over the dealership's details. */
  signature: string
  /** Addresses offered as somebody types into To or Cc. */
  suggestions: RecipientSuggestion[]
  onDone: () => void
}) {
  // To starts at the address on their row -- what "email this buyer" meant
  // when it was a line of text rather than a field -- and is theirs to change.
  const [draft, setDraft] = useState<MailDraft>(() => emptyDraft(lead.email))
  const patch = (p: Partial<MailDraft>) => setDraft((d) => ({ ...d, ...p }))
  const { subject, text: body } = draft
  const [problem, setProblem] = useState('')
  /** Why the guards refused a draft, shown rather than swallowed. */
  const [refused, setRefused] = useState<string[]>([])
  /** Which built draft is in the box, if any. It decides the `kind` the send
   *  records -- a rep tidying the wording of a credit application has not
   *  turned it into something else. */
  const [preset, setPreset] = useState<Preset>('')

  /* A built draft, fetched on demand rather than up front: `credit_application`
   * refuses when no URL is configured, and a composer that asked for both on
   * open would show that refusal to every rep writing an ordinary reply. */
  const built = useMutation({
    mutationFn: (kind: Preset) =>
      api.get<{ subject: string; body: string }>(
        `/api/leads/${lead.id}/outreach?draft=1&kind=${kind}`,
      ),
    onSuccess: (draftIn, kind) => {
      // Plain text from the server, into the editor as paragraphs. The
      // editor reports the text back a moment later, so `body` catches up.
      patch({ subject: draftIn.subject, html: textToHtml(draftIn.body), text: draftIn.body })
      setPreset(kind)
      setRefused([])
    },
    // The typed `not_configured` names the setting and the page to change it
    // on. That sentence is the whole answer to "why can I not send this".
    onError: (err: unknown) => {
      const payload = (err as ApiError)?.payload as { detail?: string } | undefined
      setProblem(payload?.detail || String((err as Error)?.message ?? err))
    },
  })

  /* Which subject the last draft wrote. A new draft replaces its own subject
   * but never one the rep typed: the box is theirs once they have touched it. */
  const [draftedSubject, setDraftedSubject] = useState('')

  const send = useMutation({
    mutationFn: () =>
      preset
        ? // A built draft keeps its kind, which is what rewrites the finance
          // link to `/r/<token>` and lets the overview count the applications
          // buyers actually opened. Answered here rather than in `compose`,
          // which has no notion of a lead's outreach kinds.
          api.post<SendResult>(`/api/leads/${lead.id}/outreach`, {
            ...draftPayload(draft),
            kind: preset,
          })
        : api.post<SendResult>('/api/email/compose', {
            ...draftPayload(draft),
            lead_id: lead.id,
          }),
    onSuccess: (result) => {
      // A refusal comes back as a stored failed row rather than an error, and
      // the sentence names the setting that would lift it. Showing it beats a
      // green tick over mail that never left the building.
      if (result.status !== 'sent') {
        setProblem(result.error || 'The provider did not accept it.')
        return
      }
      setDraft(emptyDraft(lead.email))
      setProblem('')
      setPreset('')
      onDone()
    },
    // A 400 names what it could not read -- the address that is not one, the
    // file that was refused -- in the server's own sentence.
    onError: (err: unknown) => setProblem(sendProblem(err)),
  })

  if (!lead.email) return null
  const presetLabel = (PRESETS.find(([k]) => k === preset) ?? [, ''])[1].toLowerCase()
  return (
    // No rule of its own: the footer's channel picker is the divider above
    // this, and a second one drew two lines a few pixels apart on a phone.
    <div className="min-w-0">
      <p className="mb-2 text-xs text-muted-foreground">
        {/* Which kind the send will record, said before it is pressed. A
            credit application is counted on the overview and carries a
            rewritten link, so "this is a follow-up" is a fact about the
            message rather than a label on a button. */}
        {preset
          ? `Sending as ${presetLabel}. It starts a new thread in their inbox.`
          : 'A new message, so it starts a new thread in their inbox. To answer one of theirs, open it on the timeline and press Reply.'}
      </p>
      <ComposeFields
        draft={draft}
        onChange={patch}
        suggestions={suggestions}
        // **Shown, not typed, and it carries their name.** Appended on the
        // way out by the compose endpoint, so a rep who could not see it
        // typed their name again or wondered why it was missing. A built
        // draft goes through the outreach endpoint, which appends nothing --
        // it signs itself in the body -- so the preview is not shown for one.
        signature={preset ? '' : signature}
        placeholder="Write the email..."
        // Room to read and edit a whole draft. Four rows showed the greeting
        // and a line of body, and the rest scrolled inside a box inside a
        // scrolling footer -- editing it meant finding it first.
        minHeight={200}
      />
      {problem && <p className="mt-1.5 text-xs text-destructive">{problem}</p>}
      <div className="mt-1.5"><Refused violations={refused} /></div>
      {/* The built drafts. A draft *source*, not a second composer: pressing
          one fills the box above, and the rep edits and sends it from there. */}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">Start from</span>
        {PRESETS.map(([kind, label]) => (
          <Button
            key={kind}
            size="sm"
            variant={preset === kind ? 'primary' : 'secondary'}
            disabled={built.isPending}
            onClick={() => { setProblem(''); built.mutate(kind) }}
          >
            {built.isPending && built.variables === kind ? 'Loading...' : label}
          </Button>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
        <ImportanceToggle
          value={draft.importance}
          onChange={(importance) => patch({ importance })}
          className="mr-auto"
        />
        {/* The writing assistant, beside Send: Auto-generate on an empty
            email, Polish on one with words in it. What is handed over to
            polish is the plain text -- the drafts and the guards behind them
            only read text -- so formatting is the rep's to re-apply. */}
        <AssistButton
          channel="email"
          text={body}
          leadId={lead.id}
          onProblem={setProblem}
          onDraft={(result) => {
            setRefused(result.violations ?? [])
            if (result.body) patch({ html: textToHtml(result.body), text: result.body })
            if (result.subject && (!subject.trim() || subject === draftedSubject)) {
              patch({ subject: result.subject })
              setDraftedSubject(result.subject)
            }
          }}
        />
        <Button
          size="sm"
          variant="primary"
          disabled={!sendable(draft) || send.isPending}
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

/** Texting a buyer, from the page their whole history is on.
 *
 * **A person writes this and a person presses send.** No assistant is
 * connected to SMS and there is deliberately no setting that would connect
 * one — see `app/sms.py`. The composer asks the server what it is allowed to
 * do *before* it opens, because "no number", "they texted STOP" and "Twilio is
 * not set up" are three different answers and only one of them is something a
 * rep can fix by typing.
 */
function SmsComposer({ lead, onDone }: { lead: Lead; onDone: () => void }) {
  const [body, setBody] = useState('')
  const [problem, setProblem] = useState('')
  const [draftRefused, setRefusedDraft] = useState<string[]>([])

  const { data: state } = useQuery({
    queryKey: ['lead-sms', lead.id],
    queryFn: () =>
      api.get<{
        configured: boolean
        to: string
        opted_out: boolean
        blocked: string
        segment: number
        max_body: number
      }>(`/api/leads/${lead.id}/sms`),
  })

  const send = useMutation({
    mutationFn: () =>
      api.post<{ sent: boolean; status: string; detail: string }>(
        `/api/leads/${lead.id}/sms`,
        { body },
      ),
    onSuccess: (result) => {
      // A refusal comes back 200 with the reason on it, because the row is
      // kept either way — the same shape the email composer uses. Reporting
      // only "sent" would hide the one message a rep needs to see again.
      if (!result.sent) {
        setProblem(result.detail || `The provider said: ${result.status}.`)
        return
      }
      setBody('')
      onDone()
    },
    onError: (e: unknown) => {
      const err = e as ApiError
      setProblem(String((err?.payload as { detail?: string })?.detail ?? err?.message ?? e))
    },
  })

  const segment = state?.segment ?? 160
  const over = state ? body.length > state.max_body : false
  // Blank until the server answers, so the composer never claims a refusal it
  // has not been told about.
  const refused = state?.blocked ?? ''

  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        Texting <span className="tnum font-medium text-foreground">{state?.to || lead.phone}</span>{' '}
        from the dealership&apos;s number. They can reply, and it lands on this timeline.
      </p>

      {refused ? (
        <p className="rounded-md border border-warning/30 bg-warning-muted p-2.5 text-xs leading-relaxed text-warning-foreground">
          {refused}
        </p>
      ) : (
        <>
          <textarea
            value={body}
            onChange={(e) => { setBody(e.target.value); setProblem('') }}
            rows={3}
            placeholder={`Text ${lead.name || 'them'}...`}
            className="w-full resize-y rounded-md border border-input bg-background p-2 text-sm outline-none focus:border-ring focus:ring-1 focus:ring-ring"
          />
          <div className="flex flex-wrap items-center gap-2">
            {/* Said before the send, not billed silently after it: a text is
                charged per segment, and a rep pasting a paragraph has no other
                way to know they are sending three messages. */}
            <span className="text-[11px] text-muted-foreground">
              {body.length} characters
              {body.length > segment
                ? ` · ${Math.ceil(body.length / segment)} messages`
                : ''}
            </span>
            <div className="ml-auto flex gap-2">
              {/* Two buttons, like every composer here: the writing
                  assistant and Send. Cancel was a third way to do what the
                  channel picker above already does. */}
              <AssistButton
                channel="sms"
                text={body}
                leadId={lead.id}
                onProblem={setProblem}
                onDraft={(result) => {
                  setRefusedDraft(result.violations ?? [])
                  if (result.body) setBody(result.body)
                }}
              />
              <Button
                size="sm"
                variant="primary"
                disabled={!body.trim() || over || send.isPending}
                onClick={() => send.mutate()}
              >
                {send.isPending ? 'Sending...' : 'Send text'}
              </Button>
            </div>
          </div>
        </>
      )}

      {state && !state.configured && (
        <p className="text-[11px] text-muted-foreground">
          Twilio is not configured on this install, so nothing will leave the
          building.
        </p>
      )}
      {problem && <p className="text-xs text-destructive">{problem}</p>}
      <Refused violations={draftRefused} />
    </div>
  )
}
