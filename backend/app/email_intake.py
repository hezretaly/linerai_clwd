"""Reading a delivery: who sent it, whether a person sent it, what they said.

Split from `api/inbound_email.py`, which does the other half -- authenticate,
dedupe, store, resolve. These are pure functions over the envelope and the
body, so `make smoke` can drive every branch against a real capture without an
HTTP request or a database.

**The thread is never read out of the body.** A quoted block is the buyer's own
mail client's copy of what we sent them, and it can be truncated, edited or
machine-translated on the way back. Anything in it arrives looking like
something we said. Our own `outreach` and `inbound_emails` rows are the
history; `just_the_reply` exists to throw the mirror away, not to read it.
"""

from __future__ import annotations

import re
from email.utils import parseaddr

from app.config import settings

# Where a reply stops being what the buyer wrote and starts being a copy of
# what we sent them. Outlook draws a rule of underscores; Gmail writes "On
# <date> X wrote:"; older clients use the Original Message banner. Trimming is
# conservative on purpose -- the untouched body is kept on the receipt, so a
# marker that fires wrongly costs presentation, never the message.
QUOTE_MARKERS = [
    re.compile(r"\n_{8,}\s*\n"),
    re.compile(r"\n-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"\nOn .{5,80}\bwrote:\s*\n", re.IGNORECASE),
    re.compile(r"\nFrom:\s.+\nSent:\s", re.IGNORECASE),
]

TAG_RE = re.compile(r"<[^>]+>")

# reply+<token>@domain. The local part is what carries the thread; the domain
# is whatever Cloudflare routed, and matching on it would break the moment a
# dealer forwards from a second address.
REPLY_RE = re.compile(r"reply\+([A-Za-z0-9_-]{6,64})@", re.IGNORECASE)

#: A local part that no person reads. Answering one of these is either shouting
#: into a void or, worse, the first turn of a loop between two robots.
ROBOT_LOCAL_PARTS = {
    "no-reply", "noreply", "no_reply", "donotreply", "do-not-reply",
    "postmaster", "mailer-daemon", "mailerdaemon", "bounce", "bounces",
    "notifications", "notification", "automated", "auto-reply", "autoreply",
    "root", "daemon", "cron", "nobody",
}

#: Headers that say "a machine sent this", in the words of the specifications
#: that define them. RFC 3834 forbids auto-replying to `Auto-Submitted` values
#: other than `no`, and RFC 2919/2369 mark list traffic. Checked before any
#: timer, because a cooldown does not *stop* a loop -- it slows it to
#: forty-eight real emails a day, forever.
AUTOMATED_HEADERS = {
    "auto-submitted": lambda v: v.strip().lower() not in ("", "no"),
    "x-auto-response-suppress": lambda v: bool(v.strip()),
    "precedence": lambda v: v.strip().lower() in ("bulk", "junk", "list", "auto_reply"),
    "list-id": lambda v: bool(v.strip()),
    "list-unsubscribe": lambda v: bool(v.strip()),
    "x-autoreply": lambda v: bool(v.strip()),
    "x-autorespond": lambda v: bool(v.strip()),
}

#: A sign-off, so the line after it is a name rather than a sentence.
SIGN_OFFS = re.compile(
    r"^\s*(thanks|thank you|thanks so much|cheers|regards|kind regards|best|"
    r"best regards|best wishes|sincerely|yours|many thanks|ta)\s*[,.!]*\s*$",
    re.IGNORECASE,
)

#: What a name looks like at the bottom of an email: one to four capitalised
#: words, no digits, no punctuation beyond a hyphen or an apostrophe. Narrow on
#: purpose -- this is a guess, and a wrong guess here would put a stranger's
#: name on a dealership's buyer list.
NAME_LINE = re.compile(r"^[A-Z][\w'’-]*(?: [A-Z][\w'’.-]*){0,3}$")

#: Words that look like a name by shape and are not one.
NOT_A_NAME = {
    "sent from my iphone", "sent from my ipad", "sent from outlook",
    "get outlook for ios", "get outlook for android", "sent from mail for windows",
}


def just_the_reply(text: str) -> str:
    """What the buyer actually typed, without the thread they quoted back.

    Their whole previous message comes back attached to every reply. Storing it
    means a rep opening a timeline reads "what" followed by four paragraphs of
    our own words, and the next reply carries two copies.

    **Fails open on content, deliberately.** Some people answer *inside* the
    quote -- "see my answers below" -- and a naive trim deletes the only thing
    they said. So if trimming leaves nothing, the whole body is kept. Dropping
    something a buyer really said is far worse than keeping something they did
    not: the same rule `_is_noise` follows on a call, and the same rule the
    cross-talk filter follows, for the same reason.
    """
    trimmed = text or ""
    for marker in QUOTE_MARKERS:
        found = marker.search(trimmed)
        if found:
            trimmed = trimmed[: found.start()]
    return trimmed.strip() or (text or "").strip()


def as_text(html: str) -> str:
    """A last resort for a message that arrived with no plain-text part.

    Not a renderer -- it strips tags so a rep sees words instead of markup.
    Storing raw HTML in a field the timeline prints as text is how a reply
    shows up as a page of Outlook style attributes.
    """
    from html import unescape

    without_head = re.sub(
        r"<(script|style|head)\b.*?</\1>", " ", html or "", flags=re.S | re.IGNORECASE
    )
    spaced = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", without_head, flags=re.IGNORECASE)
    return re.sub(r"\n{3,}", "\n\n", unescape(TAG_RE.sub("", spaced))).strip()


def is_ours(recipient: str) -> str:
    """Was this addressed to Liner rather than to the dealership?

    Read from the settings that already name our two published addresses, plus
    `cto@` on the same domain, rather than a second hardcoded list -- the
    landing page and the ops mailbox both read the same values, and a third
    copy is how one of them starts disagreeing about who owns an inbox.

    `reply+<token>@` is never ours whatever the domain: it is minted by a send
    to a buyer and routes back into their timeline.
    """
    address = sender_address(recipient) or (recipient or "").strip().lower()
    if not address or REPLY_RE.search(recipient or ""):
        return ""
    # Compared on the **local part**, not the whole address. The Worker's own
    # recipient filter does the same, and for the same reason: mail reaches
    # these boxes through whatever domain Cloudflare is routing, and a dealer
    # forwarding from a second one is normal. Matching the full address meant
    # `support@` on any other domain read as the dealership's.
    ours = {
        (settings.support_email or "").partition("@")[0].strip().lower(),
        (settings.founder_email or "").partition("@")[0].strip().lower(),
        "cto",
    }
    local = address.partition("@")[0]
    return address if local in {a for a in ours if a} else ""


def sender_address(sender: str) -> str:
    """`austin@example.com` out of `"Austin Miller" <austin@example.com>`.

    The envelope is stored as it arrived, because `Austin Miller <a@b>` is what
    a rep wants to read -- so every *comparison* has to take the address out of
    it first, and every one of them has to do it the same way.

    Two did not, and both failed only for a sender with a display name, which
    is most real mail. `_resolve` matched the whole header against
    `leads.email` and never hit, so a buyer who had written before came back as
    a stranger; `claim_unresolved` compared the same way and never joined
    anything up. Neither was visible while the outcome was "unresolved" -- it
    reads exactly like a first contact. It became visible the moment a
    delivery that matched nobody started minting a lead, because the second
    email from the same person then made a second buyer.
    """
    return (parseaddr(sender or "")[1] or "").strip().lower()


def display_name(sender: str) -> str:
    """`Austin Miller` out of `"Austin Miller" <austin@example.com>`.

    Not a guess: it is what the sender's own mail client asserts about them,
    which is a fact on the envelope rather than something read out of prose.
    Still never used for *matching* -- `app/matching.py` is email exact and
    phone by its last ten digits, and a name is not identity there or here.
    """
    name = parseaddr(sender or "")[0].strip().strip('"').strip()
    # An address repeated as its own display name says nothing.
    return "" if "@" in name else name


def signature_name(body: str) -> str:
    """A name signed at the bottom of a message, or "" when there isn't one.

    People do sign their mail, and it is the only name an envelope with no
    display name offers. It is a **guess**, so callers record it with
    provenance `inferred` and never as the lead's own name -- prose cannot
    carry provenance, and a name asserted as fact is one a rep repeats on the
    phone to somebody it does not belong to.

    Read from the last few lines only, and only just after a sign-off or at the
    very end. Scanning the whole body would find every capitalised word in it.
    """
    lines = [line.strip() for line in (body or "").strip().splitlines()]
    lines = [line for line in lines if line and line.lower() not in NOT_A_NAME]
    if not lines:
        return ""

    tail = lines[-4:]
    # Immediately after "Thanks," is the strongest position there is.
    for index, line in enumerate(tail[:-1]):
        if SIGN_OFFS.match(line) and NAME_LINE.match(tail[index + 1]):
            return tail[index + 1]
    # Otherwise the very last line, and only when the message said more than
    # its own signature -- a one-line body is the message, not a sign-off.
    if len(lines) > 1 and NAME_LINE.match(lines[-1]):
        return lines[-1]
    return ""


def automated_reason(sender: str, headers: dict | None, body: str) -> str:
    """Why no reply should be composed for this delivery, or "" if one may be.

    The order is deliberate: **headers first, then the address, then shape.**
    A header is the sender declaring itself a machine, and honouring it stops a
    loop on turn one. The address catches the machine that declares nothing.
    Shape is last and weakest.

    Returns a reason rather than a boolean because it is written onto the
    receipt: "no reply -- List-Unsubscribe header" is something an operator can
    act on, and "not a person" is not.

    Never consulted for *storing* a delivery. Anything that arrives is kept --
    somebody or something really wrote in, and the receipt is the only way to
    tell a filtered newsletter from a Cloudflare route that has stopped
    working.
    """
    lowered = {str(k).lower(): str(v or "") for k, v in (headers or {}).items()}
    for header, is_automated in AUTOMATED_HEADERS.items():
        if header in lowered and is_automated(lowered[header]):
            return f"{header} header says a machine sent it"

    address = (parseaddr(sender or "")[1] or "").lower().strip()
    if not address or "@" not in address:
        return "no usable From address"
    local = address.split("@", 1)[0]
    # `no-reply+alerts@` is `no-reply@` wearing a plus tag, so the part before
    # the plus is what is compared -- and *only* the plus. Splitting on the
    # hyphen too cut `no-reply` down to `no`, which is in no list, so every
    # plus-addressed robot went through as a person.
    if local.split("+", 1)[0] in ROBOT_LOCAL_PARTS or local in ROBOT_LOCAL_PARTS:
        return f"{local}@ is not a mailbox a person reads"

    if not just_the_reply(body).strip():
        return "the message has no body"
    return ""
