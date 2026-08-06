/* ============================================================
   Liner AI — Team, mock data
   ============================================================ */

const MEMBERS = [
  { id:'dana', name:'Dana Mercer', initials:'DM', role:'manager', title:'Sales manager',
    email:'dana@riversideauto.com', phone:'555-0102', status:'active',
    shift:'Mon–Fri · 8:00 AM – 6:00 PM', rotation:true, dutyTonight:false,
    openLeads:1, closedMonth:9, avgResponse:'6m', takeovers:14, lastActive:'Now',
    notify:{ flags:'SMS + dashboard', bookings:'Dashboard only', digest:'Daily at 8:00 AM' } },

  { id:'marcus', name:'Marcus Idowu', initials:'MI', role:'manager', title:'Finance manager',
    email:'marcus@riversideauto.com', phone:'555-0104', status:'active',
    shift:'Tue–Sat · 9:00 AM – 6:00 PM', rotation:false, dutyTonight:false,
    openLeads:3, closedMonth:6, avgResponse:'21m', takeovers:7, lastActive:'12 min ago',
    notify:{ flags:'SMS + dashboard', bookings:'Off', digest:'Off' } },

  { id:'jess', name:'Jess Rowe', initials:'JR', role:'rep', title:'Sales rep',
    email:'jess@riversideauto.com', phone:'555-0107', status:'active',
    shift:'Mon–Sat · 8:00 AM – 4:00 PM', rotation:true, dutyTonight:false,
    openLeads:7, closedMonth:11, avgResponse:'9m', takeovers:22, lastActive:'3 min ago',
    notify:{ flags:'Dashboard only', bookings:'SMS + dashboard', digest:'Daily at 8:00 AM' } },

  { id:'tomas', name:'Tomas Vega', initials:'TV', role:'rep', title:'Sales rep',
    email:'tomas@riversideauto.com', phone:'555-0109', status:'active',
    shift:'Wed–Sun · 10:00 AM – 6:00 PM', rotation:true, dutyTonight:true,
    openLeads:5, closedMonth:8, avgResponse:'14m', takeovers:16, lastActive:'1 hr ago',
    notify:{ flags:'SMS + dashboard', bookings:'SMS + dashboard', digest:'Off' } },

  { id:'ken', name:'Ken Abara', initials:'KA', role:'admin', title:'General manager',
    email:'ken@riversideauto.com', phone:'555-0100', status:'active',
    shift:'Mon–Fri · 9:00 AM – 5:00 PM', rotation:false, dutyTonight:false,
    openLeads:0, closedMonth:0, avgResponse:'—', takeovers:0, lastActive:'Yesterday',
    notify:{ flags:'Off', bookings:'Off', digest:'Weekly on Monday' } },

  { id:'priya', name:'Priya Nandi', initials:'PN', role:'rep', title:'BDC rep',
    email:'priya@riversideauto.com', phone:null, status:'invited',
    shift:'Not set', rotation:false, dutyTonight:false,
    openLeads:0, closedMonth:0, avgResponse:'—', takeovers:0, lastActive:'Invited 2 days ago',
    notify:{ flags:'—', bookings:'—', digest:'—' } }
];

const ROLES = {
  rep:     { label:'Sales rep', tint:'#EEF1F5', ink:'#475569' },
  manager: { label:'Manager',   tint:'#E8F2FF', ink:'#0062CC' },
  admin:   { label:'Admin',     tint:'#F1EAFE', ink:'#6D28D9' }
};

/* what each role can do — rep / manager / admin */
const PERMISSIONS = [
  ['See conversations assigned to them',        true,  true,  true ],
  ['See every conversation',                    false, true,  true ],
  ['Take over and reply as the dealership',     true,  true,  true ],
  ['Claim leads from the unclaimed pool',       true,  true,  true ],
  ['Assign leads to other people',              false, true,  true ],
  ['Pause Liner for the whole store',           false, true,  true ],
  ['Edit Liner setup and handoff rules',        false, true,  true ],
  ['Set per-vehicle inventory rules',           false, true,  true ],
  ['Write the prompt override',                 false, false, true ],
  ['Add or remove people',                      false, false, true ],
  ['See billing and change the plan',           false, false, true ]
];

const ASSIGNMENT = {
  roundRobinHours: 12,
  overnightMode: 'Hold in the pool until 8 AM',
  dutyTonight: 'Tomas Vega',
  capPerRep: 8
};