"""HTML in email, in both directions, cleaned to an allowlist.

Two different jobs share this module because they share the tool, not the
policy:

* **Outbound** (`clean_outbound`): what a rep formatted in the composer. The
  editor can only produce a dozen tags, so the allowlist is that dozen and no
  more -- a `<script>` pasted into the box, an `onclick`, a `javascript:`
  link, a `<style>` block from a Word paste all go. What is left is given the
  inline styles mail clients actually honour, because `<style>` elements are
  only partly supported in Gmail and Outlook.
* **Inbound** (`clean_inbound`): somebody else's newsletter-shaped HTML, drawn
  in a rep's browser. A much wider allowlist (tables, fonts, inline styles)
  because that is what real mail is built from, but no scripts, no forms, no
  CSS `url()` beacons, and no remote images unless the rep asks for them --
  a remote image is how a sender learns that an address is live and who
  opened it. `cid:` images the message carries itself become `data:` URIs.

Both go through `nh3`, the Rust `ammonia` sanitiser. The browser cleans the
inbound HTML a second time with DOMPurify and draws it in a sandboxed frame
with its own Content-Security-Policy; this is the first of those three walls,
not the only one.

**The stored HTML is the original.** Inbound mail is cleaned when it is read,
never when it is filed, so a stricter allowlist tomorrow applies to mail that
arrived today.
"""

from __future__ import annotations

import html as _html
import re
from typing import Callable

import nh3
from selectolax.lexbor import LexborHTMLParser

# ---------------------------------------------------------------- outbound

#: What the composer's editor can produce, and nothing else.
OUTBOUND_TAGS = {
    "p", "br", "strong", "b", "em", "i", "u", "s", "a",
    "ul", "ol", "li", "blockquote", "h2", "h3", "code", "pre", "hr",
}

#: The styles mail clients honour, set on every tag rather than trusted from
#: the input. Inline because a `<style>` element is dropped by half the
#: clients a buyer uses.
_OUTBOUND_STYLE = {
    "p": {"style": "margin:0 0 12px 0"},
    "blockquote": {"style": "margin:0 0 12px 0;padding:0 0 0 12px;border-left:3px solid #d0d0d0;color:#555"},
    "ul": {"style": "margin:0 0 12px 0;padding-left:24px"},
    "ol": {"style": "margin:0 0 12px 0;padding-left:24px"},
    "h2": {"style": "margin:0 0 12px 0;font-size:18px"},
    "h3": {"style": "margin:0 0 12px 0;font-size:16px"},
    "pre": {"style": "margin:0 0 12px 0;white-space:pre-wrap;font-family:monospace"},
    "a": {"target": "_blank"},
}


def clean_outbound(html: str) -> str:
    """A rep's formatted message, safe and ready to send.

    TipTap writes a list item as `<li><p>text</p></li>`; left alone, every
    item picks up a paragraph's bottom margin in the recipient's client. The
    paragraphs inside an item are unwrapped first, before cleaning, because
    changing markup *after* a sanitiser has run is how a sanitiser is undone.
    `h1` becomes `h2`: a heading the size of a page title reads as shouting
    in an email.
    """
    text = html or ""
    text = re.sub(r"<li>\s*<p>", "<li>", text, flags=re.I)
    text = re.sub(r"</p>\s*</li>", "</li>", text, flags=re.I)
    text = re.sub(r"</p>\s*<p>(?=(?:(?!</?li).)*</li>)", "<br>", text, flags=re.I | re.S)
    text = re.sub(r"<(/?)h1\b", r"<\1h2", text, flags=re.I)
    text = re.sub(r"<(/?)h[4-6]\b", r"<\1h3", text, flags=re.I)
    cleaned = nh3.clean(
        text,
        tags=OUTBOUND_TAGS,
        attributes={"a": {"href"}},
        url_schemes={"http", "https", "mailto", "tel"},
        link_rel="noopener noreferrer",
        set_tag_attribute_values=_OUTBOUND_STYLE,
    )
    # An editor with nothing typed in it still hands back `<p></p>`, and an
    # Enter at the end leaves one behind the last line.
    cleaned = re.sub(r'<p[^>]*>\s*</p>', "", cleaned).strip()
    return "" if not text_from_html(cleaned).strip() else cleaned


def text_to_html(text: str) -> str:
    """Plain text as HTML paragraphs, escaped.

    Blank lines separate paragraphs and single newlines become `<br>`: a
    signature is a stack of short lines and has to keep looking like one.
    """
    blocks = re.split(r"\n\s*\n", (text or "").strip())
    return "".join(
        '<p style="margin:0 0 12px 0">'
        + _html.escape(block).replace("\n", "<br>")
        + "</p>"
        for block in blocks
        if block.strip()
    )


# ------------------------------------------------------------ html -> text

_BLOCK = {"p", "div", "tr", "table", "section", "article", "header", "footer",
          "h1", "h2", "h3", "h4", "h5", "h6", "pre", "hr", "center"}


def text_from_html(html: str) -> str:
    """The text/plain half of a message, written from its HTML.

    Our own, not a library's: the input is either our composer's dozen tags
    or somebody's mail being previewed, and what matters in both is that a
    list keeps its bullets and numbers, a quote keeps its `>`, and a link
    keeps its URL -- `selectolax`'s own `.text()` loses all three. `html2text`
    would do it and is GPL, which this codebase does not take on for a walker
    of about forty lines.
    """
    if not (html or "").strip():
        return ""
    tree = LexborHTMLParser(html)
    root = tree.body or tree.root
    if root is None:
        return ""
    lines: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        line = "".join(buffer).strip()
        buffer.clear()
        if line:
            lines.append(line)

    def walk(node, quote: int, lists: list) -> None:  # noqa: ANN001
        child = node.child
        while child is not None:
            tag = (child.tag or "").lower()
            if tag == "-text":
                buffer.append(re.sub(r"\s+", " ", child.text_content or ""))
            elif tag in ("script", "style", "head", "title"):
                pass
            elif tag == "br":
                flush()
            elif tag in ("ul", "ol"):
                flush()
                walk(child, quote, lists + [[tag, 0]])
                flush()
            elif tag == "li":
                flush()
                marker = "- "
                if lists and lists[-1][0] == "ol":
                    lists[-1][1] += 1
                    marker = f"{lists[-1][1]}. "
                buffer.append(("  " * max(len(lists) - 1, 0)) + marker)
                walk(child, quote, lists)
                flush()
            elif tag == "blockquote":
                flush()
                start = len(lines)
                walk(child, quote + 1, lists)
                flush()
                while len(lines) > start and not lines[-1].strip():
                    lines.pop()
                for i in range(start, len(lines)):
                    lines[i] = ("> " + lines[i]) if lines[i].strip() else ">"
                lines.append("")
            elif tag == "a":
                inner_start = len(buffer)
                walk(child, quote, lists)
                label = "".join(buffer[inner_start:]).strip()
                href = (child.attributes.get("href") or "").strip()
                if href and not href.startswith("mailto:") and href != label:
                    buffer.append(f" ({href})")
            elif tag == "img":
                alt = (child.attributes.get("alt") or "").strip()
                if alt:
                    buffer.append(alt)
            elif tag in ("td", "th"):
                walk(child, quote, lists)
                buffer.append(" ")
            elif tag in _BLOCK:
                flush()
                walk(child, quote, lists)
                flush()
                if tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "pre"):
                    lines.append("")
            else:
                walk(child, quote, lists)
            child = child.next

    walk(root, 0, [])
    flush()
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return _html.unescape(text).strip()


# ----------------------------------------------------------------- inbound

#: Real mail is tables, fonts and inline styles. Forms, scripts, frames,
#: objects, SVG and `<style>` blocks are not on the list and never will be.
INBOUND_TAGS = {
    "a", "abbr", "address", "b", "big", "blockquote", "br", "caption", "center",
    "cite", "code", "col", "colgroup", "dd", "del", "div", "dl", "dt", "em",
    "font", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd",
    "li", "mark", "ol", "p", "pre", "q", "s", "small", "span", "strike",
    "strong", "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead",
    "tr", "tt", "u", "ul",
}

_GENERIC_ATTRS = {
    "align", "valign", "width", "height", "bgcolor", "color", "border", "dir",
    "lang", "title", "style", "cellpadding", "cellspacing", "colspan", "rowspan",
    "face", "size", "nowrap",
}

#: CSS a message may keep. No `background` shorthand, no `background-image`,
#: no `list-style-image`, no `content`: every one of those can carry a
#: `url()`, which is a remote request the frame's CSP would then have to be
#: the only thing stopping.
INBOUND_STYLE = {
    "color", "background-color", "font", "font-family", "font-size",
    "font-style", "font-weight", "line-height", "letter-spacing",
    "text-align", "text-decoration", "text-transform", "vertical-align",
    "white-space", "word-break", "word-wrap", "overflow-wrap",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "border", "border-top", "border-right", "border-bottom", "border-left",
    "border-color", "border-style", "border-width", "border-collapse",
    "border-spacing", "border-radius", "width", "max-width", "min-width",
    "height", "max-height", "min-height", "display", "table-layout",
}


_RASTER_DATA = re.compile(r"^data:image/(png|jpe?g|gif|webp);base64,", re.I)


def clean_inbound(
    html: str,
    *,
    inline: dict[str, str] | None = None,
    images: bool = False,
) -> tuple[str, int]:
    """Somebody's HTML, safe to draw, and how many remote images were held.

    `inline` maps a Content-ID (no angle brackets) to the `data:` URI of the
    image the message itself carried; `cid:` references are rewritten to it,
    and one with no match is dropped rather than left pointing nowhere.
    `images=False` drops every remote image source and counts them, which is
    what the reader's "Show images" offers to undo.

    The rewrite happens inside the sanitiser's own attribute callback, not
    after it: changing markup once a sanitiser has run is how one is undone.
    """
    inline = {k.strip("<>").lower(): v for k, v in (inline or {}).items()}
    held = 0

    def attribute(element: str, name: str, value: str):  # noqa: ANN202
        nonlocal held
        if name == "href":
            # `data:` and `cid:` are allowed schemes because an image may use
            # them; a link may not. `data:text/html,...` is a whole page a
            # click away, and the scheme list cannot say "images only".
            v = (value or "").strip().lower()
            return value if v.startswith(("http://", "https://", "mailto:", "tel:", "#")) else None
        if element == "img" and name == "src":
            v = (value or "").strip()
            if v.lower().startswith("cid:"):
                from urllib.parse import unquote

                return inline.get(unquote(v[4:]).strip("<>").lower())
            if _RASTER_DATA.match(v):
                # Raster only. An SVG is a document, and one inside a
                # message is somebody else's markup with nothing cleaning it.
                return v
            if v.lower().startswith(("http://", "https://")):
                if images:
                    return v
                held += 1
                return None
            return None
        return value

    cleaned = nh3.clean(
        html or "",
        tags=INBOUND_TAGS,
        attributes={
            "*": _GENERIC_ATTRS,
            "a": {"href", "name"},
            "img": {"src", "alt"},
        },
        url_schemes={"http", "https", "mailto", "tel", "cid", "data"},
        attribute_filter=attribute,
        filter_style_properties=INBOUND_STYLE,
        link_rel="noopener noreferrer",
        set_tag_attribute_values={"a": {"target": "_blank"}},
        strip_comments=True,
    )
    return cleaned, held


def remote_images(html: str) -> int:
    """How many remote images a message would load if shown as sent."""
    return clean_inbound(html, images=False)[1]


def cleaner(images: bool) -> Callable[[str], tuple[str, int]]:
    return lambda html: clean_inbound(html, images=images)
