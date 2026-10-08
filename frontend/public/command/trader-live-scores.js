/* BETTOR game display. Consumes the existing authenticated Trader snapshot only.
 * No score API calls, secrets, orders, guessed clocks, or settlement authority.
 */
(function (root) {
  'use strict';
  const SCHEMA = 'bettor.live_game.v1';
  const finite = x => typeof x === 'number' && Number.isFinite(x);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const value = n => finite(n) && Number.isInteger(n) && n >= 0 && n <= 1000 ? String(n) : '—';
  const ageText = n => !finite(n) ? 'unknown' : n < 0 ? 'invalid clock' : n < 60 ? Math.floor(n)+'s' : n < 3600 ? Math.floor(n/60)+'m' : Math.floor(n/3600)+'h';
  function safeLogo(s) {
    if (typeof s !== 'string' || s.length > 512) return null;
    try {
      const u = new URL(s);
      return u.protocol === 'https:' && u.hostname === 'a.espncdn.com' && !u.username && !u.password &&
        !u.port && !u.search && !u.hash && !s.includes('..') &&
        /^\/i\/teamlogos\/[a-zA-Z0-9_./-]+\.(png|webp|svg)$/.test(u.pathname) ? s : null;
    } catch (_) { return null; }
  }
  function isCurrent(g, now) {
    return g.status === 'CURRENT' && finite(g.expires_at) && finite(g.received_at) &&
      g.received_at <= now && now <= g.expires_at;
  }
  function stateLabel(g, now) {
    if (!isCurrent(g, now)) return 'STALE · last reported state';
    return ({IN_PROGRESS:'In progress',HALFTIME:'Halftime',INTERMISSION:'Intermission',
      SCHEDULED:'Scheduled',FINAL:'Final score · settlement separate',POSTPONED:'Postponed',
      CANCELED:'Canceled',SUSPENDED:'Suspended',DELAYED:'Delayed',
      SCORES_REPORTED:'Score reported · clock unavailable'})[g.game_status] || 'Game state unknown';
  }
  function sourceText(g, now) {
    const got = finite(g.received_at) ? ageText(now-g.received_at) : 'unknown';
    const updated = finite(g.source_at) ? 'provider update '+ageText(now-g.source_at)+' ago' : 'provider update time not supplied';
    return (g.source === 'THE_ODDS_API' ? 'The Odds API · fallback' : 'ESPN')+' · fetched '+got+' ago · '+updated;
  }
  function team(g, side) {
    const name = g[side] || (side === 'home' ? 'Home identity unavailable' : 'Away identity unavailable');
    const url = safeLogo(g[side+'_logo']);
    const abbr = g[side+'_abbreviation'] || side.toUpperCase();
    const logo = url ? `<span class="team-abbr provider-team-logo" data-provider-logo="true"><img src="${esc(url)}" alt="" width="36" height="36" loading="lazy" decoding="async" referrerpolicy="no-referrer"><span class="logo-fallback" hidden>${esc(abbr)}</span></span>` : `<span class="team-abbr">${esc(abbr)}</span>`;
    const possession = g.possession && g.possession === g[side+'_id'] ? '<span class="score-possession" title="Possession as reported" aria-label="Possession as reported">●</span>' : '';
    return `<div class="team-row ${side}">${logo}<span class="team-name">${esc(name)}${possession}</span><strong class="team-score" data-live-score-side="${side}" data-live-score-number="${esc(value(g[side+'_score']))}">${value(g[side+'_score'])}</strong></div>`;
  }
  function render(position, screen=false, now=Date.now()/1000) {
    const g = position.game || {};
    if (g.schema !== SCHEMA) return null; // Existing legacy renderer remains intact.
    if (!['CURRENT','STALE'].includes(g.status) || g.identity_verified !== true ||
        g.event_id !== position.event_id || g.venue !== position.venue) {
      return `<div class="score-unavailable live-score-unavailable"><b>${esc(position.title || 'Game')}</b><span>Live score unavailable</span><small>${esc(g.why || 'Score identity not verified')}</small><small>Market and position evidence remain separate.</small></div>`;
    }
    const current = isCurrent(g, now), key = JSON.stringify([g.venue,g.event_id,g.source]);
    const clock = g.display_clock || (finite(g.clock_seconds) ? Math.floor(g.clock_seconds/60)+':'+String(Math.floor(g.clock_seconds%60)).padStart(2,'0') : '');
    const period = g.inning ? `${g.inning_half === 'TOP' ? 'Top ' : g.inning_half === 'BOTTOM' ? 'Bottom ' : 'Inning '}${g.inning}` : g.period ? 'Period '+g.period : '';
    let detail = '';
    if (g.league === 'MLB') {
      const bases = Array.isArray(g.bases) && g.bases.length === 3 && g.bases.every(x => typeof x === 'boolean') ? `<span class="score-bases" aria-label="Bases: ${g.bases.map((b,i)=>b?i+1:null).filter(Boolean).join(', ') || 'empty'}">${g.bases.map((b,i)=>`<i class="score-base${b?' occupied':''}" title="Base ${i+1}"></i>`).join('')}</span>` : '';
      if ([g.balls,g.strikes,g.outs].some(finite)) detail = `<div class="count-strip">${bases}<span>B ${value(g.balls)}</span><span>S ${value(g.strikes)}</span><span>O ${value(g.outs)}</span></div>`;
    } else if (finite(g.down)) {
      detail = `<div class="count-strip"><span>${value(g.down)} &amp; ${value(g.distance)}</span><span>${esc(g.possession_text || '')}</span></div>`;
    }
    const attrs = `data-score-key="${esc(key)}" data-score-expires="${esc(g.expires_at)}" data-score-received="${esc(g.received_at)}" data-score-source="${esc(g.source)}" data-score-source-at="${finite(g.source_at)?g.source_at:''}"`;
    return `<div class="scoreboard live-scoreboard${current?'':' score-stale'}${screen?' score-expanded':''}" ${attrs} role="group" aria-label="Source-reported score: ${esc(g.away)} versus ${esc(g.home)}">`+
      `${team(g,'away')}${team(g,'home')}<div class="screen-game-meta"><span>${esc(period)}</span>${clock?`<span class="live-score-clock" title="Last reported clock; not interpolated">${esc(clock)} <small>reported</small></span>`:''}<span class="game-status-pill" data-score-status aria-live="polite">${esc(stateLabel(g,now))}</span></div>${detail}`+
      `<div class="score-note">${esc(g.last_play || (g.detail_level === 'SCORES_ONLY' ? 'Fallback supplies scores only; no ESPN clock or play text is mixed in.' : 'No play description supplied.'))}</div>`+
      `<div class="live-score-provenance" data-score-provenance>${esc(sourceText(g,now))}</div>${g.provider_issue?`<div class="live-score-issue">${esc(g.provider_issue)}</div>`:''}</div>`;
  }
  function install(doc) {
    if (!doc || !doc.addEventListener) return;
    let disposed=false, queued=false;
    const previous = new Map();
    function refresh() {
      queued=false;
      if (disposed || doc.hidden) return;
      const now=Date.now()/1000;
      const incoming = new Map();
      doc.querySelectorAll('.live-scoreboard').forEach(el=>{
        const expiry=Number(el.dataset.scoreExpires), received=Number(el.dataset.scoreReceived);
        const stale=!finite(expiry)||!finite(received)||received>now||now>expiry;
        if (el.classList.contains('score-stale') !== stale) el.classList.toggle('score-stale',stale);
        if (stale) {
          const label=el.querySelector('[data-score-status]');
          if(label && label.textContent!=='STALE · last reported state') label.textContent='STALE · last reported state';
        }
        const g={source:el.dataset.scoreSource,received_at:received,
          source_at:el.dataset.scoreSourceAt ? Number(el.dataset.scoreSourceAt) : null};
        const line=el.querySelector('[data-score-provenance]'),s=sourceText(g,now);
        if(line && line.textContent!==s) line.textContent=s;
        el.querySelectorAll('[data-live-score-side]').forEach(n=>{
          const key=el.dataset.scoreKey+'|'+n.dataset.liveScoreSide, v=n.dataset.liveScoreNumber;
          const old=previous.get(key);
          // Broadcast clones may lag their source DOM. Never treat an older
          // clone as a reverse score change, or flash the same observation again.
          if(!stale && old && received>old.at && old.value!==v && old.value!=='—' && v!=='—') {
            n.classList.add('score-confirmed-update');
          }
          const high=incoming.get(key)||old;
          if(finite(received) && (!high || received>high.at)) incoming.set(key,{value:v,at:received});
        });
      });
      incoming.forEach((v,k)=>{previous.delete(k);previous.set(k,v);});
      while(previous.size>512) previous.delete(previous.keys().next().value);
    }
    function schedule() {if(!queued&&!disposed){queued=true;queueMicrotask(refresh);}}
    const observer=new MutationObserver(schedule);
    observer.observe(doc.body,{childList:true,subtree:true});
    const timer=setInterval(refresh,1000);
    function visibility(){if(!doc.hidden)refresh();}
    function imageError(e){
      if(e.target?.tagName!=='IMG'||!e.target.closest('.provider-team-logo'))return;
      e.target.hidden=true;
      const fallback=e.target.parentElement.querySelector('.logo-fallback');if(fallback)fallback.hidden=false;
    }
    function animationEnd(e){if(e.animationName==='score-confirmed')e.target.classList.remove('score-confirmed-update');}
    doc.addEventListener('visibilitychange',visibility);doc.addEventListener('error',imageError,true);
    doc.addEventListener('animationend',animationEnd);
    root.addEventListener('pagehide',()=>{disposed=true;clearInterval(timer);observer.disconnect();
      doc.removeEventListener('visibilitychange',visibility);doc.removeEventListener('error',imageError,true);
      doc.removeEventListener('animationend',animationEnd);},{once:true});
    refresh();
  }
  const api={SCHEMA,render,safeLogo,isCurrent,stateLabel,sourceText};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else {root.TraderLiveScores=api;
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>install(document),{once:true});
    else install(document);
  }
})(typeof window==='undefined'?globalThis:window);
