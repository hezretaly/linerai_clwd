"""Tool definitions and executors.

Two rules are enforced here rather than in the prompt, because the prompt is a
request and the executor is a guarantee:

1. A vehicle marked do-not-discuss never reaches the model at all. Filtering in
   the tool is safer than instructing the model not to mention it.
2. ``save_captured_fields`` rejects provenance='typed' for any value the buyer
   did not actually say. The model must not be able to launder a guess into a
   fact a rep repeats on the phone (§18.2).

``book_appointment`` and ``escalate_to_human`` are the only tools with side
effects, so both are idempotent on (conversation_id, tool_call_id) -- a retried
turn must not double-book.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db import utcnow
from app.agent import details
from app.agent.phrasing import cased
from app.escalations import claim_for_owner
from app.events import emit
from app import conversation_once, matching
from app.matching import match_lead
from app.models import (
    Appointment,
    Dealership,
    Conversation,
    Escalation,
    HandoffRule,
    KnowledgeEntry,
    Lead,
    Message,
    Outreach,
    Vehicle,
    VehicleMention,
)

MAX_RESULTS = 5

# A buyer asks for "something German"; the inventory row records a make. This is
# the mapping between the two, and it is curated rather than inferred -- there
# is no country column on a vehicle and guessing one from a VIN's world
# manufacturer identifier gets assembly plants, not marques. A make missing from
# this table simply has no origin, which is a smaller error than a wrong one.
ORIGIN_BY_MAKE = {
    "audi": "german", "bmw": "german", "mercedes": "german",
    "mercedes-benz": "german", "porsche": "german", "volkswagen": "german",
    "vw": "german", "mini": "german",
    "acura": "japanese", "honda": "japanese", "infiniti": "japanese",
    "lexus": "japanese", "mazda": "japanese", "mitsubishi": "japanese",
    "nissan": "japanese", "subaru": "japanese", "toyota": "japanese",
    "genesis": "korean", "hyundai": "korean", "kia": "korean",
    "buick": "american", "cadillac": "american", "chevrolet": "american",
    "chrysler": "american", "dodge": "american", "ford": "american",
    "gmc": "american", "jeep": "american", "lincoln": "american",
    "ram": "american", "tesla": "american",
    "jaguar": "british", "land rover": "british", "mg": "british",
    "alfa romeo": "italian", "fiat": "italian", "maserati": "italian",
    "volvo": "swedish",
}

# The words a buyer actually uses, mapped onto the values above.
ORIGIN_ALIASES = {
    "germany": "german", "german": "german", "euro": "european",
    "european": "european", "europe": "european",
    "japan": "japanese", "japanese": "japanese", "jdm": "japanese",
    "korea": "korean", "korean": "korean",
    "america": "american", "american": "american", "usa": "american",
    "us": "american", "domestic": "american",
    "britain": "british", "british": "british", "uk": "british",
    "italy": "italian", "italian": "italian",
    "sweden": "swedish", "swedish": "swedish",
}

# "European" is a family, not a single origin -- a buyer asking for one means
# any of these.
EUROPEAN = {"german", "british", "italian", "swedish"}


def known_makes(db: Session) -> set[str]:
    """Every make the guard should notice in a reply.

    The union of the table above and whatever this dealership actually stocks,
    because the two answer different halves of the question. A make the lot has
    never carried has to be in here or "we've got a Ford Escape too" sails past
    a guard built only from the lot; a make the lot carries that the table
    happens to miss has to be in here or a real car reads as invented.

    Every status, not only `available`: a sold Accord's make is exactly what
    the guard needs to recognise when the model names it a week later.
    """
    makes = set(ORIGIN_BY_MAKE)
    for (make,) in db.query(Vehicle.make).distinct().all():
        for word in re.split(r"[^a-z0-9]+", (make or "").lower()):
            if len(word) > 2:
                makes.add(word)
    # "mercedes-benz" is one key in the table and two words in a reply.
    for key in list(makes):
        for word in re.split(r"[^a-z0-9]+", key):
            if len(word) > 2:
                makes.add(word)
    return makes


def earlier_results(db: Session, convo: Conversation) -> list[dict]:
    """Every tool result already in this thread.

    Grounding for the vehicle guard, and the after-the-fact guard on a call
    reads the same thing -- one definition, because two copies of "what has
    this conversation been told" is how one channel starts flagging a car the
    other accepts.
    """
    from app.agent.guards import tool_results_from_messages

    return [
        result
        for message in db.query(Message)
        .filter(Message.conversation_id == convo.id, Message.tool_calls_json.is_not(None))
        .all()
        for result in tool_results_from_messages(message.tool_calls_json or "[]")
    ]


def origin_of(make: str) -> str:
    return ORIGIN_BY_MAKE.get((make or "").strip().lower(), "")


def _origin_matches(make: str, wanted: str) -> bool:
    origin = origin_of(make)
    if not origin:
        return False
    if wanted == "european":
        return origin in EUROPEAN
    return origin == wanted
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: The three that are columns on `leads` rather than `captured_fields` rows.
#: One list, because both writers have to agree: the card's `save_details`
#: keeps them out of the captured fields it passes on, and
#: `save_captured_fields` sends them through `attach_lead` instead of storing
#: a second copy. A number in both places is two answers to one question, and
#: `app/matching.py` reads the column.
CONTACT_KEYS = ("name", "email", details.PHONE_KEY)


class ToolError(Exception):
    """A tool refused. The message goes back to the model as a tool result."""


# --------------------------------------------------------------------------
# Definitions (Anthropic tool schema)
# --------------------------------------------------------------------------

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "search_inventory",
        "description": (
            "Search available vehicles. Returns at most 5. Use this before naming any "
            "vehicle or quoting any price -- you have no inventory knowledge without it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keywords": {"type": "string", "description": "Free text, e.g. 'third row awd'"},
                "max_price": {"type": "integer"},
                "min_price": {"type": "integer"},
                "min_year": {"type": "integer"},
                "max_mileage": {
                    "type": "integer",
                    "description": "Miles on the clock, e.g. 100000 for 'under 100k miles'",
                },
                "min_mileage": {"type": "integer"},
                "body_style": {"type": "string"},
                "min_seats": {"type": "integer"},
                "origin": {
                    "type": "string",
                    "description": "Where the marque is from, for 'something German'.",
                    "enum": ["german", "japanese", "korean", "american", "british",
                             "italian", "swedish", "european"],
                },
            },
        },
    },
    {
        "name": "get_vehicle",
        "description": (
            "Full detail for one vehicle by VIN, including the listing's complete "
            "options list. Call it before answering any equipment question -- seats, "
            "rows, sunroof, drivetrain, tow package -- about a specific car."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"vin": {"type": "string"}},
            "required": ["vin"],
        },
    },
    {
        "name": "check_availability",
        "description": (
            "Open appointment slots. Get their name and phone number first -- times "
            "are worth nothing without somebody to hold them for. Always offer two "
            "concrete times from this result; never ask the buyer when works for them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {"type": "integer", "description": "Defaults to 7"},
                "preferred_period": {
                    "type": "string",
                    "enum": ["morning", "afternoon", "evening", "any"],
                },
            },
        },
    },
    {
        "name": "book_appointment",
        "description": (
            "Book a test drive. Requires a name and a phone number. An email is "
            "optional and is best asked for once the time is set -- a number is what "
            "a rep will actually use."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "phone": {"type": "string"},
                "email": {"type": "string", "description": "Optional."},
                "starts_at": {"type": "string", "description": "ISO 8601"},
                "vin": {"type": "string"},
            },
            "required": ["name", "phone", "starts_at"],
        },
    },
    {
        "name": "request_details",
        "description": (
            "Put a short contact form on the buyer's screen, instead of asking in "
            "prose -- call it the contact form. Use it the moment you want a way to "
            "reach them. A "
            "phone number and an email box are always included -- the number is the "
            "one they have to fill in, because a rep can ring it, and the address is "
            "there for a buyer who would rather be written to. Do NOT ask for the "
            "same things in your reply text: the form is already there, and asking "
            "twice gets the question answered in the worse place. Say what the "
            "form is for and stop."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "description": (
                        "Which boxes to show, at most four. Anything not on this list "
                        "is dropped rather than invented."
                    ),
                    "items": {
                        "type": "string",
                        "enum": list(details.FIELDS),
                    },
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "One short line shown above the boxes saying what these are "
                        "for -- 'so someone can call you about the Silverado'. A form "
                        "with no stated purpose reads as lead capture, not help."
                    ),
                },
            },
        },
    },
    {
        "name": "save_captured_fields",
        "description": (
            "Record what you learned about the buyer. Provenance must be honest: use "
            "'typed' only when the buyer said the value in their own words, 'listing' "
            "when it came from a vehicle record, 'caller_id' for phone metadata, and "
            "'inferred' for anything you concluded. Dishonest provenance is rejected."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "value": {"type": "string"},
                            "provenance": {
                                "type": "string",
                                "enum": ["typed", "listing", "caller_id", "inferred"],
                            },
                        },
                        "required": ["key", "value", "provenance"],
                    },
                }
            },
            "required": ["fields"],
        },
    },
    {
        "name": "answer_from_knowledge",
        "description": (
            "Look up the dealership's own answer to a policy question -- trade-ins, doc "
            "fee, deposits, financing, warranty, out-of-state buyers, hours. Use this "
            "for anything about how the dealership operates. You have no policy "
            "knowledge without it, and a wrong answer here is one a buyer repeats back "
            "to a rep."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The buyer's question, in their own words.",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "close_conversation",
        "description": (
            "Call this ONLY when the buyer has said they are done -- 'that's all', "
            "'thanks, bye', 'I'll think about it'. Never end a conversation yourself "
            "because you have run out of things to say; ask a question instead. Say "
            "your goodbye and nothing else: do not offer to email them a summary, and "
            "do not ask them another question on the way out."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "Two or three sentences a rep can read in five seconds: what "
                        "they wanted, what you showed them, what happens next."
                    ),
                },
                "send_summary": {
                    "type": "boolean",
                    "description": (
                        "Only if the buyer asked, unprompted, to be emailed a summary. "
                        "Never offer one. Most deployments send nothing either way."
                    ),
                },
            },
            "required": ["summary"],
        },
    },
    {
        "name": "offer_credit_application",
        "description": (
            "Put the dealership's own finance application on the buyer's screen as a "
            "button. Use it whenever they bring up financing, payments, credit or "
            "getting approved: applying before the visit is the next best thing to "
            "booking one. Say one line about why it helps -- nothing is decided by "
            "filling it in -- and stop."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Stop and hand the conversation to a person. Use for out-the-door price "
            "requests, credit or financing trouble, a request for a manager, urgency "
            "inside a few days, or a buyer ready to sign."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rule_key": {
                    "type": "string",
                    "enum": [
                        "out_the_door_price", "financing_trouble", "asks_for_manager",
                        "urgency", "ready_to_sign",
                    ],
                },
                "reason": {"type": "string"},
            },
            "required": ["rule_key", "reason"],
        },
    },
]


# --------------------------------------------------------------------------
# Executors
# --------------------------------------------------------------------------


#: A car with no published price is not an error and not a gap -- it is a
#: listing state the dealership chose, usually for something rare or very old.
#: Their site answers it with an inquiry form at the same URL, so the link is
#: derived rather than stored: `?mode=inquiry` on the listing, and only where
#: there is no price to quote.
INQUIRY_QUERY = "mode=inquiry"


def inquiry_url(v: Vehicle) -> str:
    """The dealer's own enquiry form for a car they will not price online."""
    if v.price or not v.listing_url:
        return ""
    joiner = "&" if "?" in v.listing_url else "?"
    return f"{v.listing_url}{joiner}{INQUIRY_QUERY}"


def history_url(raw: dict) -> str:
    """The car's history report link, only where it is an https URL.

    It lands in an `href` on a buyer's screen and comes from a file a dealer
    edits, so the rule is the one `showroom._https` applies to the same field.
    """
    url = str(raw.get("vehicle_history_url") or "").strip()
    return url if url.startswith("https://") else ""


def home_location(db: Session) -> str:
    """The dealership's own address, lowercased, for comparing a car's lot to it.

    Read once per tool call rather than per vehicle: a search returns five rows
    and the address does not change between them.
    """
    row = db.query(Dealership).first()
    return (row.address or "").lower() if row else ""


#: How many options lines a *search* result carries per car. A dealer's own
#: page lists a hundred per vehicle, and five cars times a hundred lines is
#: the prompt the model reads on every later turn of the conversation.
#: `get_vehicle` carries the whole list, and the prompt says to call it before
#: answering an equipment question.
SEARCH_FEATURES = 8


def _vehicle_payload(v: Vehicle, home: str = "", full: bool = True) -> dict:
    raw = json.loads(v.raw_json or "{}") if v.raw_json else {}
    features = json.loads(v.features_json or "[]")
    payload = {
        "vin": v.vin,
        "year": v.year,
        # Cased for the same reason the two serializers are, and this is the
        # one that matters most: these values are what the *assistant* reads,
        # so they reach the buyer in prose and out loud on a call. Alsbou's
        # export is all caps, so Liner said "a 2012 NISSAN VERSA". `origin_of`
        # below still gets a lowercase match either way.
        "make": cased(v.make),
        "model": cased(v.model),
        "trim": cased(v.trim),
        "price": v.price,
        "mileage": v.mileage,
        "body_style": v.body_style,
        "seats": v.seats,
        "origin": origin_of(v.make),
        "features": features if full else features[:SEARCH_FEATURES],
        "photo_url": v.photo_url,
        "listing_url": v.listing_url,
        "status": v.status,
    }
    # Which of the group's lots it is standing on. A dealership with more than
    # one address lists them all in one feed, and the appointment Liner books
    # is at the address in `dealerships` -- so a car at another store has to
    # say so, or the buyer drives to the wrong forecourt.
    #
    # The *note* is only raised for a car that is somewhere else. Craig and
    # Landreth's lot is 240 cars in Louisville and 246 between Clarksville and
    # Bullitt County, so a note on every row would have the assistant announce
    # the store it is standing in on every reply -- which is noise, and noise
    # is how the one row that mattered stops being read.
    if raw.get("location"):
        payload["location"] = raw["location"]
        if home and raw["location"].lower() not in home:
            payload["location_note"] = (
                f"This one is at the {raw['location']} store, not the address above. "
                "Say so before offering a time, and check with a person that it can "
                "be seen there."
            )
    # The dealer's own vehicle history report -- a Carfax link, in every export
    # seen so far. We cannot fetch it (their provider will not serve a server),
    # but the buyer's browser can, so the link *is* the answer to "is there a
    # Carfax?": it goes on the car's card and the prompt says to point at it
    # rather than hand the question to a colleague. On a search too, because
    # it is one short string and the history question arrives about any car.
    history = history_url(raw)
    if history:
        payload["history_url"] = history
    link = inquiry_url(v)
    if link:
        payload["inquiry_url"] = link
        # Its own key rather than `price_note`, which `rule_hold_price` below
        # already owns. Two notes writing to one field means whichever runs
        # last silently wins, and the one that loses is a rule somebody set.
        payload["no_price_note"] = (
            "No price is published for this one, so do not quote or estimate a figure, "
            "and do not read one off another car. The buyer can ask the dealership "
            "through the form on its listing -- point at that link rather than reading "
            "a URL out, and never say a URL on a call. Booking a visit comes first: "
            "the price is a person's answer."
        )
    # **What the dealership has written down about this one car**: its full
    # specification, its vehicle history report, what its service record does
    # and does not cover. Prose rather than a list, because that is what a
    # history report is -- `features` is the options list and stays a list.
    #
    # `full` only, so a search never carries it. One car's report is a page of
    # text and five of them would sit in the conversation for every later turn,
    # which is the same reasoning that cuts the options list down in a search.
    #
    # The note is the load-bearing half. Everything this codebase does about
    # invented facts assumes the model answers from a tool result and nothing
    # else, and handing it a page of prose is the one place that could slip --
    # so it is told the boundary explicitly rather than left to infer it.
    if full and raw.get("detail_doc"):
        payload["detail"] = raw["detail_doc"]
        payload["detail_note"] = (
            "The dealership's own written detail for this vehicle, including its "
            "history report. Answer from it and quote it; never go beyond it, and "
            "never fill a gap in it from what you know about the model in general. "
            "It is in front of you, so never tell the buyer you cannot retrieve, "
            "access or provide it -- give it in as much detail as they ask. "
            "Anything it marks as not confirmed must not be stated as fact -- say "
            "the listing mentions it and that a colleague will confirm. A question "
            "it does not cover is one for a colleague too."
        )
    if v.rule_hold_price:
        payload["price_note"] = "This price is firm. Do not suggest it is negotiable."
    if v.rule_mention_warranty:
        payload["warranty_note"] = (
            "Mention the 90-day/4,000-mile limited powertrain warranty on this vehicle."
        )
    if v.rule_note:
        payload["internal_note"] = v.rule_note
    return payload


def _record_mentions(db: Session, conversation_id: str, vehicles: list[Vehicle]) -> None:
    for v in vehicles:
        db.add(VehicleMention(
            conversation_id=conversation_id, vehicle_id=v.id, quoted_price=v.price
        ))
    db.commit()


def offerable(query):
    """Narrow a Vehicle query to the cars a buyer may be shown, anywhere.

    Two conditions and one rule: a car that is sold or off the lot cannot be
    offered, and a do-not-discuss car never reaches the buyer at all -- it is
    filtered in the executor rather than requested in a prompt, because a
    prompt is a request and an executor is a guarantee.

    Shared rather than repeated because the buyer now has a second surface.
    The showroom page renders cars from the same table, and two copies of this
    predicate is exactly how the consignment vehicle Liner refuses to discuss
    ends up on the page beside the chat window that will not discuss it.
    """
    return query.filter(
        Vehicle.status == "available",
        Vehicle.rule_discuss.is_(True),
    )


def _words(text: str) -> list[str]:
    """Split on anything that is not a letter or a digit, never on spaces.

    Splitting on spaces glues the punctuation to the word: "Do you have a BMW
    X5?" produced the token `x5?`, which matches nothing, so the most natural
    phrasing a buyer could possibly use returned the three cheapest cars on the
    lot instead of the car they named. "BMW X5" and "tell me about the BMW X5"
    both worked, which is what made it invisible for so long.

    A lone *letter* is dropped and a lone *digit* is kept, which is not a
    stylistic distinction. "Do you have a chevy Trax?" contains the word "a",
    which is a whole-word match against every A-Class and A-Spec on the lot, so
    two cars nobody asked about outranked the one that was named. No car is
    called "a". Several are called 3 and 5.
    """
    return [
        w for w in re.split(r"[^a-z0-9]+", (text or "").lower())
        if w and (len(w) > 1 or w.isdigit())
    ]


# What buyers call a make, mapped onto what a listing calls it. Curated for the
# same reason ORIGIN_BY_MAKE is: there is no column for it and guessing gets it
# wrong. "Chevy" is the one that earns this table on its own -- Craig and
# Landreth carry 74 Chevrolets and nobody in Louisville types "Chevrolet".
MAKE_NICKNAMES = {
    "chevy": "chevrolet", "chev": "chevrolet",
    "vw": "volkswagen", "merc": "mercedes", "benz": "mercedes",
    "bimmer": "bmw", "beemer": "bmw", "vette": "corvette",
    "caddy": "cadillac", "landrover": "rover", "range": "rover",
}


def _hits(word: str, haystack: list[str]) -> bool:
    """Does one search word match this car? Whole words, plus a plural.

    **Whole words, because a substring match scores the sentence rather than
    the car.** The haystack used to be one string and the test was `word in
    haystack`, which is fine on fourteen hand-written rows and wrong on a real
    lot: "do" is inside "Dodge", and "you" is inside "Bayou", so "do you have
    any corvettes?" ranked a Dodge Hornet in Blu Bayou first and never reached
    a Corvette. The buyer's own filler words were doing the sorting.

    **And a plural, because "corvettes" is what a buyer types.** Nothing on a
    listing is plural, so `corvettes` scored zero against every row on the lot
    and the search fell through to the cheapest five -- the same shape of
    failure as `x5?` above, and the same reason: the natural phrasing is the
    one that has to work. Only a trailing `s`, and only on a word long enough
    that dropping it still leaves something (`gts` stays `gts`).

    **And a nickname, because nobody says Chevrolet.** See MAKE_NICKNAMES.
    """
    if word in haystack:
        return True
    if MAKE_NICKNAMES.get(word) in haystack:
        return True
    return len(word) > 4 and word.endswith("s") and word[:-1] in haystack


def search_inventory(db: Session, convo: Conversation, args: dict) -> dict:
    query = offerable(db.query(Vehicle))
    if args.get("max_price"):
        query = query.filter(Vehicle.price <= int(args["max_price"]))
    if args.get("min_price"):
        query = query.filter(Vehicle.price >= int(args["min_price"]))
    if args.get("min_year"):
        query = query.filter(Vehicle.year >= int(args["min_year"]))
    if args.get("body_style"):
        query = query.filter(Vehicle.body_style.ilike(f"%{args['body_style']}%"))
    if args.get("min_seats"):
        query = query.filter(Vehicle.seats >= int(args["min_seats"]))
    if args.get("max_mileage"):
        query = query.filter(Vehicle.mileage <= int(args["max_mileage"]))
    if args.get("min_mileage"):
        query = query.filter(Vehicle.mileage >= int(args["min_mileage"]))

    # Cheapest first, but an unpriced car is not the cheapest car. SQLite sorts
    # NULL before every number, so on a lot with 119 call-for-price listings a
    # plain `price.asc()` put all five results of "what have you got?" on cars
    # with no price on them -- every one of which the assistant then has to
    # refuse to quote. `is_(None)` sorts False before True, so they fall to the
    # end and are reached only when nothing priced fits.
    rows = query.order_by(Vehicle.price.is_(None), Vehicle.price.asc()).all()

    # Origin is not a column, so it filters in Python after the SQL narrows.
    wanted_origin = ORIGIN_ALIASES.get((args.get("origin") or "").strip().lower(), "")
    if wanted_origin:
        rows = [v for v in rows if _origin_matches(v.make, wanted_origin)]

    # Both sides are tokenised the same way, and neither is tokenised on
    # spaces -- see `_words` and `_hits` for the two bugs that costs.
    keywords = _words(args.get("keywords") or "")
    if keywords:
        def score(v: Vehicle) -> int:
            # The year and the options list are in the haystack too: "2018
            # Audi Q7" names a year, and "third row" or "tow package" is an
            # options line on the cars that have one and nowhere else.
            options = " ".join(json.loads(v.features_json or "[]"))
            haystack = _words(
                f"{v.year} {v.keywords} {v.make} {v.model} {v.trim} {v.body_style} {options}"
            )
            return sum(1 for k in keywords if _hits(k, haystack))

        scored = [(score(v), v) for v in rows]
        best = max((s for s, _ in scored), default=0)
        if best:
            # **Only the rows that match the most of what was named.** "Do
            # you have a BMW X1?" scored the X1 at two and every other BMW at
            # one, and the buyer who asked about one car was shown three -- the
            # X1 and two 3-Series they had not mentioned. A row that matches
            # less of the question than another row is not an answer to it.
            # "BMW" alone still returns every BMW, since they all tie; a
            # search that names nothing on the lot falls through to price
            # order below, exactly as before.
            rows = [v for s, v in scored if s == best]

    rows = rows[:MAX_RESULTS]
    _record_mentions(db, convo.id, rows)
    if not rows:
        # Liner was escalating "do you have anything German?" to a human rather
        # than saying no. Nothing is a complete, correct answer, and it is the
        # one a buyer can act on; the guidance says so because the model's
        # instinct is to treat an empty hand as uncertainty.
        return {
            "count": 0,
            "vehicles": [],
            "guidance": (
                "Nothing on the lot matches. Say so plainly, in your own words -- this "
                "is a certain answer, not a gap in your knowledge, so do not hand it to "
                "a person. Then offer the nearest thing: drop one filter and search "
                "again, or ask what matters most to them."
            ),
        }
    home = home_location(db)
    out = {
        "count": len(rows),
        "vehicles": [_vehicle_payload(v, home, full=False) for v in rows],
    }
    if any(len(json.loads(v.features_json or "[]")) > SEARCH_FEATURES for v in rows):
        out["note"] = (
            f"Each car's `features` is cut to the first {SEARCH_FEATURES} lines here. "
            "For any equipment question -- seats, rows, sunroof, tow package -- call "
            "get_vehicle, which returns the whole list."
        )
    return out


def get_vehicle(db: Session, convo: Conversation, args: dict) -> dict:
    vin = (args.get("vin") or "").upper().strip()
    vehicle = (
        db.query(Vehicle)
        .filter(Vehicle.vin == vin, Vehicle.rule_discuss.is_(True))
        .one_or_none()
    )
    if vehicle is None:
        raise ToolError(f"No vehicle available with VIN {vin}.")
    _record_mentions(db, convo.id, [vehicle])
    convo.focus_vehicle_id = vehicle.id
    db.commit()
    return _vehicle_payload(vehicle, home_location(db))


def _dealership_hours(db: Session) -> dict:
    from app.models import Dealership

    dealership = db.query(Dealership).first()
    return json.loads(dealership.hours_json or "{}") if dealership else {}


DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _remember_old_email(db: Session, lead: Lead) -> None:
    """Park the address being replaced on the lead as a captured field.

    Provenance is 'typed' because the buyer really did type it, on an earlier
    visit -- it is a fact about them, not a guess. Overwriting the column
    without this would lose the only address a rep had for someone whose new
    one bounces.
    """
    from app.models import CapturedField

    row = (
        db.query(CapturedField)
        .filter_by(lead_id=lead.id, key="previous_email")
        .one_or_none()
    )
    if row is None:
        db.add(CapturedField(
            lead_id=lead.id, key="previous_email", value=lead.email, provenance="typed",
        ))
    else:
        row.value = lead.email


def clock_label(when: datetime) -> str:
    """"10:00 AM" -- no leading zero, and no %-I, which is glibc-only."""
    return f"{(when.hour % 12) or 12}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"


def when_label(when: datetime) -> str:
    """"Tuesday 12 August at 10:00 AM". Naive datetimes here are dealership-local
    already, so this formats the frame it is given and converts nothing."""
    return f"{when:%A} {when.day} {when:%B} at {clock_label(when)}"


def check_availability(db: Session, convo: Conversation, args: dict) -> dict:
    """Open slots from the calendar and the dealership's hours. 8 AM - 8 PM,
    closed Sunday -- read from the config row, never hardcoded."""
    from app.api.settings import live_settings

    hours = _dealership_hours(db)
    slot_len = live_settings(db).booking_slot_length
    days_ahead = int(args.get("days_ahead") or 7)
    period = args.get("preferred_period") or "any"

    now = utcnow()
    taken = {
        a.starts_at.replace(second=0, microsecond=0)
        for a in db.query(Appointment)
        .filter(Appointment.starts_at >= now, Appointment.status.in_(["booked", "confirmed"]))
        .all()
    }

    windows = {"morning": (8, 12), "afternoon": (12, 17), "evening": (17, 20), "any": (8, 20)}
    lo, hi = windows.get(period, windows["any"])

    slots: list[str] = []
    for day_offset in range(1, days_ahead + 1):
        day = (now + timedelta(days=day_offset)).replace(minute=0, second=0, microsecond=0)
        window = hours.get(DAY_NAMES[day.weekday()])
        if not window:
            continue  # closed
        open_h = int(window["open"].split(":")[0])
        close_h = int(window["close"].split(":")[0])
        start_h, end_h = max(open_h, lo), min(close_h, hi)

        # Cap per day so the result spans the week. Twelve consecutive
        # half-hours on one morning is one option dressed up as twelve, and
        # the caller needs two genuinely different times to offer.
        cursor = day.replace(hour=start_h)
        per_day = 0
        while cursor.hour < end_h and per_day < 3:
            if cursor > now and cursor.replace(second=0, microsecond=0) not in taken:
                slots.append(cursor.isoformat())
                per_day += 1
            cursor += timedelta(hours=3)
        if len(slots) >= 12:
            break

    # Whether we already know who this is, and how to ring them.
    #
    # **Offering times to somebody we cannot contact is the wrong order.** A
    # buyer who picks a slot and then abandons the form has cost us the one
    # thing worth having; a buyer who has already given a name and a number is
    # a lead whatever happens next. So the name and the number come first, and
    # this says whether they have. It is a note rather than a refusal -- "are
    # you open Saturday?" deserves an answer, and the booking card is where
    # this stops being a request and becomes a rule, since it will not submit
    # without them.
    known = contact_on(db, convo)
    result = {
        "slot_minutes": slot_len,
        "slots": slots[:12],
        "contact_known": bool(known["name"] and known["phone"]),
    }
    if not result["contact_known"]:
        result["note"] = (
            "You do not have a name and a phone number for this buyer yet. Get those "
            "first -- on a screen call request_details, on a call ask out loud -- and "
            "offer times once you have them."
        )
    return result


def contact_on(db: Session, convo: Conversation) -> dict[str, str]:
    """What is already on file for whoever this conversation belongs to.

    Read from `leads` rather than from the transcript: the columns are what a
    rep will actually ring, and `attach_lead` is the only thing that writes
    them. Empty strings rather than None, because this is serialised straight
    onto a card and a JSON null in a text input is the string "null".
    """
    lead = db.query(Lead).filter_by(id=convo.lead_id).one_or_none() if convo.lead_id else None
    if lead is None:
        return {"name": "", "email": "", "phone": ""}
    return {
        "name": lead.name or "",
        "email": lead.email or "",
        "phone": lead.phone or "",
    }


def attach_lead(
    db: Session, convo: Conversation, *, name: str, email: str, phone: str
) -> Lead:
    """Put a buyer behind this conversation, matching an existing one first.

    Extracted from `book_appointment` when the details card arrived, because
    that card also mints a buyer -- and two copies of "who is this person" is
    exactly what `app/matching.py` exists to stop. Booking is unchanged: it
    still passes an email it has already required.

    A conversation that already has a lead keeps it. What is new is folded in
    rather than overwriting: a blank is filled, and a *changed* email is taken
    while the old one is kept as a captured field, since a buyer correcting a
    typo means it and a shared family address is a real thing.
    """
    lead = db.query(Lead).filter_by(id=convo.lead_id).one_or_none() if convo.lead_id else None
    if lead is None:
        # The same matcher the ADF importer uses, so email *and* phone both
        # identify a returning buyer. Matching on email alone meant someone who
        # booked from chat and called back leaving a second address arrived as
        # a second lead, with the number on file identical on both rows.
        lead = match_lead(db, email, phone)
    if lead is None:
        lead = Lead(name=name, email=email, phone=phone,
                    source="voice" if convo.channel == "voice" else "chat")
        db.add(lead)
        db.flush()
    else:
        lead.name = lead.name or name
        lead.phone = lead.phone or phone
        # `lead.email or email` silently threw the typed address away. The form
        # asks "Where should the confirmation go?", so a buyer correcting a
        # typo means it -- and the confirmation was going to the old address.
        # The previous one is kept rather than lost: a shared family address is
        # a real thing and a rep may need it back.
        if email and lead.email and lead.email != email:
            _remember_old_email(db, lead)
        lead.email = email or lead.email
    # The buyer gave the address for this purpose -- that consent is the
    # record. Only where they actually gave one: the details card takes a phone
    # number with the email optional, and stamping consent for an address
    # nobody offered is a permission we were never given.
    if lead.email:
        lead.email_consent_at = lead.email_consent_at or utcnow()
    convo.lead_id = lead.id
    # Anything they wrote to us before they were anybody. Runs on an existing
    # lead too, not only a new one: this is also the moment a buyer's address
    # is first learnt or corrected, and mail sat unplaced against exactly that.
    matching.claim_unresolved(db, lead)
    # And the same ladder for texts, which the email one cannot do: a text
    # carries no address, so it resolves on the phone rung alone.
    from app import sms as sms_module

    sms_module.claim_unresolved(db, lead)
    return lead


def book_appointment(
    db: Session, convo: Conversation, args: dict, tool_call_id: str | None = None
) -> dict:
    # Idempotent: a retried turn must not produce two appointments.
    #
    # Live rows only. The card's call id is deterministic on the slot
    # (`form-<conversation>-<time>`), so a buyer who booked Tuesday at ten,
    # cancelled, and asked for Tuesday at ten again matched the cancelled row
    # and got it handed back as `already_booked`. The card said booked, the
    # calendar showed a cancellation, and nothing had been booked at all.
    if tool_call_id:
        existing = (
            db.query(Appointment)
            .filter(
                Appointment.conversation_id == convo.id,
                Appointment.tool_call_id == tool_call_id,
                Appointment.status.in_(["booked", "confirmed"]),
            )
            .first()
        )
        if existing is not None:
            return {"appointment_id": existing.id, "starts_at": existing.starts_at.isoformat(),
                    "already_booked": True}

    name = (args.get("name") or "").strip()
    email = (args.get("email") or "").strip().lower()
    phone = (args.get("phone") or "").strip()

    # A name and a way to reach them, and the way is a phone number first.
    #
    # **This used to require an email and take the phone as optional, and that
    # was the wrong way round.** An address cannot be answered at five past six
    # on a Friday and a number can be rung, which is the same reasoning the
    # details card already followed -- it took the number and left email
    # optional while booking insisted on the opposite, so the two cards asked a
    # buyer for different things to do the same job. Worse, on a call an email
    # has to be spelled out letter by letter and mis-heard, and a booking that
    # could not proceed without one was a booking lost to a transcription
    # error.
    #
    # Email is still taken and still validated where it is given: it is how a
    # written confirmation goes out, which is why it is asked for straight
    # after the time is set rather than not at all.
    if not name:
        raise ToolError("A name is required to book.")
    if email and not EMAIL_RE.match(email):
        raise ToolError(
            f"'{email}' is not a valid email address. Read it back to the buyer and "
            "check it, or leave it out -- the phone number is what a rep will use."
        )
    if not phone and not email:
        raise ToolError(
            "A booking needs a way to reach them. Ask for a phone number -- somebody "
            "here can ring it -- and take an email instead only if they would rather "
            "not give one."
        )

    try:
        starts_at = datetime.fromisoformat(str(args["starts_at"]).replace("Z", ""))
    except (KeyError, ValueError) as exc:
        raise ToolError("starts_at must be an ISO 8601 datetime.") from exc
    if starts_at.tzinfo is not None:
        starts_at = starts_at.replace(tzinfo=None)

    hours = _dealership_hours(db)
    window = hours.get(DAY_NAMES[starts_at.weekday()])
    if not window:
        raise ToolError(f"We are closed on {DAY_NAMES[starts_at.weekday()].title()}.")
    if not (int(window["open"][:2]) <= starts_at.hour < int(window["close"][:2])):
        raise ToolError(
            f"That is outside our hours ({window['open']} to {window['close']})."
        )

    # Nothing here checked the slot was still free. check_availability filters
    # taken slots, but that answer ages: a buyer looking at a picked time on a
    # booking card can sit on it for minutes, and the model can offer a time it
    # read several turns ago. Two buyers then get the same 10 AM and one of
    # them turns up to nobody. The executor is the guarantee, so it checks.
    clash = (
        db.query(Appointment)
        .filter(
            Appointment.starts_at == starts_at,
            Appointment.status.in_(["booked", "confirmed"]),
        )
        .first()
    )
    if clash is not None:
        raise ToolError(
            f"{when_label(starts_at)} was taken while you were deciding. "
            "Call check_availability again and offer what is still open."
        )

    lead = attach_lead(db, convo, name=name, email=email, phone=phone)

    vehicle = None
    if args.get("vin"):
        vehicle = db.query(Vehicle).filter_by(vin=str(args["vin"]).upper()).one_or_none()
    elif convo.focus_vehicle_id:
        vehicle = db.query(Vehicle).filter_by(id=convo.focus_vehicle_id).one_or_none()

    from app.api.settings import live_settings

    appointment = Appointment(
        lead_id=lead.id,
        vehicle_id=vehicle.id if vehicle else None,
        starts_at=starts_at,
        duration_min=live_settings(db).booking_slot_length,
        status="booked",
        # Who made it. A rep booking on the phone is not Liner booking, and
        # the overview counts them the same but the calendar should not.
        booked_by=str(args.get("booked_by") or "liner"),
        conversation_id=convo.id,
        tool_call_id=tool_call_id,
    )
    db.add(appointment)
    convo.stage = "booked"
    db.commit()
    db.refresh(appointment)

    emit(db, "appointment.booked", {
        "appointment_id": appointment.id,
        "lead_id": lead.id,
        "lead_name": lead.name,
        "conversation_id": convo.id,
        "vehicle_id": vehicle.id if vehicle else None,
        "starts_at": appointment.starts_at.isoformat(),
    })
    return {
        "appointment_id": appointment.id,
        "starts_at": appointment.starts_at.isoformat(),
        "duration_min": appointment.duration_min,
        "vehicle": _vehicle_payload(vehicle) if vehicle else None,
        "lead_id": lead.id,
    }


def request_details(db: Session, convo: Conversation, args: dict) -> dict:
    """Put the details card on the buyer's screen. Writes nothing by itself.

    The card is the whole result -- what comes back is what the browser draws,
    so it can only ever ask for what this returned. Nothing is saved until the
    buyer presses the button, which goes through
    `POST /api/chat/sessions/{id}/details` and lands on the same executors any
    other caller uses.

    **Chat only.** A card is a thing on a screen, and a caller has none: on a
    call this refuses rather than silently succeeding, because a model told a
    tool worked will say "I've popped a form up for you" to somebody holding a
    phone. Voice takes the number by ear, which its addendum already covers.
    """
    if convo.channel == "voice":
        raise ToolError(
            "There is no screen on a call, so there is no card to show. Ask for "
            "the number out loud and read it back to check it."
        )
    # **Once.** The card is replayed on refresh while it is unanswered, so a
    # second one is the same question twice on one screen -- and a buyer who
    # has left the boxes empty has answered, in their way. Asked again, the
    # model gets told rather than given a second card, and the sentence it
    # writes is the persuasion the prompt asks for instead of a repeat.
    if details_pending(db, convo):
        return {
            "already_asked": True,
            "note": (
                "The contact form is already on the buyer's screen, unanswered, and "
                "it stays at the bottom -- under whatever you write next. No second "
                "one is drawn. If it helps, say in one line why filling it in gets "
                "them what they asked for, then answer anything else they asked -- "
                "and do not repeat what the record cannot tell them."
            ),
        }
    card = details.card(args.get("fields"), args.get("reason") or "")
    return {
        **card,
        # Said in the result rather than only in the prompt, because this is
        # what the model is looking at when it writes the sentence that goes
        # with the card. The booking card learnt the same lesson: reply text
        # that lists the times as well gets the question answered in the worse
        # place, and here it reads as being asked twice for a phone number.
        "note": (
            "The contact form is on the buyer's screen now. Say what it is for in "
            "one line and stop -- do not ask for any of these fields in your reply."
        ),
    }


def details_pending(db: Session, convo: Conversation) -> bool:
    """Is a details card on the buyer's screen with nothing typed into it yet?

    Read off the transcript, the same way the resume path decides whether to
    redraw the card: the newest of `request_details` and `save_details` in
    the thread's tool calls decides. One reading here and one in
    `GET /api/chat/sessions/{id}` would drift, so this is the reading.
    """
    pending = False
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == convo.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    for row in rows:
        for call in json.loads(row.tool_calls_json or "[]"):
            # **A card is what a result carries, not what a tool is called.**
            # `escalate_to_human` draws one itself when a handoff would leave
            # nobody reachable, so reading the name here would have missed it
            # and offered the buyer a second card over the first.
            if (call.get("result") or {}).get("fields"):
                pending = True
            elif call.get("name") == "save_details":
                pending = False
    return pending


def save_details(db: Session, convo: Conversation, values: dict) -> dict:
    """What the buyer typed into the card. The other half of `request_details`.

    Not a tool the model can call -- it is the card's submit, the same shape as
    `book_appointment` behind the booking card. It does two things and reuses
    the existing path for both: `attach_lead` puts a buyer behind the
    conversation (the one matcher, so a returning caller is not minted twice),
    and `save_captured_fields` writes the answers.

    Going through `save_captured_fields` rather than writing rows directly is
    the point. Provenance is enforced there, against the buyer's own messages
    -- and the submission is written into the transcript as the buyer's message
    first, so `typed` is accepted because it is *true*, not because this path
    was allowed to skip the check.
    """
    keep = {
        key: str(value or "").strip()
        for key, value in (values or {}).items()
        if key in details.FIELDS and str(value or "").strip()
    }
    for required in details.REQUIRED_KEYS:
        if not keep.get(required):
            raise ToolError(f"{details.FIELDS[required].label} is required.")

    email = keep.get("email", "").lower()
    if email and not EMAIL_RE.match(email):
        raise ToolError("That email address does not look right.")

    lead = attach_lead(
        db, convo,
        name=keep.get("name", ""), email=email, phone=keep.get(details.PHONE_KEY, ""),
    )
    db.commit()

    # The contact columns live on `leads` and are already written above; the
    # rest are captured fields. Storing a phone number in both places would be
    # two answers to one question, and `app/matching.py` reads the column.
    rest = [
        {"key": key, "value": value, "provenance": "typed"}
        for key, value in keep.items()
        if key not in CONTACT_KEYS
    ]
    saved = save_captured_fields(db, convo, {"fields": rest}) if rest else {"saved": []}
    emit(db, "lead.qualified", {
        "lead_id": lead.id, "conversation_id": convo.id, "fields": sorted(keep),
    })
    return {"lead_id": lead.id, "given": sorted(keep), **saved}


def save_captured_fields(db: Session, convo: Conversation, args: dict) -> dict:
    """Provenance is enforced, not requested.

    'typed' is only accepted when the value appears in something the buyer
    actually wrote in this conversation. Anything else is downgraded to
    'inferred' and the rejection is reported back, so the model learns the
    boundary within the turn.
    """
    # **A number said out loud has to land on the row, or it was never taken.**
    #
    # This wrote `captured_fields` and nothing else, and refused outright
    # without a lead -- which on a call is every turn before the booking, since
    # `attach_lead` is what mints one and there is no card to run it. So a
    # caller who gave their number, heard it read back and said yes had it
    # recorded nowhere a tool could see: `contact_on` stayed empty,
    # `check_availability` answered *you do not have a name and a phone number
    # for this buyer yet*, and the assistant obediently asked again. A real
    # call asked three times and confirmed the same digits twice.
    #
    # So the three contact keys go through `attach_lead` -- the same single
    # writer the details card uses, minting the buyer where there is none. Not
    # a second copy of "who is this person": that is what `app/matching.py`
    # exists to prevent.
    given = [
        (key, (field.get("value") or "").strip(), field)
        for field in args.get("fields", [])
        for key in [(field.get("key") or "").strip()]
    ]
    # **Only what the buyer actually gave.** `typed` is a value they said in
    # their own words and `caller_id` is one the network handed us; either can
    # go on the row a rep rings. An `inferred` name -- the guess taken off an
    # email signature -- must not: it stays a captured field wearing its
    # provenance, which is the only thing standing between a guess and a rep
    # asserting it on the phone.
    contact = {
        key: value
        for key, value, field in given
        if key in CONTACT_KEYS and value
        and (field.get("provenance") or "") in {"typed", "caller_id"}
    }

    # Both are heard rather than read, which is where the mis-hearing is. A
    # spelled-out address is the other one: `josh at example dot com` arrives
    # however the transcriber heard it.
    if contact.get(details.PHONE_KEY) and not matching.diallable(contact[details.PHONE_KEY]):
        raise ToolError(
            f"{contact[details.PHONE_KEY]!r} is not a number anybody can ring -- a "
            "phone number here is ten digits. On a call that is a mis-heard digit, so "
            "read back what you have, ask them to say it again slowly, and save it "
            "once it is ten digits. Do not treat it as confirmed."
        )
    if contact.get("email") and not EMAIL_RE.match(contact["email"].lower()):
        raise ToolError(
            f"{contact['email']!r} is not an address that would reach anybody. Ask "
            "them to spell it again and read it back before you save it."
        )
    if contact:
        attach_lead(
            db, convo,
            name=contact.get("name", ""),
            email=contact.get("email", "").lower(),
            phone=contact.get(details.PHONE_KEY, ""),
        )
        db.commit()

    if not convo.lead_id:
        raise ToolError("No lead on this conversation yet; book or capture contact first.")

    buyer_text = " ".join(
        m.content.lower()
        for m in db.query(Message)
        .filter_by(conversation_id=convo.id, role="buyer")
        .all()
    )

    from app.models import CapturedField

    saved, downgraded = [], []
    for key, value, field in given:
        provenance = (field.get("provenance") or "inferred").strip()
        if not key or not value:
            continue
        # A contact value that went onto the row above is not also a captured
        # field: that would be two answers to one question, and
        # `app/matching.py` reads the column -- the rule `save_details` already
        # follows. A *guess* at one is the other way round and falls through to
        # be stored here, because the provenance is the point of keeping it.
        if key in CONTACT_KEYS and key in contact:
            continue
        if provenance not in {"typed", "listing", "caller_id", "inferred"}:
            provenance = "inferred"

        if provenance == "typed" and value.lower() not in buyer_text:
            provenance = "inferred"
            downgraded.append(key)

        row = (
            db.query(CapturedField)
            .filter_by(lead_id=convo.lead_id, key=key)
            .one_or_none()
        )
        if row is None:
            row = CapturedField(lead_id=convo.lead_id, key=key, value=value,
                                provenance=provenance)
            db.add(row)
        else:
            row.value = value
            row.provenance = provenance
        saved.append({"key": key, "value": value, "provenance": provenance})

    db.commit()
    emit(db, "lead.qualified", {
        "lead_id": convo.lead_id, "conversation_id": convo.id, "fields": [s["key"] for s in saved],
    })

    result: dict[str, Any] = {"saved": saved}
    if contact:
        # Said back, so the model can see the number is now on file and stop
        # asking for it. That is the whole failure this branch exists for: the
        # assistant asked, heard, confirmed -- and then a tool told it there was
        # no number, because nothing had written one anywhere.
        result["contact"] = contact_on(db, convo)
        result["note"] = (
            "That is on the buyer's record now, so nothing will ask you for it again. "
            "Do not read it back a second time."
        )
    if downgraded:
        result["rejected_typed"] = downgraded
        result["note"] = (
            "Those fields were recorded as 'inferred' because the buyer did not say them "
            "in those words. Only use 'typed' for values quoted from a buyer message."
        )
    return result


def close_conversation(
    db: Session, convo: Conversation, args: dict, tool_call_id: str | None = None
) -> dict:
    """The buyer said they were done. Write the summary, optionally email it.

    Idempotent on tool_call_id like the other side-effecting tools: a retried
    turn must not send a second summary.
    """
    summary = (args.get("summary") or "").strip()
    if not summary:
        raise ToolError("close_conversation needs a summary of what happened.")

    if convo.ended_at is not None:
        return {"closed": True, "already_closed": True, "summary": convo.summary}

    # **One last ask before the door closes.**
    #
    # The moment a buyer says they are done is the last moment there is, and a
    # conversation that ends with nobody able to ring them is worth nothing to
    # the floor however well it went. So if there is no number on file, this
    # refuses and sends the assistant back to ask -- which is the operator's
    # rule, and a rule a prompt can only request.
    #
    # **Exactly once, and that is why there is a row for it.** A buyer who says
    # "no thanks, goodbye" and is asked again, and again, cannot leave; that is
    # worse than never asking. `conversation_once.claim` is the one chance, and
    # taking it is what spends it -- so the second call goes through whatever
    # the buyer answered.
    if not contact_on(db, convo)["phone"] and conversation_once.claim(
        db, convo, conversation_once.ASKED_FOR_CONTACT
    ):
        raise ToolError(
            "Before you sign off: nobody here has a way to ring this buyer. "
            + (
                "Ask for their number out loud, read it back, and save it."
                if convo.channel == "voice" else
                "Call request_details for their name and number."
            )
            + " Say what it is for in one line. If they would rather not, that is "
            "fine -- close the conversation on the next turn and it will go through."
        )

    convo.summary = summary
    convo.ended_at = utcnow()
    # 'closed' only if nobody is waiting on it. An escalated thread stays in the
    # dealer's queue even though the buyer has gone -- that is the whole point
    # of the escalation, and a rep still owes them a call back.
    if convo.status != "handoff":
        convo.status = "closed"
    db.commit()

    result = {"closed": True, "summary": summary, "emailed": False}

    # **Nobody is offered a summary, and by default none is sent.** Every call
    # ended on *"would you like a quick summary sent over email before we
    # finish?"* -- a question after the buyer had already said goodbye, about a
    # thing they had not asked for, on the one turn that should be four words
    # long. The offer is gone from this tool's description, so the only way
    # `send_summary` arrives now is a buyer asking for one unprompted.
    #
    # `BUYER_SUMMARY_EMAIL` is off, so even that sends nothing until a
    # deployment turns it on. A switch rather than a deletion because the
    # composer is real and works; a deployment that wants it has one line to
    # change, and the reason it is off is that nobody asked for the mail.
    if not args.get("send_summary"):
        return result
    from app.config import settings

    if not settings.buyer_summary_email:
        result["note"] = (
            "Summary emails are switched off on this deployment, so nothing was sent. "
            "Do not tell the buyer one is coming."
        )
        return result

    lead = db.query(Lead).filter_by(id=convo.lead_id).one_or_none() if convo.lead_id else None
    if lead is None or not lead.email:
        result["note"] = (
            "They asked for it by email but we have no address on file. Ask for one, "
            "then call this again."
        )
        return result

    # Composed from rows, not the `summary` argument above.
    #
    # That argument is a model-written sentence and it used to be the whole
    # email. A real call mailed: "John Doe is all set with an appointment
    # tomorrow at 11 AM ... A summary will be sent to john@outlook.com." Which
    # summarises nothing, is written about the reader in the third person, and
    # tells them that what they are holding is about to be sent to them.
    #
    # The rail already decided this: a model-written summary is a second place
    # a fact can be invented. It matters more here, because this is the copy
    # the buyer keeps and reads back to a rep.
    from app import email_outbound
    from app.recap import buyer_summary

    dealership = db.query(Dealership).first()
    # **Sent, through the one path every other email takes.** This used to
    # write an `outreach` row marked `queued` and stop there -- nothing in the
    # system ever drains a queued row -- while telling the model `emailed:
    # True`. So a deployment that switched summaries on had the assistant
    # promising mail that was never sent, which is the exact failure this
    # switch was written to prevent. Now the outbound limit, the reply route
    # home and the From are the same as any send's, and `emailed` says what
    # happened.
    try:
        message = email_outbound.build(
            db, to=lead.email,
            subject=f"Your conversation with {dealership.name if dealership else 'us'}",
            # It ends with the dealership's name, address and phone already,
            # so the sign-off would be the same block again.
            body=buyer_summary(db, convo),
            # Nobody here wrote it: RFC 3834, so the buyer's own auto-responder
            # does not answer it.
            headers={"Auto-Submitted": "auto-generated"},
        )
    except email_outbound.OutboundError as exc:
        result["note"] = (
            f"Nothing was sent: {exc} Tell the buyer a colleague will follow up "
            "rather than saying an email is on its way."
        )
        return result
    sent = email_outbound.send(
        db, message, kind="summary", lead_id=lead.id,
        sent_by_user_id=None, conversation_id=convo.id,
    )
    result["to"] = lead.email
    if not sent.ok:
        result["note"] = (
            f"The summary email was not sent: {sent.detail} Tell the buyer a "
            "colleague will follow up rather than saying it is on its way."
        )
        return result

    # Delivery is the outbox unless a provider is configured, and the buyer was
    # just promised an email. Say which happened rather than reporting success.
    result["emailed"] = True
    result["delivered_externally"] = sent.sender.delivers
    if not sent.sender.delivers:
        result["note"] = (
            "Recorded, not delivered -- no email provider is configured. Tell the "
            "buyer a colleague will send it rather than saying it is on its way."
        )
    return result


def escalate_to_human(
    db: Session, convo: Conversation, args: dict, tool_call_id: str | None = None
) -> dict:
    if tool_call_id:
        existing = (
            db.query(Escalation)
            .filter_by(conversation_id=convo.id, tool_call_id=tool_call_id)
            .one_or_none()
        )
        if existing is not None:
            return {"escalation_id": existing.id, "already_escalated": True}

    # One open handoff per conversation. The tool_call_id check above only
    # makes a *retried* call idempotent; a second turn escalating for a second
    # reason used to add a second unclaimed row. That is not a second job for a
    # rep, it is the same conversation sitting in the queue twice -- and when a
    # guard misfired every turn it stacked up a row per message.
    open_row = (
        db.query(Escalation)
        .filter(Escalation.conversation_id == convo.id, Escalation.claimed_at.is_(None))
        .order_by(Escalation.created_at.asc())
        .first()
    )
    if open_row is not None:
        convo.status = "handoff"
        convo.stage = "escalated"
        db.commit()
        lead_now = (
            db.query(Lead).filter_by(id=convo.lead_id).one_or_none() if convo.lead_id else None
        )
        return {
            "escalation_id": open_row.id,
            "already_escalated": True,
            "guidance": (
                "A colleague already has this conversation in their queue; nothing "
                "new was raised. Do not tell the buyer again that somebody will "
                "confirm -- they heard it. Carry on with anything else you can "
                "answer."
            ),
            # Still nobody to ring, so the boxes go up here too -- the second
            # escalation of a conversation is the one where a number matters
            # most, and `contact_card` draws nothing when one is already on
            # screen or the buyer is already on file.
            **contact_card(db, convo, bool(lead_now and (lead_now.email or lead_now.phone))),
        }

    rule_key = args.get("rule_key") or ""
    rule = db.query(HandoffRule).filter_by(key=rule_key).one_or_none()
    if rule is not None and not rule.enabled:
        # The rule is switched off, so Liner keeps going instead of stopping.
        # That is the dealership's choice and the setup page warns about it.
        return {"escalated": False, "reason": f"The '{rule_key}' handoff rule is disabled."}

    escalation = Escalation(
        conversation_id=convo.id,
        handoff_rule_id=rule.id if rule else None,
        reason=(args.get("reason") or "").strip(),
        tool_call_id=tool_call_id,
    )
    # Born claimed when the buyer already has a rep. Otherwise a manager who
    # assigned somebody yesterday watches "Needs a person" reappear next to
    # that rep's name today, and the queue stops meaning "nobody has this".
    claimed_for = claim_for_owner(db, escalation, convo)
    db.add(escalation)
    convo.status = "handoff"
    # Deliberately NOT agent_paused. Escalating used to gag Liner immediately,
    # so a buyer who asked one question a human had to answer got "someone is
    # picking this up personally" to everything they said afterwards -- and
    # since nobody is watching the queue at 9pm, that was the end of the
    # conversation. Only a rep pressing Take over stops Liner
    # (api/conversations.py), because only then is a person actually there.
    convo.stage = "escalated"
    if rule is not None:
        rule.fired_count += 1
    db.commit()
    db.refresh(escalation)

    emit(db, "handoff.triggered", {
        "escalation_id": escalation.id,
        "conversation_id": convo.id,
        "rule_key": rule_key,
        "reason": escalation.reason,
        "notify": rule.notify if rule else "dashboard",
        # Named, so the notification says whose this is rather than raising a
        # queue entry nobody is looking at.
        "claimed_by_user_id": claimed_for,
    })

    # A handoff with no way to reach the buyer is a lost lead, not a handoff.
    lead = db.query(Lead).filter_by(id=convo.lead_id).one_or_none() if convo.lead_id else None
    reachable = bool(lead and (lead.email or lead.phone))
    guidance = (
        "A colleague has been notified. Keep talking to the buyer -- you are not "
        "finished and they are not on hold. Carry on with anything else you can "
        "answer yourself."
    )
    if not reachable:
        # Chat has a card for this and voice has a voice; neither is "ask for
        # an email in a sentence", which is what this said before -- and the
        # chat rules forbid exactly that, so the two instructions fought.
        guidance += (
            " We have no way to reach this buyer, so the contact form is on their "
            "screen now. Say in one line what it is for and stop."
            if convo.channel != "voice" else
            " We have no way to reach this buyer, so ask for their number out loud "
            "now and read it back to check it. Save it with save_captured_fields."
        )
    return {
        "escalation_id": escalation.id,
        "escalated": True,
        "rule_key": rule_key,
        "buyer_reachable": reachable,
        "guidance": guidance,
        **contact_card(db, convo, reachable),
    }


def contact_card(db: Session, convo: Conversation, reachable: bool) -> dict:
    """The details card, drawn by the escalation itself when nobody can be rung.

    **Raising a handoff and asking for a number are one act.** They were two
    calls, and a real conversation shows what that costs: the buyer asked
    whether a car had a third row, the listing did not say, and Liner said a
    colleague would confirm -- three times, over three turns, without ever
    asking how to reach them. Nobody could have confirmed anything. The buyer
    had to say *maybe you could reach out to me* before the boxes appeared.

    Telling the model harder does not fix it: the prompt already asked for
    `request_details` in that turn, and the second call is exactly the one a
    model drops. So the executor does it. An escalation that leaves nobody
    reachable now *is* the ask -- one tool call, both effects, which is the
    same reasoning that makes `close_conversation` refuse the first close on a
    buyer with no phone number.

    Nothing is drawn when there is no need: a buyer already on file, a card
    already unanswered on their screen, or a call, which has no screen at all.
    """
    if reachable or convo.channel == "voice" or details_pending(db, convo):
        return {}
    return details.card(
        None,
        "A colleague will confirm this for you -- leave a number and they will call.",
    )


def answer_from_knowledge(db: Session, convo: Conversation, args: dict) -> dict:
    """The dealership's own words on a policy question.

    These questions -- trade-ins, the doc fee, deposits -- need no inventory
    lookup and have exactly one right answer, which the dealer wrote. Without
    this the model either invents one (the guards then stop the reply and
    escalate, which is what "do you take trade-ins" was doing) or hands a
    trivial question to a human.

    Returning "no entry" is a real answer: it tells the model to escalate
    rather than fill the gap itself.
    """
    question = (args.get("question") or "").strip()
    if not question:
        raise ToolError("answer_from_knowledge needs the buyer's question.")

    entry = lookup_knowledge(db, question)
    if entry is None:
        return {
            "found": False,
            "topics": [e.topic for e in db.query(KnowledgeEntry).all()],
            "guidance": (
                "The dealership has no written answer to this. Do not compose one -- "
                "say a colleague will confirm, and escalate if it matters to the sale."
            ),
        }

    entry.use_count += 1
    db.commit()
    return {"found": True, "topic": entry.topic, "answer": entry.answer}


def _terms(text: str) -> set[str]:
    """Content words, with a trailing plural 's' stripped.

    Crude on purpose -- it only has to make "deposit" match the "Deposits"
    entry. A real stemmer is a dependency and a behaviour change for the sake
    of a seven-row table.
    """
    out = set()
    for word in re.findall(r"[a-z]+", text.lower()):
        if word in STOPWORDS or len(word) < 2:
            continue
        out.add(word[:-1] if len(word) > 3 and word.endswith("s") else word)
    return out


# Words that carry no topic signal. Filtering on length instead drops "doc",
# "fee", "tax" and "apr" -- the exact words a buyer uses for these questions --
# while keeping "your", which then matches almost every entry.
STOPWORDS = {
    "a", "about", "an", "and", "any", "anything", "are", "at", "back", "be", "buy",
    "can", "car", "could", "did", "do", "does", "for", "from", "get", "guys", "has",
    "have", "how", "i", "if", "in", "is", "it", "its", "just", "me", "much", "my",
    "of", "on", "or", "our", "so", "some", "take", "tell", "than", "that", "the",
    "their", "them", "then", "there", "they", "this", "to", "us", "want", "was",
    "we", "what", "whats", "when", "where", "which", "will", "with", "would",
    "you", "your", "yours",
}


def lookup_knowledge(db: Session, question: str) -> KnowledgeEntry | None:
    """Shared by the stub agent's knowledge rails and the tool above.

    A match on the *topic* is worth far more than a match anywhere in the
    answer: every answer mentions the dealership, so answer-body overlap alone
    picks a plausible-looking wrong entry. Asking about the doc fee and being
    told the deposit policy is worse than not answering, because the buyer
    repeats it to a rep.
    """
    words = _terms(question)
    if not words:
        return None

    best, best_score = None, 0.0
    for entry in db.query(KnowledgeEntry).all():
        topic_words = _terms(entry.topic)
        answer_words = _terms(entry.answer)
        score = 3.0 * len(words & topic_words) + len(words & answer_words)
        if score > best_score:
            best, best_score = entry, score
    # One incidental word in common is noise. Either the topic matched, or
    # several distinct words did.
    return best if best_score >= 3.0 else None


#: What the details and booking cards are for a finance application: the
#: result names the card, and the browser draws the button. The stream, the
#: rehydrate and `buyer_tool_calls` all key on this, never on the tool's name,
#: for the reason a details card is keyed on `fields`.
CREDIT_CARD = "credit_application"


def offer_credit_application(db: Session, convo: Conversation, args: dict) -> dict:
    """The dealership's finance application, as a button on the buyer's screen.

    **It was the one thing the assistant was told it could not do.** The link
    is a setting, the storefront already counts presses on it, and a buyer who
    asks about payments at nine at night is exactly who should be starting it
    -- but the prompt said "cannot send the credit application" and escalated,
    so the answer arrived the next morning, from a rep, as an email.

    In chat the result carries no URL at all, only the card: the browser draws
    a button through `/r/site/credit-application`, which counts the press and
    forwards to the configured page. A model that is never shown the address
    cannot paste a mistyped one into a sentence. By email the link has to be in
    the words, so it is handed over -- the counted hop where this deployment
    knows its own public address, the dealer's page where it does not.
    """
    from app.api.redirect import site_hop
    from app.api.settings import live_settings
    from app.config import settings as app_settings

    url = (live_settings(db).credit_application_url or "").strip()
    if not url:
        return {
            "available": False,
            "guidance": (
                "This dealership has not set up an online finance application. Say a "
                "colleague will go through financing with them, and escalate_to_human "
                "with rule_key financing_trouble -- never invent a link."
            ),
        }
    if convo.channel == "voice":
        return {
            "available": False,
            "guidance": (
                "There is nothing to press on a call. Say the finance application is on "
                "the dealership's website, and never read a URL out."
            ),
        }
    if convo.channel == "email":
        base = (app_settings.public_base_url or "").rstrip("/")
        return {
            "available": True,
            "link": f"{base}{site_hop(CREDIT_CARD)}" if base else url,
            "guidance": "Put this link in your reply, on a line of its own.",
        }
    return {
        "available": True,
        "card": CREDIT_CARD,
        "guidance": (
            "A button for their finance application is on the buyer's screen, under "
            "your reply. Point at it in one line; do not write a URL."
        ),
    }


EXECUTORS = {
    "answer_from_knowledge": answer_from_knowledge,
    "offer_credit_application": offer_credit_application,
    "search_inventory": search_inventory,
    "get_vehicle": get_vehicle,
    "check_availability": check_availability,
    "request_details": request_details,
    "save_captured_fields": save_captured_fields,
}

SIDE_EFFECT_EXECUTORS = {
    "close_conversation": close_conversation,
    "book_appointment": book_appointment,
    "escalate_to_human": escalate_to_human,
}


SCHEMAS = {t["name"]: set(t["input_schema"].get("properties", {})) for t in TOOL_DEFS}


def _reject_unknown_args(name: str, args: dict) -> None:
    """An argument the tool does not have is an error, not something to ignore.

    A free-form model will invent a parameter name eventually -- calling
    search_inventory with `need` instead of `keywords`, say. Dropping it
    silently is the worst outcome available: the filter never applies, five
    unrelated cars come back, and the model quotes a price off a vehicle the
    buyer never asked about. Confidently wrong beats obviously broken only for
    the demo, never for the buyer.

    Told about it, the model retries with the right name in the next round.
    """
    allowed = SCHEMAS.get(name)
    if allowed is None:
        return
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise ToolError(
            f"{name} has no argument {', '.join(repr(u) for u in unknown)}. "
            f"It takes: {', '.join(sorted(allowed)) or 'no arguments'}."
        )


def execute(
    db: Session, convo: Conversation, name: str, args: dict, tool_call_id: str | None = None
) -> dict:
    _reject_unknown_args(name, args)
    if name in SIDE_EFFECT_EXECUTORS:
        return SIDE_EFFECT_EXECUTORS[name](db, convo, args, tool_call_id)
    if name in EXECUTORS:
        return EXECUTORS[name](db, convo, args)
    raise ToolError(f"Unknown tool {name}")
