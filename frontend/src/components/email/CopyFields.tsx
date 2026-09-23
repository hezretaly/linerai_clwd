import { useId, useState, type ReactNode } from 'react'

import { RecipientInput, type RecipientSuggestion } from './RecipientInput'

/* Cc and Bcc, out of the way until somebody wants them.
 *
 * Most messages have neither, and two empty boxes on every composer are two
 * more things to read past on a phone. So each is a small link until it is
 * opened or already holds something -- a reply-all arrives with its Cc
 * filled, and a field with addresses in it is never hidden.
 *
 * The component draws only the fields and the links; where they go is the
 * caller's decision. That matters on the ops composer, whose gate finds To
 * and Subject as the first two inputs on the page: this goes after Subject.
 */

export type CopyFieldsProps = {
  cc: string
  bcc: string
  onCc: (value: string) => void
  onBcc: (value: string) => void
  suggestions?: RecipientSuggestion[]
}

export function CopyFields({ cc, bcc, onCc, onBcc, suggestions }: CopyFieldsProps) {
  const ccId = useId()
  const bccId = useId()
  const [openCc, setOpenCc] = useState(false)
  const [openBcc, setOpenBcc] = useState(false)
  // Focus follows a click on the link, never a field that opened because it
  // arrived with addresses in it.
  const [focusOn, setFocusOn] = useState<'cc' | 'bcc' | null>(null)

  const showCc = openCc || cc.trim() !== ''
  const showBcc = openBcc || bcc.trim() !== ''

  const link =
    'rounded px-1 text-xs font-medium text-muted-foreground hover:text-foreground hover:underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'

  return (
    <div className="min-w-0 space-y-2">
      {showCc && (
        <Row id={ccId} label="Cc">
          <RecipientInput
            id={ccId}
            ariaLabel="Cc"
            value={cc}
            onChange={onCc}
            suggestions={suggestions}
            autoFocus={focusOn === 'cc'}
          />
        </Row>
      )}
      {showBcc && (
        <Row id={bccId} label="Bcc">
          <RecipientInput
            id={bccId}
            ariaLabel="Bcc"
            value={bcc}
            onChange={onBcc}
            suggestions={suggestions}
            autoFocus={focusOn === 'bcc'}
          />
        </Row>
      )}
      {(!showCc || !showBcc) && (
        <div className="flex flex-wrap items-center gap-2">
          {!showCc && (
            <button
              type="button"
              className={link}
              aria-label="Add Cc recipients"
              onClick={() => {
                setOpenCc(true)
                setFocusOn('cc')
              }}
            >
              Cc
            </button>
          )}
          {!showBcc && (
            <button
              type="button"
              className={link}
              aria-label="Add Bcc recipients"
              title="Bcc: they receive the message, and nobody else on it can see that they did."
              onClick={() => {
                setOpenBcc(true)
                setFocusOn('bcc')
              }}
            >
              Bcc
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function Row({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 items-start gap-2">
      <label htmlFor={id} className="w-8 shrink-0 pt-2 text-xs font-medium text-muted-foreground">
        {label}
      </label>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}
