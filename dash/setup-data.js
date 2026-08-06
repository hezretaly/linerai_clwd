/* ============================================================
   Liner AI — setup, mock data
   ============================================================ */

const SETTINGS = {
  tone: 'professional',        /* warm | professional | brisk */
  push: 3,                     /* 1 let them browse … 5 always ask */
  priceMode: 'listed',         /* listed | listed_firm | never */
  discountPct: 4,              /* % below asking Liner may agree to */
  financing: 'explain',        /* never | explain | start */
  afterHours: 'push'           /* same | push | capture */
};

const TONE_COPY = {
  warm:         { label: 'Warm', hint: 'Chatty, uses the buyer’s name, a little small talk.' },
  professional: { label: 'Professional', hint: 'Polite and clear. The default most stores pick.' },
  brisk:        { label: 'Brisk', hint: 'Short sentences, no filler. Good for SMS.' }
};

const PUSH_COPY = [
  '', 'Answers, never asks', 'Mentions a visit once', 'Offers two times', 'Asks every few turns', 'Asks in every reply'
];

const RULES = [
  { id:'price', on:true, name:'Offer is below the price floor',
    detail:'Liner refuses and stops rather than counter.',
    threshold:{ label:'Floor', value:4, unit:'% below asking' },
    route:'Sales manager', notify:'SMS + dashboard', fired:11 },
  { id:'finance', on:true, name:'Financing or credit trouble mentioned',
    detail:'Liner never comments on approval odds.',
    threshold:null, route:'Marcus Idowu — finance', notify:'SMS + dashboard', fired:7 },
  { id:'manager', on:true, name:'Buyer asks for a manager',
    detail:'Includes asking for a named person twice.',
    threshold:null, route:'Sales manager', notify:'SMS + dashboard', fired:4 },
  { id:'urgency', on:true, name:'Buyer signals urgency',
    detail:'“I need this today”, “I’m deciding tonight”.',
    threshold:{ label:'Within', value:24, unit:'hours stated' },
    route:'Duty rep', notify:'Dashboard only', fired:9 },
  { id:'sign', on:true, name:'Buyer is ready to sign',
    detail:'Asks about paperwork, deposits or collection.',
    threshold:null, route:'Duty rep', notify:'SMS + dashboard', fired:3 }
];

const KNOWLEDGE = [
  { id:'k1', topic:'Documentation fee', answer:'$499 doc fee on every deal, non-negotiable, disclosed before the appointment is booked.', edited:'12 Jun', used:38 },
  { id:'k2', topic:'Trade-in policy', answer:'Appraisals are in person only. Liner books a 45-minute appraisal slot and never quotes a trade figure.', edited:'3 Jul', used:64 },
  { id:'k3', topic:'Financing terms', answer:'In-house lenders from 6.9% APR on approved credit, 24–72 months. Liner states the range only, never an individual rate.', edited:'3 Jul', used:51 },
  { id:'k4', topic:'Deposits', answer:'$500 refundable holds a vehicle for 48 hours. Taken in person or by phone with a manager, never over chat.', edited:'22 May', used:12 },
  { id:'k5', topic:'Opening hours and directions', answer:'Mon–Sat 8 AM to 6 PM, closed Sunday. Lot is on Riverside Ave, entrance off 4th.', edited:'2 Apr', used:97 },
  { id:'k6', topic:'Warranty', answer:'30-day/1,000-mile dealer warranty on everything under 100k miles. Remaining factory warranty stated per vehicle.', edited:'18 Jun', used:29 },
  { id:'k7', topic:'Out-of-state buyers', answer:'We ship at buyer expense. Liner captures the destination and hands off — never quotes shipping.', edited:'9 Jul', used:6 }
];

const VERSIONS = [
  { v:7, label:'Current', by:'Dana Mercer', when:'Today, 7:52 AM', note:'Raised the price floor from 3% to 4%.', live:true },
  { v:6, by:'Dana Mercer', when:'24 Jul, 4:10 PM', note:'Added the out-of-state shipping entry.', live:false },
  { v:5, by:'Marcus Idowu', when:'18 Jun, 11:30 AM', note:'Financing set to explain terms only.', live:false },
  { v:4, by:'Dana Mercer', when:'12 Jun, 9:05 AM', note:'Doc fee corrected to $499.', live:false },
  { v:3, by:'Onboarding', when:'2 Apr, 2:00 PM', note:'Tone intake applied. Went live.', live:false }
];

/* what a preset build actually compiles to, shown read-only unless overridden */
const COMPILED_PROMPT =
`You are the after-hours sales assistant for Riverside Auto.
Answer only from the live inventory feed and the knowledge base below.

TONE: professional — polite and clear, no filler.
APPOINTMENTS: offer two specific times once the buyer shows interest.
PRICE: state the listed price. You may agree up to 4% below asking.
  Below that, stop and hand off.
FINANCING: explain published terms only. Never estimate approval odds.
AFTER HOURS: push harder for the appointment; the showroom opens at 8 AM.

Never invent a specification. If the feed does not say it, book an
appointment and capture name, email, optional phone and reason first.

STOP AND HAND OFF WHEN: the offer is more than 4% below asking; the buyer
mentions financing or credit trouble; the buyer asks for a manager; the
buyer signals urgency inside 24 hours; the buyer is ready to sign.`;