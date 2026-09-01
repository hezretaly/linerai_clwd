import clsx from 'clsx'

import { dateTime, money, time } from '../../lib/format'
import type { Vehicle, User } from '../../lib/types'
import { Icon } from '../Icon'

/* One buyer, every channel, in the order it happened.
 *
 * The entries are composed on the server (`app/timeline.py`) rather than
 * merged here: an appointment email exists twice in the database -- once as an
 * `outreach` row and once mirrored into the thread so the round trip lands
 * visibly -- and deciding which of those a rep sees is not a rendering
 * question. This file only draws what it is given.
 */

export interface TimelineEntry {
  kind: 'call' | 'message' | 'outreach' | 'appointment' | 'escalation'
  id: string
  at: string
  /** 'chat' | 'voice' | 'email' | 'phone_logged', or '' for things that
   *  happened rather than were said. */
  channel: string
  conversation_id: string | null

  // message
  role?: 'buyer' | 'assistant' | 'rep'
  content?: string
  tool_calls?: { name: string }[]

  // outreach
  direction?: 'out' | 'in'
  subject?: string
  body?: string
  outreach_kind?: string
  to_address?: string
  trackable?: boolean
  opened?: boolean
  click_count?: number
  delivered_externally?: boolean
  error?: string | null
  in_thread?: boolean

  // appointment
  starts_at?: string
  status?: string
  booked_by?: string
  vehicle?: Vehicle | null

  // call
  seconds?: number
  live?: boolean
  has_recording?: boolean
  recording_seconds?: number
  recording_complete?: boolean
  both_sides?: boolean

  // escalation
  reason?: string
  claimed_at?: string | null
  claimed_by?: User | null
}

export const CHANNEL_LABEL: Record<string, string> = {
  chat: 'Website chat',
  voice: 'Voice call',
  email: 'Email',
  // Seeded demo threads only. There is no Meta integration -- nothing is sent
  // or received on either -- and `/api/campaigns` says which app and webhook
  // it would take. They are here because the buyer page and the conversations
  // list are channel-agnostic by construction, and this is what proves it:
  // one person's Instagram message sits in the same timeline as their call.
  instagram: 'Instagram',
  facebook: 'Facebook',
  phone_logged: 'Logged call',
}

const CHANNEL_ICON: Record<string, 'chat' | 'voice' | 'mail' | 'phone'> = {
  chat: 'chat',
  voice: 'voice',
  email: 'mail',
  instagram: 'chat',
  facebook: 'chat',
  phone_logged: 'phone',
}

/** A marker on anything that did not arrive on the channel above it. On a page
 *  that mixes chat, calls and email, "who said this and where" is the one
 *  thing a rep cannot infer from the text. */
function ChannelMark({ channel }: { channel: string }) {
  if (!channel) return null
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
      <Icon name={CHANNEL_ICON[channel] ?? 'chat'} className="h-3 w-3 shrink-0" />
      {CHANNEL_LABEL[channel] ?? channel}
    </span>
  )
}

function Message({ e, showChannel }: { e: TimelineEntry; showChannel: boolean }) {
  const stamp = time(e.at)

  if (e.role === 'buyer') {
    return (
      <>
        <div className="mt-2 max-w-[80%] self-start rounded-lg rounded-bl-sm border border-border bg-background px-3.5 py-2.5 text-sm leading-relaxed">
          {e.content}
        </div>
        <div className="tnum mb-1 flex items-center gap-2 self-start text-[11px] text-muted-foreground">
          {showChannel && <ChannelMark channel={e.channel} />}
          {stamp}
        </div>
      </>
    )
  }

  if (e.role === 'rep') {
    return (
      <>
        <div className="mt-2 max-w-[80%] self-end whitespace-pre-wrap rounded-lg rounded-br-sm bg-foreground px-3.5 py-2.5 text-sm leading-relaxed text-background">
          {e.content}
        </div>
        <div className="tnum mb-1 flex items-center gap-2 self-end text-[11px] text-muted-foreground">
          Sent by a person · {stamp}
        </div>
      </>
    )
  }

  return (
    <>
      {/* What Liner actually ran on the turn, not a narration of it. */}
      {(e.tool_calls?.length ?? 0) > 0 && (
        <div className="my-2 flex max-w-[90%] items-start gap-2 self-center rounded-md border border-border bg-background px-3 py-2 text-xs text-muted-foreground">
          <Icon name="box" className="h-3.5 w-3.5 shrink-0" />
          <span>Liner ran {e.tool_calls!.map((t) => t.name).join(', ')}</span>
        </div>
      )}
      <div className="mt-2 max-w-[80%] self-end whitespace-pre-wrap rounded-lg rounded-br-sm bg-primary px-3.5 py-2.5 text-sm leading-relaxed text-primary-foreground">
        {e.content}
      </div>
      <div className="tnum mb-1 flex items-center gap-2 self-end text-[11px] text-muted-foreground">
        {showChannel && <ChannelMark channel={e.channel} />}
        Liner · {stamp}
      </div>
    </>
  )
}

function Outreach({ e }: { e: TimelineEntry }) {
  // A reply the buyer sent us, not a send. Same row shape, opposite direction,
  // and a rep skimming a timeline has to be able to tell at a glance who wrote
  // which -- so it leans to the buyer's side and says who it is from.
  const inbound = e.direction === 'in'
  // Buyer left, us right -- the sides a chat uses, because an exchange of four
  // emails is a conversation and reads as one. It used to centre our sends,
  // which is right for a one-off follow-up into silence and wrong the moment
  // there is a back and forth to follow: a column of centred cards gives a rep
  // no way to see who wrote which without reading every one.
  return (
    <div
      className={clsx(
        'my-2 w-full max-w-[90%] rounded-lg border p-3',
        inbound
          ? 'self-start border-primary/30 bg-primary/5'
          : 'self-end border-border bg-muted/40',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
            <Icon name="mail" className="h-3 w-3 shrink-0" />
            {inbound ? 'Email reply' : CHANNEL_LABEL[e.channel] ?? e.channel}
          </span>
          <p className="mt-0.5 truncate text-sm font-medium">{e.subject}</p>
        </div>
        {/* Only where a link was there to follow. An email with nothing
            trackable in it is not "unopened". */}
        {e.trackable && (
          <span
            title={
              e.opened
                ? `Followed the link ${e.click_count} time${e.click_count === 1 ? '' : 's'}. Whether they finished the form happens on the dealership's own site and does not come back here.`
                : 'The link has not been followed yet.'
            }
            className={clsx(
              'shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium',
              e.opened
                ? 'border-success/30 bg-success-muted text-success'
                : 'border-border text-muted-foreground',
            )}
          >
            {e.opened ? 'Link opened' : 'Not opened'}
          </span>
        )}
      </div>
      {e.body && (
        <p className="mt-1.5 line-clamp-3 whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
          {e.body}
        </p>
      )}
      <p className="tnum mt-1.5 text-[11px] text-muted-foreground">
        {e.to_address ? `${inbound ? 'From' : 'To'} ${e.to_address} · ` : ''}
        {dateTime(e.at)}
        {!inbound && !e.delivered_externally && e.channel === 'email' && (
          <> · recorded locally, not delivered</>
        )}
      </p>
      {e.error && <p className="mt-1 text-xs text-destructive">{e.error}</p>}
    </div>
  )
}

/** Things that happened rather than things that were said. Centred and
 *  unstyled as a bubble, because attributing them to a side of the thread
 *  would claim someone said them. */
function Event({
  icon,
  tone = 'muted',
  children,
}: {
  icon: 'calendar' | 'alert'
  tone?: 'muted' | 'primary' | 'success'
  children: React.ReactNode
}) {
  return (
    <div
      className={clsx(
        'my-2 flex max-w-[90%] items-center gap-2 self-center rounded-md border px-3 py-2 text-xs',
        tone === 'success' && 'border-success/30 bg-success/10 text-success',
        tone === 'primary' && 'border-primary/30 bg-primary/10 text-primary',
        tone === 'muted' && 'border-border bg-background text-muted-foreground',
      )}
    >
      <Icon name={icon} className="h-3.5 w-3.5 shrink-0" />
      <span>{children}</span>
    </div>
  )
}

function Appointment({ e }: { e: TimelineEntry }) {
  const cancelled = e.status === 'cancelled' || e.status === 'no_show'
  return (
    <Event icon="calendar" tone={cancelled ? 'muted' : 'success'}>
      {cancelled ? `Appointment ${e.status?.replace('_', ' ')}` : 'Appointment'}
      {' for '}
      <b className="font-medium">{dateTime(e.starts_at)}</b>
      {e.vehicle && (
        <>
          {' — '}
          {e.vehicle.title} <span className="tnum">{money(e.vehicle.price)}</span>
        </>
      )}
      {e.booked_by === 'rep' && ' · booked by a rep'}
    </Event>
  )
}

function Escalation({ e }: { e: TimelineEntry }) {
  return (
    <Event icon="alert" tone={e.claimed_at ? 'muted' : 'primary'}>
      {e.claimed_at
        ? `Handed to ${e.claimed_by?.name ?? 'a person'}`
        : 'Waiting on a person'}
      {e.reason ? ` — ${e.reason}` : ''}
    </Event>
  )
}

/** A heading whenever the thread changes, so a call that happened two days
 *  after a chat does not read as the same conversation continuing. */
function ThreadBreak({ channel, at }: { channel: string; at: string }) {
  return (
    <div className="my-3 flex items-center gap-3 self-stretch">
      <span className="h-px flex-1 bg-border" />
      <span className="flex items-center gap-1.5 whitespace-nowrap text-[11px] font-medium text-muted-foreground">
        <Icon name={CHANNEL_ICON[channel] ?? 'chat'} className="h-3 w-3 shrink-0" />
        {CHANNEL_LABEL[channel] ?? channel} · {dateTime(at)}
      </span>
      <span className="h-px flex-1 bg-border" />
    </div>
  )
}

/** A call: how long it ran, and the audio if it was captured.
 *
 *  One entry per call rather than a header over its transcript lines. A rep
 *  scanning a buyer's history wants "an eight-minute call on Tuesday" as a
 *  single item they can press play on; the words are below it either way.
 *
 *  Duration comes from the conversation row, not from the audio, so a call
 *  whose recording failed still reports how long it lasted -- and the two
 *  being different is itself worth seeing. */
function CallEntry({ e }: { e: TimelineEntry }) {
  return (
    <div className="my-3 rounded-lg border border-border bg-card p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Icon name="phone" className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="text-sm font-medium">
          {e.live ? 'Call in progress' : 'Voice call'}
        </span>
        {!e.live && (e.seconds ?? 0) > 0 && (
          <span className="tnum text-sm text-muted-foreground">
            {mmss(e.seconds ?? 0)}
          </span>
        )}
        <span className="ml-auto text-xs text-muted-foreground">{dateTime(e.at)}</span>
      </div>

      {e.both_sides === false && (
        /* Not a fault in the call. Transcription is billed separately and can
           be switched off; what must not happen is a half-transcript
           rendering as a whole one, because Liner's lines alone read as a
           conversation it held with nobody. */
        <p className="mt-1.5 text-xs text-warning-foreground">
          Only Liner&apos;s side was transcribed on this call -- the audio has both
          halves. Set VOICE_TRANSCRIBE=true to write the buyer&apos;s down.
        </p>
      )}

      {e.has_recording ? (
        <>
          <audio
            controls
            preload="none"
            src={`/api/voice/recording/${e.conversation_id}`}
            className="mt-2 w-full"
          />
          {e.recording_complete === false && (
            /* The slices stopped arriving without an end marker -- a tab
               killed mid-call. What is here is real audio and worth keeping;
               what it is not is the whole call, and a player that did not say
               so would let a rep conclude the buyer hung up mid-sentence. */
            <p className="mt-1 text-xs text-warning-foreground">
              This call ended without closing off its recording, so the audio
              stops before the call did.
            </p>
          )}
        </>
      ) : (
        /* Not an error, and worth distinguishing from a call that had no audio
           to begin with: a browser without MediaRecorder, a tab closed before
           the upload, or a call still running. */
        <p className="mt-1.5 text-xs text-muted-foreground">
          {e.live ? 'Audio is uploaded when the call ends.' : 'No audio was captured.'}
        </p>
      )}
    </div>
  )
}

function mmss(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

export function Timeline({
  entries,
  /** Multi-channel pages mark each turn; a single thread does not need to say
   *  "website chat" against every line of one website chat. */
  markChannels,
}: {
  entries: TimelineEntry[]
  markChannels: boolean
}) {
  let lastConversation: string | null | undefined
  const rows: React.ReactNode[] = []

  for (const e of entries) {
    if (
      markChannels &&
      e.kind === 'message' &&
      e.conversation_id !== lastConversation
    ) {
      lastConversation = e.conversation_id
      rows.push(
        <ThreadBreak key={`break-${e.id}`} channel={e.channel} at={e.at} />,
      )
    }
    rows.push(
      e.kind === 'call' ? (
        <CallEntry key={e.id} e={e} />
      ) : e.kind === 'message' ? (
        <Message key={e.id} e={e} showChannel={false} />
      ) : e.kind === 'outreach' ? (
        <Outreach key={e.id} e={e} />
      ) : e.kind === 'appointment' ? (
        <Appointment key={e.id} e={e} />
      ) : (
        <Escalation key={e.id} e={e} />
      ),
    )
  }

  return <div className="mx-auto flex max-w-3xl flex-col px-4 py-4">{rows}</div>
}
