import clsx from 'clsx'
import { useEffect, useRef, useState, type DragEvent } from 'react'

import { api, ApiError } from '../../lib/api'
import { fileSize, type Attachment } from '../../lib/email'
import { Icon } from '../Icon'

/* Files for a message that is still being written.
 *
 * **A file is uploaded when it is picked, not when the message is sent.**
 * The send is then a list of ids, a JSON body like every other request here,
 * and a refused file is refused *now*, beside its name, while the rep can
 * still pick another -- not as a failed send after they have pressed it.
 * The server is the one judge of what may be attached (its type list and
 * size limits live in `email_files`), so nothing here second-guesses it; a
 * refusal shows the server's own sentence.
 *
 * **Removing a file deletes only what this picker uploaded.** A forwarded
 * message's files are in the list too, and they belong to that message: the
 * server would refuse the delete anyway, but asking produces a 404 that
 * `make shots` rightly counts as a broken request.
 *
 * **The file input comes after the button, and callers put this under the
 * editor.** `ops_browser` finds the composer's To and Subject as the first
 * two `<input>`s on the page, and a file input ahead of them would be To.
 * Rows are `div`s with list roles, not `ul > li`, for the same gate's
 * reason: the first `ul li button` on the page is the message list.
 */

export type AttachmentPickerProps = {
  value: Attachment[]
  onChange: (next: Attachment[]) => void
  uploadPath?: string
  removeBase?: string
  disabled?: boolean
  /** How many files are still uploading, whenever that changes. A composer
   *  holds its Send until this is zero: pressing it mid-upload sent the
   *  message without the file, and nothing said so. */
  onBusy?: (uploading: number) => void
}

type Busy = { key: string; name: string; size: number }
type Problem = { key: string; name: string; message: string }

function problemOf(err: unknown): string {
  if (err instanceof ApiError) {
    // `api` falls back to "POST <path> failed" when the body had no detail:
    // true, and no help to somebody choosing which file to try instead.
    const bare = /^\w+ \/\S* failed$/.test(err.message)
    if (!bare) return err.message
    if (err.status === 413) return 'This file is larger than a message can carry.'
    return `The upload was refused (${err.status}).`
  }
  return 'The upload did not finish. Check the connection and try again.'
}

export function AttachmentPicker({
  value,
  onChange,
  uploadPath = '/api/email/attachments',
  removeBase = '/api/email/attachments',
  disabled = false,
  onBusy,
}: AttachmentPickerProps) {
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState<Busy[]>([])
  const onBusyRef = useRef(onBusy)
  onBusyRef.current = onBusy
  useEffect(() => {
    onBusyRef.current?.(busy.length)
  }, [busy.length])
  const [problems, setProblems] = useState<Problem[]>([])
  const [over, setOver] = useState(false)
  // Uploads finish in any order, so each one adds to the newest list rather
  // than to the one it was started from.
  const latest = useRef(value)
  latest.current = value
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange
  const mine = useRef(new Set<string>())

  const add = (files: FileList | File[] | null) => {
    if (!files || disabled) return
    for (const file of Array.from(files)) {
      const key = `${file.name}-${file.size}-${Math.random().toString(36).slice(2)}`
      setBusy((b) => [...b, { key, name: file.name, size: file.size }])
      api
        .upload<Attachment>(uploadPath, file)
        .then((attachment) => {
          if (attachment.refused) {
            setProblems((p) => [...p, { key, name: file.name, message: attachment.refused }])
            return
          }
          mine.current.add(attachment.id)
          const next = [...latest.current, attachment]
          latest.current = next
          onChangeRef.current(next)
        })
        .catch((err) => setProblems((p) => [...p, { key, name: file.name, message: problemOf(err) }]))
        .finally(() => setBusy((b) => b.filter((x) => x.key !== key)))
    }
  }

  const remove = (id: string) => {
    const next = latest.current.filter((a) => a.id !== id)
    latest.current = next
    onChangeRef.current(next)
    if (mine.current.has(id)) {
      mine.current.delete(id)
      api.del(`${removeBase}/${encodeURIComponent(id)}`).catch(() => {})
    }
  }

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    if (!event.dataTransfer?.files?.length) return
    event.preventDefault()
    setOver(false)
    add(event.dataTransfer.files)
  }

  return (
    <div
      onDragOver={(event) => {
        if (disabled || !event.dataTransfer?.types?.includes('Files')) return
        event.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
      className={clsx(
        'min-w-0 space-y-2 rounded-md transition-colors duration-150',
        over && 'bg-accent/60 ring-2 ring-ring',
      )}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={disabled}
          onClick={() => input.current?.click()}
          className={clsx(
            'inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-background px-3 text-sm font-medium text-foreground shadow-xs transition-colors duration-150',
            'hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            'disabled:pointer-events-none disabled:opacity-50',
          )}
        >
          <Icon name="paperclip" className="h-4 w-4" />
          Attach files
        </button>
        <input
          ref={input}
          type="file"
          multiple
          hidden
          tabIndex={-1}
          aria-label="Attach files"
          disabled={disabled}
          onChange={(event) => {
            add(event.target.files)
            // The same file picked twice in a row is a second attachment, and
            // an input still holding it would not report the change.
            event.target.value = ''
          }}
        />
        {!disabled && (
          <span className="hidden text-xs text-muted-foreground sm:inline">or drop them here</span>
        )}
      </div>

      {(value.length > 0 || busy.length > 0 || problems.length > 0) && (
        <div role="list" aria-label="Attached files" className="min-w-0 space-y-1">
          {value.map((a) => (
            <div
              role="listitem"
              key={a.id}
              className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 rounded-md border border-border bg-muted/40 px-2.5 py-1.5 text-xs"
            >
              <Icon name="paperclip" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 break-all font-medium text-foreground">{a.filename}</span>
              <span className="tnum shrink-0 text-muted-foreground">{fileSize(a.size)}</span>
              <button
                type="button"
                disabled={disabled}
                aria-label={`Remove ${a.filename}`}
                title="Remove"
                onClick={() => remove(a.id)}
                className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded text-sm leading-none text-muted-foreground hover:bg-accent hover:text-accent-foreground disabled:opacity-50"
              >
                ×
              </button>
              {a.refused && <p className="w-full text-destructive">{a.refused}</p>}
            </div>
          ))}
          {busy.map((b) => (
            <div
              role="listitem"
              key={b.key}
              aria-busy="true"
              className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 rounded-md border border-dashed border-border px-2.5 py-1.5 text-xs text-muted-foreground"
            >
              <span
                aria-hidden="true"
                className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-border border-t-foreground"
              />
              <span className="min-w-0 flex-1 break-all">{b.name}</span>
              <span className="tnum shrink-0">{fileSize(b.size)}</span>
              <span className="shrink-0">Uploading</span>
            </div>
          ))}
          {problems.map((p) => (
            <div
              role="listitem"
              key={p.key}
              className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 rounded-md border border-destructive/30 bg-destructive-muted px-2.5 py-1.5 text-xs text-destructive"
            >
              <span className="min-w-0 flex-1 break-all">
                <span className="font-medium">{p.name}</span> was not attached. {p.message}
              </span>
              <button
                type="button"
                aria-label={`Dismiss the message about ${p.name}`}
                title="Dismiss"
                onClick={() => setProblems((all) => all.filter((x) => x.key !== p.key))}
                className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded text-sm leading-none hover:bg-destructive/10"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
