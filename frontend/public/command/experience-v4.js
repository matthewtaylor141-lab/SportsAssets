/* BETTOR EXPERIENCE V4
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
  doc.documentElement.classList.add('bt-exp-v4');
  body.classList.add('bt-exp-v4','bt-page-'+page);
  body.setAttribute('data-experience','BETTOR_EXPERIENCE_V4');

  var routes={
    // on command.bettortoken.com Command is "/" (the host rule maps /* to /command/:splat, so "/command/" 404s)
    command:{href:'/',label:'Command'},
    floor:{href:'/floor',label:'Floor'},
    trader:{href:'/trader',label:'Trader'}
  };

  function workspaceNav(){
    // the Command Center rail (hq2-shell / hq6) marks the current workspace with a class only: make it
    // programmatically current too (the Floor had no element with aria-current at all)
    qa('.bt-hq2-nav a.active').forEach(function(a){a.setAttribute('aria-current','page');});
    if(page==='trader'){
      var existing=q('.topbar nav[aria-label="Workspace"]');
      if(existing){
        existing.classList.add('bt-v4-workspaces');
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
        hq.classList.add('bt-v4-workspaces');
        // command-polish.js already gives Command its one Trader Mode link (in this nav on desktop, docked on
        // phones): a second 'Trader' entry duplicated it and pushed the status pills off the top bar
        if(!q('[data-v4-trader]',hq)&&!q('a.bt-trader-launch,a[href="/trader"]')){
          var a=doc.createElement('a');
          a.href='/trader';a.textContent='Trader';a.setAttribute('data-v4-trader','');
          hq.appendChild(a);
        }
        return hq;
      }
    }
    var nav=doc.createElement('nav');
    nav.className='bt-v4-workspaces';nav.setAttribute('aria-label','BETTOR workspaces');
    Object.keys(routes).forEach(function(k){
      var r=routes[k],a=doc.createElement('a');a.href=r.href;a.textContent=r.label;
      if(k===page){a.setAttribute('aria-current','page');var d=doc.createElement('i');d.className='bt-v4-dot';d.setAttribute('aria-hidden','true');a.appendChild(d);}
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
    var box=doc.createElement('div');box.className='bt-v4-actions';
    var evidence=doc.createElement('button');evidence.className='bt-v4-action';evidence.type='button';evidence.textContent='Evidence';evidence.setAttribute('data-v4-evidence-toggle','');
    var palette=doc.createElement('button');palette.className='bt-v4-action';palette.type='button';palette.textContent='⌘K';palette.title='Open workspace palette';palette.setAttribute('data-v4-palette-toggle','');
    box.appendChild(evidence);box.appendChild(palette);
    if(page==='command'){
      var focus=doc.createElement('button');focus.className='bt-v4-action';focus.type='button';focus.textContent='Focus';focus.setAttribute('aria-pressed','false');focus.setAttribute('data-v4-focus-toggle','');box.appendChild(focus);
    }
    if(page==='trader'){
      var tape=doc.createElement('button');tape.className='bt-v4-action';tape.type='button';tape.textContent='Tape';tape.setAttribute('aria-pressed','true');tape.setAttribute('data-v4-tape-toggle','');box.appendChild(tape);
    }
    // Not in the crowded top bars: there they pushed the logo and the PAPER / SMALL LIVE SHADOW labels off
    // screen on phones (CI preview run 37783164445) and, at 1440 px, the SHADOW pill off the right edge
    // (production's Command bar has ~20 px to spare). Trader: the in-flow heading controls (they wrap at any
    // width). Command: in flow at the end of the HUD, shown on phones; on wider screens the palette (with
    // Evidence and Focus entries) opens with Cmd/Ctrl+K.
    var host=page==='command'?q('#hq-hud'):(page==='floor'?q('.fl-tools'):(q('.heading-controls')||q('.top-right')));
    if(page==='command') box.classList.add('bt-v4-actions--flow');
    if(host) host.appendChild(box);
  }

  function palette(){
    var root=doc.createElement('div');root.id='bt-v4-palette';root.setAttribute('aria-hidden','true');
    root.innerHTML='<div class="bt-v4-palette-card" role="dialog" aria-modal="true" aria-label="BETTOR workspace palette">'+
      '<div class="bt-v4-palette-head"><b>BETTOR · Jump anywhere</b><span>Esc to close</span></div><div class="bt-v4-palette-list">'+
      '<a href="/"><b>Command</b><small>Executive state · capital · risk · readiness</small></a>'+
      '<a href="/floor"><b>Trading Floor</b><small>3D autonomous company · desks · collaboration</small></a>'+
      '<a href="/trader"><b>Trader Mode</b><small>Xavier · positions · protection · standing orders</small></a>'+
      '<a href="/#markets"><b>Markets</b><small>Coverage · opportunity funnel · allocator view</small></a>'+
      '<a href="/#capital"><b>Capital / Risk</b><small>Management epoch · exposure · concentration</small></a>'+
      '<a href="/#reports"><b>Reports</b><small>Management briefing · evidence · release state</small></a>'+
      '<button type="button" data-v4-evidence-toggle><b>Evidence</b><small>Visible state of this page · nothing fetched</small></button>'+
      (page==='command'?'<button type="button" data-v4-focus-toggle aria-pressed="false"><b>Focus</b><small>Dim coverage, tape and funnel · capital and attention stay</small></button>':'')+
      '</div></div>';
    body.appendChild(root);
    root.addEventListener('click',function(e){if(e.target===root) closePalette();});
  }
  function openPalette(){body.classList.add('bt-v4-palette-open');q('#bt-v4-palette').setAttribute('aria-hidden','false');}
  function closePalette(){body.classList.remove('bt-v4-palette-open');q('#bt-v4-palette').setAttribute('aria-hidden','true');}

  function evidenceDrawer(){
    var e=doc.createElement('aside');e.id='bt-v4-evidence';e.setAttribute('aria-label','Visible evidence');
    e.innerHTML='<div class="bt-v4-evidence-head"><b>Evidence · visible state</b><button type="button" aria-label="Close evidence">×</button></div><div class="bt-v4-evidence-body"><dl class="bt-v4-evidence-grid"></dl><pre class="bt-v4-evidence-snapshot"></pre></div>';
    body.appendChild(e);q('button',e).addEventListener('click',function(){body.classList.remove('bt-v4-evidence-open');});
  }
  function evidenceSnapshot(){
    var fields=[
      ['workspace',page.toUpperCase()],['path',location.pathname+location.hash],['captured',new Date().toISOString()],
      ['mode',txt(q('.safe-badge'))||txt(q('.book-tag'))||txt(q('[data-tone="good"]'))||'visible page state'],
      ['connection',txt(q('#connection'))||txt(q('#fl-status'))||txt(q('#hq-status'))||'not labelled'],
      ['primary',txt(q('h1'))||doc.title]
    ];
    var dl=q('#bt-v4-evidence .bt-v4-evidence-grid');
    dl.innerHTML=fields.map(function(x){return '<dt>'+esc(x[0])+'</dt><dd>'+esc(x[1])+'</dd>';}).join('');
    var snippets=[];
    if(page==='command'){
      snippets.push(txt(q('#hq-alert')));snippets.push(txt(q('#hq-fresh')));snippets.push(txt(q('[data-render="capital"]')));
    }else if(page==='floor'){
      snippets.push(txt(q('#fl-counts')));snippets.push(txt(q('#fl-panel')));snippets.push(txt(q('#ws-health')));
    }else{
      snippets.push(txt(q('#metrics')));snippets.push(txt(q('#brain-rail')));snippets.push(txt(q('#cockpit-context')));
    }
    q('#bt-v4-evidence .bt-v4-evidence-snapshot').textContent=snippets.filter(Boolean).slice(0,3).join('\n\n').slice(0,4500)||'No rendered evidence is visible yet.';
  }

  // green only for a positive word; anything unrecognised is neutral, never a healthy green
  // (the original default painted a green dot beside a CRITICAL attention title or a FIXTURE read)
  function pulseTone(text){
    text=String(text||'').toUpperCase();
    if(/CRITICAL|ERROR|FAILED|MISALIGNED|MISMATCH|STOPPED|UNAVAILABLE|SIGN-IN REQUIRED/.test(text)) return 'bad';
    if(/STALE|WAITING|BLOCKED|DEGRADED|PARTIAL|UNKNOWN|CONNECTING|NOT CONNECTED|DISCONNECTED|NO RECENT|FIXTURE|PREVIEW|REPLAY|SYNTHETIC|NOT YET/.test(text)) return 'warn';
    if(/\bLIVE\b|\bCURRENT\b|\bCONNECTED\b|\bFRESH\b|\bOK\b/.test(text)) return 'good';
    return 'neutral';
  }
  function pulseText(){
    if(page==='command'){
      // #hq-alert renders CRITICAL attention items only: its echo is always 'bad'
      var crit=txt(q('#hq-alert .alert-txt b'));if(crit) return ['Attention',crit,'bad'];
      var cap=txt(q('[data-render="capital"] .big'));if(cap) return ['Management equity',cap];
      return ['Command','Waiting for executive evidence'];
    }
    if(page==='floor'){
      var panel=txt(q('#fl-panel h2'))||txt(q('#fl-panel .fl-p-name'));if(panel) return ['Desk focus',panel];
      var s=txt(q('#fl-status'));return ['Trading floor',s||'Waiting for recorded desk state'];
    }
    var ctx=txt(q('#cockpit-context'));var conn=txt(q('#connection'));return ['Xavier',ctx||conn||'Waiting for native ledger'];
  }
  // The Pulse never floats over content. Fixed at the bottom centre it covered the Floor dock's status lines,
  // the phone Command 'Trader Mode' launcher and the Trader position wall (CI preview run 37783164445).
  // Trader: docked in the page heading, in flow. Command and Floor already show the state it echoes
  // (the CRITICAL bar / equity card, aria-live; the floor status and hq5 Company Pulse): kept hidden there.
  function pulse(){
    var p=doc.createElement('div');p.id='bt-v4-pulse';p.setAttribute('aria-live','polite');p.innerHTML='<i></i><b></b><span></span>';
    var host=page==='trader'?q('.page-heading > div'):null;
    if(host){p.className='bt-v4-pulse-docked';host.appendChild(p);}else{p.hidden=true;body.appendChild(p);}
    updatePulse();
  }
  function updatePulse(){
    var p=q('#bt-v4-pulse');if(!p||p.hidden)return;var v=pulseText(),all=v.slice(0,2).join(' '),tone=v[2]||pulseTone(all),b=q('b',p),s=q('span',p);
    // write only real changes: an unchanged write is still a mutation the observer in wire() would see
    if(p.dataset.tone!==tone) p.dataset.tone=tone;if(b.textContent!==v[0]) b.textContent=v[0];if(s.textContent!==v[1]) s.textContent=v[1];
  }

  function traderEnhancements(){
    if(page!=='trader') return;
    var toolbar=q('.workspace-toolbar');
    if(toolbar) toolbar.setAttribute('data-v3','position-management');
    var tape=q('.activity-panel');if(tape) tape.setAttribute('data-v4-secondary','decision-tape');
  }

  function wire(){
    doc.addEventListener('click',function(e){
      if(e.target.closest('[data-v4-evidence-toggle]')){e.preventDefault();closePalette();evidenceSnapshot();body.classList.toggle('bt-v4-evidence-open');return;}
      if(e.target.closest('[data-v4-palette-toggle]')){e.preventDefault();openPalette();return;}
      var f=e.target.closest('[data-v4-focus-toggle]');if(f){var on=!body.classList.contains('bt-v4-focus-mode');body.classList.toggle('bt-v4-focus-mode',on);qa('[data-v4-focus-toggle]').forEach(function(x){x.setAttribute('aria-pressed',String(on));});closePalette();return;}
      var t=e.target.closest('[data-v4-tape-toggle]');if(t){var show=body.classList.contains('bt-v4-tape-collapsed');body.classList.toggle('bt-v4-tape-collapsed',!show);t.setAttribute('aria-pressed',String(show));return;}
    });
    doc.addEventListener('keydown',function(e){
      // where hq5-workspace.js already binds Cmd/Ctrl+K to its palette (the Floor), one palette opens, not two
      if((e.metaKey||e.ctrlKey)&&String(e.key).toLowerCase()==='k'){if(q('.hq5-palette-backdrop'))return;e.preventDefault();openPalette();return;}
      if(e.key==='Escape'){closePalette();body.classList.remove('bt-v4-evidence-open');}
    });
    // ignore V4's own Pulse / evidence writes: reacting to them re-triggers this observer forever
    // (a microtask loop that never yields, so the page never reached DOMContentLoaded)
    function own(n){for(;n;n=n.parentNode){if(n.id==='bt-v4-pulse'||n.id==='bt-v4-evidence') return true;}return false;}
    var mo=new MutationObserver(function(recs){if(recs.every(function(r){return own(r.target);})) return;updatePulse();if(body.classList.contains('bt-v4-evidence-open')) evidenceSnapshot();});
    mo.observe(body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['data-tone','class','hidden','aria-current']});
  }

  workspaceNav();actionButtons();palette();evidenceDrawer();pulse();traderEnhancements();wire();
})();


/* ─────────────────────────────────────────────────────────────────────
   V4 LIVE FLOOR + TRADER EXTENSIONS
   No direct network clients. External image requests are logo assets only.
   ───────────────────────────────────────────────────────────────────── */
(function(){
  'use strict';
  var d=document,b=d.body;if(!b)return;
  var page=b.classList.contains('bt-page-trader')?'trader':b.classList.contains('bt-page-floor')||b.classList.contains('fl')?'floor':'command';
  b.classList.add('bt-exp-v4');b.setAttribute('data-experience','BETTOR_EXPERIENCE_V4');
  function q(s,r){return (r||d).querySelector(s)}
  function qa(s,r){return Array.prototype.slice.call((r||d).querySelectorAll(s))}
  function T(e){return e?String(e.textContent||'').replace(/\s+/g,' ').trim():''}
  function norm(s){return String(s||'').toLowerCase().replace(/&/g,'and').replace(/[^a-z0-9]+/g,' ').trim()}
  var POLY_ICON='https://polymarket.com/images/brand/icon-blue.png';
  var ESPN='https://a.espncdn.com/i/teamlogos/';
  var TEAM={};
  function add(l,key,names){names.split('|').forEach(function(n){TEAM[l+':'+norm(n)]=key})}
  // Pro-league identity. College teams intentionally fall back unless identity is exact elsewhere.
  add('NFL','ari','Arizona Cardinals|Arizona|Cardinals');add('NFL','atl','Atlanta Falcons|Atlanta|Falcons');add('NFL','bal','Baltimore Ravens|Baltimore|Ravens');add('NFL','buf','Buffalo Bills|Buffalo|Bills');add('NFL','car','Carolina Panthers|Carolina|Panthers');add('NFL','chi','Chicago Bears|Chicago|Bears');add('NFL','cin','Cincinnati Bengals|Cincinnati|Bengals');add('NFL','cle','Cleveland Browns|Cleveland|Browns');add('NFL','dal','Dallas Cowboys|Dallas|Cowboys');add('NFL','den','Denver Broncos|Denver|Broncos');add('NFL','det','Detroit Lions|Detroit|Lions');add('NFL','gb','Green Bay Packers|Green Bay|Packers');add('NFL','hou','Houston Texans|Houston|Texans');add('NFL','ind','Indianapolis Colts|Indianapolis|Colts');add('NFL','jax','Jacksonville Jaguars|Jacksonville|Jaguars');add('NFL','kc','Kansas City Chiefs|Kansas City|Chiefs');add('NFL','lv','Las Vegas Raiders|Las Vegas|Raiders');add('NFL','lac','Los Angeles Chargers|LA Chargers|Chargers');add('NFL','lar','Los Angeles Rams|LA Rams|Rams');add('NFL','mia','Miami Dolphins|Miami|Dolphins');add('NFL','min','Minnesota Vikings|Minnesota|Vikings');add('NFL','ne','New England Patriots|New England|Patriots');add('NFL','no','New Orleans Saints|New Orleans|Saints');add('NFL','nyg','New York Giants|NY Giants|Giants');add('NFL','nyj','New York Jets|NY Jets|Jets');add('NFL','phi','Philadelphia Eagles|Philadelphia|Eagles');add('NFL','pit','Pittsburgh Steelers|Pittsburgh|Steelers');add('NFL','sf','San Francisco 49ers|San Francisco|49ers');add('NFL','sea','Seattle Seahawks|Seattle|Seahawks');add('NFL','tb','Tampa Bay Buccaneers|Tampa Bay|Buccaneers|Bucs');add('NFL','ten','Tennessee Titans|Tennessee|Titans');add('NFL','wsh','Washington Commanders|Washington|Commanders');
  add('MLB','ari','Arizona Diamondbacks|Diamondbacks|D-backs');add('MLB','atl','Atlanta Braves|Braves');add('MLB','bal','Baltimore Orioles|Orioles');add('MLB','bos','Boston Red Sox|Red Sox|Boston');add('MLB','chc','Chicago Cubs|Cubs');add('MLB','cws','Chicago White Sox|White Sox');add('MLB','cin','Cincinnati Reds|Reds');add('MLB','cle','Cleveland Guardians|Guardians|Cleveland');add('MLB','col','Colorado Rockies|Rockies');add('MLB','det','Detroit Tigers|Tigers');add('MLB','hou','Houston Astros|Astros');add('MLB','kc','Kansas City Royals|Royals');add('MLB','laa','Los Angeles Angels|LA Angels|Angels');add('MLB','lad','Los Angeles Dodgers|LA Dodgers|Dodgers');add('MLB','mia','Miami Marlins|Marlins');add('MLB','mil','Milwaukee Brewers|Brewers');add('MLB','min','Minnesota Twins|Twins');add('MLB','nym','New York Mets|NY Mets|Mets');add('MLB','nyy','New York Yankees|NY Yankees|Yankees');add('MLB','ath','Athletics|Oakland Athletics|A’s|A s');add('MLB','phi','Philadelphia Phillies|Phillies');add('MLB','pit','Pittsburgh Pirates|Pirates');add('MLB','sd','San Diego Padres|Padres');add('MLB','sf','San Francisco Giants|SF Giants');add('MLB','sea','Seattle Mariners|Mariners');add('MLB','stl','St. Louis Cardinals|St Louis Cardinals|Cardinals');add('MLB','tb','Tampa Bay Rays|Rays');add('MLB','tex','Texas Rangers|Rangers');add('MLB','tor','Toronto Blue Jays|Blue Jays|Toronto');add('MLB','wsh','Washington Nationals|Nationals');
  add('NBA','atl','Atlanta Hawks|Hawks');add('NBA','bos','Boston Celtics|Celtics');add('NBA','bkn','Brooklyn Nets|Nets|Brooklyn');add('NBA','cha','Charlotte Hornets|Hornets');add('NBA','chi','Chicago Bulls|Bulls');add('NBA','cle','Cleveland Cavaliers|Cavaliers|Cavs');add('NBA','dal','Dallas Mavericks|Mavericks|Mavs');add('NBA','den','Denver Nuggets|Nuggets');add('NBA','det','Detroit Pistons|Pistons');add('NBA','gs','Golden State Warriors|Warriors|Golden State');add('NBA','hou','Houston Rockets|Rockets');add('NBA','ind','Indiana Pacers|Pacers');add('NBA','lac','LA Clippers|Los Angeles Clippers|Clippers');add('NBA','lal','Los Angeles Lakers|LA Lakers|Lakers');add('NBA','mem','Memphis Grizzlies|Grizzlies');add('NBA','mia','Miami Heat|Heat');add('NBA','mil','Milwaukee Bucks|Bucks');add('NBA','min','Minnesota Timberwolves|Timberwolves|Wolves');add('NBA','no','New Orleans Pelicans|Pelicans');add('NBA','ny','New York Knicks|NY Knicks|Knicks');add('NBA','okc','Oklahoma City Thunder|Thunder|Oklahoma City');add('NBA','orl','Orlando Magic|Magic');add('NBA','phi','Philadelphia 76ers|76ers|Sixers');add('NBA','phx','Phoenix Suns|Suns');add('NBA','por','Portland Trail Blazers|Trail Blazers|Blazers');add('NBA','sac','Sacramento Kings|Kings');add('NBA','sa','San Antonio Spurs|Spurs');add('NBA','tor','Toronto Raptors|Raptors');add('NBA','utah','Utah Jazz|Jazz');add('NBA','wsh','Washington Wizards|Wizards');
  add('NHL','ana','Anaheim Ducks|Ducks');add('NHL','bos','Boston Bruins|Bruins');add('NHL','buf','Buffalo Sabres|Sabres');add('NHL','cgy','Calgary Flames|Flames');add('NHL','car','Carolina Hurricanes|Hurricanes|Canes');add('NHL','chi','Chicago Blackhawks|Blackhawks');add('NHL','col','Colorado Avalanche|Avalanche|Avs');add('NHL','cbj','Columbus Blue Jackets|Blue Jackets');add('NHL','dal','Dallas Stars|Stars');add('NHL','det','Detroit Red Wings|Red Wings');add('NHL','edm','Edmonton Oilers|Oilers');add('NHL','fla','Florida Panthers|Panthers');add('NHL','la','Los Angeles Kings|LA Kings');add('NHL','min','Minnesota Wild|Wild');add('NHL','mtl','Montreal Canadiens|Canadiens|Montreal');add('NHL','nsh','Nashville Predators|Predators|Preds');add('NHL','nj','New Jersey Devils|Devils');add('NHL','nyi','New York Islanders|NY Islanders|Islanders');add('NHL','nyr','New York Rangers|NY Rangers');add('NHL','ott','Ottawa Senators|Senators|Sens');add('NHL','phi','Philadelphia Flyers|Flyers');add('NHL','pit','Pittsburgh Penguins|Penguins|Pens');add('NHL','sj','San Jose Sharks|Sharks');add('NHL','sea','Seattle Kraken|Kraken');add('NHL','stl','St. Louis Blues|St Louis Blues|Blues');add('NHL','tb','Tampa Bay Lightning|Lightning');add('NHL','tor','Toronto Maple Leafs|Maple Leafs|Leafs');add('NHL','uta','Utah Mammoth|Utah Hockey Club|Utah');add('NHL','van','Vancouver Canucks|Canucks');add('NHL','vgk','Vegas Golden Knights|Golden Knights|Vegas');add('NHL','wsh','Washington Capitals|Capitals|Caps');add('NHL','wpg','Winnipeg Jets|Winnipeg');
  add('WNBA','atl','Atlanta Dream|Dream');add('WNBA','chi','Chicago Sky|Sky');add('WNBA','conn','Connecticut Sun|Sun');add('WNBA','dal','Dallas Wings|Wings');add('WNBA','gs','Golden State Valkyries|Valkyries');add('WNBA','ind','Indiana Fever|Fever');add('WNBA','lv','Las Vegas Aces|Aces');add('WNBA','la','Los Angeles Sparks|LA Sparks|Sparks');add('WNBA','min','Minnesota Lynx|Lynx');add('WNBA','ny','New York Liberty|NY Liberty|Liberty');add('WNBA','phx','Phoenix Mercury|Mercury');add('WNBA','sea','Seattle Storm|Storm');add('WNBA','wsh','Washington Mystics|Mystics');add('WNBA','tor','Toronto Tempo|Tempo');add('WNBA','por','Portland Fire|Portland');
  function leagueCode(s){s=String(s||'').toUpperCase();return s==='WNBA'?'wnba':s==='NFL'?'nfl':s==='NBA'?'nba':s==='MLB'?'mlb':s==='NHL'?'nhl':null}
  function logoURL(league,name){var l=String(league||'').toUpperCase(),code=leagueCode(l),key=TEAM[l+':'+norm(name)];return code&&key?ESPN+code+'/500/'+key+'.png':null}
  // a logo that failed once stays failed for this page: removing it is a mutation, and the next decorate
  // would otherwise insert it again (a request/remove loop for every unreachable or unknown logo)
  var failedLogo={};
  function img(src,cls,alt){var im=d.createElement('img');im.className=cls;im.src=src;im.alt=alt||'';im.loading='lazy';im.decoding='async';im.referrerPolicy='no-referrer';im.addEventListener('error',function(){failedLogo[src]=1;im.remove()},{once:true});return im}
  function sportOf(card){var t=T(q('.sport-tag',card)).toUpperCase();return (t.match(/^(NFL|NBA|MLB|NHL|WNBA|NCAAF|NCAAB|CFB)/)||[])[1]||''}
  function decorateScoreboards(root){qa('.scoreboard',root||d).forEach(function(sb){var card=sb.closest('.position-card')||sb.closest('.monitor-inner');if(!card)return;var league=sportOf(card)||T(q('.screen-eyebrow',card)).split(' ')[0].toUpperCase();qa('.team-row',sb).forEach(function(row){if(sb.classList.contains('live-scoreboard')||q('.bt-v4-team-logo',row))return;var name=T(q('.team-name',row)),u=logoURL(league,name);if(!u||failedLogo[u])return;var a=q('.team-abbr',row);if(a)a.parentNode.insertBefore(img(u,'bt-v4-team-logo',name+' logo'),a)})})}
  function decorateVenue(root){qa('.position-id',root||d).forEach(function(el){if(el.dataset.v4Venue)return;el.dataset.v4Venue='1';var t=T(el).toUpperCase(),w=d.createElement('span');w.className='bt-v4-venue-wrap';if(t==='PMUS'||t.indexOf('POLYMARKET')>=0){if(!failedLogo[POLY_ICON])w.appendChild(img(POLY_ICON,'bt-v4-venue-logo','Polymarket'));var s=d.createElement('span');s.textContent=t;w.appendChild(s)}else if(t.indexOf('KALSHI')>=0){var k=d.createElement('span');k.className='bt-v4-kalshi-mark';k.textContent='KALSHI';w.appendChild(k)}else{return}el.replaceChildren(w)})}
  var lastText=new WeakMap(),primed=new WeakSet();
  function markRealChanges(root){qa('.team-score,.price-block strong,.metric-value,.screen-value',root||d).forEach(function(el){var v=T(el);if(!primed.has(el)){primed.add(el);lastText.set(el,v);return}if(lastText.get(el)!==v){lastText.set(el,v);el.classList.remove('bt-v4-live-change');void el.offsetWidth;el.classList.add('bt-v4-live-change');setTimeout(function(){el.classList.remove('bt-v4-live-change')},900)}})}

  // The production Floor already has hq5's view bar (Overview / Capital Wall / Opportunities / Risk & Health /
  // Active Desk / Map) and its desk-count strip. V4's camera rail and shift board duplicated both, stacked
  // over the same corner (on iPhone over hq5's counts) and showed a second, different 'attention' count.
  // V4 defers to them where they exist and keeps its own on a floor without them.
  function floorControls(){if(page!=='floor'||q('#bt-v4-floor-command')||q('#hq5-floorbar'))return;var stage=q('#fl-stage');if(!stage)return;var wrap=d.createElement('div');wrap.id='bt-v4-floor-command';wrap.innerHTML='<section class="bt-v4-shift"><div class="bt-v4-shift-head"><span><i class="bt-v4-live-dot"></i>Live floor</span><span data-v4-shift-state>READING</span></div><div class="bt-v4-shift-kpis"><span><b data-v4-active>—</b><small>active</small></span><span><b data-v4-attn>—</b><small>attention</small></span><span><b data-v4-desks>—</b><small>desks</small></span></div></section><nav class="bt-v4-camera-bar" aria-label="Floor camera"><button data-v4-cam="overview">Overview</button><button data-v4-cam="equity">Equity wall</button><button data-v4-cam="feed">Opportunity wall</button><button data-v4-cam="health">Health wall</button><button data-v4-cam="active">Active desk</button></nav>';stage.appendChild(wrap);wrap.addEventListener('click',function(e){var btn=e.target.closest('[data-v4-cam]');if(!btn)return;var f=window.__floor,cam=btn.dataset.v4Cam;if(!f||!f.scene)return;if(cam==='overview'){f.scene.resetView();return}if(cam==='equity'||cam==='feed'||cam==='health'){f.scene.focusWall(cam);return}if(cam==='active'){var a=qa('.fl-agent').find(function(x){return /Working|Reviewing|Challenging/i.test(T(x))})||q('.fl-agent');if(a)a.click()}});updateFloorShift()}
  // write only real changes: decorate() runs on every DOM mutation, so an unconditional write here re-triggered it every frame
  function setT(el,v){v=String(v);if(el&&el.textContent!==v)el.textContent=v}
  function setD(el,k,v){if(el&&el.dataset[k]!==v)el.dataset[k]=v}
  function updateFloorShift(){if(page!=='floor')return;var agents=qa('.fl-agent'),active=0,attn=0;agents.forEach(function(a){var t=T(a);if(/Working|Reviewing|Challenging/i.test(t)){active++;setD(a,'v4Activity','active')}else if(/Stale|Blocked|Unknown|Unavailable/i.test(t)){attn++;setD(a,'v4Activity','warn')}else setD(a,'v4Activity','idle')});var sh=q('.bt-v4-shift');if(!sh)return;setT(q('[data-v4-active]',sh),active);setT(q('[data-v4-attn]',sh),attn);setT(q('[data-v4-desks]',sh),agents.length||'—');setT(q('[data-v4-shift-state]',sh),attn?'ATTENTION':agents.length?'CURRENT':'READING');setD(sh,'tone',attn?'warn':agents.length?'good':'warn')}

  function traderRail(){if(page!=='trader'||q('#bt-v4-trader-rail'))return;var rail=d.createElement('nav');rail.id='bt-v4-trader-rail';rail.setAttribute('aria-label','Trader quick lanes');rail.innerHTML='<button data-v4-filter="all">All</button><button data-v4-filter="near">Near target</button><button data-v4-filter="blocked">Needs evidence</button><button data-v4-filter="settlement">Settlement</button><button data-v4-open-broadcast>Broadcast</button>';b.appendChild(rail);placeRail();var mq=window.matchMedia&&matchMedia('(max-width:760px)');if(mq&&mq.addEventListener)mq.addEventListener('change',placeRail);else if(mq&&mq.addListener)mq.addListener(placeRail);rail.addEventListener('click',function(e){var x=e.target.closest('[data-v4-filter]');if(x){var f=q('.filters [data-filter="'+x.dataset.v4Filter+'"]');if(f)f.click();return}if(e.target.closest('[data-v4-open-broadcast]'))openBroadcast(currentCard())})}
  // Fixed at the bottom right the quick lanes covered the toolbar's search / Wall mode controls and the
  // position wall. Wider screens: docked in the (sticky) toolbar, whose own filters are the same four lanes,
  // so only Broadcast is added there. Phones (toolbar not sticky): the fixed bottom rail stays, with room
  // reserved under the page so it never covers the last cards.
  function placeRail(){var rail=q('#bt-v4-trader-rail'),tb=q('.workspace-toolbar');if(!rail)return;var phone=!!(window.matchMedia&&matchMedia('(max-width:760px)').matches),target=(!phone&&tb)?tb:b;if(rail.parentNode!==target)target.appendChild(rail);rail.classList.toggle('bt-v4-rail-docked',target===tb)}
  // the quick lanes show which lane is on (the toolbar filters carry aria-pressed; the copies did not)
  function syncRail(){qa('#bt-v4-trader-rail [data-v4-filter]').forEach(function(x){var f=q('.filters [data-filter="'+x.dataset.v4Filter+'"]'),on=String(!!(f&&f.getAttribute('aria-pressed')==='true'));if(x.getAttribute('aria-pressed')!==on)x.setAttribute('aria-pressed',on)})}
  // Stuck bars stack exactly under one another. V4's fixed offsets (metrics 68 px, ribbon 135 px, toolbar
  // 142 px) assumed a sticky 68 px top bar that Trader does not have: scrolled, the ~110 px metrics bar
  // covered the ribbon and the toolbar's filters, with page content showing above it.
  function stackSticky(){if(page!=='trader')return;var top=0;['.metrics','#bt-v4-game-ribbon','.workspace-toolbar'].forEach(function(sel){var el=q(sel);if(!el)return;if(getComputedStyle(el).position!=='sticky'){if(el.style.top)el.style.top='';return}var v=top+'px';if(el.style.top!==v)el.style.top=v;top+=el.offsetHeight})}
  // the position wall's cards only: the Broadcast clone of a card is also a .position-card, which put a
  // duplicate in the ribbon and in Prev / Next
  function currentCard(){return q('#positions .position-card.focused')||q('#positions .position-card')}
  function cards(){return qa('#positions .position-card')}
  function addBroadcastButtons(){cards().forEach(function(card){if(q('.bt-v4-broadcast-button',card))return;var actions=q('.card-actions',card);if(!actions)return;var bt=d.createElement('button');bt.type='button';bt.className='tiny-button bt-v4-broadcast-button';bt.textContent='VIEW';bt.title='Open the full-screen position broadcast (PAPER / SHADOW)';bt.setAttribute('aria-label','Open the full-screen position broadcast, PAPER / SHADOW');bt.addEventListener('click',function(e){e.stopPropagation();var f=q('.card-focus',card);if(f)f.click();setTimeout(function(){openBroadcast(card)},60)});actions.insertBefore(bt,actions.firstChild)})}
  function logoPair(card){var ims=qa('.card-score .bt-v4-team-logo',card).slice(0,2);return ims.map(function(x){return '<img src="'+x.src+'" alt="">'}).join('')}
  function ribbonState(card){var t=T(q('.gap-caption b',card));if(/reached/i.test(t))return['TOUCH','good'];if(/Needs|evidence|stale/i.test(t))return['EVIDENCE','warn'];if(/Settlement/i.test(T(card)))return['SETTLE','warn'];return['MANAGE','good']}
  function ribbonClick(e){var btn=e.target.closest('[data-v4-ribbon]');if(!btn)return;var cs=cards(),card=cs.filter(function(c){return (c.dataset.id||'').replace(/"/g,'')===btn.dataset.v4Ribbon})[0]||cs[Number(btn.dataset.v4Index)];if(!card)return;var f=q('.card-focus',card);if(f)f.click();card.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'center'});setTimeout(updateRibbon,40)}
  function updateRibbon(){if(page!=='trader')return;var host=q('#bt-v4-game-ribbon');if(!host){host=d.createElement('nav');host.id='bt-v4-game-ribbon';host.setAttribute('aria-label','Open position ribbon');var tb=q('.workspace-toolbar');if(tb)tb.parentNode.insertBefore(host,tb);else return;host.addEventListener('click',ribbonClick)}var cs=cards(),focused=q('#positions .position-card.focused'),total=T(q('#wall-count')),lane=q('.filters [aria-pressed="true"]'),laneName=lane?T(lane):'All',query=(q('#search')||{}).value||'';
    // a lane, a search or a truncated response narrows the wall; the ribbon says so and keeps the total in
    // view (never a silent subset of the holdings)
    var narrowed=(lane&&lane.dataset.filter!=='all')||!!query||(/^\d+$/.test(total)&&Number(total)!==cs.length);
    var head='<span class="bt-v4-ribbon-count'+(narrowed?' narrowed':'')+'" role="status">'+(narrowed?cs.length+' of '+total.replace(/</g,'&lt;')+' shown · '+laneName.replace(/</g,'&lt;')+(query?' · search':''):(total||cs.length)+' positions')+'</span>';
    var html=head+cs.map(function(card,i){var title=T(q('.market-title',card))||T(q('.card-score',card)),st=ribbonState(card),id=card.dataset.id||String(i);return '<button class="bt-v4-ribbon-item" data-v4-ribbon="'+id.replace(/"/g,'')+'" data-v4-index="'+i+'"'+(card===focused?' aria-current="true"':'')+'><span class="bt-v4-ribbon-logos">'+logoPair(card)+'</span><span class="bt-v4-ribbon-main"><b>'+title.replace(/</g,'&lt;')+'</b><small>'+T(q('.holding-line',card)).replace(/</g,'&lt;')+'</small></span><span class="bt-v4-ribbon-state '+(st[1]==='warn'?'warn':'')+'">'+st[0]+'</span></button>'}).join('');if(host.__v4Html!==html){host.innerHTML=html;host.__v4Html=html}}
  function broadcastShell(){if(q('#bt-v4-broadcast'))return;var el=d.createElement('section');el.id='bt-v4-broadcast';el.setAttribute('aria-label','Live position broadcast');el.innerHTML='<div class="bt-v4-broadcast-shell"><header class="bt-v4-broadcast-top"><div class="bt-v4-brand"><img src="brand/bettortoken-logo-white.png" alt="BettorToken">POSITION BROADCAST · PAPER / SHADOW</div><span class="bt-v4-broadcast-asof" data-v4-b-asof role="status"></span><button data-v4-prev>← Prev</button><button data-v4-next>Next →</button><button data-v4-close>Close</button></header><div class="bt-v4-broadcast-score"><section class="bt-v4-broadcast-pane" data-v4-b-game></section><section class="bt-v4-broadcast-pane center" data-v4-b-market></section><section class="bt-v4-broadcast-pane" data-v4-b-decision></section></div><div class="bt-v4-broadcast-card"><section class="bt-v4-broadcast-position" data-v4-b-position></section><aside class="bt-v4-broadcast-tape" data-v4-b-tape></aside></div></div>';b.appendChild(el);el.addEventListener('click',function(e){if(e.target.closest('[data-v4-close]'))closeBroadcast();if(e.target.closest('[data-v4-prev]'))cycleBroadcast(-1);if(e.target.closest('[data-v4-next]'))cycleBroadcast(1)})}
  var broadcastIndex=0,broadcastId=null,broadcastSig='';
  // a clone carries no ids (they duplicated #focus-game, #activity ... in the document)
  function cloneInto(sel,node){var h=q(sel);if(!h)return;h.innerHTML='';if(node){var c=node.cloneNode(true);c.removeAttribute&&c.removeAttribute('id');qa('[id]',c).forEach(function(x){x.removeAttribute('id')});h.appendChild(c)}}
  function cardById(id){return cards().filter(function(c){return c.dataset.id===id})[0]||null}
  function broadcastSources(card){return [card,q('#focus-game'),q('#focus-market'),q('#focus-decision'),q('.activity-panel')]}
  function stampBroadcast(text,stale){var el=q('[data-v4-b-asof]');if(!el)return;if(el.textContent!==text)el.textContent=text;var bc=q('#bt-v4-broadcast');if(bc&&bc.classList.contains('bt-v4-broadcast-stale')!==!!stale)bc.classList.toggle('bt-v4-broadcast-stale',!!stale)}
  // Broadcast is a copy of rendered DOM. While it is open it is re-copied whenever the page re-renders that
  // position (trader.js rebuilds the wall on every poll, so the copied card element is soon detached), and
  // it is labelled STALE when the position is no longer rendered or the desk panes show another position.
  function refreshBroadcast(force){if(!b.classList.contains('bt-v4-broadcast-open')||!broadcastId)return;var card=cardById(broadcastId);
    if(!card){stampBroadcast('STALE · this position is no longer on the wall (closed, or outside the current lane) · last copy shown',true);return}
    var f=q('#positions .position-card.focused'),panes=!!(f&&f.dataset.id===broadcastId),src=broadcastSources(card),sig=src.map(function(n,i){return (i>0&&i<4&&!panes)?'':(n?n.innerHTML:'')}).join('\u0001');
    if(!force&&sig===broadcastSig)return;broadcastSig=sig;
    if(panes){cloneInto('[data-v4-b-game]',src[1]);cloneInto('[data-v4-b-market]',src[2]);cloneInto('[data-v4-b-decision]',src[3])}
    cloneInto('[data-v4-b-position]',card);cloneInto('[data-v4-b-tape]',src[4]);decorateScoreboards(q('#bt-v4-broadcast'));decorateVenue(q('#bt-v4-broadcast'));
    stampBroadcast(panes?'Copied from the rendered page at '+new Date().toLocaleTimeString('en-GB',{hour12:false})+' · re-copied when it re-renders':'STALE desk panes · the desk now shows another position · card and tape current',!panes)}
  function openBroadcast(card){if(page!=='trader'||!card)return;broadcastShell();var cs=cards();broadcastIndex=Math.max(0,cs.indexOf(card));broadcastId=card.dataset.id||null;var f=q('.card-focus',card);if(f)f.click();setTimeout(function(){b.classList.add('bt-v4-broadcast-open');refreshBroadcast(true)},70)}
  function closeBroadcast(){b.classList.remove('bt-v4-broadcast-open');broadcastId=null;broadcastSig=''}
  function cycleBroadcast(dir){var cs=cards();if(!cs.length)return;broadcastIndex=(broadcastIndex+dir+cs.length)%cs.length;openBroadcast(cs[broadcastIndex])}

  function decorate(){decorateScoreboards();decorateVenue();markRealChanges();if(page==='floor')updateFloorShift();if(page==='trader'){addBroadcastButtons();updateRibbon();syncRail();stackSticky();refreshBroadcast(false)}}
  floorControls();traderRail();broadcastShell();decorate();if(page==='trader')addEventListener('resize',stackSticky,{passive:true});
  // V4's own surfaces are not page evidence: a batch made entirely inside them must not schedule another decorate
  var OWN={'bt-v4-game-ribbon':1,'bt-v4-broadcast':1,'bt-v4-pulse':1,'bt-v4-evidence':1,'bt-v4-palette':1};
  function own(n){for(;n&&n!==b;n=n.parentNode){if(n.id&&OWN[n.id])return true}return false}
  var scheduled=false,mo=new MutationObserver(function(recs){if(scheduled||recs.every(function(r){return own(r.target)}))return;scheduled=true;requestAnimationFrame(function(){scheduled=false;decorate()})});mo.observe(b,{childList:true,subtree:true,characterData:true});
  d.addEventListener('keydown',function(e){if(e.key==='Escape'&&b.classList.contains('bt-v4-broadcast-open')){closeBroadcast();return}if(page==='trader'&&!/INPUT|TEXTAREA|SELECT/.test((e.target&&e.target.tagName)||'')){if(String(e.key).toLowerCase()==='b'){e.preventDefault();openBroadcast(currentCard())}if(e.key==='ArrowRight'&&b.classList.contains('bt-v4-broadcast-open'))cycleBroadcast(1);if(e.key==='ArrowLeft'&&b.classList.contains('bt-v4-broadcast-open'))cycleBroadcast(-1)}})
})();
