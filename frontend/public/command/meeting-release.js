(function(){
'use strict';if(window.__BTMeetingRelease)return;window.__BTMeetingRelease=true;
var AGENTS=[
 ['derek','Derek','CIO · Discovery & Entry','#9fe3bf'],['karen','Karen','Red Team','#ff8197'],['scout','Scout','Market Intelligence','#f5b072'],
 ['eddie','Eddie','Head of Execution','#67d9ff'],['allocator','Allie','Chief Allocator','#ff9bcf'],['audrey','Audrey','Risk & Audit','#bca8ff'],['xavier','Xavier','Portfolio Mgmt','#9fd2f2']
];
function esc(x){return String(x==null?'':x).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function money(n,d){return typeof n==='number'&&isFinite(n)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:d||0,maximumFractionDigits:d||0}).format(n):'UNAVAILABLE';}
function signed(n){return typeof n==='number'&&isFinite(n)?(n>0?'+':n<0?'−':'')+money(Math.abs(n),2):'UNAVAILABLE';}
function ago(t){if(!t)return'NO HEARTBEAT';var s=Math.max(0,Date.now()/1000-t);return s<60?Math.round(s)+'s':s<3600?Math.floor(s/60)+'m':Math.floor(s/3600)+'h';}
function stateColor(a,def){var s=(a&&a.state)||'';return /CHALLENG/.test(s)?'#ff8197':/WORK|REVIEW/.test(s)?'#57e1ad':/WAIT/.test(s)?'#efca79':def;}
function patchAllie(root){
 root=root||document;var w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT),n,arr=[];while(n=w.nextNode())arr.push(n);
 /* integration: 'Chief Allocator' is Allie's TITLE (shown beside her name), so only the bare legacy label 'Allocator' becomes her name */arr.forEach(function(t){if(t.nodeValue&&t.nodeValue.trim()==='Allocator')t.nodeValue='Allie';});
}
/* real floor */
if(location.pathname==='/floor'){document.body.classList.add('meeting-real-floor');}
/* avatar-first agent pages */
if(/^\/(derek|xavier|audrey|karen|eddie|scout|allocator)\/?$/.test(location.pathname)){document.body.classList.add('meeting-agent');}
patchAllie();

/* executive homepage */
function buildHome(){
 if(location.pathname!=='/'&&location.pathname!=='/index.html')return;
 document.body.classList.add('meeting-home');if(document.getElementById('bt-meeting-home'))return;
 var app=document.getElementById('app'),h=document.createElement('main');h.id='bt-meeting-home';
 h.innerHTML='<div class="mtg-wrap"><header class="mtg-head"><div><div class="mtg-kicker">BETTORTOKEN COMMAND · COMPANY OPERATING SYSTEM</div><h1>The company, at a glance.</h1><p>Capital, people, current work and blockers first. Deep ledgers and diagnostics are still here when you need them.</p></div><div class="mtg-actions"><a class="mtg-btn primary" href="/floor">Enter Trading Floor →</a><a class="mtg-btn" href="/company">Company</a><a class="mtg-btn" href="/positions">Positions</a><a class="mtg-btn" href="/profitability">Profitability</a></div></header>'+
 '<section class="mtg-capital"><article class="mtg-paper"><div class="mtg-title"><div><div class="mtg-kicker">PAPER · SIMULATED EXECUTION</div><h2>$500,000 PAPER</h2></div><span class="mtg-status" id="mtg-p-status">READING</span></div><div class="mtg-equity" id="mtg-equity">READING</div><div class="mtg-change" id="mtg-change">waiting for recorded mark</div><div class="mtg-metrics"><div class="mtg-metric"><span>Realized</span><b id="mtg-realized">—</b></div><div class="mtg-metric"><span>Unrealized</span><b id="mtg-unrealized">—</b></div><div class="mtg-metric"><span>Exposure</span><b id="mtg-exposure">—</b></div><div class="mtg-metric"><span>Positions</span><b id="mtg-positions">—</b></div></div><div class="mtg-chart"><canvas id="mtg-chart"></canvas><span class="mtg-last" id="mtg-last">READING</span></div></article>'+
 '<article class="mtg-live"><div class="mtg-title"><div><div class="mtg-kicker">REAL MONEY · BETTOR ORIGINATED</div><h2>SMALL LIVE</h2></div><span class="mtg-status warn" id="mtg-l-status">READING</span></div><div class="mtg-live-state"><b id="mtg-l-state">READING</b><p id="mtg-l-reason">Checking the separately controlled live lane.</p></div><div class="mtg-live-grid"><div class="mtg-live-stat"><span>Capital</span><b id="mtg-l-cap">—</b></div><div class="mtg-live-stat"><span>Orders</span><b id="mtg-l-orders">—</b></div><div class="mtg-live-stat"><span>Fills</span><b id="mtg-l-fills">—</b></div><div class="mtg-live-stat"><span>Venue</span><b id="mtg-l-venue">—</b></div></div></article></section>'+
 '<section class="mtg-team"><div class="mtg-team-head"><div><div class="mtg-kicker">THE BETTOR TEAM</div><h2>Seven digital employees. No second tier.</h2><p>Every person has a workspace and current operating state.</p></div><a class="mtg-btn" href="/company">Open Company →</a></div><div class="mtg-team-grid" id="mtg-team"></div><div class="mtg-workspaces"><a href="/floor">Trading Floor<span>People + collaboration</span></a><a href="/positions">Position Rooms<span>Entry → management → audit</span></a><a href="/profitability">Profitability OS<span>Economics + opportunity</span></a><a href="/audrey">Audit & Risk<span>Reconciliation + blockers</span></a><a href="/improvements">Improvement Lab<span>Evidence → experiment</span></a><a href="/acceptance">Acceptance<span>Build → gate → production</span></a></div></section>'+
 '<section class="mtg-attention"><div><div class="mtg-kicker">MANAGEMENT ATTENTION</div><h3 id="mtg-attn">Reading current blockers</h3><p id="mtg-attn-sub">No issue is inferred before the evidence arrives.</p></div><a class="mtg-btn" href="/audrey">Open Audrey →</a></section>'+
 '<details class="mtg-deep" id="mtg-deep"><summary>Open the full operating ledger and detailed diagnostics</summary></details></div>';
 if(app)app.parentNode.insertBefore(h,app);else document.body.appendChild(h);
 /* integration: the stylesheet hides #app with !important, which beat this inline style, so the ledger never opened; toggle a body class the stylesheet honours */document.getElementById('mtg-deep').addEventListener('toggle',function(){document.body.classList.toggle('meeting-ledger-open',this.open);if(this.open&&app)app.scrollIntoView({behavior:'smooth'});});
 loadHome();
}
function loadHome(){
 /* integration: the seven employee links are drawn at once and the two reads are independent; production showed a slow equity read leaving the team and attention empty for 12+ s on phone when they waited on each other */
 renderTeam(null);
 fetch('/api/command/floor',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():Promise.reject()).then(renderTeam).catch(function(){var a=document.getElementById('mtg-attn');if(a)a.textContent='FLOOR READ UNAVAILABLE';var b=document.getElementById('mtg-attn-sub');if(b)b.textContent='No alert state is shown without the floor record. Open Audrey for audit evidence.';});
 fetch('/api/command/equity/live',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():Promise.reject()).then(renderEquity).catch(function(){var e=document.getElementById('mtg-equity');if(e)e.textContent='UNAVAILABLE';var l=document.getElementById('mtg-l-state');if(l)l.textContent='UNAVAILABLE';});
}
function renderEquity(x){
 var p=x.paper||{},s=x.small_live_bettor||x.small_live||{};window.__mtgPaper=p;
 document.getElementById('mtg-equity').textContent=money(p.equity_usd,2);
 document.getElementById('mtg-realized').textContent=signed(p.realized_pnl_usd);document.getElementById('mtg-unrealized').textContent=signed(p.unrealized_pnl_usd);
 document.getElementById('mtg-exposure').textContent=money(p.exposure&&p.exposure.marked_value_usd,2);document.getElementById('mtg-positions').textContent=(p.open_positions&&p.open_positions.count)!=null?p.open_positions.count:'UNAVAILABLE';
 var ch=p.day_change||{},c=document.getElementById('mtg-change');c.textContent=typeof ch.usd==='number'?signed(ch.usd)+(typeof ch.pct==='number'?' · '+ch.pct.toFixed(3)+'%':''):'NO CHANGE BASIS';c.className='mtg-change '+(ch.usd>0?'up':ch.usd<0?'down':'');
 var ps=document.getElementById('mtg-p-status');ps.textContent=p.status||'UNAVAILABLE';ps.className='mtg-status '+(/OK|LIVE|RUNNING/.test(p.status||'')?'good':'warn');
 var state=s.status||'UNAVAILABLE';document.getElementById('mtg-l-status').textContent=state;document.getElementById('mtg-l-state').textContent=state;document.getElementById('mtg-l-reason').textContent=s.why||s.reason||'BETTOR-originated execution remains separately controlled.';
 /* meeting integration: the API's small_live_bettor shape; a missing count is UNAVAILABLE, never a manufactured 0 */var cap=s.capital&&s.capital.usd,ord=s.orders&&s.orders.submitted,fil=s.fills&&s.fills.count;document.getElementById('mtg-l-cap').textContent=typeof cap==='number'?money(cap,2):'NONE ASSIGNED';document.getElementById('mtg-l-orders').textContent=typeof ord==='number'?ord:'UNAVAILABLE';document.getElementById('mtg-l-fills').textContent=typeof fil==='number'?fil:'UNAVAILABLE';document.getElementById('mtg-l-venue').textContent=s.path_configured===false?'NO LANE':(s.venue_state||'UNAVAILABLE');
 fetch('/api/command/equity/curve?book=PAPER&window=1d',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():Promise.reject()).then(drawCurve).catch(()=>{document.getElementById('mtg-last').textContent='CURVE UNAVAILABLE';});
}
function drawCurve(c){
 var pts=c.points||c.series||[],d=pts.map(function(q){var t=typeof q.t==='number'?q.t:Date.parse(q.at||q.timestamp||'')/1000;var v=Number(q.equity_usd!=null?q.equity_usd:q.value!=null?q.value:q.v);return{t:t,v:v}}).filter(q=>isFinite(q.t)&&isFinite(q.v));
 var can=document.getElementById('mtg-chart'),r=can.getBoundingClientRect(),dp=Math.min(devicePixelRatio||1,2);can.width=r.width*dp;can.height=r.height*dp;var x=can.getContext('2d');x.scale(dp,dp);x.clearRect(0,0,r.width,r.height);
 if(!d.length){x.fillStyle='#6f8298';x.font='10px monospace';x.fillText('NO RECORDED EQUITY POINTS',14,r.height/2);return}
 var lo=Math.min.apply(null,d.map(q=>q.v)),hi=Math.max.apply(null,d.map(q=>q.v)),pad=Math.max((hi-lo)*.2,.5);lo-=pad;hi+=pad;var t0=d[0].t,t1=d[d.length-1].t||t0+1;
 function X(t){return 15+(t-t0)/Math.max(1,t1-t0)*(r.width-30)}function Y(v){return r.height-16-(v-lo)/Math.max(1,hi-lo)*(r.height-36)}
 x.strokeStyle='rgba(130,160,200,.12)';for(var i=1;i<4;i++){x.beginPath();x.moveTo(0,i*r.height/4);x.lineTo(r.width,i*r.height/4);x.stroke()}
 x.strokeStyle='#67aaff';x.lineWidth=2.4;x.shadowBlur=12;x.shadowColor='rgba(65,105,255,.42)';x.beginPath();d.forEach(function(q,i){if(!i)x.moveTo(X(q.t),Y(q.v));else{x.lineTo(X(q.t),Y(d[i-1].v));x.lineTo(X(q.t),Y(q.v));}});x.stroke();x.shadowBlur=0;
 var last=d[d.length-1];x.fillStyle='#57e1ad';x.beginPath();x.arc(X(last.t),Y(last.v),4.5,0,Math.PI*2);x.fill();var pp=window.__mtgPaper||{};document.getElementById('mtg-last').textContent=money(last.v,2)+(pp.no_new_mark||pp.status!=='OK'?' · FROZEN · NO NEW MARK':' · LATEST REAL MARK');
}
function renderTeam(f){
 var by={};(f&&f.agents||[]).forEach(a=>by[a.slug]=a);
 document.getElementById('mtg-team').innerHTML=AGENTS.map(function(m){var a=by[m[0]],col=stateColor(a,m[3]);return '<a class="mtg-agent" href="/'+m[0]+'" style="--a:'+m[3]+'"><b>'+m[1]+'</b><span class="role">'+m[2]+'</span><div class="state" style="color:'+col+'"><i></i>'+(a?esc(String(a.state||'UNKNOWN').replace(/_/g,' '))+' · '+ago(a.heartbeat&&a.heartbeat.at):'STATE UNAVAILABLE')+'</div><div class="task">'+esc(a&&a.state_detail||'Open workspace for current evidence.')+'</div><span class="open">Open workspace →</span></a>';}).join('');
 if(!f)return; /* no floor read yet: attention stays READING (never 'no alert' before the evidence) */
 var alerts=[];(f&&f.agents||[]).forEach(function(a){(a.alerts||[]).forEach(function(z){alerts.push({who:a.slug==='allocator'?'Allie':a.name||a.agent,msg:z.message||z.summary||z.code||String(z)});});});
 if(alerts.length){document.getElementById('mtg-attn').textContent=alerts[0].who+' needs attention';document.getElementById('mtg-attn-sub').textContent=alerts[0].msg}else{document.getElementById('mtg-attn').textContent='No agent alert in the current floor read';document.getElementById('mtg-attn-sub').textContent='Open Audrey for complete audit and coverage evidence.'}
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',buildHome);else buildHome();
new MutationObserver(function(){patchAllie(document);}).observe(document.body,{childList:true,subtree:true});
})();
