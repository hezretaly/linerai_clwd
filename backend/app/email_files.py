"""Attachment bytes: checked, stored on disk, and handed back safely.

**On disk, never in a database.** A table growing by megabytes a row ruins
every backup, and `make dump-ops` writes rows to JSON, which bytes do not
survive. Files live under `var/attachments/<scope>/`, where the scope is the
store's slug (or `_default` for the unprefixed store, or `ops` for Liner's own
mail) -- so one dealership's files are never in another's folder, the same
isolation the per-store database files give the rows.

**Named by their SHA-256.** The same PDF forwarded three times is one file,
and a file is never deleted out from under a row that still points at it:
nothing here removes a stored file, because two rows in two messages may share
one. A reseed deletes rows; the orphaned bytes are harmless and cost only
disk.

**Checked like an upload from a stranger, because inbound ones are.** The
declared type is not trusted -- a raster image is recognised by its first
bytes, everything else is served as a download -- and the extensions Gmail
refuses to carry are refused here too, so a rep cannot send what the buyer's
mail provider would bounce, and a buyer cannot put an `.exe` one click away
from a rep. The page says plainly that nothing here is virus-scanned, rather
than implying a check that does not exist.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import quote

from app.config import BACKEND_DIR

ROOT = BACKEND_DIR / "var" / "attachments"

#: One file, and one message's files together, on the way out. Base64 adds a
#: third, and Resend's ceiling is 40 MB per message after encoding; 20 MB of
#: files is under it with room for the body.
MAX_FILE = 15 * 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024

#: What Gmail will not carry (support.google.com/mail/answer/6590). Refusing
#: them here means a rep never sends a file that bounces at the buyer's end,
#: and a received one is kept as a name with no bytes a click away.
BLOCKED_EXTENSIONS = frozenset(
    ".ade .adp .apk .appx .appxbundle .bat .cab .chm .cmd .com .cpl .diagcab "
    ".diagcfg .diagpkg .dll .dmg .ex .ex_ .exe .hta .img .ins .iso .isp .jar "
    ".jnlp .js .jse .lib .lnk .mde .mjs .msc .msi .msix .msixbundle .msp .mst "
    ".nsh .pif .ps1 .scr .sct .shb .sys .vb .vbe .vbs .vhd .vxd .wsc .wsf .wsh "
    ".xll".split()
)

#: The only types ever shown in the page rather than downloaded, and only
#: after their first bytes agree. SVG is deliberately absent: it is a
#: document that can carry script, served from our own origin.
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
INLINE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})

_UNSAFE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]+')


def scope_for(store: str | None) -> str:
    """The folder a store's files live in. `ops` is Liner's own."""
    slug = (store or "").strip()
    if not slug:
        return "_default"
    return re.sub(r"[^a-z0-9_-]", "", slug.lower()) or "_default"


def safe_filename(name: str | None) -> str:
    """A name fit to show and to put in a download header.

    The basename only (RFC 2183: a suggested filename is "a terminal
    component only"), with path separators, control characters and the
    characters Windows refuses taken out, no leading dots, and a length a
    header can carry. A received `../../etc/passwd` becomes `passwd`.
    """
    text = unicodedata.normalize("NFC", str(name or ""))
    text = text.replace("\\", "/").split("/")[-1]
    text = _UNSAFE.sub("_", text).strip().lstrip(".").strip()
    if len(text) > 180:
        stem, dot, ext = text.rpartition(".")
        text = (stem[: 180 - len(ext) - 1] + "." + ext) if dot and len(ext) <= 10 else text[:180]
    return text or "attachment"


def extension(name: str) -> str:
    base = (name or "").lower().rstrip(". ")
    return "." + base.rsplit(".", 1)[-1] if "." in base else ""


def blocked_reason(filename: str) -> str:
    """Why this file will not be carried, or "" when it may be.

    Every extension in the name is checked, not only the last, because
    `invoice.pdf.exe` and `photo.exe.jpg` are the two oldest tricks there are
    -- and a trailing dot or space, which Windows strips, is not allowed to
    hide the real one.
    """
    name = (filename or "").lower().strip().rstrip(". ")
    parts = name.split(".")[1:]
    for part in parts:
        if "." + part in BLOCKED_EXTENSIONS:
            return (
                f"{filename} is a .{part} file, which mail providers refuse to carry "
                "because it can run as a program."
            )
    return ""


def sniff(data: bytes, declared: str = "", filename: str = "") -> str:
    """What this file is, from its bytes first and its name second.

    The declared type is the sender's claim and is only used to break a tie
    between two harmless guesses; a file that says it is a PNG and starts
    like a ZIP is a ZIP.
    """
    head = data[:16]
    for magic, kind in _MAGIC:
        if head.startswith(magic):
            return kind
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"%PDF"):
        return "application/pdf"
    guessed = mimetypes.guess_type(filename or "")[0] or ""
    if guessed in INLINE_TYPES:
        # A name ending .png on bytes that are not a PNG is not a picture.
        return "application/octet-stream"
    if guessed:
        return guessed
    declared = (declared or "").split(";")[0].strip().lower()
    if declared and declared not in INLINE_TYPES and "/" in declared:
        return declared
    return "application/octet-stream"


def store(scope: str, data: bytes) -> tuple[str, str]:
    """Write the bytes once and return `(sha256, relative path)`.

    Written to a temporary file and renamed into place, so a reader never
    sees half a file, and skipped entirely when an identical one is already
    there.
    """
    digest = hashlib.sha256(data).hexdigest()
    relative = f"{scope_for(scope)}/{digest[:2]}/{digest}"
    target = ROOT / relative
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return digest, relative


def path_of(relative: str) -> Path | None:
    """The file behind a stored relative path, or None.

    Resolved and checked to be inside the attachments root: the path comes
    from a database row, and a row is not an authority on where the disk is.
    """
    if not relative:
        return None
    candidate = (ROOT / relative).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def read(relative: str) -> bytes | None:
    path = path_of(relative)
    return path.read_bytes() if path is not None else None


def content_disposition(filename: str, *, inline: bool = False) -> str:
    """Both spellings of the filename, as RFC 6266 recommends.

    An ASCII fallback for old clients and `filename*` in UTF-8 for everyone
    else, so `Käufer.pdf` downloads as `Käufer.pdf`.
    """
    name = safe_filename(filename)
    fallback = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode() or "attachment"
    fallback = fallback.replace('"', "")
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name)}"


def download_headers(filename: str, content_type: str, *, inline: bool = False) -> dict[str, str]:
    """Headers for serving a stored file from our own origin.

    `nosniff`, so the browser believes the type we state; a `sandbox` CSP, so
    even a file that somehow renders gets an opaque origin and no script; and
    `attachment` for everything but a raster image a rep asked to see.
    """
    show = inline and content_type in INLINE_TYPES
    return {
        "Content-Disposition": content_disposition(filename, inline=show),
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self' data:",
        "Cache-Control": "private, max-age=3600",
    }


def served_type(content_type: str, *, inline: bool = False) -> str:
    """The Content-Type a download is served with.

    Only a raster image being shown keeps its own type; everything else is
    `application/octet-stream`, so no HTML, SVG or XML a sender attached can
    ever be rendered as a page from our origin.
    """
    if inline and content_type in INLINE_TYPES:
        return content_type
    if content_type == "application/pdf":
        return content_type
    return "application/octet-stream"


def data_uri(data: bytes, content_type: str) -> str | None:
    """An inline image as a `data:` URI, for a `cid:` reference to point at.

    Only raster images, and only ones small enough to put in a page -- a
    reader that inlines a 20 MB photo into an iframe document stalls the tab.
    """
    import base64

    kind = sniff(data, content_type)
    if kind not in INLINE_TYPES or len(data) > 3 * 1024 * 1024:
        return None
    return f"data:{kind};base64," + base64.b64encode(data).decode()
