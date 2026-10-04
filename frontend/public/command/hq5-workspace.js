(function(){
'use strict';
if(window.__BTHQ5)return;window.__BTHQ5=true;

var ROUTES=[
 {name:'Command Home',sub:'Executive overview',href:'/',ico:'⌂',keys:'G H'},
 {name:'Company',sub:'Digital + human team workspace',href:'/company',ico:'◇',keys:'G C'},
 {name:'Trading Floor',sub:'Live 3D headquarters',href:'/floor',ico:'◈',keys:'G F'},
 {name:'Position Rooms',sub:'Entry → orders → marks → management → audit',href:'/positions',ico:'◎',keys:'G P'},
 {name:'Profitability OS',sub:'Economics, opportunity, forecasting, twin',href:'/profitability',ico:'↗',keys:'G E'},
 {name:'Improvement Lab',sub:'Evidence → challenge → experiment → release',href:'/improvements',ico:'↻',keys:'G I'},
 {name:'Acceptance',sub:'Build → gate → production → forward proof',href:'/acceptance',ico:'✓',keys:'G A'},
 {name:'Derek',sub:'CIO · Discovery & Entry',href:'/derek',ico:'',agent:'derek'},
 {name:'Karen',sub:'Red Team',href:'/karen',ico:'',agent:'karen'},
 {name:'Scout',sub:'Market Intelligence',href:'/scout',ico:'',agent:'scout'},
 {name:'Eddie',sub:'Head of Execution',href:'/eddie',ico:'',agent:'eddie'},
 {name:'Allie',sub:'Chief Allocator',href:'/allocator',ico:'',agent:'allocator'},
 {name:'Audrey',sub:'Risk & Audit',href:'/audrey',ico:'',agent:'audrey'},
 {name:'Xavier',sub:'Portfolio Management',href:'/xavier',ico:'',agent:'xavier'}
];
function esc(x){return String(x==null?'':x).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function ago(t){if(!t)return'never';var s=Math.max(0,Date.now()/1000-t);return s<60?Math.round(s)+'s':s<3600?Math.floor(s/60)+'m':Math.floor(s/3600)+'h';}
function color(a){var s=(a&&a.state)||'';return /CHALLENG/.test(s)?'#ff8197':/WORK|REVIEW/.test(s)?'#57e1ad':/WAIT/.test(s)?'#efca79':'#6f8299';}
/* integration: the floor record carries agent/slug, not always a name; fall back to the floor's own seat names (floor-core.js), then the slug */
function nameOf(a){if(!a)return'';if(a.slug==='allocator'||a.agent==='CHIEF_ALLOCATOR')return'Allie';var seat=window.BTFloor&&window.BTFloor.BY_AGENT&&window.BTFloor.BY_AGENT[a.agent];return a.name||a.display_name||(seat&&seat.name)||(a.slug?a.slug.charAt(0).toUpperCase()+a.slug.slice(1):'');}

/* Company Pulse replaces the always-moving ticker. */
var bar=document.querySelector('.bt-hq2-pagebar');
if(bar&&!document.getElementById('hq5-pulse')){
  var b=document.createElement('button');b.id='hq5-pulse';b.className='hq5-pulse-btn';b.innerHTML='<i></i><span>Company Pulse</span>';b.type='button';
  var live=document.getElementById('bt-hq2-live');bar.insertBefore(b,live||null);
  var pop=document.createElement('aside');pop.className='hq5-pulse-pop';pop.id='hq5-pulse-pop';pop.hidden=true;pop.innerHTML='<div class="hq5-pulse-head"><b>Company Pulse</b><span>READING</span></div><div class="hq5-pulse-list"></div>';document.body.appendChild(pop);
  b.onclick=function(e){e.stopPropagation();pop.hidden=!pop.hidden;if(!pop.hidden)readPulse();};
  document.addEventListener('click',function(e){if(!pop.hidden&&!pop.contains(e.target)&&e.target!==b)pop.hidden=true;});
}
function readPulse(){
 fetch('/api/command/floor',{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}}).then(function(r){if(!r.ok)throw 0;return r.json()}).then(function(f){
   var as=f.agents||[],newest=0;as.forEach(a=>newest=Math.max(newest,a.heartbeat&&a.heartbeat.at||0));
   var age=newest?Date.now()/1000-newest:Infinity,btn=document.getElementById('hq5-pulse'),pop=document.getElementById('hq5-pulse-pop');
   if(btn){btn.classList.toggle('live',age<120);btn.classList.toggle('warn',age>=120&&age<900);btn.querySelector('span').textContent=(age<120?'Company Live':'Company Pulse')+' · '+ago(newest);}
   if(pop){pop.querySelector('.hq5-pulse-head span').textContent=as.length+' AGENTS · '+(f.edges||[]).length+' LINKS';pop.querySelector('.hq5-pulse-list').innerHTML=as.map(function(a){return '<a class="hq5-pulse-agent" href="/'+esc(a.slug)+'"><i style="--c:'+color(a)+'"></i><span><b>'+esc(nameOf(a))+'</b><small>'+esc(String(a.state||'UNKNOWN').replace(/_/g,' '))+'</small></span><em>'+ago(a.heartbeat&&a.heartbeat.at)+'</em></a>';}).join('');}
 }).catch(function(){var b=document.getElementById('hq5-pulse');if(b){b.classList.remove('live');b.classList.add('warn');b.querySelector('span').textContent='Company state unavailable';}});
}
readPulse();setInterval(function(){if(!document.hidden)readPulse();},15000);

/* Global command palette */
var backdrop=document.createElement('div');backdrop.className='hq5-palette-backdrop';backdrop.hidden=true;
backdrop.innerHTML='<section class="hq5-palette" role="dialog" aria-modal="true" aria-label="Navigate BETTOR Command"><div class="hq5-palette-search"><span>⌕</span><input id="hq5-q" autocomplete="off" placeholder="Go anywhere in BETTOR Command…" aria-label="Search Command"><kbd>ESC</kbd></div><div class="hq5-palette-results" id="hq5-results"></div></section>';
document.body.appendChild(backdrop);
var input=backdrop.querySelector('#hq5-q'),results=backdrop.querySelector('#hq5-results'),sel=0,filtered=ROUTES.slice();
function drawPalette(){
 results.innerHTML=filtered.map(function(x,i){return '<button type="button" class="hq5-cmd'+(i===sel?' on':'')+'" data-i="'+i+'"><span class="ico"'+(x.agent?' data-agent="'+esc(x.agent)+'" aria-hidden="true"':'')+'>'+esc(x.ico)+'</span><span><b>'+esc(x.name)+'</b><small>'+esc(x.sub)+'</small></span>'+(x.keys?'<kbd>'+esc(x.keys)+'</kbd>':'')+'</button>';}).join('') || '<div style="padding:20px;color:#74889e">No matching workspace.</div>';
 results.querySelectorAll('.hq5-cmd').forEach(function(btn){btn.onclick=function(){location.href=filtered[Number(btn.dataset.i)].href;};});
}
function openPalette(){backdrop.hidden=false;input.value='';filtered=ROUTES.slice();sel=0;drawPalette();setTimeout(()=>input.focus(),0);}
function closePalette(){backdrop.hidden=true;}
input.addEventListener('input',function(){var q=input.value.toLowerCase().trim();filtered=ROUTES.filter(x=>(x.name+' '+x.sub).toLowerCase().includes(q));sel=0;drawPalette();});
backdrop.addEventListener('click',function(e){if(e.target===backdrop)closePalette();});

/* Floor command layer */
function installFloor(){
 if(location.pathname!=='/floor'||document.getElementById('hq5-floorbar'))return;
 var bar=document.createElement('nav');bar.id='hq5-floorbar';bar.className='hq5-floorbar';bar.setAttribute('aria-label','Trading Floor views');
 bar.innerHTML='<span class="label">View</span>'+
  '<button class="hq5-view on" data-v="overview">Overview <kbd>1</kbd></button>'+
  '<button class="hq5-view" data-v="capital">Capital Wall <kbd>2</kbd></button>'+
  '<button class="hq5-view" data-v="opps">Opportunities <kbd>3</kbd></button>'+
  '<button class="hq5-view" data-v="health">Risk & Health <kbd>4</kbd></button>'+
  '<button class="hq5-view" data-v="active">Active Desk <kbd>5</kbd></button>'+
  '<button class="hq5-view" data-v="map">Map</button>';
 document.body.appendChild(bar);
 var summary=document.createElement('div');summary.className='hq5-floor-summary';summary.id='hq5-floor-summary';summary.innerHTML='<i></i><span>READING FLOOR</span>';document.body.appendChild(summary);
 var selected=document.createElement('div');selected.className='hq5-selected';selected.id='hq5-selected';selected.innerHTML='Focused on <b>desk</b>';document.body.appendChild(selected);

 function setOn(v){bar.querySelectorAll('.hq5-view').forEach(x=>x.classList.toggle('on',x.dataset.v===v));}
 function activeSlug(){
   var f=window.__floor&&window.__floor.floor,as=f&&f.agents||[];
   var a=as.filter(x=>/WORKING_ON|REVIEWING|CHALLENGING/.test(x.state||'')).sort((a,b)=>(b.heartbeat&&b.heartbeat.at||0)-(a.heartbeat&&a.heartbeat.at||0))[0];
   return a&&a.slug;
 }
 function view(v){
   var st=window.__floor;
   if(v==='overview'){
     /* integration: Overview also returns from the floor map to the 3D room when the room is available */
     if(st&&st.view==='2d'&&!st.sceneFailed&&document.getElementById('fl-view')&&!document.getElementById('fl-view').disabled)document.getElementById('fl-view').click();
     document.getElementById('fl-reset')&&document.getElementById('fl-reset').click();}
   else if(v==='capital'||v==='opps'||v==='health'){
     /* integration: on the 3D floor a wall screen is projected into the room and a click focuses the camera on it; where it is not projected (phone, map view, no WebGL) the same screen sits in the page, so bring it into view instead of a click nothing handles */
     var ws=document.getElementById({capital:'ws-equity',opps:'ws-feed',health:'ws-health'}[v]);
     if(ws){if(ws.closest('.wall-projected'))ws.click();else ws.scrollIntoView({behavior:'smooth',block:'center'});}
   }
   else if(v==='active'){
     var slug=activeSlug(),btn=slug&&document.querySelector('.fl-agent[data-slug="'+slug+'"]');
     if(slug&&st&&st.scene){if(st.selected===slug)st.scene.focus(slug);else if(btn)btn.click();}
   } else if(v==='map'){document.getElementById('fl-view')&&document.getElementById('fl-view').click();}
   setOn(v);
 }
 bar.addEventListener('click',function(e){var b=e.target.closest('[data-v]');if(b)view(b.dataset.v);});
 setInterval(function(){
   var st=window.__floor,f=st&&st.floor;if(!f)return;
   var active=(f.agents||[]).filter(a=>/WORKING_ON|REVIEWING|CHALLENGING/.test(a.state||'')).length;
   summary.innerHTML='<i></i><span><b>'+active+'</b> active · <b>'+(f.edges||[]).length+'</b> collaborations</span>';
   selected.hidden=!st.selected;
   if(st.selected){var a=(f.agents||[]).find(x=>x.slug===st.selected);selected.innerHTML='Focused on <b>'+esc(a?nameOf(a):st.selected)+'</b> · '+esc(a&&a.state?String(a.state).replace(/_/g,' '):'');}
 },1000);
 try{if(localStorage.getItem('bt.hq5.floorhint')==='1')document.body.classList.add('hq5-hint-done');else setTimeout(function(){document.body.classList.add('hq5-hint-done');localStorage.setItem('bt.hq5.floorhint','1');},9000);}catch(e){}
 window.__hq5FloorView=view;
}
installFloor();

/* Keyboard: Cmd/Ctrl+K everywhere; 1–5 on floor when not typing. */
document.addEventListener('keydown',function(e){
 var typing=/INPUT|TEXTAREA|SELECT/.test(document.activeElement&&document.activeElement.tagName)||document.activeElement&&document.activeElement.isContentEditable;
 if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();backdrop.hidden?openPalette():closePalette();return;}
 if(!backdrop.hidden){
   if(e.key==='Escape'){e.preventDefault();closePalette();}
   else if(e.key==='ArrowDown'){e.preventDefault();sel=Math.min(filtered.length-1,sel+1);drawPalette();}
   else if(e.key==='ArrowUp'){e.preventDefault();sel=Math.max(0,sel-1);drawPalette();}
   else if(e.key==='Enter'&&filtered[sel]){e.preventDefault();location.href=filtered[sel].href;}
   return;
 }
 if(location.pathname==='/floor'&&!typing&&window.__hq5FloorView&&/^[1-5]$/.test(e.key)){
   e.preventDefault();window.__hq5FloorView({1:'overview',2:'capital',3:'opps',4:'health',5:'active'}[e.key]);
 }
});
})();
