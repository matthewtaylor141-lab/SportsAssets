(function(){
  'use strict';
  if(window.__BTHQ2Shell){return;} window.__BTHQ2Shell=true;
  var path=(location.pathname||'/').replace(/\/+$/,'')||'/';
  var page = path==='/floor'?'floor':path.indexOf('/position')===0?'positions':
    path==='/profitability'?'profitability':path==='/acceptance'?'acceptance':
    /\/(derek|xavier|audrey|karen|allocator|eddie|scout)$/.test(path)?'agents':'home';
  var names={home:'Command',floor:'Trading Floor',positions:'Position Rooms',profitability:'Profitability OS',agents:'AI Team',acceptance:'Acceptance'};
  var links=[
    ['home','/','⌂','HQ'],['floor','/floor','◈','Floor'],['positions','/positions','◎','Positions'],
    ['profitability','/profitability','↗','Economics'],['agents','/derek','◇','Agents'],['acceptance','/acceptance','✓','Accept']
  ];
  document.body.classList.add('bt-hq2');
  var shell=document.createElement('aside'); shell.className='bt-hq2-shell';
  shell.innerHTML='<a class="bt-hq2-logo" href="/" aria-label="BettorToken Command home">B</a>'+
    '<nav class="bt-hq2-nav" aria-label="Command Center">'+links.map(function(x){
      return '<a href="'+x[1]+'" class="'+(page===x[0]?'active':'')+'"><span class="bt-hq2-ico">'+x[2]+'</span><span>'+x[3]+'</span></a>';
    }).join('')+'</nav><div class="bt-hq2-shell-foot"><div class="bt-hq2-system" id="bt-hq2-system" data-state="unknown"><i></i><span>READ</span></div></div>';
  document.body.appendChild(shell);
  var bar=document.createElement('header'); bar.className='bt-hq2-pagebar';
  bar.innerHTML='<div class="bt-hq2-brandword">BETTOR <em>COMMAND</em></div><span class="sep"></span>'+
    '<div class="bt-hq2-page-title">'+(names[page]||'Command')+'</div>'+
    '<div class="bt-hq2-live" id="bt-hq2-live" data-state="unknown"><i></i><span>CONNECTING</span></div>';
  document.body.appendChild(bar);
  var tape=document.createElement('div'); tape.className='bt-hq2-market-tape stale'; tape.id='bt-hq2-tape';
  tape.innerHTML='<div class="label">THE DISCIPLINE IS THE EDGE</div><div class="rail"><span>Reading operating state…</span></div>';
  document.body.appendChild(tape);
  // Apply safe offsets only to known top-level wrappers.
  var targets=['.fl-screen','#app','.ws-tabs','#ws-root','#page','.acc','.profitability','.position-page','.institutional'];
  targets.forEach(function(s){document.querySelectorAll(s).forEach(function(n){n.classList.add('bt-hq2-content-offset');});});
  function ago(t){if(!t)return 'NO HEARTBEAT';var s=Math.max(0,Date.now()/1000-t);return s<60?Math.round(s)+'S AGO':s<3600?Math.floor(s/60)+'M AGO':Math.floor(s/3600)+'H AGO';}
  function tone(a){var s=(a&&a.state)||'';return /WORK|REVIEW|CHALLENG/.test(s)?'good':/WAIT|STALE/.test(s)?'warn':'';}
  function update(f){
    var agents=(f&&f.agents)||[], newest=0;
    agents.forEach(function(a){newest=Math.max(newest,(a.heartbeat&&a.heartbeat.at)||0);});
    var age=newest?Date.now()/1000-newest:Infinity, state=age<120?'live':age<900?'stale':'unknown';
    var sys=document.getElementById('bt-hq2-system'), live=document.getElementById('bt-hq2-live');
    if(sys)sys.dataset.state=state;
    if(live){live.dataset.state=state;live.querySelector('span').textContent=(state==='live'?'LIVE SYSTEM · ':'STALE SYSTEM · ')+ago(newest);}
    var counts=(f&&f.counts)||{};
    var edge=(f&&f.edges||[]).length;
    var rail=document.querySelector('#bt-hq2-tape .rail');
    if(rail){
      var bits=[
        '<span><b>'+agents.length+'</b> agents reporting</span>',
        '<span><b>'+((counts.WORKING_ON||0)+(counts.REVIEWING||0)+(counts.CHALLENGING||0))+'</b> active desks</span>',
        '<span><b>'+edge+'</b> collaboration links</span>'
      ];
      agents.forEach(function(a){var seat=window.BTFloor&&BTFloor.BY_AGENT&&BTFloor.BY_AGENT[a.agent];var n=seat?seat.name:(a.display_name||a.name||a.agent||'').replace('CHIEF_ALLOCATOR','Allie').replace('Chief Allocator','Allie');bits.push('<span class="'+tone(a)+'"><b>'+n+'</b> '+String(a.state||'UNKNOWN').replace(/_/g,' ')+' · '+ago(a.heartbeat&&a.heartbeat.at)+'</span>');});
      rail.innerHTML=bits.concat(bits).join('');
    }
    var t=document.getElementById('bt-hq2-tape');if(t)t.classList.toggle('stale',state!=='live');
  }
  function read(){
    fetch('/api/command/floor',{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}})
      .then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}).then(update).catch(function(){
        var live=document.getElementById('bt-hq2-live');if(live){live.dataset.state='stale';live.querySelector('span').textContent='OPERATING STATE UNAVAILABLE';}
      });
  }
  read(); setInterval(function(){if(!document.hidden)read();},15000);
})();
