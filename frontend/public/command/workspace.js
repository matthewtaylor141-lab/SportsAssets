/* AN AGENT'S WORKSPACE (inside agent.html). Reads, same-origin and GET only:
 *   /api/command/floor/<agent>                 live state, mandate, queue,
 *                                              outputs, challenges, timeline
 *   /api/command/profitability/scorecards      economic contribution (pos-twin)
 *   /api/command/tournament/agents             agent tournaments (pos-learn)
 *   /api/command/profitability/north-star      firm-level north star (pos-econ)
 *   allocator only: /api/command/intel/allocator (ranking, correlation,
 *   capacity, opportunity cost), /api/command/profitability/capital and
 *   /capacity (pos-econ).
 * A read that is absent on this API (404) says NOT DEPLOYED ON THIS API, a
 * signed-out read says SIGN-IN REQUIRED, a failed read UNAVAILABLE with the
 * reason -- never a manufactured zero. PAPER and ACTUAL stay separate. */
(function () {
  'use strict';
  var B = window.BTFloor, cfg = window.__btWorkspace || {};
  var root = document.getElementById('ws-root');
  if (!B || !root) return;
  var esc = B.esc, slug = cfg.agent, seat = B.BY_SLUG[slug] || B.SEATS[0];
  var SCORE_NAME = {CHIEF_ALLOCATOR: 'ALLOCATOR'};
  var S = {detail: null, read: null, lastOk: null, score: null, tour: null, north: null, alloc: null, capital: null, capacity: null};

  function now() { return Date.now() / 1000; }
  function chip(st, label) { var m = B.STATES[st] || B.STATES.UNKNOWN; return '<span class="wsx-chip" style="--c:' + m.color + '"><i></i>' + esc(label || m.label) + '</span>'; }
  function ev(ref) {
    if (!ref || !ref.id) return '';
    var t = esc(ref.kind) + ' <code>' + esc(ref.id) + '</code>';
    return ref.href ? '<a href="' + esc(ref.href) + '" target="_blank" rel="noopener" title="Open the source record (JSON)">' + t + ' ↗</a>' : '<span>' + t + '</span>';
  }
  function money(v) { return typeof v === 'number' && isFinite(v) ? v.toLocaleString('en-US', {style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2}) : '—'; }
  function readState(r, what) {
    if (!r) return {status: 'LOADING', why: 'Reading ' + what + '…'};
    if (r.status === 'OK') return {status: 'OK', data: r.data, at: r.at};
    if (r.status === 'NOT_RELEASED') return {status: 'UNAVAILABLE', why: 'NOT DEPLOYED ON THIS API: ' + what + ' is not served here yet.'};
    if (r.status === 'SIGNED_OUT') return {status: 'UNAVAILABLE', why: 'SIGN-IN REQUIRED to read ' + what + '.'};
    return {status: 'UNAVAILABLE', why: 'UNAVAILABLE: ' + r.why};
  }
  function card(title, body, opts) {
    opts = opts || {};
    return '<section class="wsx-card' + (opts.wide ? ' wide' : '') + '"' + (opts.id ? ' id="' + opts.id + '"' : '') + '><header><h2>' + esc(title) + '</h2>' + (opts.tag ? '<span class="wsx-tag">' + esc(opts.tag) + '</span>' : '') + '</header>' + body + '</section>';
  }
  function empty(t) { return '<p class="wsx-empty">' + esc(t) + '</p>'; }
  function rows(list, fn) { return '<ul class="wsx-rows">' + list.map(fn).join('') + '</ul>'; }

  /* ── hero ─────────────────────────────────────────────────────── */
  function hero(a) {
    var t = now(), st = B.stateOf(a), fixture = S.lastOk && B.isFixture(S.lastOk.data);
    var stale = S.read && S.read.status !== 'OK' && S.lastOk;
    var status = !S.read ? 'Reading the floor…' : S.read.status === 'OK' ? 'Live · read ' + B.ago(S.read.at, t) : stale ? 'STALE READ · last good ' + B.ago(S.lastOk.at, t) : readState(S.read, 'the floor').why;
    return '<header class="wsx-hero" style="--a:' + seat.accent + '">' +
      '<div class="wsx-id"><span class="wsx-mono" aria-hidden="true">' + esc(seat.initial) + '</span><div><span class="wsx-eyebrow">' + esc(seat.short) + ' · AI agent</span><h1>' + esc(seat.name) + '</h1><p>' + esc(a ? a.title : seat.role) + '</p></div></div>' +
      '<div class="wsx-live">' + chip(st) + '<p class="wsx-detail">' + esc(a ? a.state_detail : (S.read ? readState(S.read, 'the floor').why : '')) + '</p>' +
        '<small>' + (a && a.state_since ? 'since ' + esc(B.ago(a.state_since, t)) + ' · ' : '') + esc(status) + (fixture ? ' · <b class="wsx-fx">FIXTURE DATA — NOT PRODUCTION</b>' : '') + '</small></div>' +
      '<div class="wsx-hb"><span class="wsx-eyebrow">Heartbeat</span><strong>' + esc(a && a.heartbeat && a.heartbeat.at != null ? B.age(t - a.heartbeat.at).trim() + ' ago' : a ? 'none recorded' : '—') + '</strong>' +
        '<small>' + esc(a && a.heartbeat && a.heartbeat.at != null ? B.clock(a.heartbeat.at) : '') + (a && a.heartbeat ? ' · stale after ' + esc(B.age(a.heartbeat.stale_after_s).trim()) : '') + '</small>' +
        '<a class="wsx-floor" href="/floor">See ' + esc(seat.name) + ' on the trading floor →</a></div>' +
      '</header>';
  }
  function strip(a) {   // eddie / scout: no duplicate workspace, only the live strip
    var t = now(), st = B.stateOf(a);
    return '<div class="wsx-strip" style="--a:' + seat.accent + '"><b>' + esc(seat.name) + '</b>' + chip(st) + '<span>' + esc(a ? a.state_detail : (S.read ? readState(S.read, 'the floor').why : 'Reading the floor…')) + '</span>' +
      '<small>♥ ' + esc(a && a.heartbeat && a.heartbeat.at != null ? B.ago(a.heartbeat.at, t) : 'no heartbeat') + '</small><a href="/floor">Trading floor →</a></div>';
  }

  /* ── sections ─────────────────────────────────────────────────── */
  function mandate(a) {
    if (!a) return card('Mandate & authority boundary', empty('Waiting for the floor read.'));
    var au = a.authority || {}, tp = au.tool_permissions;
    return card('Mandate & authority boundary', '<p class="wsx-level">Authority: <b>' + esc((au.level || '').replace(/_/g, ' ').toLowerCase()) + '</b></p>' +
      '<div class="wsx-may"><div><h3>May</h3><ul>' + (au.may || []).map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></div>' +
      '<div class="no"><h3>May not</h3><ul>' + (au.may_not || []).map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></div></div>' +
      (tp && (tp.allowed || tp.denied) ? '<details class="wsx-tools"><summary>Registered tool permissions (agent_identities)</summary><p>' + (tp.allowed || []).map(function (x) { return '<span class="wsx-tool ok">' + esc(x) + '</span>'; }).join('') + '</p><p>' + (tp.denied || []).map(function (x) { return '<span class="wsx-tool no">' + esc(x) + '</span>'; }).join('') + '</p><small>Order path: ' + esc(tp.order_path || 'none') + '</small></details>' : ''),
      {tag: au.level === 'NONE_ZERO_AUTHORITY' ? 'ZERO AUTHORITY' : au.level && /SHADOW/.test(au.level) ? 'SHADOW' : 'READ-ONLY VIEW'});
  }
  function kpis(a) {
    if (!a || !(a.monitor || []).length) return '';
    var t = now();
    return '<section class="wsx-kpis">' + a.monitor.map(function (m) {
      return '<div class="wsx-kpi"><span>' + esc(m.label) + '</span><strong class="' + (m.value == null ? 'na' : String(m.value).length > 14 ? 'long' : '') + '">' + esc(m.value == null ? 'UNAVAILABLE' : m.value) + '</strong><small>' + esc(m.source) + (m.as_of ? ' · ' + esc(B.ago(m.as_of, t)) : '') + (m.value == null && m.why ? ' · ' + esc(m.why) : '') + '</small></div>';
    }).join('') + '</section>';
  }
  function queue(d) {
    var q = d ? d.queue || [] : null, t = now();
    return card('Work queue', !d ? empty('Waiting for the read.') : q.length ? rows(q, function (x) {
      return '<li><div><b>' + esc(x.title) + '</b><small>' + esc(x.status) + (x.from ? ' · from ' + esc(x.from) : '') + ' · ' + esc(B.ago(x.at, t)) + '</small></div><div class="wsx-ev">' + ev(x) + '</div></li>';
    }) : empty('Nothing queued: no open task or unanswered challenge is recorded for ' + seat.name + '.'), {tag: 'agent_tasks · karen_challenges'});
  }
  function outputs(d) {
    var o = d ? d.outputs || [] : null, t = now();
    return card('Recent decisions & outputs', !d ? empty('Waiting for the read.') : o.length ? rows(o, function (x) {
      return '<li><span class="wsx-verdict">' + esc(x.verdict || '') + '</span><div><b>' + esc(x.summary) + '</b><small>' + esc(B.clock(x.at)) + ' · ' + esc(B.ago(x.at, t)) + '</small></div><div class="wsx-ev">' + ev(x) + '</div></li>';
    }) : empty('No output recorded yet.'), {tag: o && o[0] ? o[0].kind : ''});
  }
  function challenges(d) {
    var c = d && d.challenges, t = now();
    if (!d) return card('Challenges', empty('Waiting for the read.'));
    if (!c) return card('Challenges', empty('Karen’s challenge records are not available on this database.'));
    function list(title, arr, who) {
      return '<h3>' + esc(title) + ' <small>' + arr.length + '</small></h3>' + (arr.length ? rows(arr.slice(0, 8), function (x) {
        return '<li>' + chip(x.state === 'OPEN' ? 'CHALLENGING' : x.state === 'RESPONDED' ? 'WAITING' : x.state === 'UPHELD' ? 'STALE' : 'IDLE', x.state) + '<div><b>' + esc(x.claim) + '</b><small>' + esc(x.severity) + ' · ' + esc(who(x)) + ' · ' + esc(B.ago(x.challenged_at, t)) + (x.response_stance ? ' · answered ' + esc(x.response_stance) : '') + (x.outcome ? ' · ' + esc(x.outcome) : '') + '</small></div><div class="wsx-ev">' + ev({kind: 'karen_challenges', id: x.challenge_id, href: x.href}) + '</div></li>';
      }) : empty('None recorded.'));
    }
    return card('Challenges', (seat.agent === 'KAREN' ? list('Raised', c.given || [], function (x) { return 'against ' + x.target_agent; }) : list('Received from Karen', c.received || [], function (x) { return x.target_kind + ' ' + x.target_id; })) +
      ((c.evaluated || []).length || seat.agent === 'AUDREY' || seat.agent === 'XAVIER' ? list('Evaluated independently', c.evaluated || [], function (x) { return 'against ' + x.target_agent; }) : ''), {tag: 'karen_challenges'});
  }
  function timeline(d) {
    var tl = d ? d.timeline || [] : null, t = now();
    return card('Collaboration timeline · 24 h', !d ? empty('Waiting for the read.') : tl.length ? '<ol class="wsx-tl">' + tl.map(function (e) {
      var from = (B.BY_AGENT[e.from] || {}).name || e.from, to = (B.BY_AGENT[e.to] || {}).name || e.to, out = e.from === seat.agent;
      return '<li class="' + (out ? 'out' : 'in') + '"><span class="wsx-dot" style="--c:' + ((B.BY_AGENT[e.from] || {}).accent || '#7fa') + '"></span><div><b>' + esc(from) + ' → ' + esc(to) + '</b> · ' + esc(B.edgeLabel(e.kind)) + (e.count > 1 ? ' ×' + e.count : '') +
        '<small>' + esc(e.summary || '') + '</small><small>' + esc(B.clock(e.at)) + ' · ' + esc(B.ago(e.at, t)) + ' · ' + (e.evidence || []).slice(0, 3).map(ev).join(' · ') + '</small></div></li>';
    }).join('') + '</ol>' : empty('No recorded collaboration involving ' + seat.name + ' in the last 24 hours.'), {wide: true, tag: 'challenges · loop stages · hand-offs · reviews'});
  }
  function scorecard() {
    var sc = readState(S.score, 'the agent scorecards (/api/command/profitability/scorecards)');
    var tr = readState(S.tour, 'the agent tournaments (/api/command/tournament/agents)');
    var ns = readState(S.north, 'the north-star metrics (/api/command/profitability/north-star)');
    var name = SCORE_NAME[seat.agent] || seat.agent, t = now();
    var body = '<h3>Economic contribution <small>pos-twin scorecards</small></h3>';
    if (sc.status !== 'OK') body += '<p class="wsx-na">' + esc(sc.why) + '</p>';
    else {
      var mine = (sc.data.agents || {})[name] || [];
      body += mine.length ? '<div class="wsx-scroll"><table class="wsx-t"><thead><tr><th>Metric</th><th>Book</th><th class="r">Value</th><th class="r">n</th><th>CI</th><th>Status</th></tr></thead><tbody>' + mine.map(function (m) {
        return '<tr><td>' + esc(m.metric) + '</td><td>' + esc(m.book || '') + '</td><td class="r num">' + (m.value == null ? '<span class="wsx-na">UNAVAILABLE</span>' : esc(m.unit === 'usd' ? money(m.value) : m.value)) + '</td><td class="r num">' + esc(m.sample_n == null ? '—' : m.sample_n) + '</td><td class="num">' + (m.ci_low != null && m.ci_high != null ? esc(m.ci_low + ' … ' + m.ci_high) : '—') + '</td><td>' + esc(m.status || '') + (m.reason ? ' · ' + esc(m.reason) : '') + '</td></tr>';
      }).join('') + '</tbody></table></div><small class="wsx-src">run ' + esc(sc.data.run_id || '') + ' · ' + esc(B.ago(sc.data.computed_at, t)) + ' · books never summed</small>' : '<p class="wsx-na">UNAVAILABLE: the latest scorecard run has no rows for ' + esc(name) + '.</p>';
    }
    body += '<h3>Tournament <small>pos-learn agent tournaments</small></h3>';
    if (tr.status !== 'OK') body += '<p class="wsx-na">' + esc(tr.why) + '</p>';
    else {
      var vs = ((tr.data.data || {}).variants || []).filter(function (v) { return JSON.stringify(v).toUpperCase().indexOf('"' + name) >= 0 || String(v.agent || '').toUpperCase() === name; });
      body += vs.length ? rows(vs.slice(0, 6), function (v) {
        return '<li><div><b>' + esc(v.subject_id || v.registration_id || v.name || 'variant') + '</b><small>' + esc(['status', 'promotion_stage', 'verdict', 'score', 'metric'].filter(function (k) { return v[k] != null; }).map(function (k) { return k.replace(/_/g, ' ') + ' ' + (typeof v[k] === 'object' ? JSON.stringify(v[k]).slice(0, 60) : v[k]); }).join(' · ')) + '</small></div></li>';
      }) : '<p class="wsx-na">UNAVAILABLE: no tournament variant registered for ' + esc(name) + ' in the latest run.</p>';
    }
    body += '<h3>Firm north star <small>context, not attributed to this agent</small></h3>';
    if (ns.status !== 'OK' || !ns.data || ns.data.status !== 'OK') body += '<p class="wsx-na">' + esc(ns.status !== 'OK' ? ns.why : 'UNAVAILABLE: ' + (ns.data && (ns.data.why || ns.data.status))) + '</p>';
    else {
      var books = ns.data.data || {};
      body += '<div class="wsx-books">' + Object.keys(books).map(function (bk) {
        var ms = books[bk] || {};
        return '<div><h4>' + esc(bk) + '</h4>' + (Object.keys(ms).length ? Object.keys(ms).map(function (k) { var m = ms[k]; return '<p><span>' + esc(k.replace(/_/g, ' ').toLowerCase()) + '</span><b class="num">' + (m.value == null ? '<span class="wsx-na">UNAVAILABLE</span>' : esc(m.value)) + '</b><small>' + esc(m.status || '') + (m.sample_n != null ? ' · n ' + esc(m.sample_n) : '') + '</small></p>'; }).join('') : '<p class="wsx-na">No metric for this book.</p>') + '</div>';
      }).join('') + '</div>';
    }
    return card('Scorecard', body, {wide: true, tag: 'RESEARCH · NO AUTHORITY'});
  }
  function allocator() {
    if (seat.agent !== 'CHIEF_ALLOCATOR') return '';
    var al = readState(S.alloc, 'the shadow allocator (/api/command/intel/allocator)'), t = now();
    var body = '';
    if (al.status !== 'OK') body += '<p class="wsx-na">' + esc(al.why) + '</p>';
    else if (!al.data || al.data.status !== 'OK' || !al.data.data) body += '<p class="wsx-na">' + esc((al.data && (al.data.why || al.data.status)) || 'UNAVAILABLE') + '</p>';
    else {
      var r = al.data.data, list = r.allocation || [];
      body += '<div class="wsx-kpis inline">' +
        '<div class="wsx-kpi"><span>Sleeve budget (SHADOW)</span><strong>' + esc(money(r.budget_usd)) + '</strong><small>intel_snapshots · ' + esc(B.ago(al.data.computed_at, t)) + '</small></div>' +
        '<div class="wsx-kpi"><span>Allocated (SHADOW)</span><strong>' + esc(money(r.allocated_usd)) + '</strong><small>of ' + esc(money(r.sleeve_usd)) + ' notional sleeve</small></div>' +
        '<div class="wsx-kpi"><span>Unallocated</span><strong>' + esc(money(r.unallocated_usd)) + '</strong><small>held back by caps / scores</small></div>' +
        '<div class="wsx-kpi"><span>Candidates competing</span><strong>' + esc(r.candidates == null ? '—' : r.candidates) + '</strong><small>new decisions + open positions</small></div></div>' +
        (list.length ? '<div class="wsx-scroll"><table class="wsx-t"><thead><tr><th>#</th><th>Candidate</th><th>Kind</th><th class="r">Score / $</th><th class="r">Shadow $</th><th class="r">Capacity</th><th>Correlation</th><th>Binding constraint</th><th class="r">Opp. cost / $</th></tr></thead><tbody>' + list.slice(0, 15).map(function (c) {
          return '<tr><td class="num">' + esc(c.rank) + '</td><td>' + esc(c.us_market_slug || c.candidate_id) + '<small>' + esc((c.sport || '') + (c.game ? ' · ' + c.game : '')) + '</small></td><td>' + esc(c.candidate_kind === 'OPEN_POSITION' ? 'open position' : 'new decision') + '</td><td class="r num">' + esc(c.score == null ? '—' : Number(c.score).toFixed(4)) + '</td><td class="r num">' + esc(money(c.shadow_usd)) + '</td><td class="r num">' + esc(c.capacity_usd == null ? '—' : money(c.capacity_usd)) + '</td><td>' + esc('same game ' + (c.same_game_open == null ? '—' : c.same_game_open) + ' · same team ' + (c.same_team_open == null ? '—' : c.same_team_open)) + '</td><td>' + esc(c.binding_constraint || '—') + '</td><td class="r num">' + esc(c.opportunity_cost_per_dollar == null ? '—' : Number(c.opportunity_cost_per_dollar).toFixed(4)) + '</td></tr>';
        }).join('') + '</tbody></table></div><small class="wsx-src">Opportunity competition: an open position competes for the same dollar as a new entry. SHADOW weights only — no order, size or limit reads them.</small>' : '<p class="wsx-na">The latest run ranked no candidate.</p>');
    }
    function econ(st, title) {
      if (st.status !== 'OK') return '<h3>' + esc(title) + '</h3><p class="wsx-na">' + esc(st.why) + '</p>';
      var d = st.data || {};
      if (d.status && d.status !== 'OK') return '<h3>' + esc(title) + '</h3><p class="wsx-na">' + esc(d.status + (d.why ? ': ' + d.why : '')) + '</p>';
      var data = d.data || {}, books = ['PAPER', 'ACTUAL', 'COUNTERFACTUAL'].filter(function (b) { return data[b]; });
      if (!books.length && data.aggregate) books = ['aggregate'];
      return '<h3>' + esc(title) + ' <small>pos-econ · ' + esc(B.ago(d.computed_at, t)) + '</small></h3><div class="wsx-books">' + books.map(function (bk) {
        var o = data[bk] || {};
        var flat = Object.keys(o).filter(function (k) { return o[k] == null || typeof o[k] !== 'object'; }).slice(0, 10);
        return '<div><h4>' + esc(bk) + '</h4>' + flat.map(function (k) { var v = o[k]; return '<p><span>' + esc(k.replace(/_/g, ' ')) + '</span><b class="num">' + (v == null ? '<span class="wsx-na">UNAVAILABLE</span>' : esc(/usd$/.test(k) && typeof v === 'number' ? money(v) : v)) + '</b></p>'; }).join('') + '</div>';
      }).join('') + '</div>';
    }
    body += econ(readState(S.capital, 'portfolio capital (/api/command/profitability/capital)'), 'Capital-hour optimisation · portfolio capital per book');
    body += econ(readState(S.capacity, 'deployable capacity (/api/command/profitability/capacity)'), 'Deployable capacity');
    return card('Portfolio ranking & capital', body, {wide: true, tag: 'SHADOW · NEVER SUMMED ACROSS BOOKS'});
  }
  function peers(d) {
    var list = d ? d.peers || [] : [];
    return '<nav class="wsx-peers" aria-label="Other agents">' + B.SEATS.map(function (s) {
      var p = list.filter(function (x) { return x.slug === s.slug; })[0];
      return '<a href="/' + s.slug + '"' + (s.slug === slug ? ' aria-current="page"' : '') + ' style="--a:' + s.accent + '"><span class="wsx-mono sm">' + esc(s.initial) + '</span>' + esc(s.name) + (p ? chip(p.state, (B.STATES[p.state] || {}).short || (B.STATES[p.state] || {}).label) : '') + '</a>';
    }).join('') + '</nav>';
  }

  function draw() {
    var open = [].map.call(root.querySelectorAll('details'), function (x) { return x.open; });
    paint();
    [].forEach.call(root.querySelectorAll('details'), function (x, i) { if (open[i]) x.open = true; });
  }
  function paint() {
    var d = S.lastOk ? S.lastOk.data : null, a = d ? d.agent : null;
    if (cfg.stripOnly) { root.innerHTML = strip(a); return; }
    root.innerHTML = (d && B.isFixture(d) ? '<div class="wsx-fixture">FIXTURE DATA · rendered from a labelled test payload · not production</div>' : '') +
      hero(a) + kpis(a) +
      '<div class="wsx-grid">' + queue(d) + outputs(d) + mandate(a) + challenges(d) + allocator() + timeline(d) + scorecard() + '</div>' + peers(d) +
      '<p class="wsx-foot">Read-only. Every row above is a recorded record with its id and time; nothing here places, cancels or approves anything. The classic desk page — conversation, voice and portrait — is under “Desk &amp; conversation”.</p>';
  }
  function aux(path, key) { B.read(path).then(function (r) { S[key] = r; draw(); }); }

  B.poller('/api/command/floor/' + slug, 20000, function (u) { S.read = u.current; S.lastOk = u.lastOk; draw(); });
  if (!cfg.stripOnly) {
    aux('/api/command/profitability/scorecards?agent=' + encodeURIComponent(SCORE_NAME[seat.agent] || seat.agent), 'score');
    aux('/api/command/tournament/agents', 'tour');
    aux('/api/command/profitability/north-star', 'north');
    if (seat.agent === 'CHIEF_ALLOCATOR') { aux('/api/command/intel/allocator', 'alloc'); aux('/api/command/profitability/capital', 'capital'); aux('/api/command/profitability/capacity', 'capacity'); }
  }
  setInterval(function () { if (!document.hidden) draw(); }, 15000);
  draw();
})();
