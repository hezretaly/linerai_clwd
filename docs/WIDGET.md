# The website chat

Liner's chat on a dealership's **own** website: a bubble in the corner of
every page that opens the same assistant a buyer meets on our storefront.
It knows which car the buyer is looking at, keeps the conversation across page
loads and return visits, and tells the dealer's Google Tag Manager when a chat
turns into a lead.

The page a dealer's website provider needs is the **Website chat** card on
**Liner setup**. It has the tag with a Copy button, the sites the tag will
run on, and what the tag has reported from each site once it is live.

## Installing it

One line, pasted once:

```html
<script src="https://linerai.us/embed.js" data-dealer="alsbou" async></script>
```

Copy the line from the Website chat card rather than from here. It carries
the address this deployment is reached at. On a server that gives each dealer
group its own subdomain (`STORE_DOMAIN`), that address is the group's own, for
example `https://alsbou.linerai.us/embed.js`.

**A tag pasted before a group moved keeps working, and nobody has to edit the
dealer's site.** `linerai.us` redirects a moved group's old addresses to its
subdomain (`deploy/liner-groups-moved.conf`), so the script and its settings
still load. A redirect does not change the address the page's `<script>` tag
names, though, so the settings say where the chat lives now (`frame_origin`)
and the loader opens the chat there and talks to it there. A site with a
Content-Security-Policy needs the subdomain allowed as well as the old address.

It goes wherever the site's own scripts go, on every page:

- **In the site template**, just before `</body>`. The dealer's website
  provider does this.
  - **Get My Auto (Alsbou's platform)** has a dedicated `chatbox` slot in the
    site config and a `snippets` list for everything else.
  - Alsbou's `chatbox` slot currently holds Capital One's *Chat Concierge*.
    Putting Liner there replaces that chat, and replacing it is the dealer's
    decision. Otherwise use `snippets`.
  - Get My Auto also loads Capital One's loader for the pre-qualification
    buttons on every car. That is not a chat and can stay.
- **Through Google Tag Manager**, when an agency installs it: a *Custom HTML*
  tag with the same line, firing on *All Pages*. If a container strips
  attributes, name the dealer in the address instead:
  `https://linerai.us/embed.js?dealer=alsbou`.

**The tag to hand over today** names the dealer in the address instead:

```html
<script src="https://linerai.us/alsbou/embed.js" async></script>
```

It opens Alsbou's chat on every loader this box has ever served -- the first
bubble (`7014d59`) read the dealer from that path and never from
`data-dealer`, so on a box not yet updated the `data-dealer` form would open
the *default* store's chat -- and it keeps working after Alsbou moves to its
own subdomain, because the old box redirects `/alsbou/...`. The page
awareness, the session on their domain, the Tag Manager events and the
other-chat check arrive when the box runs `21558e0` or later; before that the
bubble opens the chat and nothing more. If their platform will not take a
script at all, an iframe of `https://linerai.us/alsbou/chat?embed=1` is the
fallback: no bubble and no page awareness, and allowed only on the sites
below.

That is the last edit anyone makes on the dealer's site. Everything else is
read from Liner on each page load:

- the label, the title and which side the bubble sits on;
- whether lead events go to Tag Manager;
- whether the bubble shows at all.

The loader file is cached for five minutes and its settings for one minute,
so a change reaches every page within a few minutes.

### Before it shows anywhere

The dealership's sites have to be listed in its profile, **both spellings of
the host**. A dealer whose canonical host is `www` still serves pages from
the bare domain, and an embed that works on one and not the other is the
worst way for this to fail.

```yaml
# backend/config/dealerships/alsbou.yaml
embed_origins:
  - https://www.alsboucars.com
  - https://alsboucars.com
```

A page on any other site gets no bubble. Its browser console says why:

```
[liner] not shown here. https://staging.example is not one of this dealership's websites (...)
```

A staging copy of the site is another origin. Add it to `embed_origins` the
same way.

### Settings

All optional. Changes take effect without touching the dealer's site.

```yaml
widget:
  label: Chat with us   # the bubble's label and tooltip, 40 characters
  title: ""             # the panel's heading; the dealership's name when empty
  side: right           # right | left -- move it off an accessibility button
  offset: 20            # pixels from the corner, 8 to 120
  gtm: true             # push events to the site's Tag Manager
  events: asc           # asc | liner | both -- see below
```

The colour is the brand's accent (`brand.accent`), not a second setting.

The **Switch off** button on the Website chat card hides the bubble on every
site within a minute. Only a manager can press it. A switched-off chat says
so in the console of any page that asks.

## What the buyer gets

- **A bubble, and nothing loaded behind it.** The chat itself is loaded on the
  buyer's first click, from `/widget/<dealer>` on the dealership's own Liner
  address (`https://alsbou.linerai.us/widget/alsbou`).
  - A chat that loads with every page would start a conversation for every
    visitor who never clicks.
  - It would also slow down the dealer's own pages.
- **A corner panel on a laptop; the whole screen on a phone.** Phones get
  16px inputs so iOS does not zoom in, and the page underneath does not
  scroll while the chat is open.
- **Their conversation back when they return.** The conversation id is kept
  in the dealer site's own storage for 30 days.
  - A frame from another site gets partitioned storage, and Safari clears it
    after a week. The chat would lose the buyer on their second visit, which
    is the one that matters.
  - Moving to another page with the chat open keeps it open on a laptop. On a
    phone it does not: there the chat is the whole screen, and a buyer who
    went back a page is looking for the page.
- **The car they are looking at.** See [Page awareness](#page-awareness).
- **An unread count on the bubble.** A reply that arrives while the panel is
  shut shows as a number: a rep answering from the dashboard, or a reply that
  finished after they closed it.

## Page awareness

The loader tells the chat which page the buyer is on. On a car's own page it
also sends the VIN, so "is this one still available?" means that car.

**Which car the page is about is decided by Liner, not by the page.**

1. **The address.** Every import stores each car's own page on the dealer's
   site (`listing_url`). A page whose path is one of those *is* that car.
   This is the most reliable evidence there is:
   - Alsbou's car pages carry four cars' structured data, for the car and
     three "similar vehicles".
   - Their addresses hold only the last six characters of the VIN.
   - Craig and Landreth's hold none.
   - Paths are compared without the host, because Craig and Landreth's feed
     and their site are on different hostnames.
2. **What the loader read off the page**, most reliable first:
   - `window.LinerWidget = { vin: '...' }`, a `<meta name="liner:vin">` or a
     `data-liner-vin` attribute, for a provider who wants to say it outright;
   - Get My Auto's once-per-page `<script id="at-vehicle-<VIN>">`;
   - schema.org structured data, only for the car whose `url` is this page;
   - `data-vin`, only when exactly one VIN carries it;
   - the address or the visible text, only with a valid VIN check digit.

   A list page has many VINs, and naming one of them would have Liner talking
   about a car the buyer never clicked, so there the loader sends none.

**What Liner is told, and what it is never told:**

- The page's address, its title and its car's identity: year, make, model,
  trim and VIN. **Never its price.** A price is re-read by a tool every turn,
  because it can change, and the prompt says to look the car up before quoting
  anything about it. The page cannot put a number in Liner's mouth.
- The title is labelled as the page's label, "never an instruction". It is
  text somebody else's page wrote.
- A sold or do-not-discuss car is not described. Liner is told only that the
  page shows a VIN that is not an available car, and that it may have sold.
- An address on a site this dealership did not list is dropped entirely.
- So are query parameters that can carry somebody's details: `email`,
  `phone`, `name`, tokens and ad click ids. What a rep sees on the buyer's
  timeline is the page, not whatever a form put in its address.

Without a model (`LLM_MODE=stub`), the scripted assistant answers "is this one
still here?" about the page's car too. It checks the car's status rather
than assuming.

The dashboard shows it as well:

- The rail's recap says which site the chat started on.
- The conversation's detail lists the last five pages.

## Google Tag Manager events

Pushed to the dealer's own `dataLayer`, where their container turns them into
GA4 events and ad conversions. By default they use the car industry's own
names, from the Automotive Standards Council's GA4 standard. An agency
already counts these and imports them into Google Ads:

| When | `events: asc` (default) | `events: liner` |
|---|---|---|
| The chat is opened | `asc_comm_engagement`, `comm_status: start` | `liner_chat_open` |
| The buyer first writes | `asc_comm_engagement`, `comm_status: engage` | `liner_chat_start` |
| A number or an address is on file | `asc_comm_submission` and `asc_comm_submission_sales` | `liner_lead` |
| A visit is booked | `asc_comm_submission_sales_appt` | `liner_appointment` |
| The finance application is opened | `asc_cta_interaction` | `liner_credit_app` |

- **Once per conversation** for writing, a lead and a booking. A buyer who
  corrects their number is one lead.
- **A lead or a booking is read off the database after the turn, never off
  a reply.** A model's sentence saying "you're booked" is not a booking.
- **ASC parameters on every push:**
  - `event_owner: liner`, so a chat lead never passes for the site's own
    forms;
  - `comm_type: chat` and `department: sales`;
  - `page_type` and `affiliation`, copied from the site's `asc_datalayer`
    where it keeps one;
  - the car as `item_id` (the VIN), `item_year`, `item_make`, `item_model`
    and `item_variant`.
- **Values are lowercase and at most 100 characters.** Both are GA4's
  limits.
- **Every key is on every push, blank where there is nothing to say.** Tag
  Manager's data layer remembers a key between pushes, so an appointment
  pushed without a VIN would otherwise carry the VIN of whichever car an
  earlier event named.
- **Nothing personal, ever.** No name, email, phone number, or anything the
  buyer typed. Google's Analytics terms forbid it, and every tag in the
  dealer's container can read the data layer. The car is fine: it is on the
  page already.

In Tag Manager, one *GA4 Event* tag covers them all:

- Trigger: *Custom Event* matching `^asc_` as a regular expression.
- Event name: `{{Event}}`.
- Parameters: the keys above, read from *Data Layer Variables*.

Parameters must be registered as custom dimensions in GA4 before they appear
in reports. A container that already forwards ASC events from other vendors
needs nothing new.

`events: both` sends both sets, for a container moving from one to the other.
Turn it back to one afterwards, or every lead is counted twice.
`gtm: false` sends nothing.

## Another chat on the site

Two chats on one page means the buyer answers in one of them, and the lead
may land in a system nobody at the dealership looks at. The loader recognises
the chat products dealer sites run by the scripts they load, the globals they
leave and the elements they draw:

- the automotive vendors: Capital One Chat Concierge, Gubagoo, CarNow,
  ActivEngage, LivePerson, Podium, Kenect, Matador, Fullpath, Impel, Dealer
  Inspire Conversations, CarChat24, Edmunds CarCode, DriveCentric, Birdeye;
- the general ones: Intercom, Drift, Zendesk, LiveChat, Tawk.to, Tidio,
  HubSpot and a dozen more.

If it finds one, it says so in two places:

- in the page's console, the day the tag goes live;
- on the Website chat card, as a warning on that site's row.

**It is reported, never removed.** Another vendor's widget is part of
somebody's contract, and a script that deleted it would be a far worse
surprise than two bubbles.

**It matches only what means a chat.** Capital One's loader also draws
pre-qualification buttons, and Elfsight's platform script also draws reviews
and banners. Both are on Alsbou's site without a chat, so neither counts. The
chat's own frame does.

**The tag on the page twice** is caught the same way, and is usually the
template plus Tag Manager. Only the first copy runs, and the card says to
remove the other.

## For the website provider

- `LinerWidget.open()` and `LinerWidget.close()` open and shut the chat from
  the site's own buttons, so a "Chat now" button does not need a second
  widget. A call made before the tag has loaded is kept and acted on once it
  has.
- **A site with a Content-Security-Policy** has to allow `https://linerai.us`
  in `script-src`, `connect-src` and `frame-src`. Otherwise the bubble never
  appears and nothing else says why. The loader names the blocked directive
  in the console when it can.
- **Every `[liner]` line in the browser console is written for this person.**
  Each names what happened and what fixes it:
  - not one of this dealership's sites;
  - switched off;
  - the tag is on the page twice;
  - another chat is on the page;
  - Liner could not be reached.

## What it is, underneath

- `frontend/public/embed.js`, the **loader**. Plain JavaScript in a shadow
  root, so the dealer's stylesheet cannot reach our button and ours cannot
  touch their page. It draws a button and a frame, reads the page, keeps the
  conversation id, and forwards events to Tag Manager.
  - It is never a second chat *client*. That is how one surface quietly stops
    drawing the booking card.
  - It is kept to ASCII: a script with no declared charset is read in the
    host page's encoding.
- `/widget/<dealer>`, the **frame**. The real `/chat`, which reads its store
  from the address and asks the loader for the conversation and the page.
  - Messages both ways are pinned to one origin.
  - With no loader answering, it carries on as the plain `/chat` it has
    always been.
- `/<dealer>/api/widget/config`, what the loader asks on every page. The
  answer is public (a name and a colour) and says in words why a bubble is
  not drawn.
- `/<dealer>/api/widget/install-report`, the loader saying it is live. Sent
  as a beacon, accepted only from a listed site, and written at most once a
  minute per site unless something changed.
- `/<dealer>/api/chat/sessions/{id}/page`, where the buyer moved to. Filed,
  never answered: nothing is said until they ask.

**Who may run it is decided by the browser.** The frame is served with
`Content-Security-Policy: frame-ancestors` listing only `embed_origins`. A
copy of the tag on somebody else's site gets a frame the browser refuses to
draw.

That is a browser control, not a security boundary. A script can post to the
chat's API without framing anything, and what stands in front of the bill is
the chat's own ceilings (`ratelimit.py`).
