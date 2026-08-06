"""Deterministic placeholder vehicle photos.

A 4:3 SVG per VIN, hue derived from a hash of the VIN, year/make/model
overlaid. It looks intentional rather than broken, never 404s, and works
offline -- which matters because the mock listings would otherwise point at
external CDN images that can block or vanish (§18.4).

Real ingest overwrites ``vehicles.photo_url`` with a downloaded image.
"""

from __future__ import annotations

import hashlib
from xml.sax.saxutils import escape


def hue_for(vin: str) -> int:
    digest = hashlib.sha256(vin.encode()).hexdigest()
    return int(digest[:4], 16) % 360


def placeholder_svg(vin: str, year: int, make: str, model: str, trim: str = "") -> str:
    hue = hue_for(vin)
    bg = f"hsl({hue}, 42%, 88%)"
    mid = f"hsl({hue}, 38%, 76%)"
    body = f"hsl({hue}, 34%, 46%)"
    ink = f"hsl({hue}, 30%, 24%)"
    title = escape(f"{year} {make} {model}")
    sub = escape(trim or vin)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" \
width="800" height="600" role="img" aria-label="{title}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{bg}"/>
      <stop offset="100%" stop-color="{mid}"/>
    </linearGradient>
  </defs>
  <rect width="800" height="600" fill="url(#g)"/>
  <g fill="{body}" opacity="0.92">
    <path d="M140 372 q22 -74 62 -104 q34 -26 108 -30 h180 q64 2 106 34 l72 56 \
q46 8 66 22 q22 16 22 44 v30 q0 12 -14 12 h-52 a54 54 0 0 0 -108 0 h-200 \
a54 54 0 0 0 -108 0 h-48 q-14 0 -14 -14 z"/>
  </g>
  <g fill="{bg}" opacity="0.85">
    <path d="M262 262 q10 -40 40 -50 h84 v66 h-132 z"/>
    <path d="M406 212 h94 q34 0 54 16 l48 40 h-196 z"/>
  </g>
  <g fill="{ink}">
    <circle cx="322" cy="436" r="46"/><circle cx="630" cy="436" r="46"/>
  </g>
  <g fill="{bg}">
    <circle cx="322" cy="436" r="20"/><circle cx="630" cy="436" r="20"/>
  </g>
  <text x="56" y="82" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" \
font-size="42" font-weight="600" fill="{ink}">{title}</text>
  <text x="56" y="126" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" \
font-size="26" fill="{ink}" opacity="0.7">{sub}</text>
  <text x="744" y="566" text-anchor="end" \
font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" \
font-size="18" fill="{ink}" opacity="0.55">placeholder image</text>
</svg>"""
