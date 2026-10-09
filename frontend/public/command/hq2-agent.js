(function(){
  'use strict';
  if(window.__BTHQ2Agent)return;window.__BTHQ2Agent=true;
  var B=window.BTFloor;if(!B)return;
  var slug=(location.pathname.replace(/\/+$/,'').split('/').pop()||'derek').toLowerCase();
  if(B.canonicalSlug)slug=B.canonicalSlug(slug);   // /eddie: Archer's historical address (266)
  var s=B.BY_SLUG[slug]||B.BY_SLUG.derek;
  var P={
    derek:["The edge has to survive the math.","Decisive, concise, probability-first. He wants the thesis, the edge, the invalidation condition and the reason to act now."],
    xavier:["Fresh evidence before clever management.","Calm and loss-aware. He manages inventory, downside and alternatives, and refuses to pretend stale evidence is current."],
    audrey:["If the ledger disagrees, the story is wrong.","Literal, forensic and citation-heavy. She reconciles before she interprets and never turns missing evidence into zero."],
    karen:["What are we missing? Prove it.","Contrarian and dry. She attacks unsupported claims, hidden assumptions and convenient certainty. She has no authority to approve anything."],
    allocator:["The portfolio matters more than the trade.","Conservative capital steward. Correlation, concentration, capacity and opportunity cost outrank a single attractive idea."],
    archer:["Price is not execution.","Fast, terse and microstructure-obsessed. Spread, depth, fees, fill probability and latency determine whether theoretical edge survives."],
    scout:["Find structure. Do not fall in love with it.","Curious and experimental. He hunts patterns and alternate data, but a research hypothesis never promotes itself."]
  };
  var quote=P[slug]||["Independent agent.",""]; var hero=document.createElement('section');hero.className='bt-agent2';hero.dataset.agent=slug;hero.style.setProperty('--a',s.accent);
  hero.innerHTML='<div class="bt-agent2-grid"><div class="bt-agent2-id"><div class="bt-agent2-face bt-portrait is-loading" data-slug="'+s.slug+'" data-portrait="loading"><canvas role="img" aria-label="'+s.name+', 3D portrait"></canvas><div class="bt-portrait-card" aria-hidden="true"><b>'+s.initial+'</b><span>'+s.name+'</span></div><div class="bt-portrait-tag">LOADING 3D PORTRAIT</div><small class="bt-portrait-credit"></small></div><div><div class="bt-kicker">BETTOR AI · '+s.short+'</div><h1>'+s.name+'</h1><p>'+s.role+'</p><div class="bt-agent2-state" id="bt-agent2-state"><i></i><span>READING LIVE STATE</span></div></div></div>'+
    '<div class="bt-agent2-panel"><div class="k">Personality</div><div class="quote">'+quote[0]+'</div><p>'+quote[1]+'</p></div>'+
    '<div class="bt-agent2-panel"><div class="k">Living identity</div><div class="bt-agent2-stats"><div class="bt-agent2-stat"><span>Memory</span><b id="bt-agent2-mem">NOT RELEASED</b></div><div class="bt-agent2-stat"><span>Experience</span><b id="bt-agent2-exp">NOT RELEASED</b></div><div class="bt-agent2-stat"><span>Voice</span><b id="bt-agent2-voice">DESK VOICE</b></div><div class="bt-agent2-stat"><span>Heartbeat</span><b id="bt-agent2-hb">READING</b></div></div><div class="bt-agent2-actions"><button class="primary" id="bt-agent2-talk">Talk to '+s.name+'</button><a href="/floor">Back to floor</a></div></div></div>';
  var anchor=document.getElementById('ws-tabs')||document.getElementById('ws-root')||document.body.firstChild;
  if(anchor&&anchor.parentNode)anchor.parentNode.insertBefore(hero,anchor.nextSibling);else document.body.insertBefore(hero,document.body.firstChild);
  var memory=document.createElement('div');memory.className='bt-agent2-memory';memory.innerHTML='<span class="unreleased">MEMORY + EXPERIENCE API</span> · The interface is ready for independent episodic memory, learned experience and voice identity. Until the backend releases evidence-backed memory records, this panel intentionally does not invent a history.';
  hero.insertAdjacentElement('afterend',memory);
  function update(a){
    var m=B.STATES[B.stateOf(a)],st=document.getElementById('bt-agent2-state');if(st){st.style.color=m.color;st.querySelector('span').textContent=m.label+(a&&(a.work_detail||a.state_detail)?' · '+(a.work_detail||a.state_detail):'');}
    // the portrait's pose follows the same recorded state (the floor's mapping):
    // stale / not deployed / unknown settle and stay still, never animated
    var st0=B.stateOf(a),mode=st0==='STALE'||st0==='NOT_DEPLOYED'||st0==='UNKNOWN'?'unavailable':/^WAITING/.test(st0)?'waiting':m.motion==='review'||m.motion==='work'?'reviewing':'monitoring';
    if(document.body.getAttribute('data-cc-mode')!==mode){document.body.setAttribute('data-cc-mode',mode);window.dispatchEvent(new CustomEvent('cc:mode',{detail:{mode:mode}}));}
    var hb=document.getElementById('bt-agent2-hb');if(hb)hb.textContent=a&&a.heartbeat&&a.heartbeat.at?B.ago(a.heartbeat.at):'UNAVAILABLE';
  }
  B.poller('/api/command/floor/'+slug,15000,function(r){
    var d=r.current.status==='OK'?r.current.data:r.lastOk&&r.lastOk.data;var a=d&&d.agent?d.agent:(d&&d.slug?d:null);if(a)update(a);
  });
  // Optional future identity endpoint. A 404 is NOT RELEASED, never a fabricated empty memory.
  // migration 266: a build before the rename knows Archer only as /eddie
  var idRead=function(s,again){return fetch('/api/command/agents/'+s+'/identity',{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}}).then(function(r){
    if(r.status===404&&s==='archer'&&!again)return idRead('eddie',true);
    if(r.status===404)return null;if(!r.ok)throw new Error();return r.json();
  });};
  idRead(slug).then(function(j){if(!j)return;var mem=document.getElementById('bt-agent2-mem'),exp=document.getElementById('bt-agent2-exp'),v=document.getElementById('bt-agent2-voice');
    if(mem)mem.textContent=j.memory&&j.memory.count!=null?j.memory.count+' memories':'UNAVAILABLE';
    if(exp)exp.textContent=j.experience&&j.experience.events!=null?j.experience.events+' resolved events':'UNAVAILABLE';
    if(v&&j.voice&&j.voice.display_name)v.textContent=j.voice.display_name;
  }).catch(function(){});
  // Talk opens the agent's conversation: the classic desk tab where the page
  // has the two tabs (derek, xavier, audrey, karen), or the framed page
  // (archer, scout: their tabs are never shown, and clicking the hidden desk
  // tab hid the live floor strip above the page). Allie has no conversation
  // desk (agent.html CLASSIC): Talk opened one anyway -- a GET of a page no
  // API serves (404) and "NOT YET RELEASED" -- so her page draws no Talk.
  (function(){
    var talk=document.getElementById('bt-agent2-talk'),desk=document.getElementById('tab-desk'),tabs=document.getElementById('ws-tabs'),frame=document.getElementById('page');
    var tabbed=!!(desk&&!desk.hidden&&tabs&&!tabs.hidden),framed=!!(frame&&(!tabs||tabs.hidden)&&!(desk&&desk.hidden));
    if(!tabbed&&!framed){talk.hidden=true;return;}
    talk.addEventListener('click',function(){
      if(tabbed){desk.click();setTimeout(function(){frame&&frame.scrollIntoView({behavior:'smooth'});},80);}
      else frame.scrollIntoView({behavior:'smooth'});
    });
  })();
  // Same-origin iframe: make legacy/classic desk inherit the dark company shell instead of the cream break in visual language.
  var frame=document.getElementById('page');
  if(frame)frame.addEventListener('load',function(){try{
    var d=frame.contentDocument;if(!d)return;d.documentElement.style.background='#030914';d.body&&Object.assign(d.body.style,{background:'#07111f',color:'#e8eef6'});
    var style=d.createElement('style');style.textContent='body{background:#07111f!important;color:#e8eef6!important} body *{border-color:rgba(151,180,214,.17)!important}'+
      // on touch the framed page's controls (the API renders it) reach 44 px
      // like the shell's own: its header brand (18 px), Trace / Refresh
      // (31 px), its navigation links and buttons -- only raised, never
      // shrunk or hidden (device matrix, iPad landscape Archer / Scout)
      '@media (pointer:coarse){.top .brand,nav a{display:inline-flex;align-items:center;min-height:44px}button,select,summary,input[type=button],input[type=submit]{min-height:44px}}';
    d.head.appendChild(style);
  }catch(e){}});
})();
