/* BETTOR HEADQUARTERS · the trading floor controller.
 * Reads GET /api/command/floor every 15 s (paused while hidden), hands it to
 * the 3D scene (floor-scene.js) or, without WebGL, to the 2D floor map, and
 * draws the side panel, roster, tooltips and the central wall screens.
 * Central wall: Derek's latest decisions + the allocator's SHADOW ranking
 * (from the floor read), the equity wall component (equity-wall.js, loaded
 * only if it exists -- otherwise a labelled placeholder, never a number) and
 * system health (/api/command/coverage, /api/command/p5/evidence). */
const B = window.BTFloor;
const $ = (s, r) => (r || document).querySelector(s);
const esc = B.esc;
const params = new URLSearchParams(location.search);
const REDUCED = B.reducedMotion();
const PHONE = matchMedia('(max-width: 760px)').matches || matchMedia('(pointer: coarse) and (max-width: 1024px)').matches;
const POLL_MS = 15000, AUX_MS = 60000;

const state = {floor: null, read: null, lastOk: null, selected: null, view: '3d', scene: null,
               coverage: null, p5: null, equity: 'loading', equityHandle: null, sceneFailed: null};

/* ── header ───────────────────────────────────────────────────────── */
function statusPill() {
  const r = state.read, ok = state.lastOk, now = Date.now() / 1000;
  let tone = 'off', text = 'Connecting…', link = '';
  if (r && r.status === 'OK') { tone = 'live'; text = (B.isFixture(r.data) ? 'FIXTURE' : 'LIVE') + ' · read ' + B.ago(r.at, now); }
  else if (r && ok) { tone = 'stale'; text = 'STALE READ · last good ' + B.ago(ok.at, now) + ' · ' + (r.status === 'SIGNED_OUT' ? 'sign-in required' : r.why); }
  else if (r && r.status === 'SIGNED_OUT') { tone = 'warn'; text = 'SIGN-IN REQUIRED · no state shown'; link = '<a href="/">Sign in to COMMAND →</a>'; }
  else if (r && r.status === 'NOT_RELEASED') { tone = 'warn'; text = 'NOT YET RELEASED · the floor read ships with the next API release'; }
  else if (r) { tone = 'warn'; text = 'UNAVAILABLE · ' + r.why; }
  $('#fl-status').innerHTML = '<span class="fl-pill" data-tone="' + tone + '" title="Source: GET /api/command/floor"><i></i><span>' + esc(text) + '</span></span>' + link;
  const f = state.floor;
  $('#fl-counts').innerHTML = f ? ['WORKING_ON', 'REVIEWING', 'CHALLENGING', 'WAITING', 'IDLE', 'STALE', 'NOT_DEPLOYED']
    .filter((k) => (f.counts || {})[k]).map((k) => '<span class="fl-count" style="--c:' + B.STATES[k].color + '" title="' + esc(B.STATES[k].label) + '"><i></i>' + f.counts[k] + ' ' + esc(B.STATES[k].label.toLowerCase()) + '</span>').join('') +
    '<span class="fl-count edges" title="Collaboration edges recorded in the last ' + Math.round((f.window_s || 3600) / 60) + ' minutes">' + (f.edges || []).length + ' collaboration link' + ((f.edges || []).length === 1 ? '' : 's') + ' · ' + Math.round((f.window_s || 3600) / 60) + 'm</span>' : '';
  $('#fl-fixture').hidden = !(f && B.isFixture(f));
}

/* ── the agents ───────────────────────────────────────────────────── */
function agentOf(slug) { return state.floor ? (state.floor.agents || []).find((a) => a.slug === slug) || null : null; }
function chip(a, short) {
  const st = B.stateOf(a), m = B.STATES[st];
  return '<span class="fl-chip" style="--c:' + m.color + '" title="' + esc(m.label) + '"><i></i>' + esc(short && m.short ? m.short : m.label) + '</span>';
}
function hb(a) {
  if (!a) return '—';
  if (!a.heartbeat || a.heartbeat.at == null) return 'no heartbeat';
  return B.ago(a.heartbeat.at);
}
function roster() {
  $('#roster').innerHTML = B.SEATS.map((s) => {
    const a = agentOf(s.slug);
    return '<button type="button" class="fl-agent' + (state.selected === s.slug ? ' on' : '') + '" data-slug="' + s.slug + '" style="--a:' + s.accent + '" aria-pressed="' + (state.selected === s.slug) + '">' +
      '<span class="fl-mono" data-agent="' + esc(s.slug) + '" aria-hidden="true">' + esc(s.initial) + '</span><span class="fl-agent-txt"><b>' + esc(s.name) + '</b><small>' + esc(s.short) + '</small>' +
      '<span class="fl-agent-st">' + (a ? chip(a, true) : '<span class="fl-chip" style="--c:#4a5566"><i></i>' + (state.read ? 'No data' : '…') + '</span>') + '<small title="Heartbeat age">♥ ' + esc(hb(a)) + '</small></span></span></button>';
  }).join('');
}
function metricRow(m) {
  const now = Date.now() / 1000;
  return '<div class="fl-metric"><span>' + esc(m.label) + '</span><strong class="' + (m.value == null ? 'na' : String(m.value).length > 12 ? 'long' : '') + '">' + esc(m.value == null ? 'UNAVAILABLE' : m.value) + '</strong>' +
    '<small>' + esc(m.source) + (m.as_of ? ' · ' + esc(B.ago(m.as_of, now)) : '') + (m.value == null && m.why ? ' · ' + esc(m.why) : '') + '</small></div>';
}
function evidence(ref) {
  if (!ref) return '';
  const id = esc(ref.kind) + ' <code>' + esc(ref.id) + '</code>';
  return ref.href ? '<a href="' + esc(ref.href) + '" target="_blank" rel="noopener" title="Open the source record (JSON)">' + id + ' ↗</a>' : '<span>' + id + '</span>';
}
function basisText(b) {
  if (!b) return '';
  if (b.id) return 'basis ' + evidence(b);
  return 'basis ' + esc(b.kind) + (b.why ? ' (' + esc(b.why) + ')' : '') + (b.at ? ' @ ' + esc(B.clock(b.at)) : '');
}
function panel(slug) {
  const s = B.BY_SLUG[slug], a = agentOf(slug), el = $('#fl-panel');
  if (!s) { el.hidden = true; return; }
  const now = Date.now() / 1000;
  const edges = state.floor ? (state.floor.edges || []).filter((e) => a && (e.from === a.agent || e.to === a.agent)) : [];
  const href = a && a.workspace || '/' + slug;
  el.innerHTML = '<header class="fl-p-head" style="--a:' + s.accent + '"><span class="fl-mono big" data-agent="' + esc(s.slug) + '" aria-hidden="true">' + esc(s.initial) + '</span><div><span class="fl-eyebrow">' + esc(s.short) + '</span><h2>' + esc(s.name) + '</h2><p>' + esc(a ? a.title : s.role) + '</p></div>' +
    '<button type="button" class="fl-x" data-close aria-label="Close the agent summary">×</button></header>' +
    (!a ? '<p class="fl-p-empty">' + esc(state.read ? (state.read.why || 'No state recorded for this agent in the latest read.') : 'Waiting for the first floor read…') + '</p>' :
    '<section class="fl-p-now">' + chip(a) + '<p>' + esc(a.state_detail || '') + '</p><small>' + (a.state_since ? 'since ' + esc(B.ago(a.state_since, now)) + ' · ' : '') +
      basisText(a.state_basis && a.state_basis[0]) + '</small></section>' +
    '<section class="fl-p-hb"><div><span class="fl-eyebrow">Heartbeat</span><strong>' + esc(a.heartbeat && a.heartbeat.at != null ? B.age(now - a.heartbeat.at).trim() + ' ago' : 'none recorded') + '</strong>' +
      '<small>' + esc(a.heartbeat && a.heartbeat.at != null ? B.clock(a.heartbeat.at) : '') + ' · stale after ' + esc(B.age(a.heartbeat && a.heartbeat.stale_after_s).trim()) + '</small><small>' + esc(a.heartbeat && a.heartbeat.source || '') + '</small></div>' +
      '<div><span class="fl-eyebrow">Authority</span><strong class="auth">' + esc((a.authority && a.authority.level || '').replace(/_/g, ' ').toLowerCase()) + '</strong></div></section>' +
    (a.monitor && a.monitor.length ? '<section class="fl-p-metrics">' + a.monitor.map(metricRow).join('') + '</section>' : '') +
    (a.focus || a.last_output ? '<section class="fl-p-sec"><span class="fl-eyebrow">Current focus / last output</span><p>' + esc((a.last_output || a.focus).summary || '') + '</p><small>' + evidence(a.last_output || a.focus) + ((a.last_output || a.focus).at ? ' · ' + esc(B.ago((a.last_output || a.focus).at, now)) : '') + '</small></section>' : '') +
    '<section class="fl-p-sec"><span class="fl-eyebrow">Challenges</span><p>' +
      (a.challenges && a.challenges.raised_open != null ? esc(a.challenges.raised_open) + ' open challenge(s) raised by Karen' :
       a.challenges && a.challenges.open_against != null ? esc(a.challenges.open_against) + ' open challenge(s) against ' + esc(s.name) : 'Not applicable / unavailable') + '</p></section>' +
    '<section class="fl-p-sec"><span class="fl-eyebrow">Collaboration · last ' + Math.round(((state.floor || {}).window_s || 3600) / 60) + ' min</span>' +
      (edges.length ? '<ul class="fl-edges">' + edges.map((e) => '<li><b>' + esc((B.BY_AGENT[e.from] || {}).name || e.from) + ' → ' + esc((B.BY_AGENT[e.to] || {}).name || e.to) + '</b> ' + esc(B.edgeLabel(e.kind)) + (e.count > 1 ? ' ×' + e.count : '') +
        '<small>' + esc(e.summary || '') + ' · ' + esc(B.ago(e.at, now)) + '</small><small>' + (e.evidence || []).slice(0, 3).map(evidence).join(' · ') + '</small></li>').join('') + '</ul>' : '<p class="muted">No recorded collaboration in the window.</p>') + '</section>' +
    '<details class="fl-p-sec"><summary>Mandate boundary — what ' + esc(s.name) + ' may and may not do</summary><div class="fl-mayg"><div><b>May</b><ul>' + (a.authority.may || []).map((x) => '<li>' + esc(x) + '</li>').join('') + '</ul></div><div><b>May not</b><ul>' + (a.authority.may_not || []).map((x) => '<li>' + esc(x) + '</li>').join('') + '</ul></div></div></details>') +
    '<footer class="fl-p-foot"><a class="fl-btn primary" href="' + esc(href) + '">Open ' + esc(s.name) + '’s workspace →</a><button type="button" class="fl-btn" data-close>Back to the floor</button></footer>';
  el.hidden = false;
}
function select(slug, opts) {
  opts = opts || {};
  if (slug && opts.again) { const a = agentOf(slug); location.href = (a && a.workspace) || '/' + slug; return; }
  state.selected = slug || null;
  roster();
  document.body.classList.remove('wall-focus');
  if (!slug) { $('#fl-panel').hidden = true; if (state.scene) state.scene.resetView(); document.body.classList.remove('focused'); renderMap(); return; }
  panel(slug);
  document.body.classList.add('focused');
  if (state.scene && state.view === '3d') { state.scene.setInsetRight(PHONE ? 0 : $('#fl-panel').offsetWidth + 16); state.scene.focus(slug); }
  renderMap();
  if (opts.fromKeyboard) $('#fl-panel').focus();
}

/* ── tooltip ──────────────────────────────────────────────────────── */
function tip(slug, x, y) {
  const el = $('#fl-tip');
  if (!slug) { el.hidden = true; return; }
  const s = B.BY_SLUG[slug], a = agentOf(slug), r = $('#fl-stage').getBoundingClientRect();
  el.innerHTML = '<b style="color:' + s.accent + '">' + esc(s.name) + '</b><span>' + esc(a ? a.title : s.role) + '</span>' + (a ? chip(a) + '<small>' + esc(a.state_detail || '') + '</small>' : '<small>No state yet</small>') + '<em>Click to focus · click again for the workspace</em>';
  el.hidden = false;
  el.style.left = Math.min(r.width - 280, Math.max(8, x - r.left + 14)) + 'px';
  el.style.top = Math.max(8, y - r.top + 14) + 'px';
}

/* ── the central wall ─────────────────────────────────────────────── */
function feedHtml() {
  const f = state.floor, now = Date.now() / 1000;
  if (!f) return '<header><span class="ws-eyebrow">Market · opportunity feed</span></header><p class="ws-empty">' + esc(state.read ? state.read.why || 'No floor read.' : 'Waiting for the first read…') + '</p>';
  const opp = f.opportunities || [], feed = f.feed || [];
  const sec = (f.sections || {})['opportunities.intel_allocations'] || {};
  return '<header><span class="ws-eyebrow">Market · opportunity feed</span><span class="ws-tag">PAPER · SHADOW</span></header>' +
    '<h3>Allie · allocator ranking <small>shadow sleeve · ' + (opp[0] ? esc(B.ago(opp[0].at, now)) : esc(sec.why || 'no run')) + '</small></h3>' +
    (opp.length ? '<ol class="ws-rows">' + opp.slice(0, 4).map((o) => '<li><span class="ws-rank">' + esc(o.rank) + '</span><span class="ws-mkt">' + esc(o.market || o.candidate_id || o.id) + '<small>' + esc(o.candidate_kind === 'OPEN_POSITION' ? 'open position' : 'new decision') + (o.binding_constraint ? ' · ' + esc(o.binding_constraint) : '') + '</small></span><span class="ws-num">' + (o.shadow_usd != null ? '$' + Number(o.shadow_usd).toFixed(2) : '—') + '<small>' + (o.score != null ? 'score ' + Number(o.score).toFixed(4) : 'no score') + '</small></span></li>').join('') + '</ol>' : '<p class="ws-empty">No shadow ranking recorded' + (sec.why ? ' (' + esc(sec.why) + ')' : '') + '.</p>') +
    '<h3>Derek’s latest decisions <small>paper_decisions</small></h3>' +
    (feed.length ? '<ol class="ws-rows">' + feed.slice(0, 5).map((d) => '<li><span class="ws-verdict ' + (d.verdict === 'ENTER' ? 'enter' : 'refuse') + '">' + esc(d.verdict) + '</span><span class="ws-mkt">' + esc(d.market || d.fixture || d.id) + '<small>' + esc(d.side || '') + (d.refusal ? ' · ' + esc(d.refusal) : '') + '</small></span><span class="ws-num">' + (d.limit_price != null ? '$' + Number(d.limit_price).toFixed(2) : '') + '<small>' + esc(B.ago(d.at, now)) + '</small></span></li>').join('') + '</ol>' : '<p class="ws-empty">No paper decision recorded.</p>');
}
function healthHtml() {
  const f = state.floor, now = Date.now() / 1000, cov = state.coverage, p5 = state.p5;
  let covHtml = '<p class="ws-empty">Coverage: ' + esc(cov ? cov.why || cov.status : 'reading…') + '</p>';
  if (cov && cov.status === 'OK') {
    const ls = cov.data && cov.data.league_status;
    if (ls && ls.summary) covHtml = '<div class="ws-chips">' + Object.keys(ls.summary).filter((k) => ls.summary[k]).map((k) => '<span class="ws-chip ' + (k === 'HEALTHY' ? 'ok' : k === 'COVERAGE_INCIDENT' || k === 'UNAVAILABLE' ? 'bad' : 'mid') + '">' + esc(k.replace(/_/g, ' ').toLowerCase()) + ' <b>' + ls.summary[k] + '</b></span>').join('') + '</div><small class="ws-src">/api/command/coverage · league status ' + esc(ls.day || '') + ' · read ' + esc(B.ago(cov.at, now)) + '</small>';
    else covHtml = '<p class="ws-empty">Coverage league status: ' + esc((ls && (ls.why || ls.status)) || 'UNAVAILABLE') + '</p>';
  }
  let p5Html = '<p class="ws-empty">P5: ' + esc(p5 ? p5.why || p5.status : 'reading…') + '</p>';
  if (p5 && p5.status === 'OK' && p5.data) {
    const d = p5.data, fb = d.first_blocking;
    p5Html = '<div class="ws-p5"><span class="ws-chip ' + (d.verdict === 'LIVE_ADMISSIBLE' ? 'ok' : 'bad') + '">P5 · ' + esc(d.verdict || 'UNKNOWN') + '</span><small>' + esc((d.proven || []).length + ' proven · ' + (d.not_proven || []).length + ' not proven') + '</small></div>' +
      (fb ? '<small class="ws-src">First blocker: <b>' + esc(fb.predicate) + '</b> — ' + esc(String(fb.reason || '').slice(0, 120)) + '</small>' : '') + '<small class="ws-src">/api/command/p5/evidence · read ' + esc(B.ago(p5.at, now)) + '</small>';
  }
  return '<header><span class="ws-eyebrow">System health</span><span class="ws-tag">LIVE READS</span></header>' +
    '<div class="ws-hb">' + B.SEATS.map((s) => { const a = agentOf(s.slug), m = B.STATES[B.stateOf(a)]; return '<span style="--c:' + m.color + '"><i></i>' + esc(s.name) + '<small>' + esc(a ? (a.state === 'NOT_DEPLOYED' ? 'not deployed' : a.heartbeat && a.heartbeat.at != null ? B.age(now - a.heartbeat.at).trim() : 'no beat') : '—') + '</small></span>'; }).join('') + '</div>' +
    '<h3>Coverage</h3>' + covHtml + '<h3>Live admission</h3>' + p5Html;
}
function equityPlaceholder() {
  return '<header><span class="ws-eyebrow">Central wall · equity</span><span class="ws-tag">' + (state.equity === 'loading' ? 'LOADING' : 'INTEGRATING') + '</span></header>' +
    '<div class="ws-eq-ph"><div><b>$500,000 PAPER EXPERIMENT</b><span>Equity ticker slot</span></div><div><b>SMALL LIVE — BETTOR ORIGINATED</b><span>its own status · the legacy mirror (Polymarket US · Kalshi) below, each venue on its own</span></div></div>' +
    '<p class="ws-empty">' + (state.equity === 'loading' ? 'Loading the equity wall component…' : 'The live equity wall component is being integrated on this floor. No figure is shown until it is: this slot never displays a number of its own.') + '</p>';
}
function drawWall() {
  const host = state.view === '3d' && !PHONE && state.scene ? $('#fl-wall') : $('#fl-wallflow');
  const feedEl = $('#ws-feed'), healthEl = $('#ws-health'), eqEl = $('#ws-equity');
  if (feedEl.parentNode !== host) { host.appendChild(feedEl); host.appendChild(eqEl); host.appendChild(healthEl); }
  document.body.classList.toggle('wall-projected', host.id === 'fl-wall');
  feedEl.innerHTML = feedHtml();
  healthEl.innerHTML = healthHtml();
  if (state.equity !== 'mounted') eqEl.innerHTML = equityPlaceholder();
}
function loadEquity() {
  // GUARDED: the equity stream ships equity-wall.js; until it does the
  // request 404s and the slot stays a labelled placeholder.
  const s = document.createElement('script'); s.src = 'equity-wall.js'; s.async = true;
  s.onload = () => {
    const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = 'equity-wall.css'; document.head.appendChild(css);
    if (window.BTEquityWall && typeof window.BTEquityWall.mount === 'function') {
      try { state.equityHandle = window.BTEquityWall.mount($('#ws-equity'), {compact: true}); state.equity = 'mounted'; $('#ws-equity').classList.add('mounted'); }
      catch (e) { state.equity = 'absent'; }
    } else state.equity = 'absent';
    drawWall();
  };
  s.onerror = () => { state.equity = 'absent'; drawWall(); };
  document.head.appendChild(s);
}

/* project the DOM wall screens onto the 3D wall (CSS homography) */
function adj(m) { return [m[4] * m[8] - m[5] * m[7], m[2] * m[7] - m[1] * m[8], m[1] * m[5] - m[2] * m[4], m[5] * m[6] - m[3] * m[8], m[0] * m[8] - m[2] * m[6], m[2] * m[3] - m[0] * m[5], m[3] * m[7] - m[4] * m[6], m[1] * m[6] - m[0] * m[7], m[0] * m[4] - m[1] * m[3]]; }
function mm(a, b) { const c = []; for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) { let s = 0; for (let k = 0; k < 3; k++) s += a[3 * i + k] * b[3 * k + j]; c[3 * i + j] = s; } return c; }
function mv(m, v) { return [m[0] * v[0] + m[1] * v[1] + m[2] * v[2], m[3] * v[0] + m[4] * v[1] + m[5] * v[2], m[6] * v[0] + m[7] * v[1] + m[8] * v[2]]; }
function basis(p) { const m = [p[0].x, p[1].x, p[2].x, p[0].y, p[1].y, p[2].y, 1, 1, 1]; const v = mv(adj(m), [p[3].x, p[3].y, 1]); return mm(m, [v[0], 0, 0, 0, v[1], 0, 0, 0, v[2]]); }
function project(el, quad) {
  const w = el.offsetWidth, h = el.offsetHeight;
  if (!w || !h || quad.some((p) => p.behind)) { el.style.visibility = 'hidden'; return; }
  const src = [{x: 0, y: 0}, {x: w, y: 0}, {x: w, y: h}, {x: 0, y: h}];
  const t = mm(basis(quad), adj(basis(src)));
  const n = t.map((x) => x / t[8]);
  el.style.visibility = '';
  el.style.transform = 'matrix3d(' + [n[0], n[3], 0, n[6], n[1], n[4], 0, n[7], 0, 0, 1, 0, n[2], n[5], 0, n[8]].join(',') + ')';
}
function onFrame(f) {
  if (!f.cameraDirty || !document.body.classList.contains('wall-projected')) return;
  const q = state.scene.wallQuads();
  project($('#ws-feed'), q.feed); project($('#ws-equity'), q.equity); project($('#ws-health'), q.health);
}

/* ── the 2D floor map (no WebGL, or chosen) ───────────────────────── */
function renderMap() {
  const host = $('#fl-map');
  if (state.view !== '2d') { host.hidden = true; return; }
  host.hidden = false;
  const W = 1000, H = 560, cx = 500, cy = 140, R = 380, N = B.SEATS.length;
  const pos = {};
  B.SEATS.forEach((s, i) => { const th = -75 + 150 * i / (N - 1), r = th * Math.PI / 180; pos[s.agent] = {x: cx + Math.sin(r) * R, y: cy + Math.cos(r) * R * 0.92}; });
  const f = state.floor, now = Date.now() / 1000;
  const edges = (f && f.edges || []).filter((e) => pos[e.from] && pos[e.to]);
  let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Floor map: seven desks around the central wall, with recorded collaboration links"><defs>' +
    B.SEATS.map((s) => '<marker id="ar-' + s.slug + '" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="' + s.accent + '"/></marker>').join('') +
    '<radialGradient id="mapglow" cx="50%" cy="20%" r="70%"><stop offset="0" stop-color="#16324a" stop-opacity=".55"/><stop offset="1" stop-color="#05090e" stop-opacity="0"/></radialGradient></defs>' +
    '<rect width="' + W + '" height="' + H + '" fill="url(#mapglow)"/>' +
    '<rect x="270" y="18" width="460" height="34" rx="6" fill="#0e1b28" stroke="#3d6f93"/><text x="500" y="40" text-anchor="middle" class="m-wall">CENTRAL WALL · equity · feed · health</text>' +
    '<circle cx="' + cx + '" cy="' + (cy + 120) + '" r="92" fill="none" stroke="#24435c" stroke-dasharray="2 6"/><text x="' + cx + '" y="' + (cy + 126) + '" text-anchor="middle" class="m-logo">BETTOR</text>';
  for (const e of edges) {
    const a = pos[e.from], b = pos[e.to], s = B.BY_AGENT[e.from];
    const mx = (a.x + b.x) / 2 + (cx - (a.x + b.x) / 2) * 0.35, my = (a.y + b.y) / 2 + (cy + 120 - (a.y + b.y) / 2) * 0.55;
    const op = Math.max(0.35, 1.1 - (now - (e.at || now)) / ((f.window_s || 3600)));
    svg += '<path class="m-edge' + (REDUCED ? '' : ' flow') + '" d="M' + a.x.toFixed(1) + ' ' + a.y.toFixed(1) + ' Q' + mx.toFixed(1) + ' ' + my.toFixed(1) + ' ' + b.x.toFixed(1) + ' ' + b.y.toFixed(1) + '" stroke="' + s.accent + '" stroke-opacity="' + op.toFixed(2) + '" marker-end="url(#ar-' + s.slug + ')"><title>' + esc(s.name + ' → ' + (B.BY_AGENT[e.to] || {}).name + ': ' + B.edgeLabel(e.kind) + (e.count > 1 ? ' ×' + e.count : '') + ' · ' + B.ago(e.at, now)) + '</title></path>';
  }
  B.SEATS.forEach((s) => {
    const a = agentOf(s.slug), p = pos[s.agent], m = B.STATES[B.stateOf(a)], dim = ['STALE', 'NOT_DEPLOYED', 'UNKNOWN'].includes(B.stateOf(a));
    svg += '<g class="m-desk' + (state.selected === s.slug ? ' sel' : '') + (dim ? ' dim' : '') + '" data-slug="' + s.slug + '" tabindex="0" role="button" aria-label="' + esc(s.name + ', ' + m.label + (a ? ': ' + (a.state_detail || '') : '')) + '" transform="translate(' + p.x.toFixed(1) + ' ' + p.y.toFixed(1) + ')">' +
      '<circle r="46" fill="' + m.color + '" fill-opacity=".08" stroke="' + m.color + '" stroke-width="2"' + (a && a.state === 'NOT_DEPLOYED' ? ' stroke-dasharray="4 5"' : '') + '/>' +
      (!REDUCED && m.motion === 'work' ? '<circle r="46" class="m-pulse" stroke="' + m.color + '"/>' : '') +
      '<rect x="-36" y="-14" width="72" height="12" rx="3" fill="#162230" stroke="' + s.accent + '" stroke-opacity=".7"/>' +
      '<circle cy="16" r="15" fill="#0d1620" stroke="' + s.accent + '" stroke-width="2"/><text y="21" text-anchor="middle" class="m-ini" fill="' + s.accent + '">' + esc(s.initial) + '</text>' +
      '<text y="-58" text-anchor="middle" class="m-name">' + esc(s.name) + '</text>' +
      '<text y="64" text-anchor="middle" class="m-state" fill="' + m.color + '">' + esc(m.label.toUpperCase()) + '</text>' +
      '<text y="80" text-anchor="middle" class="m-hb">♥ ' + esc(hb(a)) + '</text></g>';
  });
  svg += '</svg>';
  host.innerHTML = svg + '<p class="fl-map-note">' + (state.sceneFailed ? '3D unavailable on this device (' + esc(state.sceneFailed) + ') — the floor map shows the same recorded states.' : 'Floor map · the same recorded states as the 3D floor.') + '</p>';
}

/* ── data flow ────────────────────────────────────────────────────── */
function render() {
  statusPill(); roster(); drawWall(); renderMap();
  if (state.selected) panel(state.selected);
  if (state.scene) {
    const meta = {status: state.read ? state.read.status : 'NONE', why: state.read && state.read.why, stale: !!(state.read && state.read.status !== 'OK' && state.lastOk)};
    state.scene.setData(state.floor, meta);
  }
}
function onFloor(u) {
  state.read = u.current; state.lastOk = u.lastOk;
  state.floor = u.lastOk ? u.lastOk.data : null;
  render();
}
async function aux(path, key) {
  const r = await B.read(path);
  state[key] = r.status === 'OK' ? {status: 'OK', data: r.data, at: r.at} : {status: r.status, why: r.status === 'SIGNED_OUT' ? 'sign-in required' : r.status === 'NOT_RELEASED' ? 'not released on this API' : r.why};
  drawWall();
}

/* ── boot ─────────────────────────────────────────────────────────── */
async function boot() {
  roster(); statusPill(); drawWall();
  const want2d = params.get('view') === '2d';
  let gl = false;
  if (!want2d) {
    try {
      const mod = await import('./floor-scene.js');
      if (mod.webglAvailable() && !window.__floorForceNoWebGL) {
        state.scene = await mod.createFloor($('#fl-stage'), {
          phone: PHONE, reducedMotion: REDUCED, seats: B.SEATS,
          onPick: (slug, info) => { if (slug) select(slug, info); else if (state.selected || document.body.classList.contains('wall-focus')) select(null); },
          onHover: (slug, x, y) => tip(slug, x, y), onFrame,
          onStatus: (k, v) => { if (k === 'characters-ready') document.body.setAttribute('data-characters', v ? 'partial' : 'ready'); }
        });
        gl = true;
      } else state.sceneFailed = 'WebGL is not available';
    } catch (e) { state.sceneFailed = (e && e.message) || 'the 3D renderer could not start'; console.warn('floor: 3D unavailable', e); }
  }
  state.view = gl ? '3d' : '2d';
  document.body.classList.toggle('view-2d', !gl);
  $('#fl-view').textContent = gl ? 'Floor map' : (state.sceneFailed && !want2d ? '3D unavailable' : '3D floor');
  $('#fl-view').disabled = !gl && !!state.sceneFailed && !want2d;
  $('#fl-loading').hidden = true;
  B.poller('/api/command/floor', POLL_MS, onFloor);
  const auxLoop = () => { if (!document.hidden) { aux('/api/command/coverage', 'coverage'); aux('/api/command/p5/evidence', 'p5'); } };
  auxLoop(); setInterval(auxLoop, AUX_MS);
  loadEquity();
  setInterval(() => { if (document.hidden) return; statusPill(); roster(); if (state.scene) state.scene.tickClocks(); if (state.selected) panel(state.selected); drawWall(); renderMap(); }, 5000);
  render();
}

document.addEventListener('click', (e) => {
  const ws = e.target.closest('.wall-projected .fl-wall .wall-screen');
  if (ws && state.scene && !e.target.closest('a,button,[data-ew-win]')) { state.selected = null; $('#fl-panel').hidden = true; document.body.classList.remove('focused'); document.body.classList.add('wall-focus'); state.scene.focusWall(ws.dataset.screen); roster(); return; }
  const a = e.target.closest('.fl-agent'); if (a) { select(a.dataset.slug === state.selected ? null : a.dataset.slug, {fromKeyboard: e.detail === 0}); return; }
  const d = e.target.closest('.m-desk'); if (d) { select(d.dataset.slug, {again: d.dataset.slug === state.selected}); return; }
  if (e.target.closest('[data-close]')) { select(null); return; }
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && (state.selected || document.body.classList.contains('wall-focus'))) select(null);
  const d = e.target.closest && e.target.closest('.m-desk');
  if (d && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); select(d.dataset.slug, {again: d.dataset.slug === state.selected, fromKeyboard: true}); }
});
$('#fl-reset').addEventListener('click', () => select(null));
$('#fl-view').addEventListener('click', async () => {
  if (state.view === '3d') { state.view = '2d'; document.body.classList.add('view-2d'); if (state.scene) state.scene.setPaused(true); $('#fl-view').textContent = '3D floor'; $('#fl-view').setAttribute('aria-pressed', 'true'); }
  else if (state.scene) { state.view = '3d'; document.body.classList.remove('view-2d'); state.scene.setPaused(false); $('#fl-view').textContent = 'Floor map'; $('#fl-view').setAttribute('aria-pressed', 'false'); }
  else { location.search = ''; return; }
  drawWall(); renderMap();
});
window.__floor = state;
boot();
