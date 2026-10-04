(function(){
'use strict';
if(window.__BTHQ3)return;window.__BTHQ3=true;
function money(n){return typeof n==='number'&&isFinite(n)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(n):'UNAVAILABLE';}
function esc(x){return String(x==null?'':x).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function ago(t){if(!t)return'NO HEARTBEAT';var s=Math.max(0,Date.now()/1000-t);return s<60?Math.round(s)+'s':s<3600?Math.floor(s/60)+'m':Math.floor(s/3600)+'h';}
function agentName(a){
  if(!a)return'';
  if(a.agent==='CHIEF_ALLOCATOR'||a.slug==='allocator')return'Allie';
  return a.name||a.agent||'';
}
function agentStateClass(a){var s=(a&&(a.work_state||a.state))||'';return /CHALLENG|BLOCKED/.test(s)?'challenge':/WORKING|REVIEW/.test(s)?'live':/WAIT|HANDOFF/.test(s)?'wait':'';}

/* Home operating brief */
function buildHome(){
  if(location.pathname!=='/'&&location.pathname!=='/index.html')return;
  if(document.getElementById('bt-hq3-home'))return;
  var home=document.createElement('section');home.id='bt-hq3-home';
  home.innerHTML='<div class="hq3-home-wrap"><div class="hq3-home-top"><article class="hq3-home-hero">'+
    '<div class="bt-kicker">BETTORTOKEN · AUTONOMOUS INVESTMENT OPERATING SYSTEM</div>'+
    '<h1>See the company think.</h1><p>Markets, decisions, execution, management, challenge and audit — one living operating system. Every pulse below is tied to a durable record.</p>'+
    '<div class="hq3-home-pulse" id="hq3-pulse"><span class="hq3-chip"><i></i>READING COMPANY STATE</span></div>'+
    '<div class="hq3-cta"><a class="primary" href="/floor">Enter the Trading Floor →</a><a href="/positions">Open Position Rooms</a><a href="/profitability">Profitability OS</a></div></article>'+
    '<div class="hq3-home-side"><article class="hq3-capital-card"><div class="bt-kicker">PAPER CAPITAL</div><div class="value" id="hq3-equity">READING</div><div class="sub" id="hq3-eq-sub">Recorded equity · simulated execution</div></article>'+
    '<article class="hq3-company-card"><div class="bt-kicker">AI COMPANY PULSE</div><div class="hq3-company-agents" id="hq3-agents"></div><div class="sub" id="hq3-company-sub">Waiting for agent heartbeats.</div></article></div></div></div>';
  var app=document.getElementById('app'); if(app)app.parentNode.insertBefore(home,app);else document.body.appendChild(home);
  readFloor();readEq();
}
function readFloor(){
 fetch('/api/command/floor',{credentials:'same-origin',cache:'no-store'}).then(function(r){if(!r.ok)throw 0;return r.json()}).then(function(f){
   var as=f.agents||[],active=as.filter(function(a){return /WORKING|REVIEW|CHALLENG/.test(a.work_state||a.state||'')}).length;
   var pulse=document.getElementById('hq3-pulse');if(pulse)pulse.innerHTML=
    '<span class="hq3-chip good"><i></i>'+active+' ACTIVE DESKS</span><span class="hq3-chip"><i></i>'+as.length+' AGENTS REPORTING</span>'+
    '<span class="hq3-chip"><i></i>'+((f.edges||[]).length)+' COLLABORATIONS</span>';
   var box=document.getElementById('hq3-agents');if(box)box.innerHTML=as.map(function(a){
     return '<div class="hq3-agent-mini '+agentStateClass(a)+'"><b>'+esc(agentName(a))+'</b><span>'+esc(String(a.work_state||a.state||'UNKNOWN').replace(/_/g,' '))+' · '+ago(a.heartbeat&&a.heartbeat.at)+'</span></div>';
   }).join('');
   var sub=document.getElementById('hq3-company-sub');if(sub)sub.textContent='Live state from durable work records · '+((f.edges||[]).length)+' recent collaboration links.';
 }).catch(function(){
   var p=document.getElementById('hq3-pulse');if(p)p.innerHTML='<span class="hq3-chip warn"><i></i>COMPANY STATE UNAVAILABLE</span>';
 });
}
function readEq(){
 fetch('/api/command/equity/live',{credentials:'same-origin',cache:'no-store'}).then(function(r){if(!r.ok)throw 0;return r.json()}).then(function(x){
   var p=x.paper||{},n=document.getElementById('hq3-equity'),s=document.getElementById('hq3-eq-sub');
   if(n)n.textContent=money(p.equity_usd);
   if(s)s.textContent='PAPER · '+(p.status||'UNAVAILABLE')+' · latest genuine mark '+(p.last_change_at||p.source_at||'UNAVAILABLE');
 }).catch(function(){var n=document.getElementById('hq3-equity');if(n)n.textContent='UNAVAILABLE';});
}

/* Allie should be her name everywhere visible. Keep CHIEF_ALLOCATOR as internal agent id. */
function renameAllie(root){
  root=root||document;
  var walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
  var n,targets=[];while(n=walker.nextNode())targets.push(n);
  targets.forEach(function(t){
    if(/Chief Allocator|>Allocator</.test(t.nodeValue||''))return;
    if(t.nodeValue&&t.nodeValue.trim()==='Allocator')t.nodeValue=t.nodeValue.replace('Allocator','Allie');
    if(t.nodeValue&&t.nodeValue.indexOf('Chief Allocator')>=0&&/workspace|office|desk|agent/i.test((t.parentNode&&t.parentNode.textContent)||'')){
      t.nodeValue=t.nodeValue.replace(/Chief Allocator/g,'Allie');
    }
  });
}
renameAllie(document);

/* Reference-video treatment: attach current real value badge to the latest endpoint.
   Never advances the endpoint; it follows whatever equity-wall.js actually renders. */
function accountData(key){
  var st=window.BTEquityWall&&window.BTEquityWall.state();var l=st&&st.live;if(!l)return null;
  if(key==='paper')return l.paper;
  var v=l.actual&&l.actual.venues;return v&&v[key];
}
function annotateEndpoints(){
  // integration fix (C28): this runs from a MutationObserver on the equity
  // wall; writing the badge mutates that same subtree, so the observer is
  // paused while we write and the badge is only rewritten when it changes
  // (otherwise the observer re-fires forever and freezes the page)
  if(window.__hq3EqObs)window.__hq3EqObs.disconnect();
  try{annotateEndpointsOnce();}finally{var h=document.querySelector('.btew-host');if(h&&window.__hq3EqObs)window.__hq3EqObs.observe(h,{childList:true,subtree:true,attributes:true});}
}
function annotateEndpointsOnce(){
  document.querySelectorAll('[data-ew-acct]').forEach(function(card){
    var plot=card.querySelector('.ew-plot');if(!plot)return;
    var end=plot.querySelector('.ew-end');var old=plot.querySelector('.bt-hq3-last');if(!end){if(old)old.remove();return;}
    var key=card.getAttribute('data-ew-acct'),a=accountData(key);if(!a||typeof a.equity_usd!=='number'){if(old)old.remove();return;}
    var tag=old||document.createElement('span');var cls='bt-hq3-last'+(end.classList.contains('live')?'':' frozen');
    var change=(a.day_change&&a.day_change.usd);var html='<b>'+money(a.equity_usd)+'</b><small>'+(end.classList.contains('live')?'LATEST REAL MARK':'FROZEN · NO NEW MARK')+(typeof change==='number'?' · '+(change>=0?'+':'−')+money(Math.abs(change)):'')+'</small>';
    var L=end.style.left||'90%',T=end.style.top||'50%';
    if(tag.className!==cls)tag.className=cls;if(tag.innerHTML!==html)tag.innerHTML=html;
    if(tag.style.left!==L)tag.style.left=L;if(tag.style.top!==T)tag.style.top=T;if(!old)plot.appendChild(tag);
  });
}
var eqObs=new MutationObserver(function(){annotateEndpoints()});window.__hq3EqObs=eqObs;
function hookEq(){var h=document.querySelector('.btew-host');if(!h){setTimeout(hookEq,500);return;}eqObs.observe(h,{childList:true,subtree:true,attributes:true});annotateEndpoints();}
hookEq();

/* Floor finishing layers */
function floorExtras(){
  var w=document.querySelector('.hq2-world');if(!w)return;
  if(!w.querySelector('.hq3-floor-wordmark')){var m=document.createElement('div');m.className='hq3-floor-wordmark';m.innerHTML='BETTOR<small>HEADQUARTERS</small>';w.appendChild(m);}
  if(!w.querySelector('.hq3-world-status')){var s=document.createElement('div');s.className='hq3-world-status';s.innerHTML='<span class="live">RECORDED ACTIVITY</span><span>7 INDEPENDENT DESKS</span><span class="hq3-allie-mark">CAPITAL ALLOCATION</span>';w.appendChild(s);}
}
setTimeout(floorExtras,50);
new MutationObserver(function(){renameAllie(document);floorExtras();}).observe(document.body,{childList:true,subtree:true});
if(location.pathname==='/'||location.pathname==='/index.html'){setInterval(function(){if(!document.hidden){readFloor();readEq();}},15000);}
document.addEventListener('DOMContentLoaded',buildHome);if(document.readyState!=='loading')buildHome();
})();
