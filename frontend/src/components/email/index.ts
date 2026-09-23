/**
 * Email, drawn: the pieces every composer and reader here is built from --
 * the buyer page, the dealership mailbox, the calendar drawer and `/ops/mail`.
 * One set, so a message is written and read the same way on every screen.
 *
 * - `RecipientInput` -- several addresses in one box as chips plus a text
 *   input. `value` is one comma-separated string holding the chips *and* the
 *   text still being typed, so Send reads what is on screen. Controlled: pass
 *   back exactly what `onChange` gave you. Its text input is the only
 *   `<input>` it renders, so it can stand first in a composer.
 * - `CopyFields` -- Cc and Bcc as two `RecipientInput`s that stay a pair of
 *   small links until opened or non-empty. Renders only the fields; place it
 *   where the gate needs it (after Subject on the ops composer).
 * - `RichEditor` -- the body, as HTML (TipTap StarterKit, exactly what the
 *   server's `clean_outbound` keeps), with a toolbar that wraps. `onChange`
 *   gives `(html, text)`; `html` is "" for an empty box. Setting `value` from
 *   outside replaces the content (a draft, a preset, a quote: plain text goes
 *   in through `textToHtml`), and the caller is then told the normalised
 *   pair. It is a `contenteditable`, never a `<textarea>`.
 * - `AttachmentPicker` -- "Attach files" and a hidden file input after it,
 *   uploads each file on pick through `api.upload`, shows refusals in red,
 *   takes dropped files. Put it below the editor. `uploadPath`/`removeBase`
 *   default to the dealership's `/api/email/attachments`; ops passes its own.
 * - `AttachmentList` -- a message's files, read-only: name, size, download
 *   link through `resolve` (`withStore` on dealer pages, identity on /ops),
 *   a thumbnail for inline images, the reason for a refused file.
 * - `EmailView` -- one message read: envelope (From, To, Cc, Bcc when
 *   `showBcc`, Reply-To when it differs, Date, High importance), the held
 *   images strip, the body (HTML in a sandboxed, self-sizing frame; text
 *   pre-wrapped) and its files. It does not print the subject: every caller
 *   already has it in a heading.
 *
 * Do not wrap `RichEditor` or `CopyFields` in `ui`'s `<Field>`, which is a
 * `<label>`: a label hands clicks on its text to the first button inside it.
 * Use `FieldGroup` from `ui` for a caption over a composite control.
 * `lib/email.ts` has the types and the text helpers (`textToHtml`,
 * `quotedBody`, `reSubject`, `otherRecipients`, ...).
 */

export { RecipientInput } from './RecipientInput'
export type { RecipientInputProps, RecipientSuggestion } from './RecipientInput'
export { CopyFields } from './CopyFields'
export type { CopyFieldsProps } from './CopyFields'
export { RichEditor } from './RichEditor'
export type { RichEditorProps } from './RichEditor'
export { AttachmentPicker } from './AttachmentPicker'
export type { AttachmentPickerProps } from './AttachmentPicker'
export { AttachmentList } from './AttachmentList'
export type { AttachmentListProps } from './AttachmentList'
export { EmailView, sanitizeEmailHtml } from './EmailView'
export type { EmailViewProps } from './EmailView'
