/**
 * The shapes an email arrives in, and the small pieces of text work every
 * composer and reader needs.
 *
 * The types mirror what the server serialises (`email_envelopes.summary`,
 * `attachment_out` and the reader in `api/mail_reader.py`) so a list row, a
 * timeline card and the reader all describe one message the same way. The
 * helpers are here rather than in the components because they are plain
 * functions -- this file carries no editor and no sanitiser, so a page that
 * only needs `reSubject` does not pull a rich-text editor into its chunk.
 */

/** One person on an envelope. `name` is "" when the header carried none. */
export type Addr = { name: string; address: string }

export type Importance = 'normal' | 'high'

/** One file on a message, either direction.
 *
 *  `url` is "" when the bytes were never kept -- a refused type, or a relay
 *  that forwarded only the name -- and `refused` then says why. `inline` is
 *  the server's word that this is a raster image it recognised and may be
 *  shown in the page; it is not the MIME disposition, which is `disposition`.
 */
export type Attachment = {
  id: string
  filename: string
  size: number
  content_type: string
  content_id: string
  disposition: string
  refused: string
  inline: boolean
  url: string
  created_at: string | null
}

/** What a list row, a timeline card or a send response carries beyond the
 *  one address. `bcc` is only ever filled for mail we sent. */
export type EmailSummary = {
  to: Addr[]
  cc: Addr[]
  bcc: Addr[]
  reply_to: Addr[]
  has_html: boolean
  importance: Importance
  attachments: Attachment[]
}

/** Which reader a message comes from: the dealership's (`message` is an
 *  outreach row, `unmatched` a receipt nobody could place) or ours (`form`,
 *  `email`, `ours`). */
export type MailKind = 'message' | 'unmatched' | 'form' | 'email' | 'ours'

/** Addresses in header form (`Name <a@b>`), ours already taken out. */
export type ReplyTarget = { to: string[]; cc: string[]; subject: string }

/** One message, opened. `html` is the server-cleaned body or "" when there
 *  is none; `text` is the untrimmed text/plain half. */
export type MailContent = {
  kind: MailKind
  id: string
  direction: 'in' | 'out'
  subject: string
  from: Addr
  to: Addr[]
  cc: Addr[]
  bcc: Addr[]
  reply_to: Addr[]
  date: string | null
  text: string
  html: string
  images_held: number
  importance: Importance
  message_id: string
  attachments: Attachment[]
  lead_id: string | null
  reply: ReplyTarget
  reply_all: ReplyTarget
  forward: { subject: string; attachment_ids: string[] }
}

// ------------------------------------------------------------------ text

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** Plain text as HTML paragraphs, the way the server's `text_to_html` does
 *  it: a blank line starts a paragraph and a single newline is a `<br>`, so
 *  a Liner draft or a signature keeps its shape once it is in the editor. */
export function textToHtml(text: string): string {
  return (text || '')
    .trim()
    .split(/\n\s*\n/)
    .filter((block) => block.trim())
    .map((block) => `<p>${escapeHtml(block).replace(/\r?\n/g, '<br>')}</p>`)
    .join('')
}

/** `Re: ` once, however many times the thread has gone back and forth --
 *  a subject that is mostly prefix is somebody's fourth reply. */
export function reSubject(subject: string): string {
  const s = (subject || '').trim()
  if (!s) return ''
  return /^re\s*:/i.test(s) ? s : `Re: ${s}`
}

/** `Fwd: ` once. `Fw:` is Outlook's spelling of the same thing. */
export function fwdSubject(subject: string): string {
  const s = (subject || '').trim()
  if (!s) return ''
  return /^fwd?\s*:/i.test(s) ? s : `Fwd: ${s}`
}

/** A file's size the way people read one: `812 B`, `14 KB`, `2.4 MB`. */
export function fileSize(bytes: number): string {
  const n = Math.max(0, Number(bytes) || 0)
  if (n < 1024) return `${n} B`
  const kb = n / 1024
  if (kb < 1024) return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`
  const mb = kb / 1024
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`
}

// ------------------------------------------------------------- addresses

/** A display name that has to be quoted before it can sit in a header --
 *  `Doe, Jane` unquoted reads as two recipients. */
const SPECIALS = /[()<>[\]:;@\\,."]/

/** `Name <address>`, or the bare address when there is no name. Header
 *  form, so the result can go straight back into a To box or to the API. */
export function formatAddr(a: Addr): string {
  const name = (a.name || '').replace(/[\r\n]+/g, ' ').trim()
  if (!name || name.toLowerCase() === a.address.toLowerCase()) return a.address
  const safe = SPECIALS.test(name) ? `"${name.replace(/(["\\])/g, '\\$1')}"` : name
  return `${safe} <${a.address}>`
}

/** Several people as one comma-separated header value. */
export function addrList(list: Addr[]): string {
  return (list || []).map(formatAddr).join(', ')
}

/** The entries in what somebody typed into one box.
 *
 *  The same rule as the server's `email_addresses.split_entries`, on
 *  purpose: commas, semicolons and newlines separate, except inside a quoted
 *  name or an angle-bracketed address, so `"Doe, Jane" <jane@x.com>` stays
 *  one person. A box that split differently from the server would show two
 *  chips for what is sent as one address, or the other way round.
 */
export function splitRecipients(text: string): string[] {
  const { entries, rest } = splitPending(text)
  return rest.trim() ? [...entries, rest.trim()] : entries
}

/** `splitRecipients`, keeping apart what is finished and what is still being
 *  typed: everything before the last separator is `entries`, and whatever
 *  follows it -- possibly half an address -- is `rest`, verbatim. */
export function splitPending(text: string): { entries: string[]; rest: string } {
  const entries: string[] = []
  let current = ''
  let quoted = false
  let angled = false
  let escaped = false
  for (const ch of text || '') {
    if (escaped) {
      current += ch
      escaped = false
      continue
    }
    if (ch === '\\' && quoted) {
      current += ch
      escaped = true
      continue
    }
    if (ch === '"') quoted = !quoted
    else if (ch === '<' && !quoted) angled = true
    else if (ch === '>' && !quoted) angled = false
    if (',;\n\r'.includes(ch) && !quoted && !angled) {
      if (current.trim()) entries.push(current.trim())
      current = ''
      continue
    }
    current += ch
  }
  return { entries, rest: current }
}

/** One typed entry taken apart. Loose: a name and whatever sits in the
 *  angle brackets, or the whole thing as the address. The server decides
 *  what is really an address; this is for drawing a chip. */
export function parseEntry(entry: string): Addr {
  const text = (entry || '').trim()
  const angled = /^(.*)<([^<>]*)>\s*$/.exec(text)
  if (!angled) return { name: '', address: text }
  let name = angled[1].trim()
  if (name.startsWith('"') && name.endsWith('"') && name.length >= 2) {
    name = name.slice(1, -1).replace(/\\(.)/g, '$1')
  }
  return { name, address: angled[2].trim() }
}

/** The address alone, from a bare address or `Name <address>`. */
export function bareAddress(entry: string): string {
  return parseEntry(entry).address
}

/** something@something.something -- deliberately loose. It exists to put a
 *  typo in red before Send, not to be a second authority on what an address
 *  is: the server parses every entry and names the ones it refuses. */
export function looksLikeAddress(entry: string): boolean {
  return /^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/.test(bareAddress(entry))
}

/** How many people a box's text names, pending text included. */
export function recipientCount(text: string): number {
  return splitRecipients(text).length
}

/** People on a message beyond the one address a row already prints -- the
 *  "+N" beside it. Counts To and Cc (never Bcc) and does not count
 *  `primary` itself. */
export function otherRecipients(email: EmailSummary | null | undefined, primary = ''): number {
  if (!email) return 0
  const skip = primary.trim().toLowerCase()
  const seen = new Set<string>()
  for (const a of [...(email.to || []), ...(email.cc || [])]) {
    const key = (a.address || '').toLowerCase()
    if (key && key !== skip) seen.add(key)
  }
  return seen.size
}

/** Files a row should count or list -- the same cut the server's summary
 *  makes: an inline image the HTML already shows is part of the body. */
export function listedAttachments(message: Pick<MailContent, 'attachments' | 'html'>): Attachment[] {
  const hasHtml = Boolean((message.html || '').trim())
  return (message.attachments || []).filter(
    (a) => !(hasHtml && a.disposition === 'inline' && a.content_id),
  )
}

/** Whether Reply all would reach anybody Reply does not -- the button is
 *  only worth drawing when it would. */
export function replyAllAddsSomeone(message: Pick<MailContent, 'reply' | 'reply_all'>): boolean {
  const reply = new Set(
    [...message.reply.to, ...message.reply.cc].map((e) => bareAddress(e).toLowerCase()),
  )
  return [...message.reply_all.to, ...message.reply_all.cc].some(
    (e) => !reply.has(bareAddress(e).toLowerCase()),
  )
}

/** Whether an opened message has a body to draw at all. */
export function hasBody(message: Pick<MailContent, 'html' | 'text'>): boolean {
  return Boolean((message.html || '').trim() || (message.text || '').trim())
}

// ----------------------------------------------------------------- quoting

function quoteDate(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('en-US', {
    weekday: 'short',
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function bodyHtml(content: MailContent): string {
  return (content.html || '').trim() || textToHtml(content.text || '')
}

/** What a reply or a forward carries of the message it answers, as HTML for
 *  the editor.
 *
 *  A reply is an "On <date>, <from> wrote:" line over a `<blockquote>`; a
 *  forward is the header block every mail client writes, over the message
 *  itself, because a forward is passing the message on rather than
 *  answering it. The body is the server-cleaned HTML, or the text escaped
 *  when there is none -- the editor keeps only the structure it knows, and
 *  the server cleans what is sent again, so nothing here is the last word.
 */
export function quoteHtml(content: MailContent, mode: 'reply' | 'forward' = 'reply'): string {
  const from = formatAddr(content.from)
  const when = quoteDate(content.date)
  if (mode === 'forward') {
    const lines = [
      '---------- Forwarded message ----------',
      `From: ${from}`,
      when && `Date: ${when}`,
      `Subject: ${content.subject || '(no subject)'}`,
      content.to.length && `To: ${addrList(content.to)}`,
      content.cc.length && `Cc: ${addrList(content.cc)}`,
    ].filter(Boolean) as string[]
    return `<p>${lines.map(escapeHtml).join('<br>')}</p>${bodyHtml(content)}`
  }
  const said = when ? `On ${when}, ${from} wrote:` : `${from} wrote:`
  return `<p>${escapeHtml(said)}</p><blockquote>${bodyHtml(content)}</blockquote>`
}

/** `quoteHtml` under an empty first paragraph, which is where the caret
 *  belongs when a reply opens. */
export function quotedBody(content: MailContent, mode: 'reply' | 'forward' = 'reply'): string {
  return `<p></p>${quoteHtml(content, mode)}`
}
