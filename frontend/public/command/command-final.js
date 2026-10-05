/* BETTOR COMMAND · FINAL CONVERGENCE
   Presentation/read-only only. Every number and agent state comes from
   Command read APIs. No financial action, no synthetic work, no fake price. */
(function(){
'use strict';
if(window.__BTCommandFinal)return; window.__BTCommandFinal=true;

var PATH=(location.pathname||'/').replace(/\/+$/,'')||'/';
var AGENT_PATH=/^\/(derek|karen|scout|archer|eddie|allocator|audrey|xavier)$/;  // eddie: Archer's historical address (266)
var EQ_LIVE='/api/command/equity/live', EQ_CURVE='/api/command/equity/curve?book=PAPER&window=1d';
var floorTimer=null, equitySamples=[], lastFloor=null, lastEquity=null;
var eqOffset=0;            // server clock minus browser clock, from the latest equity read
var chartView=null;        // the x-domain frozen while marks are not current
var ownCurve=null;         // PAPER 1d curve read here only when the equity wall holds none

function esc(x){return String(x==null?'':x).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function fin(n){return typeof n==='number'&&isFinite(n);}
function money(n,d){if(!fin(n))return'—';return new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:d==null?2:d,maximumFractionDigits:d==null?2:d}).format(n);}
function signed(n){return fin(n)?(n>0?'+':n<0?'−':'')+money(Math.abs(n),2):'—';}
/* the homepage capital surface keeps meeting-release.js's exact formatting:
   a figure the read does not carry is UNAVAILABLE, never a zero */
function mMoney(n,d){return fin(n)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:d||0,maximumFractionDigits:d||0}).format(n):'UNAVAILABLE';}
function mSigned(n){return fin(n)?(n>0?'+':n<0?'−':'')+mMoney(Math.abs(n),2):'UNAVAILABLE';}
function epoch(v){if(v==null)return null;if(typeof v==='number')return v;var n=Date.parse(v);return isNaN(n)?null:n/1000;}
function ageS(s){if(!fin(s))return'—';s=Math.max(0,s);return s<90?Math.floor(s)+'s':s<5400?Math.floor(s/60)+'m':s<172800?(s/3600).toFixed(1)+'h':(s/86400).toFixed(1)+'d';}
function age(t){if(!t)return'never';return ageS(Date.now()/1000-t);}
function workState(a){return String(a&&a.work_state||a&&a.state||'UNKNOWN');}
function workDetail(a){return a&&a.work_detail||a&&a.state_detail||'No recorded work detail.';}
var ACTIVE=/^(WORKING|WORKING_ON|REVIEWING|CHALLENGING)$/;
function workTone(s){
 s=String(s||'');
 if(/^IDLE/.test(s))return'blue';
 if(/BLOCKED|FAILED|CHALLENG/.test(s))return'red';
 if(/WAITING|HANDOFF/.test(s))return'gold';
 if(/WORK|REVIEW/.test(s))return'green';
 if(/STALE|UNKNOWN|NOT_DEPLOYED/.test(s))return'dim';
 return'blue';
}
/* legacy IDLE only means "no run in progress": it knows nothing about open
   work, so it is never shown as "No open work" (R30's IDLE_NO_OPEN_WORK is) */
function human(s){
 var map={
  WORKING:'Working now',WORKING_ON:'Working now',REVIEWING:'Reviewing',
  CHALLENGING:'Challenging evidence',WAITING:'Waiting',
  WAITING_FOR_FRESH_EVIDENCE:'Waiting for fresh evidence',
  BLOCKED_ON_MARKET_DATA:'Blocked on market data',
  HANDOFF_PENDING:'Handoff pending',IDLE:'Idle',
  IDLE_NO_OPEN_WORK:'No open work',STALE:'Stale',UNKNOWN:'Unavailable',
  NOT_DEPLOYED:'Not deployed'
 };
 return map[s]||String(s||'UNKNOWN').replace(/_/g,' ').toLowerCase().replace(/^./,function(c){return c.toUpperCase()});
}
function seatOf(code){return window.BTFloor&&BTFloor.BY_AGENT&&BTFloor.BY_AGENT[code]||null;}
function nameOf(code,as){
 if(code==='CHIEF_ALLOCATOR')return'Allie';
 var s=seatOf(code);if(s)return s.name;
 var a=(as||[]).find(function(x){return x.agent===code;});
 return a&&(a.display_name||a.name)||String(code||'');
}
function fetchJSON(url,noAlias){
 // migration 266: a build before the rename knows Archer only as /eddie
 var ARCHER_SEG=/\/archer(?=\/|\?|$)/;
 return fetch(url,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}})
  .then(function(r){
   if(r.status===404&&!noAlias&&ARCHER_SEG.test(url))return fetchJSON(url.replace(ARCHER_SEG,'/eddie'),true);
   if(!r.ok)throw new Error('HTTP '+r.status);
   return r.json().then(function(j){return window.BTFloor&&BTFloor.dealias?BTFloor.dealias(j):j;});});
}
function setText(n,txt,cls){
 if(typeof n==='string')n=document.getElementById(n);if(!n)return null;
 txt=String(txt);if(n.textContent!==txt)n.textContent=txt;
 if(cls!=null&&n.className!==cls)n.className=cls;
 return n;
}

/* ── ONE WRITER PER FIGURE ───────────────────────────────────────────
   meeting-release.js / hq6-complete.js write some homepage figures ONCE
   from their own load-time reads. Once this layer has a newer read it owns
   those nodes: a later write by an older reader is put back at once (in the
   same microtask, before paint), so no stale value or style flip is shown. */
var owned={},ownObs=null,onForeign={},expectCanvas=0;
function own(id,text,cls){
 owned[id]={text:String(text),cls:cls==null?null:cls};
 var n=setText(id,text,cls);if(!n)return null;
 if(n.__cfWatched)return n;n.__cfWatched=true;
 ensureObs().observe(n,{childList:true,characterData:true,subtree:true,attributes:true,attributeFilter:['class']});
 return n;
}
function ensureObs(){
 if(!ownObs)ownObs=new MutationObserver(function(recs){
  var hit={};
  recs.forEach(function(r){
   var t=r.target.nodeType===1?r.target:r.target.parentElement;
   if(t&&t.id==='mtg-chart'&&r.type==='attributes'){if(expectCanvas>0)expectCanvas--;else hit['mtg-chart']=1;return;}
   while(t&&!(t.id&&owned[t.id]))t=t.parentElement;
   if(t)hit[t.id]=1;
  });
  Object.keys(hit).forEach(function(id){
   if(id==='mtg-chart'){if(onForeign[id])onForeign[id]();return;}
   var o=owned[id],el=document.getElementById(id);if(!o||!el)return;
   if(el.textContent!==o.text||(o.cls!=null&&el.className!==o.cls)){setText(el,o.text,o.cls);if(onForeign[id])onForeign[id]();}
  });
 });
 return ownObs;
}

/* ── EQUITY SOURCE ───────────────────────────────────────────────────
   Pages that load equity-wall.js share its one poll loop. Elsewhere (floor,
   agent pages, company…) the attach is bounded and falls back to a direct
   read-only GET of /api/command/equity/live every ~10 s, paused while the
   tab is hidden. Started only by a consumer (home, floor HUD). */
var eqSubs=[],eqStarted=false;
function emitEquity(st){
 lastEquity=st;
 if(st&&fin(st.serverNow))eqOffset=st.serverNow-Date.now()/1000;
 eqSubs.slice().forEach(function(cb){try{cb(st);}catch(e){/* one view never breaks another */}});
}
function onEquity(cb){
 eqSubs.push(cb);
 if(lastEquity){try{cb(lastEquity);}catch(e){/* ignore */}}
 if(eqStarted)return;eqStarted=true;
 var tries=0;
 (function attach(){
  if(window.BTEquityWall&&typeof window.BTEquityWall.subscribe==='function'){window.BTEquityWall.subscribe(emitEquity);return;}
  if(++tries<=12){setTimeout(attach,250);return;}
  directEquity();
 })();
}
function directEquity(){
 var etag=null,live=null,status='IDLE',offset=0,busy=false,timer=null,handed=false;
 function emit(){emitEquity({status:status,live:live,serverNow:Date.now()/1000+offset,curves:{},source:'direct'});}
 function tick(){
  if(handed)return;
  // a page may load equity-wall.js later (the floor does, after its 3D
  // scene): hand over to that one shared loop instead of polling twice
  if(window.BTEquityWall&&typeof window.BTEquityWall.subscribe==='function'){handed=true;clearInterval(timer);window.BTEquityWall.subscribe(emitEquity);return;}
  if(document.hidden||busy)return;busy=true;
  var h={Accept:'application/json'};if(etag)h['If-None-Match']=etag;
  var sent=Date.now();
  fetch(EQ_LIVE,{method:'GET',credentials:'same-origin',cache:'no-store',headers:h}).then(function(r){
   if(r.status===304){status=live?'OK':status;return null;}
   if(r.status===401||r.status===403){status='SIGNED_OUT';live=null;etag=null;return null;}
   if(!r.ok){status='ERROR';return null;}
   return r.json().then(function(j){
    if(!j||j.schema!=='bt.equity.v1'){status='ERROR';return;}
    var mid=(sent+Date.now())/2000;
    live=j;etag=r.headers.get('ETag')||j.etag||null;status='OK';offset=(fin(j.computed_at)?j.computed_at:mid)-mid;
   });
  }).catch(function(){status='ERROR';}).then(function(){busy=false;emit();});
 }
 tick();timer=setInterval(tick,10000);
 document.addEventListener('visibilitychange',function(){if(!document.hidden)tick();});
}
/* mark freshness from the RECORDED newest mark time (ages derived on the
   page, as the equity wall does); never from a client guess */
function markInfo(st,p){
 var m=p&&p.marks_as_of||{},lim=fin(m.stale_mark_after_s)?m.stale_mark_after_s:fin(p&&p.stale_after_s)?p.stale_after_s:300;
 var at=epoch(m.newest_at),now=Date.now()/1000+eqOffset;
 var a=at!=null?Math.max(0,now-at):fin(m.newest_age_s)?m.newest_age_s:null;
 var feedOk=!st||!st.status||st.status==='OK';
 return {age:a,limit:lim,newestAt:at,feedOk:feedOk,fresh:!!p&&feedOk&&p.status==='OK'&&a!=null&&a<=lim};
}

/* ───────────────── HOME · real-time company operating surface ───────── */
function installHome(){
 if(PATH!=='/'&&PATH!=='/index.html')return;
 var tries=0;
 function go(){
  var home=document.getElementById('bt-meeting-home');
  if(!home){if(tries++<40)setTimeout(go,150);return;}
  if(document.getElementById('cf-command-strip'))return;
  document.body.classList.add('command-final-home');

  var head=home.querySelector('.mtg-head');
  var strip=document.createElement('section'); strip.id='cf-command-strip'; strip.className='cf-command-strip';
  strip.innerHTML=
   '<div class="cf-strip-cell"><span>Company</span><b id="cf-company-state">READING</b><small id="cf-company-sub">current agent work states</small></div>'+
   '<div class="cf-strip-cell"><span>Active desks</span><b id="cf-active">—</b><small>real recorded work</small></div>'+
   '<div class="cf-strip-cell"><span>Blocked / waiting</span><b id="cf-blocked">—</b><small>requires evidence or input</small></div>'+
   '<div class="cf-strip-cell"><span>Open positions</span><b id="cf-openpos">—</b><small>PAPER · current inventory</small></div>'+
   '<div class="cf-strip-cell"><span>Latest handoff</span><b id="cf-handoff">—</b><small id="cf-handoff-sub">durable collaboration only</small></div>';
  head.insertAdjacentElement('afterend',strip);

  var paper=home.querySelector('.mtg-paper');
  if(paper){
   paper.classList.add('cf-paper');
   var chart=paper.querySelector('.mtg-chart');
   if(chart){
    chart.classList.add('cf-live-chart');
    var hdr=document.createElement('div');hdr.className='cf-chart-head';
    hdr.innerHTML='<div><span>LIVE MARK-TO-MARKET</span><b id="cf-mark-state">READING</b></div><div><span>Newest mark</span><b id="cf-mark-age">—</b></div><div><span>Marked positions</span><b id="cf-marked">—</b></div><div><span>Unmarked</span><b id="cf-unmarked">—</b></div>';
    chart.insertAdjacentElement('beforebegin',hdr);
    var can=chart.querySelector('#mtg-chart');
    if(can){can.setAttribute('aria-label','Live PAPER mark-to-market equity from current recorded position marks');}
    var dot=document.createElement('i');dot.className='cf-live-dot';dot.id='cf-live-dot';dot.hidden=true;dot.setAttribute('aria-hidden','true');chart.appendChild(dot);
   }
  }

  pollFloorHome(); subscribeEquityHome();
 }
 go();
}
function renderHomeFloor(f){
 lastFloor=f;
 var as=f&&f.agents||[];
 var active=as.filter(function(a){return ACTIVE.test(workState(a));});
 var blocked=as.filter(function(a){return /BLOCKED|WAITING|HANDOFF/.test(workState(a));});
 var company=document.getElementById('cf-company-state');
 if(company){
  company.textContent=blocked.length?'ATTENTION · '+blocked.length:'OPERATING';
  company.className=blocked.length?'warn':'good';
 }
 var a=document.getElementById('cf-active'); if(a)a.textContent=active.length+' / '+as.length;
 var b=document.getElementById('cf-blocked'); if(b)b.textContent=blocked.length;
 var edges=(f&&f.edges||[]).slice().sort(function(x,y){return(y.at||0)-(x.at||0);});
 var e=edges[0],hn=document.getElementById('cf-handoff'),hs=document.getElementById('cf-handoff-sub');
 if(e&&hn){
  hn.textContent=nameOf(e.from,as)+' → '+nameOf(e.to,as);
  hs.textContent=String(e.kind||'collaboration').replace(/_/g,' ').toLowerCase()+' · '+age(e.at)+' ago';
 }else if(hn){hn.textContent='None in window';if(hs)hs.textContent='no durable collaboration recorded';}
 // Upgrade any legacy cards immediately with work_state/work_detail.
 as.forEach(function(x){
  var card=document.querySelector('.hq4-agent-card.'+x.slug+',.mtg-agent[href="/'+x.slug+'"]');
  if(!card)return;
  var st=card.querySelector('.hq4-agent-state,.state'),dt=card.querySelector('.hq4-agent-task,.task');
  if(st){st.innerHTML='<i></i>'+esc(human(workState(x)))+' · '+esc(age(x.heartbeat&&x.heartbeat.at))+' ago';st.setAttribute('data-tone',workTone(workState(x)));}
  if(dt)dt.textContent=workDetail(x);
 });
 // Management attention: derived from recorded signals only (there is no
 // `alerts` field on a floor agent; reading one always said "no alert").
 var top=companyAttention(f);
 if(top){
  var t=top.top?nameOf(top.top.agent,as)+' · '+top.top.title:'No recorded attention item';
  var sub=top.top?top.top.text+(top.count>1?' · +'+(top.count-1)+' more recorded item'+(top.count>2?'s':''):''):'Read: '+top.read.join(', ')+'. Nothing blocked, waiting, challenged or failing.';
  own('mtg-attn',t);own('mtg-attn-sub',sub);
  own('hq6-attn',top.top?nameOf(top.top.agent,as)+' needs attention':'No recorded attention item');
  own('hq6-attn-p',top.top?top.top.title+' — '+top.top.text:'Nothing blocked, waiting, challenged or failing in the current floor read.');
  own('hq6-attn-t',top.top?'Recorded signal · '+top.top.source:'Sources read: '+top.read.join(', '));
  // hq6's brief is built after the homepage settles; claim it as soon as it exists
  if(!document.getElementById('hq6-attn')&&(renderHomeFloor.retry=(renderHomeFloor.retry||0)+1)<=20)setTimeout(function(){if(lastFloor)renderHomeFloor(lastFloor);},500);
 }
}
function pollFloorHome(){
 function tick(){fetchJSON('/api/command/floor').then(renderHomeFloor).catch(function(){
  if(lastFloor)return; // the last good read stays, labelled by its own age
  own('mtg-attn','FLOOR READ UNAVAILABLE');own('mtg-attn-sub','No attention state is shown without the floor record.');
 });}
 tick();floorTimer=setInterval(function(){if(!document.hidden)tick();},10000);
}
function renderCapital(st){
 var live=st&&st.live,p=live&&live.paper;
 if(!p){
  if(st&&(st.status==='SIGNED_OUT'||st.status==='ERROR')){
   own('mtg-equity','UNAVAILABLE');
   setText('cf-mark-state',st.status==='SIGNED_OUT'?'SIGN-IN REQUIRED':'UNAVAILABLE','dim');
   ['cf-mark-age','cf-marked','cf-unmarked','cf-openpos'].forEach(function(id){setText(id,'UNAVAILABLE');});
  }
  return;
 }
 own('mtg-equity',mMoney(p.equity_usd,2));
 own('mtg-realized',mSigned(p.realized_pnl_usd));
 own('mtg-unrealized',mSigned(p.unrealized_pnl_usd));
 own('mtg-exposure',mMoney(p.exposure&&p.exposure.marked_value_usd,2));
 var op=p.open_positions||{};
 own('mtg-positions',op.count!=null?op.count:'UNAVAILABLE');
 var ch=p.day_change||{};
 own('mtg-change',fin(ch.usd)?mSigned(ch.usd)+(fin(ch.pct)?' · '+ch.pct.toFixed(3)+'%':''):'NO CHANGE BASIS','mtg-change '+(ch.usd>0?'up':ch.usd<0?'down':''));
 own('mtg-p-status',p.status||'UNAVAILABLE','mtg-status '+(/OK|LIVE|RUNNING/.test(p.status||'')?'good':'warn'));
 var s=live.small_live_bettor||live.small_live;
 if(s){
  var state=s.status||'UNAVAILABLE',cap=s.capital&&s.capital.usd,ord=s.orders&&s.orders.submitted,fil=s.fills&&s.fills.count;
  own('mtg-l-status',state);own('mtg-l-state',state);
  own('mtg-l-reason',s.why||s.reason||'BETTOR-originated execution remains separately controlled.');
  own('mtg-l-cap',fin(cap)?mMoney(cap,2):'NONE ASSIGNED');own('mtg-l-orders',fin(ord)?ord:'UNAVAILABLE');own('mtg-l-fills',fin(fil)?fil:'UNAVAILABLE');
  own('mtg-l-venue',s.path_configured===false?'NO LANE':(s.venue_state||'UNAVAILABLE'));
 }
 setText('cf-openpos',op.count!=null?op.count:'UNAVAILABLE');
 var mi=markInfo(st,p);
 setText('cf-mark-state',p.status==='UNAVAILABLE'?'UNAVAILABLE':!mi.feedOk?'FEED INTERRUPTED':mi.fresh?'LIVE MARKS':p.status==='STALE'?'STALE MARKS':'MARKS NOT CURRENT',
  mi.fresh?'live':p.status==='STALE'||!mi.feedOk?'warn':'dim');
 setText('cf-mark-age',p.status==='UNAVAILABLE'?'UNAVAILABLE':mi.age!=null?ageS(mi.age)+' ago':'—');
 setText('cf-marked',op.marked!=null?op.marked:'UNAVAILABLE');
 setText('cf-unmarked',op.unmarked!=null?op.unmarked:'UNAVAILABLE');
 return mi;
}
var homeFresh=null;
function subscribeEquityHome(){
 onForeign['mtg-last']=onForeign['mtg-chart']=function(){if(lastEquity)drawLiveEquity(lastEquity);};
 // meeting-release.js draws #mtg-chart once from its own curve read; a draw
 // that is not ours (its canvas resize) is answered with an immediate redraw
 var can=document.getElementById('mtg-chart');
 if(can)ensureObs().observe(can,{attributes:true,attributeFilter:['width','height']});
 onEquity(function(st){
  var mi=renderCapital(st);var p=st&&st.live&&st.live.paper;
  if(p&&mi){recordEquitySample(p,mi);homeFresh=mi.fresh;}
  ensureCurve(st);
  drawLiveEquity(st);
 });
 // the mark AGE is derived from the recorded timestamp every second (no value moves);
 // when it crosses the stale bound the chart freezes at once
 setInterval(function(){
  var st=lastEquity,p=st&&st.live&&st.live.paper;if(!p||p.status==='UNAVAILABLE')return;
  var mi=markInfo(st,p);setText('cf-mark-age',mi.age!=null?ageS(mi.age)+' ago':'—');
  if(homeFresh!==null&&mi.fresh!==homeFresh){homeFresh=mi.fresh;renderCapital(st);drawLiveEquity(st);}
 },1000);
}
function ensureCurve(st){
 var c=st&&st.curves&&st.curves['PAPER||1d'];
 if(c&&(c.data||c.inflight))return;
 if(ownCurve&&(ownCurve.inflight||Date.now()-ownCurve.at<10000))return;
 ownCurve=ownCurve||{at:0,data:null};ownCurve.inflight=true;
 fetchJSON(EQ_CURVE).then(function(j){ownCurve.data=j;}).catch(function(){/* the line still draws from live samples */})
  .then(function(){ownCurve.inflight=false;ownCurve.at=Date.now();if(lastEquity)drawLiveEquity(lastEquity);});
}
function recordEquitySample(p,mi){
 if(!fin(p.equity_usd)||!mi.fresh)return;
 var t=mi.newestAt||epoch(p.source_at);if(t==null)return;
 var last=equitySamples[equitySamples.length-1];
 if(!last||t>last.t+0.001||p.equity_usd!==last.v){
  equitySamples.push({t:t,v:p.equity_usd});
  if(equitySamples.length>1500)equitySamples=equitySamples.slice(-1500);
 }
}
function curvePoints(st){
 var out=[],curves=st&&st.curves||{},c=curves['PAPER||1d'];
 var data=c&&c.data||ownCurve&&ownCurve.data;
 var pts=data&&data.points||[];
 pts.forEach(function(p){if(fin(p.t)&&fin(p.v))out.push({t:p.t,v:p.v});});
 out.sort(function(a,b){return a.t-b.t;});
 equitySamples.forEach(function(p){if(!out.length||p.t>out[out.length-1].t)out.push(p);});
 return out;
}
function drawLiveEquity(st){
 var can=document.getElementById('mtg-chart'); if(!can)return;
 var p=st&&st.live&&st.live.paper; if(!p)return;
 var dot=document.getElementById('cf-live-dot');
 if(!fin(p.equity_usd)){
  // UNAVAILABLE: no line at all rather than an old one that looks current
  expectCanvas+=2;can.width=can.width;can.height=can.height;
  if(dot)dot.hidden=true;can.removeAttribute('data-endpoint');chartView=null;
  own('mtg-last','UNAVAILABLE · '+(p.why||p.status||'no equity in this read'),'mtg-last frozen');
  return;
 }
 var mi=markInfo(st,p),fresh=mi.fresh,now=Date.now()/1000+eqOffset;
 var pts=curvePoints(st);
 if(!fresh){
  // FROZEN: the server's current value placed at its last genuine change,
  // never extended to now (same rule as the equity wall's chart model)
  var last=pts[pts.length-1],chg=epoch(p.last_change_at)||epoch(p.source_at)||(last?last.t:now);
  if(!last||last.v!==p.equity_usd)pts.push({t:Math.min(now,Math.max(chg,last?last.t:chg)),v:p.equity_usd});
 }
 if(!pts.length)pts=[{t:now,v:p.equity_usd}];
 var rect=can.getBoundingClientRect();if(rect.width<10||rect.height<10)return;
 var t1,t0;
 if(fresh){chartView=null;t1=now;}
 else{
  // the x-domain freezes when marks stop being current, so the frozen
  // endpoint keeps its exact position on every later poll
  var lastT=pts[pts.length-1].t;
  if(!chartView)chartView={t1:Math.max(lastT,Math.min(now,(mi.newestAt||lastT)+mi.limit))};
  if(lastT>chartView.t1)chartView.t1=lastT;
  t1=chartView.t1;
 }
 t0=Math.max(pts[0].t,t1-86400);if(t1<=t0)t0=t1-60;
 var dpr=Math.min(devicePixelRatio||1,2),cw=Math.round(rect.width*dpr),chh=Math.round(rect.height*dpr);
 expectCanvas+=2;can.width=cw;can.height=chh;
 var x=can.getContext('2d');x.setTransform(dpr,0,0,dpr,0,0);x.clearRect(0,0,rect.width,rect.height);
 var W=rect.width,H=rect.height,padL=16,padR=18,padT=26,padB=44;
 var vis=pts.filter(function(q){return q.t>=t0;}),prior=pts.filter(function(q){return q.t<t0;}).pop();
 if(prior)vis.unshift({t:t0,v:prior.v});
 if(!vis.length)vis=[pts[pts.length-1]];
 var vals=vis.map(function(q){return q.v}),lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals),span=Math.max(1,hi-lo),vp=Math.max(2,span*.18);lo-=vp;hi+=vp;
 function X(t){return padL+(Math.max(t0,Math.min(t1,t))-t0)/(t1-t0)*(W-padL-padR);}
 function Y(v){return padT+(hi-v)/(hi-lo)*(H-padT-padB);}
 // premium grid
 x.strokeStyle='rgba(137,163,196,.095)';x.lineWidth=1;
 for(var i=0;i<5;i++){var yy=padT+i*(H-padT-padB)/4;x.beginPath();x.moveTo(padL,yy);x.lineTo(W-padR,yy);x.stroke();}
 var base=500000;if(base>=lo&&base<=hi){x.strokeStyle='rgba(239,202,121,.22)';x.setLineDash([5,5]);x.beginPath();x.moveTo(padL,Y(base));x.lineTo(W-padR,Y(base));x.stroke();x.setLineDash([]);}
 var endT=fresh?now:vis[vis.length-1].t,endV=fresh?p.equity_usd:vis[vis.length-1].v;
 // area under the exact sample-and-hold line
 var grad=x.createLinearGradient(0,padT,0,H-padB);grad.addColorStop(0,fresh?'rgba(77,125,255,.28)':'rgba(120,135,155,.20)');grad.addColorStop(1,'rgba(77,125,255,0)');
 x.beginPath();x.moveTo(X(vis[0].t),H-padB);x.lineTo(X(vis[0].t),Y(vis[0].v));
 for(i=1;i<vis.length;i++){x.lineTo(X(vis[i].t),Y(vis[i-1].v));x.lineTo(X(vis[i].t),Y(vis[i].v));}
 if(fresh){x.lineTo(X(now),Y(vis[vis.length-1].v));x.lineTo(X(now),Y(p.equity_usd));}
 x.lineTo(X(endT),H-padB);x.closePath();x.fillStyle=grad;x.fill();
 // glow line
 x.save();x.strokeStyle=fresh?'#71a7ff':'#7b8798';x.lineWidth=2.5;x.lineJoin='round';x.shadowColor=fresh?'rgba(79,130,255,.7)':'transparent';x.shadowBlur=fresh?15:0;x.beginPath();x.moveTo(X(vis[0].t),Y(vis[0].v));
 for(i=1;i<vis.length;i++){x.lineTo(X(vis[i].t),Y(vis[i-1].v));x.lineTo(X(vis[i].t),Y(vis[i].v));}
 if(fresh){x.lineTo(X(now),Y(vis[vis.length-1].v));x.lineTo(X(now),Y(p.equity_usd));}
 x.stroke();x.restore();
 // endpoint: at NOW only while the newest recorded mark is current
 var ex=X(endT),ey=Y(endV);
 x.fillStyle=fresh?'rgba(87,225,173,.18)':'rgba(150,160,174,.14)';x.beginPath();x.arc(ex,ey,10,0,Math.PI*2);x.fill();
 x.fillStyle=fresh?'#57e1ad':'#8794a6';x.beginPath();x.arc(ex,ey,4,0,Math.PI*2);x.fill();
 x.font='600 11px ui-monospace,monospace';x.fillStyle='#9fb2c6';x.textAlign='right';x.fillText(money(p.equity_usd,2),W-padR,16);x.textAlign='left';
 if(dot){dot.hidden=!fresh;if(fresh){dot.style.left=ex.toFixed(1)+'px';dot.style.top=ey.toFixed(1)+'px';}}
 can.setAttribute('data-endpoint',JSON.stringify({x:Math.round(ex*10)/10,y:Math.round(ey*10)/10,t:Math.round(endT),v:endV,live:fresh}));
 var why=!mi.feedOk?'FEED INTERRUPTED':p.status==='STALE'?'STALE MARKS':p.status==='OK'?'MARKS NOT CURRENT':(p.status||'NOT CURRENT');
 own('mtg-last',(fresh?'LIVE · current recorded mark':'FROZEN · '+why)+' · '+money(p.equity_usd,2),'mtg-last'+(fresh?'':' frozen'));
}

/* ───────────────── FLOOR · truthful gamification + work states ─────── */
function installFloor(){
 if(PATH!=='/floor')return;
 document.body.classList.add('command-final-floor');
 var tries=0;
 function go(){
  var stage=document.querySelector('.fl-stage');if(!stage){if(tries++<50)setTimeout(go,150);return;}
  if(document.getElementById('cf-floor-hud'))return;
  var hud=document.createElement('div');hud.id='cf-floor-hud';hud.className='cf-floor-hud';
  hud.innerHTML='<div><span>DESKS ACTIVE</span><b id="cf-f-active">—</b></div><div><span>BLOCKED</span><b id="cf-f-blocked">—</b></div><div><span>HANDOFFS</span><b id="cf-f-handoffs">—</b></div><div><span>OPEN PAPER POSITIONS</span><b id="cf-f-pos">—</b></div><div class="cf-hud-truth"><i></i><span>REAL EVENTS ONLY</span></div>';
  stage.appendChild(hud);
  setInterval(updateFloorHud,1200);updateFloorHud();
  onEquity(updateFloorHud);
 }
 go();
}
function updateFloorHud(){
 var st=window.__floor,f=st&&st.floor;
 if(f){
  var as=f.agents||[],active=as.filter(function(a){return ACTIVE.test(workState(a));}).length;
  var blocked=as.filter(function(a){return /BLOCKED|WAITING/.test(workState(a));}).length;
  var hand=as.filter(function(a){return /HANDOFF/.test(workState(a));}).length;
  [['cf-f-active',active+' / '+as.length],['cf-f-blocked',blocked],['cf-f-handoffs',hand]].forEach(function(x){setText(x[0],x[1]);});
 }
 var e=lastEquity;if(!e)return;
 var p=e.live&&e.live.paper,op=p&&p.open_positions;
 setText('cf-f-pos',p&&op&&op.count!=null?op.count:e.status==='SIGNED_OUT'?'SIGN IN':(p||e.status==='ERROR'||e.status==='OK')?'UNAVAILABLE':'—');
}

/* ───────────────── AGENT PAGES · mission-first management UX ───────── */
/* Role framing is static identity (what each person is for). Every value
   shown beside it comes from the agent's own floor/detail read. */
var ROLE={
 derek:{eyebrow:'ENTRY DECISIONS · DISCOVERY',out:'Latest decision',metric:'Decision record'},
 karen:{eyebrow:'RED TEAM · EVIDENCE CHALLENGES',out:'Latest challenge',metric:'Open challenges raised'},
 scout:{eyebrow:'MARKET INTELLIGENCE · RESEARCH',out:'Latest research output',metric:'Research output'},
 archer:{eyebrow:'EXECUTION QUALITY · SHADOW',out:'Latest execution estimate',metric:'Execution estimate'},
 allocator:{eyebrow:'CAPITAL ALLOCATION · SHADOW SLEEVE',out:'Latest allocation run',metric:'Allocation'},
 audrey:{eyebrow:'AUDIT · RECONCILIATION',out:'Latest finding',metric:'Audit findings'},
 xavier:{eyebrow:'PORTFOLIO MANAGEMENT · OPEN POSITIONS',out:'Latest management review',metric:'Positions under management'}
};
function installAgent(){
 var m=PATH.match(AGENT_PATH);if(!m)return; var slug=m[1]==='eddie'?'archer':m[1],role=ROLE[slug];
 document.body.classList.add('command-final-agent');
 var tries=0;
 function go(){
  var work=document.querySelector('.hq6-agent-work'),person=document.querySelector('.hq6-agent-person');
  if(!work||!person){if(tries++<60)setTimeout(go,150);return;}
  if(document.getElementById('cf-agent-mission'))return;
  var mission=document.createElement('section');mission.id='cf-agent-mission';mission.className='cf-agent-mission cf-role-'+slug;
  var seat=window.BTFloor&&BTFloor.BY_SLUG&&BTFloor.BY_SLUG[slug];if(seat)mission.style.setProperty('--cf-a',seat.accent);
  var nm=slug==='allocator'?'Allie':slug.replace(/^./,function(c){return c.toUpperCase();});
  mission.innerHTML='<header><div><span class="cf-eyebrow">CURRENT MISSION · '+esc(role.eyebrow)+'</span><h2 id="cf-am-title">Reading '+esc(nm)+'’s work</h2><p id="cf-am-detail">Only recorded work is shown.</p></div><span class="cf-work-chip" id="cf-am-state">READING</span></header>'+
   '<div class="cf-mission-grid"><div><span>Queue</span><b id="cf-am-queue">—</b><small id="cf-am-queue-sub">recorded tasks</small></div><div><span>'+esc(role.out)+'</span><b id="cf-am-output">—</b><small id="cf-am-output-sub">recorded output</small></div><div><span>Collaborating</span><b id="cf-am-peer">—</b><small id="cf-am-peer-sub">durable handoff</small></div><div class="cf-am-attn-cell"><span>Management attention</span><b id="cf-am-attn">—</b><small id="cf-am-attn-sub">recorded signals only</small></div><div class="cf-am-role-cell"><span>'+esc(role.metric)+'</span><b id="cf-am-role">—</b><small id="cf-am-role-sub">from '+esc(nm)+'’s own record</small></div></div>';
  work.insertBefore(mission,work.firstChild);
  var ok=false;
  function tick(){fetchJSON('/api/command/floor/'+slug).then(function(d){ok=true;renderMission(d,slug);}).catch(function(e){
   if(ok)return; // the last good read stays on screen
   setText('cf-am-state','UNAVAILABLE');setText('cf-am-title','Current work unavailable');
   setText('cf-am-detail','The workspace read failed ('+(e&&e.message||'network')+'). No state is inferred.');
   setText('cf-am-attn','UNAVAILABLE');setText('cf-am-attn-sub','attention sources not read');
   setText('cf-am-role','UNAVAILABLE');setText('cf-am-role-sub','role record not read');
  });}
  tick();setInterval(function(){if(!document.hidden)tick();},8000);
 }
 go();
}
function monitorOf(a,re){var ms=a&&a.monitor||[];for(var i=0;i<ms.length;i++){if(re.test(ms[i].label||''))return ms[i];}return null;}
function mval(m){return m&&m.value!=null?m.value:null;}
/* one role-specific metric per person, only from what the payload carries */
function roleMetric(slug,d,a){
 var outs=d&&d.outputs||[],lo=a.last_output||a.focus||null,ch=a.challenges||{},wc=a.work_counts;
 var undeployed=a.deployed===false?'Not deployed ('+(a.deploy_why||'reason not recorded')+')':null;
 function U(why){return {b:'UNAVAILABLE',s:why};}
 if(undeployed)return U(undeployed);
 if(slug==='derek'){
  var dec=outs.filter(function(o){return o.kind==='paper_decisions';});
  var n24=mval(monitorOf(a,/^Decisions/)),e24=mval(monitorOf(a,/^ENTER/));
  if(!dec.length&&n24==null)return U('no paper decision in this read');
  var refused=dec.filter(function(o){return o.verdict==='REFUSE';});
  var why=refused[0]&&String(refused[0].summary||'').split(' · ').slice(1).join(' · ');
  return {b:n24!=null?n24+' decisions · '+(e24!=null?e24:'?')+' ENTER (24h)':dec.length+' recent decisions listed',
   s:(dec.length?refused.length+' of the last '+dec.length+' refused':'no decision listed')+(why?' · latest refusal: '+why:'')};
 }
 if(slug==='karen'){
  var open=ch.raised_open!=null?ch.raised_open:mval(monitorOf(a,/^Open challenges/));
  var r24=mval(monitorOf(a,/^Raised/));
  if(open==null)return U('challenge counts not read');
  var tg={};(d&&d.challenges&&d.challenges.given||[]).forEach(function(x){if(/^(OPEN|RESPONDED)$/.test(String(x.state||'')))tg[x.target_agent]=(tg[x.target_agent]||0)+1;});
  var tgs=Object.keys(tg).map(function(k){return nameOf(k)+' '+tg[k];});
  return {b:open+' open',s:(r24!=null?r24+' raised (24h)':'24h count not read')+(tgs.length?' · against '+tgs.join(', '):'')};
 }
 if(slug==='scout'){
  var feat=mval(monitorOf(a,/^Features/));
  if(feat==null&&!lo)return U('no research record in this read');
  return {b:feat!=null?feat+' features registered':'Count not read',s:lo?'latest proposal '+age(lo.at)+' ago · research shadow only':'no feature recorded'};
 }
 if(slug==='archer'){
  // his records keep their storage name (eddie_execution_estimates)
  var est=outs.filter(function(o){return /archer|eddie/.test(o.kind||'');})[0]||(lo&&/archer|eddie/.test(lo.kind||'')?lo:null);
  var n=mval(monitorOf(a,/^Estimates/));
  if(!est&&n==null)return U('no execution estimate in this read');
  var keys=est?Object.keys(est).filter(function(k){return /spread|depth|slippage|executable_edge/.test(k)&&est[k]!=null;}):[];
  return {b:n!=null?n+' estimates (24h)':'24h count not read',
   s:keys.length?keys.map(function(k){return k.replace(/_/g,' ')+' '+est[k];}).join(' · '):'spread / depth / slippage not carried by this read'};
 }
 if(slug==='allocator'){
  var run=outs.filter(function(o){return o.kind==='intel_runs';})[0]||(lo&&lo.kind==='intel_runs'?lo:null);
  var alloc=mval(monitorOf(a,/allocated/)),funded=mval(monitorOf(a,/^Funded/)),ranked=mval(monitorOf(a,/^Candidates/));
  if(!run&&alloc==null)return U('no allocation run in this read');
  return {b:alloc!=null?alloc+' SHADOW allocated':'Allocation not read',s:(funded!=null?funded+' funded':'funded not read')+(ranked!=null?' of '+ranked+' ranked':'')+' · notional SHADOW sleeve'};
 }
 if(slug==='audrey'){
  var f24=mval(monitorOf(a,/^Findings/)),c24=mval(monitorOf(a,/^Critical/)),cov=mval(monitorOf(a,/^Coverage/));
  var ev=(d&&d.challenges&&d.challenges.evaluated||[]).length;
  if(f24==null&&!lo)return U('no audit record in this read');
  return {b:(f24!=null?f24+' findings':'Findings not read')+(c24!=null?' · '+c24+' critical':'')+' (24h)',s:(cov!=null?cov+' coverage alert'+(cov===1?'':'s')+' (24h)':'coverage alerts not read')+(d&&d.challenges?' · '+ev+' challenge'+(ev===1?'':'s')+' evaluated':'')};
 }
 if(slug==='xavier'){
  if(wc&&wc.open_positions!=null){
   var stale=(wc.WAITING_FOR_FRESH_EVIDENCE||0),blk=(wc.BLOCKED_ON_MARKET_DATA||0);
   return {b:wc.open_positions+' open',s:(wc.CURRENT!=null?wc.CURRENT+' on current reviews':'current reviews not counted')+' · '+stale+' waiting for fresh evidence'+(blk?' · '+blk+' blocked on market data':'')+(wc.UNREVIEWED?' · '+wc.UNREVIEWED+' not yet reviewed':'')};
  }
  var mp=mval(monitorOf(a,/Managed positions · PAPER/));
  if(mp==null)return U('position counts not read');
  return {b:mp+' open (PAPER)',s:'review / stale-evidence counts arrive with work_counts'};
 }
 return U('no role metric for this desk');
}
/* MANAGEMENT ATTENTION from recorded signals only. An unread source is never
   taken as "nothing"; with no source read at all the answer is UNAVAILABLE. */
function attentionOf(a,d){
 var items=[],read=[],nm=nameOf(a.agent);
 var ws=a.work_state;
 if(ws!=null){read.push('work state');
  if(/BLOCKED|WAITING|HANDOFF/.test(ws))items.push({rank:/BLOCKED/.test(ws)?0:/HANDOFF/.test(ws)?1:2,tone:/BLOCKED/.test(ws)?'red':'gold',title:human(ws),text:a.work_detail||'no detail recorded',source:'work_state'});
 }
 var c=a.challenges||{};
 if(c.open_against!=null||c.raised_open!=null){read.push('challenges');
  if(c.open_against>0)items.push({rank:3,tone:'gold',title:c.open_against+' open challenge'+(c.open_against===1?'':'s')+' against '+nm,text:'awaiting an answer or an independent evaluation',source:'karen_challenges'});
  if(c.raised_open>0)items.push({rank:5,tone:'blue',title:c.raised_open+' raised challenge'+(c.raised_open===1?'':'s')+' still open',text:'awaiting the target’s answer',source:'karen_challenges'});
 }
 var recv=d&&d.challenges&&Array.isArray(d.challenges.received)?d.challenges.received:null;
 if(recv){read.push('received challenges');
  var open=recv.filter(function(x){return /^(OPEN|RESPONDED)$/.test(String(x.state||''));});
  if(open.length&&!(c.open_against>0))items.push({rank:3,tone:'gold',title:open.length+' received challenge'+(open.length===1?'':'s')+' unresolved',text:'latest: '+String(open[0].claim||open[0].challenge_id||'').slice(0,120),source:'karen_challenges'});
  else if(open.length&&items.length){var it=items.filter(function(x){return x.source==='karen_challenges'&&x.rank===3;})[0];if(it)it.text='latest: '+String(open[0].claim||open[0].challenge_id||'').slice(0,120);}
 }
 var sr=a.status_row;
 if(sr){read.push('run status');
  if(sr.last_error)items.push({rank:2,tone:'red',title:'Last run error',text:String(sr.last_error).slice(0,140)+(sr.errors?' · '+sr.errors+' error'+(sr.errors===1?'':'s')+' recorded':''),source:'agent_status'});
  else if(sr.errors>0)items.push({rank:4,tone:'gold',title:sr.errors+' run error'+(sr.errors===1?'':'s')+' recorded',text:'see the run history',source:'agent_status'});
 }
 var hb=a.heartbeat;
 if(hb&&hb.age_s!=null&&hb.stale_after_s!=null){read.push('heartbeat');
  if(hb.age_s>hb.stale_after_s)items.push({rank:4,tone:'dim',title:'Heartbeat stale',text:'last heartbeat '+ageS(hb.age_s)+' ago (stale after '+ageS(hb.stale_after_s)+')',source:'heartbeat'});
 }
 if(a.deployed===false){read.push('deployment');items.push({rank:6,tone:'dim',title:'Not deployed',text:a.deploy_why||'reason not recorded',source:'deployment'});}
 items.sort(function(x,y){return x.rank-y.rank;});
 return {items:items,read:read};
}
function companyAttention(f){
 var as=f&&f.agents||[];if(!as.length)return null;
 var all=[],read={};
 as.forEach(function(a){var r=attentionOf(a,null);r.read.forEach(function(k){read[k]=1;});r.items.forEach(function(it){if(it.rank<=4)all.push(Object.assign({agent:a.agent},it));});});
 all.sort(function(x,y){return x.rank-y.rank;});
 return {top:all[0]||null,count:all.length,read:Object.keys(read)};
}
function renderMission(d,slug){
 var a=d&&d.agent||{},s=workState(a),chip=document.getElementById('cf-am-state');
 if(chip){chip.textContent=human(s);chip.setAttribute('data-tone',workTone(s));chip.title=a.work_state?'work_state '+a.work_state:'legacy state '+(a.state||'UNKNOWN')+' (work_state not served)';}
 setText('cf-am-title',human(s));
 setText('cf-am-detail',workDetail(a));
 var q=d&&d.queue||[];setText('cf-am-queue',q.length);
 setText('cf-am-queue-sub',q[0]?(q[0].title||q[0].kind||'open work'):'no open recorded queue');
 var outs=d&&d.outputs||[],o=outs[0]||a.last_output||a.focus||null;
 setText('cf-am-output',o?(o.verdict&&o.summary&&o.summary.indexOf(o.verdict)!==0?o.verdict+' · '+o.summary:o.summary||o.verdict||'Recorded output'):'No recent output');
 setText('cf-am-output-sub',o?(o.kind?String(o.kind).replace(/_/g,' ')+' · ':'')+age(o.at)+' ago':'nothing recorded in this read');
 var tl=d&&d.timeline||[],e=tl.slice().sort(function(x,y){return(y.at||0)-(x.at||0);})[0];
 if(e){var me=a.agent,other=e.from===me?e.to:e.from;setText('cf-am-peer',nameOf(other));setText('cf-am-peer-sub',String(e.kind||'collaboration').replace(/_/g,' ').toLowerCase()+' · '+(e.from===me?'to ':'from ')+nameOf(other)+' · '+age(e.at)+' ago');}
 else{setText('cf-am-peer','No current handoff');setText('cf-am-peer-sub','no durable collaboration in the window');}
 var at=attentionOf(a,d),an=document.getElementById('cf-am-attn');
 if(!at.read.length){setText(an,'UNAVAILABLE');setText('cf-am-attn-sub','no attention source in this read');if(an)an.removeAttribute('data-tone');}
 else if(!at.items.length){setText(an,'None recorded');setText('cf-am-attn-sub','read: '+at.read.join(', '));if(an)an.setAttribute('data-tone','green');}
 else{var top=at.items[0];setText(an,top.title);setText('cf-am-attn-sub',top.text+(at.items.length>1?' · +'+(at.items.length-1)+' more':''));if(an)an.setAttribute('data-tone',top.tone);}
 // hq6's NOW panel beside the portrait reads alerts (a field no agent has); keep its attention cell on the same truth
 if(document.getElementById('hq6-now-attn'))own('hq6-now-attn',!at.read.length?'UNAVAILABLE':at.items.length?at.items[0].title:'None recorded');
 var rm=roleMetric(slug,d,a);setText('cf-am-role',rm.b);setText('cf-am-role-sub',rm.s);
 var cell=document.getElementById('cf-am-role');if(cell)cell.toggleAttribute('data-unavailable',rm.b==='UNAVAILABLE');
}

/* ───────────────── COMPANY CARDS · work-state first ────────────────── */
function normalizeVisibleAgentCards(){
 document.querySelectorAll('.hq4-agent-card,.mtg-agent').forEach(function(card){
  card.classList.add('cf-person-card');
 });
}
setInterval(normalizeVisibleAgentCards,3000);

installHome();installFloor();installAgent();normalizeVisibleAgentCards();
/* Command Ops R1: the executive header and the operations desk subscribe to
   this ONE equity loop (BTEquityWall where loaded, else the bounded direct
   read) instead of polling /api/command/equity/live a second time. */
window.BTCommandFinal={onEquity:onEquity};
})();
