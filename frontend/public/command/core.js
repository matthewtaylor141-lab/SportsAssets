/* BETTORTOKEN COMMAND · data contracts and read-only transport.
 * No trading method, venue client, credential reader, or automatic demo fallback.
 * Display arithmetic is not a replacement for the accounting ledger. */
(function (root) {
  'use strict';
  const VERSION = '1.0.0';
  const SCHEMA = 'bt.command.v1';
  const finite = v => typeof v === 'number' && Number.isFinite(v);
  const round = v => Math.round((v + Number.EPSILON) * 100) / 100;
  const num = (v, fallback = null) => finite(v) ? v : fallback;
  const sum = (xs, f) => round(xs.reduce((a, x) => a + f(x), 0));
  const clone = obj => JSON.parse(JSON.stringify(obj));
  const text = v => String(v ?? '');
  const esc = v => text(v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const usd = (v, digits = 0) => finite(v) ? new Intl.NumberFormat('en-US', {style:'currency',currency:'USD',minimumFractionDigits:digits,maximumFractionDigits:digits}).format(v) : '—';
  const signed = (v, digits = 0) => finite(v) ? `${v > 0 ? '+' : v < 0 ? '−' : ''}${usd(Math.abs(v), digits)}` : '—';
  const count = v => finite(v) ? new Intl.NumberFormat('en-US', {maximumFractionDigits:0}).format(v) : '—';
  const price = v => finite(v) ? `${(v * 100).toFixed(1)}¢` : '—';
  const pct = (v, d=1) => finite(v) ? `${(v * 100).toFixed(d)}%` : '—';
  const clock = iso => Number.isFinite(Date.parse(iso)) ? new Date(iso).toISOString().slice(11,19) : '—';
  const date = iso => Number.isFinite(Date.parse(iso)) ? new Date(iso).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric',timeZone:'UTC'}) : '—';
  const duration = s => !finite(s) ? '—' : s < 60 ? `${Math.floor(s)}s` : s < 3600 ? `${Math.floor(s/60)}m ${Math.floor(s%60)}s` : `${Math.floor(s/3600)}h ${Math.floor(s%3600/60)}m`;
  const safeCsv = v => { const s=text(v); return '"'+(/^[=+\-@\t\r]/.test(s) ? "'"+s : s).replace(/"/g,'""')+'"'; };
  function toCsv(headers, rows) { return [headers,...rows].map(r=>r.map(safeCsv).join(',')).join('\r\n'); }
  function stable(obj) { if(Array.isArray(obj)) return '['+obj.map(stable).join(',')+']'; if(obj&&typeof obj==='object') return '{'+Object.keys(obj).sort().map(k=>JSON.stringify(k)+':'+stable(obj[k])).join(',')+'}'; return JSON.stringify(obj); }
  async function digest(obj) { if(!root.crypto?.subtle) return null; const b=await root.crypto.subtle.digest('SHA-256',new TextEncoder().encode(stable(obj))); return [...new Uint8Array(b)].map(x=>x.toString(16).padStart(2,'0')).join(''); }
  function validate(snapshot, options={}) {
    const errors=[]; const s=snapshot;
    if (!s || typeof s!=='object') return {ok:false,errors:['Snapshot must be an object.']};
    if(s.schema!==SCHEMA) errors.push('Unsupported snapshot schema.');
    if(typeof s.snapshotId!=='string'||!s.snapshotId.trim()) errors.push('Missing snapshot identity.');
    if(!Number.isSafeInteger(s.sequence)||s.sequence<0) errors.push('Invalid sequence.');
    if(!['DEMO','LIVE','SHADOW','REPLAY'].includes(s.mode)) errors.push('Unknown mode.');
    if(!['INTERNAL','INVESTOR'].includes(s.audience)) errors.push('Unknown audience.');
    if(options.audience && s.audience!==options.audience) errors.push('Audience projection mismatch.');
    if(!['ILLUSTRATIVE','NATIVE_LEDGER','SHADOW_RESEARCH'].includes(s.source)) errors.push('Source classification required.');
    if(options.remote && (s.mode==='DEMO'||s.source==='ILLUSTRATIVE')) errors.push('Demo data refused on the connected feed.');
    if(s.mode==='LIVE'&&s.source!=='NATIVE_LEDGER') errors.push('Live mode requires native-ledger provenance.');
    if(!s.asOf || !/Z$/.test(s.asOf) || !Number.isFinite(Date.parse(s.asOf))) errors.push('UTC as-of timestamp required.');
    if(Date.parse(s.asOf)>Date.now()+120000) errors.push('Snapshot timestamp is in the future.');
    if(!s.provenance||typeof s.provenance.sourceId!=='string'||typeof s.provenance.accountingBasis!=='string') errors.push('Missing accounting provenance.');
    if(!s.account||typeof s.account!=='object') errors.push('Account object required.');
    else for(const k of ['value','cash','reserved','available','realizedMtd','unrealized','netMtd','grossVolumeMtd']) if(s.account[k]!==null&&!finite(s.account[k])) errors.push(`Invalid account.${k}; use null for unknown.`);
    for(const k of ['positions','orders','decisions','history','activity','services','gates']) if(!Array.isArray(s[k])) errors.push(`${k} must be an array.`);
    if(errors.length) return {ok:false,errors};
    const required = {
      position:['title','outcome','sport','family','venue','leg','intent','reason','openedAt','asOf','markBasis'],
      order:['title','outcome','venue','side','status','at','reason'],
      decision:['title','outcome','sport','family','venue','action','state','gate','blocker','reason','evidence','at']
    };
    for(const [kind,rows] of [['position',s.positions],['order',s.orders],['decision',s.decisions]]) {
      const ids=new Set();
      if(rows.length>10000)errors.push('Snapshot exceeds 10,000 records per table. Use a paginated read model.');
      for(const r of rows){
        if(!r||typeof r.id!=='string'||!r.id||ids.has(r.id)){ errors.push(`Missing or duplicate ${kind} id.`); continue; }
        ids.add(r.id);
        if(options.remote&&r.evidence==='ILLUSTRATIVE')errors.push('Illustrative records refused on the connected feed.');
        for(const k of required[kind])if(typeof r[k]!=='string'||!r[k])errors.push(`${kind}.${k} is required.`);
        if(kind!=='decision'&&(!r.marketId||!r.eventId)) errors.push(`${kind} market and event identity required.`);
        for(const k of ['quantity','filled','price','entry','mark','value','cost','unrealized','netEv','p10','p90','pPositive']) if(k in r && r[k]!==null&&!finite(r[k])) errors.push(`${kind}.${k} is not finite.`);
        for(const k of ['price','entry','mark','pPositive'])if(finite(r[k])&&(r[k]<0||r[k]>1))errors.push(`${kind}.${k} must be within [0,1].`);
        if(kind!=='decision'&&!finite(r.quantity))errors.push('Record quantity is required.');
        if(kind==='order'&&!finite(r.filled))errors.push('Filled quantity is required.');
        if(finite(r.quantity)&&r.quantity<0) errors.push('Negative quantity refused.');
        if(kind==='order' && finite(r.filled) && (r.filled<0||r.filled>r.quantity)) errors.push('Invalid order fill quantity.');
      }
    }
    if(s.history.some(r=>!Number.isFinite(Date.parse(r.at))||!finite(r.value))) errors.push('Invalid portfolio history point.');
    if(s.audience==='INVESTOR' && (s.decisions.length || s.orders.length || s.positions.length || s.models?.length)) errors.push('Investor projection contains internal records.');
    if(options.previous && s.sequence<=options.previous.sequence) errors.push('Out-of-order or duplicate snapshot refused.');
    return {ok:!errors.length,errors};
  }
  function freshness(s, now=Date.now(), staleMs=45000, connection='connected') {
    if(!s) return {status:'DISCONNECTED',age:null};
    if(s.mode==='DEMO') return {status:'DEMO',age:null};
    const age=now-Date.parse(s.asOf);
    if(!finite(age)||age< -120000) return {status:'INVALID CLOCK',age:null};
    if(connection!=='connected') return {status:'DISCONNECTED',age:Math.max(age,0)};
    return {status:age>staleMs?'STALE':s.mode,age:Math.max(age,0)};
  }
  function marketExposure(s, key='sport') {
    const groups={};
    if(s.positions.some(p=>!finite(p.cost)))return [];
    for(const p of s.positions) { if(!finite(p.cost)) continue; const name=p[key]||'Unclassified'; groups[name]=(groups[name]||0)+p.cost; }
    return Object.entries(groups).map(([name,value])=>({name,value:round(value)})).sort((a,b)=>b.value-a.value);
  }
  function projectInvestor(s) {
    if(s.mode!=='DEMO') throw new Error('Live investor data requires the server investor projection.');
    return {...clone(s),audience:'INVESTOR',positions:[],orders:[],decisions:[],models:[],activity:s.activity.filter(a=>a.kind==='report'),allocation:marketExposure(s),counts:{positions:s.positions.length,orders:s.orders.length,events:new Set(s.positions.map(p=>p.eventId)).size}};
  }
  function demoSnapshot() {
    const asOf='2026-09-17T22:30:00Z';
    const raw=[
      ['p1','New York · Boston','New York to win','MLB','Moneyline','Polymarket US','YES',120000,.47,.51,'HOLD','Positive continuation value; no exit instruction.'],
      ['p2','Seattle · Houston','Over 8.5 runs','MLB','Total','Kalshi','OVER',90000,.64,.61,'EXIT WATCH','Re-evaluate passive exit as the spread narrows.'],
      ['p3','Kansas City · Denver','Kansas City −3.5','NFL','Spread','Polymarket US','YES',75000,.40,.44,'PASSIVE EXIT','Illustrative standing sell order; no order sent by this hub.'],
      ['p4','London · Manchester','Under 2.5 goals','Soccer','Total','Kalshi','UNDER',65000,.39,.36,'HOLD','Unrealized loss is not itself an exit trigger.'],
      ['p5','Miami · Buffalo','Buffalo to win','NFL','Moneyline','Polymarket US','YES',40000,.68,.72,'HOLD','Residual position held within the example event limit.'],
      ['p6','Atlanta · New York','Over 7.5 runs','MLB','Total','Kalshi','OVER',55000,.53,.57,'PAIR REVIEW','Complement economics under review; venue semantics matter.'],
      ['p7','Dortmund · Munich','Dortmund to win','Soccer','Moneyline','Polymarket US','YES',35000,.31,.28,'EXIT WATCH','Price disagreement increased; action remains research-only.'],
      ['p8','Los Angeles · San Diego','Los Angeles −1.5','MLB','Spread','Kalshi','YES',60000,.43,.46,'HOLD','Liquidity and current expected value favor holding in the example.']
    ];
    const positions=raw.map((r,i)=>({id:r[0],eventId:`demo-event-${i+1}`,marketId:`demo-market-${i+1}`,title:r[1],outcome:r[2],sport:r[3],family:r[4],venue:r[5],leg:r[6],quantity:r[7],entry:r[8],mark:r[9],cost:round(r[7]*r[8]),value:round(r[7]*r[9]),unrealized:round(r[7]*(r[9]-r[8])),intent:r[10],reason:r[11],asOf,openedAt:new Date(Date.parse(asOf)-(36+i*13)*60000).toISOString(),markBasis:'Illustrative bid-side liquidation mark',evidence:'ILLUSTRATIVE'}));
    const orders=[
      {id:'O-DM-1048',positionId:'p3',eventId:'demo-event-3',marketId:'demo-market-3',title:'Kansas City · Denver',outcome:'Kansas City −3.5',venue:'Polymarket US',side:'SELL',price:.46,quantity:15000,filled:4500,status:'PARTIAL',at:'2026-09-17T22:24:18Z',reason:'Passive exit · example only'},
      {id:'O-DM-1047',positionId:'p1',eventId:'demo-event-1',marketId:'demo-market-1',title:'New York · Boston',outcome:'New York to win',venue:'Polymarket US',side:'BUY',price:.49,quantity:20000,filled:0,status:'RESTING',at:'2026-09-17T22:27:08Z',reason:'Entry · example only'},
      {id:'O-DM-1046',positionId:'p2',eventId:'demo-event-2',marketId:'demo-market-2',title:'Seattle · Houston',outcome:'Over 8.5 runs',venue:'Kalshi',side:'SELL',price:.63,quantity:10000,filled:0,status:'RESTING',at:'2026-09-17T22:26:22Z',reason:'Exit watch · example only'},
      {id:'O-DM-1045',positionId:'p6',eventId:'demo-event-6',marketId:'demo-market-6',title:'Atlanta · New York',outcome:'Over 7.5 runs',venue:'Kalshi',side:'BUY',price:.54,quantity:25000,filled:1500,status:'PARTIAL',at:'2026-09-17T22:23:47Z',reason:'Research allocation · example only'},
      {id:'O-DM-1044',positionId:'p8',eventId:'demo-event-8',marketId:'demo-market-8',title:'Los Angeles · San Diego',outcome:'Los Angeles −1.5',venue:'Kalshi',side:'BUY',price:.43,quantity:20000,filled:0,status:'RESTING',at:'2026-09-17T22:28:02Z',reason:'Entry · example only'},
      {id:'O-DM-1043',positionId:'p7',eventId:'demo-event-7',marketId:'demo-market-7',title:'Dortmund · Munich',outcome:'Dortmund to win',venue:'Polymarket US',side:'SELL',price:.30,quantity:8000,filled:8000,status:'FILLED',at:'2026-09-17T22:19:30Z',reason:'Historical demo lifecycle'},
    ];
    const decisions=[
      {id:'D-DEMO-831',title:'New York · Boston',outcome:'New York to win',sport:'MLB',family:'Moneyline',venue:'Polymarket US',action:'POST BID',state:'SHADOW',price:.49,netEv:.0082,p10:-.0021,p90:.0194,pPositive:.79,fillProbability:null,quantity:2000,gate:'BLOCKED',blocker:'Fill quantity and dependence model unvalidated',reason:'External consensus and book imbalance agree. Native execution calibration remains unavailable.',evidence:'ILLUSTRATIVE',at:'2026-09-17T22:29:57Z'},
      {id:'D-DEMO-830',title:'Seattle · Houston',outcome:'Over 8.5 runs',sport:'MLB',family:'Total',venue:'Kalshi',action:'PASSIVE EXIT',state:'WATCHING',price:.63,netEv:.0034,p10:-.0062,p90:.0141,pPositive:.62,fillProbability:null,quantity:1000,gate:'BLOCKED',blocker:'Execution-value uncertainty',reason:'An exit is being considered, not instructed. No live recommendation is produced.',evidence:'ILLUSTRATIVE',at:'2026-09-17T22:29:53Z'},
      {id:'D-DEMO-829',title:'London · Manchester',outcome:'Under 2.5 goals',sport:'Soccer',family:'Total',venue:'Kalshi',action:'NO TRADE',state:'REJECTED',price:.37,netEv:-.0026,p10:-.0124,p90:.0052,pPositive:.31,fillProbability:null,quantity:1000,gate:'BLOCKED',blocker:'Net estimated edge below zero',reason:'The spread and expected exit cost absorb the illustrative price discrepancy.',evidence:'ILLUSTRATIVE',at:'2026-09-17T22:29:46Z'},
      {id:'D-DEMO-828',title:'Miami · Buffalo',outcome:'Buffalo to win',sport:'NFL',family:'Moneyline',venue:'Polymarket US',action:'HOLD',state:'WATCHING',price:.72,netEv:.0046,p10:-.0035,p90:.0128,pPositive:.72,fillProbability:null,quantity:1500,gate:'BLOCKED',blocker:'Continuation-value validation pending',reason:'Hold is compared with exit, not triggered by an arbitrary profit percentage.',evidence:'ILLUSTRATIVE',at:'2026-09-17T22:29:32Z'},
      {id:'D-DEMO-827',title:'Atlanta · New York',outcome:'Over 7.5 runs',sport:'MLB',family:'Total',venue:'Kalshi',action:'PAIR REVIEW',state:'SHADOW',price:.54,netEv:.0061,p10:-.0018,p90:.0162,pPositive:.74,fillProbability:null,quantity:2000,gate:'BLOCKED',blocker:'Venue-specific complement mechanics',reason:'Separate legs and incremental economics retained. No assumed cross-venue equivalence.',evidence:'ILLUSTRATIVE',at:'2026-09-17T22:29:25Z'},
      {id:'D-DEMO-826',title:'Dortmund · Munich',outcome:'Dortmund to win',sport:'Soccer',family:'Moneyline',venue:'Polymarket US',action:'NO TRADE',state:'REJECTED',price:.29,netEv:null,p10:null,p90:null,pPositive:null,fillProbability:null,quantity:1000,gate:'BLOCKED',blocker:'Required economic input unidentified',reason:'An unidentified economic term is not replaced with zero.',evidence:'ILLUSTRATIVE',at:'2026-09-17T22:29:12Z'}
    ];
    const value=1248560, cash=978510, reserved=31090;
    const history=Array.from({length:65},(_,i)=>{const f=i/64; return {at:new Date(Date.UTC(2026,8,1)+f*(Date.parse(asOf)-Date.UTC(2026,8,1))).toISOString(),value:round(1200000+48560*f+7000*Math.sin(f*8)*Math.sin(f*Math.PI)+3800*Math.sin(f*41)*Math.sin(f*Math.PI)),netFlows:0};});
    history[64].value=value;
    return {schema:SCHEMA,snapshotId:'DEMO-20260917-001',sequence:1,source:'ILLUSTRATIVE',mode:'DEMO',audience:'INTERNAL',asOf,
      provenance:{sourceId:'DETERMINISTIC-DESIGN-FIXTURE-V1',accountingBasis:'Synthetic USD ledger; bid-side marks; no external cash flows. Not company NAV.',reconciliation:'DEMO_ONLY',window:'2026-09-01 / 2026-09-17',disclaimer:'ILLUSTRATIVE DATA ONLY. No displayed result represents BettorToken performance, executed trades, or proven edge.'},
      account:{value,cash,reserved,available:cash-reserved,positionValue:sum(positions,p=>p.value),realizedMtd:40860,unrealized:sum(positions,p=>p.unrealized),netMtd:48560,grossVolumeMtd:6482100,feesMtd:2440,rebatesMtd:810},
      counts:{positions:positions.length,orders:5,events:8},positions,orders,decisions,history,
      pipeline:{observed:148320,rejected:147486,reviewed:834,shadow:96,submitted:0},
      engine:{name:'BETTOR EV Core',mode:'SHADOW',decisionGrade:'BLOCKED',modelVersion:'ev-core · design preview',autoPromotion:false,independentFillEvidence:null},
      gates:[{name:'Point-in-time data',status:'PASS',detail:'Example timestamp and identity checks.'},{name:'Economic term completeness',status:'PENDING',detail:'Unknown terms remain unknown.'},{name:'Fill quantity calibration',status:'BLOCKED',detail:'BETTOR-native order observations required.'},{name:'Joint execution dependence',status:'BLOCKED',detail:'Independent marginals are not decision-grade.'},{name:'Production authorization',status:'BLOCKED',detail:'No order authority exists in this interface.'}],
      services:[{name:'Portfolio ledger',status:'DEMO',latency:null,detail:'Synthetic account snapshot'},{name:'Order lifecycle',status:'DEMO',latency:null,detail:'Synthetic resting and partial orders'},{name:'EV telemetry',status:'DEMO',latency:null,detail:'Illustrative probability distributions'},{name:'External consensus',status:'NOT CONNECTED',latency:null,detail:'Timestamped adapter required'}],
      models:[{name:'Raw market baseline',kind:'Settlement',status:'BASELINE',detail:'Market-derived, not independent alpha.'},{name:'Order-book microprice',kind:'Short horizon',status:'DECLARED',detail:'Shadow evaluation; no production admission.'},{name:'Order-flow imbalance',kind:'Microstructure',status:'DECLARED',detail:'Share-count units separated from ratios.'},{name:'Fill-conditioned value',kind:'Execution',status:'NOT IDENTIFIED',detail:'Requires BETTOR-native fill evidence.'},{name:'Queue / fill quantity',kind:'Execution',status:'NOT IDENTIFIED',detail:'Distributional; not an exact L2 queue position.'},{name:'Capital allocation',kind:'Portfolio',status:'INTERFACE',detail:'Total-dollar EV over capital-hours.'}],
      activity:[{id:'a1',at:'2026-09-17T22:29:57Z',kind:'decision',title:'Opportunity moved to shadow',detail:'New York · Boston · POST BID · no order sent'},{id:'a2',at:'2026-09-17T22:29:46Z',kind:'rejected',title:'Candidate rejected',detail:'London · Manchester · exit cost exceeds edge'},{id:'a3',at:'2026-09-17T22:29:32Z',kind:'risk',title:'Execution gate remains blocked',detail:'Fill-quantity calibration unavailable'},{id:'a4',at:'2026-09-17T22:29:18Z',kind:'order',title:'Partial-fill lifecycle example',detail:'O-DM-1048 · 4,500 / 15,000 shares · simulated'},{id:'a5',at:'2026-09-17T22:28:50Z',kind:'report',title:'Snapshot ready for reporting',detail:'Immutable source snapshot attached to each export'}]
    };
  }
  function endpoint(path) {
    if(typeof path!=='string'||!/^\/api\/command(?:\/|$)/.test(path)||path.includes('..')||path.includes('\\')||/[?#]/.test(path)) throw new Error('Only configured same-origin /api/command/ read endpoints are allowed.');
    return path;
  }
  class Feed {
    constructor(config,onState){this.config=config||{};this.onState=onState;this.generation=0;this.current=null;this.stream=null;this.timer=null;this.controller=null;this.closed=true;}
    stop(){this.generation++;this.closed=true;clearTimeout(this.timer);this.stream?.close();this.stream=null;this.controller?.abort();this.controller=null;this.current=null;}
    async start(audience='INTERNAL') {
      this.stop();this.closed=false;const gen=this.generation;
      const path=endpoint(audience==='INVESTOR'?(this.config.investorSnapshot||'/api/command/investor/snapshot'):(this.config.snapshot||'/api/command/snapshot'));
      const consume = payload=>{
        const v=validate(payload,{remote:true,audience});if(!v.ok)throw new Error(v.errors.join(' '));
        if(this.current&&payload.sequence<this.current.sequence)throw new Error('Out-of-order snapshot.');
        if(this.current&&payload.sequence===this.current.sequence&&payload.snapshotId!==this.current.snapshotId)throw new Error('Sequence identity conflict.');
        if(this.current&&payload.sequence===this.current.sequence&&stable(payload)!==stable(this.current))throw new Error('An existing snapshot identity changed content.');
        if(this.current&&payload.sequence===this.current.sequence){this.onState({status:'connected',snapshot:this.current});return;}
        this.current=clone(payload);this.onState({status:'connected',snapshot:this.current});
      };
      this.onState({status:'connecting',snapshot:null});
      const poll=async()=>{
        if(this.closed||gen!==this.generation)return;
        const controller=new AbortController();this.controller=controller;const timeout=setTimeout(()=>controller.abort(),10000);
        try {const response=await fetch(path,{method:'GET',credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'},signal:this.controller.signal});
          if(response.status===401||response.status===403){this.current=null;throw new Error('Authentication required. Sign in through the secured host.');}
          // A 503 FROM THIS API CARRIES A REASON, AND IT USED TO BE
          // THROWN AWAY. The server names the specific failure --
          // which retrieval was incomplete, or which builder
          // produced a row the contract refuses -- and rendering
          // only the status code turned a precise diagnosis into
          // "Snapshot endpoint returned 503" on management's screen.
          // The body is read defensively: a proxy error page is not
          // JSON, and a failure to parse the reason must not replace
          // the failure being reported.
          if(!response.ok){let why='';
            try{const b=await response.json();
              why=typeof b?.detail==='string'?b.detail
                 :typeof b?.detail?.reason==='string'
                   ?[b.detail.reason,b.detail.detail].filter(Boolean).join(': ')
                 :typeof b?.reason==='string'?b.reason:'';}catch(_){}
            throw new Error(why?`Snapshot endpoint returned ${response.status}. ${why}`
                               :`Snapshot endpoint returned ${response.status}.`);}
          const payload=await response.json();if(this.closed||gen!==this.generation)return;consume(payload);
        } catch(e){if(this.closed||gen!==this.generation)return;this.onState({status:'disconnected',snapshot:this.current,error:e.message||'Feed unavailable.'});}
        finally{clearTimeout(timeout);if(!this.closed&&gen===this.generation)this.timer=setTimeout(poll,Math.max(5000,this.config.pollMs||15000));}
      };
      await poll();
      const streamPath=audience==='INVESTOR'?this.config.investorStream:this.config.stream;
      if(streamPath&&!this.closed&&gen===this.generation){
        this.stream=new EventSource(endpoint(streamPath),{withCredentials:true});
        this.stream.addEventListener('snapshot',event=>{if(this.closed||gen!==this.generation)return;try{consume(JSON.parse(event.data));}catch(e){this.onState({status:'disconnected',snapshot:this.current,error:e.message});}});
        this.stream.onerror=()=>{if(!this.closed&&gen===this.generation)this.onState({status:'disconnected',snapshot:this.current,error:'Stream interrupted; snapshot polling remains active.'});};
      }
    }
  }
  const api={VERSION,SCHEMA,finite,num,round,sum,clone,esc,usd,signed,count,price,pct,clock,date,duration,toCsv,stable,digest,validate,freshness,marketExposure,projectInvestor,demoSnapshot,endpoint,Feed};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.BTCore=api;
})(typeof window==='undefined'?globalThis:window);
