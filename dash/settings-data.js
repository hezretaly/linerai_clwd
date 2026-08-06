/* ============================================================
   Liner AI — Settings, mock data
   ============================================================ */

const DEALER = {
  name:'Riverside Auto',
  address:'2140 Riverside Ave, entrance off 4th',
  city:'Fairview, OR 97024',
  phone:'555-0100',
  email:'sales@riversideauto.com',
  website:'riversideauto.com',
  timezone:'Pacific Time (US & Canada)'
};

const HOURS = [
  ['Monday',    '8:00 AM', '6:00 PM', true],
  ['Tuesday',   '8:00 AM', '6:00 PM', true],
  ['Wednesday', '8:00 AM', '6:00 PM', true],
  ['Thursday',  '8:00 AM', '6:00 PM', true],
  ['Friday',    '8:00 AM', '6:00 PM', true],
  ['Saturday',  '8:00 AM', '5:00 PM', true],
  ['Sunday',    '—',       '—',       false]
];

const EMBED = '<script src="https://cdn.liner.ai/widget.js"\n        data-store="riverside-auto"\n        data-position="bottom-right" async><\/script>';

const CHANNELS = [
  { id:'widget', name:'Website widget', detail:'Chat and voice, bottom-right of riversideauto.com',
    status:'live', meta:'Installed 2 Apr · 41 conversations this month' },
  { id:'sms', name:'SMS number', detail:'Outbound texts and confirmations',
    status:'live', meta:'(555) 0148-22 · provisioned 2 Apr' },
  { id:'phone', name:'Inbound phone', detail:'Liner answers the main line after hours',
    status:'not_connected', meta:'Forward 555-0100 to the Liner number to switch this on' },
  { id:'email', name:'Sales inbox', detail:'sales@riversideauto.com',
    status:'live', meta:'Forwarding verified 2 Apr' }
];

const SOURCES = [
  { id:'autotrader', name:'AutoTrader', status:'live', leads:34, cost:212, meta:'ADF/XML email parsing' },
  { id:'cargurus',   name:'CarGurus',   status:'live', leads:28, cost:198, meta:'ADF/XML email parsing' },
  { id:'carscom',    name:'Cars.com',   status:'live', leads:19, cost:205, meta:'ADF/XML email parsing' },
  { id:'facebook',   name:'Facebook Marketplace', status:'not_connected', leads:0, cost:0, meta:'Not connected' }
];

const INTEGRATIONS = [
  { id:'calendar', name:'Calendar', detail:'Liner books straight onto the Liner calendar.',
    status:'internal', meta:'Two-way sync with Google or Outlook is not available yet.' },
  { id:'crm', name:'CRM / DMS', detail:'No integration by design — Liner reads your public listings.',
    status:'none', meta:'Sold vehicles stay live until your website updates.' }
];

const NOTIFY_RULES = [
  ['A conversation needs a person', 'Immediately', 'SMS + dashboard'],
  ['A flag has waited over 30 minutes', 'Escalate', 'SMS to the manager on shift'],
  ['An appointment is booked', 'Immediately', 'Dashboard only'],
  ['An appointment is unconfirmed 4 hours out', 'Escalate', 'SMS to the assigned rep'],
  ['The inventory feed fails to sync', 'Immediately', 'Email to admins'],
  ['Overnight summary', 'Daily at 8:00 AM', 'Email + dashboard']
];

const BILLING = {
  plan:'Liner AI — single rooftop',
  price:2000,
  cycle:'Monthly, billed on the 2nd',
  next:'2 August 2026',
  method:'Visa ending 4417',
  pilotEnds:null,
  invoices:[
    ['2 Jul 2026', 2000, 'Paid'],
    ['2 Jun 2026', 2000, 'Paid'],
    ['2 May 2026', 2000, 'Paid'],
    ['2 Apr 2026', 0, 'Pilot — no charge']
  ]
};