import clsx from 'clsx'
import { useId, useRef, useState, type KeyboardEvent } from 'react'

import {
  formatAddr,
  looksLikeAddress,
  parseEntry,
  splitPending,
  splitRecipients,
} from '../../lib/email'

/* Several addresses in one box, drawn as chips with a text input after them.
 *
 * **The value is one string, and it is the whole box.** Chips and the text
 * still being typed are both in it, comma separated, so a caller's Send reads
 * what is on screen even when the last address never became a chip -- a
 * person who types an address and presses Send has not done anything wrong,
 * and the server splits the string exactly the way the chips were split
 * (`splitPending` is the server's `split_entries`). It also keeps every
 * existing caller and gate working: a composer that used to hold "a@b.com"
 * still holds "a@b.com", and the first `<input>` on the page is still where
 * it is typed.
 *
 * **What this box emitted is told apart from what a caller set.** The text
 * after the last comma is the input's value only while it is being typed;
 * an address a caller put there -- a reply's To, a buyer's email -- is
 * finished and draws as a chip. Remembering the last emitted string is how
 * the two are told apart without a second copy of the value in state.
 *
 * **The text input is the only labelable element in here.** A chip's remove
 * control is a `span`, not a `<button>`: callers wrap fields in a `<label>`,
 * and a label hands a click on its caption to the first labelable element
 * inside it -- which would be the first chip's remove button, so clicking
 * the word "To" would delete a recipient. The suggestion list is not
 * `ul > li > button` for a reason of the same shape: `ops_browser` finds the
 * message list as the first one on the page.
 */

export type RecipientSuggestion = { name: string; email: string }

export type RecipientInputProps = {
  value: string
  onChange: (value: string) => void
  suggestions?: RecipientSuggestion[]
  placeholder?: string
  ariaLabel: string
  autoFocus?: boolean
  id?: string
}

const MAX_SUGGESTIONS = 6

/** Chips, then the text being typed, as the one string the caller holds. */
function compose(chips: string[], pending: string): string {
  return chips.length ? `${chips.join(', ')}, ${pending}` : pending
}

export function RecipientInput({
  value,
  onChange,
  suggestions,
  placeholder,
  ariaLabel,
  autoFocus,
  id,
}: RecipientInputProps) {
  const emitted = useRef<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const listId = useId()
  const [focused, setFocused] = useState(false)
  const [open, setOpen] = useState(true)
  const [active, setActive] = useState(0)

  // Compared trimmed, so a caller that tidies the string on the way back
  // does not turn half-typed text into a chip; drawn from what was emitted,
  // so the space somebody just typed after a first name is not lost to it.
  const own = emitted.current !== null && value.trim() === emitted.current.trim()
  const { entries, rest } = splitPending(own ? (emitted.current as string) : value)
  const chips = own || !rest.trim() ? entries : [...entries, rest.trim()]
  const pending = own ? rest.replace(/^\s+/, '') : ''

  const emit = (next: string) => {
    emitted.current = next
    onChange(next)
  }

  const commit = () => {
    if (pending.trim()) emit(compose([...chips, pending.trim()], ''))
  }

  const remove = (index: number) => {
    emit(compose(chips.filter((_, i) => i !== index), pending))
    input.current?.focus()
  }

  const query = pending.trim().toLowerCase()
  const taken = new Set(chips.map((c) => parseEntry(c).address.toLowerCase()))
  const matches =
    query && suggestions
      ? suggestions
          .filter(
            (s) =>
              s.email &&
              !taken.has(s.email.toLowerCase()) &&
              (s.email.toLowerCase().includes(query) || (s.name || '').toLowerCase().includes(query)),
          )
          .slice(0, MAX_SUGGESTIONS)
      : []
  const showList = focused && open && matches.length > 0
  const highlighted = Math.min(active, Math.max(matches.length - 1, 0))

  const pick = (s: RecipientSuggestion) => {
    emit(compose([...chips, formatAddr({ name: s.name || '', address: s.email })], ''))
    setActive(0)
    input.current?.focus()
  }

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (showList && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
      event.preventDefault()
      const step = event.key === 'ArrowDown' ? 1 : -1
      setActive((highlighted + step + matches.length) % matches.length)
      return
    }
    if (event.key === 'Enter') {
      // Never a submit: a composer inside a <form> would send the message
      // from the To box, half addressed.
      event.preventDefault()
      if (showList) pick(matches[highlighted])
      else commit()
      return
    }
    if (event.key === 'Escape' && showList) {
      // Only the list: the drawer this sits in closes on Escape too, and a
      // person dismissing suggestions has not asked to lose the message.
      event.stopPropagation()
      setOpen(false)
      return
    }
    if (event.key === 'Backspace' && !pending && chips.length) {
      const el = event.currentTarget
      if (el.selectionStart === 0 && el.selectionEnd === 0) {
        event.preventDefault()
        emit(compose(chips.slice(0, -1), ''))
      }
    }
  }

  return (
    <div className="relative min-w-0">
      <div
        onClick={() => input.current?.focus()}
        className={clsx(
          'flex min-h-9 w-full min-w-0 flex-wrap items-center gap-1 rounded-md border border-input bg-background px-2 py-1 text-sm shadow-xs',
          focused && 'ring-2 ring-ring',
        )}
      >
        {chips.map((chip, index) => {
          const ok = looksLikeAddress(chip)
          const { name, address } = parseEntry(chip)
          return (
            <span
              key={`${index}-${chip}`}
              title={
                ok
                  ? chip
                  : `"${chip}" is not an email address, so this message will be refused as it stands. Remove it and type the address again.`
              }
              className={clsx(
                'inline-flex min-w-0 max-w-full items-center gap-1 rounded-full border py-0.5 pl-2 pr-1 text-xs',
                ok
                  ? 'border-border bg-secondary text-secondary-foreground'
                  : 'border-destructive/40 bg-destructive-muted text-destructive',
              )}
            >
              <span className="min-w-0 break-all">
                {name ? (
                  <>
                    <span className="font-medium">{name}</span>{' '}
                    <span className={ok ? 'text-muted-foreground' : undefined}>{address}</span>
                  </>
                ) : (
                  address
                )}
              </span>
              <span
                role="button"
                tabIndex={-1}
                aria-label={`Remove ${chip}`}
                title="Remove"
                onMouseDown={(event) => event.preventDefault()}
                onClick={(event) => {
                  event.stopPropagation()
                  remove(index)
                }}
                className="inline-flex h-5 w-5 shrink-0 cursor-pointer items-center justify-center rounded-full text-sm leading-none opacity-70 hover:bg-foreground/10 hover:opacity-100"
              >
                ×
              </span>
            </span>
          )
        })}
        <input
          ref={input}
          id={id}
          type="text"
          inputMode="email"
          autoComplete="off"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          autoFocus={autoFocus}
          aria-label={ariaLabel}
          placeholder={chips.length ? undefined : placeholder}
          value={pending}
          role={suggestions ? 'combobox' : undefined}
          aria-autocomplete={suggestions ? 'list' : undefined}
          aria-expanded={suggestions ? showList : undefined}
          aria-controls={suggestions ? listId : undefined}
          aria-activedescendant={showList ? `${listId}-${highlighted}` : undefined}
          onChange={(event) => {
            setOpen(true)
            setActive(0)
            emit(compose(chips, event.target.value))
          }}
          onKeyDown={onKeyDown}
          onPaste={(event) => {
            // A pasted list is finished text: every address in it becomes a
            // chip at once, rather than the last one waiting for a comma.
            const text = event.clipboardData.getData('text')
            if (!text.includes('@')) return
            event.preventDefault()
            const el = event.currentTarget
            const before = pending.slice(0, el.selectionStart ?? pending.length)
            const after = pending.slice(el.selectionEnd ?? pending.length)
            // Pasted straight after a finished address is a second address,
            // not the end of the first one.
            const joint = looksLikeAddress(before.trim()) && !/[,;\s]$/.test(before) ? ', ' : ''
            emit(compose([...chips, ...splitRecipients(before + joint + text + after)], ''))
          }}
          onFocus={() => setFocused(true)}
          onBlur={() => {
            setFocused(false)
            commit()
          }}
          className="h-7 min-w-[8rem] flex-1 bg-transparent px-1 text-sm outline-none placeholder:text-muted-foreground"
        />
      </div>
      {showList && (
        <div
          id={listId}
          role="listbox"
          aria-label={`${ariaLabel} suggestions`}
          className="absolute left-0 right-0 top-full z-30 mt-1 max-h-56 overflow-y-auto rounded-md border border-border bg-popover py-1 text-popover-foreground shadow-md"
        >
          {matches.map((s, index) => (
            <div
              key={s.email}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === highlighted}
              // Mouse down, not click: the input would blur first and commit
              // the half-typed text as a chip of its own.
              onMouseDown={(event) => {
                event.preventDefault()
                pick(s)
              }}
              onMouseEnter={() => setActive(index)}
              className={clsx(
                'cursor-pointer px-3 py-1.5 text-sm',
                index === highlighted && 'bg-accent text-accent-foreground',
              )}
            >
              {s.name && <span className="font-medium">{s.name} </span>}
              <span className="break-all text-muted-foreground">{s.email}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
