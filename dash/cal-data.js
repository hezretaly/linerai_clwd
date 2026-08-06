/* ============================================================
   Liner AI — Calendar, mock data
   Week of Mon 27 Jul – Sun 2 Aug 2026. "Today" is Fri 31 Jul,
   matching the other pages.
   ============================================================ */

const TODAY = '2026-07-31';

const EVENT_TYPES = {
  testdrive: { label: 'Test drive',      color: '#007AFF', tint: '#E8F2FF' },
  delivery:  { label: 'Delivery',        color: '#16A34A', tint: '#E9F7EE' },
  pickup:    { label: 'Pickup',          color: '#0D9488', tint: '#E6F5F3' },
  appraisal: { label: 'Trade appraisal', color: '#7C3AED', tint: '#F1EAFE' },
  service:   { label: 'Service',         color: '#64748B', tint: '#EEF1F5' },
  followup:  { label: 'Follow-up',       color: '#C26A00', tint: '#FDF1E1' }
};

const EVENTS = [
  /* ---------- Monday 27 ---------- */
  { id:'e01', date:'2026-07-27', start:'10:00', mins:60, type:'testdrive', name:'Warren Diaz',
    vehicle:'2018 Silverado LT', rep:'Jess Rowe', status:'confirmed', bookedBy:'liner',
    phone:'555-0121', source:'Website', note:'Asked about tow rating. Wants to bring his trade.' },
  { id:'e02', date:'2026-07-27', start:'15:00', mins:45, type:'appraisal', name:'Marisol Vance',
    vehicle:'2014 Odyssey EX-L', rep:'Tomas Vega', status:'confirmed', bookedBy:'manual',
    phone:'555-0166', source:'Walk-in', note:'Trade appraisal only. Not shopping yet.' },

  /* ---------- Tuesday 28 ---------- */
  { id:'e03', date:'2026-07-28', start:'09:30', mins:60, type:'testdrive', name:'Ben Okafor',
    vehicle:'2021 Tucson SEL', rep:'Jess Rowe', status:'confirmed', bookedBy:'liner',
    phone:'555-0209', source:'CarGurus', note:'Second visit. Drove the Sportage last week.' },
  { id:'e04', date:'2026-07-28', start:'11:00', mins:90, type:'delivery', name:'Karen Whitfield',
    vehicle:'2019 CR-V EX', rep:'Tomas Vega', status:'confirmed', bookedBy:'manual',
    phone:'555-0233', source:'Website', note:'Financing signed Friday. Plates ready.' },
  { id:'e05', date:'2026-07-28', start:'16:30', mins:30, type:'followup', name:'Priya Raman',
    vehicle:'2022 Mazda CX-5', rep:'Unassigned', status:'confirmed', bookedBy:'liner',
    phone:'555-0512', source:'Website', note:'Liner scheduled a callback about the trade figure.' },

  /* ---------- Wednesday 29 ---------- */
  { id:'e06', date:'2026-07-29', start:'10:00', mins:60, type:'testdrive', name:'Alan Petrov',
    vehicle:'2020 RAV4 XLE', rep:'Jess Rowe', status:'cancelled', bookedBy:'liner',
    phone:'555-0344', source:'AutoTrader', note:'Cancelled — bought elsewhere.' },
  { id:'e07', date:'2026-07-29', start:'13:00', mins:45, type:'service', name:'Sol Bergman',
    vehicle:'2015 Escape', rep:'Service desk', status:'confirmed', bookedBy:'liner',
    phone:'555-0840', source:'Phone', note:'Routed by Liner to service. Not a sales lead.' },
  { id:'e08', date:'2026-07-29', start:'17:00', mins:60, type:'testdrive', name:'Dionne Marsh',
    vehicle:'2021 Ram 1500', rep:'Tomas Vega', status:'confirmed', bookedBy:'manual',
    phone:'555-0377', source:'Walk-in', note:'' },

  /* ---------- Thursday 30 ---------- */
  { id:'e09', date:'2026-07-30', start:'09:00', mins:60, type:'testdrive', name:'Lucia Bracco',
    vehicle:'2017 Civic EX', rep:'Jess Rowe', status:'confirmed', bookedBy:'liner',
    phone:'555-0418', source:'Cars.com', note:'' },
  { id:'e10', date:'2026-07-30', start:'14:00', mins:90, type:'delivery', name:'Owen Tsai',
    vehicle:'2022 Tucson SEL', rep:'Dana Mercer', status:'confirmed', bookedBy:'manual',
    phone:'555-0455', source:'Cars.com', note:'' },
  { id:'e11', date:'2026-07-30', start:'18:00', mins:30, type:'followup', name:'Colin Hart',
    vehicle:'2021 Ram 1500', rep:'Unassigned', status:'confirmed', bookedBy:'liner',
    phone:'555-0311', source:'CarGurus', note:'Liner nudge if still unread by 2 PM Friday.' },

  /* ---------- Friday 31 — today ---------- */
  { id:'e12', date:'2026-07-31', start:'11:00', mins:60, type:'testdrive', name:'Sasha Denholm',
    vehicle:'2019 F-150 XLT', rep:'Jess Rowe', status:'confirmed', bookedBy:'manual',
    phone:'555-0490', source:'Website', note:'' },
  { id:'e13', date:'2026-07-31', start:'15:00', mins:45, type:'appraisal', name:'Janet Whitfield',
    vehicle:'2015 Odyssey', rep:'Tomas Vega', status:'confirmed', bookedBy:'liner',
    phone:'555-0507', source:'Phone', note:'Bringing the Odyssey for appraisal. Confirmed by SMS.' },
  { id:'e14', date:'2026-07-31', start:'16:00', mins:60, type:'testdrive', name:'Devon Clarke',
    vehicle:'2019 F-150 XLT', rep:'Dana Mercer', status:'unconfirmed', bookedBy:'liner',
    phone:'555-0288', source:'Website', note:'Booked at 2:14 AM. Confirmation text sent 2:20 AM, no reply.' },
  { id:'e15', date:'2026-07-31', start:'17:30', mins:60, type:'testdrive', name:'Amara Osei',
    vehicle:'2022 Mazda CX-5', rep:'Unassigned', status:'unconfirmed', bookedBy:'liner',
    phone:null, source:'Website', note:'No phone number on file. Confirmation sent by email only.' },
  { id:'e16', date:'2026-07-31', start:'17:30', mins:45, type:'pickup', name:'Gil Otonye',
    vehicle:'2022 Tucson SEL', rep:'Jess Rowe', status:'confirmed', bookedBy:'liner',
    phone:'555-0455', source:'Cars.com', note:'Booked this morning. Directions sent by SMS.' },

  /* ---------- Saturday 1 ---------- */
  { id:'e17', date:'2026-08-01', start:'09:30', mins:60, type:'testdrive', name:'Nadia Fisk',
    vehicle:'2020 RAV4 XLE', rep:'Jess Rowe', status:'confirmed', bookedBy:'liner',
    phone:'555-0197', source:'Phone', note:'Pre-approved elsewhere. Bring finance in early.' },
  { id:'e18', date:'2026-08-01', start:'11:00', mins:60, type:'testdrive', name:'Hollis Reyner',
    vehicle:'2020 Grand Cherokee', rep:'Tomas Vega', status:'confirmed', bookedBy:'manual',
    phone:'555-0532', source:'Walk-in', note:'' },
  { id:'e19', date:'2026-08-01', start:'13:00', mins:90, type:'delivery', name:'Ruth Angelou',
    vehicle:'2018 Silverado LT', rep:'Dana Mercer', status:'confirmed', bookedBy:'manual',
    phone:'555-0548', source:'Website', note:'' },
  { id:'e20', date:'2026-08-01', start:'15:30', mins:45, type:'appraisal', name:'Teo Marchetti',
    vehicle:'2013 Tacoma', rep:'Tomas Vega', status:'unconfirmed', bookedBy:'liner',
    phone:'555-0563', source:'AutoTrader', note:'Booked overnight. Awaiting confirmation.' },

  /* ---------- Sunday 2 — closed ---------- */
  { id:'e21', date:'2026-08-02', start:'12:00', mins:30, type:'followup', name:'Renee Solano',
    vehicle:'2019 Equinox LT', rep:'Marcus Idowu', status:'confirmed', bookedBy:'manual',
    phone:'555-0733', source:'AutoTrader', note:'Finance callback. Marcus is in Sunday.' },

  /* ---------- elsewhere in July, for the month view ---------- */
  { id:'e22', date:'2026-07-21', start:'10:00', mins:60, type:'testdrive', name:'Ivy Sandoval',
    vehicle:'2019 Equinox LT', rep:'Jess Rowe', status:'confirmed', bookedBy:'liner',
    phone:'555-0611', source:'Website', note:'' },
  { id:'e23', date:'2026-07-23', start:'14:00', mins:90, type:'delivery', name:'Peter Halloran',
    vehicle:'2021 Tucson SEL', rep:'Dana Mercer', status:'confirmed', bookedBy:'manual',
    phone:'555-0627', source:'Cars.com', note:'' },
  { id:'e24', date:'2026-07-24', start:'11:30', mins:45, type:'appraisal', name:'Nell Braithwaite',
    vehicle:'2016 Forester', rep:'Tomas Vega', status:'confirmed', bookedBy:'liner',
    phone:'555-0639', source:'Phone', note:'' },
  { id:'e25', date:'2026-08-04', start:'10:00', mins:60, type:'testdrive', name:'Ravi Chandra',
    vehicle:'2022 Mazda CX-5', rep:'Jess Rowe', status:'confirmed', bookedBy:'liner',
    phone:'555-0655', source:'Website', note:'' }
];