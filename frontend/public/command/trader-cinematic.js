/*
 * BETTOR Trader Mode · Cinematic V2
 * Read-only visual observer. It never requests market data, never modifies the
 * native snapshot, and never creates synthetic prices. Quote motion is triggered
 * only when a real position quote observation_id / bid changes in TraderMode.
 */
(function(){
'use strict';

const C=window.TraderCore;
if(!C)return;

const $=id=>document.getElementById(id);
const quoteMemory=new Map();
let lastSnapshot=null;
let lastFocused=null;
let pulseTimer=null;

function focusedPosition(snapshot){
  if(!snapshot||!Array.isArray(snapshot.positions))return null;
  const el=document.querySelector('.position-card.focused');
  const id=el&&el.dataset.id;
  return snapshot.positions.find(p=>p.position_id===id)||snapshot.positions[0]||null;
}

function node(label,value,state,cls=''){
  return `<div class="brain-node ${cls}" data-state="${C.esc(state||'unknown')}"><small>${C.esc(label)}</small><b>${C.esc(value||'—')}</b><i></i></div>`;
}

function humanMissing(code){
  return String(code||'').replace(/^NO_/,'').replaceAll('_',' ').toLowerCase();
}

function renderBrain(snapshot,p){
  const rail=$('brain-rail');
  if(!rail)return;
  if(!snapshot||!p){
    rail.innerHTML=node('SYSTEM','Waiting for native ledger','stale','brain-pulse')+
      node('VENUE BOOK','—','stale')+node('XAVIER','—','stale')+
      node('PROTECTION','—','stale')+node('TARGET','—','stale');
    return;
  }
  const now=Date.now()/1000;
  const q=p.quote||{},pk=C.packet(p,now),standing=C.standing(p),target=C.target(p);
  const bookCurrent=q.current===true&&C.fresh(q.at,now,300);
  const protectionMissing=pk.missing.some(x=>String(x).includes('PROTECTION'));
  const entry=C.finite(p.entry_price)?`ENTRY ${C.cents(p.entry_price)}`:'ENTRY UNAVAILABLE';
  const xavier=pk.complete
    ? String((p.packet&&p.packet.current_recommendation)||(p.review&&p.review.recommendation)||'PACKET CURRENT')
    : `WAITING · ${humanMissing(pk.missing[0]||'evidence')}`;
  const book=bookCurrent?`${C.cents(q.bid)} · ${C.duration(C.age(q.at,now))}`:`STALE · ${C.duration(C.age(q.at,now))}`;
  const protect=protectionMissing?'EVIDENCE MISSING':standing.length?`${standing.length} STANDING`:'NO STANDING ORDER';
  const tgt=target&&C.finite(target.limit_price)?`${C.cents(target.limit_price)} ${target.direction||''}`:'NO RECORDED TARGET';

  rail.innerHTML=
    node('DEREK / ENTRY',entry,C.finite(p.entry_price)?'recorded':'stale','brain-pulse')+
    node('VENUE BOOK',book,bookCurrent?'current':'stale')+
    node('XAVIER / MANAGEMENT',xavier,pk.complete?'current':'blocked')+
    node('PROTECTION',protect,protectionMissing?'blocked':standing.length?'current':'stale')+
    node('NEXT PRICE CONDITION',tgt,target?'recorded':'stale');
}

function microPath(series){
  const xs=(series||[]).filter(x=>C.finite(x.price)&&C.finite(x.at)).slice(-32);
  if(!xs.length)return '';
  const W=100,H=20,pad=1.5;
  const vals=xs.map(x=>x.price),lo=Math.min(...vals),hi=Math.max(...vals),span=Math.max(.005,hi-lo);
  const points=xs.map((x,i)=>[
    pad+i*(W-2*pad)/Math.max(1,xs.length-1),
    H-pad-(x.price-lo)/span*(H-2*pad)
  ]);
  const d=points.map((v,i)=>`${i?'L':'M'}${v[0].toFixed(2)},${v[1].toFixed(2)}`).join(' ');
  const last=points[points.length-1];
  return `<svg viewBox="0 0 100 20" preserveAspectRatio="none" aria-label="Observed bid history"><path d="${d}"></path><circle class="micro-last" cx="${last[0].toFixed(2)}" cy="${last[1].toFixed(2)}" r="1.4"></circle></svg>`;
}

function renderMicrocharts(snapshot){
  if(!snapshot)return;
  for(const p of snapshot.positions||[]){
    const card=[...document.querySelectorAll('.position-card')].find(el=>el.dataset.id===p.position_id);
    if(!card)continue;
    let host=card.querySelector('.micro-chart');
    if(!host){
      host=document.createElement('div');
      host.className='micro-chart';
      const footer=card.querySelector('.card-footer');
      if(footer)footer.before(host);else card.append(host);
    }
    // write only on change: this runs from a MutationObserver on the wall,
    // and an unconditional innerHTML write re-triggers it forever (the
    // delivered patch froze the page as soon as positions rendered)
    const html=microPath(C.series(p))||'<span class="age-label">No observed price history</span>';
    if(host.dataset.sig!==html){host.innerHTML=html;host.dataset.sig=html;}
  }
}

function quoteDirection(prior,p){
  const q=p&&p.quote||{};
  if(!prior||!C.finite(prior.bid)||!C.finite(q.bid))return null;
  if(q.bid>prior.bid)return 'up';
  if(q.bid<prior.bid)return 'down';
  return 'flat';
}

function fireQuotePulse(p,prior){
  const cockpit=document.querySelector('.cockpit');
  if(!cockpit)return;
  const dir=quoteDirection(prior,p);
  if(!dir||dir==='flat')return;
  cockpit.classList.remove('cinematic-tick','cinematic-tick-up','cinematic-tick-down');
  void cockpit.offsetWidth;
  cockpit.classList.add('cinematic-tick',`cinematic-tick-${dir}`);

  let flare=cockpit.querySelector('.quote-flare');
  if(!flare){
    flare=document.createElement('div');
    flare.className='quote-flare';
    cockpit.append(flare);
  }
  const q=p.quote||{};
  const delta=(q.bid-prior.bid)*100;
  flare.textContent=`${dir==='up'?'▲':'▼'} ${delta>0?'+':''}${C.fmt(delta,1)}¢ · ${C.cents(q.bid)}`;

  clearTimeout(pulseTimer);
  pulseTimer=setTimeout(()=>cockpit.classList.remove('cinematic-tick','cinematic-tick-up','cinematic-tick-down'),1500);

  const card=[...document.querySelectorAll('.position-card')].find(el=>el.dataset.id===p.position_id);
  if(card){
    card.classList.remove('tick-up','tick-down');
    void card.offsetWidth;
    card.classList.add(dir==='up'?'tick-up':'tick-down');
    setTimeout(()=>card.classList.remove('tick-up','tick-down'),900);
  }
}

function inspect(){
  const tm=window.TraderMode;
  return tm&&typeof tm.inspect==='function'?tm.inspect():null;
}

function update(){
  const snapshot=inspect();
  if(!snapshot)return;

  const focus=focusedPosition(snapshot);
  renderBrain(snapshot,focus);
  renderMicrocharts(snapshot);

  for(const p of snapshot.positions||[]){
    const q=p.quote||{};
    const prior=quoteMemory.get(p.position_id);
    const key=q.observation_id??`${q.at??''}:${q.bid??''}`;
    if(prior&&prior.key!==key&&C.finite(q.bid)){
      if(focus&&focus.position_id===p.position_id)fireQuotePulse(p,prior);
    }
    quoteMemory.set(p.position_id,{key,bid:q.bid,at:q.at});
  }

  if(snapshot.snapshot_at!==lastSnapshot){
    document.body.dataset.snapshotPulse=String(snapshot.snapshot_at);
    lastSnapshot=snapshot.snapshot_at;
  }
  if(focus&&focus.position_id!==lastFocused){
    lastFocused=focus.position_id;
    const cockpit=document.querySelector('.cockpit');
    if(cockpit&&typeof cockpit.animate==='function'){
      cockpit.animate(
        [{filter:'brightness(.92)',transform:'scale(.997)'},{filter:'brightness(1)',transform:'scale(1)'}],
        {duration:420,easing:'cubic-bezier(.2,.8,.2,1)'}
      );
    }
  }
}

/* Ambient depth only. It moves the room, not market values. */
function parallax(e){
  if(document.body.classList.contains('reduced-motion'))return;
  if(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches)return;
  const x=(e.clientX/window.innerWidth-.5)*2;
  const y=(e.clientY/window.innerHeight-.5)*2;
  document.documentElement.style.setProperty('--mx',x.toFixed(3));
  document.documentElement.style.setProperty('--my',y.toFixed(3));
}
document.addEventListener('pointermove',parallax,{passive:true});

const observer=new MutationObserver(()=>update());
const start=()=>{
  const positions=$('positions'),market=$('focus-market');
  if(positions)observer.observe(positions,{childList:true,subtree:true,attributes:true});
  if(market)observer.observe(market,{childList:true,subtree:true});
  update();
};
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);
else start();

/* 500 ms display observer: no network access and no synthetic values. */
setInterval(update,500);

window.TraderCinematic={
  inspect,
  update,
  version:'BETTOR_TRADER_MODE_CINEMATIC_V2',
  dataAuthority:'TraderMode native ledger snapshot only'
};
})();
