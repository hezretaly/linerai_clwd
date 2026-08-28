"""The prospect's colours, read off their profile.

Deliberately not a database column. `create_all` adds a table to a database
that already exists and never a column, and there is no Alembic here -- so a
`brand` column would simply not appear on a deployed install, which is the one
place it matters. It is also not per-row data: which dealership this instance
is set up as is a deployment decision, and it already lives in a file.

Only the accent family travels. Every structural token -- greys, borders,
radii, the whole of shadcn classic -- still comes from
`styles/liner-theme.css`, so a prospect's colour cannot quietly restyle the
product into something that no longer reads. That is the same rule
`.theme-buyer` already follows; this makes the accent a value rather than a
constant.
"""

from __future__ import annotations

import re

import yaml

from app.config import settings

#: A CSS colour we are willing to interpolate into a style, and nothing else.
#:
#: This value comes from a file an operator edits, and it ends up inside a
#: `style` attribute in the buyer's browser. Anything that is not plainly a
#: hex colour is dropped rather than escaped: there is no legitimate reason for
#: a brand accent to be a `url(...)`, and a validator that tries to sanitise
#: arbitrary CSS is a validator that will eventually be wrong.
HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

#: What the buyer surfaces already use, and what an unset or rejected value
#: falls back to. Never a blank: an empty accent renders invisible text.
DEFAULT_ACCENT = "#0a84ff"
DEFAULT_INK = "#ffffff"


def _colour(value, fallback: str) -> str:
    text = str(value or "").strip()
    return text if HEX.match(text) else fallback


def brand() -> dict:
    """Accent, ink and logo for whichever profile this instance is running.

    Read per call rather than cached at import: the profile is edited during
    setup, and a colour that needs a restart to appear is one somebody will
    conclude does not work.
    """
    path = settings.dealership_config
    raw: dict = {}
    if path.is_file():
        try:
            raw = (yaml.safe_load(path.read_text()) or {}).get("brand") or {}
        except (OSError, yaml.YAMLError):
            raw = {}
    return {
        "accent": _colour(raw.get("accent"), DEFAULT_ACCENT),
        "accent_ink": _colour(raw.get("accent_ink"), DEFAULT_INK),
        # A URL, not a colour, so it is offered only when it is one we would
        # actually load. Anything else is dropped and the name is used.
        "logo_url": (
            str(raw.get("logo_url") or "").strip()
            if str(raw.get("logo_url") or "").strip().startswith(("https://", "/"))
            else ""
        ),
    }
