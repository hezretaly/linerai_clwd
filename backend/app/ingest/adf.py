"""ADF/XML lead ingest.

ADF (Auto-lead Data Format) is the one thing the automotive lead world agrees
on: every marketplace -- AutoTrader, CarGurus, Cars.com, a dealer's own website
form -- can post the same XML document. Parsing it is what lets this system take
leads it did not create, which is the only honest way to show outreach working
against something other than its own chat transcripts.

Two notes on what this deliberately does not do:

* **It does not fetch anything.** ADF is normally delivered by email (an
  ADF-XML attachment to a lead inbox) or by HTTP POST from the marketplace.
  Neither is configured here, so this parses a document you hand it -- an
  upload, or the manual form. There is no polling and no lead inbox.
* **It parses with ``defusedxml``, not ``xml.etree``.** The document is
  untrusted by definition; stdlib ElementTree still expands entities and will
  happily eat a "billion laughs" bomb.

The grammar below is the ADF 1.0 spec's ``<prospect>`` subset that carries
usable information. Anything outside it is ignored rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from defusedxml.ElementTree import ParseError, fromstring

MAX_BYTES = 1_000_000
MAX_PROSPECTS = 200

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")
DIGITS_RE = re.compile(r"\d")


@dataclass
class Prospect:
    """One ``<prospect>``, flattened to the fields this system can act on."""

    name: str = ""
    email: str = ""
    phone: str = ""
    # <provider><name> if present, else <vendor><vendorname>. "AutoTrader".
    provider: str = ""
    requested_at: str = ""
    comments: str = ""
    timeframe: str = ""
    vehicle_year: int | None = None
    vehicle_make: str = ""
    vehicle_model: str = ""
    vehicle_trim: str = ""
    vehicle_vin: str = ""
    vehicle_stock: str = ""
    # Non-fatal: the prospect still imports, the dealer sees the caveat.
    warnings: list[str] = field(default_factory=list)

    @property
    def vehicle_label(self) -> str:
        parts = [str(self.vehicle_year or ""), self.vehicle_make, self.vehicle_model,
                 self.vehicle_trim]
        return " ".join(p for p in parts if p).strip()

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "provider": self.provider,
            "requested_at": self.requested_at,
            "comments": self.comments,
            "timeframe": self.timeframe,
            "vehicle_year": self.vehicle_year,
            "vehicle_make": self.vehicle_make,
            "vehicle_model": self.vehicle_model,
            "vehicle_trim": self.vehicle_trim,
            "vehicle_vin": self.vehicle_vin,
            "vehicle_stock": self.vehicle_stock,
            "vehicle_label": self.vehicle_label,
            "warnings": self.warnings,
        }


class AdfError(Exception):
    """The document as a whole is unusable -- not one bad prospect in it."""


def _text(node) -> str:
    return " ".join((node.text or "").split()) if node is not None else ""


def _find(node, path: str):
    """Case-insensitive child lookup. Feeds disagree on `<vendorName>` casing."""
    if node is None:
        return None
    head, _, rest = path.partition("/")
    for child in node:
        tag = child.tag.split("}")[-1].lower()
        if tag == head.lower():
            return _find(child, rest) if rest else child
    return None


def _findall(node, tag: str) -> list:
    if node is None:
        return []
    return [c for c in node if c.tag.split("}")[-1].lower() == tag.lower()]


def _attr(node, name: str) -> str:
    if node is None:
        return ""
    for key, value in node.attrib.items():
        if key.split("}")[-1].lower() == name.lower():
            return (value or "").strip()
    return ""


def _name(contact) -> str:
    """ADF splits a name across repeated <name part="first"|"last"> elements."""
    parts: dict[str, str] = {}
    for node in _findall(contact, "name"):
        parts[_attr(node, "part").lower() or "full"] = _text(node)
    if parts.get("full"):
        return parts["full"]
    ordered = [parts.get("first", ""), parts.get("middle", ""), parts.get("last", "")]
    return " ".join(p for p in ordered if p).strip()


def _int(value: str) -> int | None:
    match = re.search(r"\d{4}", value or "")
    return int(match.group(0)) if match else None


def _requestdate(raw: str) -> str:
    """Normalised to naive local, matching the rest of the schema.

    ADF carries an offset; this system's timestamps are dealership-local and
    naive on purpose. Keeping the wall-clock reading is closer to what a rep
    means by "they enquired at 9:14" than shifting it into another frame.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    cleaned = re.sub(r"(Z|[+-]\d{2}:?\d{2})$", "", text)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).isoformat()
        except ValueError:
            continue
    return ""


def _prospect(node) -> Prospect:
    out = Prospect()

    customer = _find(node, "customer")
    contact = _find(customer, "contact")
    out.name = _name(contact)

    email = _text(_find(contact, "email"))
    if email and not EMAIL_RE.match(email):
        out.warnings.append(f"{email!r} is not a usable email address -- dropped")
        email = ""
    out.email = email.lower()

    phones = _findall(contact, "phone")
    # Prefer a voice line: an ADF <phone type="fax"> is not a way to reach a buyer.
    voice = [p for p in phones if _attr(p, "type").lower() in ("", "voice", "cellphone")]
    phone = _text((voice or phones or [None])[0])
    out.phone = phone if DIGITS_RE.search(phone) else ""

    out.comments = _text(_find(customer, "comments")) or _text(_find(node, "comments"))
    out.timeframe = _text(_find(customer, "timeframe/description"))

    # Interest="buy" is the one worth working. A trade-in-only or service
    # prospect gets flagged rather than silently treated like a shopper.
    vehicles = _findall(node, "vehicle")
    buying = [v for v in vehicles if _attr(v, "interest").lower() in ("", "buy", "lease")]
    vehicle = (buying or vehicles or [None])[0]
    if vehicle is not None:
        out.vehicle_year = _int(_text(_find(vehicle, "year")))
        out.vehicle_make = _text(_find(vehicle, "make"))
        out.vehicle_model = _text(_find(vehicle, "model"))
        out.vehicle_trim = _text(_find(vehicle, "trim"))
        out.vehicle_vin = _text(_find(vehicle, "vin")).upper()
        out.vehicle_stock = _text(_find(vehicle, "stock"))
        if not out.comments:
            out.comments = _text(_find(vehicle, "comments"))
    if vehicles and not buying:
        out.warnings.append("No vehicle on this prospect is marked interest=\"buy\"")

    provider = _find(node, "provider")
    out.provider = _name(provider) or _text(_find(provider, "name"))
    if not out.provider:
        out.provider = _text(_find(node, "vendor/vendorname"))
    out.requested_at = _requestdate(_text(_find(node, "requestdate")))

    if not out.email:
        out.warnings.append(
            "No email address, so this lead cannot be emailed -- a rep has to call."
        )
    if not out.name:
        out.warnings.append("No customer name in the document")
    return out


def parse_adf(raw: str) -> tuple[list[Prospect], list[dict]]:
    """Returns (prospects, per-prospect errors). Raises AdfError on the document."""
    if len(raw.encode("utf-8", errors="ignore")) > MAX_BYTES:
        raise AdfError(f"File is over {MAX_BYTES // 1000} KB. ADF drops are small documents.")
    text = raw.strip()
    if not text:
        raise AdfError("The file is empty.")
    # <?adf version="1.0"?> is a processing instruction ahead of the root, which
    # ElementTree accepts; a stray BOM ahead of it is not.
    text = text.lstrip("﻿")

    try:
        root = fromstring(text)
    except ParseError as exc:
        raise AdfError(f"This is not well-formed XML: {exc}") from None
    except Exception as exc:  # defusedxml raises its own types for bombs
        raise AdfError(f"Refused to parse this document: {exc}") from None

    tag = root.tag.split("}")[-1].lower()
    nodes = _findall(root, "prospect")
    if tag == "prospect":
        nodes = [root]
    elif tag != "adf":
        raise AdfError(f"Root element is <{tag}>, expected <adf>. This is not an ADF document.")
    if not nodes:
        raise AdfError("No <prospect> elements in this document.")
    if len(nodes) > MAX_PROSPECTS:
        raise AdfError(f"{len(nodes)} prospects in one file, over the {MAX_PROSPECTS} limit.")

    prospects, errors = [], []
    for index, node in enumerate(nodes, start=1):
        try:
            parsed = _prospect(node)
        except Exception as exc:
            errors.append({"row": index, "error": str(exc)})
            continue
        if not parsed.email and not parsed.phone:
            errors.append({
                "row": index,
                "error": (
                    f"{parsed.name or 'Prospect ' + str(index)} has neither an email nor a "
                    "phone number, so there is no way to contact them."
                ),
            })
            continue
        prospects.append(parsed)
    return prospects, errors
