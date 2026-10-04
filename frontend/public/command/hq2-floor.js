/* BETTOR HEADQUARTERS · THE TRADING FLOOR · data, HUD and text fallback.
 *
 * Classic script (window.BTHQ). It READS, it never writes:
 *   GET /api/command/floor              the seven desks, edges, feed, ranking
 *   GET /api/command/floor/<slug>       one desk's queue / outputs (on focus;
 *                                       Xavier's for the management wall)
 *   GET /api/command/equity/live        PAPER and SMALL LIVE, separately
 *   GET /api/command/coverage           league status for the coverage wall
 * Same origin, session cookie, no-store (window.BTFloor.read in
 * floor-core.js). The 3D scene (hq2-floor-scene.js) draws exactly what this
 * file hands it; when WebGL is missing it draws the same truth as text.
 *
 * TRUTH RULES (pinned by backend/tests/test_floor_hq_truth_ui.py):
 *   - no invented number: a missing value is UNAVAILABLE with its reason, an
 *     old read is STALE with its age, a fixture payload is labelled FIXTURE;
 *   - desk states are the floor API's derived states, translated one to one
 *     (WORKING / REVIEWING / CHALLENGING / WAITING / BLOCKED / NO TASK /
 *     STALE / OFFLINE); nothing is shown as working without that record;
 *   - PAPER and SMALL LIVE (and each legacy venue) are separate figures:
 *     no line of this file adds one book to another. */
(function () {
  'use strict';
  if (window.BTHQ) return;
  var B = window.BTFloor;
  if (!B) return;

  /* THE SEVEN DESKS. Static identity only (names, roles, colours, the
   * workspace route); every state comes from the floor read. User-facing
   * name of the Chief Allocator is Allie; slug and route stay allocator. */
  var SEATS = [
    {agent: 'DEREK', slug: 'derek', name: 'Derek', role: 'Chief Investment Officer', zone: 'Discovery & entry',
     accent: '#7fe0b4', href: '/derek'},
    {agent: 'KAREN', slug: 'karen', name: 'Karen', role: 'Red team', zone: 'Adversarial review',
     accent: '#ff7d86', href: '/karen'},
    {agent: 'SCOUT', slug: 'scout', name: 'Scout', role: 'Market intelligence', zone: 'Research & signals',
     accent: '#f3ae68', href: '/scout'},
    {agent: 'CHIEF_ALLOCATOR', slug: 'allocator', name: 'Allie', role: 'Chief Allocator', zone: 'Capital allocation',
     accent: '#e9c46a', href: '/allocator'},
    {agent: 'EDDIE', slug: 'eddie', name: 'Eddie', role: 'Head of Execution', zone: 'Execution & microstructure',
     accent: '#5fd8cf', href: '/eddie'},
    {agent: 'AUDREY', slug: 'audrey', name: 'Audrey', role: 'Audit & reconciliation', zone: 'Audit & reconciliation',
     accent: '#c3a8ff', href: '/audrey'},
    {agent: 'XAVIER', slug: 'xavier', name: 'Xavier', role: 'Portfolio Manager', zone: 'Portfolio management',
     accent: '#86c8f4', href: '/xavier'}
  ];
  var BY_SLUG = {}, BY_AGENT = {};
  SEATS.forEach(function (s) { BY_SLUG[s.slug] = s; BY_AGENT[s.agent] = s; });

  /* THE FLOOR API STATE -> what the floor shows. One to one; `active` is
   * the only thing allowed to drive "at work" motion. */
  var STATE = {
    WORKING_ON:   {label: 'WORKING',     color: '#4fe0a8', tone: 'work',  active: true},
    REVIEWING:    {label: 'REVIEWING',   color: '#68b6ff', tone: 'work',  active: true},
    CHALLENGING:  {label: 'CHALLENGING', color: '#ff7d8e', tone: 'work',  active: true},
    WAITING:      {label: 'WAITING',     color: '#eab768', tone: 'wait',  active: false},
    BLOCKED:      {label: 'BLOCKED',     color: '#ff9a5c', tone: 'wait',  active: false},
    IDLE:         {label: 'NO TASK',     color: '#9fb0c4', tone: 'idle',  active: false},
    STALE:        {label: 'STALE',       color: '#7d8796', tone: 'stale', active: false},
    NOT_DEPLOYED: {label: 'OFFLINE',     color: '#5b6576', tone: 'off',   active: false},
    UNAVAILABLE:  {label: 'UNAVAILABLE', color: '#5b6576', tone: 'off',   active: false}
  };

  var esc = B.esc;
  var now = function () { return Date.now() / 1000; };
  function epoch(v) {
    if (v == null) return null;
    if (typeof v === 'number') return isFinite(v) ? v : null;
    var t = Date.parse(v); return isNaN(t) ? null : t / 1000;
  }
  function ago(t) { var e = epoch(t); return e == null ? 'never' : B.ago(e); }
  function clock(t) {
    var e = epoch(t); if (e == null) return '—';
    return new Date(e * 1000).toISOString().slice(11, 19) + ' UTC';
  }
  function usd(v) {
    if (v == null || typeof v !== 'number' || !isFinite(v)) return null;
    var s = Math.abs(v).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    return (v < 0 ? '−$' : '$') + s;
  }
  function signedUsd(v) { var s = usd(v); return s == null ? null : (v > 0 ? '+' : '') + s; }
  function words(s) { return String(s == null ? '' : s).replace(/_/g, ' '); }

  /* ── reads ───────────────────────────────────────────────────────── */
  var R = {
    floor: {status: 'LOADING', data: null, at: null, why: null, lastOk: null},
    equity: {status: 'LOADING', data: null, at: null, why: null, lastOk: null, seq: null, changedAt: null},
    coverage: {status: 'LOADING', data: null, at: null, why: null, lastOk: null},
    detail: {}
  };
  var subs = [];
  function emit(kind) { subs.forEach(function (fn) { try { fn(kind, api); } catch (e) { if (window.console) console.warn('BTHQ subscriber', e); } }); }
  function store(slot, res) {
    slot.status = res.status; slot.at = res.at; slot.why = res.why || null;
    if (res.status === 'OK') { slot.data = res.data; slot.lastOk = res.at; }
  }
  function onFloor(r) { store(R.floor, r.current); paint(); emit('floor'); }
  function onEquity(r) {
    var prev = R.equity.data && R.equity.data.seq;
    store(R.equity, r.current);
    var seq = R.equity.data && R.equity.data.seq;
    // a genuine content change on the server raises seq (command_equity
    // POLLING CONTRACT); only that may pulse the capital core
    if (r.current.status === 'OK' && prev != null && seq != null && seq !== prev) R.equity.changedAt = now();
    paint(); emit('equity');
  }
  function onCoverage(r) { store(R.coverage, r.current); emit('coverage'); paintStatic(); }
  function readDetail(slug) {
    if (!BY_SLUG[slug]) return Promise.resolve(null);
    return B.read('/api/command/floor/' + slug).then(function (res) {
      var slot = R.detail[slug] || (R.detail[slug] = {});
      store(slot, res); emit('detail:' + slug);
      if (slug === selected) paintPanel();
      return slot;
    });
  }

  /* ── view models (what every surface draws) ──────────────────────── */
  function floorData() { return R.floor.data; }
  function floorFresh() { return R.floor.status === 'OK'; }
  function agentOf(slug) {
    var f = floorData(); if (!f) return null;
    var seat = BY_SLUG[slug];
    return (f.agents || []).filter(function (a) { return a.slug === slug || (seat && a.agent === seat.agent); })[0] || null;
  }
  function desk(slug) {
    var seat = BY_SLUG[slug], a = agentOf(slug);
    var code = !a ? 'UNAVAILABLE' : a.state in STATE ? a.state : 'UNAVAILABLE';
    if (code === 'WAITING' && a.status_row && /^(BLOCKED|FAILED)$/.test(a.status_row.state || '')) code = 'BLOCKED';
    var meta = STATE[code];
    var level = a && a.authority && a.authority.level || '';
    return {
      seat: seat, slug: slug, name: seat.name, role: seat.role, zone: seat.zone, accent: seat.accent,
      href: (a && a.workspace) || seat.href, code: code, apiState: a ? a.state : null,
      label: meta.label, color: meta.color, tone: meta.tone, active: !!meta.active && floorFresh(),
      shadow: /SHADOW/.test(level), authority: level,
      detail: a ? (a.state_detail || '') : (R.floor.why || 'The floor has not been read yet.'),
      since: a ? a.state_since : null,
      heartbeatAt: a && a.heartbeat ? a.heartbeat.at : null,
      heartbeatSource: a && a.heartbeat ? a.heartbeat.source : null,
      deployed: a ? a.deployed !== false : null, deployWhy: a ? a.deploy_why : null,
      monitor: a ? (a.monitor || []) : [],
      last: a ? (a.last_output || a.focus || null) : null,
      challenges: a ? a.challenges || {} : {},
      may: a && a.authority ? a.authority.may || [] : [], mayNot: a && a.authority ? a.authority.may_not || [] : [],
      readStale: !!a && !floorFresh(), readAt: R.floor.lastOk, readWhy: R.floor.why
    };
  }
  function desks() { return SEATS.map(function (s) { return desk(s.slug); }); }
  function edges() {
    var f = floorData(); if (!f) return [];
    return (f.edges || []).map(function (e) {
      var a = BY_AGENT[e.from], b = BY_AGENT[e.to];
      if (!a || !b || a === b) return null;
      return {from: a.slug, to: b.slug, kind: e.kind, label: B.edgeLabel(e.kind), count: e.count || 1,
              at: e.at, summary: e.summary, evidence: e.evidence || []};
    }).filter(Boolean);
  }
  function equity() {
    var d = R.equity.data;
    var p = d && d.paper, s = d && d.small_live_bettor, v = d && d.actual && d.actual.venues || {};
    return {status: R.equity.status, why: R.equity.why, readAt: R.equity.lastOk, fresh: R.equity.status === 'OK',
            changedAt: R.equity.changedAt, paper: p || null, small: s || null,
            pm: v.polymarket_us || null, kalshi: v.kalshi || null, fixture: B.isFixture(d)};
  }
  /* The tickers: one line per real paper position, with its real mark and
   * the time that mark was observed. UNMARKED stays UNMARKED. */
  function tickers() {
    var p = equity().paper;
    var rows = p && p.open_positions && p.open_positions.rows || [];
    return rows.map(function (r) {
      return {market: r.market || r.key || 'UNKNOWN MARKET', side: r.side || '', state: r.mark_state,
              price: typeof r.mark_price === 'number' ? r.mark_price : null, at: r.mark_observed_at,
              pnl: typeof r.unrealized_pnl_usd === 'number' ? r.unrealized_pnl_usd : null,
              cost: r.cost_basis_usd, why: r.why_unmarked};
    });
  }
  function decisions() { var f = floorData(); return f ? (f.feed || []) : []; }
  function ranking() { var f = floorData(); return f ? (f.opportunities || []) : []; }
  function coverage() {
    var c = R.coverage.data, ls = c && c.league_status;
    if (R.coverage.status !== 'OK') return {status: R.coverage.status, why: R.coverage.why, rows: [], counts: {}};
    if (!ls || ls.status === 'UNAVAILABLE') return {status: 'UNAVAILABLE', why: ls && ls.why || 'NO_LEAGUE_STATUS', rows: [], counts: {}};
    var counts = {};
    (ls.statuses || []).forEach(function (r) { counts[r.status] = (counts[r.status] || 0) + 1; });
    return {status: 'OK', day: ls.day, tz: ls.tz, rows: ls.statuses || [], counts: counts, readAt: R.coverage.lastOk};
  }
  function xavierDecisions() {
    var d = R.detail.xavier && R.detail.xavier.data;
    if (!d) return {status: R.detail.xavier ? R.detail.xavier.status : 'LOADING', why: R.detail.xavier && R.detail.xavier.why, rows: []};
    return {status: 'OK', readAt: R.detail.xavier.lastOk, rows: (d.outputs || []).filter(function (o) { return o.kind === 'xavier_management_assessments'; })};
  }
  function fixture() {
    return B.isFixture(R.floor.data) || B.isFixture(R.equity.data) || B.isFixture(R.coverage.data);
  }

  /* ── selection / navigation ─────────────────────────────────────── */
  var selected = null, hovered = null;
  function select(slug, how) {
    slug = slug && BY_SLUG[slug] ? slug : null;
    if (slug === selected) { emit('select'); return; }
    selected = slug;
    document.body.classList.toggle('hqf-focused', !!slug);
    if (slug) readDetail(slug);
    paintPanel(); paintRoster(); emit('select');
    if (slug && how !== 'pointer') { var p = el('hqf-panel'); if (p && how === 'keyboard') p.focus({preventScroll: true}); }
  }
  function open(slug) {
    var d = desk(slug);
    if (!d || !d.href || d.href.charAt(0) !== '/') return;
    location.href = d.href;
  }
  function hover(slug, x, y) {
    hovered = slug && BY_SLUG[slug] ? slug : null;
    var tip = el('hqf-tip'); if (!tip) return;
    if (!hovered) { tip.hidden = true; return; }
    var d = desk(hovered);
    tip.innerHTML = '<b>' + esc(d.name) + '</b><span>' + esc(d.role) + '</span><em style="--c:' + d.color + '"><i></i>' + esc(d.label) +
      (d.shadow ? ' · SHADOW' : '') + '</em><small>' + esc(d.detail).slice(0, 140) + '</small>';
    tip.hidden = false;
    var r = el('hqf-stage').getBoundingClientRect();
    var tx = Math.min(r.width - 260, Math.max(8, x - r.left + 16)), ty = Math.min(r.height - 120, Math.max(8, y - r.top + 14));
    tip.style.transform = 'translate(' + tx + 'px,' + ty + 'px)';
  }

  /* ── DOM ─────────────────────────────────────────────────────────── */
  function el(id) { return document.getElementById(id); }
  function chip(color, text) { return '<span class="hqf-chip" style="--c:' + color + '"><i></i>' + esc(text) + '</span>'; }

  function paintTop() {
    var t = el('hqf-read'); if (!t) return;
    var f = R.floor, e = R.equity, bits = [];
    if (f.status === 'OK') bits.push(chip('#4fe0a8', 'FLOOR READ ' + clock(f.at)));
    else if (f.lastOk) bits.push(chip('#eab768', 'FLOOR STALE · LAST READ ' + ago(f.lastOk) + ' · ' + (f.why || f.status)));
    else if (f.status === 'LOADING') bits.push(chip('#9fb0c4', 'READING THE FLOOR…'));
    else bits.push(chip('#ff9a5c', (f.status === 'SIGNED_OUT' ? 'SIGNED OUT · ' : 'FLOOR UNAVAILABLE · ') + (f.why || f.status)));
    var fx = el('hqf-fixture'); if (fx) fx.hidden = !fixture();
    t.innerHTML = bits.join('');
    // the capital chip: PAPER and SMALL LIVE side by side, never one figure
    var c = el('hqf-capital'); if (!c) return;
    var q = equity(), p = q.paper, s = q.small;
    var pv = p && usd(p.equity_usd), ps = p ? p.status : (q.status === 'LOADING' ? 'READING' : 'UNAVAILABLE');
    c.innerHTML =
      '<div class="hqf-cap" data-st="' + esc(ps) + '"><span class="k">Paper equity · simulated</span><b>' + esc(pv || 'UNAVAILABLE') + '</b>' +
        '<small>' + esc(ps) + (p && p.no_new_mark ? ' · NO NEW MARK' : '') + (p && p.source_at ? ' · ' + clock(p.source_at) : (q.why ? ' · ' + q.why : '')) + '</small></div>' +
      '<div class="hqf-cap small" data-st="' + esc(s && s.status || 'UNAVAILABLE') + '"><span class="k">Small Live · BETTOR originated</span><b>' + esc(s && s.status ? words(s.status) : 'UNAVAILABLE') + '</b>' +
        '<small>' + esc(s ? (s.equity && s.equity.usd != null ? usd(s.equity.usd) : 'equity: ' + (s.equity && s.equity.why || s.why || 'UNAVAILABLE')) : (q.why || 'not read')) + '</small></div>';
  }
  function paintRoster() {
    var nav = el('hqf-roster'); if (!nav) return;
    if (!nav.firstChild) {
      nav.innerHTML = '<p class="hqf-roster-h">Desks</p>' + SEATS.map(function (s) {
        return '<button type="button" class="hqf-desk" data-slug="' + s.slug + '" style="--a:' + s.accent + '" aria-pressed="false">' +
          '<span class="n">' + esc(s.name) + '</span><span class="r">' + esc(s.role) + '</span><span class="st"><i></i><em></em></span></button>';
      }).join('');
      nav.addEventListener('click', function (ev) {
        var b = ev.target.closest('.hqf-desk'); if (!b) return;
        var slug = b.getAttribute('data-slug');
        if (selected === slug) open(slug); else select(slug, ev.detail === 0 ? 'keyboard' : 'roster');
      });
      nav.addEventListener('scroll', rosterScroll, {passive: true});
    }
    nav.querySelectorAll('.hqf-desk').forEach(function (b) {
      var d = desk(b.getAttribute('data-slug'));
      b.style.setProperty('--c', d.color);
      b.querySelector('em').textContent = d.label + (d.shadow ? ' · SHADOW' : '');
      b.setAttribute('aria-pressed', String(selected === d.slug));
      b.setAttribute('aria-label', d.name + ', ' + d.role + ': ' + d.label + '. ' + (selected === d.slug ? 'Press again to open the workspace.' : 'Press to focus the desk.'));
      b.classList.toggle('on', selected === d.slug);
      b.classList.toggle('active', d.active);
    });
  }
  // phone: the swipeable roster selects the desk that snaps to the centre
  var rosterTimer = 0;
  function rosterScroll() {
    if (!matchMedia('(max-width: 780px)').matches) return;
    clearTimeout(rosterTimer);
    rosterTimer = setTimeout(function () {
      var nav = el('hqf-roster'), r = nav.getBoundingClientRect(), mid = r.left + r.width / 2, best = null, bd = 1e9;
      nav.querySelectorAll('.hqf-desk').forEach(function (b) { var q = b.getBoundingClientRect(), dd = Math.abs(q.left + q.width / 2 - mid); if (dd < bd) { bd = dd; best = b; } });
      if (best && selected && best.getAttribute('data-slug') !== selected) select(best.getAttribute('data-slug'), 'swipe');
    }, 160);
  }
  function metricHtml(m) {
    var v = m.value == null ? '<b class="na">UNAVAILABLE</b><small>' + esc(m.why || 'no value recorded') + '</small>'
      : '<b>' + esc(m.value) + '</b>';
    return '<div class="hqf-m"><span>' + esc(m.label) + '</span>' + v +
      '<small class="src">' + esc(m.source || '') + (m.as_of ? ' · ' + esc(clock(m.as_of)) : '') + '</small></div>';
  }
  function paintPanel() {
    var p = el('hqf-panel'); if (!p) return;
    if (!selected) { p.hidden = true; p.innerHTML = ''; return; }
    var d = desk(selected), det = R.detail[selected], dd = det && det.data;
    var since = d.since ? 'since ' + clock(d.since) + ' (' + ago(d.since) + ')' : '';
    var hb = d.heartbeatAt ? 'heartbeat ' + ago(d.heartbeatAt) : (d.deployed === false ? 'not deployed' : 'no heartbeat recorded');
    var q = dd ? (dd.queue || []) : null, outs = dd ? (dd.outputs || []) : null;
    p.innerHTML =
      '<header class="hqf-p-head" style="--a:' + d.accent + '">' +
        '<div><span class="hqf-p-zone">' + esc(d.zone) + '</span><h2>' + esc(d.name) + '</h2><p>' + esc(d.role) + '</p></div>' +
        '<button type="button" class="hqf-x" data-act="back" aria-label="Back to the floor overview">×</button></header>' +
      '<div class="hqf-p-state" style="--c:' + d.color + '"><span class="hqf-chip big" style="--c:' + d.color + '"><i></i>' + esc(d.label) + '</span>' +
        (d.shadow ? '<span class="hqf-tag">SHADOW</span>' : '') + (d.readStale ? '<span class="hqf-tag warn">STALE READ · ' + esc(ago(d.readAt)) + '</span>' : '') +
        '<p>' + esc(d.detail) + '</p><small>' + esc([since, hb].filter(Boolean).join(' · ')) + ' · source GET /api/command/floor</small></div>' +
      (d.monitor.length ? '<div class="hqf-p-grid">' + d.monitor.slice(0, 6).map(metricHtml).join('') + '</div>' : '') +
      (d.last ? '<div class="hqf-p-sec"><h3>Latest recorded output</h3><p class="hqf-p-out">' + esc(d.last.summary || '') + '</p><small class="src">' +
        esc((d.last.kind || '') + (d.last.id ? ' #' + d.last.id : '') + (d.last.at ? ' · ' + clock(d.last.at) : '')) + '</small></div>' : '') +
      '<div class="hqf-p-sec"><h3>Queue</h3>' + (q == null ? '<p class="hqf-dim">' + esc(det ? (det.why || det.status) : 'Reading…') + '</p>' :
        q.length ? '<ul class="hqf-list">' + q.slice(0, 4).map(function (x) { return '<li><b>' + esc(x.title) + '</b><small>' + esc(x.status + ' · ' + ago(x.at)) + '</small></li>'; }).join('') + '</ul>'
        : '<p class="hqf-dim">No open task recorded.</p>') + '</div>' +
      (outs && outs.length ? '<div class="hqf-p-sec"><h3>Recent outputs</h3><ul class="hqf-list">' + outs.slice(0, 4).map(function (x) {
        return '<li><b>' + esc(x.summary || x.verdict) + '</b><small>' + esc(clock(x.at)) + '</small></li>'; }).join('') + '</ul></div>' : '') +
      (d.mayNot.length ? '<details class="hqf-p-sec"><summary>Authority · ' + esc(words(d.authority) || 'UNKNOWN') + '</summary><ul class="hqf-auth">' +
        d.may.map(function (x) { return '<li class="y">' + esc(x) + '</li>'; }).join('') + d.mayNot.map(function (x) { return '<li class="n">' + esc(x) + '</li>'; }).join('') + '</ul></details>' : '') +
      '<div class="hqf-p-act"><a class="hqf-open" href="' + esc(d.href) + '">Open ' + esc(d.name) + '’s workspace →</a>' +
        '<button type="button" class="hqf-ghost" data-act="back">Back to overview</button></div>';
    p.hidden = false;
    p.querySelectorAll('[data-act="back"]').forEach(function (b) { b.addEventListener('click', function () { select(null); }); });
  }

  /* ── the text floor (no WebGL, or the viewer asked for it) ───────── */
  function paintStatic() {
    var s = el('hqf-static'); if (!s || s.hidden) return;
    var q = equity(), p = q.paper, sm = q.small, cov = coverage(), tk = tickers(), dec = decisions().slice(0, 8);
    var capital = '<section class="hqf-s-cap"><article><h3>Paper equity <small>' + esc(p ? p.label || 'PAPER' : 'PAPER') + '</small></h3><b>' + esc(p && usd(p.equity_usd) || 'UNAVAILABLE') + '</b>' +
      '<p>' + esc(p ? p.status + (p.why ? ' · ' + p.why : '') + (p.no_new_mark ? ' · NO NEW MARK since ' + clock(p.last_genuine_mark_update_at) : '') : (q.why || 'not read')) + '</p>' +
      '<small>source ' + esc(p && p.source || 'GET /api/command/equity/live') + (p && p.source_at ? ' · ' + esc(clock(p.source_at)) : '') + '</small></article>' +
      '<article><h3>Small Live <small>BETTOR originated</small></h3><b>' + esc(sm && sm.status ? words(sm.status) : 'UNAVAILABLE') + '</b><p>' + esc(sm ? (sm.why || '') : (q.why || 'not read')) + '</p>' +
      '<small>equity ' + esc(sm && sm.equity ? (sm.equity.usd != null ? usd(sm.equity.usd) : 'UNAVAILABLE · ' + sm.equity.why) : 'UNAVAILABLE') + '</small></article></section>';
    var deskHtml = '<section class="hqf-s-desks">' + desks().map(function (d) {
      return '<a class="hqf-s-desk" href="' + esc(d.href) + '" style="--a:' + d.accent + ';--c:' + d.color + '"><span class="z">' + esc(d.zone) + '</span><b>' + esc(d.name) + '</b><span class="r">' + esc(d.role) + '</span>' +
        '<span class="hqf-chip" style="--c:' + d.color + '"><i></i>' + esc(d.label) + (d.shadow ? ' · SHADOW' : '') + '</span><p>' + esc(d.detail) + '</p>' +
        '<small>' + esc(d.heartbeatAt ? 'heartbeat ' + ago(d.heartbeatAt) : d.deployed === false ? 'not deployed' : 'no heartbeat') + '</small></a>';
    }).join('') + '</section>';
    var tkHtml = '<section class="hqf-s-list"><h3>Paper positions · real marks</h3>' + (tk.length ? '<ul>' + tk.map(function (t) {
      return '<li><b>' + esc(t.market) + ' ' + esc(t.side) + '</b><span>' + (t.price != null ? esc(t.price.toFixed(4)) + ' · ' + esc(words(t.state)) + ' · ' + esc(clock(t.at)) : 'UNMARKED · ' + esc(t.why || 'no mark')) + '</span></li>'; }).join('') + '</ul>'
      : '<p class="hqf-dim">' + esc(p ? 'No open paper position.' : 'UNAVAILABLE · ' + (q.why || 'not read')) + '</p>') + '</section>';
    var decHtml = '<section class="hqf-s-list"><h3>Decisions & refusals · PAPER</h3>' + (dec.length ? '<ul>' + dec.map(function (x) {
      return '<li><b>' + esc(x.verdict) + ' ' + esc(x.market || x.id) + '</b><span>' + esc((x.refusal ? words(x.refusal) + ' · ' : '') + clock(x.at)) + '</span></li>'; }).join('') + '</ul>'
      : '<p class="hqf-dim">' + esc(floorData() ? 'No paper decision recorded.' : 'UNAVAILABLE · ' + (R.floor.why || 'not read')) + '</p>') + '</section>';
    var covHtml = '<section class="hqf-s-list"><h3>Coverage · league status</h3>' + (cov.status === 'OK' ? '<p>' + Object.keys(cov.counts).map(function (k) { return esc(words(k)) + ' <b>' + cov.counts[k] + '</b>'; }).join(' · ') + '</p><small>day ' + esc(cov.day || '') + '</small>'
      : '<p class="hqf-dim">' + esc(cov.status + (cov.why ? ' · ' + cov.why : '')) + '</p>') + '</section>';
    s.innerHTML = '<header class="hqf-s-head"><h2>Trading floor · text view</h2><p>' + esc(webgl === false ? '3D is unavailable on this device (WebGL could not start). ' : '') +
      'Every value below is a real read with its timestamp; nothing is simulated.</p>' + (webgl !== false ? '<button type="button" class="hqf-ghost" data-act="3d">Back to the 3D floor</button>' : '') + '</header>' +
      capital + deskHtml + '<div class="hqf-s-cols">' + tkHtml + decHtml + covHtml + '</div>';
    var b3 = s.querySelector('[data-act="3d"]'); if (b3) b3.addEventListener('click', function () { textView(false); });
  }
  var webgl = null;
  function textView(on) {
    var s = el('hqf-static'), st = el('hqf-stage');
    if (!s || !st) return;
    s.hidden = !on; st.hidden = !!on;
    document.body.classList.toggle('hqf-text', !!on);
    if (on) paintStatic();
    emit(on ? 'paused' : 'resumed');
  }
  function noWebGL(reason) {
    webgl = false;
    var l = el('hqf-loader'); if (l) l.hidden = true;
    textView(true);
    var t = el('hqf-textbtn'); if (t) t.hidden = true;
    if (window.console && reason) console.info('BETTOR floor: text view (' + reason + ')');
  }
  function progress(frac, label) {
    var l = el('hqf-loader'); if (!l) return;
    var bar = l.querySelector('i'), lab = l.querySelector('span');
    if (bar) bar.style.transform = 'scaleX(' + Math.max(0.02, Math.min(1, frac)) + ')';
    if (lab && label) lab.textContent = label;
    if (frac >= 1) { l.classList.add('done'); setTimeout(function () { l.hidden = true; }, 700); }
  }

  function paint() { paintTop(); paintRoster(); if (selected) paintPanel(); paintStatic(); }

  /* ── wiring ─────────────────────────────────────────────────────── */
  function boot() {
    paint();
    var back = el('hqf-back'); if (back) back.addEventListener('click', function () { select(null); });
    var tb = el('hqf-textbtn'); if (tb) tb.addEventListener('click', function () { textView(!document.body.classList.contains('hqf-text')); });
    document.addEventListener('keydown', function (ev) {
      if (ev.target && /input|textarea|select/i.test(ev.target.tagName)) return;
      if (ev.key === 'Escape' && selected) { ev.preventDefault(); select(null); var b = document.querySelector('.hqf-desk[data-slug]'); if (b) b.focus(); }
      if ((ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') && selected && !(ev.target && ev.target.closest && ev.target.closest('.hqf-panel details'))) {
        var i = SEATS.findIndex(function (s) { return s.slug === selected; });
        var n = SEATS[(i + (ev.key === 'ArrowRight' ? 1 : SEATS.length - 1)) % SEATS.length];
        select(n.slug, 'keyboard-cycle');
        var bb = document.querySelector('.hqf-desk[data-slug="' + n.slug + '"]'); if (bb) bb.focus({preventScroll: true});
      }
    });
    B.poller('/api/command/floor', 15000, onFloor);
    B.poller('/api/command/equity/live', 8000, onEquity);
    B.poller('/api/command/coverage?tz=America%2FNew_York&days=1', 120000, onCoverage);
    // Xavier's management decisions feed the portfolio wall
    var xv = function () { if (!document.hidden) readDetail('xavier'); };
    xv(); setInterval(xv, 60000);
    setInterval(function () { if (selected && !document.hidden) readDetail(selected); }, 30000);
    setInterval(function () { if (!document.hidden) paintTop(); }, 5000);
  }

  var api = window.BTHQ = {
    SEATS: SEATS, BY_SLUG: BY_SLUG, BY_AGENT: BY_AGENT, STATE: STATE,
    reads: R, desk: desk, desks: desks, edges: edges, equity: equity, tickers: tickers, decisions: decisions,
    ranking: ranking, coverage: coverage, xavierDecisions: xavierDecisions, fixture: fixture,
    selected: function () { return selected; }, hovered: function () { return hovered; },
    select: select, open: open, hover: hover, textView: textView, noWebGL: noWebGL, progress: progress,
    subscribe: function (fn) { subs.push(fn); }, util: {usd: usd, signedUsd: signedUsd, clock: clock, ago: ago, epoch: epoch, words: words, esc: esc},
    reducedMotion: B.reducedMotion
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
