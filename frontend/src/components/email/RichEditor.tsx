import clsx from 'clsx'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { EditorContent, useEditor, useEditorState, type Editor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import { Placeholder } from '@tiptap/extensions'

/* The body of an email, with formatting.
 *
 * **What it can produce is what the server keeps.** StarterKit is paragraphs,
 * bold, italic, underline, strike, two heading levels, lists, a quote, code,
 * a rule and links -- which is `email_html.OUTBOUND_TAGS` item for item. A
 * button here for something the server strips would be a format that looks
 * right on screen and arrives plain. Links take http, https, mailto and tel,
 * the server's schemes, for the same reason.
 *
 * **Pasting from Word keeps the structure and loses the styling.** The editor
 * only knows its own nodes and marks, so a Word paste's fonts, colours and
 * `mso-` rules fall away on their own -- that is the point, since whatever
 * survived would be one more thing a buyer's mail client draws differently.
 * The one thing that falls away with them and should not is a list: Word
 * writes bullets as paragraphs with a `·` in a span, so `fromWord` turns
 * those back into lists before the editor reads them.
 *
 * **`value` is controlled, and a value from outside replaces the content.**
 * A Liner draft, a preset or a quoted reply is set by the caller and has to
 * land in the box; what the editor emitted itself must not be set back into
 * it on every keystroke, or the caret jumps to the end. The last emitted
 * string tells the two apart. After an outside value lands the caller is
 * told the normalised html and its text, so the pair it holds always agrees.
 */

export type RichEditorProps = {
  value: string
  onChange: (html: string, text: string) => void
  placeholder?: string
  /** px */
  minHeight?: number
  ariaLabel?: string
  disabled?: boolean
}

/** The schemes the server's cleaner keeps on a link. */
const LINK_SCHEME = /^(https?:|mailto:|tel:)/i
const ANY_SCHEME = /^[a-z][a-z0-9+.-]*:/i
/** A bare host, which is what autolink hands over for a typed `example.com`
 *  before it adds `https://` itself. */
const BARE_HOST = /^[\w-]+(\.[\w-]+)+([/?#:]|$)/

function allowedHref(url: string): boolean {
  const u = (url || '').trim()
  if (ANY_SCHEME.test(u)) return LINK_SCHEME.test(u)
  // No scheme: a host autolink will complete, never a path -- a relative
  // link in an email points at nothing on the reader's machine.
  return BARE_HOST.test(u)
}

/** What somebody typed into the link box, as an href the server will keep:
 *  an address becomes `mailto:`, a phone number `tel:`, anything else
 *  without a scheme is taken to be a website. */
function normaliseHref(raw: string): string {
  const text = raw.trim()
  if (!text) return ''
  if (LINK_SCHEME.test(text)) return text
  if (/^[^\s@/]+@[^\s@/]+\.[^\s@/]+$/.test(text)) return `mailto:${text}`
  if (/^\+?[\d\s().-]{6,}$/.test(text)) return `tel:${text.replace(/[^\d+]/g, '')}`
  if (/^[a-z][a-z0-9+.-]*:/i.test(text)) return ''
  return `https://${text.replace(/^\/+/, '')}`
}

// ------------------------------------------------------------- Word paste

const WORD = /urn:schemas-microsoft-com:office|class="?Mso|mso-list/i

function wordListLevel(p: Element): number | null {
  const style = p.getAttribute('style') || ''
  const found = /mso-list:\s*[^;"]*?level(\d+)/i.exec(style)
  if (found) return Number(found[1])
  return /^MsoListParagraph/i.test(p.getAttribute('class') || '') ? 1 : null
}

/** Word's list paragraphs as real lists. Each one carries its level in an
 *  `mso-list` style and its marker in a span styled `mso-list:Ignore`; the
 *  marker says whether the list is numbered, and is then removed because the
 *  list draws its own. Anything that is not Word is returned untouched. */
function fromWord(html: string): string {
  if (!WORD.test(html) || typeof DOMParser === 'undefined') return html
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const paragraphs = Array.from(doc.body.querySelectorAll('p')).filter(
    (p) => wordListLevel(p) !== null,
  )
  if (!paragraphs.length) return html

  // Consecutive list paragraphs are one list; anything between them ends it.
  const runs: Element[][] = []
  for (const p of paragraphs) {
    const run = runs[runs.length - 1]
    if (run && run[run.length - 1].nextElementSibling === p) run.push(p)
    else runs.push([p])
  }

  for (const run of runs) {
    const stack: { level: number; list: HTMLElement }[] = []
    let top: HTMLElement | null = null
    for (const p of run) {
      const level = wordListLevel(p) ?? 1
      const marker = p.querySelector('span[style*="mso-list:Ignore" i], span[style*="mso-list: Ignore" i]')
      const ordered = /^\s*[\dA-Za-z]{1,4}[.)]/.test(marker?.textContent || '')
      marker?.remove()
      const tag = ordered ? 'OL' : 'UL'

      while (stack.length && stack[stack.length - 1].level > level) stack.pop()
      const same = stack[stack.length - 1]
      if (same && same.level === level && same.list.tagName !== tag) stack.pop()
      if (!stack.length || stack[stack.length - 1].level < level) {
        const list = doc.createElement(tag)
        const parent = stack[stack.length - 1]
        if (parent) {
          const li = (parent.list.lastElementChild as HTMLElement | null) ?? parent.list.appendChild(doc.createElement('li'))
          li.appendChild(list)
        } else if (top) {
          top.after(list)
        } else {
          p.before(list)
        }
        if (!parent) top = list
        stack.push({ level, list })
      }
      const li = doc.createElement('li')
      stack[stack.length - 1].list.appendChild(li)
      li.appendChild(p)
    }
  }
  return doc.body.innerHTML
}

// ------------------------------------------------------------------ editor

function snapshot(editor: Editor): { html: string; text: string } {
  return {
    // An editor with nothing in it still says `<p></p>`; "" is what a caller
    // deciding whether Send can be pressed needs to see.
    html: editor.isEmpty ? '' : editor.getHTML(),
    text: editor.getText({ blockSeparator: '\n\n' }),
  }
}

export function RichEditor({
  value,
  onChange,
  placeholder,
  minHeight = 160,
  ariaLabel = 'Message',
  disabled = false,
}: RichEditorProps) {
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange
  const placeholderRef = useRef(placeholder ?? '')
  placeholderRef.current = placeholder ?? ''
  const emitted = useRef<string | null>(null)
  const [linkOpen, setLinkOpen] = useState(false)

  const emit = (ed: Editor) => {
    const { html, text } = snapshot(ed)
    emitted.current = html
    onChangeRef.current(html, text)
  }

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [2, 3] },
        link: {
          openOnClick: false,
          autolink: true,
          linkOnPaste: true,
          defaultProtocol: 'https',
          // http, https and mailto are linkify's own; only tel needs adding.
          protocols: ['tel'],
          isAllowedUri: (url) => allowedHref(url),
          HTMLAttributes: { rel: 'noopener noreferrer', target: '_blank' },
        },
      }),
      Placeholder.configure({ placeholder: () => placeholderRef.current }),
    ],
    content: value || '',
    editable: !disabled,
    editorProps: {
      attributes: {
        role: 'textbox',
        'aria-multiline': 'true',
        'aria-label': ariaLabel,
        spellcheck: 'true',
        style: `min-height:${minHeight}px`,
        class: CONTENT_CLASS,
      },
      transformPastedHTML: fromWord,
      handleKeyDown: (_view, event) => {
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
          event.preventDefault()
          setLinkOpen(true)
          return true
        }
        return false
      },
      // A file dropped on the text would otherwise make the browser open it
      // in place of the page, taking the unsent message with it. Files go
      // through the attachment picker below.
      handleDrop: (_view, event) => Boolean((event as DragEvent).dataTransfer?.files?.length),
    },
    onUpdate: ({ editor: ed }) => emit(ed),
  })

  // A value from outside: a draft, a preset, a quote, or "" after a send.
  // `emit` reads refs only, so `value` is the one thing this reacts to. An
  // empty box is not reported -- a caller treating any onChange as "the rep
  // has started writing" would otherwise ask before closing an empty one.
  useEffect(() => {
    if (!editor || editor.isDestroyed) return
    if (value === emitted.current) return
    if (value !== snapshot(editor).html) {
      editor.commands.setContent(value || '', { emitUpdate: false })
    }
    if (snapshot(editor).html) emit(editor)
    else emitted.current = ''
  }, [editor, value])

  useEffect(() => {
    if (editor && !editor.isDestroyed) editor.setEditable(!disabled, false)
  }, [editor, disabled])

  useEffect(() => {
    if (!editor || editor.isDestroyed) return
    editor.setOptions({
      editorProps: {
        ...editor.options.editorProps,
        attributes: {
          role: 'textbox',
          'aria-multiline': 'true',
          'aria-label': ariaLabel,
          spellcheck: 'true',
          style: `min-height:${minHeight}px`,
          class: CONTENT_CLASS,
        },
      },
    })
  }, [editor, ariaLabel, minHeight])

  return (
    <div
      // A caller that wraps this in a <label> would otherwise have every
      // click on the text forwarded to the toolbar's first button.
      onClick={(event) => {
        if (event.currentTarget.closest('label')) event.preventDefault()
      }}
      className={clsx(
        'min-w-0 overflow-hidden rounded-md border border-input bg-background shadow-xs',
        'focus-within:ring-2 focus-within:ring-ring',
        disabled && 'opacity-60',
      )}
    >
      <Toolbar
        editor={editor}
        disabled={disabled}
        linkOpen={linkOpen}
        setLinkOpen={setLinkOpen}
      />
      <EditorContent editor={editor} />
    </div>
  )
}

/* Tailwind's preflight takes the bullets off lists and the size off
 * headings, so the editor's own content puts them back. Colours are tokens,
 * as everywhere else; the placeholder is Placeholder's `data-placeholder`
 * drawn by a pseudo-element. */
const CONTENT_CLASS = [
  'min-w-0 px-3 py-2 text-sm leading-relaxed break-words focus:outline-none',
  '[&>*+*]:mt-3',
  '[&_ul]:list-disc [&_ul]:pl-6 [&_ol]:list-decimal [&_ol]:pl-6 [&_li>p]:my-0 [&_li+li]:mt-1',
  '[&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground',
  '[&_h2]:text-base [&_h2]:font-semibold [&_h3]:text-sm [&_h3]:font-semibold',
  '[&_a]:text-primary [&_a]:underline',
  '[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:font-mono [&_code]:text-[0.85em]',
  '[&_pre]:whitespace-pre-wrap [&_pre]:rounded-md [&_pre]:bg-muted [&_pre]:p-3 [&_pre_code]:bg-transparent [&_pre_code]:p-0',
  '[&_hr]:border-border',
  '[&_p.is-editor-empty:first-child]:before:pointer-events-none [&_p.is-editor-empty:first-child]:before:float-left [&_p.is-editor-empty:first-child]:before:h-0',
  '[&_p.is-editor-empty:first-child]:before:text-muted-foreground [&_p.is-editor-empty:first-child]:before:content-[attr(data-placeholder)]',
].join(' ')

// ------------------------------------------------------------------ toolbar

type ToolState = {
  bold: boolean
  italic: boolean
  underline: boolean
  strike: boolean
  h2: boolean
  h3: boolean
  bullet: boolean
  ordered: boolean
  quote: boolean
  code: boolean
  link: boolean
  canUndo: boolean
  canRedo: boolean
}

function Toolbar({
  editor,
  disabled,
  linkOpen,
  setLinkOpen,
}: {
  editor: Editor
  disabled: boolean
  linkOpen: boolean
  setLinkOpen: (open: boolean) => void
}) {
  // Toolbar state is read through `useEditorState`: TipTap 3 no longer
  // re-renders the component on every transaction, so a toolbar reading
  // `editor.isActive` directly would show Bold as off inside bold text.
  const state = useEditorState<ToolState>({
    editor,
    selector: ({ editor: e }) => ({
      bold: e.isActive('bold'),
      italic: e.isActive('italic'),
      underline: e.isActive('underline'),
      strike: e.isActive('strike'),
      h2: e.isActive('heading', { level: 2 }),
      h3: e.isActive('heading', { level: 3 }),
      bullet: e.isActive('bulletList'),
      ordered: e.isActive('orderedList'),
      quote: e.isActive('blockquote'),
      code: e.isActive('code'),
      link: e.isActive('link'),
      canUndo: e.can().undo(),
      canRedo: e.can().redo(),
    }),
  })

  const run = () => editor.chain().focus()

  return (
    <div className="border-b border-border">
      <div
        role="toolbar"
        aria-label="Formatting"
        className="flex min-w-0 flex-wrap items-center gap-0.5 px-1.5 py-1"
      >
        <Tool label="Bold" keys="Ctrl+B" active={state.bold} disabled={disabled} onClick={() => run().toggleBold().run()}>
          <span className="font-bold">B</span>
        </Tool>
        <Tool label="Italic" keys="Ctrl+I" active={state.italic} disabled={disabled} onClick={() => run().toggleItalic().run()}>
          <span className="font-serif italic">I</span>
        </Tool>
        <Tool label="Underline" keys="Ctrl+U" active={state.underline} disabled={disabled} onClick={() => run().toggleUnderline().run()}>
          <span className="underline underline-offset-2">U</span>
        </Tool>
        <Tool label="Strikethrough" active={state.strike} disabled={disabled} onClick={() => run().toggleStrike().run()}>
          <span className="line-through">S</span>
        </Tool>
        <Divider />
        <Tool label="Heading" active={state.h2} disabled={disabled} onClick={() => run().toggleHeading({ level: 2 }).run()}>
          H2
        </Tool>
        <Tool label="Subheading" active={state.h3} disabled={disabled} onClick={() => run().toggleHeading({ level: 3 }).run()}>
          H3
        </Tool>
        <Divider />
        <Tool label="Bulleted list" active={state.bullet} disabled={disabled} onClick={() => run().toggleBulletList().run()}>
          <ListGlyph ordered={false} />
        </Tool>
        <Tool label="Numbered list" active={state.ordered} disabled={disabled} onClick={() => run().toggleOrderedList().run()}>
          <ListGlyph ordered />
        </Tool>
        <Tool label="Quote" active={state.quote} disabled={disabled} onClick={() => run().toggleBlockquote().run()}>
          <span className="font-serif text-base leading-none">&ldquo;</span>
        </Tool>
        <Tool label="Code" active={state.code} disabled={disabled} onClick={() => run().toggleCode().run()}>
          <span className="font-mono">{'</>'}</span>
        </Tool>
        <Tool label="Horizontal line" disabled={disabled} onClick={() => run().setHorizontalRule().run()}>
          <span aria-hidden="true">&mdash;</span>
        </Tool>
        <Tool label="Link" keys="Ctrl+K" active={state.link || linkOpen} disabled={disabled} onClick={() => setLinkOpen(!linkOpen)}>
          <span className="underline underline-offset-2">Link</span>
        </Tool>
        <Divider />
        <Tool label="Undo" keys="Ctrl+Z" disabled={disabled || !state.canUndo} onClick={() => run().undo().run()}>
          <span aria-hidden="true">&#8630;</span>
        </Tool>
        <Tool label="Redo" keys="Ctrl+Shift+Z" disabled={disabled || !state.canRedo} onClick={() => run().redo().run()}>
          <span aria-hidden="true">&#8631;</span>
        </Tool>
      </div>
      {linkOpen && !disabled && (
        <LinkRow editor={editor} onDone={() => setLinkOpen(false)} />
      )}
    </div>
  )
}

function Tool({
  label,
  keys,
  active,
  disabled,
  onClick,
  children,
}: {
  label: string
  keys?: string
  active?: boolean
  disabled?: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={keys ? `${label} (${keys})` : label}
      aria-pressed={active === undefined ? undefined : active}
      disabled={disabled}
      // Keep the selection in the editor: a button that takes focus on
      // mouse down leaves nothing selected for Bold to apply to.
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
      className={clsx(
        'inline-flex h-7 min-w-7 items-center justify-center rounded px-1.5 text-xs text-muted-foreground transition-colors duration-150',
        'hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        'disabled:pointer-events-none disabled:opacity-40',
        active && 'bg-accent text-foreground',
      )}
    >
      {children}
    </button>
  )
}

function Divider() {
  return <span aria-hidden="true" className="mx-0.5 h-4 w-px bg-border" />
}

function ListGlyph({ ordered }: { ordered: boolean }) {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
      <path d="M9 6h12M9 12h12M9 18h12" />
      {ordered ? (
        <path d="M4 4v4M3 18h2.5l-2.5 2.5V21h3M3 11h3l-3 3h3" strokeWidth={1.5} />
      ) : (
        <>
          <circle cx="4.5" cy="6" r="1" fill="currentColor" />
          <circle cx="4.5" cy="12" r="1" fill="currentColor" />
          <circle cx="4.5" cy="18" r="1" fill="currentColor" />
        </>
      )}
    </svg>
  )
}

/** The address a link points at, typed under the toolbar rather than into a
 *  browser prompt -- a prompt cannot say what went wrong with what was typed. */
function LinkRow({ editor, onDone }: { editor: Editor; onDone: () => void }) {
  const current = (editor.getAttributes('link').href as string | undefined) ?? ''
  const [href, setHref] = useState(current)
  const [problem, setProblem] = useState('')

  const apply = () => {
    const next = normaliseHref(href)
    if (!href.trim()) {
      editor.chain().focus().extendMarkRange('link').unsetLink().run()
      onDone()
      return
    }
    if (!next || !allowedHref(next)) {
      setProblem('A link has to be a web address, an email address or a phone number.')
      return
    }
    const { empty } = editor.state.selection
    if (empty && !editor.isActive('link')) {
      // Nothing selected to turn into a link: the address becomes the text.
      editor
        .chain()
        .focus()
        .insertContent({ type: 'text', text: href.trim(), marks: [{ type: 'link', attrs: { href: next } }] })
        .run()
    } else {
      editor.chain().focus().extendMarkRange('link').setLink({ href: next }).run()
    }
    onDone()
  }

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2 border-t border-border px-2 py-1.5">
      <input
        type="text"
        inputMode="url"
        autoFocus
        aria-label="Link address"
        placeholder="https://..., name@example.com or a phone number"
        value={href}
        onChange={(event) => {
          setHref(event.target.value)
          setProblem('')
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault()
            apply()
          } else if (event.key === 'Escape') {
            event.preventDefault()
            event.stopPropagation()
            editor.commands.focus()
            onDone()
          }
        }}
        className="h-8 min-w-[12rem] flex-1 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />
      <button
        type="button"
        onClick={apply}
        className="h-8 rounded-md border border-border bg-background px-3 text-xs font-medium hover:bg-accent"
      >
        {current ? 'Update link' : 'Add link'}
      </button>
      {current && (
        <button
          type="button"
          onClick={() => {
            editor.chain().focus().extendMarkRange('link').unsetLink().run()
            onDone()
          }}
          className="h-8 rounded-md px-2 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-accent-foreground"
        >
          Remove link
        </button>
      )}
      {problem && <p className="w-full text-xs text-destructive">{problem}</p>}
    </div>
  )
}
