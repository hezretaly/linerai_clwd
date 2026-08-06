/* ============================================================
   Liner AI — Leads, mock data
   People carried over from conv-data.js keep the same names and
   vehicles, so the two pages describe one dealership.
   ============================================================ */

const LEADS = [
  {
    id: 'tara', name: 'Tara Nolan', initials: 'TN',
    status: 'qualified', flagged: true,
    source: 'Website', channel: 'chat',
    vehicle: '2020 Grand Cherokee Limited',
    assigned: null, ageH: 11, lastTouch: '11h ago',
    phone: '555-0148', email: null,
    contactRisk: false,
    detail: {
      leadId: 'Lead #4471', first: 'Yesterday, 10:11 PM · website chat',
      contact: [['phone','555-0148','typed'],['mail','Not given',null],['globe','Website · organic',null]],
      vehicle: { name: '2020 Jeep Grand Cherokee Limited', meta: '38,400 mi · stock #RA-2291 · 62 days on lot', price: '$29,200' },
      captured: [['Name','Tara Nolan','typed'],['Phone','555-0148','typed'],['Best time','Mornings','typed'],['Budget','$26,000 cash','inferred'],['Trade-in','None mentioned',null],['Reason','Comparing against a Fairview listing','inferred']],
      conversations: [['chat','Website chat','Yesterday 10:11 PM','Flagged — price floor','destructive']],
      appointments: [],
      history: [['Flagged for a person — price floor','Today · 8:26 AM',true],['Phone number captured','Today · 8:27 AM',false],['Liner quoted stock #RA-2291','Yesterday · 10:11 PM',false],['First contact — website chat','Yesterday · 10:11 PM',false]]
    }
  },
  {
    id: 'renee', name: 'Renee Solano', initials: 'RS',
    status: 'qualifying', flagged: true,
    source: 'AutoTrader', channel: 'email',
    vehicle: '2019 Equinox LT',
    assigned: 'Marcus Idowu', ageH: 5, lastTouch: '5h ago',
    phone: '555-0733', email: 'r.solano@mailbox.com',
    contactRisk: false,
    detail: {
      leadId: 'Lead #4468 · $212 acquisition', first: 'Today, 3:29 AM · AutoTrader',
      contact: [['phone','555-0733','typed'],['mail','r.solano@mailbox.com','marketplace'],['globe','AutoTrader · paid lead',null]],
      vehicle: { name: '2019 Chevrolet Equinox LT', meta: '44,100 mi · stock #RA-2107 · 38 days on lot', price: '$18,750' },
      captured: [['Name','Renee Solano','marketplace'],['Phone','555-0733','typed'],['Best time','After 2 PM','typed'],['Credit','Repossession, 2023','typed'],['Timeline','Ready this week','inferred']],
      conversations: [['email','AutoTrader email','Today 3:31 AM','Flagged — financing','destructive']],
      appointments: [],
      history: [['Flagged for a person — financing','Today · 3:40 AM',true],['Assigned to Marcus Idowu','Today · 3:41 AM',false],['Liner made first contact','Today · 3:31 AM',false]]
    }
  },
  {
    id: 'unknown', name: 'Grand Cherokee caller', initials: '··',
    anonymous: true,
    status: 'new', flagged: true,
    source: 'Phone', channel: 'voice',
    vehicle: '—',
    assigned: null, ageH: 9, lastTouch: '9h ago',
    phone: '555-0621', email: null,
    contactRisk: false,
    detail: {
      leadId: 'Lead #4470 · identity not confirmed', first: 'Yesterday, 11:47 PM · inbound call',
      contact: [['phone','555-0621','caller-id'],['mail','Not given',null],['globe','Inbound phone · after hours',null]],
      vehicle: null,
      captured: [['Name','Not given',null],['Phone','555-0621','caller-id'],['Asked for','Marcus (finance)','typed'],['Prior contact','Implied — "he knows the situation"','inferred']],
      conversations: [['voice','Inbound call · 1:08','Yesterday 11:47 PM','Flagged — manager request','destructive']],
      appointments: [],
      history: [['Flagged for a person — manager request','Yesterday · 11:48 PM',true],['Call ended — 1:08','Yesterday · 11:48 PM',false]]
    }
  },
  {
    id: 'hector', name: 'Hector Villalba', initials: 'HV',
    status: 'qualifying', flagged: false, live: true,
    source: 'Website', channel: 'voice',
    vehicle: '2021 Tacoma TRD',
    assigned: null, ageH: 0, lastTouch: 'on call now',
    phone: '555-0904', email: null,
    contactRisk: false,
    detail: {
      leadId: 'Lead #4479 · live now', first: 'Today, 9:10 AM · website voice',
      contact: [['phone','555-0904','caller-id'],['mail','Not given',null],['globe','Website · voice widget',null]],
      vehicle: { name: '2021 Toyota Tacoma TRD Off-Road', meta: '41,000 mi · stock #RA-2318 · 12 days on lot', price: '$34,900' },
      captured: [['Name','Hector Villalba','typed'],['Phone','555-0904','caller-id'],['Interest','Accident history, mileage','inferred']],
      conversations: [['voice','Website voice · in progress','Today 9:10 AM','Live','success']],
      appointments: [],
      history: [['Call started','Today · 9:10 AM',true]]
    }
  },
  {
    id: 'colin', name: 'Colin Hart', initials: 'CH',
    status: 'new', flagged: false,
    source: 'CarGurus', channel: 'email',
    vehicle: '2021 Ram 1500',
    assigned: null, ageH: 3, lastTouch: '3h ago',
    phone: '555-0311', email: 'chart@mailbox.com',
    contactRisk: false,
    detail: {
      leadId: 'Lead #4474 · $198 acquisition', first: 'Today, 6:04 AM · CarGurus',
      contact: [['phone','555-0311','marketplace'],['mail','chart@mailbox.com','marketplace'],['globe','CarGurus · paid lead',null]],
      vehicle: { name: '2021 Ram 1500 Big Horn', meta: '52,300 mi · stock #RA-2240 · 21 days on lot', price: '$31,400' },
      captured: [['Name','Colin Hart','marketplace'],['Phone','555-0311','marketplace'],['Email','chart@mailbox.com','marketplace']],
      conversations: [['email','CarGurus email','Today 6:05 AM','Awaiting reply','warning']],
      appointments: [],
      history: [['Awaiting reply — nudge at 2:00 PM','Today · 6:05 AM',true],['Liner made first contact','Today · 6:05 AM',false]]
    }
  },
  {
    id: 'gil', name: 'Gil Otonye', initials: 'GO',
    status: 'appointment', flagged: false,
    source: 'Cars.com', channel: 'email',
    vehicle: '2022 Tucson SEL',
    assigned: 'Jess Rowe', ageH: 0, lastTouch: '20m ago',
    phone: '555-0455', email: 'g.otonye@mailbox.com',
    contactRisk: false,
    detail: {
      leadId: 'Lead #4477 · $205 acquisition', first: 'Today, 8:46 AM · Cars.com',
      contact: [['phone','555-0455','typed'],['mail','g.otonye@mailbox.com','marketplace'],['globe','Cars.com · paid lead',null]],
      vehicle: { name: '2022 Hyundai Tucson SEL', meta: '28,900 mi · stock #RA-2301 · 9 days on lot', price: '$26,300' },
      captured: [['Name','Gil Otonye','marketplace'],['Phone','555-0455','typed'],['Appointment','Friday 5:30 PM','typed'],['Timeline','Buying this week','inferred']],
      conversations: [['email','Cars.com SMS','Today 8:46 AM','Booked','success']],
      appointments: [['Test drive','Today 5:30 PM','2022 Tucson SEL','Confirmed','success']],
      history: [['Appointment booked — Fri 5:30 PM','Today · 8:52 AM',true],['Assigned to Jess Rowe','Today · 8:53 AM',false],['Liner made first contact','Today · 8:46 AM',false]]
    }
  },
  {
    id: 'devon', name: 'Devon Clarke', initials: 'DC',
    status: 'appointment', flagged: false,
    source: 'Website', channel: 'voice',
    vehicle: '2019 F-150 XLT',
    assigned: 'Dana Mercer', ageH: 7, lastTouch: '7h ago',
    phone: '555-0288', email: null,
    contactRisk: false,
    detail: {
      leadId: 'Lead #4465', first: 'Today, 2:14 AM · website voice',
      contact: [['phone','555-0288','typed'],['mail','Not given',null],['globe','Website · voice widget',null]],
      vehicle: { name: '2019 Ford F-150 XLT', meta: '61,200 mi · stock #RA-2188 · 44 days on lot', price: '$27,900' },
      captured: [['Name','Devon Clarke','typed'],['Phone','555-0288','typed'],['Appointment','Today 4:00 PM','typed'],['Trade-in','2012 Ranger','typed']],
      conversations: [['voice','Website voice · 4:02','Today 2:14 AM','Booked','success']],
      appointments: [['Test drive','Today 4:00 PM','2019 F-150 XLT','Unconfirmed','warning']],
      history: [['Confirmation text unanswered','Today · 2:20 AM',true],['Appointment booked','Today · 2:19 AM',false],['Call answered by Liner','Today · 2:14 AM',false]]
    }
  },
  {
    id: 'amara', name: 'Amara Osei', initials: 'AO',
    status: 'appointment', flagged: false,
    source: 'Website', channel: 'chat',
    vehicle: '2022 Mazda CX-5',
    assigned: null, ageH: 4, lastTouch: '4h ago',
    phone: null, email: 'a.osei@mailbox.com',
    contactRisk: true,
    detail: {
      leadId: 'Lead #4472', first: 'Today, 5:02 AM · website chat',
      contact: [['phone','No phone number',null],['mail','a.osei@mailbox.com','typed'],['globe','Website · organic',null]],
      vehicle: { name: '2022 Mazda CX-5 Touring', meta: '31,700 mi · stock #RA-2295 · 16 days on lot', price: '$25,400' },
      captured: [['Name','Amara Osei','typed'],['Phone','Not given',null],['Email','a.osei@mailbox.com','typed'],['Appointment','Today 5:30 PM','typed']],
      conversations: [['chat','Website chat','Today 5:02 AM','Awaiting confirmation','warning']],
      appointments: [['Test drive','Today 5:30 PM','2022 Mazda CX-5','Unconfirmed','warning']],
      history: [['Confirmation email unanswered','Today · 5:10 AM',true],['Appointment booked','Today · 5:09 AM',false],['First contact — website chat','Today · 5:02 AM',false]]
    }
  },
  {
    id: 'nadia', name: 'Nadia Fisk', initials: 'NF',
    status: 'appointment', flagged: false,
    source: 'Phone', channel: 'voice',
    vehicle: '2020 RAV4 XLE',
    assigned: 'Jess Rowe', ageH: 2, lastTouch: '2h ago',
    phone: '555-0197', email: null,
    contactRisk: false,
    detail: {
      leadId: 'Lead #4476', first: 'Today, 6:38 AM · inbound call',
      contact: [['phone','555-0197','caller-id'],['mail','Not given',null],['globe','Inbound phone · after hours',null]],
      vehicle: { name: '2020 Toyota RAV4 XLE', meta: '39,800 mi · stock #RA-2276 · 27 days on lot', price: '$24,100' },
      captured: [['Name','Nadia Fisk','typed'],['Phone','555-0197','caller-id'],['Appointment','Saturday 9:30 AM','typed'],['Financing','Pre-approved elsewhere','typed']],
      conversations: [['voice','Inbound call · 3:12','Today 6:38 AM','Booked','success']],
      appointments: [['Test drive','Saturday 9:30 AM','2020 RAV4 XLE','Confirmed','success']],
      history: [['Appointment booked — Sat 9:30 AM','Today · 6:41 AM',true],['Assigned to Jess Rowe','Today · 6:42 AM',false]]
    }
  },
  {
    id: 'priya', name: 'Priya Raman', initials: 'PR',
    status: 'qualifying', flagged: false,
    source: 'Website', channel: 'chat',
    vehicle: '2022 Mazda CX-5',
    assigned: null, ageH: 2, lastTouch: '2h ago',
    phone: '555-0512', email: null,
    contactRisk: false,
    detail: {
      leadId: 'Lead #4478', first: 'Today, 7:40 AM · website chat',
      contact: [['phone','555-0512','typed'],['mail','Not given',null],['globe','Website · paid search',null]],
      vehicle: { name: '2022 Mazda CX-5 Touring', meta: '31,700 mi · stock #RA-2295 · 16 days on lot', price: '$25,400' },
      captured: [['Name','Priya Raman','typed'],['Phone','555-0512','typed'],['Trade-in','2016 Corolla','typed'],['Budget','Around $400/mo','inferred']],
      conversations: [['chat','Website chat','Today 7:40 AM','Qualifying','muted']],
      appointments: [],
      history: [['Trade-in captured','Today · 7:48 AM',true],['First contact — website chat','Today · 7:40 AM',false]]
    }
  },
  {
    id: 'ray', name: 'Ray Alvarez', initials: 'RA',
    status: 'lost', flagged: false,
    source: 'Website', channel: 'chat',
    vehicle: '2017 Civic EX',
    assigned: null, ageH: 2, lastTouch: '2h ago',
    phone: null, email: null,
    contactRisk: true,
    detail: {
      leadId: 'Lead #4475', first: 'Today, 7:15 AM · website chat',
      contact: [['phone','No phone number',null],['mail','Not given',null],['globe','Website · organic',null]],
      vehicle: { name: '2017 Honda Civic EX', meta: '72,400 mi · stock #RA-2044 · 91 days on lot', price: '$16,300' },
      captured: [['Name','Ray Alvarez','typed'],['Phone','Not given',null],['Intent','Said he would drop by','inferred']],
      conversations: [['chat','Website chat','Today 7:15 AM','Closed out','muted']],
      appointments: [],
      history: [['Conversation closed — no contact left','Today · 7:19 AM',true],['Liner confirmed vehicle availability','Today · 7:16 AM',false]]
    }
  },
  {
    id: 'sol', name: 'Sol Bergman', initials: 'SB',
    status: 'new', flagged: false,
    source: 'Phone', channel: 'voice',
    vehicle: 'Service enquiry',
    assigned: null, ageH: 8, lastTouch: '8h ago',
    phone: '555-0840', email: null,
    contactRisk: false,
    detail: {
      leadId: 'Lead #4463', first: 'Today, 1:05 AM · inbound call',
      contact: [['phone','555-0840','caller-id'],['mail','Not given',null],['globe','Inbound phone · after hours',null]],
      vehicle: null,
      captured: [['Name','Sol Bergman','typed'],['Phone','555-0840','caller-id'],['Enquiry','Service, not sales','typed']],
      conversations: [['voice','Inbound call · 0:48','Today 1:05 AM','Routed to service','muted']],
      appointments: [],
      history: [['Routed to service voicemail','Today · 1:06 AM',true]]
    }
  }
];