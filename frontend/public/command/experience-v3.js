/* BETTOR EXPERIENCE V3
 * DOM-only presentation/interaction layer. It performs no network requests.
 */
(function(){
  'use strict';
  var doc=document, body=doc.body;
  if(!body) return;

  function q(s,r){return (r||doc).querySelector(s);}
  function qa(s,r){return Array.prototype.slice.call((r||doc).querySelectorAll(s));}
  function txt(el){return el?String(el.textContent||'').replace(/\s+/g,' ').trim():'';}
  function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
  function mode(){
    var p=location.pathname.toLowerCase();
    if(/trader/.test(p)) return 'trader';
    if(/floor/.test(p)||body.classList.contains('fl')) return 'floor';
    return 'command';
  }
  var page=mode();
  doc.documentElement.classList.add('bt-exp-v3');
  body.classList.add('bt-exp-v3','bt-page-'+page);
  body.setAttribute('data-experience','BETTOR_EXPERIENCE_V3');

  var routes={
    command:{href:'/command/',label:'Command'},
    floor:{href:'/floor',label:'Floor'},
    trader:{href:'/trader',label:'Trader'}
  };

  function workspaceNav(){
    if(page==='trader'){
      var existing=q('.topbar nav[aria-label="Workspace"]');
      if(existing){
        existing.classList.add('bt-v3-workspaces');
        qa('a',existing).forEach(function(a){
          var h=(a.getAttribute('href')||'').toLowerCase();
          if(page==='trader'&&/trader/.test(h)) a.setAttribute('aria-current','page');
        });
        return existing;
      }
    }
    if(page==='command'){
      var hq=q('#hq-nav');
      if(hq){
        hq.classList.add('bt-v3-workspaces');
        if(!q('[data-v3-trader]',hq)){
          var a=doc.createElement('a');
          a.href='/trader';a.textContent='Trader';a.setAttribute('data-v3-trader','');
          hq.appendChild(a);
        }
        return hq;
      }
    }
    var nav=doc.createElement('nav');
    nav.className='bt-v3-workspaces';nav.setAttribute('aria-label','BETTOR workspaces');
    Object.keys(routes).forEach(function(k){
      var r=routes[k],a=doc.createElement('a');a.href=r.href;a.textContent=r.label;
      if(k===page){a.setAttribute('aria-current','page');var d=doc.createElement('i');d.className='bt-v3-dot';d.setAttribute('aria-hidden','true');a.appendChild(d);}
      nav.appendChild(a);
    });
    var host=page==='floor'?q('.fl-top'):q('.topbar');
    if(host){
      var tools=page==='floor'?q('.fl-tools',host):q('.top-right',host);
      host.insertBefore(nav,tools||null);
    }
    return nav;
  }

  function actionButtons(){
    var box=doc.createElement('div');box.className='bt-v3-actions';
    var evidence=doc.createElement('button');evidence.className='bt-v3-action';evidence.type='button';evidence.textContent='Evidence';evidence.setAttribute('data-v3-evidence-toggle','');
    var palette=doc.createElement('button');palette.className='bt-v3-action';palette.type='button';palette.textContent='⌘K';palette.title='Open workspace palette';palette.setAttribute('data-v3-palette-toggle','');
    box.appendChild(evidence);box.appendChild(palette);
    if(page==='command'){
      var focus=doc.createElement('button');focus.className='bt-v3-action';focus.type='button';focus.textContent='Focus';focus.setAttribute('aria-pressed','false');focus.setAttribute('data-v3-focus-toggle','');box.appendChild(focus);
    }
    if(page==='trader'){
      var tape=doc.createElement('button');tape.className='bt-v3-action';tape.type='button';tape.textContent='Tape';tape.setAttribute('aria-pressed','true');tape.setAttribute('data-v3-tape-toggle','');box.appendChild(tape);
    }
    var host=page==='command'?q('#hq-top'):(page==='floor'?q('.fl-tools'):q('.top-right'));
    if(host) host.appendChild(box);
  }

  function palette(){
    var root=doc.createElement('div');root.id='bt-v3-palette';root.setAttribute('aria-hidden','true');
    root.innerHTML='<div class="bt-v3-palette-card" role="dialog" aria-modal="true" aria-label="BETTOR workspace palette">'+
      '<div class="bt-v3-palette-head"><b>BETTOR · Jump anywhere</b><span>Esc to close</span></div><div class="bt-v3-palette-list">'+
      '<a href="/command/"><b>Command</b><small>Executive state · capital · risk · readiness</small></a>'+
      '<a href="/floor"><b>Trading Floor</b><small>3D autonomous company · desks · collaboration</small></a>'+
      '<a href="/trader"><b>Trader Mode</b><small>Xavier · positions · protection · standing orders</small></a>'+
      '<a href="/command/#markets"><b>Markets</b><small>Coverage · opportunity funnel · allocator view</small></a>'+
      '<a href="/command/#capital"><b>Capital / Risk</b><small>Management epoch · exposure · concentration</small></a>'+
      '<a href="/command/#reports"><b>Reports</b><small>Management briefing · evidence · release state</small></a>'+
      '</div></div>';
    body.appendChild(root);
    root.addEventListener('click',function(e){if(e.target===root) closePalette();});
  }
  function openPalette(){body.classList.add('bt-v3-palette-open');q('#bt-v3-palette').setAttribute('aria-hidden','false');}
  function closePalette(){body.classList.remove('bt-v3-palette-open');q('#bt-v3-palette').setAttribute('aria-hidden','true');}

  function evidenceDrawer(){
    var e=doc.createElement('aside');e.id='bt-v3-evidence';e.setAttribute('aria-label','Visible evidence');
    e.innerHTML='<div class="bt-v3-evidence-head"><b>Evidence · visible state</b><button type="button" aria-label="Close evidence">×</button></div><div class="bt-v3-evidence-body"><dl class="bt-v3-evidence-grid"></dl><pre class="bt-v3-evidence-snapshot"></pre></div>';
    body.appendChild(e);q('button',e).addEventListener('click',function(){body.classList.remove('bt-v3-evidence-open');});
  }
  function evidenceSnapshot(){
    var fields=[
      ['workspace',page.toUpperCase()],['path',location.pathname+location.hash],['captured',new Date().toISOString()],
      ['mode',txt(q('.safe-badge'))||txt(q('.book-tag'))||txt(q('[data-tone="good"]'))||'visible page state'],
      ['connection',txt(q('#connection'))||txt(q('#fl-status'))||txt(q('#hq-status'))||'not labelled'],
      ['primary',txt(q('h1'))||doc.title]
    ];
    var dl=q('#bt-v3-evidence .bt-v3-evidence-grid');
    dl.innerHTML=fields.map(function(x){return '<dt>'+esc(x[0])+'</dt><dd>'+esc(x[1])+'</dd>';}).join('');
    var snippets=[];
    if(page==='command'){
      snippets.push(txt(q('#hq-alert')));snippets.push(txt(q('#hq-fresh')));snippets.push(txt(q('[data-render="capital"]')));
    }else if(page==='floor'){
      snippets.push(txt(q('#fl-counts')));snippets.push(txt(q('#fl-panel')));snippets.push(txt(q('#ws-health')));
    }else{
      snippets.push(txt(q('#metrics')));snippets.push(txt(q('#brain-rail')));snippets.push(txt(q('#cockpit-context')));
    }
    q('#bt-v3-evidence .bt-v3-evidence-snapshot').textContent=snippets.filter(Boolean).slice(0,3).join('\n\n').slice(0,4500)||'No rendered evidence is visible yet.';
  }

  function pulseTone(text){
    text=String(text||'').toUpperCase();
    if(/CRITICAL|ERROR|FAILED|MISALIGNED|STOPPED|UNAVAILABLE/.test(text)) return 'bad';
    if(/STALE|WAITING|BLOCKED|DEGRADED|PARTIAL|UNKNOWN|CONNECTING/.test(text)) return 'warn';
    return 'good';
  }
  function pulseText(){
    if(page==='command'){
      var crit=txt(q('#hq-alert .alert-txt b'));if(crit) return ['Attention',crit];
      var cap=txt(q('[data-render="capital"] .big'));if(cap) return ['Management equity',cap];
      return ['Command','Waiting for executive evidence'];
    }
    if(page==='floor'){
      var panel=txt(q('#fl-panel h2'))||txt(q('#fl-panel .fl-p-name'));if(panel) return ['Desk focus',panel];
      var s=txt(q('#fl-status'));return ['Trading floor',s||'Waiting for recorded desk state'];
    }
    var ctx=txt(q('#cockpit-context'));var conn=txt(q('#connection'));return ['Xavier',ctx||conn||'Waiting for native ledger'];
  }
  function pulse(){
    var p=doc.createElement('div');p.id='bt-v3-pulse';p.setAttribute('aria-live','polite');p.innerHTML='<i></i><b></b><span></span>';body.appendChild(p);updatePulse();
  }
  function updatePulse(){
    var p=q('#bt-v3-pulse');if(!p)return;var v=pulseText(),all=v.join(' ');p.dataset.tone=pulseTone(all);q('b',p).textContent=v[0];q('span',p).textContent=v[1];
  }

  function traderEnhancements(){
    if(page!=='trader') return;
    var toolbar=q('.workspace-toolbar');
    if(toolbar) toolbar.setAttribute('data-v3','position-management');
    var tape=q('.activity-panel');if(tape) tape.setAttribute('data-v3-secondary','decision-tape');
  }

  function wire(){
    doc.addEventListener('click',function(e){
      if(e.target.closest('[data-v3-evidence-toggle]')){e.preventDefault();evidenceSnapshot();body.classList.toggle('bt-v3-evidence-open');return;}
      if(e.target.closest('[data-v3-palette-toggle]')){e.preventDefault();openPalette();return;}
      var f=e.target.closest('[data-v3-focus-toggle]');if(f){var on=!body.classList.contains('bt-v3-focus-mode');body.classList.toggle('bt-v3-focus-mode',on);f.setAttribute('aria-pressed',String(on));return;}
      var t=e.target.closest('[data-v3-tape-toggle]');if(t){var show=body.classList.contains('bt-v3-tape-collapsed');body.classList.toggle('bt-v3-tape-collapsed',!show);t.setAttribute('aria-pressed',String(show));return;}
    });
    doc.addEventListener('keydown',function(e){
      if((e.metaKey||e.ctrlKey)&&String(e.key).toLowerCase()==='k'){e.preventDefault();openPalette();return;}
      if(e.key==='Escape'){closePalette();body.classList.remove('bt-v3-evidence-open');}
    });
    var mo=new MutationObserver(function(){updatePulse();if(body.classList.contains('bt-v3-evidence-open')) evidenceSnapshot();});
    mo.observe(body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['data-tone','class','hidden','aria-current']});
  }

  workspaceNav();actionButtons();palette();evidenceDrawer();pulse();traderEnhancements();wire();
})();
