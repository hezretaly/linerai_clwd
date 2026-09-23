"""Who an email is to: parsing, checking and storing recipient lists.

Every address a person types into a To, Cc or Bcc box, and every list a
received message carries, goes through here. The rest of the system used to
treat "to" as one bare address, and a rep who typed two into the one box sent
a single string with a comma in it -- refused by the provider, or matched to
nobody. So the rule now is one function that turns what was typed into a list
of `Recipient`s, and says precisely which entry it could not read.

**Parsed one entry at a time, never as a whole header.** Python's
`getaddresses` in strict mode (the 3.11 default since the CVE-2023-27043 fix)
turns an entire list into `[('', '')]` if one separator is a semicolon, and
still lets a bare word through as an "address". So a box's text is split on
commas, semicolons and newlines *outside* quotes and angle brackets -- which
keeps `"Doe, Jane" <jane@x.com>` whole -- and each piece is parsed with the
header registry (which decodes encoded names and reports defects) and then
checked by `email-validator`, which pydantic already brought in.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from email.headerregistry import HeaderRegistry
from email.utils import formataddr
from typing import Iterable, Sequence

from email_validator import EmailNotValidError, validate_email

#: The most recipients one message may carry across To, Cc and Bcc together.
#: Resend's own cap on `to` is 50, and Cloudflare's sending service caps all
#: three combined at 50; one number that holds for both is the one to keep.
MAX_RECIPIENTS = 50

#: C0 controls and DEL. A display name carrying CR/LF would let whoever typed
#: it add a header of their own (`formataddr` in 3.11 does not refuse them).
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

_registry = HeaderRegistry()


@dataclass(frozen=True)
class Recipient:
    name: str
    address: str

    @property
    def key(self) -> str:
        """What two spellings of one address have in common.

        Domains are case-insensitive and local parts, in practice, are too; a
        list de-duplicated on anything stricter sends the same person two
        copies.
        """
        return self.address.lower()

    def header(self) -> str:
        """`Name <address>`, or the bare address, safe to put in a header."""
        name = _CONTROL.sub(" ", self.name or "").strip()
        return formataddr((name, self.address)) if name else self.address

    def as_dict(self) -> dict:
        return {"name": self.name, "address": self.address}


def split_entries(text: str) -> list[str]:
    """What somebody typed into one box, as one string per address.

    Commas, semicolons and newlines separate entries, except inside a quoted
    display name or an angle-bracketed address. Outlook users type `;`
    between addresses and people paste a column out of a spreadsheet, and
    both have to work.
    """
    entries: list[str] = []
    current: list[str] = []
    quoted = False
    angled = False
    escaped = False
    for ch in text or "":
        if escaped:
            current.append(ch)
            escaped = False
            continue
        if ch == "\\" and quoted:
            current.append(ch)
            escaped = True
            continue
        if ch == '"':
            quoted = not quoted
        elif ch == "<" and not quoted:
            angled = True
        elif ch == ">" and not quoted:
            angled = False
        if ch in ",;\n\r" and not quoted and not angled:
            entries.append("".join(current))
            current = []
            continue
        current.append(ch)
    entries.append("".join(current))
    return [e.strip() for e in entries if e.strip()]


def parse_one(entry: str) -> Recipient | None:
    """One typed entry as a `Recipient`, or None when it is not an address.

    Exactly one mailbox, no defects, and an address `email-validator` accepts
    (syntax only -- no DNS, which would make sending depend on a lookup this
    box may not be able to make).
    """
    entry = _CONTROL.sub(" ", entry or "").strip()
    if not entry or "@" not in entry:
        return None
    try:
        header = _registry("To", entry)
    except Exception:  # noqa: BLE001 -- a parse failure is simply "not an address"
        return None
    if getattr(header, "defects", None):
        return None
    mailboxes = [a for g in header.groups for a in g.addresses]
    if len(mailboxes) != 1:
        return None
    mailbox = mailboxes[0]
    address = (mailbox.addr_spec or "").strip()
    if not address or "@" not in address:
        return None
    try:
        checked = validate_email(address, check_deliverability=False)
    except EmailNotValidError:
        return None
    return Recipient(name=(mailbox.display_name or "").strip(), address=checked.normalized)


def parse(values: str | Sequence[str] | None) -> tuple[list[Recipient], list[str]]:
    """Every recipient in what was sent, and every entry that was not one.

    `values` is what an API caller sent for one role: a single string (which
    may itself hold several addresses -- the old composers sent one), or a
    list of strings. Returns the recipients in order, without duplicates, and
    the entries that could not be read, verbatim, so a refusal can name them.
    """
    if values is None:
        return [], []
    raw: list[str] = []
    for value in [values] if isinstance(values, str) else list(values):
        if isinstance(value, dict):
            name = str(value.get("name") or "")
            address = str(value.get("address") or value.get("email") or "")
            value = formataddr((name, address)) if name else address
        raw.extend(split_entries(str(value or "")))
    found: list[Recipient] = []
    bad: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        recipient = parse_one(entry)
        if recipient is None:
            bad.append(entry)
            continue
        if recipient.key in seen:
            continue
        seen.add(recipient.key)
        found.append(recipient)
    return found, bad


def distinct(
    to: Sequence[Recipient], cc: Sequence[Recipient], bcc: Sequence[Recipient]
) -> tuple[list[Recipient], list[Recipient], list[Recipient]]:
    """The three lists with nobody in two of them.

    A person in To who is also typed into Cc gets one copy, as To. Bcc loses
    to both, because an address that is visible elsewhere on the message is
    not blind.
    """
    seen: set[str] = set()
    out: list[list[Recipient]] = []
    for role in (to, cc, bcc):
        kept = []
        for r in role:
            if r.key in seen:
                continue
            seen.add(r.key)
            kept.append(r)
        out.append(kept)
    return out[0], out[1], out[2]


def dumps(recipients: Iterable[Recipient]) -> str:
    return json.dumps([r.as_dict() for r in recipients])


def loads(text: str | None) -> list[Recipient]:
    """A stored list back as `Recipient`s. Tolerates rows written by hand."""
    try:
        items = json.loads(text or "[]")
    except ValueError:
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get("address"):
            out.append(Recipient(name=str(item.get("name") or ""), address=str(item["address"])))
        elif isinstance(item, str) and "@" in item:
            out.append(Recipient(name="", address=item))
    return out


def as_dicts(text: str | None) -> list[dict]:
    """A stored list as the `{name, address}` dicts the API serves."""
    return [r.as_dict() for r in loads(text)]


def from_header_value(value: str | None) -> list[Recipient]:
    """A received header (To, Cc, Reply-To) as recipients, groups flattened.

    Received mail is not a form anybody typed into, so this is lenient where
    `parse` is strict: an entry that fails validation is kept if it has an
    `@`, because refusing to show who a message was addressed to is worse
    than showing an odd address.
    """
    if not value:
        return []
    try:
        header = _registry("To", str(value))
    except Exception:  # noqa: BLE001
        return []
    out: list[Recipient] = []
    seen: set[str] = set()
    for group in header.groups:
        for mailbox in group.addresses:
            address = (mailbox.addr_spec or "").strip()
            if "@" not in address or address.lower() in seen:
                continue
            seen.add(address.lower())
            out.append(Recipient(name=(mailbox.display_name or "").strip(), address=address))
    return out
