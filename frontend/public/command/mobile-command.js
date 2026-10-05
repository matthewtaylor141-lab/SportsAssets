/* BETTOR MOBILE COMMAND — the owner's mobile app (Safari / iPhone / iPad).
 *
 * READ ONLY. Every value comes from a production GET under /api/command/,
 * read by the same field the Operations Desk (ops-desk.js) reads in
 * production. A value the API does not serve renders DATA NOT AVAILABLE with
 * its reason -- never 0, never a default, never an invented agent or event.
 * PAPER is simulated execution on a fictional account; SMALL LIVE is shown as
 * served (SHADOW); the legacy mirror is real money and is shown on its own
 * line, never summed and never labelled PAPER.
 *
 * READS (one in flight per URL, never faster than the desk's minimum for that
 * endpoint -- command-ops.js MIN_MS -- exponential backoff after a failure,
 * a 25 s timeout, paused while the app is hidden or signed out):
 *   /api/command/equity/live          15 s  paper book, SMALL LIVE, legacy mirror lanes
 *   /api/command/floor                30 s  the seven agent seats, Derek's PAPER feed, edges
 *   /api/command/paper/derek?limit=40 30 s  PAPER decisions, fills, 24 h decision summary
 *   /api/command/coverage?days=2     120 s  funnel + per-league coverage, alerts, PinnAPI
 *   /api/command/release              60 s  API / worker SHAs and their alignment
 * /api/command/overview is NOT read: ~25 unbounded statements with no statement
 * timeout; nothing on this page needs it.
 *
 * The pure part (formatting + view models) is exported as window.BTMobile and
 * touches no DOM, so it is tested in node (backend/tests/test_mobile_command_page.py). */
(function (root) {
  'use strict';

  // ── FORMATTING (pure) ─────────────────────────────────────────────
  function esc(x) {
    return String(x == null ? '' : x).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  function fin(n) { return typeof n === 'number' && isFinite(n); }
  function num(v) {
    if (fin(v)) { return v; }
    if (typeof v === 'string' && v.trim() !== '' && isFinite(Number(v))) { return Number(v); }
    return null;
  }
  function epoch(t) {
    if (fin(t)) { return t > 1e12 ? t / 1000 : t; }
    if (typeof t === 'string' && t) {
      if (/^\d+(\.\d+)?$/.test(t)) { return epoch(Number(t)); }
      var x = Date.parse(t);
      return isNaN(x) ? null : x / 1000;
    }
    return null;
  }
  function nowS() { return Date.now() / 1000; }
  function age(s) {
    if (!fin(s)) { return null; }
    s = Math.max(0, s);
    if (s < 60) { return Math.round(s) + 's'; }
    if (s < 3600) { return Math.floor(s / 60) + 'm'; }
    if (s < 86400) { return Math.floor(s / 3600) + 'h ' + Math.floor(s % 3600 / 60) + 'm'; }
    return Math.floor(s / 86400) + 'd ' + Math.floor(s % 86400 / 3600) + 'h';
  }
  function clock(t) { var e = epoch(t); return e == null ? null : new Date(e * 1000).toISOString().slice(11, 19) + 'Z'; }
  function usd(v, d) {
    v = num(v);
    if (v == null) { return null; }
    d = d == null ? 2 : d;
    var s = Math.abs(v).toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d});
    return (v < 0 && Number(s.replace(/,/g, '')) !== 0 ? '−$' : '$') + s;
  }
  function intf(v) { v = num(v); return v == null ? null : Math.round(v).toLocaleString('en-US'); }
  function pp(v) { v = num(v); return v == null ? null : (v > 0 ? '+' : '') + v.toFixed(2) + ' pp'; }
  function short(sha) { return sha ? String(sha).slice(0, 7) : null; }
  function words(s) { return s == null ? null : String(s).replace(/_/g, ' '); }
  var fmt = {esc: esc, fin: fin, num: num, epoch: epoch, age: age, clock: clock, usd: usd, int: intf, pp: pp, short: short};

  var DNA = 'DATA NOT AVAILABLE';

  // ── VIEW MODELS (pure: parsed API bodies in, plain objects out) ───
  /* the PAPER book tiles: equity/live paper.* and paper/derek */
  function kpis(E, D, opts) {
    opts = opts || {};
    var P = E && E.paper, out = [];
    var bookWhy = !E ? (opts.equityWhy || 'equity/live not read') :
      !P ? 'equity/live carried no paper section' :
      P.status === 'UNAVAILABLE' ? ('paper book UNAVAILABLE: ' + (P.why || 'no reason recorded')) : null;
    function paperTile(k, field, raw, sub) {
      var t = {k: k, book: 'PAPER · SIMULATED', sub: sub || null, tone: null};
      if (bookWhy) { t.value = null; t.why = bookWhy; return t; }
      t.value = usd(raw);
      t.why = t.value == null ? 'paper.' + field + ' not served' : null;
      if (P.status === 'STALE') { t.tone = 'warn'; t.sub = (t.sub ? t.sub + ' · ' : '') + 'STALE'; }
      return t;
    }
    var op = (P && P.open_positions) || {}, ex = (P && P.exposure) || {}, m = (P && P.marks_as_of) || {};
    out.push(paperTile('Realized P&L', 'realized_pnl_usd', P && P.realized_pnl_usd, 'ledger sum'));
    // unrealized: marked positions only; an open book with no mark is not a $0 P&L
    var un = paperTile('Unrealized', 'unrealized_pnl_usd', P && P.unrealized_pnl_usd,
      num(op.count) != null ? (intf(op.marked) || '0') + ' of ' + intf(op.count) + ' marked' : null);
    if (!bookWhy && num(op.count) > 0 && !num(op.marked)) { un.value = null; un.why = 'no open position is marked (' + intf(op.count) + ' open)'; }
    var lim = num(m.stale_mark_after_s) || 300, markAge = num(m.newest_age_s);
    if (!bookWhy && un.value != null && markAge != null && markAge > lim) { un.tone = 'warn'; un.sub = (un.sub ? un.sub + ' · ' : '') + 'marks ' + age(markAge) + ' old'; }
    out.push(un);
    out.push(paperTile('Available', 'available_usd', P && P.available_usd,
      P && num(P.reserved_usd) != null ? 'reserved ' + usd(P.reserved_usd) : null));
    out.push(paperTile('Deployed', 'exposure.cost_basis_usd', ex.cost_basis_usd,
      num(op.count) != null ? intf(op.count) + ' open · cost basis' : 'cost basis of open positions'));
    // decisions in the last 24 h: the full count paper/derek serves (refusal_summary_24h)
    var s = D && D.refusal_summary_24h, dec = {k: 'Decisions 24h', book: 'PAPER', sub: null, tone: null};
    if (!D) { dec.value = null; dec.why = opts.derekWhy || 'paper/derek not read'; }
    else if (!s) { dec.value = null; dec.why = 'paper/derek carried no refusal_summary_24h'; }
    else if (s.status === 'OK' || s.status === 'EMPTY') {
      var all = 0, enter = 0;
      (s.data || []).forEach(function (r) { var n = num(r.n) || 0; all += n; if (r.verdict === 'ENTER') { enter += n; } });
      dec.value = intf(all); dec.why = null; dec.sub = 'ENTER ' + intf(enter) + (s.status === 'EMPTY' && s.why ? ' · ' + s.why : '');
    } else { dec.value = null; dec.why = 'refusal_summary_24h ' + s.status + (s.why ? ': ' + s.why : ''); }
    out.push(dec);
    // PAPER fills in the last 24 h, from the newest `limit` fill rows
    var f = D && D.fills, fl = {k: 'Fills 24h', book: 'PAPER · SIMULATED', sub: null, tone: null};
    if (!D) { fl.value = null; fl.why = opts.derekWhy || 'paper/derek not read'; }
    else if (!f) { fl.value = null; fl.why = 'paper/derek carried no fills section'; }
    else if (f.status === 'OK' || f.status === 'EMPTY') {
      var rows = f.data || [], cut = nowS() - 86400;
      var n24 = rows.filter(function (x) { var e = epoch(x.filled_at); return e != null && e >= cut; }).length;
      var capped = !!(opts.limit && rows.length >= opts.limit && n24 === rows.length);
      fl.value = intf(n24) + (capped ? '+' : ''); fl.why = null;
      fl.sub = capped ? 'newest ' + opts.limit + ' rows read' : 'entry fills';
    } else { fl.value = null; fl.why = 'fills ' + f.status + (f.why ? ': ' + f.why : ''); }
    out.push(fl);
    return out;
  }

  /* the mode block: SMALL LIVE as served; never a readiness percentage */
  function mode(E, equityWhy) {
    var SL = E && E.small_live_bettor;
    if (!E) { return {value: null, why: equityWhy || 'equity/live not read', tone: 'warn'}; }
    if (!SL || !SL.status) { return {value: null, why: (SL && SL.why) || 'equity/live carried no SMALL LIVE status', tone: 'warn'}; }
    return {value: String(SL.status), why: SL.why || null, tone: SL.status === 'ACTIVE' ? 'bad' : SL.status === 'SHADOW' ? 'good' : 'warn'};
  }

  /* the funnel: today's provider events through PAPER fills (coverage days[0]);
     a stage unmeasured in a league is not summed as 0 (ops-desk.js dayTotals) */
  var STAGES = [
    {h: 'PROVIDER EVENTS', k: 'provider_events', book: null},
    {h: 'EVENTS MATCHED', k: 'mapped_events', book: null},
    {h: 'EV EVALUATED', k: 'evaluated_events', book: null},
    {h: 'ENTER', k: 'entered_events', book: 'PAPER'},
    {h: 'ORDERS', k: 'ordered_events', book: 'PAPER'},
    {h: 'FILLS', k: 'filled_events', book: 'PAPER'}
  ];
  function funnel(C, covWhy) {
    var day = C && C.days && C.days[0];
    if (!day) { return {day: null, why: C ? 'coverage served no day' + (C.why ? ': ' + C.why : '') : (covWhy || 'coverage not read'), stages: [], partial: false}; }
    var leagues = day.leagues || [], partial = false;
    var stages = STAGES.map(function (s) {
      var sum = 0, measured = 0, miss = [];
      leagues.forEach(function (L) {
        var x = num(L[s.k]);
        if (x == null) { miss.push((L.league_name || L.league) + ': ' + ((L.unavailable || {})[s.k] || 'NULL')); } else { sum += x; measured++; }
      });
      if (miss.length && measured) { partial = true; }
      return {h: s.h, k: s.k, book: s.book, value: measured ? sum : null,
              why: measured ? (miss.length ? 'PARTIAL · unmeasured in ' + miss.length + ': ' + miss.join('; ') : null) : (leagues.length ? 'unmeasured in every league' : 'no league rows today'),
              partial: !!(measured && miss.length)};
    });
    // the largest measured loss between consecutive stages
    var worst = 0, worstI = -1;
    for (var i = 1; i < stages.length; i++) {
      var a = stages[i - 1].value, b = stages[i].value;
      if (a != null && b != null && a - b > worst) { worst = a - b; worstI = i; }
    }
    if (worstI > 0) { stages[worstI].largestLoss = worst; }
    return {day: day.day || null, tz: C.tz || 'America/New_York', why: null, stages: stages, partial: partial, leagues: leagues.length};
  }

  /* the seven agent seats exactly as /api/command/floor serves them */
  var TONE = {WORKING_ON: 'good', REVIEWING: 'good', CHALLENGING: 'good', STALE: 'warn'};
  function agents(F) {
    return ((F && F.agents) || []).map(function (a) {
      var slug = /^[a-z_]+$/.test(a.slug || '') ? a.slug : null;
      var ws = typeof a.workspace === 'string' && /^\/[a-z]+$/.test(a.workspace) ? a.workspace : null;
      return {
        id: a.agent, slug: slug, name: a.display_name || a.agent, title: a.title || null,
        authority: (a.authority && a.authority.level) || null,
        state: a.state || null, tone: TONE[a.state] || null,
        detail: a.state_detail || null, since: epoch(a.state_since),
        portrait: slug ? 'team-demo/assets/models/portraits/' + slug + '.jpg' : null,
        href: ws,
        monitor: (a.monitor || []).slice(0, 3).map(function (mm) {
          return {label: mm.label, value: mm.value == null ? null : String(mm.value), why: mm.why || null};
        })
      };
    });
  }

  /* Derek's latest PAPER decisions on real events (floor feed[]) */
  function feed(F) {
    return ((F && F.feed) || []).map(function (x) {
      return {at: epoch(x.at), market: x.market || null, fixture: x.fixture || null, verdict: x.verdict || null,
              refusal: x.refusal || null, side: x.side || null, limit: num(x.limit_price), p: num(x.p_blended),
              book: x.book || 'PAPER'};
    });
  }

  /* PAPER decisions (paper/derek opportunities.data[]), economics read as the desk reads them */
  function opps(D) {
    var o = D && D.opportunities;
    if (!o) { return {status: null, why: D ? 'paper/derek carried no opportunities' : null, rows: []}; }
    return {status: o.status, why: o.why || null, rows: (o.data || []).map(function (d) {
      var e = d.economics || {}, pd = d.policy_decision || {}, acq = e.acquisition || {}, lab = d.label || {};
      var gross = num(e.best_level_edge_pp); if (gross == null) { gross = num(pd.gross_edge_pp); }
      var net = num(acq.expected_net_profit_usd); if (net == null) { net = num(pd.net_expected_profit_usd); }
      return {at: epoch(d.decided_at), title: lab.event_title || d.us_market_slug || null, competition: lab.competition || null,
              verdict: d.verdict || null, refusal: d.refusal || null, strategy: d.strategy || null, gross: gross, net: net};
    })};
  }

  /* recorded activity: floor edges + Derek's feed, newest first; each time from its record */
  function tape(F) {
    var rows = [];
    ((F && F.edges) || []).forEach(function (e) {
      var at = epoch(e.at); if (at == null) { return; }
      rows.push({at: at, who: (e.from || '?') + ' → ' + (e.to || '?'), what: e.summary || words(e.kind), n: num(e.count)});
    });
    feed(F).forEach(function (x) {
      if (x.at == null) { return; }
      rows.push({at: x.at, who: 'DEREK · PAPER', what: (x.verdict || '') + ' ' + (x.market || '') + (x.refusal ? ' · ' + x.refusal : ''), n: null});
    });
    return rows.sort(function (a, b) { return b.at - a.at; });
  }

  /* per-league coverage, today */
  function coverage(C) {
    var d0 = C && C.days && C.days[0], st = {};
    (((C && C.league_status) || {}).statuses || []).forEach(function (s) { st[s.league] = s; });
    return ((d0 && d0.leagues) || []).map(function (L) {
      var un = L.unavailable || {}, s = st[L.league] || {};
      function cell(k) { var x = num(L[k]); return {value: x, why: x == null ? (un[k] || 'NULL') : null}; }
      return {league: L.league, name: L.league_name || L.league, status: s.status || null, reason: s.reason || null,
              provider: cell('provider_events'), mapped: cell('mapped_events'), evaluated: cell('evaluated_events'),
              filled: cell('filled_events')};
    });
  }

  /* the release identity: API, workers, their alignment -- explicit, never the first N keys */
  function release(R, build) {
    var a = (R && R.api) || {}, w = (R && R.workers) || {}, al = (R && R.alignment) || {}, sc = (R && R.schema) || {};
    var rows = [
      {k: 'API', value: a.short || short(a.sha), why: a.why || 'not served'},
      {k: 'WORKERS', value: w.short || short(w.sha), why: w.why || 'not served'},
      {k: 'ALIGNMENT', value: al.verdict ? al.verdict + (al.matched_how ? ' (' + al.matched_how + ')' : '') : null, why: al.why || 'not served',
       tone: al.verdict === 'ALIGNED' ? 'good' : al.verdict === 'MISALIGNED' ? 'bad' : 'warn'},
      {k: 'SCHEMA', value: sc.max_version != null ? String(sc.max_version) : null, why: sc.why || 'not served'},
      {k: 'AS OF', value: clock(R && R.generated_at), why: 'not served'}
    ];
    if (build) {
      rows.push({k: 'FRONTEND', value: build.sha ? short(build.sha) + (build.source === 'LOCAL' ? ' · LOCAL' : '') : null, why: build.why || 'no build record'});
      if (build.deploy_id) { rows.push({k: 'NETLIFY DEPLOY', value: String(build.deploy_id), why: null}); }
    }
    rows.forEach(function (r) { if (r.value != null) { r.why = null; } });
    return rows;
  }

  /* blockers derived from recorded state (a port of command-ops.js deriveIncidents,
     without /overview); a read that failed is itself a blocker */
  function attention(st) {
    var out = [], read = [], missing = [];
    function input(k, ok) { (ok ? read : missing).push(k); }
    var rel = st.release, eq = st.equity, cov = st.coverage, fl = st.floor;
    input('release', rel && rel.ok); input('equity', eq && eq.ok); input('coverage', cov && cov.ok); input('floor', fl && fl.ok);
    ['release', 'equity', 'coverage', 'floor', 'derek'].forEach(function (k) {
      var r = st[k];
      if (r && !r.ok && r.state !== 'SIGNED_OUT') {
        out.push({sev: k === 'release' ? 'CRITICAL' : 'WARNING', title: k.toUpperCase() + ' READ FAILING', detail: r.why || r.state, at: r.at ? r.at / 1000 : null});
      }
    });
    var R = rel && rel.ok ? rel.data : null;
    if (R) {
      var al = R.alignment || {}, a = R.api || {}, w = R.workers || {};
      if (al.verdict === 'MISALIGNED') { out.push({sev: 'CRITICAL', title: 'API / WORKER SHA MISMATCH', detail: 'API ' + (a.short || short(a.sha) || '?') + ' ≠ WORKERS ' + (w.short || short(w.sha) || '?'), at: epoch(R.generated_at)}); }
      else if (al.verdict === 'UNKNOWN') { out.push({sev: 'WARNING', title: 'API / WORKER SHA NOT COMPARABLE', detail: al.why || 'API or worker SHA unavailable', at: epoch(R.generated_at)}); }
    }
    var P = eq && eq.ok && eq.data && eq.data.paper;
    if (P && (P.status === 'STALE' || P.status === 'UNAVAILABLE')) {
      out.push({sev: P.status === 'UNAVAILABLE' ? 'CRITICAL' : 'WARNING', title: 'PAPER EQUITY ' + P.status, detail: P.why || 'no reason recorded', at: epoch(eq.data.computed_at)});
    }
    var C = cov && cov.ok ? cov.data : null;
    if (C) {
      var sup = C.provider_supplement || {};
      if (sup.status && sup.status !== 'OK') { out.push({sev: 'CRITICAL', title: 'PINNAPI HEARTBEAT NOT CURRENT', detail: (sup.why || 'provider supplement ' + sup.status) + (fin(sup.age_s) ? ' · last beat ' + age(sup.age_s) + ' ago' : ''), at: epoch(C.as_of)}); }
      var coll = (C.league_status || {}).collector || {};
      if (coll.budget_dropped && coll.budget_dropped.length) {
        out.push({sev: 'WARNING', title: 'COLLECTOR BUDGET DROPS', detail: coll.budget_dropped.length + ' competition(s) not requested this cycle: ' + coll.budget_dropped.join(', '), at: epoch(coll.at)});
      }
      var today = C.days && C.days[0] && C.days[0].day, groups = {};
      (C.alerts || []).forEach(function (x) {
        if (today && x.day !== today) { return; }      // the current day only, on the phone
        var k = [x.league, x.kind, x.stage_to].join('|');
        var g = groups[k] || (groups[k] = {a: x, at: null, sev: x.severity});
        var at = epoch(x.detected_at); if (at != null && (g.at == null || at > g.at)) { g.at = at; }
        if (x.severity === 'CRITICAL') { g.sev = 'CRITICAL'; }
      });
      Object.keys(groups).forEach(function (k) {
        var g = groups[k], d = g.a.detail || {};
        out.push({sev: g.sev || 'WARNING', title: 'COVERAGE ' + words(g.a.kind || 'ALERT') + ' · ' + (g.a.league_name || g.a.league), detail: d.statement || ((g.a.stage_from || '?') + ' → ' + (g.a.stage_to || '?')), at: g.at});
      });
    }
    var Fd = fl && fl.ok ? fl.data : null;
    ((Fd && Fd.agents) || []).forEach(function (a) {
      if (a.state !== 'STALE' && a.state !== 'NOT_DEPLOYED') { return; }
      var hb = a.heartbeat || {};
      out.push({sev: a.state === 'STALE' ? 'WARNING' : 'INFO', title: (a.display_name || a.agent) + ' ' + words(a.state),
                detail: a.state === 'STALE' ? 'heartbeat ' + (fin(hb.age_s) ? age(hb.age_s) + ' old' : 'never recorded') : (a.deploy_why || 'not deployed on this API'), at: epoch(hb.at)});
    });
    var rank = {CRITICAL: 0, WARNING: 1, INFO: 2};
    out.sort(function (x, y) { return (rank[x.sev] - rank[y.sev]) || ((y.at || 0) - (x.at || 0)); });
    return {items: out, read: read, missing: missing};
  }

  /* the legacy mirror: real money, its own line, never summed with PAPER */
  function mirror(E) {
    var V = E && E.actual && E.actual.venues;
    if (!V) { return null; }
    var pm = V.polymarket_us || {}, ka = V.kalshi || {};
    return {label: (E.actual && E.actual.label) || 'ACTUAL · LEGACY MIRROR', scale: (E.actual && E.actual.scale) || null,
            rows: [{k: 'Polymarket US', lane: (pm.lane && pm.lane.state) || null, status: pm.status || null, why: pm.why || null},
                   {k: 'Kalshi', lane: (ka.lane && ka.lane.state) || null, status: ka.status || null, why: ka.why || null}]};
  }

  var FEEDS = [
    {k: 'equity', url: '/api/command/equity/live', every: 15000},
    {k: 'floor', url: '/api/command/floor', every: 30000},
    {k: 'derek', url: '/api/command/paper/derek?limit=40', every: 30000, limit: 40},
    {k: 'coverage', url: '/api/command/coverage?days=2', every: 120000},
    {k: 'release', url: '/api/command/release', every: 60000}
  ];

  root.BTMobile = {fmt: fmt, DNA: DNA, FEEDS: FEEDS, STAGES: STAGES,
                   model: {kpis: kpis, mode: mode, funnel: funnel, agents: agents, feed: feed, opps: opps, tape: tape,
                           coverage: coverage, release: release, attention: attention, mirror: mirror}};

  // ═════════════════════════════════════════════════════════════════
  // THE PAGE (DOM). Nothing below runs without the mobile shell.
  // ═════════════════════════════════════════════════════════════════
  var doc = root.document;
  if (!doc || typeof doc.getElementById !== 'function') { return; }

  var S = {screen: 'command', expanded: false, signedOut: false, build: null};
  var F = {};                       // per feed: {res, good, fails, nextAt, inflight}
  FEEDS.forEach(function (f) { F[f.k] = {res: null, good: null, fails: 0, nextAt: 0, inflight: null}; });
  var MAX_BACKOFF_MS = 300000, TIMEOUT_MS = 25000;

  function $(s) { return doc.querySelector(s); }
  function $$(s) { return Array.prototype.slice.call(doc.querySelectorAll(s)); }
  function setHTML(sel, html) { var el = $(sel); if (el && el.innerHTML !== html) { el.innerHTML = html; } }
  function dna(why) { return '<span class="mc-dna" title="' + esc(why || '') + '">' + DNA + '</span>' + (why ? '<small class="mc-why">' + esc(why) + '</small>' : ''); }
  function badge(text, tone) { return text == null ? '' : '<span class="mc-badge' + (tone ? ' ' + tone : '') + '">' + esc(text) + '</span>'; }
  function toast(msg) { var e = $('#mc-toast'); if (!e) { return; } e.textContent = msg; e.classList.add('show'); clearTimeout(toast.t); toast.t = setTimeout(function () { e.classList.remove('show'); }, 2400); }
  function offline() { return !!(root.navigator && root.navigator.onLine === false); }

  function classify(r, body) {
    if (r.status === 401 || r.status === 403) { return {state: 'SIGNED_OUT', why: 'sign-in required (HTTP ' + r.status + ')'}; }
    if (r.status === 404) { return {state: 'NOT_RELEASED', why: 'the serving API has no such route (HTTP 404)'}; }
    var reason = body && body.detail && (body.detail.reason || body.detail.detail);
    return {state: r.status >= 500 ? 'UNAVAILABLE' : 'ERROR', why: 'HTTP ' + r.status + (reason ? ' · ' + reason : '')};
  }

  /* read one feed: one in flight per URL, never faster than its interval (half on a
     forced read), exponential backoff after a failure, a timeout on every request */
  function read(f, force) {
    var e = F[f.k], t = Date.now();
    if (e.inflight) { return e.inflight; }
    if (S.signedOut) { return Promise.resolve(e.res); }
    var last = e.res && (e.res.sent || e.res.at);
    if (last && e.res.ok && t - last < (force ? f.every / 2 : f.every - 500)) { return Promise.resolve(e.res); }
    if (e.fails && t < e.nextAt) { return Promise.resolve(e.res); }
    var sent = t, ctl = typeof AbortController === 'function' ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctl) { ctl.abort(); } }, TIMEOUT_MS);
    e.inflight = fetch(f.url, {method: 'GET', credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}, signal: ctl ? ctl.signal : undefined})
      .then(function (r) {
        return r.text().then(function (txt) {
          var body = null, perr = null;
          try { body = txt ? JSON.parse(txt) : null; } catch (x) { perr = 'the response is not JSON'; }
          if (r.ok && !perr) { return {ok: true, state: 'OK', http: r.status, data: body, why: null}; }
          var c = classify(r, body);
          return {ok: false, state: r.ok ? 'ERROR' : c.state, http: r.status, data: null, why: r.ok ? perr : c.why};
        });
      }, function (err) {
        return {ok: false, state: 'ERROR', http: 0, data: null,
                why: err && err.name === 'AbortError' ? 'no answer within ' + (TIMEOUT_MS / 1000) + ' s' : (offline() ? 'offline' : 'network: the API did not answer')};
      })
      .then(function (res) {
        clearTimeout(timer);
        res.at = Date.now(); res.sent = sent; res.ms = res.at - sent;
        if (res.ok) { e.fails = 0; e.nextAt = 0; e.good = res; }
        else {
          e.fails += 1;
          e.nextAt = res.at + Math.min(MAX_BACKOFF_MS, f.every * Math.pow(2, Math.min(e.fails, 5)));
          if (res.state === 'SIGNED_OUT') { signedOut(); }
        }
        e.res = res; e.inflight = null;
        render();
        return res;
      });
    return e.inflight;
  }
  function tickAll(force) {
    if (doc.hidden) { return Promise.resolve([]); }
    return Promise.all(FEEDS.map(function (f) { return read(f, force); }));
  }
  /* signed out: stop polling until the sign-in reloads the page; open the one sign-in panel */
  function signedOut() {
    if (S.signedOut) { return; }
    S.signedOut = true;
    if (root.BTUnlock && typeof root.BTUnlock.open === 'function') { root.BTUnlock.open(); }
  }

  /* the newest data per feed: the last good body, flagged when the newest read failed */
  function cur(k) {
    var e = F[k];
    return {data: e.good ? e.good.data : null, ok: !!(e.res && e.res.ok), stale: !!(e.good && e.res && !e.res.ok),
            goodAt: e.good ? e.good.at : null, why: e.res && !e.res.ok ? e.res.why : null};
  }
  function readWhy(k) {
    if (S.signedOut) { return 'sign-in required'; }
    if (!F[k].res) { return offline() ? 'offline: no read in this session' : 'reading…'; }
    return cur(k).why || null;
  }
  function staleNote(k) {
    var c = cur(k);
    return c.stale ? '<small class="mc-stale">LAST GOOD READ ' + esc(age((Date.now() - c.goodAt) / 1000)) + ' AGO · ' + esc(c.why || '') + '</small>' : '';
  }
  function guard(name, fn) {
    try { fn(); } catch (err) {
      var el = $('[data-render="' + name + '"]');
      if (el) { el.innerHTML = '<div class="mc-row">' + dna('this panel could not render: ' + (err && err.message || err)) + '</div>'; }
    }
  }

  // ── RENDER (each panel guarded: one panel never stops another or the polling) ──
  function render() {
    guard('strip', renderStrip); guard('kpis', renderKpis); guard('mode', renderMode); guard('funnel', renderFunnel);
    guard('attention', renderAttention); guard('agents', renderAgents); guard('tape', renderTape); guard('live', renderLive);
    guard('opps', renderOpps); guard('coverage', renderCoverage); guard('release', renderRelease); guard('feeds', renderFeeds);
    guard('mirror', renderMirror);
    if (doc.body) { doc.body.classList.toggle('mc-offline', offline()); }
  }

  function renderStrip() {
    var n = FEEDS.length, ok = FEEDS.filter(function (f) { return F[f.k].res && F[f.k].res.ok; }).length;
    var newest = 0; FEEDS.forEach(function (f) { if (F[f.k].good && F[f.k].good.at > newest) { newest = F[f.k].good.at; } });
    var E = cur('equity').data, P = E && E.paper, SL = E && E.small_live_bettor, R = cur('release').data;
    var al = R && R.alignment && R.alignment.verdict, bits = [];
    if (S.signedOut) { bits.push('<span><i class="mc-dot mc-dot-warn"></i>SIGN-IN REQUIRED</span>'); }
    else if (offline()) { bits.push('<span><i class="mc-dot mc-dot-warn"></i>OFFLINE</span>'); }
    else { bits.push('<span><i class="mc-dot ' + (ok === n ? 'mc-dot-live' : ok ? 'mc-dot-warn' : 'mc-dot-off') + '"></i>' + ok + '/' + n + ' feeds</span>'); }
    bits.push('<span>PAPER ' + esc(P ? ((P.lane && P.lane.state ? P.lane.state + ' · ' : '') + (P.status || '—')) : '—') + '</span>');
    bits.push('<span>SMALL LIVE ' + esc(SL && SL.status ? SL.status : '—') + '</span>');
    if (R) {
      bits.push('<span' + (al === 'MISALIGNED' ? ' class="mc-strip-bad"' : '') + '>API ' + esc((R.api && (R.api.short || short(R.api.sha))) || '—') +
        ' · WORKERS ' + esc((R.workers && (R.workers.short || short(R.workers.sha))) || '—') + (al === 'ALIGNED' ? ' ✓' : al === 'MISALIGNED' ? ' MISMATCH' : '') + '</span>');
    }
    bits.push('<span>' + (newest ? 'read ' + esc(clock(newest / 1000)) : '—') + '</span>');
    setHTML('#mc-system-strip', bits.join(''));
  }

  function tileHTML(t) {
    return '<div class="mc-kpi"' + (t.tone ? ' data-tone="' + t.tone + '"' : '') + '><small>' + esc(t.k) + '</small>' +
      (t.value != null ? '<strong>' + esc(t.value) + '</strong>' : '<strong class="mc-kpi-dna">' + dna(t.why) + '</strong>') +
      '<em>' + esc(t.book) + (t.sub ? ' · ' + esc(t.sub) : '') + '</em></div>';
  }
  function renderKpis() {
    var e = cur('equity'), d = cur('derek');
    var ts = kpis(e.data, d.data, {equityWhy: readWhy('equity'), derekWhy: readWhy('derek'), limit: 40});
    setHTML('#mc-kpis', ts.map(tileHTML).join('') + (e.stale || d.stale ? '<div class="mc-kpi-note">' + staleNote(e.stale ? 'equity' : 'derek') + '</div>' : ''));
  }
  function renderMode() {
    var m = mode(cur('equity').data, readWhy('equity')), el = $('#mc-readiness');
    if (!el) { return; }
    el.setAttribute('data-tone', m.tone || '');
    el.title = m.why || '';
    setHTML('#mc-readiness', '<span>SMALL LIVE</span>' + (m.value != null ? '<b>' + esc(m.value) + '</b>' : '<b class="mc-mode-dna">' + DNA + '</b>') + (m.why ? '<small>' + esc(m.why) + '</small>' : ''));
  }
  function renderFunnel() {
    var fu = funnel(cur('coverage').data, readWhy('coverage')), head = $('#mc-funnel-day');
    if (head) { head.textContent = fu.day ? fu.day + ' · ' + (fu.tz || '') + ' · provider events' + (fu.partial ? ' · PARTIAL' : '') : 'today · provider events'; }
    if (!fu.stages.length) { setHTML('#mc-funnel', '<div class="mc-row">' + dna(fu.why) + '</div>'); return; }
    setHTML('#mc-funnel', fu.stages.map(function (s) {
      return '<div class="mc-funnel-step' + (s.largestLoss ? ' is-loss' : '') + '"' + (s.why ? ' title="' + esc(s.why) + '"' : '') + '><small>' + esc(s.h) + (s.book ? ' · ' + s.book : '') + '</small>' +
        (s.value != null ? '<b>' + esc(intf(s.value)) + (s.partial ? '<sup>*</sup>' : '') + '</b>' : '<b class="mc-step-dna">N/A</b>') +
        (s.largestLoss ? '<em>largest loss −' + esc(intf(s.largestLoss)) + '</em>' : '') + '</div>';
    }).join('') + staleNote('coverage'));
  }
  function renderAttention() {
    var st = {};
    ['release', 'equity', 'coverage', 'floor', 'derek'].forEach(function (k) { var e = F[k]; st[k] = e.res ? {ok: e.res.ok, state: e.res.state, why: e.res.why, at: e.res.at, data: e.good ? e.good.data : null} : null; });
    var a = attention(st), be = $('#mc-incident-count');
    if (be) {
      be.textContent = a.read.length ? String(a.items.length) : '—';
      be.className = 'mc-badge' + (a.items.some(function (x) { return x.sev === 'CRITICAL'; }) ? ' bad' : a.items.length ? ' warn' : a.read.length ? ' good' : '');
    }
    if (!a.items.length) {
      setHTML('#mc-attention', '<div class="mc-row"><b>' + (a.read.length ? 'No blocker in ' + a.read.length + ' of 4 reads' : 'UNKNOWN · no read yet') + '</b><p>' +
        (a.missing.length ? 'Not read: ' + esc(a.missing.join(', ')) + '. ' : '') + 'Strategy gates and refusals are on the desktop Operations Desk.</p></div>');
      return;
    }
    setHTML('#mc-attention', a.items.slice(0, 8).map(function (x) {
      return '<div class="mc-row" data-sev="' + esc(x.sev) + '"><div class="mc-row-head"><b>' + esc(x.title) + '</b>' + badge(x.sev, x.sev === 'CRITICAL' ? 'bad' : x.sev === 'WARNING' ? 'warn' : '') + '</div><p>' + esc(x.detail) + (x.at ? ' · ' + esc(clock(x.at)) : '') + '</p></div>';
    }).join('') + (a.items.length > 8 ? '<p class="mc-more"><a href="ops.html#incidents">' + (a.items.length - 8) + ' more on the Operations Desk</a></p>' : ''));
  }
  function portrait(a) {
    return a.portrait ? '<img class="mc-avatar" src="' + esc(a.portrait) + '" alt="" loading="lazy" width="40" height="40">' : '<span class="mc-avatar" aria-hidden="true"></span>';
  }
  function renderAgents() {
    var c = cur('floor'), as = agents(c.data);
    if (!as.length) {
      var why = c.data ? 'the floor served no agent seats' : readWhy('floor');
      ['#mc-agent-strip', '#mc-floor-agents', '#mc-agents'].forEach(function (s) { setHTML(s, '<div class="mc-row">' + dna(why) + '</div>'); });
    } else {
      var since = function (a) { return a.since ? ' · since ' + age(nowS() - a.since) : ''; };
      setHTML('#mc-agent-strip', as.map(function (a) {
        return '<a class="mc-agent-mini" href="' + esc(a.href || '#') + '">' + portrait(a) + '<b>' + esc(a.name) + '</b><small>' + esc(words(a.state) || DNA) + '</small></a>';
      }).join(''));
      setHTML('#mc-floor-agents', as.map(function (a) {
        return '<article class="mc-desk"><div class="mc-desk-head">' + portrait(a) + badge(words(a.state) || DNA, a.tone) + '</div><h3>' + esc(a.name) + '</h3>' +
          (a.title ? '<p>' + esc(a.title) + '</p>' : '') + '<p class="mc-detail">' + esc(a.detail || (DNA + ' · no state detail served')) + esc(since(a)) + '</p>' +
          (a.monitor.length ? '<dl class="mc-monitor">' + a.monitor.map(function (m) { return '<dt>' + esc(m.label) + '</dt><dd>' + (m.value != null ? esc(m.value) : '<span class="mc-dna" title="' + esc(m.why || '') + '">N/A</span>') + '</dd>'; }).join('') + '</dl>' : '') + '</article>';
      }).join('') + staleNote('floor'));
      setHTML('#mc-agents', as.map(function (a) {
        return '<a class="mc-agent-card" href="' + esc(a.href || '#') + '">' + portrait(a) + '<h3>' + esc(a.name) + '</h3>' + (a.title ? '<p>' + esc(a.title) + '</p>' : '') +
          badge(words(a.state) || DNA, a.tone) + (a.authority ? '<p class="mc-authority">' + esc(words(a.authority)) + '</p>' : '') +
          '<p class="mc-detail">' + esc(a.detail || DNA) + '</p></a>';
      }).join(''));
    }
    var w = $('#mc-watch-real'), fd = feed(c.data), newest = fd.length ? fd[0].at : null;
    if (w) { w.classList.toggle('is-live', !!(c.ok && newest && nowS() - newest < 900)); }
    var ws = $('#mc-watch-sub');
    if (ws) { ws.textContent = newest ? 'Newest PAPER decision ' + age(nowS() - newest) + ' ago · tap to open' : (c.data ? 'No PAPER decision in the floor read' : (readWhy('floor') || 'reading…')); }
  }
  function renderTape() {
    var c = cur('floor'), rows = tape(c.data);
    if (!c.data) { setHTML('#mc-tape', '<div class="mc-row">' + dna(readWhy('floor')) + '</div>'); return; }
    if (!rows.length) { setHTML('#mc-tape', '<div class="mc-row"><b>No recorded activity in the floor window.</b><p>Nothing is synthesized.</p></div>'); return; }
    setHTML('#mc-tape', rows.slice(0, S.expanded ? 30 : 6).map(function (x) {
      return '<div class="mc-tape-item"><time datetime="' + esc(new Date(x.at * 1000).toISOString()) + '">' + esc(clock(x.at)) + '</time><div><b>' + esc(x.who) + '</b> · ' + esc(x.what || '') + (x.n > 1 ? ' ×' + esc(intf(x.n)) : '') + '</div></div>';
    }).join('') + staleNote('floor'));
  }
  function renderLive() {
    var c = cur('floor'), fd = feed(c.data);
    if (!c.data) { setHTML('#mc-live-events', '<article class="mc-row">' + dna(readWhy('floor')) + '</article>'); return; }
    if (!fd.length) { setHTML('#mc-live-events', '<article class="mc-row"><b>No PAPER decision in the floor read.</b><p>The app never invents activity.</p></article>'); return; }
    setHTML('#mc-live-events', fd.map(function (x) {
      return '<article class="mc-row"><div class="mc-row-head"><b>' + esc(x.fixture || x.market || DNA) + '</b>' + badge(x.verdict, x.verdict === 'ENTER' ? 'good' : '') + '</div>' +
        '<p>' + esc(clock(x.at) || '') + (x.at ? ' (' + esc(age(nowS() - x.at)) + ' ago)' : '') + ' · ' + esc(x.book) + (x.side ? ' · ' + esc(x.side) : '') +
        (x.limit != null ? ' · limit ' + esc(x.limit.toFixed(3)) : '') + (x.p != null ? ' · p ' + esc(x.p.toFixed(3)) : '') + '</p>' +
        (x.market && x.fixture ? '<p class="mc-mono">' + esc(x.market) + '</p>' : '') + (x.refusal ? '<p>' + esc(x.refusal) + '</p>' : '') + '</article>';
    }).join('') + staleNote('floor'));
  }
  function renderOpps() {
    var c = cur('derek'), o = opps(c.data), cnt = $('#mc-opp-count');
    if (cnt) { cnt.textContent = c.data ? String(o.rows.length) : '—'; }
    if (!c.data) { setHTML('#mc-opportunities', '<article class="mc-row">' + dna(readWhy('derek')) + '</article>'); return; }
    if (!o.rows.length) { setHTML('#mc-opportunities', '<article class="mc-row"><b>' + esc(o.status === 'EMPTY' ? 'No PAPER decision recorded' : DNA) + '</b><p>' + esc(o.why || '') + '</p></article>'); return; }
    setHTML('#mc-opportunities', o.rows.map(function (r) {
      var econ = [];
      if (r.gross != null) { econ.push('gross ' + pp(r.gross)); }
      if (r.net != null) { econ.push('net ' + usd(r.net)); }
      return '<article class="mc-row"><div class="mc-row-head"><b>' + esc(r.title || DNA) + '</b>' + badge(r.verdict, r.verdict === 'ENTER' ? 'good' : '') + '</div>' +
        '<p>' + esc(clock(r.at) || '') + (r.competition ? ' · ' + esc(r.competition) : '') + (r.strategy ? ' · ' + esc(r.strategy) : '') + ' · PAPER</p>' +
        '<p>' + (econ.length ? esc(econ.join(' · ')) : 'economics not served on this decision') + '</p>' + (r.refusal ? '<p>' + esc(r.refusal) + '</p>' : '') + '</article>';
    }).join('') + staleNote('derek'));
  }
  function renderCoverage() {
    var c = cur('coverage'), rows = coverage(c.data);
    if (!c.data) { setHTML('#mc-coverage', '<article class="mc-row">' + dna(readWhy('coverage')) + '</article>'); return; }
    if (!rows.length) { setHTML('#mc-coverage', '<article class="mc-row"><b>No league row today.</b><p>' + esc(c.data.why || '') + '</p></article>'); return; }
    function cellV(x) { return x.value != null ? esc(intf(x.value)) : '<span class="mc-dna" title="' + esc(x.why || '') + '">N/A</span>'; }
    setHTML('#mc-coverage', rows.map(function (r) {
      var tone = r.status === 'HEALTHY' || r.status === 'OK' ? 'good' : r.status ? 'warn' : '';
      return '<article class="mc-sport"><div class="mc-sport-head"><b>' + esc(r.name) + '</b>' + badge(words(r.status) || 'NO STATUS', tone) + '</div>' +
        (r.reason ? '<p class="mc-why">' + esc(r.reason) + '</p>' : '') +
        '<dl><dt>Provider</dt><dd>' + cellV(r.provider) + '</dd><dt>Matched</dt><dd>' + cellV(r.mapped) + '</dd><dt>Evaluated</dt><dd>' + cellV(r.evaluated) + '</dd><dt>PAPER fills</dt><dd>' + cellV(r.filled) + '</dd></dl></article>';
    }).join('') + staleNote('coverage'));
  }
  function renderRelease() {
    var c = cur('release');
    if (!c.data) {
      setHTML('#mc-release', '<span>Status</span><span>' + dna(readWhy('release')) + '</span>' +
        (S.build ? '<span>FRONTEND</span><span>' + (S.build.sha ? esc(short(S.build.sha)) : dna(S.build.why)) + '</span>' : ''));
      return;
    }
    setHTML('#mc-release', release(c.data, S.build).map(function (r) {
      return '<span>' + esc(r.k) + '</span><span' + (r.tone ? ' data-tone="' + r.tone + '"' : '') + '>' + (r.value != null ? esc(r.value) : dna(r.why)) + '</span>';
    }).join(''));
  }
  function renderFeeds() {
    setHTML('#mc-feeds', FEEDS.map(function (f) {
      var e = F[f.k], r = e.res, st = !r ? (S.signedOut ? 'SIGN-IN' : 'READING') : r.ok ? 'OK' : r.state;
      return '<span>' + esc(f.url.replace('/api/command/', '')) + '</span><span data-tone="' + (r && r.ok ? 'good' : r ? 'warn' : '') + '">' + esc(st) +
        (e.good ? ' · ' + esc(age((Date.now() - e.good.at) / 1000)) + ' ago' : '') + ' · every ' + (f.every / 1000) + ' s' +
        (r && !r.ok && r.why ? '<small class="mc-why">' + esc(r.why) + '</small>' : '') + '</span>';
    }).join(''));
  }
  function renderMirror() {
    var e = cur('equity'), m = mirror(e.data);
    if (!m) { setHTML('#mc-mirror', '<span>Status</span><span>' + dna(e.data ? 'equity/live carried no legacy mirror section' : readWhy('equity')) + '</span>'); return; }
    setHTML('#mc-mirror', m.rows.map(function (r) {
      return '<span>' + esc(r.k) + '</span><span>' + esc('lane ' + (r.lane || '—') + ' · account ' + (r.status || '—')) + (r.why ? '<small class="mc-why">' + esc(r.why) + '</small>' : '') + '</span>';
    }).join('') + '<span>Scale</span><span>' + esc(m.scale || '—') + ' · never summed with PAPER</span>');
  }

  // ── NAVIGATION ────────────────────────────────────────────────────
  function screen(n) {
    S.screen = n;
    $$('.mc-screen').forEach(function (x) { x.classList.toggle('is-active', x.getAttribute('data-screen') === n); });
    $$('.mc-tabbar button').forEach(function (x) {
      var on = x.getAttribute('data-tab') === n;
      x.classList.toggle('is-active', on);
      if (on) { x.setAttribute('aria-current', 'page'); } else { x.removeAttribute('aria-current'); }
    });
    root.scrollTo(0, 0);
  }
  function sheet(open) {
    var s = $('#mc-sheet'); if (!s) { return; }
    s.hidden = !open;
    if (open) { var first = s.querySelector('.mc-sheet-panel a, .mc-sheet-panel button'); if (first) { first.focus(); } }
  }
  function install(open) { var s = $('#mc-install'); if (s) { s.hidden = !open; } }
  function refreshNow(show) {
    if (show) { toast(S.signedOut ? 'Sign in to read' : 'Refreshing…'); }
    tickAll(true).then(function () {
      if (!show) { return; }
      var bad = FEEDS.filter(function (f) { return !(F[f.k].res && F[f.k].res.ok); }).length;
      toast(S.signedOut ? 'Sign-in required' : bad ? (FEEDS.length - bad) + ' of ' + FEEDS.length + ' feeds read' : 'Updated');
    });
  }
  function wire() {
    $$('.mc-tabbar button').forEach(function (b) { b.addEventListener('click', function () { screen(b.getAttribute('data-tab')); }); });
    $$('[data-go]').forEach(function (b) { b.addEventListener('click', function () { screen(b.getAttribute('data-go')); }); });
    function on(sel, fn) { var el = $(sel); if (el) { el.addEventListener('click', fn); } }
    on('#mc-watch-real', function () { screen('live'); });
    on('#mc-refresh', function () { refreshNow(true); });
    on('#mc-live-refresh', function () { refreshNow(true); });
    on('#mc-more', function () { sheet(true); });
    $$('[data-sheet-close]').forEach(function (x) { x.addEventListener('click', function () { sheet(false); }); });
    on('#mc-install-help', function () { sheet(false); install(true); });
    $$('[data-install-close]').forEach(function (x) { x.addEventListener('click', function () { install(false); }); });
    on('#mc-tape-expand', function () {
      S.expanded = !S.expanded; renderTape();
      var b = $('#mc-tape-expand'); if (b) { b.textContent = S.expanded ? 'Collapse' : 'History'; }
    });
    doc.addEventListener('keydown', function (e) { if (e.key === 'Escape') { sheet(false); install(false); } });
    // a portrait that fails to load becomes the neutral avatar tile (no letter mark)
    doc.addEventListener('error', function (e) {
      var t = e.target;
      if (t && t.tagName === 'IMG' && t.classList.contains('mc-avatar')) { t.removeAttribute('src'); t.classList.add('mc-avatar-missing'); }
    }, true);
    root.addEventListener('online', function () { render(); tickAll(true); });
    root.addEventListener('offline', render);
    doc.addEventListener('visibilitychange', function () { if (!doc.hidden) { tickAll(true); } });
  }
  function readBuild() {
    var u = (root.location.pathname || '').indexOf('/command/') === 0 ? '/command/build.json' : '/build.json';
    fetch(u, {method: 'GET', cache: 'no-store', credentials: 'same-origin'})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { S.build = j && j.schema === 'bt.frontend.build.v1' ? j : {sha: null, why: 'no build record'}; guard('release', renderRelease); },
            function () { S.build = {sha: null, why: 'build record could not be read'}; guard('release', renderRelease); });
  }
  function registerWorker() {
    var nav = root.navigator;
    if (!nav || !('serviceWorker' in nav)) { return; }
    // the app shell only: scope ./mobile.html, so no other Command page is controlled
    nav.serviceWorker.register('sw-mobile.js', {scope: './mobile.html', updateViaCache: 'none'}).catch(function () { /* no offline shell; the app still reads */ });
  }
  function installHint() {
    var nav = root.navigator || {}, ua = nav.userAgent || '';
    var stand = (root.matchMedia && root.matchMedia('(display-mode: standalone)').matches) || nav.standalone === true;
    var ios = /iPad|iPhone|iPod/.test(ua) || (/Macintosh/.test(ua) && nav.maxTouchPoints > 1);
    if (!ios || stand) { return; }
    var seen = '1';
    try { seen = root.localStorage.getItem('bt-mobile-install-seen'); } catch (e) { seen = '1'; }
    if (seen) { return; }
    setTimeout(function () {
      if (!S.signedOut && !doc.getElementById('command-unlock')) { install(true); }
      try { root.localStorage.setItem('bt-mobile-install-seen', '1'); } catch (e) { /* private mode */ }
    }, 2500);
  }
  function boot() {
    if (!doc.getElementById('mobile-command')) { return; }
    wire();
    render();
    tickAll(false);
    readBuild();
    setInterval(function () { if (!doc.hidden) { tickAll(false); render(); } }, 5000);
    registerWorker();
    installHint();
  }
  if (doc.readyState === 'loading') { doc.addEventListener('DOMContentLoaded', boot); } else { boot(); }
})(typeof window !== 'undefined' ? window : this);
