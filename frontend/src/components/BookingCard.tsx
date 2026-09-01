import { useState } from 'react'
import clsx from 'clsx'

import { ApiError } from '../lib/api'

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
  /** What the buyer has already told us, off their lead row. The card fills
   *  its boxes from this and asks for nothing it already has -- being asked
   *  for a number you gave two turns ago reads as not having been listened
   *  to. Optional so an older payload still renders. */
  known?: { name?: string; email?: string; phone?: string }
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
export interface BookingSubmission {
  starts_at: string
  name: string
  email: string
  phone: string
}

export function BookingCard({
  data,
  stale = false,
  submit: send,
}: {
  data: BookingCardData
  /** A card further up the thread. Its times were open when it was drawn and
   *  may not be now, so it stays readable and stops taking input rather than
   *  submitting a slot the buyer was offered several turns ago. */
  stale?: boolean
  /** Who to send it to. The buyer's chat posts to the public endpoint; a rep
   *  booking from the dashboard posts to theirs. Both land on the same
   *  `book_appointment` executor, so the card does not need to know which. */
  submit: (payload: BookingSubmission) => Promise<void>
}) {
  const [day, setDay] = useState<BookingDay | null>(data.days.length === 1 ? data.days[0] : null)
  const [slot, setSlot] = useState<BookingSlot | null>(null)
  const [form, setForm] = useState({
    name: data.known?.name ?? '',
    email: data.known?.email ?? '',
    phone: data.known?.phone ?? '',
  })
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [done, setDone] = useState(false)

  if (done || data.days.length === 0) return null

  const frozen = saving || stale

  const submit = async () => {
    const next: Record<string, string> = {}
    if (!form.name.trim()) next.name = 'We need a name for the appointment.'
    // **A number, not an address.** This card used to require an email and
    // treat the phone as optional, which was the wrong way round: a rep can
    // ring a number this evening and cannot get an answer out of an inbox at
    // five past six on a Friday. It is also what the details card has always
    // asked for, and the two cards asking a buyer for different things to do
    // the same job is how one of them starts being the wrong one to fill in.
    if (form.phone.replace(/\D/g, '').length < 7) {
      next.phone = 'A number someone here can call you on.'
    }
    // Loosely matched, and only when there is something to match: the server
    // owns the real rule, a strict regex here would reject an address the
    // backend would have accepted, and blank is now a legitimate answer.
    if (form.email.trim() && !/^\S+@\S+\.\S+$/.test(form.email.trim())) {
      next.email = 'That address does not look right.'
    }
    setErrors(next)
    if (Object.keys(next).length > 0 || !slot) return

    setSaving(true)
    try {
      await send({ starts_at: slot.starts_at, ...form })
      setDone(true)
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
                -- {data.slot_minutes} minutes. Who should we ask for?
              </span>
            </p>
            {(
              [
                { key: 'name', label: 'Name', type: 'text', hint: '' },
                { key: 'phone', label: 'Phone', type: 'tel', hint: '' },
                // Last, and optional. The confirmation goes here when there is
                // one; a rep rings the number above either way.
                { key: 'email', label: 'Email', type: 'email', hint: ' (optional)' },
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
