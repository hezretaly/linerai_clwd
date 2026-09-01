import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../../lib/api'
import { Button, Sheet } from '../ui'

/* A person's own email sign-off, and the image under it.
 *
 * **Theirs, and only theirs.** A signature carries somebody's name and title,
 * so there is no admin view of these -- a manager rewriting a rep's would put
 * words under that rep's name in front of a buyer. It hangs off the account
 * menu rather than a settings page for the same reason: it is a personal
 * preference, not a dealership setting.
 *
 * **Empty is a real answer.** Leave it blank and the dealership's own block
 * goes out, which is what happened before any of this existed -- so the
 * fallback is shown rather than left as a mystery behind an empty box.
 */

interface Signature {
  text: string
  image_url: string
  updated_at: string
  /** What goes out when this person has written nothing. */
  fallback: string
  max_chars: number
  max_image_kb: number
}

export function SignatureSheet({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const file = useRef<HTMLInputElement>(null)
  const [text, setText] = useState<string | null>(null)
  const [problem, setProblem] = useState('')

  const { data } = useQuery({
    queryKey: ['my-signature'],
    queryFn: () => api.get<Signature>('/api/me/signature'),
  })

  // Seeded once from the server and edited locally after that. Re-seeding on
  // every refetch would throw away what somebody is halfway through typing.
  useEffect(() => {
    if (data && text === null) setText(data.text)
  }, [data, text])

  const done = (fresh: Signature) => {
    queryClient.setQueryData(['my-signature'], fresh)
    setProblem('')
  }
  const failed = (e: unknown) => setProblem(String((e as Error)?.message ?? e))

  const save = useMutation({
    mutationFn: () => api.put<Signature>('/api/me/signature', { text: text ?? '' }),
    onSuccess: done,
    onError: failed,
  })

  const upload = useMutation({
    // `api.upload` rather than a second hand-rolled fetch: it already leaves
    // the content type to the browser, which is what writes the multipart
    // boundary, and it carries the session cookie.
    mutationFn: (chosen: File) => api.upload<Signature>('/api/me/signature/image', chosen),
    onSuccess: done,
    onError: failed,
  })

  const drop = useMutation({
    mutationFn: () => api.del<Signature>('/api/me/signature/image'),
    onSuccess: done,
    onError: failed,
  })

  const value = text ?? ''
  const over = data ? value.length > data.max_chars : false

  return (
    <Sheet
      open
      onClose={onClose}
      title={<h2 className="text-sm font-semibold">My email signature</h2>}
    >
      <div className="space-y-4 p-4">
        <p className="text-xs leading-relaxed text-muted-foreground">
          Added to the bottom of every email you send from here. Liner's own
          replies are signed by the dealership, never by you.
        </p>

        <div>
          <label htmlFor="sig-text" className="mb-1 block text-xs font-medium">
            Sign-off
          </label>
          <textarea
            id="sig-text"
            value={value}
            onChange={(e) => setText(e.target.value)}
            rows={6}
            placeholder={data?.fallback}
            className="w-full resize-y rounded-md border border-input bg-background p-2 text-sm outline-none focus:border-ring focus:ring-1 focus:ring-ring"
          />
          <p className="mt-1 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
            <span>
              {value.length}
              {data ? ` / ${data.max_chars}` : ''}
            </span>
            {/* Blank is a real setting, so it says what blank does rather than
                leaving somebody to find out by sending one. */}
            <span className="ml-auto">
              {value.trim()
                ? 'Yours goes out.'
                : "Empty — the dealership's block goes out instead."}
            </span>
          </p>
        </div>

        <div>
          <p className="mb-1 text-xs font-medium">Image</p>
          {data?.image_url ? (
            <div className="flex items-start gap-3">
              <img
                src={data.image_url}
                alt=""
                className="max-h-20 rounded border border-border bg-background"
              />
              <Button size="sm" variant="ghost" onClick={() => drop.mutate()}>
                Remove
              </Button>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">None.</p>
          )}
          <input
            ref={file}
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp"
            hidden
            onChange={(e) => {
              const chosen = e.target.files?.[0]
              if (chosen) upload.mutate(chosen)
              e.target.value = ''
            }}
          />
          <Button
            size="sm"
            variant="ghost"
            className="mt-2"
            disabled={upload.isPending}
            onClick={() => file.current?.click()}
          >
            {upload.isPending ? 'Uploading...' : data?.image_url ? 'Replace' : 'Add an image'}
          </Button>
          {/* **An image only reaches a mail client that loads pictures**, and
              plain-text readers never will. Said here rather than discovered:
              a logo nobody can see is not worth a phone number left out of the
              words above it. */}
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            PNG, JPEG, GIF or WebP, up to {data?.max_image_kb ?? 512} KB. It rides in
            the HTML half of the email, so anything the sign-off has to say — a
            name, a number — belongs in the text above rather than in the picture.
          </p>
        </div>

        {problem && <p className="text-xs text-destructive">{problem}</p>}

        <div className="flex justify-end gap-2 border-t border-border pt-3">
          <Button size="sm" variant="ghost" onClick={onClose}>
            Close
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={over || save.isPending || text === null}
            onClick={() => save.mutate()}
          >
            {save.isPending ? 'Saving...' : 'Save'}
          </Button>
        </div>
      </div>
    </Sheet>
  )
}
