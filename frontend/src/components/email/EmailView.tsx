import clsx from 'clsx'
import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import DOMPurify, { type DOMPurify as Purifier } from 'dompurify'

import { dateTime } from '../../lib/format'
import { listedAttachments, type Addr, type MailContent } from '../../lib/email'
import { Badge } from '../ui'
import { AttachmentList } from './AttachmentList'

/* One email, read.
 *
 * **Somebody else's HTML is drawn in a frame of its own, behind three
 * walls.** The server cleans it (`email_html.clean_inbound`: no scripts, no
 * forms, no remote images unless asked); this cleans it again with DOMPurify,
 * because a second sanitiser from a different codebase fails differently from
 * the first; and the frame is sandboxed with no `allow-scripts` and carries a
 * Content-Security-Policy of its own, so whatever both missed still cannot
 * run, post a form or fetch anything but an image. A newsletter's 600-pixel
 * table and its `<style>` blocks are contained by the frame too -- drawn in
 * the page, either one would widen or restyle the dashboard around it.
 *
 * **Nothing in the frame may be refused by the frame's own policy.** A
 * blocked request is a console error, and `make shots` fails a page on any
 * console error; worse, a relative `src` would resolve against *our* host and
 * 404 there. So the cleaning pass keeps exactly what the policy allows --
 * images from `data:` and http(s), links to http(s), mailto and tel -- and
 * drops the rest, including CSS `url()`s pointing anywhere else.
 *
 * `allow-same-origin` is what lets this page measure the frame's height, and
 * it is safe only because scripts are off: a frame that could run script
 * *and* share our origin could reach out of its sandbox. Links open in a new
 * tab (`allow-popups-to-escape-sandbox`, so the page they open is a normal
 * page rather than a crippled one) with no referrer and no opener.
 */

export type EmailViewProps = {
  message: MailContent
  resolve: (url: string) => string
  onShowImages?: () => void
  showBcc?: boolean
  compact?: boolean
}

// -------------------------------------------------------------- cleaning

/** What the frame's own policy allows, and so all the cleaning keeps. */
const CSP = "default-src 'none'; img-src data: https: http:; style-src 'unsafe-inline'; font-src data:"
const IMAGE_URL = /^(?:data:image\/|https?:|\/\/)/i
const LINK_URL = /^(?:https?:|mailto:|tel:)/i

const SANITIZE = {
  WHOLE_DOCUMENT: false,
  FORBID_TAGS: [
    'style', 'form', 'input', 'button', 'textarea', 'select', 'script', 'iframe', 'object',
    'embed', 'svg', 'math',
    // Each of these fetches something the policy refuses, or rewrites the
    // frame's own document (`base`, `meta`): gone rather than blocked.
    'link', 'meta', 'base', 'video', 'audio', 'source', 'track', 'frame', 'frameset', 'noscript',
    'template',
  ],
  FORBID_ATTR: ['srcset', 'action', 'formaction', 'ping', 'poster'],
  ALLOW_DATA_ATTR: false,
}

/** A length in viewport units. Inside the frame the viewport *is* the frame,
 *  whose height follows the content -- so `min-height:100vh` plus a little
 *  padding grows the frame, which grows the content, for ever. Mail clients
 *  mostly ignore these units anyway, so a declaration using one is dropped. */
const VIEWPORT_UNIT = /\d\s*[dls]?v(?:h|w|min|max|b|i)\b/i

/** CSS `url()`s the policy would refuse -- `cid:`, a relative path onto our
 *  own host -- become `none`, so a background that cannot load is simply not
 *  drawn. `image-set()` can name a URL without `url()`, and email has no use
 *  for it, so a style carrying one is dropped whole. */
function cleanStyle(style: string): string | null {
  if (/image-set\s*\(/i.test(style)) return null
  const kept = VIEWPORT_UNIT.test(style)
    ? style
        .split(';')
        .filter((declaration) => !VIEWPORT_UNIT.test(declaration))
        .join(';')
    : style
  return kept.replace(/url\(\s*(['"]?)(.*?)\1\s*\)/gi, (whole, _quote: string, url: string) =>
    IMAGE_URL.test(url.trim()) ? whole : 'none',
  )
}

let purifier: Purifier | null = null

/** One DOMPurify instance with this reader's hooks. Its own instance, not the
 *  shared default, so the hooks cannot leak into any other caller's cleaning. */
function cleaner(): Purifier {
  if (purifier) return purifier
  const p = DOMPurify(window)
  p.addHook('uponSanitizeAttribute', (_node, data) => {
    const value = (data.attrValue || '').trim()
    if (data.attrName === 'src' || data.attrName === 'background') {
      if (!IMAGE_URL.test(value)) data.keepAttr = false
    } else if (data.attrName === 'href') {
      if (!LINK_URL.test(value)) data.keepAttr = false
    } else if (data.attrName === 'style') {
      const style = cleanStyle(value)
      if (style === null) data.keepAttr = false
      else data.attrValue = style
    }
  })
  p.addHook('afterSanitizeAttributes', (node) => {
    if (node.tagName === 'A' && node.hasAttribute('href')) {
      node.setAttribute('target', '_blank')
      node.setAttribute('rel', 'noopener noreferrer')
    } else if (node.hasAttribute('target')) {
      node.removeAttribute('target')
    }
  })
  purifier = p
  return p
}

/** The received HTML, cleaned again here. Exported for a caller that wants
 *  to know whether anything survives before drawing a frame for it. */
export function sanitizeEmailHtml(html: string): string {
  if (!(html || '').trim() || typeof window === 'undefined') return ''
  return String(cleaner().sanitize(html, SANITIZE))
}

/* The frame's own stylesheet: a readable default for mail that brings none.
 * `Canvas` and `CanvasText` are the system's own page colours rather than
 * colours of ours -- email is written for a light page, and the frame cannot
 * see the dashboard's tokens anyway. */
const BASE_CSS = [
  ':root{color-scheme:light}',
  'html,body{margin:0;padding:0}',
  'body{background:Canvas;color:CanvasText;font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;overflow-wrap:break-word;word-wrap:break-word}',
  'body>div{display:flow-root;padding:12px 14px}',
  'img{max-width:100%;height:auto}',
  'pre{white-space:pre-wrap}',
  'blockquote{margin:0 0 0 4px;padding-left:12px;border-left:2px solid GrayText}',
].join('')

function frameDocument(clean: string): string {
  return (
    '<!doctype html><html><head><meta charset="utf-8">' +
    `<meta http-equiv="Content-Security-Policy" content="${CSP}">` +
    '<meta name="referrer" content="no-referrer">' +
    '<base target="_blank">' +
    `<style>${BASE_CSS}</style>` +
    `</head><body><div>${clean}</div></body></html>`
  )
}

// ----------------------------------------------------------------- frame

const MIN_HEIGHT = 24
/** Past this the frame scrolls inside itself. Also the stop for a body whose
 *  height is measured in `vh`: it grows with the frame, and without a
 *  ceiling it would chase it for ever. */
const MAX_HEIGHT = 40000
const MAX_MEASURES = 200

function MailFrame({ html, compact }: { html: string; compact: boolean }) {
  const doc = useMemo(() => frameDocument(sanitizeEmailHtml(html)), [html])
  const frame = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(compact ? 80 : 160)

  // A layout effect, so the load listener is in place before the browser
  // gets a turn to finish loading the srcdoc React has just set.
  useLayoutEffect(() => {
    const el = frame.current
    if (!el) return
    let observer: ResizeObserver | null = null
    let raf = 0
    let measures = 0

    const measure = () => {
      raf = 0
      try {
        const d = el.contentDocument
        const box = d?.body?.firstElementChild as HTMLElement | null | undefined
        if (!d || !box) return
        // A horizontal scrollbar takes its height out of the frame; without
        // it the last line would sit under the bar.
        const bar = Math.max(0, (d.defaultView?.innerHeight ?? 0) - d.documentElement.clientHeight)
        const content = Math.max(box.scrollHeight, box.getBoundingClientRect().height)
        const next = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.ceil(content + bar)))
        setHeight((prev) => (Math.abs(prev - next) > 1 ? next : prev))
      } catch {
        // The frame is gone or no longer ours to read: keep the last height.
      }
    }
    const schedule = () => {
      // A frame, not now: resizing the frame inside the observer's own
      // callback is how "ResizeObserver loop" errors are made.
      if (raf || measures >= MAX_MEASURES) return
      measures += 1
      raf = requestAnimationFrame(measure)
    }
    const attach = () => {
      observer?.disconnect()
      observer = null
      measures = 0
      try {
        const box = el.contentDocument?.body?.firstElementChild
        if (box && typeof ResizeObserver !== 'undefined') {
          observer = new ResizeObserver(schedule)
          observer.observe(box)
        }
      } catch {
        // Not readable: the height stays where it is, which is still a frame
        // that scrolls rather than a page that breaks.
      }
      schedule()
    }

    el.addEventListener('load', attach)
    if (el.contentDocument?.body?.firstElementChild) attach()
    return () => {
      el.removeEventListener('load', attach)
      observer?.disconnect()
      if (raf) cancelAnimationFrame(raf)
    }
  }, [doc])

  return (
    <div className="min-w-0 overflow-hidden rounded-md border border-border">
      <iframe
        ref={frame}
        title="Message"
        sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
        referrerPolicy="no-referrer"
        srcDoc={doc}
        style={{ height }}
        className="block w-full border-0"
      />
    </div>
  )
}

// ---------------------------------------------------------------- reader

function Person({ a }: { a: Addr }) {
  if (!a.name || a.name.toLowerCase() === a.address.toLowerCase()) {
    return <span className="break-all text-foreground">{a.address}</span>
  }
  return (
    <span className="break-words">
      <span className="font-medium text-foreground">{a.name}</span>{' '}
      <span className="break-all text-muted-foreground">&lt;{a.address}&gt;</span>
    </span>
  )
}

function People({ list }: { list: Addr[] }) {
  return (
    <>
      {list.map((a, i) => (
        <span key={`${a.address}-${i}`}>
          {i > 0 && ', '}
          <Person a={a} />
        </span>
      ))}
    </>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  )
}

export function EmailView({
  message,
  resolve,
  onShowImages,
  showBcc = false,
  compact = false,
}: EmailViewProps) {
  const html = (message.html || '').trim()
  const text = message.text || ''
  const files = listedAttachments(message)
  const from = (message.from?.address || '').toLowerCase()
  const replyTo = message.reply_to || []
  // Only worth a line when answering would go somewhere other than From.
  const showReplyTo = replyTo.some((a) => a.address.toLowerCase() !== from)
  const held = message.images_held || 0

  return (
    <article className={clsx('min-w-0', compact ? 'space-y-2' : 'space-y-3')}>
      <header
        className={clsx(
          'min-w-0 rounded-md border border-border bg-muted/40 text-xs',
          compact ? 'p-2' : 'p-3',
        )}
      >
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5">
          <Row label="From">
            {message.from?.address ? <Person a={message.from} /> : <span className="text-muted-foreground">Unknown sender</span>}
          </Row>
          {message.to.length > 0 && (
            <Row label="To">
              <People list={message.to} />
            </Row>
          )}
          {message.cc.length > 0 && (
            <Row label="Cc">
              <People list={message.cc} />
            </Row>
          )}
          {showBcc && message.bcc.length > 0 && (
            <Row label="Bcc">
              <People list={message.bcc} />
            </Row>
          )}
          {showReplyTo && (
            <Row label="Reply-To">
              <People list={replyTo} />
            </Row>
          )}
          {message.date && (
            <Row label="Date">
              <span className="tnum text-foreground">{dateTime(message.date)}</span>
            </Row>
          )}
        </dl>
        {message.importance === 'high' && (
          <div className="mt-2 flex flex-wrap gap-2">
            <Badge tone="warning">High importance</Badge>
          </div>
        )}
      </header>

      {held > 0 && (
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          <span className="min-w-0">
            {held === 1 ? '1 remote image was' : `${held} remote images were`} not loaded, so the
            sender cannot tell that this was opened.
          </span>
          {onShowImages && (
            <button
              type="button"
              onClick={onShowImages}
              className="font-medium text-foreground underline underline-offset-2 hover:no-underline"
            >
              Show images
            </button>
          )}
        </div>
      )}

      {html ? (
        <MailFrame html={html} compact={compact} />
      ) : text.trim() ? (
        <div className="whitespace-pre-wrap break-words text-sm leading-relaxed">{text}</div>
      ) : (
        <p className="text-sm text-muted-foreground">
          {files.length ? 'No message text, only files.' : 'No message text.'}
        </p>
      )}

      <AttachmentList items={files} resolve={resolve} />
    </article>
  )
}
