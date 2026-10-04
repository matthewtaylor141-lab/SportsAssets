/* BETTOR COMMAND · FINAL CONVERGENCE
   Presentation/read-only only. Every number and agent state comes from
   Command read APIs. No financial action, no synthetic work, no fake price. */
(function(){
'use strict';
if(window.__BTCommandFinal)return; window.__BTCommandFinal=true;

var PATH=(location.pathname||'/').replace(/\/+$/,'')||'/';
var AGENT_PATH=/^\/(derek|karen|scout|eddie|allocator|audrey|xavier)$/;
var floorTimer=null, equitySamples=[], lastFloor=null, lastEquity=null;

function esc(x){return String(x==null?'':x).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function money(n,d){if(typeof n!=='number'||!isFinite(n))return'—';return new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:d==null?2:d,maximumFractionDigits:d==null?2:d}).format(n);}
function signed(n){return typeof n==='number'&&isFinite(n)?(n>0?'+':n<0?'−':'')+money(Math.abs(n),2):'—';}
function epoch(v){if(v==null)return null;if(typeof v==='number')return v;var n=Date.parse(v);return isNaN(n)?null:n/1000;}
function age(t){if(!t)return'never';var s=Math.max(0,Date.now()/1000-t);return s<60?Math.floor(s)+'s':s<3600?Math.floor(s/60)+'m':s<86400?(s/3600).toFixed(1)+'h':(s/86400).toFixed(1)+'d';}
function workState(a){return String(a&&a.work_state||a&&a.state||'UNKNOWN');}
function workDetail(a){return a&&a.work_detail||a&&a.state_detail||'No recorded work detail.';}
function workTone(s){
 s=String(s||'');
 if(/BLOCKED|FAILED|CHALLENG/.test(s))return'red';
 if(/WAITING|HANDOFF/.test(s))return'gold';
 if(/WORK|REVIEW/.test(s))return'green';
 if(/STALE|UNKNOWN|NOT_DEPLOYED/.test(s))return'dim';
 return'blue';
}
function human(s){
 var map={
  WORKING:'Working now',WORKING_ON:'Working now',REVIEWING:'Reviewing',
  CHALLENGING:'Challenging evidence',WAITING:'Waiting',
  WAITING_FOR_FRESH_EVIDENCE:'Waiting for fresh evidence',
  BLOCKED_ON_MARKET_DATA:'Blocked on market data',
  HANDOFF_PENDING:'Handoff pending',IDLE:'No open work',
  IDLE_NO_OPEN_WORK:'No open work',STALE:'Stale',UNKNOWN:'Unavailable',
  NOT_DEPLOYED:'Not deployed'
 };
 return map[s]||String(s||'UNKNOWN').replace(/_/g,' ').toLowerCase().replace(/^./,function(c){return c.toUpperCase()});
}
function fetchJSON(url){
 return fetch(url,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}})
  .then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json();});
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

  var wrap=home.querySelector('.mtg-wrap'),head=home.querySelector('.mtg-head');
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
   }
  }

  pollFloorHome(); subscribeEquityHome();
 }
 go();
}
function renderHomeFloor(f){
 lastFloor=f;
 var as=f&&f.agents||[];
 var active=as.filter(function(a){return /WORK|REVIEW|CHALLENG/.test(workState(a));});
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
  var from=as.find(function(x){return x.agent===e.from}),to=as.find(function(x){return x.agent===e.to});
  hn.textContent=(from&&from.slug==='allocator'?'Allie':from&&from.display_name||from&&from.name||e.from)+' → '+(to&&to.slug==='allocator'?'Allie':to&&to.display_name||to&&to.name||e.to);
  hs.textContent=String(e.kind||'collaboration').replace(/_/g,' ').toLowerCase()+' · '+age(e.at)+' ago';
 }
 // Upgrade any legacy cards immediately with work_state/work_detail.
 as.forEach(function(x){
  var card=document.querySelector('.hq4-agent-card.'+x.slug+',.mtg-agent[href="/'+x.slug+'"]');
  if(!card)return;
  var st=card.querySelector('.hq4-agent-state,.state'),dt=card.querySelector('.hq4-agent-task,.task');
  if(st){st.innerHTML='<i></i>'+esc(human(workState(x)))+' · '+esc(age(x.heartbeat&&x.heartbeat.at))+' ago';st.setAttribute('data-tone',workTone(workState(x)));}
  if(dt)dt.textContent=workDetail(x);
 });
}
function pollFloorHome(){
 function tick(){fetchJSON('/api/command/floor').then(renderHomeFloor).catch(function(){});}
 tick();floorTimer=setInterval(function(){if(!document.hidden)tick();},10000);
}
function subscribeEquityHome(){
 function attach(){
  if(!window.BTEquityWall){setTimeout(attach,250);return;}
  window.BTEquityWall.subscribe(function(st){
   lastEquity=st;var p=st&&st.live&&st.live.paper;if(!p)return;
   var open=document.getElementById('cf-openpos');if(open)open.textContent=p.open_positions&&p.open_positions.count!=null?p.open_positions.count:'—';
   var m=p.marks_as_of||{},fresh=typeof m.newest_age_s==='number'&&m.newest_age_s<=(m.stale_mark_after_s||300)&&p.status==='OK';
   var ms=document.getElementById('cf-mark-state');if(ms){ms.textContent=fresh?'LIVE MARKS':p.status==='STALE'?'STALE MARKS':'MARKS NOT CURRENT';ms.className=fresh?'live':p.status==='STALE'?'warn':'dim';}
   var ma=document.getElementById('cf-mark-age');if(ma)ma.textContent=typeof m.newest_age_s==='number'?Math.floor(m.newest_age_s)+'s':'—';
   var mk=document.getElementById('cf-marked');if(mk)mk.textContent=p.open_positions&&p.open_positions.marked!=null?p.open_positions.marked:'—';
   var un=document.getElementById('cf-unmarked');if(un)un.textContent=p.open_positions&&p.open_positions.unmarked!=null?p.open_positions.unmarked:'—';
   recordEquitySample(p,st.serverNow||Date.now()/1000);
   drawLiveEquity(st);
  });
 }
 attach();
}
function recordEquitySample(p,now){
 if(typeof p.equity_usd!=='number'||!isFinite(p.equity_usd))return;
 var m=p.marks_as_of||{},t=epoch(m.newest_at)||epoch(p.source_at)||now;
 var fresh=p.status==='OK'&&typeof m.newest_age_s==='number'&&m.newest_age_s<=(m.stale_mark_after_s||300);
 if(!fresh)return;
 var last=equitySamples[equitySamples.length-1];
 if(!last||t>last.t+0.001||p.equity_usd!==last.v){
  equitySamples.push({t:t,v:p.equity_usd});
  if(equitySamples.length>1500)equitySamples=equitySamples.slice(-1500);
 }
}
function curvePoints(st){
 var out=[],curves=st&&st.curves||{},c=curves['PAPER||1d'];
 var pts=c&&c.data&&c.data.points||[];
 pts.forEach(function(p){if(typeof p.t==='number'&&typeof p.v==='number')out.push({t:p.t,v:p.v});});
 equitySamples.forEach(function(p){if(!out.length||p.t>out[out.length-1].t||p.v!==out[out.length-1].v)out.push(p);});
 return out.sort(function(a,b){return a.t-b.t;});
}
function drawLiveEquity(st){
 var can=document.getElementById('mtg-chart'); if(!can)return;
 var p=st&&st.live&&st.live.paper,pts=curvePoints(st); if(!p||typeof p.equity_usd!=='number')return;
 var m=p.marks_as_of||{},fresh=p.status==='OK'&&typeof m.newest_age_s==='number'&&m.newest_age_s<=(m.stale_mark_after_s||300);
 var now=st.serverNow||Date.now()/1000;
 if(fresh)pts.push({t:now,v:p.equity_usd,endpoint:true});
 if(!pts.length)pts=[{t:now,v:p.equity_usd,endpoint:true}];
 var rect=can.getBoundingClientRect();if(rect.width<10||rect.height<10)return;
 var dpr=Math.min(devicePixelRatio||1,2);can.width=Math.round(rect.width*dpr);can.height=Math.round(rect.height*dpr);
 var x=can.getContext('2d');x.setTransform(dpr,0,0,dpr,0,0);x.clearRect(0,0,rect.width,rect.height);
 var W=rect.width,H=rect.height,padL=16,padR=18,padT=18,padB=24;
 var vals=pts.map(function(q){return q.v}),lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals),span=Math.max(1,hi-lo),vp=Math.max(2,span*.18);lo-=vp;hi+=vp;
 var t1=now,t0=Math.max(pts[0].t,t1-86400);if(t1<=t0)t0=t1-60;
 function X(t){return padL+(Math.max(t0,Math.min(t1,t))-t0)/(t1-t0)*(W-padL-padR);}
 function Y(v){return padT+(hi-v)/(hi-lo)*(H-padT-padB);}
 // premium grid
 x.strokeStyle='rgba(137,163,196,.095)';x.lineWidth=1;
 for(var i=0;i<5;i++){var yy=padT+i*(H-padT-padB)/4;x.beginPath();x.moveTo(padL,yy);x.lineTo(W-padR,yy);x.stroke();}
 var base=500000;if(base>=lo&&base<=hi){x.strokeStyle='rgba(239,202,121,.22)';x.setLineDash([5,5]);x.beginPath();x.moveTo(padL,Y(base));x.lineTo(W-padR,Y(base));x.stroke();x.setLineDash([]);}
 // area under exact sample-and-hold line
 var grad=x.createLinearGradient(0,padT,0,H-padB);grad.addColorStop(0,'rgba(77,125,255,.28)');grad.addColorStop(1,'rgba(77,125,255,0)');
 x.beginPath();x.moveTo(X(pts[0].t),H-padB);x.lineTo(X(pts[0].t),Y(pts[0].v));
 for(i=1;i<pts.length;i++){x.lineTo(X(pts[i].t),Y(pts[i-1].v));x.lineTo(X(pts[i].t),Y(pts[i].v));}
 if(fresh)x.lineTo(X(now),Y(p.equity_usd));
 x.lineTo(X(fresh?now:pts[pts.length-1].t),H-padB);x.closePath();x.fillStyle=grad;x.fill();
 // glow line
 x.save();x.strokeStyle=fresh?'#71a7ff':'#7b8798';x.lineWidth=2.5;x.lineJoin='round';x.shadowColor=fresh?'rgba(79,130,255,.7)':'transparent';x.shadowBlur=fresh?15:0;x.beginPath();x.moveTo(X(pts[0].t),Y(pts[0].v));
 for(i=1;i<pts.length;i++){x.lineTo(X(pts[i].t),Y(pts[i-1].v));x.lineTo(X(pts[i].t),Y(pts[i].v));}
 if(fresh)x.lineTo(X(now),Y(p.equity_usd));x.stroke();x.restore();
 // endpoint
 var ex=X(fresh?now:pts[pts.length-1].t),ey=Y(fresh?p.equity_usd:pts[pts.length-1].v);
 x.fillStyle=fresh?'rgba(87,225,173,.18)':'rgba(150,160,174,.14)';x.beginPath();x.arc(ex,ey,10,0,Math.PI*2);x.fill();
 x.fillStyle=fresh?'#57e1ad':'#8794a6';x.beginPath();x.arc(ex,ey,4,0,Math.PI*2);x.fill();
 x.font='600 11px ui-monospace,monospace';x.fillStyle='#9fb2c6';x.textAlign='right';x.fillText(money(p.equity_usd,2),W-padR,14);x.textAlign='left';
 var lab=document.getElementById('mtg-last');if(lab)lab.textContent=(fresh?'LIVE · current recorded mark':'FROZEN · '+(p.status||'not current'))+' · '+money(p.equity_usd,2);
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
 }
 go();
}
function updateFloorHud(){
 var st=window.__floor,f=st&&st.floor;if(!f)return;
 var as=f.agents||[],active=as.filter(function(a){return /WORK|REVIEW|CHALLENG/.test(workState(a));}).length;
 var blocked=as.filter(function(a){return /BLOCKED|WAITING/.test(workState(a));}).length;
 var hand=as.filter(function(a){return /HANDOFF/.test(workState(a));}).length;
 var ids=[['cf-f-active',active+' / '+as.length],['cf-f-blocked',blocked],['cf-f-handoffs',hand]];
 ids.forEach(function(x){var n=document.getElementById(x[0]);if(n)n.textContent=x[1];});
 if(lastEquity&&lastEquity.live&&lastEquity.live.paper){var p=document.getElementById('cf-f-pos');if(p)p.textContent=lastEquity.live.paper.open_positions&&lastEquity.live.paper.open_positions.count!=null?lastEquity.live.paper.open_positions.count:'—';}
}

/* ───────────────── AGENT PAGES · mission-first management UX ───────── */
function installAgent(){
 var m=PATH.match(AGENT_PATH);if(!m)return; var slug=m[1];
 document.body.classList.add('command-final-agent');
 var tries=0;
 function go(){
  var work=document.querySelector('.hq6-agent-work'),person=document.querySelector('.hq6-agent-person');
  if(!work||!person){if(tries++<60)setTimeout(go,150);return;}
  if(document.getElementById('cf-agent-mission'))return;
  var mission=document.createElement('section');mission.id='cf-agent-mission';mission.className='cf-agent-mission';
  mission.innerHTML='<header><div><span class="cf-eyebrow">CURRENT MISSION</span><h2 id="cf-am-title">Reading '+(slug==='allocator'?'Allie':slug.replace(/^./,function(c){return c.toUpperCase();}))+'’s work</h2><p id="cf-am-detail">Only recorded work is shown.</p></div><span class="cf-work-chip" id="cf-am-state">READING</span></header>'+
   '<div class="cf-mission-grid"><div><span>Queue</span><b id="cf-am-queue">—</b><small id="cf-am-queue-sub">recorded tasks</small></div><div><span>Latest output</span><b id="cf-am-output">—</b><small id="cf-am-output-sub">recorded decision</small></div><div><span>Collaborating</span><b id="cf-am-peer">—</b><small id="cf-am-peer-sub">durable handoff</small></div><div><span>Management attention</span><b id="cf-am-attn">—</b><small>recorded alerts only</small></div></div>';
  work.insertBefore(mission,work.firstChild);
  function tick(){fetchJSON('/api/command/floor/'+slug).then(renderMission).catch(function(){});}
  tick();setInterval(function(){if(!document.hidden)tick();},8000);
 }
 go();
}
function renderMission(d){
 var a=d&&d.agent||{},s=workState(a),chip=document.getElementById('cf-am-state');
 if(chip){chip.textContent=human(s);chip.setAttribute('data-tone',workTone(s));}
 var t=document.getElementById('cf-am-title');if(t)t.textContent=human(s);
 var det=document.getElementById('cf-am-detail');if(det)det.textContent=workDetail(a);
 var q=d&&d.queue||[],qn=document.getElementById('cf-am-queue');if(qn)qn.textContent=q.length;
 var qs=document.getElementById('cf-am-queue-sub');if(qs)qs.textContent=q[0]?(q[0].title||q[0].kind||'open work'):'no open recorded queue';
 var outs=d&&d.outputs||[],on=document.getElementById('cf-am-output'),os=document.getElementById('cf-am-output-sub');
 if(on)on.textContent=outs[0]?(outs[0].verdict||outs[0].summary||'Recorded output'):'No recent output';
 if(os)os.textContent=outs[0]?age(outs[0].at)+' ago':'—';
 var tl=d&&d.timeline||[],e=tl.slice().sort(function(x,y){return(y.at||0)-(x.at||0);})[0],pn=document.getElementById('cf-am-peer'),ps=document.getElementById('cf-am-peer-sub');
 if(e){var own=a.agent,other=e.from===own?e.to:e.from,seat=window.BTFloor&&BTFloor.BY_AGENT&&BTFloor.BY_AGENT[other];if(pn)pn.textContent=seat?seat.name:other;if(ps)ps.textContent=String(e.kind||'collaboration').replace(/_/g,' ').toLowerCase()+' · '+age(e.at)+' ago';}
 else if(pn)pn.textContent='No current handoff';
 var alerts=a.alerts||d.alerts||[],an=document.getElementById('cf-am-attn');if(an)an.textContent=alerts.length?(alerts[0].message||alerts[0].summary||alerts[0].code||'Attention required'):'None recorded';
}

/* ───────────────── COMPANY CARDS · work-state first ────────────────── */
function normalizeVisibleAgentCards(){
 document.querySelectorAll('.hq4-agent-card,.mtg-agent').forEach(function(card){
  card.classList.add('cf-person-card');
 });
}
setInterval(normalizeVisibleAgentCards,3000);

/* Equity subscription on non-home pages powers floor HUD and global truth. */
function globalEquity(){
 function attach(){if(!window.BTEquityWall){setTimeout(attach,300);return;}window.BTEquityWall.subscribe(function(st){lastEquity=st;});}
 attach();
}

installHome();installFloor();installAgent();globalEquity();normalizeVisibleAgentCards();
})();
