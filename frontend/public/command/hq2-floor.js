(function(){
  'use strict';
  if(window.__BTHQ2Floor)return; window.__BTHQ2Floor=true;
  var B=window.BTFloor;
  if(!B){return;}
  // each agent's face: a still render of their OWN licensed 3D model
  // (team-demo/assets/models/portraits, rendered from manifest.json models)
  var FACES={derek:1,xavier:1,audrey:1,karen:1,allocator:1,eddie:1,scout:1};
  function face(slug){return FACES[slug]?'background:#0b1526 url(team-demo/assets/models/portraits/'+slug+'.jpg) center 18%/cover no-repeat':'';}
  var persona={
    derek:["DECISIVE · PROBABILITY FIRST","Rejects weak edges. Wants a crisp thesis, a measurable edge and a reason to act now."],
    karen:["SKEPTICAL · SOURCE OBSESSED","Adversarial by design. Finds the unsupported sentence, asks what is missing and refuses decorative certainty."],
    scout:["CURIOUS · PATTERN HUNTER","Research-first and exploratory. Looks for structure without promoting his own hypotheses."],
    eddie:["FAST · EXECUTION OBSESSED","Thinks in spread, depth, fees, queue, slippage and latency. Recommendation is not an order."],
    allocator:["ALLIE · PORTFOLIO FIRST","Sees the whole book: capacity, correlation, concentration, capital-hours and opportunity cost."],
    audrey:["LITERAL · LEDGER FIRST","Reconciles the record. Contradictions survive until proven resolved; missing evidence never becomes zero."],
    xavier:["CALM · LOSS AWARE","Manages what we already own. Freshness, downside and alternatives come before narrative confidence."]
  };
  var pos={derek:[10,68],karen:[23,43],scout:[39,28],eddie:[59,27],allocator:[76,40],audrey:[88,61],xavier:[70,70]};
  var state={floor:null,read:null};
  var host=document.createElement('main');host.id='bt-hq2-floor';host.className='bt-hq2-content-offset';
  host.innerHTML='<section class="hq2-floor-head"><div class="hq2-floor-title"><div class="eyebrow">BettorToken · operating headquarters</div><h1>Where the company is alive.</h1><p>Seven independent desks. One evidence chain. Every state, hand-off and collaboration below comes from recorded system activity — not decorative animation.</p></div><div class="hq2-floor-meta"><span id="hq2-fresh">READING</span><span id="hq2-edges">— COLLABORATIONS</span><span class="live">READ ONLY</span></div></section>'+
  '<section class="hq2-world"><div class="hq2-world-topline"><span>LIVE OPERATING FLOOR</span><span>THE DISCIPLINE IS THE EDGE</span></div>'+
  '<svg class="hq2-lines" id="hq2-lines" viewBox="0 0 1000 650" preserveAspectRatio="none"></svg>'+
  '<div class="hq2-core"><div><div class="k">Capital pulse</div><div class="v" id="hq2-core-v">READING</div><div class="s" id="hq2-core-s">Paper and Small Live remain separate.</div></div></div>'+
  '<div class="hq2-pods" id="hq2-pods"></div>'+
  '<div class="hq2-walls"><article class="hq2-wall"><div class="k">Opportunity radar</div><div class="big" id="hq2-opp">READING</div><div class="small" id="hq2-opp-s">Waiting for allocator evidence.</div></article>'+
  '<article class="hq2-wall"><div class="k">Company pulse</div><div class="big" id="hq2-pulse">READING</div><div class="small" id="hq2-pulse-s">Agent state is derived from heartbeats and durable work records.</div></article>'+
  '<article class="hq2-wall"><div class="k">Execution + risk</div><div class="big" id="hq2-exec">READING</div><div class="small" id="hq2-exec-s">Eddie recommendations stay separate from venue orders.</div></article></div>'+
  '<aside class="hq2-floor-drawer" id="hq2-drawer" aria-label="Agent detail"></aside></section>';
  document.body.appendChild(host);
  function esc(x){return B.esc(x)}
  function seat(slug){return B.BY_SLUG[slug]}
  function agent(slug){return state.floor&&((state.floor.agents||[]).find(function(a){return a.slug===slug;}))}
  function active(a){return a&&/WORKING_ON|REVIEWING|CHALLENGING/.test(a.state||'')}
  function pods(){
    var el=document.getElementById('hq2-pods'); if(!el)return;
    el.innerHTML=B.SEATS.map(function(s){
      var a=agent(s.slug), m=B.STATES[B.stateOf(a)], p=pos[s.slug]||[50,50], per=persona[s.slug]||['INDEPENDENT AGENT',''];
      return '<button class="hq2-pod" data-slug="'+s.slug+'" data-active="'+(active(a)?'1':'0')+'" style="--a:'+s.accent+';left:'+p[0]+'%;top:'+p[1]+'%">'+
        '<div class="presence"><span class="portrait" aria-hidden="true" style="'+face(s.slug)+'"></span><span><span class="name">'+esc(s.name)+'</span><span class="role">'+esc(s.short)+'</span></span></div>'+
        '<div class="state" style="color:'+m.color+'"><i></i>'+esc(m.label)+' · '+esc(a&&a.heartbeat&&a.heartbeat.at?B.ago(a.heartbeat.at):'NO HEARTBEAT')+'</div>'+
        '<div class="detail">'+esc(a&&a.state_detail||'No current task recorded.')+'</div><div class="persona">'+esc(per[0])+'</div></button>';
    }).join('');
    el.querySelectorAll('.hq2-pod').forEach(function(b){b.addEventListener('click',function(){openDrawer(b.dataset.slug);});});
  }
  function lines(){
    var svg=document.getElementById('hq2-lines'); if(!svg||!state.floor)return; var e=(state.floor.edges||[]).slice(-14);
    svg.innerHTML=e.map(function(x){
      var fs=B.BY_AGENT[x.from],ts=B.BY_AGENT[x.to]; if(!fs||!ts)return '';
      var a=pos[fs.slug],b=pos[ts.slug]; if(!a||!b)return '';
      var x1=a[0]*10,y1=a[1]*6.5,x2=b[0]*10,y2=b[1]*6.5,mx=(x1+x2)/2,my=Math.min(y1,y2)-35;
      return '<path class="live" d="M'+x1+','+y1+' Q'+mx+','+my+' '+x2+','+y2+'" stroke="'+fs.accent+'"><title>'+esc(B.edgeLabel(x.kind))+'</title></path>';
    }).join('');
  }
  function openDrawer(slug){
    var s=seat(slug),a=agent(slug),per=persona[slug]||['INDEPENDENT AGENT',''];var d=document.getElementById('hq2-drawer');
    if(!s||!d)return; var m=B.STATES[B.stateOf(a)];
    var monitor=(a&&a.monitor||[]).slice(0,4);
    d.innerHTML='<div class="hq2-d-head"><span class="portrait" aria-hidden="true" style="'+face(slug)+'"></span><div><div class="bt-kicker">'+esc(s.short)+'</div><h2>'+esc(s.name)+'</h2><p>'+esc(a&&a.title||s.role)+'</p></div><button class="hq2-d-x" aria-label="Close">×</button></div>'+
      '<div class="hq2-d-persona"><b>'+esc(per[0])+'</b><span>'+esc(per[1])+'</span></div>'+
      '<div class="hq2-d-grid"><div class="hq2-d-stat"><span>Current state</span><b style="color:'+m.color+'">'+esc(m.label)+'</b></div>'+
      '<div class="hq2-d-stat"><span>Heartbeat</span><b>'+esc(a&&a.heartbeat&&a.heartbeat.at?B.ago(a.heartbeat.at):'UNAVAILABLE')+'</b></div>'+
      monitor.map(function(x){return '<div class="hq2-d-stat"><span>'+esc(x.label)+'</span><b>'+esc(x.value==null?'UNAVAILABLE':x.value)+'</b></div>';}).join('')+'</div>'+
      '<div class="hq2-d-open"><a href="'+esc(a&&a.workspace||'/'+slug)+'">Enter '+esc(s.name)+'’s office →</a><button type="button" id="hq2-d-close">Stay on floor</button></div>';
    d.classList.add('open');d.querySelector('.hq2-d-x').onclick=function(){d.classList.remove('open')};d.querySelector('#hq2-d-close').onclick=function(){d.classList.remove('open')};
  }
  function walls(){
    var f=state.floor||{}, opp=(f.opportunities||[]), agents=f.agents||[];
    var working=agents.filter(active).length, stale=agents.filter(function(a){return a.state==='STALE'||a.state==='NOT_DEPLOYED'}).length;
    document.getElementById('hq2-opp').textContent=opp.length?opp.length+' ranked candidate'+(opp.length===1?'':'s'):'NO RANKING';
    document.getElementById('hq2-opp-s').textContent=opp[0]?(opp[0].market||opp[0].candidate_id||'Top candidate')+(opp[0].score!=null?' · score '+Number(opp[0].score).toFixed(4):''):'No shadow allocation ranking recorded.';
    document.getElementById('hq2-pulse').textContent=working+' ACTIVE · '+stale+' STALE';
    var ed=agent('eddie');document.getElementById('hq2-exec').textContent=ed?String(ed.state||'UNKNOWN').replace(/_/g,' '):'UNAVAILABLE';
    document.getElementById('hq2-exec-s').textContent=ed&&ed.state_detail||'Execution desk has no current state.';
    document.getElementById('hq2-edges').textContent=(f.edges||[]).length+' COLLABORATIONS';
  }
  function equity(){
    if(!window.BTEquityWall){document.getElementById('hq2-core-v').textContent='EQUITY FEED';document.getElementById('hq2-core-s').textContent='Loading recorded equity state…';return;}
    var st=window.BTEquityWall.state(),l=st&&st.live,p=l&&l.paper,sm=l&&l.small_live;
    if(p&&typeof p.equity_usd==='number'){document.getElementById('hq2-core-v').textContent=new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(p.equity_usd);}
    else document.getElementById('hq2-core-v').textContent=p&&p.status||'UNAVAILABLE';
    document.getElementById('hq2-core-s').textContent='PAPER · '+(p&&p.status||'UNAVAILABLE')+' · SMALL LIVE · '+(sm&&sm.status||'SHADOW');
  }
  function ensureEquity(){
    if(window.BTEquityWall){window.BTEquityWall.subscribe(function(){equity();});equity();return;}
    var css=document.createElement('link');css.rel='stylesheet';css.href='equity-wall.css';document.head.appendChild(css);
    var s=document.createElement('script');s.src='equity-wall.js';s.onload=function(){if(window.BTEquityWall){window.BTEquityWall.subscribe(equity);equity();}};document.body.appendChild(s);
  }
  var p=B.poller('/api/command/floor',15000,function(r){
    state.read=r.current;if(r.current.status==='OK')state.floor=r.current.data;else if(r.lastOk)state.floor=r.lastOk.data;
    var fr=document.getElementById('hq2-fresh'); if(fr)fr.textContent=r.current.status==='OK'?'READ '+B.ago(r.current.at):'STALE · '+(r.current.why||r.current.status);
    pods();lines();walls();window.dispatchEvent(new CustomEvent('bt:hq2floor:update',{detail:state.floor}));
  });
  window.addEventListener('bt:hq2floor:select',function(e){if(e.detail&&e.detail.slug)openDrawer(e.detail.slug);});
  ensureEquity();
})();
