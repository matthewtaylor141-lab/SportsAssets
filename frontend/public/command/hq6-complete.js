(function(){
'use strict';if(window.__BTHQ6)return;window.__BTHQ6=true;
var PATH=(location.pathname||'/').replace(/\/+$/,'')||'/';
var AGENTS=['derek','karen','scout','eddie','allocator','audrey','xavier'];

function esc(x){return String(x==null?'':x).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function ago(t){if(!t)return'never';var s=Math.max(0,Date.now()/1000-t);return s<60?Math.round(s)+'s':s<3600?Math.floor(s/60)+'m':Math.floor(s/3600)+'h';}
/* integration: floor agents carry display_name (not name); fall back to the floor's seat names, then the slug */
function nameOf(a){if(!a)return'';if(a.slug==='allocator'||a.agent==='CHIEF_ALLOCATOR')return'Allie';var seat=window.BTFloor&&window.BTFloor.BY_AGENT&&window.BTFloor.BY_AGENT[a.agent];return a.name||a.display_name||(seat&&seat.name)||(a.slug?a.slug.charAt(0).toUpperCase()+a.slug.slice(1):'');}
function humanState(slug,state,detail){
  state=String(state||'UNKNOWN');
  var M={
    derek:{WORKING_ON:'Reviewing opportunities',REVIEWING:'Reviewing an entry decision',WAITING:'Waiting for a qualified opportunity',IDLE:'Standing by for the next opportunity'},
    karen:{CHALLENGING:'Challenging the evidence',REVIEWING:'Reviewing a claim',WAITING:'Waiting for a claim to challenge',IDLE:'No open challenge'},
    scout:{WORKING_ON:'Researching a market pattern',REVIEWING:'Reviewing research evidence',WAITING:'Waiting for new research input',IDLE:'Standing by for a research candidate'},
    eddie:{WORKING_ON:'Evaluating execution quality',REVIEWING:'Reviewing execution economics',WAITING:'Waiting for an executable candidate',IDLE:'Standing by for an executable candidate'},
    allocator:{WORKING_ON:'Ranking capital allocation',REVIEWING:'Reviewing portfolio impact',WAITING:'Waiting for qualified candidates',IDLE:'No allocation decision in progress'},
    audrey:{WORKING_ON:'Reconciling the operating record',REVIEWING:'Auditing evidence and controls',WAITING:'Waiting for the next audit item',IDLE:'No active audit item'},
    xavier:{WORKING_ON:'Managing an open position',REVIEWING:'Reviewing position management',WAITING:'Waiting for fresh management evidence',IDLE:'No management action in progress'}
  };
  if(state==='STALE')return'Waiting for fresh system evidence';
  if(state==='NOT_DEPLOYED')return'Not deployed on this API';
  if(state==='UNKNOWN')return'Current work unavailable';
  return (M[slug]&&M[slug][state]) || String(detail||state).replace(/_/g,' ').toLowerCase().replace(/^./,c=>c.toUpperCase());
}
function edgeLabel(kind){return String(kind||'collaboration').replace(/_/g,' ').toLowerCase().replace(/^./,c=>c.toUpperCase());}

/* Five-item primary navigation. Governance remains in Company + Cmd/K palette. */
function simplifyNav(){
  var nav=document.querySelector('.bt-hq2-nav');if(!nav||nav.dataset.hq6)return;nav.dataset.hq6='1';
  var wanted=[['/','⌂','HQ'],['/floor','◈','Floor'],['/positions','◎','Positions'],['/profitability','↗','Economics'],['/company','◇','Company']];
  var p=PATH;nav.innerHTML=wanted.map(function(x){
    var active=x[0]==='/'?p==='/':x[0]==='/company'?(p==='/company'||p==='/acceptance'||p==='/improvements'||AGENTS.some(a=>p==='/'+a)):p.indexOf(x[0])===0;
    return '<a href="'+x[0]+'" class="'+(active?'active':'')+'"><span class="bt-hq2-ico">'+x[1]+'</span><span>'+x[2]+'</span></a>';
  }).join('');
  var title=document.querySelector('.bt-hq2-page-title');
  if(title&&AGENTS.some(a=>p==='/'+a))title.textContent='Company · '+(p==='/allocator'?'Allie':p.slice(1).replace(/^./,c=>c.toUpperCase()));
}
simplifyNav();

/* Homepage management brief: only durable floor facts. */
var briefTries=0;
function installBrief(){
  if(PATH!=='/'||document.getElementById('hq6-brief'))return;
  /* integration: the executive home is built on DOMContentLoaded (and after its own reads), so wait for it (bounded) instead of giving up 50 ms after load */
  var team=document.querySelector('.mtg-team'),capital=document.querySelector('.mtg-capital');if(!team||!capital){if(++briefTries<80)setTimeout(installBrief,125);return;}
  var sec=document.createElement('section');sec.id='hq6-brief';sec.className='hq6-brief';
  sec.innerHTML='<article class="hq6-brief-card attn"><div class="k">Needs attention</div><h3 id="hq6-attn">Reading current evidence</h3><p id="hq6-attn-p">No issue is inferred until the floor read arrives.</p><small id="hq6-attn-t"></small></article>'+
    '<article class="hq6-brief-card"><div class="k">Latest collaboration</div><h3 id="hq6-collab">Reading hand-offs</h3><p id="hq6-collab-p">Only durable collaboration events appear here.</p><small id="hq6-collab-t"></small></article>'+
    '<article class="hq6-brief-card"><div class="k">Active now</div><h3 id="hq6-active">Reading company state</h3><p id="hq6-active-p">Agent activity is derived from current heartbeats.</p><small id="hq6-active-t"></small></article>';
  capital.insertAdjacentElement('afterend',sec);
  fetch('/api/command/floor',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():Promise.reject()).then(function(f){
    var as=f.agents||[],alerts=[];
    as.forEach(function(a){(a.alerts||[]).forEach(z=>alerts.push({a:a,z:z}));});
    if(alerts.length){
      var z=alerts[0];document.getElementById('hq6-attn').textContent=nameOf(z.a)+' needs attention';
      document.getElementById('hq6-attn-p').textContent=z.z.message||z.z.summary||z.z.code||String(z.z);
      document.getElementById('hq6-attn-t').textContent='Recorded alert';
    }else{
      document.getElementById('hq6-attn').textContent='No current agent alert';
      document.getElementById('hq6-attn-p').textContent='Open Audrey for the complete audit and coverage record.';
    }
    var es=(f.edges||[]).slice().sort((a,b)=>(b.at||0)-(a.at||0)),e=es[0];
    if(e){
      var from=as.find(a=>a.agent===e.from),to=as.find(a=>a.agent===e.to);
      document.getElementById('hq6-collab').textContent=(nameOf(from)||e.from)+' → '+(nameOf(to)||e.to);
      document.getElementById('hq6-collab-p').textContent=e.summary||edgeLabel(e.kind);
      document.getElementById('hq6-collab-t').textContent=edgeLabel(e.kind)+' · '+ago(e.at)+' ago';
    }else document.getElementById('hq6-collab').textContent='No collaboration in the current window';
    var active=as.filter(a=>/WORKING_ON|REVIEWING|CHALLENGING/.test(a.state||''));
    document.getElementById('hq6-active').textContent=active.length+' active desk'+(active.length===1?'':'s');
    document.getElementById('hq6-active-p').textContent=active.length?active.map(a=>nameOf(a)+' — '+humanState(a.slug,a.state,a.state_detail)).join(' · '):'No active work is recorded in the current floor read.';
    var newest=Math.max.apply(null,as.map(a=>a.heartbeat&&a.heartbeat.at||0));document.getElementById('hq6-active-t').textContent=newest?'Newest heartbeat '+ago(newest)+' ago':'No heartbeat';
  }).catch(function(){
    ['hq6-attn','hq6-collab','hq6-active'].forEach(id=>document.getElementById(id).textContent='UNAVAILABLE');
  });
}
setTimeout(installBrief,50);

/* Agent page: persistent person + work, identity deeper, human status first. */
function installAgent(){
  var m=PATH.match(/^\/(derek|xavier|audrey|karen|allocator|eddie|scout)$/);if(!m)return;
  var slug=m[1],hero=document.querySelector('.bt-agent2'),tabs=document.getElementById('ws-tabs'),ws=document.getElementById('ws-root'),page=document.getElementById('page'),state=document.getElementById('state');
  if(!hero||hero.closest('.hq6-agent-layout'))return;
  var parent=hero.parentNode,layout=document.createElement('div');layout.className='hq6-agent-layout';
  var person=document.createElement('aside');person.className='hq6-agent-person';
  var work=document.createElement('main');work.className='hq6-agent-work';
  parent.insertBefore(layout,tabs||hero);layout.append(person,work);

  // keep the 3D person persistently visible
  person.appendChild(hero);
  var grid=hero.querySelector('.bt-agent2-grid'),panels=grid?Array.from(grid.querySelectorAll(':scope > .bt-agent2-panel')):[];
  var now=document.createElement('section');now.className='hq6-now';now.style.setProperty('--a',getComputedStyle(hero).getPropertyValue('--a'));
  now.innerHTML='<div class="eyebrow">NOW</div><h2 id="hq6-now-title">Reading current work</h2><p id="hq6-now-detail">Waiting for the current floor record.</p><div class="hq6-now-code" id="hq6-now-code">STATE · READING</div>'+
    '<div class="hq6-now-grid"><div class="hq6-now-cell"><span>Latest output</span><b id="hq6-now-output">READING</b></div><div class="hq6-now-cell"><span>Heartbeat</span><b id="hq6-now-hb">READING</b></div><div class="hq6-now-cell"><span>Working with</span><b id="hq6-now-with">READING</b></div><div class="hq6-now-cell"><span>Management attention</span><b id="hq6-now-attn">READING</b></div></div><div class="hq6-now-collab" id="hq6-now-collab">Collaboration history will appear from durable hand-offs and challenges.</div>';
  if(grid)grid.appendChild(now);

  // identity/personality are useful, but not the first screen
  var more=document.createElement('details');more.className='hq6-agent-more';more.innerHTML='<summary>Identity, personality, memory & voice</summary>';
  panels.forEach(p=>more.appendChild(p));
  var memory=document.querySelector('.bt-agent2-memory');if(memory)more.appendChild(memory);
  person.appendChild(more);

  // work and conversation stay beside the person
  if(tabs){work.appendChild(tabs);var b1=document.getElementById('tab-ws'),b2=document.getElementById('tab-desk');if(b1)b1.textContent='Work';if(b2)b2.textContent='Conversation';}
  if(ws)work.appendChild(ws);if(page)work.appendChild(page);if(state)work.appendChild(state);

  var kicker=hero.querySelector('.bt-kicker');if(kicker)kicker.textContent='BETTOR · DIGITAL EMPLOYEE · '+(window.BTFloor&&BTFloor.BY_SLUG[slug]?BTFloor.BY_SLUG[slug].short:'');
  // With the 3D employee always visible, work is the productive default.
  var workTab=document.getElementById('tab-ws');if(workTab&&!workTab.hidden)setTimeout(()=>workTab.click(),30);

  function render(d){
    var a=d&&d.agent?d.agent:(d&&d.slug?d:null);if(!a)return;
    document.getElementById('hq6-now-title').textContent=humanState(slug,a.state,a.state_detail);
    document.getElementById('hq6-now-detail').textContent=a.state_detail||'No additional work detail recorded.';
    document.getElementById('hq6-now-code').innerHTML='MACHINE STATE · '+esc(String(a.state||'UNKNOWN'))+' <span class="hq6-machine-state">'+ago(a.heartbeat&&a.heartbeat.at)+' AGO</span>';
    var out=a.last_output||a.focus||(d&&d.last_output)||(d&&d.focus);
    document.getElementById('hq6-now-output').textContent=out?(out.summary||out.kind||out.id||'Recorded output'):'No consequential output in current read';
    document.getElementById('hq6-now-hb').textContent=a.heartbeat&&a.heartbeat.at?ago(a.heartbeat.at)+' ago':'UNAVAILABLE';
    var tl=(d&&d.timeline)||[],peers=[];
    tl.slice().sort((x,y)=>(y.at||0)-(x.at||0)).slice(0,8).forEach(function(e){
      var other=e.from===a.agent?e.to:e.from,seat=window.BTFloor&&BTFloor.BY_AGENT&&BTFloor.BY_AGENT[other],n=seat?seat.name:other;
      if(n&&peers.indexOf(n)<0)peers.push(n);
    });
    document.getElementById('hq6-now-with').textContent=peers.length?peers.slice(0,3).join(', '):'No collaboration in current detail';
    var alerts=a.alerts||(d&&d.alerts)||[];
    document.getElementById('hq6-now-attn').textContent=alerts.length?(alerts[0].message||alerts[0].summary||alerts[0].code||'Recorded alert'):'No management request recorded';
    if(tl[0])document.getElementById('hq6-now-collab').textContent='Latest collaboration · '+edgeLabel(tl[0].kind)+' · '+ago(tl[0].at)+' ago';
  }
  fetch('/api/command/floor/'+slug,{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():Promise.reject()).then(render).catch(function(){
    document.getElementById('hq6-now-title').textContent='Current work unavailable';document.getElementById('hq6-now-code').textContent='STATE · UNAVAILABLE';
  });
}
setTimeout(installAgent,80);

/* Floor selected context + optional Watch BETTOR Work. */
function installFloor(){
  if(PATH!=='/floor')return;
  var bar=document.getElementById('hq5-floorbar');if(!bar){setTimeout(installFloor,250);return;}
  if(!bar.querySelector('.hq6-watch')){
    var watch=document.createElement('button');watch.type='button';watch.className='hq5-view hq6-watch';watch.textContent='Watch BETTOR Work';bar.appendChild(watch);
    var toast=document.createElement('div');toast.className='hq6-watch-toast';toast.id='hq6-watch-toast';document.body.appendChild(toast);
    /* integration: follow ONLY an edge that ARRIVES after activation. An edge
       counts as new when its recorded time is later than everything seen at
       activation (or it is an unseen edge at that same instant). A changed
       "newest" caused by older edges rolling out of the floor window is not
       an event and never moves the camera. */
    var on=false,seenAt=null,seenKeys={},timer=null;
    function edges(){var f=window.__floor&&window.__floor.floor;return (f&&f.edges)||[];}
    function key(e){return e&&(e.id||[e.kind,e.from,e.to,e.at,e.count].join('|'));}
    function arm(){seenAt=0;seenKeys={};edges().forEach(function(e){seenKeys[key(e)]=1;seenAt=Math.max(seenAt,e.at||0);});}
    function tick(){
      if(!on||seenAt===null)return;
      var fresh=edges().filter(function(e){var t=e.at||0;return t>seenAt||(t===seenAt&&!seenKeys[key(e)]);}).sort((a,b)=>(a.at||0)-(b.at||0));
      if(!fresh.length)return;
      var e=fresh[fresh.length-1];fresh.forEach(function(x){seenKeys[key(x)]=1;seenAt=Math.max(seenAt,x.at||0);});
      var seat=window.BTFloor&&BTFloor.BY_AGENT&&BTFloor.BY_AGENT[e.to],slug=seat&&seat.slug;
      var b=slug&&document.querySelector('.fl-agent[data-slug="'+slug+'"]');if(b)b.click();
      var from=window.BTFloor&&BTFloor.BY_AGENT&&BTFloor.BY_AGENT[e.from],to=window.BTFloor&&BTFloor.BY_AGENT&&BTFloor.BY_AGENT[e.to];
      toast.textContent=(from?from.name:e.from)+' → '+(to?to.name:e.to)+' · '+edgeLabel(e.kind);toast.classList.add('show');setTimeout(()=>toast.classList.remove('show'),4800);
    }
    watch.onclick=function(){
      on=!on;watch.classList.toggle('active',on);watch.textContent=on?'Watching Real Events':'Watch BETTOR Work';
      if(on)arm();else{seenAt=null;seenKeys={};}if(timer){clearInterval(timer);timer=null;}if(on)timer=setInterval(tick,2000);
    };
  }

  // Add management-language context to the existing evidence drawer.
  var lastSlug=null;
  setInterval(function(){
    var st=window.__floor,panel=document.getElementById('fl-panel');if(!st||!panel||panel.hidden||!st.selected)return;
    var slug=st.selected,a=(st.floor&&st.floor.agents||[]).find(x=>x.slug===slug);if(!a)return;
    var ctx=panel.querySelector('.hq6-context');
    if(!ctx){ctx=document.createElement('section');ctx.className='hq6-context';var head=panel.querySelector('.fl-p-head');if(head)head.insertAdjacentElement('afterend',ctx);}
    var es=(st.floor.edges||[]).filter(e=>e.from===a.agent||e.to===a.agent).sort((x,y)=>(y.at||0)-(x.at||0)),e=es[0],other=e&&(e.from===a.agent?e.to:e.from),seat=other&&window.BTFloor&&BTFloor.BY_AGENT&&BTFloor.BY_AGENT[other];
    ctx.innerHTML='<div class="k">CURRENT WORK</div><h3>'+esc(humanState(slug,a.state,a.state_detail))+'</h3><p>'+esc(a.state_detail||'No additional state detail recorded.')+'</p><small>MACHINE · '+esc(a.state||'UNKNOWN')+(e?' · LATEST COLLAB · '+esc(seat?seat.name:other)+' · '+ago(e.at)+' AGO':'')+'</small>';
    lastSlug=slug;
  },1000);
}
installFloor();
})();
