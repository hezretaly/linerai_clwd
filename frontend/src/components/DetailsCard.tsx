import { useState } from 'react'
import clsx from 'clsx'

/** A short form in the chat thread, asking the buyer for details in boxes.
 *
 *  Built entirely from a tool result -- `agent/details.py` decides which boxes
 *  exist and this draws exactly those, the same contract the booking card has.
 *  It cannot ask for anything the server did not offer, so there is no second
 *  vocabulary to keep in step.
 *
 *  A chip could never do this job: its text is sent as the buyer's own message,
 *  so a pre-written chip asking for a name would put words in their mouth. Here
 *  the buyer types, which is why the answers are stored as `typed` rather than
 *  as a guess. */

export interface DetailsField {
  key: string
  label: string
  kind: 'text' | 'tel' | 'email' | 'choice'
  placeholder: string
  choices: string[]
  hint: string
  required: boolean
}

export interface DetailsCardData {
  reason: string
  required: string[]
  fields: DetailsField[]
}

export function DetailsCard({
  data,
  stale = false,
  submit,
}: {
  data: DetailsCardData
  /** A card further up the thread. It stays readable but stops taking input:
   *  the buyer has moved on, and a second submission would re-ask questions
   *  they have already answered. */
  stale?: boolean
  submit: (values: Record<string, string>) => Promise<void>
}) {
  const [values, setValues] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [done, setDone] = useState(false)

  if (done || data.fields.length === 0) return null

  const frozen = saving || stale
  const set = (key: string, value: string) =>
    setValues((prev) => ({ ...prev, [key]: value }))

  const missing = data.fields.filter((f) => f.required && !(values[f.key] || '').trim())

  const send = async () => {
    if (missing.length > 0) {
      // Named, never "please complete the form". The card is short enough that
      // the one thing standing in the way can always be said out loud.
      setError(`${missing.map((f) => f.label).join(' and ')} still needed.`)
      return
    }
    setError('')
    setSaving(true)
    try {
      await submit(values)
      setDone(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That did not save. Try again?')
      setSaving(false)
    }
  }

  return (
    <div
      className={clsx(
        'min-w-0 rounded-2xl border border-border bg-card p-4 shadow-sm',
        stale && 'opacity-60',
      )}
    >
      {data.reason && (
        <p className="mb-3 text-sm text-muted-foreground">{data.reason}</p>
      )}

      <div className="space-y-3">
        {data.fields.map((field) => (
          <div key={field.key} className="min-w-0">
            <label
              htmlFor={`detail-${field.key}`}
              className="mb-1 block text-xs font-medium"
            >
              {field.label}
              {!field.required && (
                <span className="ml-1 font-normal text-muted-foreground">optional</span>
              )}
            </label>

            {field.kind === 'choice' ? (
              // Buttons, not a select. Three taps beat a dropdown on a phone,
              // and the options are visible without opening anything.
              <div className="flex flex-wrap gap-1.5">
                {field.choices.map((choice) => (
                  <button
                    key={choice}
                    type="button"
                    disabled={frozen}
                    onClick={() => set(field.key, values[field.key] === choice ? '' : choice)}
                    className={clsx(
                      'rounded-full border px-3 py-1.5 text-xs font-medium transition-colors',
                      values[field.key] === choice
                        ? 'border-primary bg-primary text-primary-foreground'
                        : 'border-input bg-background hover:bg-accent',
                      frozen && 'cursor-not-allowed opacity-60',
                    )}
                  >
                    {choice}
                  </button>
                ))}
              </div>
            ) : (
              <input
                id={`detail-${field.key}`}
                type={field.kind}
                // A phone keypad on a phone, an email keyboard for an address.
                inputMode={field.kind === 'tel' ? 'tel' : undefined}
                autoComplete={
                  { tel: 'tel', email: 'email', text: 'name' }[field.kind] ?? 'on'
                }
                value={values[field.key] || ''}
                placeholder={field.placeholder}
                disabled={frozen}
                onChange={(e) => set(field.key, e.target.value)}
                className="w-full min-w-0 rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none focus:border-ring disabled:opacity-60"
              />
            )}

            {field.hint && (
              <p className="mt-1 text-[11px] text-muted-foreground">{field.hint}</p>
            )}
          </div>
        ))}
      </div>

      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}

      <button
        type="button"
        onClick={send}
        disabled={frozen}
        className="mt-3 w-full rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
      >
        {/* Not "Send". The composer's own button says that and sits a few
            inches below, so two controls on one screen would read identically
            -- and identically to a screen reader, which announces the label
            and nothing else. */}
        {saving ? 'Sending...' : 'Send details'}
      </button>
    </div>
  )
}
