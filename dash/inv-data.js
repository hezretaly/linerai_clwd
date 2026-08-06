/* ============================================================
   Liner AI — Inventory, mock data
   The vehicle list exactly as Liner sees it, plus feed health.
   Stock numbers match the vehicles referenced on the other pages.
   ============================================================ */

const FEED = {
  source: 'riversideauto.com/inventory',
  method: 'Public listings scrape',
  lastSync: 'Today, 8:41 AM',
  nextSync: '9:11 AM',
  every: 'every 30 minutes',
  total: 62,
  syncedOk: 59,
  issues: 3
};

/* problem: null | 'sold' | 'stale' | 'noprice' */
const VEHICLES = [
  {
    id:'RA-2291', year:2020, make:'Jeep', model:'Grand Cherokee Limited',
    price:29200, mileage:38400, days:62, status:'available', problem:null,
    title:'Clean', trim:'Limited 4WD', colour:'White on black leather',
    lastSeen:'Today, 8:41 AM', photos:14, quoted:6,
    overrides:{ discuss:true, warranty:false, holdPrice:true, note:'Priced firm. Reduced $1,100 on 24 Jul, no further movement without a manager.' }
  },
  {
    id:'RA-2188', year:2019, make:'Ford', model:'F-150 XLT',
    price:27900, mileage:61200, days:44, status:'available', problem:null,
    title:'Clean', trim:'XLT SuperCrew 4x4', colour:'Magnetic grey',
    lastSeen:'Today, 8:41 AM', photos:18, quoted:9,
    overrides:{ discuss:true, warranty:false, holdPrice:false, note:'' }
  },
  {
    id:'RA-2318', year:2021, make:'Toyota', model:'Tacoma TRD Off-Road',
    price:34900, mileage:41000, days:12, status:'available', problem:null,
    title:'Clean', trim:'TRD Off-Road Double Cab', colour:'Cement grey',
    lastSeen:'Today, 8:41 AM', photos:22, quoted:4,
    overrides:{ discuss:true, warranty:true, holdPrice:false, note:'Remaining factory powertrain warranty to 60k — mention it.' }
  },
  {
    id:'RA-2035', year:2018, make:'Chevrolet', model:'Silverado LT',
    price:26400, mileage:68900, days:104, status:'sold', problem:'sold',
    title:'Clean', trim:'LT Crew Cab 4x4', colour:'Summit white',
    lastSeen:'Today, 8:41 AM', photos:11, quoted:2,
    soldOn:'Wednesday, 29 Jul', soldBy:'Tomas Vega',
    overrides:{ discuss:true, warranty:false, holdPrice:false, note:'' }
  },
  {
    id:'RA-2301', year:2022, make:'Hyundai', model:'Tucson SEL',
    price:26300, mileage:28900, days:9, status:'available', problem:null,
    title:'Clean', trim:'SEL AWD', colour:'Amazon grey',
    lastSeen:'Today, 8:41 AM', photos:20, quoted:7,
    overrides:{ discuss:true, warranty:true, holdPrice:false, note:'Factory warranty transfers. Worth leading with.' }
  },
  {
    id:'RA-2295', year:2022, make:'Mazda', model:'CX-5 Touring',
    price:25400, mileage:31700, days:16, status:'available', problem:null,
    title:'Clean', trim:'Touring AWD', colour:'Machine grey',
    lastSeen:'Today, 8:41 AM', photos:17, quoted:5,
    overrides:{ discuss:true, warranty:false, holdPrice:false, note:'' }
  },
  {
    id:'RA-2240', year:2021, make:'Ram', model:'1500 Big Horn',
    price:31400, mileage:52300, days:21, status:'available', problem:null,
    title:'Clean', trim:'Big Horn Crew Cab', colour:'Billet silver',
    lastSeen:'Today, 8:41 AM', photos:16, quoted:3,
    overrides:{ discuss:true, warranty:false, holdPrice:false, note:'Tow package included — buyers ask about this constantly.' }
  },
  {
    id:'RA-2107', year:2019, make:'Chevrolet', model:'Equinox LT',
    price:18750, mileage:44100, days:38, status:'available', problem:null,
    title:'Clean', trim:'LT AWD', colour:'Nightfall blue',
    lastSeen:'Today, 8:41 AM', photos:13, quoted:4,
    overrides:{ discuss:true, warranty:false, holdPrice:false, note:'' }
  },
  {
    id:'RA-2276', year:2020, make:'Toyota', model:'RAV4 XLE',
    price:24100, mileage:39800, days:27, status:'available', problem:null,
    title:'Clean', trim:'XLE AWD', colour:'Silver sky',
    lastSeen:'Today, 8:41 AM', photos:19, quoted:6,
    overrides:{ discuss:true, warranty:false, holdPrice:false, note:'' }
  },
  {
    id:'RA-2044', year:2017, make:'Honda', model:'Civic EX',
    price:16300, mileage:72400, days:91, status:'available', problem:null,
    title:'Clean', trim:'EX Sedan', colour:'Taffeta white',
    lastSeen:'Today, 8:41 AM', photos:9, quoted:8,
    overrides:{ discuss:false, warranty:false, holdPrice:false, note:'Do not discuss. Waiting on a transmission diagnosis — pull from the floor if it comes back bad.' }
  },
  {
    id:'RA-2162', year:2019, make:'Subaru', model:'Outback Premium',
    price:null, mileage:57600, days:33, status:'available', problem:'noprice',
    title:'Clean', trim:'Premium AWD', colour:'Crystal black',
    lastSeen:'Today, 8:41 AM', photos:12, quoted:0,
    overrides:{ discuss:true, warranty:false, holdPrice:false, note:'' }
  },
  {
    id:'RA-1998', year:2016, make:'Subaru', model:'Forester 2.5i',
    price:14900, mileage:88200, days:127, status:'available', problem:'stale',
    title:'Clean', trim:'2.5i Premium', colour:'Sepia bronze',
    lastSeen:'25 Jul, 8:41 AM', photos:8, quoted:1,
    overrides:{ discuss:true, warranty:false, holdPrice:false, note:'' }
  },
  {
    id:'RA-2210', year:2020, make:'Kia', model:'Sportage LX',
    price:19800, mileage:48300, days:29, status:'available', problem:null,
    title:'Clean', trim:'LX AWD', colour:'Steel grey',
    lastSeen:'Today, 8:41 AM', photos:15, quoted:2,
    overrides:{ discuss:true, warranty:false, holdPrice:false, note:'' }
  },
  {
    id:'RA-2255', year:2021, make:'Nissan', model:'Rogue SV',
    price:23600, mileage:35100, days:18, status:'available', problem:null,
    title:'Clean', trim:'SV AWD', colour:'Gun metallic',
    lastSeen:'Today, 8:41 AM', photos:21, quoted:3,
    overrides:{ discuss:true, warranty:false, holdPrice:false, note:'' }
  }
];

/* which conversations quoted a given vehicle — drives the blast radius panel */
const QUOTED_IN = {
  'RA-2035': [
    ['Marcus Feld', 'Yesterday 9:14 PM', 'Website chat', 'Liner said it was available'],
    ['Wendy Alcott', 'Yesterday 11:02 PM', 'Website chat', 'Liner said it was available']
  ],
  'RA-2291': [['Tara Nolan', 'Yesterday 10:11 PM', 'Website chat', 'Quoted $29,200']],
  'RA-2318': [['Hector Villalba', 'Today 9:10 AM', 'Website voice', 'Quoted $34,900']]
};