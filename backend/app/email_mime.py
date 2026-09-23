"""A received message, read from its own bytes.

The Worker used to parse mail at the edge and post a JSON digest of it, and
the digest was the problem: it carried one address for the sender, none for
Cc, a list of file *names* with no files behind them, and it cost the Worker
CPU it does not have on the free plan. Now it posts the raw message and this
module reads it -- with the standard library's own parser under
`policy.default`, which decodes encoded names and filenames, understands
multipart structure and never needs a dependency.

**Nothing here raises on a bad message.** A malformed header, an unknown
charset, a truncated base64 part or a nesting depth somebody built to hurt a
parser each cost what they affect and nothing more: the message is still filed
with whatever could be read, because a buyer's email that the system refused
to read is indistinguishable from one they never sent.

**Pure functions over bytes.** No database and no disk, so the whole of it can
be driven from a scratch script against a message built in memory -- and the
placement that stores what it returns is the only thing that decides where
anything goes.
"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import unquote

from app import email_addresses, email_html
from app.email_addresses import Recipient

#: How many files one message may leave behind. A real message carries a
#: handful; one carrying thousands of tiny parts is somebody testing what a
#: parser does with it, and the answer is to stop counting.
MAX_PARTS = 200

#: How deep multiparts may nest before the rest is left unread. Real mail
#: rarely goes past four (mixed > alternative > related > html); Python's own
#: parser recurses, so a message nested a thousand deep is a stack overflow
#: dressed as an email.
MAX_DEPTH = 24

_MSGID = re.compile(r"<[^<>\s]+>")
_CID_REF = re.compile(r"""cid:([^"'\s>)]+)""", re.IGNORECASE)
_WS = re.compile(r"\s+")


@dataclass
class ParsedEmail:
    """What a message said, as far as it could be read.

    `from_` is the **header** From -- the person -- and never the envelope
    sender, which is a bounce address or an SRS rewrite as often as not.
    `headers` carries only the loop-breaking headers `automated_reason` reads,
    keys lowercased. `parts` are the files and inline images, each a dict of
    `filename, content_type, data, content_id, disposition`.
    """

    message_id: str = ""
    in_reply_to: str = ""
    references: str = ""
    subject: str = ""
    from_: Recipient | None = None
    to: list[Recipient] = field(default_factory=list)
    cc: list[Recipient] = field(default_factory=list)
    reply_to: list[Recipient] = field(default_factory=list)
    dated_at: datetime | None = None
    text: str = ""
    html: str = ""
    importance: str = "normal"
    headers: dict[str, str] = field(default_factory=dict)
    parts: list[dict] = field(default_factory=list)
    size: int = 0
    #: What could not be read, in words, for the receipt. Empty on a clean
    #: message.
    problems: list[str] = field(default_factory=list)

    @property
    def sender(self) -> Recipient | None:
        return self.from_

    def from_header(self) -> str:
        """The sender as the receipt stores it: `Name <address>`, or bare."""
        return display(self.from_) if self.from_ is not None else ""


#: What forces a display name into quotes (RFC 5322 `specials`).
_SPECIALS = re.compile(r'[()<>\[\]:;@\\,."]')


def display(recipient: Recipient) -> str:
    """`Name <address>` for a person to read, and for `parseaddr` to read back.

    Not `formataddr`: that RFC 2047-encodes a non-ASCII name, which is right
    for a header on the wire and wrong for a column a rep reads -- `Jörg`
    stored as `=?utf-8?b?SsO2cmc=?=` becomes the buyer's name the moment
    `display_name` copies it onto the lead. A name needing quotes gets them,
    with its own quotes and backslashes escaped, so a comma in it is never
    read as two people.
    """
    name = re.sub(r"[\x00-\x1f\x7f]", " ", recipient.name or "").strip()
    if not name or name.lower() == recipient.address.lower():
        return recipient.address
    if _SPECIALS.search(name):
        name = '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"{name} <{recipient.address}>"


# ------------------------------------------------------------------ headers


def _header_block(raw: bytes) -> bytes:
    """The bytes before the first blank line: every header, and no body.

    Reading the headers of a thirty-megabyte message should not cost reading
    thirty megabytes, and the intake needs only the headers to decide whether
    it has seen a message before.
    """
    for sep in (b"\r\n\r\n", b"\n\n"):
        at = raw.find(sep)
        if at != -1:
            return raw[: at + len(sep)]
    return raw


def _raw_values(msg: Message, name: str) -> list[str]:
    """Every value of one header as it arrived, unfolded, before decoding.

    Read from the stored source rather than the parsed header object, because
    the parsed one is already decoded -- and a decoded display name with a
    comma in it is two recipients to anything that parses it again.
    """
    wanted = name.lower()
    out = []
    try:
        items = msg.raw_items()
    except Exception:  # noqa: BLE001 -- a header block that cannot be walked has nothing to give
        return []
    for key, value in items:
        if str(key).lower() != wanted:
            continue
        text = value if isinstance(value, str) else str(value)
        out.append(re.sub(r"\r?\n[ \t]+", " ", text).strip())
    return [v for v in out if v]


def _decoded(msg: Message, name: str) -> str:
    """One header, decoded (`=?utf-8?...?=` becomes words), on one line."""
    try:
        value = msg.get(name)
    except Exception:  # noqa: BLE001 -- a header the registry cannot parse
        values = _raw_values(msg, name)
        value = values[0] if values else ""
    return _WS.sub(" ", str(value or "")).strip()


def _addresses(msg: Message, name: str) -> list[Recipient]:
    """A To, Cc, Reply-To or From header as recipients, however many times
    it was repeated. Lenient, because this is somebody else's mail and not a
    form -- see `email_addresses.from_header_value`."""
    values = _raw_values(msg, name)
    if not values:
        return []
    try:
        return email_addresses.from_header_value(", ".join(values))
    except Exception:  # noqa: BLE001
        return []


def _sender(msg: Message) -> Recipient | None:
    found = _addresses(msg, "from")
    if found:
        return found[0]
    # A From the registry refused (an unquoted comma in the name, a missing
    # bracket) still usually has an address in it somewhere.
    values = _raw_values(msg, "from")
    name, address = parseaddr(values[0] if values else "")
    if address and "@" in address:
        return Recipient(name=(name or "").strip(), address=address.strip())
    return None


def message_ids(value: str) -> list[str]:
    """`<a@b> <c@d>` as `["<a@b>", "<c@d>"]`, in the order written.

    A bare id with no brackets -- which some clients write -- is kept as a
    single entry rather than dropped: it is still what the sender meant.
    """
    found = _MSGID.findall(value or "")
    if found:
        return found
    bare = (value or "").strip()
    return [bare] if bare and " " not in bare else []


def _date(msg: Message) -> datetime | None:
    """The `Date:` header as naive UTC, or None when it cannot be read."""
    values = _raw_values(msg, "date")
    if not values:
        return None
    try:
        when = parsedate_to_datetime(values[0])
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when is None:
        return None
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    return when


def _importance(msg: Message) -> str:
    """high or normal. `Importance` is the standard one (RFC 2156); Outlook
    also reads `X-Priority` (1 and 2 are high), `X-MSMail-Priority` and
    `Priority: urgent`. Low is not a separate state here: nothing on the
    dashboard would treat it differently from normal."""
    importance = (_raw_values(msg, "importance") or [""])[0].lower()
    if importance.startswith("high"):
        return "high"
    priority = (_raw_values(msg, "x-priority") or [""])[0].strip()
    if priority[:1] in ("1", "2"):
        return "high"
    ms = (_raw_values(msg, "x-msmail-priority") or [""])[0].lower()
    if ms.startswith("high"):
        return "high"
    if (_raw_values(msg, "priority") or [""])[0].lower().startswith("urgent"):
        return "high"
    return "normal"


def _loop_headers(msg: Message) -> dict[str, str]:
    # Imported here: `email_intake` is the module that owns the list, and
    # asking it rather than copying it is what keeps the two from drifting --
    # a header the backend checks and the parser never collects is a loop
    # breaker that silently does nothing.
    from app.email_intake import AUTOMATED_HEADERS

    out: dict[str, str] = {}
    for name in AUTOMATED_HEADERS:
        values = _raw_values(msg, name)
        if values:
            out[name] = values[0]
    return out


def _fill_headers(parsed: ParsedEmail, msg: Message) -> None:
    ids = message_ids(_decoded(msg, "message-id"))
    parsed.message_id = ids[0][:255] if ids else ""
    replying = message_ids(" ".join(_raw_values(msg, "in-reply-to")))
    parsed.in_reply_to = replying[0][:255] if replying else ""
    parsed.references = " ".join(message_ids(" ".join(_raw_values(msg, "references"))))
    parsed.subject = _decoded(msg, "subject")
    parsed.from_ = _sender(msg)
    parsed.to = _addresses(msg, "to")
    parsed.cc = _addresses(msg, "cc")
    parsed.reply_to = _addresses(msg, "reply-to")
    parsed.dated_at = _date(msg)
    parsed.importance = _importance(msg)
    parsed.headers = _loop_headers(msg)


def parse_headers(raw: bytes) -> ParsedEmail:
    """Only what the headers say: enough to dedupe, route and name a receipt.

    The intake calls this before it answers, and `parse` after, in the
    background -- a thirty-megabyte attachment is read once, off the request's
    clock.
    """
    parsed = ParsedEmail(size=len(raw or b""))
    try:
        msg = BytesParser(policy=policy.default).parsebytes(_header_block(raw or b""), headersonly=True)
        _fill_headers(parsed, msg)
    except Exception as exc:  # noqa: BLE001 -- never let a header block lose the message
        parsed.problems.append(f"The headers could not be read: {exc}"[:200])
    return parsed


# -------------------------------------------------------------------- body


def _charset(part: Message) -> str:
    try:
        return part.get_content_charset() or "utf-8"
    except Exception:  # noqa: BLE001
        return "utf-8"


def _text_of(part: Message) -> str:
    """A text part's words, whatever its charset claims to be.

    `get_content()` first, because it knows every encoding Python does. A
    charset it has never heard of (`x-unknown`, a typo) or bytes that are not
    what the charset says fall back to the raw payload decoded leniently: some
    replaced characters are far better than a message read as empty.
    """
    try:
        content = part.get_content()
        if isinstance(content, bytes):
            return content.decode(_charset(part), errors="replace")
        return str(content)
    except Exception:  # noqa: BLE001
        pass
    try:
        payload = part.get_payload(decode=True) or b""
    except Exception:  # noqa: BLE001
        payload = b""
    if isinstance(payload, str):
        return payload
    try:
        return payload.decode(_charset(part), errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _bytes_of(part: Message) -> bytes:
    try:
        data = part.get_payload(decode=True)
    except Exception:  # noqa: BLE001
        data = None
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        return data.encode("utf-8", errors="replace")
    return b""


def _nested_bytes(part: Message) -> bytes:
    """A forwarded message, as the `.eml` a rep can open.

    Re-serialised from the parsed copy, because the parser keeps no pointer
    into the original bytes. Close enough to open in any mail client, and
    the untouched original is on disk regardless.
    """
    try:
        payload = part.get_payload()
        inner = payload[0] if isinstance(payload, list) and payload else payload
        if isinstance(inner, Message):
            try:
                return inner.as_bytes(policy=policy.default.clone(linesep="\r\n"))
            except Exception:  # noqa: BLE001 -- a header the generator will not refold
                return inner.as_string(policy=policy.compat32).encode("utf-8", errors="replace")
        if isinstance(inner, (bytes, str)):
            return inner if isinstance(inner, bytes) else inner.encode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    return _bytes_of(part)


def _filename(part: Message) -> str:
    try:
        return (part.get_filename() or "").strip()
    except Exception:  # noqa: BLE001 -- an undecodable RFC 2231 name
        return ""


def _disposition(part: Message) -> str:
    try:
        return (part.get_content_disposition() or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def _content_id(part: Message) -> str:
    values = _raw_values(part, "content-id")
    return values[0].strip().strip("<>").strip() if values else ""


def _named(content_type: str, index: int, stem: str = "attachment") -> str:
    """A name for a part that arrived without one."""
    ext = mimetypes.guess_extension(content_type or "") or ""
    if ext in ("", ".bin", ".ksh"):
        ext = {"text/calendar": ".ics", "message/rfc822": ".eml"}.get(content_type, ".bin")
    return f"{stem}-{index}{ext}"


def parse(raw: bytes) -> ParsedEmail:
    """Everything a received message carries, as far as it can be read.

    The body is the message's own text/plain where it has one, and otherwise
    words written from its HTML -- `email_html.text_from_html`, which keeps a
    list's bullets and a link's URL. HTML is kept exactly as it arrived; it is
    cleaned when somebody opens it, never when it is filed.

    Every other leaf is a part: files, inline images, calendar invites and
    signatures alike. A forwarded message (`message/rfc822`) is kept whole as
    an `.eml` rather than opened up, because its own attachments belong to it
    and flattening them into this message would say this sender sent them.
    """
    raw = raw or b""
    parsed = parse_headers(raw)
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception as exc:  # noqa: BLE001
        parsed.problems.append(f"The message could not be parsed: {exc}"[:200])
        parsed.text = raw.decode("utf-8", errors="replace")
        return parsed

    try:
        body_plain = msg.get_body(preferencelist=("plain",))
    except Exception:  # noqa: BLE001
        body_plain = None
    try:
        body_html = msg.get_body(preferencelist=("html",))
    except Exception:  # noqa: BLE001
        body_html = None
    # `get_body` treats a text/html attachment as a candidate only when it is
    # inline, but a part named `invoice.html` is a file whatever it says.
    if body_html is not None and body_html.get_content_type() != "text/html":
        body_html = None
    if body_plain is not None and body_plain.get_content_type() != "text/plain":
        body_plain = None

    texts: list[str] = [_text_of(body_plain)] if body_plain is not None else []
    htmls: list[str] = [_text_of(body_html)] if body_html is not None else []
    leaves: list[Message] = []
    truncated = False

    def walk(part: Message, depth: int, in_alternative: bool) -> None:
        nonlocal truncated
        if depth > MAX_DEPTH or len(leaves) >= MAX_PARTS:
            truncated = True
            return
        ctype = part.get_content_type()
        if ctype in ("message/rfc822", "message/global"):
            leaves.append(part)
            return
        if part.is_multipart():
            try:
                children = list(part.iter_parts())
            except Exception:  # noqa: BLE001
                children = []
            for child in children:
                walk(child, depth + 1, in_alternative or ctype == "multipart/alternative")
            return
        if part is body_plain or part is body_html:
            return
        if (
            ctype in ("text/plain", "text/html")
            and not _filename(part)
            and _disposition(part) != "attachment"
        ):
            # A second rendering of the body inside an alternative is the same
            # words again; outside one it is more of the message -- Apple Mail
            # writes text, a picture, then more text, as siblings.
            if not in_alternative:
                (texts if ctype == "text/plain" else htmls).append(_text_of(part))
            return
        leaves.append(part)

    try:
        walk(msg, 0, False)
    except RecursionError:
        truncated = True
    if truncated:
        parsed.problems.append(
            f"Only the first {MAX_PARTS} parts were read; the rest of the message was left on disk."
        )

    # One line ending, whatever the sender's client used: the body is read by
    # regexes (`just_the_reply`, the signature reader) written for "\n".
    parsed.html = "\n".join(h for h in htmls if h.strip())
    parsed.text = "\n\n".join(
        t.replace("\r\n", "\n").replace("\r", "\n").strip() for t in texts if t.strip()
    )
    if not parsed.text.strip() and parsed.html:
        try:
            parsed.text = email_html.text_from_html(parsed.html)
        except Exception:  # noqa: BLE001
            parsed.text = ""

    referenced = {
        unquote(m).strip("<>").lower() for m in _CID_REF.findall(parsed.html or "")
    }
    for index, part in enumerate(leaves, start=1):
        ctype = part.get_content_type()
        cid = _content_id(part)
        if ctype in ("message/rfc822", "message/global"):
            data = _nested_bytes(part)
            name = _filename(part)
            if not name:
                try:
                    inner = part.get_payload()[0]
                    subject = _WS.sub(" ", str(inner.get("subject") or "")).strip()
                except Exception:  # noqa: BLE001
                    subject = ""
                name = f"{subject[:120] or 'forwarded message'}.eml"
            ctype = "message/rfc822"
        else:
            data = _bytes_of(part)
            name = _filename(part) or _named(
                ctype, index, "image" if ctype.startswith("image/") else "attachment"
            )
        # Inline only when the HTML actually draws it. An image with a
        # Content-ID that nothing refers to is a file the reader has to list,
        # or it is on the message and visible nowhere.
        if cid and cid.lower() in referenced:
            disposition = "inline"
        elif cid:
            disposition = "attachment"
        else:
            disposition = "inline" if _disposition(part) == "inline" else "attachment"
        parsed.parts.append({
            "filename": name,
            "content_type": ctype,
            "data": data,
            "content_id": cid[:255],
            "disposition": disposition,
        })
    return parsed
