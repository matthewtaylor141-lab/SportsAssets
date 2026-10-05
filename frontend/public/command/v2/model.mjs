/**
 * BETTOR Command V2 — explicit read models, no financial write authority.
 * Contract baseline: SportsAssets 63665964bf2a56a6a78d7cfbcab41e83d1725d82.
 * Source field paths are documented in docs/DATA_CONTRACTS.md.
 * Unknown never becomes zero, a forecast never becomes a realized result,
 * and absence of an agent never becomes activity.
 */
export const VERSION = 'BETTOR_COMMAND_V2_1.0.0';
export const BASE_SHA = '63665964bf2a56a6a78d7cfbcab41e83d1725d82';
export const ROSTER = Object.freeze([
  {id:'DEREK',slug:'derek',name:'Derek',role:'Discovery & entry',desk:'Opportunity desk',color:'#2274e8'},
  {id:'KAREN',slug:'karen',name:'Karen',role:'Evidence challenge',desk:'Review studio',color:'#b45278'},
  {id:'SCOUT',slug:'scout',name:'Scout',role:'Market intelligence',desk:'Discovery desk',color:'#258b84'},
  {id:'EDDIE',slug:'eddie',name:'Eddie',role:'Execution',desk:'Execution desk',color:'#bd7a24'},
  {id:'ALLOCATOR',slug:'allocator',name:'Allie',role:'Capital allocation',desk:'Capital desk',color:'#89734c'},
  {id:'AUDREY',slug:'audrey',name:'Audrey',role:'Risk & audit',desk:'Audit station',color:'#646dc9'},
  {id:'XAVIER',slug:'xavier',name:'Xavier',role:'Position management',desk:'Portfolio desk',color:'#477f94'},
  {id:'ARIANA',slug:'ariana',name:'Ariana',role:'Cross-venue arbitrage',desk:'Arbitrage desk',color:'#8654c4'}
]);
export const FEEDS = Object.freeze({
  equity:{url:'/api/command/equity/live',every:15000,ttl:45000},
  floor:{url:'/api/command/floor',every:30000,ttl:90000},
  derek:{url:'/api/command/paper/derek?limit=100',every:30000,ttl:90000},
  xavier:{url:'/api/command/paper/xavier',every:30000,ttl:90000},
  coverage:{url:'/api/command/coverage?days=2',every:120000,ttl:300000},
  release:{url:'/api/command/release',every:60000,ttl:180000},
  build:{url:'/build.json',every:300000,ttl:600000},
  curve:{url:'/api/command/equity/curve?book=paper&window=1d',every:60000,ttl:180000}
});
export function number(x){
  if(typeof x==='number') return Number.isFinite(x)?x:null;
  if(typeof x==='string' && x.trim()!=='' && /^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$/.test(x.trim())) return Number.isFinite(Number(x))?Number(x):null;
  return null;
}
export function epoch(x){
  if(x===null || x===undefined || x==='') return null;
  const n=number(x); if(n!==null) return n>1e12?n:n*1000;
  if(typeof x!=='string') return null;
  const v=Date.parse(x); return Number.isFinite(v)?v:null;
}
export const text=x=>typeof x==='string'?x:null;
export const words=x=>typeof x==='string'?x.replaceAll('_',' ').toLowerCase():'Unknown';
export const money=(x,d=2)=>number(x)===null?'—':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:d,maximumFractionDigits:d}).format(number(x));
export const integer=x=>number(x)===null?'—':new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(number(x));
export const pp=x=>number(x)===null?'—':`${number(x)>0?'+':''}${number(x).toFixed(2)} pp`;
export const percent=x=>number(x)===null?'—':`${number(x).toFixed(1)}%`;
export function stamp(x){const t=epoch(x);return t===null?'Time unavailable':new Date(t).toLocaleString('en-US',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false,timeZone:'America/New_York'})+' ET';}
export function age(x,now=Date.now()){
  const t=epoch(x);if(t===null)return 'Age unavailable';const n=(now-t)/1000;
  if(n < -5)return 'Clock ahead';if(n<60)return `${Math.max(0,Math.floor(n))}s ago`;
  if(n<3600)return `${Math.floor(n/60)}m ago`;if(n<86400)return `${Math.floor(n/3600)}h ago`;return `${Math.floor(n/86400)}d ago`;
}
export function section(s){
  if(!s || typeof s!=='object')return {status:'UNAVAILABLE',rows:[],why:'Section not served'};
  if(!['OK','EMPTY'].includes(s.status))return {status:s.status||'UNAVAILABLE',rows:[],why:s.why||'Section not available'};
  if(!Array.isArray(s.data))return {status:'INVALID',rows:[],why:'Expected section.data array'};
  return {status:s.status,rows:s.data,why:s.why||null};
}
export function paper(E){
  const p=E?.paper;
  if(!p || !['OK','STALE'].includes(p.status))return {status:p?.status||'UNAVAILABLE',why:p?.why||'equity/live.paper not available',equity:null,day:null,realized:null,unrealized:null,available:null,deployed:null,cash:null,open:null};
  const o=p.open_positions||{},m=p.marks_as_of||{},markAge=number(m.newest_age_s),threshold=number(m.stale_mark_after_s);
  const staleMarks=number(o.stale_marks),unmarked=number(o.unmarked),count=number(o.count),marked=number(o.marked);
  // Keep partial marked P&L separate, but do not present it as a full current mark.
  const allMarked=p.status==='OK' && (count===0 || (count!==null && marked===count && unmarked===0 && staleMarks===0 && markAge!==null && threshold!==null && markAge>=0 && threshold>0 && markAge<=threshold));
  return {status:p.status,why:p.why||null,asOf:E.computed_at||null,
    equity:number(p.equity_usd),day:number(p.day_change?.usd),dayPct:number(p.day_change?.pct),
    realized:number(p.realized_pnl_usd),unrealized:allMarked?number(p.unrealized_pnl_usd):null,
    markedUnrealized:number(p.unrealized_pnl_usd),allMarked,
    available:number(p.available_usd),cash:number(p.cash_usd),reserved:number(p.reserved_usd),
    deployed:number(p.exposure?.cost_basis_usd),open:count,marked,unmarked,staleMarks,
    treatment:text(p.equity_treatment?.text),sleeves:p.sleeves||null};
}
export function modes(E){
  const v=E?.actual?.venues||{};
  return {smallLive:{status:text(E?.small_live_bettor?.status)||'UNAVAILABLE',why:text(E?.small_live_bettor?.why)},
    legacy:['polymarket_us','kalshi'].map(id=>({id,name:id==='kalshi'?'Kalshi':'Polymarket US',status:text(v[id]?.status)||'UNAVAILABLE',lane:text(v[id]?.lane?.state),why:text(v[id]?.why)}))};
}
export function agents(F,staleRead=false){
  const rows=Array.isArray(F?.agents)?F.agents:[];
  return ROSTER.map(r=>{
    const matches=rows.filter(a=>a.slug===r.slug || String(a.agent||'').toUpperCase()===r.id);
    if(matches.length!==1)return {...r,registered:false,state:matches.length>1?'AMBIGUOUS':'NOT_REPORTED',detail:matches.length>1?'More than one matching agent record.':'No production floor record. Deployment is not verified.',at:null,monitor:[],authority:null,portrait:null};
    const a=matches[0];const state=staleRead?'STALE':(text(a.work_state)||text(a.state)||'UNKNOWN');
    return {...r,registered:true,state,detail:text(a.work_detail)||text(a.state_detail)||'No task detail recorded.',
      at:a.state_since||a.heartbeat?.at||null,heartbeatAge:number(a.heartbeat?.age_s),
      monitor:Array.isArray(a.monitor)?a.monitor.filter(m=>typeof m.label==='string').slice(0,4):[],
      authority:text(a.authority?.level),activityBasis:text(a.activity_basis),
      portrait:`/team-demo/assets/models/portraits/${r.slug}.jpg`,raw:a};
  });
}
export function decisions(D){
  const s=section(D?.opportunities);
  return {...s,rows:s.rows.map(d=>{
    const l=d.label||{},e=d.economics||{},p=d.policy_decision||{};
    return {id:text(d.decision_id),at:d.decided_at||null,event:text(l.event_title)||text(d.us_market_slug)||'Event not labeled',
      market:text(d.us_market_slug),competition:text(l.competition),family:text(l.market_type),line:number(l.line),period:text(l.period),
      side:text(l.participant)||text(d.holding_side)||text(d.intent),verdict:text(d.verdict)||'UNKNOWN',
      refusal:text(d.refusal),strategy:text(d.strategy),
      gross:number(e.best_level_edge_pp)??number(p.gross_edge_pp),
      expectedNet:number(e.acquisition?.expected_net_profit_usd)??number(p.net_expected_profit_usd),
      qty:number(d.proposed_qty),price:number(d.limit_price),pinnacle:number(d.p_pinnacle),
      raw:d};
  })};
}
export function orders(D,X){
  const a=section(D?.orders),b=section(X?.standing_orders);
  const rows=[...a.rows.map(x=>({...x,origin:'Entry'})),...b.rows.map(x=>({...x,origin:'Management'}))];
  const seen=new Map();
  for(const o of rows){if(typeof o.order_id!=='string')continue;if(!seen.has(o.order_id))seen.set(o.order_id,o);}
  return {status:([a,b].every(x=>['OK','EMPTY'].includes(x.status))?'OK':[a,b].some(x=>['OK','EMPTY'].includes(x.status))?'PARTIAL':'UNAVAILABLE'),why:'Latest bounded entry and management reads; not the full order ledger.',rows:[...seen.values()].map(o=>({
    id:o.order_id,decisionId:text(o.decision_id),event:text(o.label?.event_title)||text(o.us_market_slug)||'Event not labeled',at:o.created_at||null,
    state:text(o.state)||'UNKNOWN',qty:number(o.qty),filled:number(o.filled_qty),price:number(o.limit_price),side:text(o.label?.participant)||text(o.holding_side),
    tif:text(o.time_in_force),strategy:text(o.strategy),origin:o.origin,raw:o
  }))};
}
export function positions(X){const s=section(X?.positions);return {...s,rows:s.rows.map(p=>({id:text(p.position_key),event:text(p.label?.event_title)||text(p.us_market_slug)||'Event not labeled',market:text(p.us_market_slug),competition:text(p.label?.competition),family:text(p.label?.market_type),line:number(p.label?.line),side:text(p.label?.participant)||text(p.holding_side),qty:number(p.open_qty),cost:number(p.cost_basis_usd)??number(p.acquisition_cost_usd),unrealized:number(p.unrealized_pnl_usd),strategy:text(p.strategy),raw:p}))};}
export const STAGES=Object.freeze([
 ['provider_events','Discovered'],['mapped_events','Mapped'],['evaluated_events','Evaluated'],['entered_events','Entered'],['ordered_events','Ordered'],['filled_events','Filled']
]);
export function coverage(C){
  const day=C?.days?.[0],ls=Array.isArray(day?.leagues)?day.leagues:[];
  const statuses=Array.isArray(C?.league_status?.statuses)?C.league_status.statuses:[];
  const stages=STAGES.map(([key,label])=>{
    const measured=ls.filter(x=>number(x[key])!==null),sum=measured.reduce((s,x)=>s+number(x[key]),0);
    return {key,label,value:measured.length?sum:null,measured:measured.length,total:ls.length,partial:measured.length!==ls.length,why:!ls.length?'No league rows':`${ls.length-measured.length} leagues unmeasured`};
  });
  return {day:day?.day||null,tz:C?.tz||'America/New_York',stages,rows:ls.map(l=>({id:l.league,name:l.league_name||l.league,status:statuses.find(s=>s.league===l.league)?.status||'UNVERIFIED',reason:statuses.find(s=>s.league===l.league)?.reason||null,provider:number(l.provider_events),mapped:number(l.mapped_events),evaluated:number(l.evaluated_events),entered:number(l.entered_events),orders:number(l.ordered_events),fills:number(l.filled_events),raw:l}))};
}
export function release(R,B){return {api:text(R?.api?.sha),worker:text(R?.workers?.sha),alignment:text(R?.alignment?.verdict)||'UNKNOWN',schema:number(R?.schema?.max_version),at:R?.generated_at||null,frontend:text(B?.sha),deployId:text(B?.deploy_id),buildSource:text(B?.source)};}
export function tape(F,D){
  const out=[],seen=new Set();
  for(const e of Array.isArray(F?.edges)?F.edges:[]){
    if(epoch(e.at)===null)continue;
    out.push({id:`edge:${e.from}:${e.to}:${e.at}:${e.kind}`,at:e.at,kind:'Collaboration',title:`${e.from||'Unknown'} → ${e.to||'Unknown'}`,detail:text(e.summary)||words(e.kind),source:'floor.edges',raw:e});
  }
  for(const e of Array.isArray(F?.feed)?F.feed:[]){
    if(epoch(e.at)===null)continue;
    out.push({id:`decision:${e.market}:${e.at}:${e.verdict}`,at:e.at,kind:'Decision',title:text(e.fixture)||text(e.market)||'Recorded event',detail:`${e.verdict||'Unknown'}${e.refusal?' · '+e.refusal:''}`,source:'floor.feed · PAPER',raw:e});
  }
  for(const f of section(D?.fills).rows){
    if(epoch(f.filled_at)===null)continue;
    out.push({id:`fill:${f.fill_id||f.order_id}:${f.filled_at}:${f.qty}`,at:f.filled_at,kind:'Paper fill',title:text(f.label?.event_title)||text(f.us_market_slug)||'Recorded fill',detail:`${integer(f.qty)} contracts at ${money(f.price)} · simulated`,source:'paper/derek.fills',raw:f});
  }
  return out.sort((a,b)=>epoch(b.at)-epoch(a.at)).filter(x=>{if(seen.has(x.id))return false;seen.add(x.id);return true;}).slice(0,150);
}
export function events(F,D){
  const map=new Map();
  for(const d of decisions(D).rows){
    if(!d.market)continue;
    let e=map.get(d.market);
    if(!e){e={id:d.market,event:d.event,competition:d.competition,family:d.family,line:d.line,at:d.at,rows:[],status:'RECORDED',watchers:[]};map.set(d.market,e);}
    e.rows.push(d);if(epoch(d.at)>epoch(e.at))e.at=d.at;
  }
  for(const f of Array.isArray(F?.feed)?F.feed:[]){
    if(typeof f.market!=='string')continue;
    if(!map.has(f.market))map.set(f.market,{id:f.market,event:text(f.fixture)||f.market,competition:null,family:null,line:null,at:f.at,rows:[],status:'RECORDED',watchers:[]});
  }
  // A past decision is not evidence of an actively watched or in-play game.
  return [...map.values()].map(e=>({...e,rows:e.rows.slice().sort((a,b)=>(epoch(b.at)||0)-(epoch(a.at)||0))})).sort((a,b)=>(epoch(b.at)||0)-(epoch(a.at)||0));
}
export function incidents(store,now=Date.now()){
  const out=[];
  for(const [key,cfg] of Object.entries(FEEDS)){
    const r=store[key];if(!r){out.push({severity:'INFO',title:`${key} not read`,detail:'Waiting for the first response.',source:key});continue;}
    if(r.state!=='OK')out.push({severity:r.state==='AUTH'?'CRITICAL':'WARNING',title:`${key}: ${words(r.state)}`,detail:r.error||'No current response.',source:key});
    else if(!r.received || now-r.received>cfg.ttl)out.push({severity:'WARNING',title:`${key} read is stale`,detail:'The last successful fetch is outside the display freshness window.',source:key});
  }
  for(const [k,valid] of [['equity',!!store.equity?.data?.paper],['floor',Array.isArray(store.floor?.data?.agents)],['coverage',Array.isArray(store.coverage?.data?.days)],['derek',!!store.derek?.data?.opportunities],['release',!!store.release?.data?.api&&!!store.release?.data?.workers]]){if(store[k]?.state==='OK'&&!valid)out.push({severity:'WARNING',title:`${k} schema is not supported`,detail:'The read succeeded but expected source fields were not supplied. Values remain unavailable.',source:k});}
  const R=store.release?.data;if(R?.alignment?.verdict==='MISALIGNED')out.push({severity:'CRITICAL',title:'API and workers disagree',detail:`${R.api?.sha||'?'} / ${R.workers?.sha||'?'}`,source:'release'});
  const E=store.equity?.data;
  if(['STALE','UNAVAILABLE'].includes(E?.paper?.status))out.push({severity:'WARNING',title:`Paper book ${words(E.paper.status)}`,detail:E.paper.why||'No current book.',source:'equity'});
  const C=store.coverage?.data;
  if(C?.provider_supplement?.status && C.provider_supplement.status!=='OK')out.push({severity:'WARNING',title:'Provider data needs attention',detail:C.provider_supplement.why||C.provider_supplement.status,source:'coverage'});
  for(const a of Array.isArray(C?.alerts)?C.alerts:[])out.push({severity:a.severity||'WARNING',title:`${a.league_name||a.league||'Coverage'} · ${words(a.kind)}`,detail:text(a.detail?.statement)||`${a.stage_from||'?'} → ${a.stage_to||'?'}`,source:'coverage'});
  for(const a of agents(store.floor?.data))if(a.registered && ['STALE','NOT_DEPLOYED','BLOCKED'].includes(a.state))out.push({severity:'WARNING',title:`${a.name} · ${words(a.state)}`,detail:a.detail,source:'floor'});
  const weight={CRITICAL:0,WARNING:1,INFO:2};return out.sort((a,b)=>(weight[a.severity]??3)-(weight[b.severity]??3));
}
export function viewModel(store,now=Date.now()){
 const raw=k=>store[k]?.data||null;const stale=k=>store[k]?.state!=='OK'||now-(store[k]?.received||0)>FEEDS[k].ttl;
 return {paper:paper(raw('equity')),modes:modes(raw('equity')),agents:agents(raw('floor'),stale('floor')),decisions:decisions(raw('derek')),orders:orders(raw('derek'),raw('xavier')),positions:positions(raw('xavier')),coverage:coverage(raw('coverage')),release:release(raw('release'),raw('build')),tape:tape(raw('floor'),raw('derek')),events:events(raw('floor'),raw('derek')),incidents:incidents(store,now),curve:curve(raw('curve')),stale,store};
}

/** Recorded PAPER step series. Preserve null gaps; never extend a curve to now. */
export function curve(C){
 if(!C||!Array.isArray(C.points))return {points:[],why:'Recorded PAPER curve unavailable'};
 return {points:C.points.filter(p=>number(p.t)!==null).map(p=>({t:number(p.t)*1000,v:number(p.v),cause:text(p.cause)})).sort((a,b)=>a.t-b.t),why:text(C.why)};
}
