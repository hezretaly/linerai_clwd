import { useState } from 'react'

import { fileSize, type Attachment } from '../../lib/email'
import { Icon } from '../Icon'

/* The files a message carries, read-only.
 *
 * **A download is a plain link, never the JSON client.** The bytes come back
 * with the server's own `Content-Disposition` and `nosniff`, and `resolve`
 * turns the server's path into an href: `withStore` on a dealership page, so
 * the request reaches the store that holds the file, and the path unchanged
 * on `/ops`, which is never per store.
 *
 * **A refused file is listed with its reason and no link.** Somebody really
 * sent it, so leaving it out would misreport the message; offering a link to
 * bytes that were never kept would be a 404 with nothing saying why.
 *
 * A thumbnail is drawn only for what the server marked `inline` -- a raster
 * image it recognised itself -- and a thumbnail that fails to load is
 * dropped rather than left as a torn-image icon beside the name.
 */

export type AttachmentListProps = {
  items: Attachment[]
  resolve: (url: string) => string
}

function withInline(url: string): string {
  return `${url}${url.includes('?') ? '&' : '?'}inline=1`
}

export function AttachmentList({ items, resolve }: AttachmentListProps) {
  const [broken, setBroken] = useState<Record<string, boolean>>({})
  if (!items.length) return null

  return (
    <div className="min-w-0 space-y-1.5">
      <p className="text-xs font-medium text-muted-foreground">
        {items.length === 1 ? '1 file' : `${items.length} files`}
      </p>
      <div role="list" aria-label="Files" className="flex min-w-0 flex-wrap gap-2">
        {items.map((a) => {
          const kept = Boolean(a.url) && !a.refused
          const thumb = kept && a.inline && !broken[a.id]
          const body = (
            <>
              {thumb ? (
                <img
                  src={resolve(withInline(a.url))}
                  alt=""
                  loading="lazy"
                  onError={() => setBroken((b) => ({ ...b, [a.id]: true }))}
                  className="h-10 w-10 shrink-0 rounded border border-border object-cover"
                />
              ) : (
                <Icon name="file" className="h-4 w-4 shrink-0 text-muted-foreground" />
              )}
              <span className="min-w-0 flex-1">
                <span className="block break-all font-medium text-foreground">{a.filename || 'Unnamed file'}</span>
                {a.size > 0 && <span className="tnum block text-muted-foreground">{fileSize(a.size)}</span>}
                {a.refused && <span className="block text-destructive">{a.refused}</span>}
              </span>
              {kept && <Icon name="download" className="h-4 w-4 shrink-0 text-muted-foreground" />}
            </>
          )
          const box =
            'flex w-full min-w-0 items-center gap-2 rounded-md border border-border px-2.5 py-1.5 text-xs'
          return (
            <div role="listitem" key={a.id} className="min-w-0 max-w-full basis-full sm:max-w-xs sm:basis-auto">
              {kept ? (
                <a
                  href={resolve(a.url)}
                  download={a.filename || true}
                  title={`Download ${a.filename}`}
                  className={`${box} bg-card transition-colors duration-150 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring`}
                >
                  {body}
                </a>
              ) : (
                <div className={`${box} ${a.refused ? 'border-destructive/30 bg-destructive-muted' : 'bg-muted/40'}`}>
                  {body}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
