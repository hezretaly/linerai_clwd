import { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'

import { applyBrand } from '../lib/brand'
import { api, streamMessages } from '../lib/api'
import { useDealership } from '../lib/dealership'
import { BookingCard } from '../components/BookingCard'
import type { BookingCardData, BookingResult } from '../components/BookingCard'
import { DetailsCard } from '../components/DetailsCard'
import type { DetailsCardData } from '../components/DetailsCard'
import { money } from '../lib/format'
import type { IntegrationsPayload, Rail } from '../lib/types'

interface VehicleCardData {
  vin: string
  year: number
  make: string
  model: string
  trim: string
  price: number | null
  mileage: number | null
  photo_url: string
  features?: string[]
  /** Which of the group's lots it is on. Empty for a single-site dealership. */
  location?: string
  /** The dealer's own enquiry form, only for a car they do not price online.
   *  Derived server-side, so the card can only ever offer a link the tool
   *  result carried -- the same rule the booking card follows. */
  inquiry_url?: string
}

/** The transcript is one ordered list, and a card is an entry in it.
 *
 *  Search results and the booking card used to live in their own state, render
 *  under the whole thread, and get cleared on every send. So three cars the
 *  buyer was asked to choose between vanished the moment they answered, and
 *  every card that survived piled up at the bottom next to none of the
 *  messages that produced them. Anything the buyer was shown stays where it
 *  was shown. */
/** How long a silence has to run before Liner says one more thing. Matches
 *  `QUIET_SECONDS` in `agent/nudge.py`; the server holds the allowance, this
 *  holds the clock, and they are written down in both places so a change in
 *  one is visible against the other. */
const QUIET_MS = 120_000

type Item =
  | { kind: 'text'; id: string; role: 'buyer' | 'assistant' | 'rep'; content: string }
  | { kind: 'vehicles'; id: string; vehicles: VehicleCardData[] }
  | { kind: 'booking'; id: string; data: BookingCardData }
  | { kind: 'details'; id: string; data: DetailsCardData }

interface ChatMessage {
  id: string
  role: string
  content: string
  tool_calls: { name: string; result: Record<string, unknown> }[]
}

/** Survives a refresh. The conversation itself has always been on the server;
 *  only this id was lost, and losing it started a new one from scratch. */
const STORAGE_KEY = 'liner.chat.conversation'

const SEARCH_TOOLS = new Set(['search_inventory', 'get_vehicle'])

/** Mirrors _vehicles_from on the server: a search returns a list, a lookup
 *  returns one car. */
function vehiclesFrom(result: Record<string, unknown>): VehicleCardData[] {
  if (Array.isArray(result.vehicles)) return result.vehicles as VehicleCardData[]
  if (result.vin) return [result as unknown as VehicleCardData]
  return []
}

export function Chat() {
  // Who this instance is, and their colour. Read here rather than off the
  // session payload because the resume path returns before that payload
  // exists -- so a refresh used to drop a prospect's accent back to the
  // product's blue, on the buyer's screen, mid-demo.
  const dealership = useDealership()
  /* Embedded in the showroom's widget, which has a title bar of its own.
   *
   * Two things have to go, and both are wrong rather than merely redundant:
   * the header repeats the dealership's name directly under the widget bar
   * already carrying it, and "Back" is a link to `/` that, followed inside an
   * iframe, replaces the chat with the landing page in a 24rem box. The
   * transcript, the rails and the composer are the same in both. */
  const embedded = new URLSearchParams(window.location.search).get('embed') === '1'
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [items, setItems] = useState<Item[]>([])
  const [rails, setRails] = useState<Rail[]>([])
  const [draft, setDraft] = useState('')
  const [typing, setTyping] = useState(false)
  // What the server says is missing, rather than a vendor name written here.
  // This used to hardcode ANTHROPIC_API_KEY and went stale the day the default
  // provider changed, telling a tester to set a key the system no longer uses.
  const [stubbed, setStubbed] = useState<string[] | null>(null)
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void (async () => {
      const stored = localStorage.getItem(STORAGE_KEY)
      if (stored && (await resume(stored, setConversationId, setItems, setRails))) {
        void loadIntegrations(setStubbed)
        return
      }

      const session = await api.post<{
        conversation_id: string
        greeting: string
        rails: Rail[]
      }>('/api/chat/sessions')
      localStorage.setItem(STORAGE_KEY, session.conversation_id)
      setConversationId(session.conversation_id)
      setRails(session.rails)
      setItems([{ kind: 'text', id: 'greeting', role: 'assistant', content: session.greeting }])
      void loadIntegrations(setStubbed)
    })()
  }, [])

  useEffect(() => {
    applyBrand(dealership?.brand)
  }, [dealership])

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' })
  }, [items, typing])

  // Only the newest booking card takes input. An older one is a list of times
  // that have since moved on, and tapping it would submit a slot the buyer was
  // offered several turns ago.
  const liveBookingId = [...items].reverse().find((i) => i.kind === 'booking')?.id
  // Same rule for the details card: an older one asks questions the buyer
  // has already answered, and submitting it would re-ask them.
  const liveDetailsId = [...items].reverse().find((i) => i.kind === 'details')?.id

  // While a booking card is up its own controls are the ask, so the stage
  // followups ("Saturday morning works") would be the same question posed a
  // second, worse way. The knowledge chips stay: they are about something else,
  // and they are the way out of the card without typing.
  const visibleRails = liveBookingId ? rails.filter((r) => r.kind === 'knowledge') : rails

  // ---- the follow-up on a quiet buyer ----------------------------------
  //
  // Driven from here because this is the only place that can tell the buyer is
  // still on the page. `/chat` has no socket, so a message written server-side
  // into a thread nobody is watching would surface on refresh, or above their
  // next message, out of order. A closed tab asks for nothing, which is right:
  // there would be nobody to read it.
  //
  // The allowance is the server's -- `agent/nudge.py` reads the transcript and
  // permits exactly one standing after the buyer's last message. This timer
  // being restarted by a reload, or running twice in two tabs, therefore
  // cannot buy a second follow-up.
  useEffect(() => {
    if (!conversationId || typing) return
    // The last thing *said*, not the last thing in the list. After a search
    // the newest entry is the row of cars, so testing `items[len - 1]` for an
    // assistant reply never matched -- which is every turn that shows
    // anything, i.e. the case this exists for. Measured: zero requests.
    const spoken = [...items].reverse().find((i) => i.kind === 'text')
    // Only after Liner has spoken. A buyer who has just sent something is
    // owed the reply that is still on its way, not a nudge on top of it.
    if (!spoken || spoken.kind !== 'text' || spoken.role !== 'assistant') return
    // And never over a card. A booking card or a details card is something to
    // fill in, and the pause while somebody types their email is not a silence
    // to fill -- a message arriving under a half-finished form is an
    // interruption, not a follow-up.
    const last = items[items.length - 1]
    if (last && (last.kind === 'booking' || last.kind === 'details')) return

    const timer = setTimeout(() => {
      void (async () => {
        try {
          const result = await api.post<{
            sent: boolean
            assistant_message?: ChatMessage
            rails?: Rail[]
          }>(`/api/chat/sessions/${conversationId}/nudge`, {})
          if (!result.sent || !result.assistant_message) return
          setItems((prev) => [
            ...prev,
            {
              kind: 'text',
              id: result.assistant_message!.id,
              role: 'assistant',
              content: result.assistant_message!.content,
            },
          ])
          if (result.rails) setRails(result.rails)
        } catch {
          // A follow-up nobody asked for is not worth an error in front of a
          // buyer. Staying quiet is exactly what would have happened anyway.
        }
      })()
    }, QUIET_MS)
    return () => clearTimeout(timer)
  }, [items, typing, conversationId])

  const send = async (payload: { content?: string; rail_id?: string }, label: string) => {
    if (!conversationId || typing) return

    setItems((prev) => [
      ...prev,
      { kind: 'text', id: `local-${Date.now()}`, role: 'buyer', content: label },
    ])
    setDraft('')

    // 400-900ms before the indicator, so it reads as a person starting to type.
    const delay = 400 + Math.random() * 500
    const timer = setTimeout(() => setTyping(true), delay)

    let streamed = ''
    let streamId = ''

    try {
      await streamMessages(conversationId, payload, (event, data) => {
        if (event === 'token') {
          clearTimeout(timer)
          setTyping(false)
          streamed += String(data.text ?? '')
          if (!streamId) {
            streamId = `stream-${Date.now()}`
            const id = streamId
            setItems((prev) => [
              ...prev,
              { kind: 'text', id, role: 'assistant', content: streamed },
            ])
          } else {
            const id = streamId
            setItems((prev) =>
              prev.map((i) => (i.kind === 'text' && i.id === id ? { ...i, content: streamed } : i)),
            )
          }
        } else if (event === 'held' || event === 'error') {
          // The turn failed after the response had already started -- a missing
          // key, a vendor outage, a rate limit. Without this branch the buyer
          // watches a typing indicator that never resolves, which is the worst
          // way to fail: it looks like being ignored rather than like a fault.
          clearTimeout(timer)
          setTyping(false)
          setItems((prev) => [
            ...prev,
            {
              kind: 'text',
              id: `${event}-${Date.now()}`,
              role: 'rep',
              content: String(data.message),
            },
          ])
        } else if (event === 'vehicles') {
          setItems((prev) => [
            ...prev,
            {
              kind: 'vehicles',
              id: `cars-${Date.now()}`,
              vehicles: data.vehicles as VehicleCardData[],
            },
          ])
        } else if (event === 'booking') {
          setItems((prev) => [
            ...prev,
            { kind: 'booking', id: `book-${Date.now()}`, data: data as unknown as BookingCardData },
          ])
        } else if (event === 'details') {
          setItems((prev) => [
            ...prev,
            { kind: 'details', id: `details-${Date.now()}`, data: data as unknown as DetailsCardData },
          ])
        } else if (event === 'rails') {
          setRails(data.rails as Rail[])
        }
      })
    } finally {
      clearTimeout(timer)
      setTyping(false)
    }
  }

  const onBooked = (bookingItemId: string) => (result: BookingResult) => {
    setItems((prev) => [
      ...prev.filter((i) => i.id !== bookingItemId),
      {
        kind: 'text',
        id: result.buyer_message.id,
        role: 'buyer',
        content: result.buyer_message.content,
      },
      {
        kind: 'text',
        id: result.assistant_message.id,
        role: 'assistant',
        content: result.assistant_message.content,
      },
    ])
  }

  return (
    <div className="mx-auto flex h-full max-w-2xl flex-col bg-background">
      {!embedded && (
        <header className="flex items-center justify-between border-b border-border px-5 py-3">
          <div>
            <p className="text-sm font-semibold">{dealership?.name || ' '}</p>
            <p className="text-xs text-muted-foreground">
              {typing ? 'Typing...' : 'Usually replies instantly'}
            </p>
          </div>
          <a href="/" className="text-sm text-primary hover:underline">
            Back
          </a>
        </header>
      )}

      {stubbed && (
        <p className="border-b border-warning/30 bg-warning-muted px-5 py-2 text-xs text-warning-foreground">
          Scripted assistant --{' '}
          {stubbed.map((key, i) => (
            <span key={key}>
              {i > 0 && ', '}
              <code className="font-mono">{key}</code>
            </span>
          ))}{' '}
          {stubbed.length === 1 ? 'is' : 'are'} not set. It calls the real tools and books real
          appointments, but the wording is canned and it can't improvise.
        </p>
      )}

      <div ref={scroller} className="flex-1 space-y-3 overflow-y-auto px-5 py-4">
        {items.map((item) => {
          if (item.kind === 'text') {
            return (
              <div
                key={item.id}
                className={clsx('flex', item.role === 'buyer' ? 'justify-end' : 'justify-start')}
              >
                <div
                  className={clsx(
                    'max-w-[80%] rounded-2xl px-4 py-2.5 text-[15px] leading-snug whitespace-pre-wrap animate-fade-up',
                    item.role === 'buyer'
                      ? 'bg-bubble-buyer text-bubble-buyer-foreground'
                      : item.role === 'rep'
                        ? 'border border-primary/30 bg-accent text-accent-foreground'
                        : 'bg-muted text-foreground',
                  )}
                >
                  {item.content}
                </div>
              </div>
            )
          }

          if (item.kind === 'vehicles') {
            return (
              <div key={item.id} className="space-y-2">
                {item.vehicles.map((vehicle) => (
                  <article
                    key={vehicle.vin}
                    className="flex gap-3 rounded-2xl border border-border bg-card p-3 animate-fade-up"
                  >
                    <img
                      src={vehicle.photo_url}
                      alt=""
                      className="h-20 w-28 shrink-0 rounded-lg object-cover"
                    />
                    <div className="min-w-0">
                      <p className="text-sm font-semibold">
                        {vehicle.year} {vehicle.make} {vehicle.model}
                      </p>
                      <p className="text-sm text-muted-foreground">
                        {/* No published price is a listing state the dealer
                            chose, and their own site answers it with an
                            enquiry form at the same URL. The words become the
                            link, so the buyer can ask -- while Liner still
                            offers a visit, which is the better outcome and
                            what the method says to do. */}
                        {vehicle.price ? (
                          money(vehicle.price)
                        ) : vehicle.inquiry_url ? (
                          <a
                            href={vehicle.inquiry_url}
                            target="_blank"
                            rel="noreferrer"
                            className="font-medium text-primary underline-offset-4 hover:underline"
                          >
                            Ask for a price
                          </a>
                        ) : (
                          money(vehicle.price)
                        )}
                        {vehicle.mileage ? ` -- ${vehicle.mileage.toLocaleString()} mi` : ''}
                        {vehicle.location ? ` -- ${vehicle.location}` : ''}
                      </p>
                      {vehicle.features?.length ? (
                        <p className="mt-0.5 truncate text-xs text-muted-foreground">
                          {vehicle.features.slice(0, 3).join(' - ')}
                        </p>
                      ) : null}
                    </div>
                  </article>
                ))}
              </div>
            )
          }

          if (item.kind === 'details') {
            return (
              <DetailsCard
                key={item.id}
                data={item.data}
                stale={item.id !== liveDetailsId}
                submit={async (values) => {
                  const result = await api.post<{
                    buyer_message: ChatMessage
                    assistant_message: ChatMessage
                    rails: Rail[]
                  }>(`/api/chat/sessions/${conversationId}/details`, { values })
                  // Appended where the card sits, like every other entry --
                  // the transcript is one ordered list and what the buyer was
                  // shown stays where it was shown.
                  setItems((prev) => [
                    ...prev,
                    { kind: 'text', id: result.buyer_message.id, role: 'buyer',
                      content: result.buyer_message.content },
                    { kind: 'text', id: result.assistant_message.id, role: 'assistant',
                      content: result.assistant_message.content },
                  ])
                  setRails(result.rails)
                }}
              />
            )
          }

          return (
            <BookingCard
              key={item.id}
              data={item.data}
              stale={item.id !== liveBookingId}
              submit={async (payload) => {
                const result = await api.post<BookingResult>(
                  `/api/chat/sessions/${conversationId}/book`,
                  payload,
                )
                onBooked(item.id)(result)
              }}
            />
          )
        })}

        {typing && (
          <div className="flex justify-start">
            <div className="flex gap-1 rounded-2xl bg-muted px-4 py-3">
              {[0, 1, 2].map((index) => (
                <span
                  key={index}
                  className="typing-dot h-2 w-2 rounded-full bg-muted-foreground"
                  style={{ animationDelay: `${index * 0.15}s` }}
                />
              ))}
            </div>
          </div>
        )}
      </div>

      {visibleRails.length > 0 && (
        <div className="flex flex-wrap gap-2 border-t border-border px-5 pt-3">
          {visibleRails.map((rail) => (
            <button
              key={rail.id}
              disabled={typing}
              onClick={() => void send({ rail_id: rail.id }, rail.message_text)}
              className={clsx(
                'rounded-full border px-3 py-1.5 text-sm transition-colors duration-150',
                rail.kind === 'knowledge'
                  ? 'border-border text-muted-foreground hover:bg-muted'
                  : 'border-primary/40 text-primary hover:bg-accent',
                'disabled:opacity-50',
              )}
            >
              {rail.label}
            </button>
          ))}
        </div>
      )}

      <form
        className="flex gap-2 px-5 py-3"
        onSubmit={(event) => {
          event.preventDefault()
          if (draft.trim()) void send({ content: draft.trim() }, draft.trim())
        }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask about anything on the lot..."
          className="h-11 flex-1 rounded-full border border-input bg-background px-4 text-[15px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <button
          type="submit"
          disabled={!draft.trim() || typing}
          className="h-11 rounded-full bg-primary px-5 text-sm font-medium text-primary-foreground disabled:opacity-40"
        >
          Send
        </button>
      </form>
    </div>
  )
}

async function loadIntegrations(setStubbed: (missing: string[] | null) => void) {
  const health = await api.get<IntegrationsPayload>('/api/integrations')
  const llm = health.integrations.find((i) => i.key === 'llm')
  setStubbed(llm && !llm.configured ? llm.missing : null)
}

/** Rebuild a thread the buyer already had. Returns false if that conversation
 *  is gone -- a reseeded database, a cleared server -- so the caller opens a
 *  new one instead of showing an error for something the buyer cannot fix. */
async function resume(
  id: string,
  setConversationId: (id: string) => void,
  setItems: (items: Item[]) => void,
  setRails: (rails: Rail[]) => void,
): Promise<boolean> {
  let payload: {
    id: string
    greeting: string
    messages: ChatMessage[]
    rails: Rail[]
    booking: BookingCardData | null
    details: DetailsCardData | null
  }
  try {
    payload = await api.get(`/api/chat/sessions/${id}`)
  } catch {
    localStorage.removeItem(STORAGE_KEY)
    return false
  }

  const rebuilt: Item[] = [
    { kind: 'text', id: 'greeting', role: 'assistant', content: payload.greeting },
  ]
  for (const message of payload.messages) {
    if (message.content) {
      rebuilt.push({
        kind: 'text',
        id: message.id,
        role: message.role === 'buyer' ? 'buyer' : message.role === 'rep' ? 'rep' : 'assistant',
        content: message.content,
      })
    }
    // The cars the buyer was shown are recoverable because the reply carries
    // the tool calls that produced them.
    const shown = message.tool_calls
      .filter((c) => SEARCH_TOOLS.has(c.name))
      .flatMap((c) => vehiclesFrom(c.result))
    if (shown.length > 0) {
      rebuilt.push({ kind: 'vehicles', id: `cars-${message.id}`, vehicles: shown.slice(0, 3) })
    }
  }
  // Times are not replayed from the transcript -- the server looked them up
  // again, because a slot list from ten minutes ago may be gone.
  // Only sent while it is unanswered -- the server drops it once `save_details`
  // is in the transcript, so a refresh never re-asks for details already given.
  if (payload.details) {
    rebuilt.push({ kind: 'details', id: `details-${payload.id}`, data: payload.details })
  }
  if (payload.booking) {
    rebuilt.push({ kind: 'booking', id: `book-${payload.id}`, data: payload.booking })
  }

  setConversationId(payload.id)
  setItems(rebuilt)
  setRails(payload.rails)
  return true
}
