import type { ReactNode } from 'react'

/** A bare address, same shape as the one the HTML half of an outgoing email
 *  makes clickable (`resend._linked`) -- stops at whitespace or a tag, and
 *  trailing punctuation is given back so a link at the end of a sentence
 *  keeps the sentence's own full stop outside the href. */
const URL_RE = /https?:\/\/[^\s<]+/g

/** A chat bubble or a buyer's timeline entry is plain text, deliberately --
 *  no markdown parser, no HTML sanitiser, on the one surface a stranger
 *  types into. But a bare `https://...` the model or a rep wrote read to a
 *  buyer as a wall of characters they could not press, and with no spaces in
 *  it a long one could not wrap either, so it forced the bubble past its own
 *  edge instead of the text flowing inside it. This is the narrow exception:
 *  turn a real link into a real `<a>`, and nothing else about the text.
 *
 *  React elements, not an HTML string -- everything that is not a URL stays
 *  a plain string, which React escapes exactly as it always did, so this
 *  needs no sanitising step of its own. */
export function linked(text: string | null | undefined): ReactNode[] {
  const value = text ?? ''
  const parts: ReactNode[] = []
  let last = 0
  let key = 0
  for (const match of value.matchAll(URL_RE)) {
    const start = match.index ?? 0
    let url = match[0]
    let trail = ''
    while (url && '.,;:)!?'.includes(url[url.length - 1])) {
      trail = url[url.length - 1] + trail
      url = url.slice(0, -1)
    }
    if (start > last) parts.push(value.slice(last, start))
    parts.push(
      <a
        key={key++}
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="break-all underline underline-offset-2"
      >
        {url}
      </a>,
    )
    if (trail) parts.push(trail)
    last = start + match[0].length
  }
  if (last < value.length) parts.push(value.slice(last))
  return parts
}
