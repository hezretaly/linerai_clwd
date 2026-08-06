/* ============================================================
   Liner AI — Conversations, mock data
   One object per conversation. The three panes all render from
   this, so adding a case is a data edit, not a markup edit.
   ============================================================ */

const CONVERSATIONS = [
  {
    id: 'tara',
    name: 'Tara Nolan',
    initials: 'TN',
    channel: 'chat',
    source: 'Website',
    vehicle: '2020 Grand Cherokee Limited',
    group: 'Needs a person',
    state: 'flagged',
    time: '8:26 AM',
    waiting: '11h 1m',
    unread: true,
    snippet: '"I can do 26 flat, cash, today. Otherwise I\'m going to look at the one in Fairview."',
    tags: [['Price floor', 'destructive'], ['Website', 'muted'], ['Unclaimed', 'muted']],
    banner: {
      tone: 'destructive',
      title: 'Liner stopped — offer is below your price floor',
      sub: 'Buyer is $3,200 under asking. Waiting 11h 1m, still unclaimed.'
    },
    thread: [
      { t: 'day', text: 'Yesterday · Thursday, Jul 30' },
      { t: 'them', text: 'Hi, is the 2020 Grand Cherokee Limited still there? The white one.', time: '10:11 PM' },
      { t: 'sys', text: 'Liner checked inventory — <b>2020 Grand Cherokee Limited · $29,200 · 38,400 mi · stock #RA-2291</b>' },
      { t: 'liner', text: "It is — white on black leather, 38,400 miles, clean CARFAX and one owner. It's on the front row right now. Are you looking to come see it this week?", time: '10:11 PM · replied in 14s' },
      { t: 'them', text: "Maybe. What's your best price on it? I've been watching a similar one over in Fairview for a while.", time: '10:14 PM' },
      { t: 'liner', text: "The listed price is $29,200, and that already reflects a $1,100 reduction we made last week. I can't go lower than that over chat, but if you come in, the team can talk numbers properly with your trade in hand. Would Saturday morning work?", time: '10:15 PM' },
      { t: 'day', text: 'Today · Friday, Jul 31' },
      { t: 'them', text: "I can do 26 flat, cash, today. Otherwise I'm going to look at the one in Fairview.", time: '8:26 AM' },
      { t: 'sys-alert', text: 'Rule fired: <b>Price below floor</b> — offer is 11% under asking, your limit is 4%. Liner stopped and flagged for a person.' },
      { t: 'liner', text: "That's below what I'm able to agree to here, but I don't want to lose you over it — let me get one of our managers to look at this properly. Can I take your number so they can call you first thing?", time: '8:26 AM' },
      { t: 'them', text: 'Sure — 555-0148. Mornings are better for me.', time: '8:27 AM' },
      { t: 'sys', text: 'Liner captured <b>name, phone and reason</b> before handing off. Lead is reachable either way.' }
    ],
    composer: { locked: 'Liner is holding this conversation', sub: "It won't reply again until someone takes over or hands it back.", cta: 'Take over' },
    rail: {
      leadId: 'Lead #4471 · first contact last night',
      contact: [
        { icon: 'phone', value: '555-0148', prov: 'typed' },
        { icon: 'mail', value: 'Not given', prov: null },
        { icon: 'globe', value: 'Website · organic', prov: null }
      ],
      vehicle: { name: '2020 Jeep Grand Cherokee Limited', meta: '38,400 mi · stock #RA-2291<br>Front row · 62 days on lot', price: '$29,200' },
      captured: [
        ['Name', 'Tara Nolan', 'typed'],
        ['Phone', '555-0148', 'typed'],
        ['Best time', 'Mornings', 'typed'],
        ['Budget', '$26,000 cash', 'inferred'],
        ['Reason', 'Comparing against a Fairview listing', 'inferred']
      ],
      assigned: 'Unclaimed — in the pool',
      history: [
        ['Flagged for a person — price floor', 'Today · 8:26 AM', true],
        ['Phone number captured', 'Today · 8:27 AM', false],
        ['Liner quoted stock #RA-2291', 'Yesterday · 10:11 PM', false],
        ['First contact — website chat', 'Yesterday · 10:11 PM', false]
      ]
    }
  },

  {
    id: 'renee',
    name: 'Renee Solano',
    initials: 'RS',
    channel: 'email',
    source: 'AutoTrader',
    vehicle: '2019 Equinox LT',
    group: 'Needs a person',
    state: 'flagged',
    time: '3:40 AM',
    waiting: '5h 32m',
    unread: true,
    snippet: '"Had a repo a couple years back — is that going to kill it before I drive out there?"',
    tags: [['Finance', 'destructive'], ['AutoTrader', 'muted']],
    banner: {
      tone: 'destructive',
      title: 'Liner stopped — buyer raised credit trouble',
      sub: 'Needs Marcus in finance. Waiting 5h 32m.'
    },
    thread: [
      { t: 'day', text: 'Today · Friday, Jul 31' },
      { t: 'sys', text: 'Marketplace lead received from <b>AutoTrader</b> — name, email and phone supplied. Liner made first contact.' },
      { t: 'liner', text: "Hi Renee — thanks for your enquiry on the 2019 Equinox LT. It's still available: 44,100 miles, clean title, and we've just had it through service. When were you hoping to come take a look?", time: '3:31 AM · outbound' },
      { t: 'them', text: "Thanks. Before I drive an hour out there — had a repo a couple years back — is that going to kill it before I drive out there?", time: '3:40 AM' },
      { t: 'sys-alert', text: 'Rule fired: <b>Financing or credit mentioned</b> — Liner does not discuss approval odds. Stopped and flagged for finance.' },
      { t: 'liner', text: "That's absolutely something we can work with, and it's worth the conversation — but I'd rather our finance manager give you a straight answer than have me guess. What's the best number and time for him to reach you?", time: '3:40 AM' },
      { t: 'them', text: 'Sure — 555-0733, after 2pm works best. It was 2023, on a Nissan.', time: '3:44 AM' },
      { t: 'sys', text: 'Liner captured <b>name, email, phone and reason</b> before handing off. Lead is reachable either way.' }
    ],
    composer: { locked: 'Liner is holding this conversation', sub: 'Escalation routed to finance. Nothing sends until a person takes over.', cta: 'Take over' },
    rail: {
      leadId: 'Lead #4468 · AutoTrader, $212 acquisition',
      contact: [
        { icon: 'phone', value: '555-0733', prov: 'typed' },
        { icon: 'mail', value: 'r.solano@mailbox.com', prov: 'marketplace' },
        { icon: 'globe', value: 'AutoTrader · paid lead', prov: null }
      ],
      vehicle: { name: '2019 Chevrolet Equinox LT', meta: '44,100 mi · stock #RA-2107<br>Back row · 38 days on lot', price: '$18,750' },
      captured: [
        ['Name', 'Renee Solano', 'marketplace'],
        ['Phone', '555-0733', 'typed'],
        ['Best time', 'After 2 PM', 'typed'],
        ['Credit', 'Repossession, 2023', 'typed'],
        ['Timeline', 'Ready this week', 'inferred']
      ],
      assigned: 'Marcus Idowu — finance',
      history: [
        ['Flagged for a person — financing', 'Today · 3:40 AM', true],
        ['Phone and reason captured', 'Today · 3:44 AM', false],
        ['Liner made first contact', 'Today · 3:31 AM', false],
        ['Lead received from AutoTrader', 'Today · 3:29 AM', false]
      ]
    }
  },

  {
    id: 'unknown',
    name: 'Grand Cherokee caller',
    initials: '··',
    channel: 'voice',
    source: 'Phone',
    vehicle: '—',
    group: 'Needs a person',
    state: 'flagged',
    time: '11:47 PM',
    waiting: '9h 25m',
    unread: false,
    anonymous: true,
    snippet: 'Asked for Marcus in finance by name. Liner escalated rather than take a message.',
    tags: [['Asked for a manager', 'destructive'], ['Phone', 'muted']],
    banner: {
      tone: 'destructive',
      title: 'Liner stopped — caller asked for a manager by name',
      sub: 'No name given. Caller ID is the only identifier. Waiting 9h 25m.'
    },
    thread: [
      { t: 'day', text: 'Yesterday · Thursday, Jul 30' },
      { t: 'sys', text: 'Inbound call answered by Liner · <b>555-0621</b> · duration 1:08 · transcribed' },
      { t: 'them', text: "Yeah, hi — is Marcus there? Marcus in the finance office.", time: '11:47 PM' },
      { t: 'liner', text: "Marcus isn't in right now — we're closed until 8 AM. I can take a message and have him call you first thing, or I can help if it's about a vehicle.", time: '11:47 PM' },
      { t: 'them', text: "No, I need Marcus. He knows the situation. Just tell him to call me back.", time: '11:48 PM' },
      { t: 'sys-alert', text: 'Rule fired: <b>Asked for a manager</b> — named a specific person twice. Liner stopped rather than take a message.' },
      { t: 'liner', text: "Understood — I'll make sure Marcus sees this the moment he's in. He'll have your number from this call. Anything you'd like me to pass along?", time: '11:48 PM' },
      { t: 'them', text: "Just that I called. He'll know.", time: '11:48 PM' }
    ],
    composer: { locked: 'Liner is holding this conversation', sub: 'Voice call ended. Replying here sends an SMS to the caller ID.', cta: 'Take over' },
    rail: {
      leadId: 'Lead #4470 · identity not confirmed',
      contact: [
        { icon: 'phone', value: '555-0621', prov: 'caller-id' },
        { icon: 'mail', value: 'Not given', prov: null },
        { icon: 'globe', value: 'Inbound phone · after hours', prov: null }
      ],
      vehicle: null,
      captured: [
        ['Name', 'Not given', null],
        ['Phone', '555-0621', 'caller-id'],
        ['Asked for', 'Marcus (finance)', 'typed'],
        ['Prior contact', 'Implied — "he knows the situation"', 'inferred']
      ],
      assigned: 'Unclaimed — in the pool',
      history: [
        ['Flagged for a person — manager request', 'Yesterday · 11:48 PM', true],
        ['Call ended — 1:08', 'Yesterday · 11:48 PM', false],
        ['Inbound call answered by Liner', 'Yesterday · 11:47 PM', false]
      ]
    }
  },

  {
    id: 'hector',
    name: 'Hector Villalba',
    initials: 'HV',
    channel: 'voice',
    source: 'Website',
    vehicle: '2021 Tacoma TRD',
    group: 'Live now',
    state: 'live',
    time: 'now',
    waiting: null,
    unread: false,
    snippet: 'Liner: "It\'s got 41,000 miles and a clean CARFAX — want to see it today?"',
    tags: [['On call 1:24', 'live'], ['Website voice', 'muted']],
    banner: {
      tone: 'primary',
      title: 'Call in progress — 1:24',
      sub: 'You can join with your browser mic. Liner will announce the transfer.'
    },
    thread: [
      { t: 'day', text: 'Today · Friday, Jul 31' },
      { t: 'sys', text: 'Website voice call started · transcribing live' },
      { t: 'them', text: "Hey, I'm looking at the Tacoma on your site, the grey one. Is that still around?", time: '9:10 AM' },
      { t: 'sys', text: 'Liner checked inventory — <b>2021 Toyota Tacoma TRD Off-Road · $34,900 · 41,000 mi · stock #RA-2318</b>' },
      { t: 'liner', text: "It is — 2021 TRD Off-Road, grey, and it's on the lot right now.", time: '9:10 AM' },
      { t: 'them', text: 'Any accidents on it? And what kind of miles?', time: '9:11 AM' },
      { t: 'liner', text: "It's got 41,000 miles and a clean CARFAX — want to see it today?", time: '9:11 AM' }
    ],
    composer: { locked: 'Liner is on this call', sub: 'Join to take the call over. Liner will hand off and stop speaking.', cta: 'Join call' },
    rail: {
      leadId: 'Lead #4479 · live now',
      contact: [
        { icon: 'phone', value: '555-0904', prov: 'caller-id' },
        { icon: 'mail', value: 'Not given', prov: null },
        { icon: 'globe', value: 'Website · voice widget', prov: null }
      ],
      vehicle: { name: '2021 Toyota Tacoma TRD Off-Road', meta: '41,000 mi · stock #RA-2318<br>Front lot · 12 days on lot', price: '$34,900' },
      captured: [
        ['Name', 'Hector Villalba', 'typed'],
        ['Phone', '555-0904', 'caller-id'],
        ['Interest', 'Accident history, mileage', 'inferred']
      ],
      assigned: 'Unclaimed — in the pool',
      history: [
        ['Call started', 'Today · 9:10 AM', true],
        ['Liner quoted stock #RA-2318', 'Today · 9:10 AM', false]
      ]
    }
  },

  {
    id: 'colin',
    name: 'Colin Hart',
    initials: 'CH',
    channel: 'email',
    source: 'CarGurus',
    vehicle: '2021 Ram 1500',
    group: 'Earlier today',
    state: 'waiting',
    time: '6:05 AM',
    waiting: '3h 07m',
    unread: false,
    snippet: 'Liner reached out first with two time slots. Delivered, still unread.',
    tags: [['Awaiting reply', 'warning'], ['CarGurus', 'muted']],
    banner: {
      tone: 'warning',
      title: 'Waiting on the buyer — 3h 07m',
      sub: 'Liner made first contact. Delivered, not yet opened. Next nudge scheduled 2:00 PM.'
    },
    thread: [
      { t: 'day', text: 'Today · Friday, Jul 31' },
      { t: 'sys', text: 'Marketplace lead received from <b>CarGurus</b> — Liner made first contact by email.' },
      { t: 'liner', text: "Morning Colin — thanks for your enquiry on the 2021 Ram 1500 Big Horn. It's still here: 52,300 miles, one owner, tow package included. I've got Saturday 10:00 AM or Saturday 2:30 PM open for a test drive — either work?", time: '6:05 AM · outbound' },
      { t: 'sys', text: 'Delivered 6:05 AM · <b>not yet opened</b>. Follow-up scheduled for 2:00 PM.' }
    ],
    composer: { locked: 'Liner is handling this conversation', sub: 'It will follow up at 2:00 PM if there is still no reply.', cta: 'Take over' },
    rail: {
      leadId: 'Lead #4474 · CarGurus, $198 acquisition',
      contact: [
        { icon: 'phone', value: '555-0311', prov: 'marketplace' },
        { icon: 'mail', value: 'chart@mailbox.com', prov: 'marketplace' },
        { icon: 'globe', value: 'CarGurus · paid lead', prov: null }
      ],
      vehicle: { name: '2021 Ram 1500 Big Horn', meta: '52,300 mi · stock #RA-2240<br>Side lot · 21 days on lot', price: '$31,400' },
      captured: [
        ['Name', 'Colin Hart', 'marketplace'],
        ['Phone', '555-0311', 'marketplace'],
        ['Email', 'chart@mailbox.com', 'marketplace']
      ],
      assigned: 'Unclaimed — in the pool',
      history: [
        ['Awaiting reply', 'Today · 6:05 AM', true],
        ['Liner made first contact', 'Today · 6:05 AM', false],
        ['Lead received from CarGurus', 'Today · 6:04 AM', false]
      ]
    }
  },

  {
    id: 'gil',
    name: 'Gil Otonye',
    initials: 'GO',
    channel: 'email',
    source: 'Cars.com',
    vehicle: '2022 Tucson SEL',
    group: 'Earlier today',
    state: 'booked',
    time: '8:52 AM',
    waiting: null,
    unread: false,
    snippet: 'Booked Friday 5:30 PM — 2022 Tucson SEL. Confirmation sent.',
    tags: [['Booked', 'success'], ['Cars.com', 'muted']],
    banner: null,
    thread: [
      { t: 'day', text: 'Today · Friday, Jul 31' },
      { t: 'sys', text: 'Marketplace lead received from <b>Cars.com</b> — Liner made first contact by SMS.' },
      { t: 'liner', text: "Morning Gil — the 2022 Tucson SEL is still available, 28,900 miles and still under factory warranty. Want me to hold it for a test drive?", time: '8:46 AM · outbound' },
      { t: 'them', text: 'Yes please. Anything after 5 today?', time: '8:50 AM' },
      { t: 'liner', text: "5:30 PM works — I've put it under your name. What's the best number for the confirmation?", time: '8:51 AM' },
      { t: 'them', text: '555-0455', time: '8:52 AM' },
      { t: 'sys', text: 'Appointment booked — <b>Test drive · Friday 5:30 PM · 2022 Tucson SEL</b>. Confirmation and directions sent by SMS.' }
    ],
    composer: { locked: 'Liner is handling this conversation', sub: 'Appointment confirmed. Liner will send a reminder two hours before.', cta: 'Take over' },
    rail: {
      leadId: 'Lead #4477 · Cars.com, $205 acquisition',
      contact: [
        { icon: 'phone', value: '555-0455', prov: 'typed' },
        { icon: 'mail', value: 'g.otonye@mailbox.com', prov: 'marketplace' },
        { icon: 'globe', value: 'Cars.com · paid lead', prov: null }
      ],
      vehicle: { name: '2022 Hyundai Tucson SEL', meta: '28,900 mi · stock #RA-2301<br>Front row · 9 days on lot', price: '$26,300' },
      captured: [
        ['Name', 'Gil Otonye', 'marketplace'],
        ['Phone', '555-0455', 'typed'],
        ['Appointment', 'Friday 5:30 PM', 'typed'],
        ['Timeline', 'Buying this week', 'inferred']
      ],
      assigned: 'Jess Rowe',
      history: [
        ['Appointment booked — Fri 5:30 PM', 'Today · 8:52 AM', true],
        ['Phone captured', 'Today · 8:52 AM', false],
        ['Liner made first contact', 'Today · 8:46 AM', false]
      ]
    }
  }
];