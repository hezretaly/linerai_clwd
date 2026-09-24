/** The website chat's side of `embed.js`: talking to the page around the frame.
 *
 *  On a dealer's own site the chat is an iframe from another origin, so the
 *  only way it learns anything about that page -- which car is on it, where
 *  the buyer's conversation id is kept -- is a message from the loader, and
 *  the only way it tells that page anything -- a lead for their Tag Manager,
 *  a reply waiting while the panel is shut -- is a message back.
 *
 *  **Both directions are pinned to one origin.** The loader names its page's
 *  origin in `?parent=`; what arrives from anywhere else is ignored, and what
 *  is sent goes to that origin only, so a frame that ended up somewhere else
 *  cannot hand out the conversation id. The browser already refuses to draw
 *  the frame on a site the dealership did not list (`frame-ancestors`), so
 *  `?parent=` naming a page it is not on gets no answer rather than a leak.
 *
 *  With no loader answering -- the frame opened on its own, or an old cached
 *  copy of the tag -- the handshake times out and the chat carries on as the
 *  plain `/chat` it has always been.
 */

import { WIDGET } from './store'

/** What the loader read off the page. Claims from somebody else's page: the
 *  server decides what, if anything, is kept (`app/page_context.py`). */
export interface PageReport {
  url: string
  title: string
  vin: string
}

export interface ParentInit {
  conversationId: string | null
  page: PageReport | null
  open: boolean
}

const ORIGIN = /^https?:\/\/[a-z0-9.-]+(:\d{1,5})?$/i
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** How long the chat waits for the loader before carrying on without it. The
 *  loader answers in the same tick it hears from us, so this only ever runs
 *  out when nobody is there; long enough for a slow phone regardless. */
const HANDSHAKE_MS = 1500

function parentOrigin(): string {
  if (!WIDGET || typeof window === 'undefined' || window.parent === window) return ''
  const raw = new URLSearchParams(window.location.search).get('parent') || ''
  return ORIGIN.test(raw) ? raw.toLowerCase() : ''
}

/** The dealer page's origin, or "" when this chat is not framed by a loader. */
export const PARENT: string = parentOrigin()

let open = true
let settle: ((init: ParentInit | null) => void) | null = null
const pageListeners = new Set<(page: PageReport) => void>()
const visibilityListeners = new Set<(open: boolean) => void>()

function cleanPage(raw: unknown): PageReport | null {
  if (!raw || typeof raw !== 'object') return null
  const p = raw as Record<string, unknown>
  const url = String(p.url ?? '').slice(0, 2000)
  if (!url) return null
  return {
    url,
    title: String(p.title ?? '').slice(0, 300),
    vin: String(p.vin ?? '').slice(0, 17),
  }
}

function onMessage(event: MessageEvent) {
  if (event.source !== window.parent || event.origin.toLowerCase() !== PARENT) return
  const m = event.data as Record<string, unknown> | null
  if (!m || m.liner !== 1 || typeof m.type !== 'string') return
  if (m.type === 'init') {
    open = m.open !== false
    const id = typeof m.conversationId === 'string' && UUID.test(m.conversationId)
      ? m.conversationId : null
    settle?.({ conversationId: id, page: cleanPage(m.page), open })
    settle = null
  } else if (m.type === 'page') {
    const page = cleanPage(m.page)
    if (page) pageListeners.forEach((listener) => listener(page))
  } else if (m.type === 'visibility') {
    open = m.open === true
    visibilityListeners.forEach((listener) => listener(open))
  }
}

/** Tell the page around the frame something. A no-op off a dealer's site. */
export function postToParent(type: string, data: Record<string, unknown> = {}): void {
  if (!PARENT) return
  try {
    window.parent.postMessage({ ...data, liner: 1, type }, PARENT)
  } catch {
    // A parent origin the browser will not post to. Nothing to do: the chat
    // itself works regardless, and this is only ever a courtesy to the page.
  }
}

/** The loader's answer to our hello: the conversation it kept on the dealer's
 *  domain and the page the buyer is on. `null` when nobody answered. Started
 *  as soon as this module loads, before React has drawn anything, so the
 *  answer is usually waiting by the time the chat asks for it. */
export const parentInit: Promise<ParentInit | null> = new Promise((resolve) => {
  if (!PARENT) {
    resolve(null)
    return
  }
  settle = resolve
  window.addEventListener('message', onMessage)
  postToParent('hello', { version: '2' })
  setTimeout(() => {
    settle?.(null)
    settle = null
  }, HANDSHAKE_MS)
})

/** The buyer moved to another page of the dealer's site. */
export function onParentPage(listener: (page: PageReport) => void): () => void {
  pageListeners.add(listener)
  return () => pageListeners.delete(listener)
}

/** The panel around the chat was opened or shut. */
export function onParentVisibility(listener: (open: boolean) => void): () => void {
  visibilityListeners.add(listener)
  return () => visibilityListeners.delete(listener)
}

/** Whether the buyer can see the chat right now. Always true off a dealer's
 *  site, where the page is the chat. */
export function parentOpen(): boolean {
  return PARENT ? open : true
}
