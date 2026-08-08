import { useState } from 'react'
import clsx from 'clsx'

import { api, ApiError } from '../lib/api'

export interface BookingSlot {
  starts_at: string
  label: string
}

export interface BookingDay {
  date: string
  label: string
  short: string
  sub: string
  slots: BookingSlot[]
}

export interface BookingCardData {
  slot_minutes: number
  days: BookingDay[]
}

export interface BookingResult {
  appointment: { id: string; starts_at: string }
  buyer_message: { id: string; content: string }
  assistant_message: { id: string; content: string }
  stage: string
}

/** Day -> time -> contact details, in one card the buyer never has to type into
 *  twice. Every day and every time here came from `check_availability`; nothing
 *  on this card is composed on the client, so a slot that is not really open
 *  cannot be offered. The submit goes through `book_appointment`, which is
 *  where the hours and clash rules live -- this is a nicer way to reach the
 *  same executor, not a way around it. */
export function BookingCard({
  data,
  conversationId,
  stale = false,
  onBooked,
}: {
  data: BookingCardData
  conversationId: string
  /** A card further up the thread. Its times were open when it was drawn and
   *  may not be now, so it stays readable and stops taking input rather than
   *  submitting a slot the buyer was offered several turns ago. */
  stale?: boolean
  onBooked: (result: BookingResult) => void
}) {
  const [day, setDay] = useState<BookingDay | null>(data.days.length === 1 ? data.days[0] : null)
  const [slot, setSlot] = useState<BookingSlot | null>(null)
  const [form, setForm] = useState({ name: '', email: '', phone: '' })
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [done, setDone] = useState(false)

  if (done || data.days.length === 0) return null

  const frozen = saving || stale

  const submit = async () => {
    const next: Record<string, string> = {}
    if (!form.name.trim()) next.name = 'We need a name for the appointment.'
    // Matched loosely on purpose: the server owns the real rule, and a strict
    // regex here would reject an address the backend would have accepted.
    if (!/^\S+@\S+\.\S+$/.test(form.email.trim())) next.email = 'A valid email, please.'
    setErrors(next)
    if (Object.keys(next).length > 0 || !slot) return

    setSaving(true)
    try {
      const result = await api.post<BookingResult>(
        `/api/chat/sessions/${conversationId}/book`,
        { starts_at: slot.starts_at, ...form },
      )
      setDone(true)
      onBooked(result)
    } catch (error) {
      // 409 is the slot going while the buyer typed, or a time outside hours.
      // Show what the server said and send them back to pick again -- both are
      // things the buyer can act on, and neither is a failure of the form.
      const message =
        error instanceof ApiError
          ? String((error.payload as { detail?: string })?.detail ?? error.message)
          : 'That did not go through. Try another time.'
      setErrors({ form: message })
      setSlot(null)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section
      className={clsx(
        'animate-fade-up rounded-2xl border border-border bg-card p-4',
        stale && 'pointer-events-none opacity-60',
      )}
    >
      <div className="space-y-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {day ? 'Day' : 'Pick a day'}
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {data.days.map((d) => (
              <button
                key={d.date}
                disabled={frozen}
                onClick={() => {
                  setDay(d)
                  setSlot(null)
                }}
                className={clsx(
                  'rounded-xl border px-3 py-2 text-left transition-colors duration-150',
                  day?.date === d.date
                    ? 'border-primary bg-primary text-primary-foreground'
                    : 'border-border bg-background hover:border-primary',
                )}
              >
                <span className="block text-sm font-semibold">{d.short}</span>
                <span className="block text-xs opacity-80">{d.sub}</span>
              </button>
            ))}
          </div>
        </div>

        {day && (
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {slot ? 'Time' : `What time on ${day.label}?`}
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              {day.slots.map((s) => (
                <button
                  key={s.starts_at}
                  disabled={frozen}
                  onClick={() => setSlot(s)}
                  className={clsx(
                    'rounded-full border px-4 py-2 text-sm font-medium transition-colors duration-150',
                    slot?.starts_at === s.starts_at
                      ? 'border-primary bg-primary text-primary-foreground'
                      : 'border-border bg-background hover:border-primary',
                  )}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {slot && (
          <div className="space-y-2 border-t border-border pt-3">
            <p className="text-sm">
              <span className="font-semibold">
                {day?.label} at {slot.label}
              </span>{' '}
              <span className="text-muted-foreground">
                -- {data.slot_minutes} minutes. Where should the confirmation go?
              </span>
            </p>
            {(
              [
                { key: 'name', label: 'Name', type: 'text', hint: '' },
                { key: 'email', label: 'Email', type: 'email', hint: '' },
                { key: 'phone', label: 'Phone', type: 'tel', hint: ' (optional)' },
              ] as const
            ).map((field) => (
              <label key={field.key} className="block">
                <span className="text-xs text-muted-foreground">
                  {field.label}
                  {field.hint}
                </span>
                <input
                  type={field.type}
                  value={form[field.key]}
                  disabled={frozen}
                  onChange={(e) => {
                    setForm({ ...form, [field.key]: e.target.value })
                    setErrors((prev) => ({ ...prev, [field.key]: '' }))
                  }}
                  className={clsx(
                    'mt-1 w-full rounded-lg border bg-background px-3 py-2 text-sm outline-none',
                    'focus:border-primary',
                    errors[field.key] ? 'border-destructive' : 'border-border',
                  )}
                />
                {errors[field.key] && (
                  <span className="text-xs text-destructive">{errors[field.key]}</span>
                )}
              </label>
            ))}
            <button
              onClick={() => void submit()}
              disabled={frozen}
              className="w-full rounded-full bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-60"
            >
              {saving ? 'Booking...' : 'Book it'}
            </button>
          </div>
        )}

        {errors.form && <p className="text-sm text-destructive">{errors.form}</p>}
      </div>
    </section>
  )
}
