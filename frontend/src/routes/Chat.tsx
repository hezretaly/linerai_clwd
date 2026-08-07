import { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'

import { api, streamMessages } from '../lib/api'
import { money } from '../lib/format'
import type { IntegrationsPayload, Rail } from '../lib/types'

interface Bubble {
  id: string
  role: 'buyer' | 'assistant' | 'rep'
  content: string
}

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
}

export function Chat() {
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [bubbles, setBubbles] = useState<Bubble[]>([])
  const [rails, setRails] = useState<Rail[]>([])
  const [vehicles, setVehicles] = useState<VehicleCardData[]>([])
  const [slots, setSlots] = useState<string[]>([])
  const [draft, setDraft] = useState('')
  const [typing, setTyping] = useState(false)
  // What the server says is missing, rather than a vendor name written here.
  // This used to hardcode ANTHROPIC_API_KEY and went stale the day the default
  // provider changed, telling a tester to set a key the system no longer uses.
  const [stubbed, setStubbed] = useState<string[] | null>(null)
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void (async () => {
      const session = await api.post<{
        conversation_id: string
        greeting: string
        rails: Rail[]
      }>('/api/chat/sessions')
      setConversationId(session.conversation_id)
      setRails(session.rails)
      setBubbles([{ id: 'greeting', role: 'assistant', content: session.greeting }])

      const health = await api.get<IntegrationsPayload>('/api/integrations')
      const llm = health.integrations.find((i) => i.key === 'llm')
      setStubbed(llm && !llm.configured ? llm.missing : null)
    })()
  }, [])

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' })
  }, [bubbles, typing, vehicles, slots])

  const send = async (payload: { content?: string; rail_id?: string }, label: string) => {
    if (!conversationId || typing) return

    setBubbles((prev) => [
      ...prev,
      { id: `local-${Date.now()}`, role: 'buyer', content: label },
    ])
    setDraft('')
    setVehicles([])
    setSlots([])

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
            setBubbles((prev) => [
              ...prev,
              { id: streamId, role: 'assistant', content: streamed },
            ])
          } else {
            setBubbles((prev) =>
              prev.map((b) => (b.id === streamId ? { ...b, content: streamed } : b)),
            )
          }
        } else if (event === 'held') {
          clearTimeout(timer)
          setTyping(false)
          setBubbles((prev) => [
            ...prev,
            { id: `held-${Date.now()}`, role: 'rep', content: String(data.message) },
          ])
        } else if (event === 'error') {
          // The turn failed after the response had already started -- a missing
          // key, a vendor outage, a rate limit. Without this branch the buyer
          // watches a typing indicator that never resolves, which is the worst
          // way to fail: it looks like being ignored rather than like a fault.
          clearTimeout(timer)
          setTyping(false)
          setBubbles((prev) => [
            ...prev,
            { id: `error-${Date.now()}`, role: 'rep', content: String(data.message) },
          ])
        } else if (event === 'vehicles') {
          setVehicles(data.vehicles as VehicleCardData[])
        } else if (event === 'slots') {
          setSlots(data.slots as string[])
        } else if (event === 'rails') {
          setRails(data.rails as Rail[])
        }
      })
    } finally {
      clearTimeout(timer)
      setTyping(false)
    }
  }

  return (
    <div className="mx-auto flex h-full max-w-2xl flex-col bg-background">
      <header className="flex items-center justify-between border-b border-border px-5 py-3">
        <div>
          <p className="text-sm font-semibold">Riverside Auto</p>
          <p className="text-xs text-muted-foreground">
            {typing ? 'Typing...' : 'Usually replies instantly'}
          </p>
        </div>
        <a href="/" className="text-sm text-primary hover:underline">
          Back
        </a>
      </header>

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
        {bubbles.map((bubble) => (
          <div
            key={bubble.id}
            className={clsx('flex', bubble.role === 'buyer' ? 'justify-end' : 'justify-start')}
          >
            <div
              className={clsx(
                'max-w-[80%] rounded-2xl px-4 py-2.5 text-[15px] leading-snug whitespace-pre-wrap animate-fade-up',
                bubble.role === 'buyer'
                  ? 'bg-bubble-buyer text-bubble-buyer-foreground'
                  : bubble.role === 'rep'
                    ? 'border border-primary/30 bg-accent text-accent-foreground'
                    : 'bg-muted text-foreground',
              )}
            >
              {bubble.content}
            </div>
          </div>
        ))}

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

        {vehicles.length > 0 && (
          <div className="space-y-2">
            {vehicles.map((vehicle) => (
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
                    {money(vehicle.price)}
                    {vehicle.mileage ? ` -- ${vehicle.mileage.toLocaleString()} mi` : ''}
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
        )}

        {slots.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {slots.map((slot) => {
              const when = new Date(slot)
              const label = when.toLocaleString('en-US', {
                weekday: 'long',
                hour: 'numeric',
                minute: '2-digit',
              })
              return (
                <button
                  key={slot}
                  onClick={() => void send({ content: `${label} works for me.` }, `${label} works`)}
                  className="rounded-full border border-primary bg-accent px-4 py-2 text-sm font-medium text-accent-foreground transition-colors duration-150 hover:bg-primary hover:text-primary-foreground"
                >
                  {label}
                </button>
              )
            })}
          </div>
        )}
      </div>

      {rails.length > 0 && (
        <div className="flex flex-wrap gap-2 border-t border-border px-5 pt-3">
          {rails.map((rail) => (
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
