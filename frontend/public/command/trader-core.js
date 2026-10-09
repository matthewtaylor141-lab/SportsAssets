/* BETTOR Trader Mode · pure display mechanics. No order or credential access. */
(function(root){'use strict';
const finite=v=>typeof v==='number'&&Number.isFinite(v);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(n,d=0)=>finite(n)?new Intl.NumberFormat('en-US',{maximumFractionDigits:d,minimumFractionDigits:d}).format(n):'—';
const cents=p=>finite(p)?`${fmt(p*100,1)}¢`:'—';
const money=(v,signed=false)=>finite(v)?`${signed?(v<0?'−':v>0?'+':''):v<0?'−':''}$${fmt(Math.abs(v),2)}`:'—';
const age=(at,now=Date.now()/1000)=>finite(at)&&finite(now)?now-at:null;
const fresh=(at,now,limit)=>finite(at)&&finite(now)&&0<=now-at&&now-at<=limit;
const duration=s=>!finite(s)?'unavailable':s<0?'clock invalid':s<60?`${Math.floor(s)}s`:s<3600?`${Math.floor(s/60)}m`:`${Math.floor(s/3600)}h`;
function validate(s,{remote=true,now=Date.now()/1000}={}){
 const errors=[];
 if(!s||s.schema!=='bettor.trader.v1')return{ok:false,errors:['Unsupported trader snapshot schema.']};
 if(remote&&(s.source!=='NATIVE_LEDGER'||s.mode!=='PAPER'||s.execution_authority!==false))errors.push('Remote feed must be native-ledger PAPER, with no execution authority.');
 if(!finite(s.snapshot_at)||s.snapshot_at>now+5)errors.push('Snapshot timestamp is absent or in the future.');
 if(!Array.isArray(s.positions))errors.push('Position population is absent.');
 const ids=new Set();
 for(const p of s.positions||[]){
  if(!p.position_id||ids.has(p.position_id))errors.push('Duplicate or absent canonical position key.');
  ids.add(p.position_id);
  if(!finite(p.qty)||p.qty<=0)errors.push('Invalid held quantity.');
  if(!['LONG','SHORT'].includes(p.holding_side))errors.push('Unknown holding side.');
  for(const o of p.orders||[])if(o.position_id!==p.position_id)errors.push('Order belongs to another position.');
 }
 if(!Number.isSafeInteger(s.total_position_count)||s.total_position_count<(s.positions||[]).length)errors.push('Position count does not reconcile.');
 return{ok:!errors.length,errors};
}
function gap(o,q,now=Date.now()/1000){
 if(!o||!q)return{distance:null,met:null,reason:'No recorded target'};
 const dir=o.direction,lim=o.limit_price,ref=dir==='SELL'?q.bid:q.ask;
 if(!['BUY','SELL'].includes(dir)||!finite(lim)||!finite(ref)||lim<0||lim>1||ref<0||ref>1)return{distance:null,met:null,reason:'Price unavailable'};
 const delta=dir==='SELL'?lim-ref:ref-lim;
 const current=q.current===true&&fresh(q.at,now,300);
 return{distance:Math.max(0,delta*100),signed:delta*100,met:current?delta<=1e-12:null,
  reason:current?null:'Book not current',reference:ref,limit:lim,side:dir==='SELL'?'bid':'ask',isFill:false};
}
function standing(p){return(p.orders||[]).filter(o=>['RESTING','PARTIALLY_FILLED','CANCEL_PENDING','PENDING_SIMULATION'].includes(o.state));}
// A standing order whose recorded expiry has passed is not protection, even
// while the ledger still reads RESTING (the expiry sweep lags): the server's
// packet already refuses it (NO_VALID_ACTIVE_PROTECTION) and the page must not
// show it as the standing limit. It stays listed, labelled, in the orders view.
function pastExpiry(o,now=Date.now()/1000){return!!o&&finite(o.expires_at)&&finite(now)&&o.expires_at<=now;}
function target(p,now=Date.now()/1000){const os=standing(p).filter(o=>!pastExpiry(o,now));return os.find(o=>o.direction==='SELL'&&['RESTING','PARTIALLY_FILLED'].includes(o.state))||os.find(o=>o.direction==='SELL')||os[0]||p.proposal||null;}
// a recorded instant as UTC wall time (never the viewer's zone, never invented)
const utc=at=>finite(at)?new Date(at*1000).toISOString().slice(11,19)+'Z':'—';
function packet(p,now=Date.now()/1000){const k=p.packet||{};const q=p.quote||{};const missing=[...(k.missing||[])];
 if(!fresh(k.probability_at,now,30)||k.probability_current!==true)missing.push('NO_FRESH_PROBABILITY');
 if(!fresh(q.at,now,300)||q.current!==true)missing.push('NO_CURRENT_EXECUTABLE_BOOK');
 return{complete:k.complete===true&&missing.length===0,missing:[...new Set(missing)]};}
function clock(g,now=Date.now()/1000){
 if(!g||!finite(g.clock_seconds))return{value:null,text:'Clock unavailable',estimated:false,frozen:true};
 const delta=age(g.source_at,now),valid=finite(delta)&&delta>=0&&delta<=15&&g.status==='CURRENT';
 const interpolate=valid&&g.clock_running===true&&['UP','DOWN'].includes(g.clock_direction);
 const elapsed=interpolate?delta:0;
 const value=Math.max(0,g.clock_seconds+(g.clock_direction==='UP'?elapsed:-elapsed));
 const min=Math.floor(value/60),sec=Math.floor(value%60);
 return{value,text:`${interpolate?'~':''}${min}:${String(sec).padStart(2,'0')}`,estimated:interpolate,frozen:!interpolate};
}
function series(p){const seen=new Set();return(p.quote_history||[]).filter(h=>{
 if(!finite(h.price)||h.price<0||h.price>1||!finite(h.at))return false;
 const id=h.observation_id??`${h.at}:${h.price}`;if(seen.has(id))return false;seen.add(id);return true;
 }).sort((a,b)=>a.at-b.at).slice(-64);}
function chart(p,limit){
 const xs=series(p),W=420,H=88,pad=9;
 if(!xs.length)return'<text x="12" y="40">No observed price history</text>';
 const values=xs.map(x=>x.price);if(finite(limit))values.push(limit);
 const lo=Math.max(0,Math.min(...values)-.012),hi=Math.min(1,Math.max(...values)+.012),span=Math.max(.01,hi-lo);
 const y=v=>H-pad-(v-lo)/span*(H-pad*2);
 const pts=xs.map((h,i)=>[pad+i*(W-pad*2)/Math.max(1,xs.length-1),y(h.price)]);
 const path=pts.map((v,i)=>`${i?'L':'M'}${v[0].toFixed(2)},${v[1].toFixed(2)}`).join(' ');
 const last=pts[pts.length-1];
 let svg='<defs><linearGradient id="chart-shade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#7fe2ac" stop-opacity=".17"/><stop offset="1" stop-color="#7fe2ac" stop-opacity="0"/></linearGradient></defs>';
 for(let i=1;i<4;i++)svg+=`<path class="chart-grid" d="M0 ${i*22}H420"/>`;
 if(finite(limit)){const ty=y(limit);svg+=`<path class="chart-target" d="M0 ${ty}H420"/><text class="target-label" x="418" y="${Math.max(9,ty-5)}" text-anchor="end">TARGET ${cents(limit)}</text>`;}
 if(pts.length>1)svg+=`<path class="chart-fill" d="${path} L${last[0]},88 L${pts[0][0]},88 Z"/><path class="chart-line" d="${path}"/>`;
 svg+=`<circle cx="${last[0]}" cy="${last[1]}" r="3.2" fill="#90e4b1"/><circle cx="${last[0]}" cy="${last[1]}" r="7" fill="#90e4b122"/>`;
 if(pts.length===1)svg+='<text x="20" y="80">One observation; awaiting history</text>';
 return svg;
}
function matches(p,filter,query,now){
 const t=target(p,now),g=gap(t,p.quote,now),pk=packet(p,now);
 if(query&&!`${p.title} ${p.market_title} ${p.market_id} ${p.sport} ${p.position_id}`.toLowerCase().includes(query.toLowerCase()))return false;
 if(filter==='near')return p.state==='ACTIVE'&&g.met!==null&&g.distance<=2;
 if(filter==='blocked')return p.state==='ACTIVE'&&!pk.complete;
 if(filter==='settlement')return p.state==='SETTLEMENT_PENDING';
 return true;
}
const api={finite,esc,fmt,cents,money,age,fresh,duration,validate,gap,standing,pastExpiry,target,utc,packet,clock,series,chart,matches};
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.TraderCore=api;
})(typeof window==='undefined'?globalThis:window);
