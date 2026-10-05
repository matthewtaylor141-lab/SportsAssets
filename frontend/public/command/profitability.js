/* BETTOR · MANAGEMENT PROFITABILITY COCKPIT — rendered from the read models.
 *
 * IT ANSWERS, ONLY FROM WHAT THE SERVER SENDS:
 *   Are we making money?  Why?  What opportunities exist now?
 *   How much capital can be deployed?  What blocks more profit?
 *
 * EVERY READ IS AN AUTHENTICATED SAME-ORIGIN GET under /api/command/ that
 * passes BTCore.endpoint(); the only query strings are the fixed literals in
 * READS. A failed read (network, 5xx, non-JSON), an envelope that says
 * UNAVAILABLE or EMPTY, or a null field renders "UNAVAILABLE — <why>" with
 * the server's reason: never 0, never an invented figure. A route that is
 * not deployed (404) says which workstream has not shipped it
 * ("POS-LEARN NOT DEPLOYED"). PAPER and ACTUAL are drawn side by side and
 * never summed. Forecasts carry UNPROVEN until the server says otherwise.
 * Kill-switch entries are RECOMMEND_PAUSE records and nothing on this page
 * is a control: the page writes nothing and contacts no venue.
 *
 * DISPLAY ARITHMETIC IS LIMITED AND LABELLED: the drawdown chart's path is
 * the cumulative sum of the closed positions the /capital read returns (the
 * MAX_DRAWDOWN figure itself is the server's), and chart geometry. Every
 * number carries its source and timestamp on hover.
 *
 * window.BTCockpit.mount(el, {compact: true}) renders the compact executive
 * view for the homepage (answers, hero metrics, today's opportunity, top
 * opportunities, lost opportunities, capital blockers).
 */
(function (root) {
  'use strict';

  var C = root.BTCore || null;
  var POLL_MS = 120000;
  var STALE_S = 2 * 3600;          // the profitability cycle runs hourly
  var STALE_OTHER_S = 24 * 3600;
  var READS = {
    index: {path: '/api/command/profitability', q: '', owner: 'POS-ECON'},
    ns: {path: '/api/command/profitability/north-star', q: '', owner: 'POS-ECON'},
    capital: {path: '/api/command/profitability/capital', q: '?limit=1000', owner: 'POS-ECON'},
    capacity: {path: '/api/command/profitability/capacity', q: '?limit=100', owner: 'POS-ECON'},
    forecast: {path: '/api/command/profitability/forecast', q: '?history=30', owner: 'POS-ECON'},
    lol: {path: '/api/command/profitability/lost-opportunities', q: '?limit=200', owner: 'POS-LOL'},
    scores: {path: '/api/command/profitability/opportunity-scores', q: '?limit=50', owner: 'POS-LOL'},
    horizons: {path: '/api/command/profitability/forecast-horizons', q: '', owner: 'POS-LOL'},
    tmodels: {path: '/api/command/tournament/models', q: '', owner: 'POS-LEARN'},
    tagents: {path: '/api/command/tournament/agents', q: '', owner: 'POS-LEARN'},
    edgeconf: {path: '/api/command/profitability/edge-confidence', q: '', owner: 'POS-LEARN'},
    avoidance: {path: '/api/command/profitability/avoidance', q: '', owner: 'POS-LEARN'},
    twin: {path: '/api/command/twin', q: '', owner: 'POS-TWIN'},
    scorecards: {path: '/api/command/profitability/scorecards', q: '', owner: 'POS-TWIN'},
    ladder: {path: '/api/command/profitability/evidence-ladder', q: '', owner: 'POS-TWIN'},
    kills: {path: '/api/command/profitability/kill-switches', q: '?limit=50', owner: 'POS-TWIN'},
    coverage: {path: '/api/command/coverage', q: '?tz=America/New_York&days=1', owner: 'COVERAGE'},
    overview: {path: '/api/command/overview', q: '', owner: 'COMMAND'},
    floor: {path: '/api/command/floor', q: '', owner: 'FLOOR'},
    p5: {path: '/api/command/p5/evidence', q: '', owner: 'P5'}
  };
  // THE PAPER SLEEVE (migration 223): the cockpit DEFAULTS to the INVESTMENT
  // sleeve. PAPER figures that answer "are we making money" come from
  // /profitability/sleeves for the selected sleeve; training losses are
  // research cost, training wins are not production alpha. COMBINED is the
  // whole paper book (every sleeve), labelled so.
  var SLEEVES = [['INVESTMENT', 'Investment / production-candidate'], ['TRAINING', 'Training / exploration'], ['BENCHMARK', 'Benchmark / control'],
                 ['UNCLASSIFIED', 'Unclassified'], ['COMBINED', 'Combined accounting']];
  var SLEEVE_KEY = 'btpc.sleeve.v1';
  function savedSleeve() {
    try { var v = localStorage.getItem(SLEEVE_KEY); return SLEEVES.some(function (s) { return s[0] === v; }) ? v : 'INVESTMENT'; } catch (e) { return 'INVESTMENT'; }
  }
  READS.sleeves = {path: '/api/command/profitability/sleeves', q: '?sleeve=INVESTMENT', owner: 'POS-SLEEVES'};
  var COMPACT_READS = ['sleeves', 'ns', 'capital', 'capacity', 'lol', 'scores', 'horizons', 'ladder', 'kills'];
  var BOOKS = ['PAPER', 'ACTUAL'];
  var METRICS = [
    ['REALIZED_NET_EDGE', 'Realized net edge', 'ratio'],
    ['PROFIT_PER_CAPITAL_HOUR', 'Profit per capital-hour', 'pch'],
    ['PROB_POSITIVE_ROLLING_30D_PNL', 'P(positive rolling 30-day P&L)', 'prob'],
    ['MAX_DRAWDOWN', 'Max drawdown', 'usd'],
    ['EDGE_CALIBRATION', 'Edge calibration (realized ÷ predicted)', 'x']
  ];

  // ── formatting ──────────────────────────────────────────────────────
  function fin(v) { return typeof v === 'number' && isFinite(v); }
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  function usd(v, d) {
    if (!fin(v)) return null;
    d = d == null ? 2 : d;
    var s = Math.abs(v).toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d});
    return (v < 0 ? '−$' : '$') + s;
  }
  function susd(v, d) { return !fin(v) ? null : (v > 0 ? '+' : '') + usd(v, d); }
  function pct(v, d) { return fin(v) ? (v * 100).toFixed(d == null ? 1 : d) + '%' : null; }
  function numf(v, d) { return fin(v) ? v.toLocaleString('en-US', {minimumFractionDigits: d || 0, maximumFractionDigits: d == null ? 2 : d}) : null; }
  function sig(v) { return fin(v) ? (Math.abs(v) >= 1000 ? numf(v, 0) : Math.abs(v) >= 1 ? numf(v, 2) : v === 0 ? '0' : Number(v.toPrecision(3)).toString()) : null; }
  function fmtHero(v, kind) {
    if (!fin(v)) return null;
    if (kind === 'ratio') return (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(2) + '¢/$';
    if (kind === 'pch') return (v >= 0 ? '+' : '−') + '$' + Math.abs(v).toFixed(4);
    return fmtMetric(v, kind);
  }
  function chartW() { var w = root.innerWidth || 1024; return w < 640 ? 340 : 600; }
  function fmtMetric(v, kind) {
    if (!fin(v)) return null;
    if (kind === 'usd') return usd(v, 2);
    if (kind === 'prob') return pct(v, 1);
    if (kind === 'ratio') return (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(2) + '¢ per $';
    if (kind === 'pch') return (v >= 0 ? '+' : '−') + '$' + Math.abs(v).toFixed(4) + ' /$·h';
    if (kind === 'x') return v.toFixed(2) + '×';
    return sig(v);
  }
  function ts(epoch) {
    if (!fin(epoch)) return '—';
    return new Date(epoch * 1000).toISOString().replace('T', ' ').slice(0, 16) + 'Z';
  }
  function age(epoch) {
    if (!fin(epoch)) return null;
    var s = Date.now() / 1000 - epoch;
    if (s < 0) return 'in the future';
    if (s < 90) return Math.round(s) + 's ago';
    if (s < 5400) return Math.round(s / 60) + 'm ago';
    if (s < 172800) return (s / 3600).toFixed(1) + 'h ago';
    return Math.round(s / 86400) + 'd ago';
  }
  function short(s, n) { s = String(s == null ? '' : s); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
  function human(code) { return String(code || '').replace(/_/g, ' ').toLowerCase().replace(/^./, function (c) { return c.toUpperCase(); }); }

  // ── reads ───────────────────────────────────────────────────────────
  var state = {results: {}, readAt: null, first: true, sleeve: savedSleeve()};
  READS.sleeves.q = '?sleeve=' + state.sleeve;
  function sleeveMode() { return state.sleeve !== 'COMBINED'; }
  function sleeveName(s) { var f = SLEEVES.filter(function (x) { return x[0] === s; })[0]; return f ? f[1] : s; }
  /* the PAPER column's label: the selected sleeve, or every sleeve */
  function bookLabel(b) { return b === 'PAPER' ? 'PAPER · ' + (sleeveMode() ? state.sleeve : 'ALL SLEEVES') : b; }
  function combinedLabel(b) { return b === 'PAPER' ? 'PAPER · ALL SLEEVES' : b; }

  function endpointOf(r) {
    if (C && typeof C.endpoint === 'function') return C.endpoint(r.path) + r.q;
    if (!/^\/api\/command(\/|$)/.test(r.path)) throw new Error('not an allowed read endpoint');
    return r.path + r.q;
  }

  function read(key) {
    var r = READS[key];
    var path;
    try { path = endpointOf(r); } catch (e) {
      state.results[key] = {http: 0, error: 'not an allowed read endpoint'};
      return Promise.resolve();
    }
    return fetch(path, {method: 'GET', credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}})
      .then(function (resp) {
        var http = resp.status;
        return resp.text().then(function (txt) {
          var body = null;
          try { body = txt ? JSON.parse(txt) : null; } catch (e) { body = null; }
          state.results[key] = {http: http, body: body, at: Date.now() / 1000,
                                error: body === null && http < 400 ? 'the response was not JSON' : null};
        });
      })
      .catch(function (e) { state.results[key] = {http: 0, error: 'network: ' + (e && e.message || 'failed'), at: Date.now() / 1000}; });
  }

  /* One read, classified: {state, why, body, computed_at, src}. */
  function env(key) {
    var r = state.results[key];
    var src = READS[key].path;
    if (!r) return {state: 'UNAVAILABLE', why: 'not read yet', src: src};
    if (r.http === 401 || r.http === 403) return {state: 'AUTH', why: 'sign in required (' + r.http + ')', src: src};
    if (r.http === 404) return {state: 'NOT_DEPLOYED', why: READS[key].owner + ' NOT DEPLOYED: ' + src + ' answered 404', src: src};
    if (r.http === 0 || r.http >= 500 || r.error) {
      var d = r.body && (r.body.detail || r.body);
      var why = r.error || (d && (d.reason ? d.reason + (d.detail ? ': ' + d.detail : '') : d.why)) || ('HTTP ' + r.http);
      return {state: 'UNAVAILABLE', why: String(why), src: src, http: r.http};
    }
    if (r.http >= 400) return {state: 'UNAVAILABLE', why: 'HTTP ' + r.http, src: src};
    var b = r.body || {};
    var st = b.status;
    if (st === 'EMPTY' || st === 'UNAVAILABLE') return {state: st, why: b.why || st, body: b, src: src, computed_at: b.computed_at};
    var out = {state: 'OK', why: null, body: b, src: src, computed_at: b.computed_at || b.read_at || b.evaluated_at || null};
    var lim = READS[key].owner.indexOf('POS-') === 0 ? STALE_S : STALE_OTHER_S;
    if (fin(out.computed_at) && Date.now() / 1000 - out.computed_at > lim) {
      out.stale = true;
    }
    return out;
  }

  // ── small renderers ─────────────────────────────────────────────────
  function stPill(s, title) {
    return '<span class="pc-st ' + esc(s) + '"' + (title ? ' data-tip="' + esc(title) + '"' : '') + '>' + esc(String(s).replace(/_/g, ' ')) + '</span>';
  }
  function na(why, label) {
    return '<div class="pc-na"><b>' + esc(label || 'UNAVAILABLE') + '</b>— ' + esc(why || 'no reason given') + '</div>';
  }
  function srcLine(e, extra) {
    var bits = [];
    if (e.stale) bits.push(stPill('STALE', 'computed ' + age(e.computed_at) + '; the cycle runs hourly'));
    bits.push('<span class="mono" data-tip="' + esc('source: GET ' + e.src + (e.computed_at ? '\ncomputed at ' + ts(e.computed_at) : '')) + '">' + esc(e.src.replace('/api/command', '')) + '</span>');
    if (e.computed_at) bits.push('<span>' + esc(age(e.computed_at)) + '</span>');
    if (extra) bits.push(extra);
    return '<span class="src">' + bits.join(' · ') + '</span>';
  }
  function card(id, title, sub, e, body, opts) {
    opts = opts || {};
    return '<section class="card' + (opts.wide ? ' wide' : '') + '" id="pc-' + id + '" aria-labelledby="pc-' + id + '-h">' +
      '<h2 id="pc-' + id + '-h">' + esc(title) + (sub ? ' <span class="sub">' + esc(sub) + '</span>' : '') +
      (opts.pill || '') + (e ? srcLine(e, opts.srcExtra) : '') + '</h2>' +
      '<div class="card-body">' + body + '</div></section>';
  }
  function guard(e, fn, label) {
    if (e.state === 'OK') {
      try { return fn(e.body); } catch (err) { return na('the page could not draw this read: ' + (err && err.message), 'UNAVAILABLE'); }
    }
    return na(e.why, e.state === 'NOT_DEPLOYED' ? (label || 'NOT DEPLOYED') : e.state === 'AUTH' ? 'SIGN IN' : e.state);
  }
  function kv(items) {
    return '<div class="pc-kv">' + items.map(function (it) {
      var v = it[1];
      var tip = it[3] ? ' data-tip="' + esc(it[3]) + '"' : '';
      if (v == null) return '<div' + tip + '><span class="k">' + esc(it[0]) + '</span><span class="v na">UNAVAILABLE</span>' + (it[2] ? '<span class="s">' + esc(it[2]) + '</span>' : '') + '</div>';
      return '<div' + tip + '><span class="k">' + esc(it[0]) + '</span><span class="v">' + esc(v) + '</span>' + (it[2] ? '<span class="s">' + esc(it[2]) + '</span>' : '') + '</div>';
    }).join('') + '</div>';
  }
  function bookHead(b) { return '<h3><span class="pc-sw ' + b.toLowerCase() + '"></span>' + esc(combinedLabel(b)) + '</h3>'; }
  function um(obj, key) { return obj && obj.unmeasured && obj.unmeasured[key]; }

  // ── SVG helpers (drawn to scale; ticks on clean values) ─────────────
  function niceTicks(lo, hi, n) {
    if (!(hi > lo)) { hi = lo + 1; }
    var span = hi - lo, step = Math.pow(10, Math.floor(Math.log10(span / n)));
    var err = (span / n) / step;
    step *= err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1;
    // the ticks ENCLOSE the data: first tick <= lo, last tick >= hi
    var out = [], t = Math.floor(lo / step + 1e-9) * step;
    var end = Math.ceil(hi / step - 1e-9) * step;
    for (; t <= end + step * 1e-6; t += step) out.push(Math.abs(t) < step * 1e-9 ? 0 : t);
    return out;
  }
  function scale(d0, d1, r0, r1) { return function (v) { return d1 === d0 ? (r0 + r1) / 2 : r0 + (v - d0) * (r1 - r0) / (d1 - d0); }; }
  function tipAttr(t) { return ' data-tip="' + esc(t) + '"'; }

  /* CI bar: point + 90% interval on a symmetric axis around 0 (or 0..1). */
  function ciBar(m, kind, tip) {
    var v = m.value, lo = m.ci_low, hi = m.ci_high;
    var W = 260, H = 22, pad = 6;
    var dom;
    if (kind === 'prob') dom = [0, 1];
    else {
      var a = Math.max(Math.abs(v || 0), Math.abs(lo || 0), Math.abs(hi || 0)) * 1.15 || 1;
      dom = kind === 'usd' ? [0, a] : [-a, a];
    }
    var x = scale(dom[0], dom[1], pad, W - pad);
    var s = '<svg class="pc-chart pc-cibar" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + esc(tip) + '">';
    s += '<line class="axis" x1="' + pad + '" x2="' + (W - pad) + '" y1="' + (H / 2) + '" y2="' + (H / 2) + '"/>';
    if (dom[0] < 0) s += '<line class="zero" x1="' + x(0) + '" x2="' + x(0) + '" y1="3" y2="' + (H - 3) + '"/>';
    if (kind === 'x') s += '<line class="ceil" x1="' + x(1) + '" x2="' + x(1) + '" y1="3" y2="' + (H - 3) + '"/>';
    if (fin(lo) && fin(hi)) s += '<line class="ci" x1="' + x(Math.max(dom[0], lo)) + '" x2="' + x(Math.min(dom[1], hi)) + '" y1="' + (H / 2) + '" y2="' + (H / 2) + '"/>';
    if (fin(v)) s += '<circle class="ci-pt" cx="' + x(Math.max(dom[0], Math.min(dom[1], v))) + '" cy="' + (H / 2) + '" r="4.5"/>';
    s += '<rect class="hit" x="0" y="0" width="' + W + '" height="' + H + '"' + tipAttr(tip) + '/></svg>';
    return s;
  }

  /* Horizontal stacked bar of buckets (values >= 0), 2px surface gaps. */
  function stackBar(parts, total, H, label) {
    var W = 600, x = 0;
    var s = '<svg class="pc-chart" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" style="height:' + H + 'px" role="img" aria-label="' + esc(label) + '">';
    if (!(total > 0)) {
      s += '<rect x="0" y="0" width="' + W + '" height="' + H + '" rx="4" class="bar-muted"' + tipAttr(label + ': nothing in any bucket') + '/>';
      return s + '</svg>';
    }
    parts.forEach(function (p) {
      if (!(p.v > 0)) return;
      var w = W * p.v / total;
      s += '<rect x="' + (x + (x > 0 ? 1 : 0)) + '" y="0" width="' + Math.max(1, w - (x > 0 ? 2 : 0)) + '" height="' + H + '" rx="3" class="' + p.cls + '"' + tipAttr(p.tip) + '/>';
      x += w;
    });
    return s + '</svg>';
  }

  // ═══════════════════════════════════════════════════════════════════
  // SECTIONS
  // ═══════════════════════════════════════════════════════════════════

  /* PAPER metrics/capital follow the SELECTED SLEEVE (recomputed on that
     sleeve's own positions); ACTUAL and COMBINED read the book-level runs.
     A failed sleeve read is UNAVAILABLE -- never the combined book. */
  function sleeveData() {
    var e = env('sleeves');
    return e.state === 'OK' ? (e.body.data || {}) : null;
  }
  function nsMetricBook(b, m) {
    var e = env('ns');
    if (e.state !== 'OK') return null;
    return ((e.body.data || {})[b] || {})[m] || null;
  }
  function nsMetric(b, m) {
    if (b === 'PAPER' && sleeveMode()) { var d = sleeveData(); return d && d.north_star ? d.north_star[m] || null : null; }
    return nsMetricBook(b, m);
  }
  function capSnapBook(b) {
    var e = env('capital');
    return e.state === 'OK' ? (e.body.data || {})[b] || null : null;
  }
  function capSnap(b) {
    if (b === 'PAPER' && sleeveMode()) { var d = sleeveData(); return d ? d.capital || null : null; }
    return capSnapBook(b);
  }
  /* why a PAPER / ACTUAL figure is missing, from the read it comes from */
  function whyFor(b, key) {
    if (b === 'PAPER' && sleeveMode()) { var e = env('sleeves'); return e.state === 'OK' ? (e.body.data.north_star_why || 'no ' + state.sleeve + ' observation') : 'sleeve read ' + e.state + ': ' + e.why; }
    return env(key).why;
  }
  function horizon(b, h) {
    var e = env('horizons');
    return e.state === 'OK' ? ((e.body.data || {})[b] || {})[h] || null : null;
  }

  // ── the five questions ──────────────────────────────────────────────
  function answers() {
    var qs = [];
    // 1 Are we making money?
    var money = [];
    var head = 'Not established';
    BOOKS.forEach(function (b) {
      var m = nsMetric(b, 'REALIZED_NET_EDGE');
      var c = capSnap(b);
      var net = c && c.realized_net_profit_usd;
      var line = bookLabel(b) + ': ';
      if (!m) line += (b === 'PAPER' && sleeveMode() ? 'sleeve read: ' + whyFor(b, 'ns') : 'north-star read ' + env('ns').state.toLowerCase());
      else if (m.status === 'MEASURED') {
        line += 'realized net ' + (fin(net) ? susd(net) : 'UNAVAILABLE') + ' (30d, n=' + m.sample_n + '); net edge ' + fmtMetric(m.value, 'ratio') +
          (fin(m.ci_low) ? ', 90% CI ' + fmtMetric(m.ci_low, 'ratio') + ' to ' + fmtMetric(m.ci_high, 'ratio') : '');
        if (fin(m.ci_low) && m.ci_low > 0) head = bookLabel(b) + ': positive, CI above zero';
        else if (head === 'Not established') head = 'Not proven: the CI spans zero';
      } else line += human(m.status) + ' — ' + (m.why || ('n=' + m.sample_n));
      money.push(line);
    });
    qs.push(['Are we making money?', head, money]);
    // 2 Why?
    var why = [];
    var cal = nsMetric('PAPER', 'EDGE_CALIBRATION');
    if (cal && fin(cal.value)) why.push(bookLabel('PAPER') + ' realized ' + cal.value.toFixed(2) + '× of predicted net profit (n=' + cal.sample_n + ', ' + human(cal.status) + ').');
    else why.push('Edge calibration: ' + (cal ? (cal.why || human(cal.status)) : env('ns').why || 'UNAVAILABLE'));
    var lol = env('lol');
    if (lol.state === 'OK') {
      var bc = lol.body.data.summary.by_class;
      why.push('Settled refusals: ' + bc.GOOD_REFUSAL.n + ' good, ' + bc.FALSE_REFUSAL.n + ' false, ' + bc.UNKNOWABLE.n + ' unknowable.');
    } else why.push('Refusal ledger: ' + lol.why);
    qs.push(['Why?', cal && fin(cal.value) ? (cal.value >= 1 ? 'Realized at or above prediction' : 'Realized below prediction') : 'Attribution not measurable yet', why]);
    // 3 Opportunities now
    var cap = env('capacity');
    var opp = [], oppHead = 'UNAVAILABLE';
    if (cap.state === 'OK') {
      var a = cap.body.data;
      oppHead = (a.measured_markets || 0) + ' of ' + (a.markets || 0) + ' markets measurable';
      opp.push('Executable at positive edge: ' + (usd(a.EXECUTABLE_OPPORTUNITY_DOLLARS) || 'UNAVAILABLE (' + um(a, 'EXECUTABLE_OPPORTUNITY_DOLLARS') + ')') + ' conditional on fill; expected ' + (usd(a.EXPECTED_EXECUTABLE_OPPORTUNITY_DOLLARS) || 'UNAVAILABLE') + '.');
    } else opp.push('Capacity: ' + cap.why);
    var sc = env('scores');
    if (sc.state === 'OK') {
      var top = (sc.body.data.rows || []).filter(function (r) { return r.status === 'MEASURED'; })[0];
      opp.push(top ? 'Top score ' + sig(top.opportunity_score) + ' $/$·h on ' + short(top.us_market_slug, 32) + '.' : 'No candidate has a measured score (' + sc.body.data.counts.UNAVAILABLE + ' unavailable).');
    } else opp.push('Opportunity scores: ' + sc.why);
    qs.push(['What opportunities exist now?', oppHead, opp]);
    // 4 Deployable
    var dep = [], depHead = 'UNAVAILABLE';
    if (cap.state === 'OK') {
      var ag = cap.body.data;
      depHead = usd(ag.EXECUTABLE_CAPACITY_USD) ? usd(ag.EXECUTABLE_CAPACITY_USD) + ' executable at positive edge' : 'No measured depth';
      dep.push('Capacity ceiling (break-even size) ' + (usd(ag.CAPACITY_CEILING_USD) || 'UNAVAILABLE') + ' across the measured books.');
    } else dep.push('Capacity: ' + cap.why);
    BOOKS.forEach(function (b) {
      var c = capSnap(b);
      dep.push(bookLabel(b) + ' idle capital ' + (c && fin(c.idle_capital_usd) ? usd(c.idle_capital_usd) : 'UNAVAILABLE' + (c ? ' (' + (um(c, 'idle_capital_usd') || '') + ')' : '')));
    });
    dep.push('The $25 cap, the 1:1,000 scale and every gate are unchanged by this page.');
    qs.push(['How much capital can be deployed?', depHead, dep]);
    // 5 Blockers
    var bl = blockers().slice(0, 3);
    qs.push(['What blocks more profit?', bl.length ? bl[0].title : 'No blocker read', bl.slice(1).map(function (x) { return x.title + (x.detail ? ': ' + x.detail : ''); })]);
    return '<section class="pc-answers" aria-label="The five questions">' + qs.map(function (q) {
      return '<div class="pc-q"><p class="t">' + esc(q[0]) + '</p><p class="a">' + esc(q[1]) + '</p>' +
        q[2].map(function (d) { return '<p class="d">' + esc(d) + '</p>'; }).join('') + '</div>';
    }).join('') + '</section>';
  }

  // ── the sleeve selector + every sleeve's ledger economics ──────────
  function sm(v) { return susd(v, 2) || 'UNAVAILABLE'; }
  function sleevePanel(compact) {
    var e = env('sleeves');
    var bar = '<div class="pc-sleevebar" role="group" aria-label="Paper sleeve shown in the PAPER figures">' +
      '<span class="pc-sleeve-lbl">PAPER economics shown for</span>' + SLEEVES.map(function (s) {
        return '<button type="button" class="pc-sleeve-btn s-' + s[0].toLowerCase() + '" data-sleeve="' + s[0] + '" aria-pressed="' + (state.sleeve === s[0]) + '">' + esc(s[1]) + '</button>';
      }).join('') + '</div>';
    var body;
    if (e.state !== 'OK') {
      body = na((e.state === 'NOT_DEPLOYED' ? 'the sleeve read is not deployed: ' : '') + e.why, e.state === 'NOT_DEPLOYED' ? 'NOT DEPLOYED' : e.state) +
        (sleeveMode() ? '<p class="pc-note">Without the sleeve read the PAPER figures below show UNAVAILABLE rather than the combined book.</p>' : '');
    } else {
      var d = e.body.data || {}, all = d.sleeves || {}, acc = d.accounting || {};
      var order = ['INVESTMENT', 'TRAINING', 'BENCHMARK', 'UNCLASSIFIED'];
      var rows = order.filter(function (k) { return all[k]; }).map(function (k) {
        var s = all[k], ex = s.exposure || {}, mk = s.marks || {};
        var g = mk.last_genuine_mark_update_at ? age(mk.last_genuine_mark_update_at) : 'none in 6 h';
        var tag = s.realized_label ? '<span class="pc-sltag">' + esc(s.realized_label) + '</span>' : '';
        var sel = state.sleeve === k || state.sleeve === 'COMBINED';
        var c = s.equity_contribution_usd;
        return '<tr class="' + (sel ? 'sel ' : '') + 's-' + k.toLowerCase() + '"' + tipAttr(s.title + ' · ' + (s.purpose || '') + '\n' + (s.unrealized_basis || '') + '\nsource ' + (d.live_source || '')) + '>' +
          '<td><span class="pc-slname">' + esc(sleeveName(k)) + '</span></td>' +
          '<td class="num ' + (c > 0 ? 'pos' : c < 0 ? 'neg' : '') + '">' + esc(sm(c)) + '</td>' +
          '<td class="num">' + esc(sm(s.realized_pnl_usd)) + tag + '</td>' +
          '<td class="num">' + esc(sm(s.unrealized_pnl_usd)) + (ex.unmarked_positions ? ' <span class="pc-sltag">' + ex.unmarked_positions + ' unmarked</span>' : '') + '</td>' +
          '<td class="num">' + esc(usd(ex.cost_basis_usd, 2) || 'UNAVAILABLE') + '</td>' +
          '<td class="num">' + esc((s.positions_open || 0) + ' / ' + (s.positions_closed || 0)) + '</td>' +
          '<td>' + esc(human(mk.state || '')) + '<span class="pc-slsub">' + esc(g) + '</span></td></tr>';
      }).join('');
      var cs = acc.sleeve_contributions_usd;
      var recon = '<p class="pc-note pc-acc"><b>' + esc(acc.title || 'COMBINED ACCOUNTING TOTAL') + '</b>: ' + esc(usd(acc.starting_cash_usd, 2) || 'UNAVAILABLE') + ' starting cash ' +
        esc(fin(cs) ? (cs >= 0 ? '+ ' : '− ') + usd(Math.abs(cs), 2) : 'UNAVAILABLE') + ' from the sleeves = ' + esc(usd(acc.accounted_equity_usd, 2) || 'UNAVAILABLE') +
        (acc.reconciled ? ' · reconciles to the account equity' : ' · DIFFERS from the account equity by ' + esc(usd(acc.difference_usd, 2) || 'an unknown amount')) + '.</p>';
      body = (compact ? '' : '<div class="tablewrap pc-scroll"><table class="pc-sleeves"><thead><tr><th>Sleeve</th><th>Equity contribution</th><th>Realized</th><th>Unrealized</th><th>Exposure · cost</th><th>Open / closed</th><th>Marks · last genuine change</th></tr></thead><tbody>' + rows + '</tbody></table></div>') +
        recon + '<p class="pc-note">' + esc((d.equity_method || {}).text || '') + ' Training losses are research cost; training wins are not production alpha. ' +
        (sleeveMode() ? 'The answers, hero tiles and north-star PAPER column show the ' + sleeveName(state.sleeve).toUpperCase() + ' sleeve, recomputed on its own positions; capital, drawdown, forecast and capacity cards are book-level (all sleeves) and say so.' : 'COMBINED shows the whole paper book: every sleeve, including training research cost.') + '</p>';
    }
    return card('sleeves', 'Paper sleeves', 'economic purpose · ' + (sleeveMode() ? sleeveName(state.sleeve) + ' selected' : 'all sleeves'), e.state === 'OK' ? e : null, bar + body, {wide: true});
  }

  // ── hero tiles ──────────────────────────────────────────────────────
  function bk(b, valText, why, tip, small, pill, label) {
    return '<div class="pc-bk"' + (tip ? tipAttr(tip) : '') + '><span class="k"><span class="pc-sw ' + b.toLowerCase() + '"></span>' + esc(label || b) + (pill ? ' ' + pill : '') + '</span>' +
      (valText != null ? '<span class="v' + (small ? ' sm' : '') + '">' + esc(valText) + '</span>' + (why ? '<span class="why">' + esc(why) + '</span>' : '')
                       : '<span class="na">UNAVAILABLE</span><span class="why">' + esc(why || '') + '</span>') + '</div>';
  }
  function tile(label, inner, foot, one) {
    return '<div class="pc-tile"><span class="lbl">' + esc(label) + '</span><div class="books' + (one ? ' one' : '') + '">' + inner + '</div>' +
      (foot ? '<div class="foot">' + foot + '</div>' : '') + '</div>';
  }
  function metricTile(label, metric, kind) {
    var e = env('ns');
    var inner = BOOKS.map(function (b) {
      var m = nsMetric(b, metric);
      var L = bookLabel(b);
      var srcPath = b === 'PAPER' && sleeveMode() ? '/api/command/profitability/sleeves?sleeve=' + state.sleeve + ' (recomputed on this sleeve\'s positions)' : '/api/command/profitability/north-star';
      if (!m) return bk(b, null, whyFor(b, 'ns'), null, false, null, L);
      var tip = label + ' · ' + L + '\nstatus ' + m.status + ' · n=' + m.sample_n +
        (fin(m.ci_low) ? '\n90% CI ' + fmtMetric(m.ci_low, kind) + ' to ' + fmtMetric(m.ci_high, kind) : '') +
        '\nsource GET ' + srcPath + '\ncomputed ' + ts(m.computed_at) + '; data as of ' + ts(m.data_as_of);
      var pill = stPill(m.status || 'UNAVAILABLE');
      if (!fin(m.value)) return bk(b, null, m.why || m.status, tip, false, pill, L);
      return bk(b, fmtHero(m.value, kind), (kind === 'pch' ? 'per $·h · ' : '') + 'n=' + m.sample_n +
        (fin(m.ci_low) ? ' · 90% CI ' + fmtMetric(m.ci_low, kind) + ' to ' + fmtMetric(m.ci_high, kind) : ' · CI n/a'), tip, false, pill, L);
    }).join('');
    return tile(label, inner, e.state === 'OK' ? 'north-star · ' + esc(age(e.computed_at) || '') : esc(e.state));
  }
  function heroTiles() {
    var t = [];
    // Net P&L (30d realized, per book)
    var ec = env('capital');
    t.push(tile('Net P&L · realized, 30-day window', BOOKS.map(function (b) {
      var c = capSnap(b);
      var L = bookLabel(b), sl = b === 'PAPER' && sleeveMode();
      if (!c) return bk(b, null, sl ? whyFor(b, 'capital') : ec.state === 'OK' ? 'no CAPITAL snapshot for ' + b : ec.why, null, false, null, L);
      var v = c.realized_net_profit_usd;
      var lossNote = sl && state.sleeve === 'TRAINING' && fin(v) ? (v < 0 ? ' · RESEARCH COST' : v > 0 ? ' · not production alpha' : '') : '';
      return bk(b, fin(v) ? susd(v) : null, fin(v) ? 'n=' + (c.realized_sample || 0) + ' closed · ' + (age(c.computed_at) || '') + lossNote : um(c, 'realized_net_profit_usd'),
        'Realized net P&L · ' + L + ' · positions released in the ' + (c.window_days || 30) + '-day window\nsource GET ' + (sl ? '/api/command/profitability/sleeves?sleeve=' + state.sleeve : '/api/command/profitability/capital') + '\ncomputed ' + ts(c.computed_at), false, stPill(fin(v) ? 'MEASURED' : 'UNAVAILABLE'), L);
    }).join(''), 'capital · never summed across books'));
    t.push(metricTile('Realized net edge', 'REALIZED_NET_EDGE', 'ratio'));
    t.push(metricTile('Profit per capital-hour', 'PROFIT_PER_CAPITAL_HOUR', 'pch'));
    // Capital turnover (trailing 30d, measured)
    var eh = env('horizons');
    t.push(tile('Capital turnover · trailing 30 days', BOOKS.map(function (b) {
      var h = horizon(b, '30D');
      if (!h) return bk(b, null, eh.state === 'OK' ? 'no horizon row for ' + b : eh.why, null, false, null, combinedLabel(b));
      var v = h.trailing_30d_capital_turnover;
      return bk(b, fin(v) ? numf(v, 1) + '×' : null, fin(v) ? usd(h.trailing_30d_committed_usd, 0) + ' committed' : um(h, 'trailing_30d_capital_turnover'),
        'Capital turnover · ' + combinedLabel(b) + ' (book-level: not split by sleeve)\ncommitted ÷ average locked capital, trailing 30 days (measured, not forecast)\nsource GET /api/command/profitability/forecast-horizons\nissued ' + ts(h.issued_at), false, null, combinedLabel(b));
    }).join(''), 'committed ÷ average locked capital'));
    t.push(metricTile('Max drawdown · 30d', 'MAX_DRAWDOWN', 'usd'));
    t.push(metricTile('P(positive rolling 30 days)', 'PROB_POSITIVE_ROLLING_30D_PNL', 'prob'));
    // Deployable capacity (book-agnostic, from PAPER decisions' recorded books)
    var cap = env('capacity');
    var capInner;
    if (cap.state === 'OK') {
      var a = cap.body.data;
      capInner = '<div class="pc-bk"' + tipAttr('Executable capacity at positive marginal edge, summed over the latest MEASURED candidate per market\nceiling = break-even size\nsource GET /api/command/profitability/capacity\ncomputed ' + ts(cap.computed_at)) + '><span class="k">Executable · ceiling</span>' +
        (fin(a.EXECUTABLE_CAPACITY_USD) ? '<span class="v">' + esc(usd(a.EXECUTABLE_CAPACITY_USD)) + '</span><span class="why">ceiling ' + esc(usd(a.CAPACITY_CEILING_USD) || 'UNAVAILABLE') + ' · ' + a.measured_markets + ' of ' + a.markets + ' markets</span>'
                                       : '<span class="na">UNAVAILABLE</span><span class="why">' + esc(um(a, 'EXECUTABLE_CAPACITY_USD') || '') + '</span>') + '</div>';
    } else capInner = '<div class="pc-bk"><span class="na">' + esc(cap.state) + '</span><span class="why">' + esc(cap.why) + '</span></div>';
    t.push(tile('Deployable capacity', capInner, 'recorded books at decision · PAPER candidates', true));
    // Evidence level
    var lad = env('ladder');
    var lv;
    if (lad.state === 'OK') {
      var L = lad.body;
      var lvName = ((L.data || {}).levels || [])[L.level];
      lv = '<div class="pc-bk"' + tipAttr('Profitability evidence ladder (0–6), predeclared criteria; never skips a level\nsource GET /api/command/profitability/evidence-ladder\ncomputed ' + ts(L.computed_at)) + '><span class="k">Level of 6</span><span class="v">' + esc(L.level) + '</span><span class="why">' + esc(lvName ? human(lvName.name) : '') + ' · confidence ' + esc(human(L.confidence_status || 'UNAVAILABLE')) + '</span></div>';
    } else lv = '<div class="pc-bk"><span class="na">' + esc(lad.state === 'NOT_DEPLOYED' ? 'POS-TWIN NOT DEPLOYED' : lad.state) + '</span><span class="why">' + esc(lad.why) + '</span></div>';
    t.push(tile('Evidence level', lv, 'digital twin · evidence ladder', true));
    return '<section class="pc-hero" aria-label="Hero metrics">' + t.join('') + '</section>';
  }

  // ── north star (per book, side by side) ─────────────────────────────
  function northStar() {
    var e = env('ns');
    var body = guard(e, function (b) {
      var rows = METRICS.map(function (mm) {
        var cells = BOOKS.map(function (bk_) {
          var m = nsMetric(bk_, mm[0]);
          if (!m) return '<td>' + na(bk_ === 'PAPER' && sleeveMode() ? whyFor(bk_, 'ns') : 'no observation for ' + bk_) + '</td>';
          var tip = mm[1] + ' · ' + bk_ + '\n' + (fin(m.value) ? 'value ' + fmtMetric(m.value, mm[2]) : 'no value') +
            (fin(m.ci_low) ? '\n' + Math.round((m.ci_level || 0.9) * 100) + '% CI ' + fmtMetric(m.ci_low, mm[2]) + ' to ' + fmtMetric(m.ci_high, mm[2]) : (m.ci_why ? '\nno CI: ' + m.ci_why : '')) +
            '\nn=' + m.sample_n + ' · period ' + ts(m.period_start) + ' → ' + ts(m.period_end) +
            '\nsource GET /api/command/profitability/north-star\ncomputed ' + ts(m.computed_at);
          var fr = m.freshness || {};
          var trend = m.trend || {};
          return '<td><div class="cell"><div class="top"><span class="val">' + esc(fin(m.value) ? fmtMetric(m.value, mm[2]) : '—') + '</span>' + stPill(m.status, m.why || null) +
            '<span class="meta">n=' + esc(m.sample_n) + '</span></div>' +
            (fin(m.value) ? ciBar(m, mm[2], tip) : '<span class="meta">' + esc(m.why || '') + '</span>') +
            '<span class="meta">trend ' + esc(human(trend.direction || 'UNAVAILABLE')) + (trend.why ? ' (' + esc(human(trend.why)) + ')' : '') +
            ' · data ' + esc(fr.data_as_of ? age(fr.data_as_of) : (fr.why ? human(fr.why) : '—')) + '</span></div></td>';
        }).join('');
        var unit = ((((b.data || {}).PAPER || {})[mm[0]]) || {}).unit || '';
        return '<tr><td><span class="mname">' + esc(mm[1]) + '</span><span class="munit">' + esc(unit) + '</span></td>' + cells + '</tr>';
      }).join('');
      return '<div class="tablewrap pc-scroll"><table class="pc-ns"><thead><tr><th>Metric</th><th><span class="pc-sw paper"></span> ' + esc(bookLabel('PAPER')) + '</th><th><span class="pc-sw actual"></span> ACTUAL</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
        '<p class="pc-note">The bar is the 90% interval with the point estimate; the vertical tick is zero (or 1.0× for calibration). MEASURED needs ≥ 30 closed positions per book (and 30 days for the rolling probability). PAPER and ACTUAL are separate rows of the same table, never added.</p>';
    });
    return card('ns', 'The five north-star metrics', 'per book · sample · 90% CI · status · trend · freshness', e, body, {wide: true});
  }

  // ── capital-hours & turnover ────────────────────────────────────────
  var BUCKETS = [['OVERDUE', 'Overdue', 'bar-warn'], ['WITHIN_6H', '≤ 6 h', 'bar-1'], ['WITHIN_24H', '≤ 24 h', 'bar-2'],
                 ['WITHIN_72H', '≤ 72 h', 'bar-3'], ['LATER', 'Later', 'bar-4'], ['UNKNOWN', 'Unknown', 'bar-muted']];
  function capital() {
    var e = env('capital');
    var body = guard(e, function (b) {
      var books = BOOKS.map(function (bk_) {
        var c = (b.data || {})[bk_];
        if (!c) return '<div class="pc-book">' + bookHead(bk_) + na('no CAPITAL snapshot for ' + bk_ + ' yet') + '</div>';
        var tipB = ' · ' + bk_ + '\nsource GET /api/command/profitability/capital\ncomputed ' + ts(c.computed_at);
        var rs = c.release_schedule || {};
        var tot = BUCKETS.reduce(function (s, k) { return s + ((rs[k[0]] || {}).capital_usd || 0); }, 0);
        var parts = BUCKETS.map(function (k) {
          var x = rs[k[0]] || {};
          return {v: x.capital_usd || 0, cls: k[2], tip: k[1] + ' · ' + bk_ + '\n' + usd(x.capital_usd || 0) + ' in ' + (x.positions || 0) + ' positions\n' + (c.release_basis || um(c, 'release_basis') || '')};
        });
        return '<div class="pc-book">' + bookHead(bk_) + kv([
          ['Locked', usd(c.capital_locked_usd), c.capital_locked_basis ? human(c.capital_locked_basis) : um(c, 'capital_locked_usd'), 'Capital locked' + tipB],
          ['Idle', usd(c.idle_capital_usd), um(c, 'idle_capital_usd') || 'account − locked', 'Idle capital' + tipB],
          ['Account capital', usd(c.account_capital_usd), c.account_capital_basis || um(c, 'account_capital_usd'), 'Account capital' + tipB],
          ['Utilization', fin(c.capital_utilization) ? pct(c.capital_utilization, 3) : null, um(c, 'capital_utilization') || 'capital-hours ÷ (capital × hours)', 'Capital utilization' + tipB],
          ['Capital-hours · 30d', fin(c.capital_hours_in_window) ? numf(c.capital_hours_in_window, 0) + ' $·h' : null, um(c, 'capital_hours_in_window'), 'Capital-hours in the window' + tipB],
          ['Waiting settlement', usd(c.capital_waiting_for_settlement_usd), um(c, 'capital_waiting_for_settlement_usd'), 'Capital waiting for settlement' + tipB],
          ['Overdue settlement', usd(c.capital_overdue_settlement_usd), um(c, 'capital_overdue_settlement_usd'), 'Capital overdue settlement' + tipB],
          ['Realized $/$·h', fin(c.REALIZED_PROFIT_PER_CAPITAL_HOUR) ? fmtMetric(c.REALIZED_PROFIT_PER_CAPITAL_HOUR, 'pch') : null, um(c, 'REALIZED_PROFIT_PER_CAPITAL_HOUR'), 'Realized profit per capital-hour' + tipB]
        ]) + '<div class="pc-note" style="margin:0 0 4px">Release schedule · ' + esc(usd(tot) || '$0.00') + ' locked</div>' +
          stackBar(parts, tot, 14, 'Release schedule ' + bk_) + releaseTimeline(c, bk_) + '</div>';
      }).join('');
      var cf = (b.data || {}).COUNTERFACTUAL;
      return '<div class="pc-twobook">' + books + '</div>' +
        '<div class="pc-legend">' + BUCKETS.map(function (k) { return '<span><span class="pc-sw" style="background:var(' + ({'bar-warn': '--dk-warn', 'bar-1': '--pc-ramp-1', 'bar-2': '--pc-ramp-2', 'bar-3': '--pc-ramp-3', 'bar-4': '--pc-ramp-4', 'bar-muted': '--pc-muted-fill'})[k[2]] + ')"></span>' + esc(k[1]) + '</span>'; }).join('') + '</div>' +
        (cf ? '<p class="pc-note">COUNTERFACTUAL (hold to settlement) is its own book: ' + esc(cf.positions != null ? cf.positions + ' positions' : 'see /capital') + ' — never added to PAPER or ACTUAL.</p>' : '');
    });
    return card('capital', 'Capital-hours & turnover', 'locked · release schedule · idle', e, body, {wide: true});
  }
  function releaseTimeline(c, bk_) {
    var rel = (c.next_releases || []).filter(function (r) { return fin(r.expected_release_at); });
    var now = Date.now() / 1000, span = 72 * 3600;
    var W = chartW(), H = 46, pad = 8;
    var x = scale(now, now + span, pad, W - pad);
    var s = '<svg class="pc-chart" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Next expected releases, ' + bk_ + '">';
    s += '<line class="axis" x1="' + pad + '" x2="' + (W - pad) + '" y1="20" y2="20"/>';
    [0, 6, 24, 48, 72].forEach(function (h) {
      var xx = x(now + h * 3600);
      s += '<line class="grid" x1="' + xx + '" x2="' + xx + '" y1="14" y2="26"/><text x="' + xx + '" y="40" text-anchor="' + (h === 0 ? 'start' : h === 72 ? 'end' : 'middle') + '">' + (h === 0 ? 'now' : '+' + h + 'h') + '</text>';
    });
    var maxc = Math.max.apply(null, rel.map(function (r) { return r.capital_usd || 0; }).concat([1]));
    rel.forEach(function (r) {
      var t = Math.min(Math.max(r.expected_release_at, now), now + span);
      var rr = 4 + 5 * Math.sqrt((r.capital_usd || 0) / maxc);
      s += '<circle class="dot-' + bk_.toLowerCase() + '" cx="' + x(t) + '" cy="20" r="' + rr.toFixed(1) + '"' + tipAttr(bk_ + ' release ' + (r.expected_release_at < now ? '(overdue) ' : '') + ts(r.expected_release_at) + '\n' + usd(r.capital_usd) + '\n' + r.position_key + '\nsource GET /api/command/profitability/capital') + '/>';
    });
    if (!rel.length) s += '<text x="' + (W / 2) + '" y="12" text-anchor="middle">no expected release recorded</text>';
    return s + '</svg>';
  }

  // ── deployable capacity: edge at size ───────────────────────────────
  var capSel = 0;
  function capacity() {
    var e = env('capacity');
    var body = guard(e, function (b) {
      var a = b.data || {};
      var cands = (b.candidates || []).filter(function (c) { return c.status === 'MEASURED' && Array.isArray(c.EXPECTED_EDGE_AT_SIZE); })
        .sort(function (x, y) { return (y.EXECUTABLE_OPPORTUNITY_DOLLARS || 0) - (x.EXECUTABLE_OPPORTUNITY_DOLLARS || 0); }).slice(0, 6);
      var tipA = '\nsource GET /api/command/profitability/capacity\ncomputed ' + ts(e.computed_at);
      var rates = a.rates || {};
      var fp = rates.fill_probability || {};
      var head = kv([
        ['Theoretical', usd(a.THEORETICAL_OPPORTUNITY_DOLLARS), 'max(0, p − best) × visible', 'Theoretical opportunity (headline)' + tipA],
        ['Executable', usd(a.EXECUTABLE_OPPORTUNITY_DOLLARS), 'net of fees, conditional on fill', 'Executable opportunity' + tipA],
        ['Expected executable', usd(a.EXPECTED_EXECUTABLE_OPPORTUNITY_DOLLARS), um(a, 'EXPECTED_EXECUTABLE_OPPORTUNITY_DOLLARS') || '× fill probability', 'Expected executable opportunity' + tipA],
        ['Capacity ceiling', usd(a.CAPACITY_CEILING_USD), 'break-even size, summed', 'Capacity ceiling' + tipA],
        ['Fill probability', fin(fp.value) ? pct(fp.value, 0) : null, fp.value != null ? (fp.basis || '') + ' n=' + fp.n : fp.why, 'Fill probability (PAPER-simulated)' + tipA]
      ]);
      if (!cands.length) return head + na('no MEASURED candidate with an edge-at-size grid (' + JSON.stringify(a.unavailable_by_reason || {}) + ')');
      if (capSel >= cands.length) capSel = 0;
      var btns = '<div class="sl-chiprow" role="group" aria-label="Candidate">' + cands.map(function (c, i) {
        return '<button type="button" class="sl-chip' + (i === capSel ? ' on' : '') + '" data-capsel="' + i + '" aria-pressed="' + (i === capSel) + '">' + esc(short(c.us_market_slug, 22)) + ' · ' + esc(usd(c.EXECUTABLE_OPPORTUNITY_DOLLARS)) + '</button>';
      }).join('') + '</div>';
      return head + btns + edgeChart(cands[capSel]) +
        '<div class="pc-legend"><span><span class="pc-sw paper"></span>expected net edge per $ (measured)</span><span><span class="pc-sw" style="background:var(--dk-surface);border:1px solid var(--dk-ink-3)"></span>exceeds visible depth</span><span><span class="pc-sw warn"></span>capacity ceiling</span></div>';
    });
    return card('capacity', 'Deployable capacity', 'EXPECTED_EDGE_AT_SIZE · $10 → $10,000', e, body);
  }
  function edgeChart(c) {
    var g = c.EXPECTED_EDGE_AT_SIZE;
    var W = chartW(), H = 210, L = 52, R = 14, T = 12, B = 34;
    var lx = function (v) { return Math.log10(v); };
    var x = scale(1, 4, L, W - R);
    var meas = g.filter(function (p) { return p.status === 'MEASURED' && fin(p.edge_per_dollar); });
    var ys = meas.map(function (p) { return p.edge_per_dollar; }).concat([0]);
    var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    var pad = (y1 - y0) * 0.12 || 0.01;
    var ticks = niceTicks(y0 - pad, y1 + pad, 4);
    var y = scale(ticks[0], ticks[ticks.length - 1], H - B, T);
    var s = '<svg class="pc-chart" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Expected net edge per dollar by deployed size for ' + esc(c.us_market_slug) + '">';
    ticks.forEach(function (t) { s += '<line class="grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + y(t) + '" y2="' + y(t) + '"/><text x="' + (L - 6) + '" y="' + (y(t) + 3) + '" text-anchor="end">' + (t * 100).toFixed(1) + '¢</text>'; });
    s += '<line class="zero" x1="' + L + '" x2="' + (W - R) + '" y1="' + y(0) + '" y2="' + y(0) + '"/>';
    [10, 100, 1000, 10000].forEach(function (v) { s += '<text x="' + x(lx(v)) + '" y="' + (H - 14) + '" text-anchor="' + (v === 10 ? 'start' : v === 10000 ? 'end' : 'middle') + '">$' + v.toLocaleString('en-US') + '</text>'; });
    s += '<text x="' + (W - R) + '" y="' + (H - 2) + '" text-anchor="end">size deployed (log scale)</text>';
    if (fin(c.CAPACITY_CEILING_USD) && c.CAPACITY_CEILING_USD >= 10) {
      var cx = x(lx(Math.min(10000, c.CAPACITY_CEILING_USD)));
      s += '<line class="ceil" x1="' + cx + '" x2="' + cx + '" y1="' + T + '" y2="' + (H - B) + '"/><text x="' + (cx + 4) + '" y="' + (T + 10) + '" class="lbl-strong">ceiling ' + esc(usd(c.CAPACITY_CEILING_USD, 0)) + '</text>';
    }
    if (meas.length > 1) s += '<path class="line-paper" d="' + meas.map(function (p, i) { return (i ? 'L' : 'M') + x(lx(p.size_usd)).toFixed(1) + ' ' + y(p.edge_per_dollar).toFixed(1); }).join(' ') + '"/>';
    g.forEach(function (p) {
      var tip = '$' + p.size_usd.toLocaleString('en-US') + ' deployed · ' + c.us_market_slug + '\n' +
        (p.status === 'MEASURED' ? 'edge ' + (p.edge_per_dollar * 100).toFixed(2) + '¢ per $ · expected net ' + usd(p.expected_net_profit_usd) + '\nVWAP ' + p.vwap + ' · impact ' + p.price_impact + ' · ' + p.contracts + ' contracts'
                                 : 'EXCEEDS VISIBLE DEPTH: the recorded book cannot fill this size') +
        '\nsource GET /api/command/profitability/capacity (decision ' + ts(c.decided_at) + ')';
      var px = x(lx(p.size_usd));
      if (p.status === 'MEASURED' && fin(p.edge_per_dollar)) s += '<circle class="dot-paper" cx="' + px + '" cy="' + y(p.edge_per_dollar) + '" r="4.5"' + tipAttr(tip) + '/>';
      else s += '<circle class="dot-hollow" cx="' + px + '" cy="' + (H - B) + '" r="4"' + tipAttr(tip) + '/>';
    });
    return s + '</svg>';
  }

  // ── drawdown (per book, from the closed positions the read returns) ─
  function drawdown() {
    var e = env('capital');
    var body = guard(e, function (b) {
      var pos = b.positions || [];
      var out = BOOKS.map(function (bk_) {
        var m = nsMetricBook(bk_, 'MAX_DRAWDOWN');
        var closed = pos.filter(function (p) { return p.book === bk_ && p.state === 'CLOSED' && fin(p.net_profit_usd) && fin(p.released_at); })
          .sort(function (a, c) { return a.released_at - c.released_at; });
        var head = '<div class="pc-note" style="margin:0 0 4px">' + (m ? 'Server MAX_DRAWDOWN ' + (fin(m.value) ? usd(m.value) : 'UNAVAILABLE (' + (m.why || m.status) + ')') : 'north-star ' + env('ns').state) + '</div>';
        if (closed.length < 2) return '<div class="pc-book">' + bookHead(bk_) + head + na(closed.length + ' closed position(s) with realized P&L in this read; a path needs two', 'NO PATH') + '</div>';
        return '<div class="pc-book">' + bookHead(bk_) + head + ddChart(closed, bk_) + '</div>';
      }).join('');
      var trunc = (b.positions || []).length >= 1000 ? ' The read returned its 1,000-row limit: older positions are not in the path.' : '';
      return '<div class="pc-twobook">' + out + '</div><p class="pc-note">Line: cumulative realized net P&L by release time (the sum of the closed positions this read returns, per book). Shaded: distance below the running peak (underwater).' + esc(trunc) + '</p>';
    });
    return card('drawdown', 'Drawdown', 'cumulative realized P&L · underwater', e, body, {wide: true});
  }
  function ddChart(closed, bk_) {
    var pts = [], cum = 0, peak = 0;
    closed.forEach(function (p) { cum += p.net_profit_usd; peak = Math.max(peak, cum); pts.push({t: p.released_at, v: cum, pk: peak, p: p}); });
    var W = chartW(), H = 190, L = 58, R = 10, T = 10, B = 26;
    var t0 = pts[0].t, t1 = pts[pts.length - 1].t;
    var x = scale(t0, t1, L, W - R);
    var lo = Math.min(0, Math.min.apply(null, pts.map(function (q) { return q.v; })));
    var hi = Math.max(0, Math.max.apply(null, pts.map(function (q) { return q.pk; })));
    var ticks = niceTicks(lo, hi, 4);
    var y = scale(ticks[0], ticks[ticks.length - 1], H - B, T);
    var s = '<svg class="pc-chart" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Cumulative realized P&L and drawdown, ' + bk_ + '">';
    ticks.forEach(function (t) { s += '<line class="grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + y(t) + '" y2="' + y(t) + '"/><text x="' + (L - 6) + '" y="' + (y(t) + 3) + '" text-anchor="end">' + esc(usd(t, 0)) + '</text>'; });
    s += '<line class="zero" x1="' + L + '" x2="' + (W - R) + '" y1="' + y(0) + '" y2="' + y(0) + '"/>';
    var under = 'M' + pts.map(function (q) { return x(q.t).toFixed(1) + ' ' + y(q.pk).toFixed(1); }).join(' L') + ' L' + pts.slice().reverse().map(function (q) { return x(q.t).toFixed(1) + ' ' + y(q.v).toFixed(1); }).join(' L') + 'Z';
    s += '<path class="under" d="' + under + '"/>';
    s += '<path class="line-' + bk_.toLowerCase() + '" d="M' + pts.map(function (q) { return x(q.t).toFixed(1) + ' ' + y(q.v).toFixed(1); }).join(' L') + '"/>';
    [t0, (t0 + t1) / 2, t1].forEach(function (t, i) { s += '<text x="' + x(t) + '" y="' + (H - 8) + '" text-anchor="' + ['start', 'middle', 'end'][i] + '">' + esc(ts(t).slice(5, 10)) + '</text>'; });
    var last = pts[pts.length - 1];
    s += '<circle class="dot-' + bk_.toLowerCase() + '" cx="' + x(last.t) + '" cy="' + y(last.v) + '" r="4"/>';
    var step = Math.max(1, Math.floor(pts.length / 80));
    pts.forEach(function (q, i) {
      if (i % step && i !== pts.length - 1) return;
      var w = Math.max(6, (W - L - R) / pts.length * step);
      s += '<rect class="hit" x="' + (x(q.t) - w / 2) + '" y="' + T + '" width="' + w + '" height="' + (H - T - B) + '"' +
        tipAttr(bk_ + ' · released ' + ts(q.t) + '\nposition ' + susd(q.p.net_profit_usd) + ' · cumulative ' + susd(q.v) + '\ndrawdown ' + usd(q.pk - q.v) + '\n' + short(q.p.position_key, 80) + '\nsource GET /api/command/profitability/capital') + '/>';
    });
    return s + '</svg>';
  }

  // ── forecast fan chart ──────────────────────────────────────────────
  var HZ = [['24H', 1], ['7D', 7], ['30D', 30]];
  function forecast() {
    var eh = env('horizons'), ef = env('forecast');
    var body;
    if (eh.state !== 'OK' && ef.state !== 'OK') body = na('horizons: ' + eh.why + ' · 30-day forecast: ' + ef.why, eh.state);
    else {
      var books = BOOKS.map(function (bk_) {
        var rows = HZ.map(function (h) { return [h[0], h[1], horizon(bk_, h[0])]; });
        var f30 = ef.state === 'OK' ? (ef.body.data || {})[bk_] : null;
        var anyMeasured = rows.some(function (r) { return r[2] && fin(r[2].p50_pnl_usd); });
        var status = rows.map(function (r) { return r[2] && r[2].status; }).filter(Boolean)[0] || (f30 && f30.status) || 'UNAVAILABLE';
        return '<div class="pc-book">' + bookHead(bk_) + '<div style="margin:-2px 0 6px">' + stPill(status === 'FORWARD_VALIDATED' ? 'OK' : status, 'forecast status from the server') + '</div>' +
          (anyMeasured ? fanChart(rows, bk_) : na((rows[2][2] && (rows[2][2].why || um(rows[2][2], 'expected_pnl_usd'))) || (f30 && f30.why) || eh.why || 'no forecast issued', status)) +
          horizonTable(rows, bk_) + '</div>';
      }).join('');
      body = '<div class="pc-twobook">' + books + '</div><p class="pc-note">Band: P10–P90 of the block-bootstrapped net P&L at each horizon; line: P50. ' +
        'Every horizon stays UNPROVEN until ≥ 12 of its own persisted forecasts are scored forward and pass coverage and Brier checks. Rounded on the server: $1, 0.01, 0.1 — no false precision.</p>';
    }
    return card('forecast', 'Revenue forecast', '24 h · 7 d · 30 d · P10 / P50 / P90', eh.state === 'OK' ? eh : ef, body, {wide: true});
  }
  function fanChart(rows, bk_) {
    var W = chartW(), H = 200, L = 58, R = 16, T = 22, B = 28;
    var pts = [{d: 0, p10: 0, p50: 0, p90: 0}].concat(rows.filter(function (r) { return r[2] && fin(r[2].p50_pnl_usd); }).map(function (r) {
      return {d: r[1], p10: r[2].p10_pnl_usd, p50: r[2].p50_pnl_usd, p90: r[2].p90_pnl_usd, h: r[0], row: r[2]};
    }));
    var x = scale(0, 30, L, W - R);
    var lo = Math.min.apply(null, pts.map(function (p) { return p.p10; })), hi = Math.max.apply(null, pts.map(function (p) { return p.p90; }));
    var ticks = niceTicks(Math.min(0, lo), Math.max(0, hi), 4);
    var y = scale(ticks[0], ticks[ticks.length - 1], H - B, T);
    var s = '<svg class="pc-chart" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Forecast fan chart ' + bk_ + '">';
    ticks.forEach(function (t) { s += '<line class="grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + y(t) + '" y2="' + y(t) + '"/><text x="' + (L - 6) + '" y="' + (y(t) + 3) + '" text-anchor="end">' + esc(usd(t, 0)) + '</text>'; });
    s += '<line class="zero" x1="' + L + '" x2="' + (W - R) + '" y1="' + y(0) + '" y2="' + y(0) + '"/>';
    s += '<path class="band" d="M' + pts.map(function (p) { return x(p.d) + ' ' + y(p.p90); }).join(' L') + ' L' + pts.slice().reverse().map(function (p) { return x(p.d) + ' ' + y(p.p10); }).join(' L') + 'Z"/>';
    s += '<path class="line-' + bk_.toLowerCase() + '" d="M' + pts.map(function (p) { return x(p.d) + ' ' + y(p.p50); }).join(' L') + '"/>';
    [0, 7, 30].forEach(function (d) { s += '<text x="' + x(d) + '" y="' + (H - 8) + '" text-anchor="' + (d === 0 ? 'start' : d === 30 ? 'end' : 'middle') + '">' + (d === 0 ? 'now' : '+' + d + 'd') + '</text>'; });
    pts.slice(1).forEach(function (p) {
      s += '<line class="ci" x1="' + x(p.d) + '" x2="' + x(p.d) + '" y1="' + y(p.p10) + '" y2="' + y(p.p90) + '"/>';
      s += '<circle class="dot-' + bk_.toLowerCase() + '" cx="' + x(p.d) + '" cy="' + y(p.p50) + '" r="4.5"' + tipAttr(bk_ + ' · ' + p.h + ' horizon\nP10 ' + usd(p.p10, 0) + ' · P50 ' + usd(p.p50, 0) + ' · P90 ' + usd(p.p90, 0) + '\nP(positive) ' + pct(p.row.prob_positive, 0) + ' · status ' + p.row.status + '\n' + p.row.sample_days + ' days / ' + p.row.sample_positions + ' positions of history\nsource GET /api/command/profitability/forecast-horizons · issued ' + ts(p.row.issued_at)) + '/>';
    });
    s += '<text class="stamp" x="' + (W - R) + '" y="14" text-anchor="end">UNPROVEN</text>';
    return s + '</svg>';
  }
  function horizonTable(rows, bk_) {
    var f = function (r, k, kind) {
      if (!r) return '—';
      var v = r[k];
      if (!fin(v)) return '<span class="muted" data-tip="' + esc(um(r, k) || 'not measured') + '">n/a</span>';
      return esc(kind === 'usd' ? usd(v, 0) : kind === 'p' ? pct(v, 0) : kind === 'u' ? Number((v * 100).toPrecision(2)) + '%' : kind === 'i' ? numf(v, 0) : numf(v, 1));
    };
    var lines = [['Expected opportunities', 'expected_opportunities', 'n'], ['Qualified opportunities', 'expected_qualified_opportunities', 'n'],
                 ['Expected turnover', 'expected_turnover_usd', 'usd'], ['Deployable capital (now)', 'deployable_capital_usd', 'usd'],
                 ['Capital-hours ($·h)', 'expected_capital_hours', 'i'], ['Net P&L P10', 'p10_pnl_usd', 'usd'], ['Net P&L P50', 'p50_pnl_usd', 'usd'],
                 ['Net P&L P90', 'p90_pnl_usd', 'usd'], ['P(positive)', 'prob_positive', 'p'], ['Expected drawdown', 'expected_max_drawdown_usd', 'usd'],
                 ['Capacity utilization', 'capacity_utilization', 'u']];
    return '<div class="tablewrap pc-scroll" style="margin-top:8px"><table class="pc-table" style="min-width:420px"><thead><tr><th>' + bk_ + '</th>' +
      HZ.map(function (h) { return '<th class="num">' + h[0] + (h[0] === '30D' ? ' (month)' : '') + '</th>'; }).join('') + '</tr></thead><tbody>' +
      lines.map(function (ln) { return '<tr><td>' + esc(ln[0]) + '</td>' + rows.map(function (r) { return '<td class="num">' + f(r[2], ln[1], ln[2]) + '</td>'; }).join('') + '</tr>'; }).join('') +
      '</tbody></table></div>';
  }

  // ── today's opportunity ────────────────────────────────────────────
  function todaysOpportunity() {
    var e = env('capacity'), sc = env('scores');
    var parts = [];
    parts.push(guard(e, function (b) {
      var a = b.data || {};
      var h = horizon('PAPER', '24H');
      return kv([
        ['Markets assessed (24h)', numf(a.markets, 0), (a.measured_markets || 0) + ' with depth evidence'],
        ['Executable now', usd(a.EXECUTABLE_OPPORTUNITY_DOLLARS), 'conditional on fill'],
        ['Expected executable', usd(a.EXPECTED_EXECUTABLE_OPPORTUNITY_DOLLARS), um(a, 'EXPECTED_EXECUTABLE_OPPORTUNITY_DOLLARS') || '× fill probability'],
        ['Daily executable', usd(a.daily_executable_opportunity_dollars), (a.days_observed || 0) + ' day(s) observed'],
        ['Next 24h opportunities', h && fin(h.expected_opportunities) ? numf(h.expected_opportunities, 1) : null, h ? (um(h, 'expected_opportunities') || 'UNPROVEN forecast') : env('horizons').why],
        ['Next 24h qualified', h && fin(h.expected_qualified_opportunities) ? numf(h.expected_qualified_opportunities, 1) : null, h ? um(h, 'expected_qualified_opportunities') : null]
      ]) + (Object.keys(a.unavailable_by_reason || {}).length ? '<p class="pc-note">Not measurable: ' + Object.keys(a.unavailable_by_reason).map(function (k) { return esc(human(k)) + ' ×' + a.unavailable_by_reason[k]; }).join(' · ') + '</p>' : '');
    }));
    if (sc.state === 'OK') parts.push('<p class="pc-note">Opportunity scores: ' + sc.body.data.counts.MEASURED + ' measured, ' + sc.body.data.counts.UNAVAILABLE + ' unavailable.</p>');
    return card('today', "Today's opportunity", 'recorded books at decision', e, parts.join(''));
  }

  // ── top opportunities (decomposed, expandable) ──────────────────────
  var COMP_ORDER = [['NET_EV', 'Net EV'], ['EXECUTION_CONFIDENCE', 'Execution conf.'], ['LIQUIDITY_CAPACITY', 'Liquidity / capacity'],
                    ['EDGE_CONFIDENCE', 'Edge conf.'], ['CALIBRATION_CONFIDENCE', 'Calibration conf.'], ['SETTLEMENT_CONFIDENCE', 'Settlement conf.'],
                    ['REGIME_CONFIDENCE', 'Regime conf.'], ['CORRELATION_RISK_COST', 'Correlation / risk cost']];
  function compVal(c) {
    if (!c) return null;
    if (c.state) return String(c.state);
    if (!fin(c.value)) return null;
    if (c.unit === 'USD') return usd(c.value);
    if (c.unit === 'probability' || c.unit === 'fraction') return pct(c.value, 0);
    return sig(c.value);
  }
  function topOpportunities(limit) {
    var e = env('scores');
    var body = guard(e, function (b) {
      var rows = (b.data.rows || []).slice(0, limit || 12);
      if (!rows.length) return na('no candidate scored', 'EMPTY');
      return rows.map(function (r, i) {
        var comps = r.components || {};
        var ex = r.expand || {};
        var title = (r.us_market_slug || r.candidate_id) + (r.holding_side ? ' · ' + r.holding_side : '');
        var scoreTxt = r.status === 'MEASURED' ? sig(r.opportunity_score) : 'UNAVAILABLE';
        var compHtml = COMP_ORDER.map(function (k) {
          var c = comps[k[0]];
          var v = compVal(c);
          return '<div class="pc-comp' + (c && c.in_score ? ' in' : '') + '"' + tipAttr(k[1] + (c && c.source ? ' · source ' + c.source : '') + '\n' + ((c && c.basis) || '') + (c && c.why ? '\nwhy: ' + c.why : '') + (c && c.in_score ? '\nenters the score' : '\nshown beside the score; not multiplied in')) + '>' +
            '<span class="k">' + esc(k[1]) + (c && c.in_score ? '<span>in score</span>' : '') + '</span>' +
            (v != null ? '<span class="v">' + esc(v) + '</span>' + (c && c.source ? '<span class="w">' + esc(/^(ARCHER|EDDIE)_EXECUTION_ESTIMATE$/.test(c.source || '') ? 'from Archer\'s estimate (shadow)' : 'from the capacity fill share') + '</span>' : '') : '<span class="v pc-na">UNAVAILABLE</span><span class="w">' + esc(short(c && c.why || '', 90)) + '</span>') + '</div>';
        }).join('');
        var pin = ex.pinnacle || {}, mk = ex.market || {}, dt = ex.derek_thesis || {};
        var part = function (o, f) { return !o || o.status !== 'OK' ? '<span class="pc-na">UNAVAILABLE — ' + esc((o && o.why) || 'not read') + '</span>' : f(o); };
        var dl = [
          ['Pinnacle probability', part(pin, function (o) { return esc(fin(o.probability) ? o.probability.toFixed(3) : '—') + (o.observed_at ? ' at ' + esc(ts(o.observed_at)) : ' (no observation time recorded)') + (o.provider ? ' · ' + esc(o.provider) : ''); })],
          ['Evidence age', pin.status === 'OK' && fin(pin.evidence_age_s) ? esc(numf(pin.evidence_age_s, 1) + 's of ' + numf(pin.limit_s, 0) + 's limit') : '<span class="muted">not recorded</span>'],
          ['Market price', esc(fin(mk.market_price) ? (mk.market_price * 100).toFixed(1) + '¢' : '—') + ' <span class="muted">' + esc(mk.price_basis || '') + '</span>'],
          ['Gross edge', fin(mk.gross_edge_pp) ? esc(mk.gross_edge_pp.toFixed(2) + ' pp') : '<span class="muted">not recorded (no policy decision)</span>'],
          ['Fees / slippage', esc((fin(mk.fees_usd) ? usd(mk.fees_usd) + ' fees' : 'fees not recorded') + ' · impact ' + (fin(mk.expected_price_impact) ? (mk.expected_price_impact * 100).toFixed(2) + '¢' : 'n/a'))],
          ['Expected executable edge', fin(mk.expected_executable_edge_per_dollar) ? esc((mk.expected_executable_edge_per_dollar * 100).toFixed(2) + '¢ per $') : '<span class="muted">n/a</span>'],
          ['Capacity', esc((usd(mk.executable_capacity_usd) || 'n/a') + ' executable · ceiling ' + (usd(mk.capacity_ceiling_usd) || 'n/a') + ' · depth ' + (usd(mk.visible_depth_usd) || 'n/a'))],
          ['Settlement state', esc(compVal(ex.settlement) || 'UNAVAILABLE — ' + ((ex.settlement || {}).why || ''))],
          ['Derek thesis', part(dt, function (o) { return esc(o.verdict + (o.refusals && o.refusals.length ? ' · ' + o.refusals.join(', ') : '') + (fin(o.net_expected_profit_usd) ? ' · policy net ' + usd(o.net_expected_profit_usd) : '') + (o.rationale ? ' · ' + o.rationale : '')); })],
          ['Karen challenge', part(ex.karen_challenge, function (o) { return esc(o.severity + ' · ' + o.state + ' · ' + short(o.claim, 140)); })],
          ['Archer execution', part(ex.archer_execution || ex.eddie_execution, function (o) { return esc(o.recommendation + (o.execution_style ? ' · ' + o.execution_style : '') + ' · P(fill) ' + (pct(o.expected_fill_probability, 0) || 'n/a') + ' · net executable edge ' + (fin(o.expected_net_executable_edge_pp) ? o.expected_net_executable_edge_pp.toFixed(2) + ' pp' : 'n/a') + ' · executable EV ' + (usd(o.expected_executable_ev_usd) || 'n/a') + (o.recommendation_reason ? ' · ' + o.recommendation_reason : '')) + ' <span class="muted">' + esc('SHADOW_ONLY · ' + (o.estimate_id || '') + ' · ' + ts(o.estimated_at)) + '</span>'; })],
          ['Allie · Chief Allocator ranking', part(ex.allocator, function (o) { return esc('rank ' + o.rank + ' · shadow ' + usd(o.shadow_usd) + (o.binding_constraint ? ' · bound by ' + o.binding_constraint : '')); }) + (ex.allocator && ex.allocator.leaderboard_rank ? ' <span class="muted">(leaderboard rank ' + ex.allocator.leaderboard_rank + ')</span>' : '')],
          ['Xavier if entered', part(ex.xavier, function (o) { return esc('thesis ' + o.thesis_id + ' · entry EV ' + usd(o.entry_ev_usd)); })],
          ['Audrey audit', part(ex.audrey, function (o) { return o.n ? esc(o.n + ' finding(s) · latest ' + o.kind + ' (' + o.severity + ')') : esc(o.note || 'no finding'); })]
        ].map(function (d) { return '<dt>' + esc(d[0]) + '</dt><dd>' + d[1] + '</dd>'; }).join('');
        return '<details class="pc-opp"><summary><span class="rk">' + (i + 1) + '</span><span class="nm">' + esc(short(title, 64)) +
          '<span class="s">' + esc((r.league || '—') + ' · ' + (r.strategy || '') + ' · decided ' + ts(r.decided_at)) + '</span>' +
          '<span class="f">EV ' + esc(usd(r.expected_net_executable_ev_usd) || 'n/a') + ' × P(fill) ' + esc(pct(r.fill_probability, 0) || 'n/a') + ' × capacity ' + esc(fin(r.capacity_factor) ? r.capacity_factor.toFixed(2) : 'n/a') + ' ÷ ' + esc(fin(r.capital_hours) ? numf(r.capital_hours, 0) + ' $·h' : 'n/a $·h') + '</span></span>' +
          '<span class="sc"' + tipAttr('Opportunity score = expected net executable EV × fill probability × capacity factor ÷ capital-hours\n' + (r.status === 'MEASURED' ? 'EV ' + usd(r.expected_net_executable_ev_usd) + ' × P(fill) ' + pct(r.fill_probability, 0) + ' × ' + numf(r.capacity_factor, 2) + ' ÷ ' + numf(r.capital_hours, 1) + ' $·h' : 'why: ' + r.why) + '\nsource GET /api/command/profitability/opportunity-scores · computed ' + ts(r.computed_at)) + '>' +
          (r.status === 'MEASURED' ? esc(scoreTxt) + '<span class="u">$ per $·h</span>' : '<span class="pc-na">UNAVAILABLE</span><span class="u">' + esc(short(r.why, 40)) + '</span>') + '</span></summary>' +
          '<div class="body"><div class="pc-comps">' + compHtml + '</div><dl class="pc-dl">' + dl + '</dl></div></details>';
      }).join('') + '<p class="pc-note">' + esc(b.formula || '') + '. Tap a row for its decomposition and evidence. Components marked "in score" multiply into it; the others are shown beside it and are never a silent 1.0.</p>';
    });
    return card('opps', 'Top opportunities', 'Opportunity Score leaderboard · decomposed', e, body, {wide: true});
  }

  // ── lost opportunities ─────────────────────────────────────────────
  var CLS = [['GOOD_REFUSAL', 'Good refusal', 'bar-good', 'good'], ['FALSE_REFUSAL', 'False refusal', 'bar-bad', 'bad'], ['UNKNOWABLE', 'Unknowable', 'bar-muted', 'muted']];
  function lostOpportunities(compact) {
    var e = env('lol');
    var body = guard(e, function (b) {
      var s = b.data.summary, bc = s.by_class, m = s.management || {};
      var tot = s.total || 0;
      var parts = CLS.map(function (c) { return {v: bc[c[0]].n, cls: c[2], tip: c[1] + ': ' + bc[c[0]].n + ' of ' + tot + '\nHYPOTHETICAL P&L ' + (fin(bc[c[0]].hypothetical_pnl_usd) ? susd(bc[c[0]].hypothetical_pnl_usd) : 'not priced') + ' (' + bc[c[0]].priced_n + ' priced)\nsource GET /api/command/profitability/lost-opportunities'}; });
      var html = '<div class="pc-legend" style="margin:0 0 6px">' + CLS.map(function (c) { return '<span><span class="pc-sw ' + c[3] + '"></span>' + esc(c[1]) + ' ' + bc[c[0]].n + '</span>'; }).join('') + '<span class="muted">of ' + tot + ' settled refusals</span></div>' +
        stackBar(parts, tot, 16, 'Refusal classes') +
        kv([['Missed measurable profit', fin(m.missed_measurable_profit_usd) ? susd(m.missed_measurable_profit_usd) : null, (m.missed_measurable_profit_why || (m.missed_measurable_profit_n + ' priced false refusal(s)')) + ' · HYPOTHETICAL', m.basis],
            ['Avoided losses', fin(m.avoided_losses_usd) ? usd(m.avoided_losses_usd) : null, (m.avoided_losses_n || 0) + ' good refusal(s) that would have lost · HYPOTHETICAL', m.basis],
            ['Hindsight winners, correctly refused', numf(m.hindsight_winners_correctly_refused_n, 0), 'not missed profit: no executable edge, or a correct control', m.basis]]);
      if (compact) return html;
      var maxA = Math.max.apply(null, (s.by_attribution || []).map(function (a) { return a.n; }).concat([1]));
      html += '<div class="pc-twobook"><div class="pc-book"><h3>Refusal attribution</h3>' + attrBars(s.by_attribution || [], maxA) + '</div>' +
        '<div class="pc-book"><h3>Trend · last 30 days</h3>' + trendChart(s.trend_30d || []) + '</div></div>';
      var defects = b.data.false_refusal_defects || [];
      html += '<div class="pc-book" style="margin-top:12px"><h3><span class="pc-sw bad"></span>FALSE_REFUSAL defects</h3>' +
        (defects.length ? '<ul class="pc-list">' + defects.map(function (d) { return '<li><span class="mono">' + esc(d.defect) + '</span> × ' + d.n + '</li>'; }).join('') + '</ul>' : '<p class="pc-note" style="margin:0">No false refusal: no settled refusal showed a positive executable net EV blocked by a defect.</p>') +
        ((b.data.false_refusals || []).length ? '<div class="tablewrap pc-scroll" style="margin-top:8px"><table class="pc-table"><thead><tr><th>Decided</th><th>League</th><th>Defect</th><th class="num">Net EV at decision</th><th class="num">Hypothetical P&L</th><th>Decision</th></tr></thead><tbody>' +
          b.data.false_refusals.slice(0, 20).map(function (r) { return '<tr><td>' + esc(ts(r.decided_at)) + '</td><td>' + esc(r.league) + '</td><td class="mono">' + esc(r.defect) + '</td><td class="num">' + esc(usd(r.decision_time_net_ev_usd)) + '</td><td class="num"' + tipAttr('HYPOTHETICAL: decision-time quantity at the decision-time executable price against settlement ' + r.settlement_evidence_id) + '>' + esc(fin(r.hypothetical_pnl_usd) ? susd(r.hypothetical_pnl_usd) : 'n/a') + '</td><td class="mono">' + esc(short(r.decision_ref, 28)) + '</td></tr>'; }).join('') + '</tbody></table></div>' : '') + '</div>';
      html += '<div class="tablewrap pc-scroll" style="margin-top:12px"><table class="pc-table"><thead><tr><th>League</th><th class="num">Good</th><th class="num">False</th><th class="num">Unknowable</th><th class="num">Total</th></tr></thead><tbody>' +
        (s.by_league || []).map(function (l) { return '<tr><td>' + esc(l.league) + '</td><td class="num">' + l.GOOD_REFUSAL + '</td><td class="num">' + l.FALSE_REFUSAL + '</td><td class="num">' + l.UNKNOWABLE + '</td><td class="num">' + l.n + '</td></tr>'; }).join('') + '</tbody></table></div>' +
        '<p class="pc-note">' + esc(b.disclosure_ledger || '') + '</p>';
      return html;
    });
    return card('lol', 'Lost opportunities', 'settled refusals · decision-time evidence only', e, body, {wide: !compact, pill: ' ' + stPill('HYPOTHETICAL', 'the P&L figures here are hypothetical, never results')});
  }
  function attrBars(list, maxA) {
    if (!list.length) return '<p class="pc-note">No attribution yet.</p>';
    var W = chartW(), rowH = 24, L = W < 400 ? 128 : 190, H = list.length * rowH + 4;
    var x = scale(0, maxA, L, W - 40);
    var s = '<svg class="pc-chart" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Refusals by attribution">';
    list.forEach(function (a, i) {
      var y = i * rowH + 4, xx = L;
      s += '<text x="' + (L - 8) + '" y="' + (y + 13) + '" text-anchor="end" class="lbl-strong">' + esc(human(a.attribution)) + '</text>';
      CLS.forEach(function (c) {
        var v = a[c[0]] || 0;
        if (!v) return;
        var w = x(v) - L;
        s += '<rect x="' + xx + '" y="' + y + '" width="' + Math.max(1, w - 2) + '" height="16" rx="3" class="' + c[2] + '"' + tipAttr(human(a.attribution) + ' · ' + c[1] + ': ' + v) + '/>';
        xx += w;
      });
      s += '<text x="' + (xx + 6) + '" y="' + (y + 13) + '">' + a.n + '</text>';
    });
    return s + '</svg>';
  }
  function trendChart(days) {
    if (!days.length) return '<p class="pc-note">No settled refusal in the last 30 days.</p>';
    var W = chartW(), H = 150, L = 30, R = 8, T = 8, B = 24;
    var max = Math.max.apply(null, days.map(function (d) { return d.good + d.false_refusal + d.unknowable; }).concat([1]));
    var ticks = niceTicks(0, max, 3);
    var y = scale(0, ticks[ticks.length - 1], H - B, T);
    var bw = Math.min(24, (W - L - R) / days.length - 4);
    var s = '<svg class="pc-chart" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Refusal classes per day">';
    ticks.forEach(function (t) { s += '<line class="grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + y(t) + '" y2="' + y(t) + '"/><text x="' + (L - 5) + '" y="' + (y(t) + 3) + '" text-anchor="end">' + t + '</text>'; });
    days.forEach(function (d, i) {
      var cx = L + (i + 0.5) * (W - L - R) / days.length, base = H - B;
      [['good', 'bar-good', 'Good'], ['false_refusal', 'bar-bad', 'False'], ['unknowable', 'bar-muted', 'Unknowable']].forEach(function (k) {
        var v = d[k[0]] || 0;
        if (!v) return;
        var h = base - y(v);
        s += '<rect x="' + (cx - bw / 2) + '" y="' + (base - h) + '" width="' + bw + '" height="' + Math.max(1, h - 2) + '" class="' + k[1] + '"' + tipAttr(d.day + ' · ' + k[2] + ': ' + v + (k[0] === 'false_refusal' && fin(d.missed_hypothetical_usd) ? '\nmissed (HYPOTHETICAL) ' + susd(d.missed_hypothetical_usd) : '')) + '/>';
        base -= h;
      });
      if (days.length <= 10 || i % Math.ceil(days.length / 8) === 0) s += '<text x="' + cx + '" y="' + (H - 8) + '" text-anchor="middle">' + esc(d.day.slice(5)) + '</text>';
    });
    return s + '</svg>';
  }

  // ── today's attribution ────────────────────────────────────────────
  function todaysAttribution() {
    var e = env('lol'), av = env('avoidance');
    var body = guard(e, function (b) {
      var days = b.data.summary.trend_30d || [];
      var last = days[days.length - 1];
      var rows = (b.data.rows || []).filter(function (r) { return last && ts(r.decided_at).slice(0, 10) === last.day; });
      var by = {};
      rows.forEach(function (r) { by[r.attribution] = (by[r.attribution] || 0) + 1; });
      var keys = Object.keys(by).sort(function (a, c) { return by[c] - by[a]; });
      return (last ? '<p class="pc-note" style="margin:0 0 6px">Latest decision day with settled refusals: ' + esc(last.day) + ' — ' + last.good + ' good, ' + last.false_refusal + ' false, ' + last.unknowable + ' unknowable.</p>' : '') +
        (keys.length ? '<ul class="pc-list">' + keys.map(function (k) { return '<li>' + esc(human(k)) + ' × ' + by[k] + '</li>'; }).join('') + '</ul>' : '<p class="pc-note">No attribution for that day in the rows read.</p>');
    });
    var avHtml = av.state === 'OK' ? '<p class="pc-note">Avoidance meta-model (pos-learn, SHADOW, never blocks): ' + esc(human((av.body.data || {}).status || '')) + ' · levels ' + esc(Object.keys((av.body.data || {}).levels || {}).map(function (k) { return human(k) + ' ' + av.body.data.levels[k]; }).join(', ') || 'none') + '</p>'
                                  : '<p class="pc-note">Avoidance meta-model: ' + esc(av.state === 'NOT_DEPLOYED' ? 'POS-LEARN NOT DEPLOYED' : av.state + ' — ' + av.why) + '</p>';
    return card('attr', "Today's attribution", 'why the book refused · settled decisions', e, body + avHtml);
  }

  // ── capital blockers ───────────────────────────────────────────────
  function blockers() {
    var out = [];
    var cap = env('capacity');
    if (cap.state !== 'OK') out.push({title: 'Capacity unreadable', detail: cap.why, bad: true});
    else {
      var r = cap.body.data.unavailable_by_reason || {};
      Object.keys(r).sort(function (a, b) { return r[b] - r[a]; }).slice(0, 2).forEach(function (k) {
        out.push({title: human(k), detail: r[k] + ' candidate market(s) without depth evidence', k: 'capacity'});
      });
      var fp = ((cap.body.data.rates || {}).fill_probability) || {};
      if (!fin(fp.value)) out.push({title: 'Fill probability not measured', detail: fp.why, k: 'capacity'});
    }
    var lol = env('lol');
    if (lol.state === 'OK') {
      var bya = (lol.body.data.summary.by_attribution || []).filter(function (a) { return a.UNKNOWABLE || a.FALSE_REFUSAL; });
      bya.slice(0, 2).forEach(function (a) { out.push({title: human(a.attribution), detail: a.UNKNOWABLE + ' unknowable, ' + a.FALSE_REFUSAL + ' false refusal(s)', k: 'refusals', bad: a.FALSE_REFUSAL > 0}); });
      (lol.body.data.false_refusal_defects || []).slice(0, 2).forEach(function (d) { out.push({title: 'Defect: ' + d.defect, detail: d.n + ' false refusal(s)', k: 'defect', bad: true}); });
    }
    BOOKS.forEach(function (b) {
      var m = nsMetric(b, 'REALIZED_NET_EDGE');
      if (m && m.status !== 'MEASURED') out.push({title: b + ' net edge ' + human(m.status), detail: m.why || ('n=' + m.sample_n + ' of 30 closed positions'), k: 'evidence'});
    });
    var lad = env('ladder');
    if (lad.state === 'OK') {
      var next = ((lad.body.data || {}).levels || []).filter(function (l) { return !l.passed; })[0];
      if (next) out.push({title: 'Evidence level ' + next.level + ' not met', detail: next.why_not || next.criterion, k: 'evidence'});
    }
    var k = env('kills');
    if (k.state === 'OK' && (k.body.recommendations || []).length) out.push({title: k.body.recommendations.length + ' RECOMMEND_PAUSE record(s)', detail: 'research recommendations only; nothing is paused by them', k: 'risk', bad: true});
    var f = horizon('PAPER', '30D');
    if (f && f.status !== 'FORWARD_VALIDATED') out.push({title: 'Forecast ' + human(f.status), detail: f.why || '', k: 'forecast'});
    return out;
  }
  function blockersCard() {
    var bl = blockers();
    var body = bl.length ? '<ul class="pc-blockers">' + bl.map(function (x) { return '<li class="' + (x.bad ? 'pc-bl-bad' : '') + '"><span class="k">' + esc(x.k || 'read') + '</span>' + esc(x.title) + (x.detail ? ' — <span class="muted">' + esc(x.detail) + '</span>' : '') + '</li>'; }).join('') + '</ul>'
                         : '<p class="pc-note">No blocker could be read.</p>';
    return card('blockers', 'Capital blockers', 'what stands between the books and more profit', null, body);
  }

  // ── evidence ladder ────────────────────────────────────────────────
  function ladder() {
    var e = env('ladder');
    var body = guard(e, function (b) {
      var d = b.data || {};
      var levels = d.levels || [];
      var conf = d.confidence || {};
      return '<ol class="pc-ladder">' + levels.map(function (l) {
        return '<li class="' + (l.passed ? 'pass' : '') + (l.level === b.level ? ' current' : '') + '"' + tipAttr('Level ' + l.level + ' · ' + l.name + '\n' + l.criterion + (l.why_not ? '\nnot met: ' + l.why_not : '') + '\nsource GET /api/command/profitability/evidence-ladder · ' + ts(b.computed_at)) + '>' +
          '<span class="lv">' + l.level + '</span><span class="nm">' + esc(human(l.name)) + '<span class="cr">' + esc(short(l.why_not || l.criterion, 150)) + '</span></span>' + stPill(l.passed ? 'PASS' : 'UNAVAILABLE', null).replace('UNAVAILABLE</span>', 'NOT MET</span>') + '</li>';
      }).join('') + '</ol><p class="pc-note">Profitability confidence: ' + esc(human(b.confidence_status || 'UNAVAILABLE')) + (conf.why ? ' — ' + esc(conf.why) : '') + '. The ladder never skips a level and never states a subjective percentage.</p>';
    }, 'POS-TWIN NOT DEPLOYED');
    return card('ladder', 'Profitability confidence · evidence ladder', 'levels 0–6', e.state === 'NOT_DEPLOYED' ? null : e, body);
  }

  // ── tournaments ────────────────────────────────────────────────────
  function tournaments() {
    var em = env('tmodels'), ea = env('tagents');
    var models = guard(em, function (b) {
      var ms = (b.data || {}).models || [];
      if (!ms.length) return na('no model registered', 'EMPTY');
      return '<div class="tablewrap pc-scroll"><table class="pc-table"><thead><tr><th>Model</th><th>Role</th><th>Status</th><th class="num">Resolved</th><th class="num">Brier</th><th class="num">Realized edge</th><th>Vs champion</th><th>Stage</th></tr></thead><tbody>' +
        ms.map(function (m) { return '<tr><td class="mono">' + esc(m.subject_id) + '</td><td>' + esc(m.role) + '</td><td>' + esc(human(m.status)) + '</td><td class="num">' + esc(m.resolved) + '</td><td class="num">' + esc(fin(m.brier) ? m.brier.toFixed(3) : 'n/a') + '</td><td class="num">' + esc(fin(m.realized_edge_mean) ? m.realized_edge_mean.toFixed(3) : 'n/a') + '</td><td>' + esc(m.versus_champion ? human(m.versus_champion.verdict) : '—') + '</td><td>' + esc(human(m.promotion_stage || '')) + '</td></tr>'; }).join('') + '</tbody></table></div>' +
        '<p class="pc-note">Champion ' + esc((b.data || {}).champion || '—') + '. ' + esc((b.data || {}).champion_rule || '') + '</p>';
    }, 'POS-LEARN NOT DEPLOYED');
    var agents = guard(ea, function (b) {
      var vs = (b.data || {}).variants || [];
      if (!vs.length) return na('no agent variant registered', 'EMPTY');
      return '<div class="tablewrap pc-scroll"><table class="pc-table"><thead><tr><th>Variant</th><th>Agent</th><th>Status</th><th class="num">Net economics</th><th class="num">Max DD</th><th class="num">False refusals</th><th>Vs V1</th></tr></thead><tbody>' +
        vs.map(function (v) { var m = v.metrics || {}; return '<tr><td class="mono">' + esc(v.subject_id) + '</td><td>' + esc(v.agent) + '</td><td>' + esc(human(v.status)) + '</td><td class="num">' + esc(fin(m.net_economics_usd) ? susd(m.net_economics_usd) : 'n/a') + '</td><td class="num">' + esc(fin(m.max_drawdown_usd) ? usd(m.max_drawdown_usd) : 'n/a') + '</td><td class="num">' + esc(m.false_refusals != null ? m.false_refusals : 'n/a') + '</td><td>' + esc(v.versus_v1 ? human(v.versus_v1.verdict) : '—') + '</td></tr>'; }).join('') + '</tbody></table></div>';
    }, 'POS-LEARN NOT DEPLOYED');
    return card('tmodels', 'Model tournament', 'M0–M5 · forward scores · promotion ladder', em.state === 'NOT_DEPLOYED' ? null : em, models) +
           card('tagents', 'Agent tournament', 'variants vs V1 on the same opportunities', ea.state === 'NOT_DEPLOYED' ? null : ea, agents);
  }

  // ── agent economic contribution (twin scorecards) ──────────────────
  function scorecards() {
    var e = env('scorecards');
    var body = guard(e, function (b) {
      var ag = b.agents || {};
      var names = Object.keys(ag);
      if (!names.length) return na(b.why || 'no scorecard yet', 'EMPTY');
      var rows = [];
      names.forEach(function (n) { (ag[n] || []).forEach(function (r) { rows.push(r); }); });
      return '<div class="tablewrap pc-scroll"><table class="pc-table"><thead><tr><th>Agent</th><th>Metric</th><th>Book</th><th class="num">Value</th><th class="num">n</th><th>Status</th></tr></thead><tbody>' +
        rows.slice(0, 60).map(function (r) {
          return '<tr><td>' + esc(r.agent) + '</td><td>' + esc(human(r.metric)) + '</td><td>' + esc(r.book || '—') + '</td><td class="num"' + tipAttr((r.basis || '') + (fin(r.ci_low) ? '\nCI ' + r.ci_low + ' to ' + r.ci_high : '') + '\nunit ' + (r.unit || '') + '\nsource GET /api/command/profitability/scorecards · ' + ts(r.computed_at)) + '>' + esc(fin(r.value) ? (r.unit === 'USD' ? usd(r.value) : sig(r.value)) : 'n/a') + '</td><td class="num">' + esc(r.sample_n != null ? r.sample_n : '—') + '</td><td>' + stPill(r.status || 'UNAVAILABLE', r.reason) + '</td></tr>';
        }).join('') + '</tbody></table></div><p class="pc-note">Economic contribution only; PAPER and ACTUAL rows are separate.</p>';
    }, 'POS-TWIN NOT DEPLOYED');
    return card('scorecards', 'Agent economic contribution', 'scorecards per agent and book', e.state === 'NOT_DEPLOYED' ? null : e, body);
  }

  // ── digital twin ───────────────────────────────────────────────────
  function twin() {
    var e = env('twin');
    var body = guard(e, function (b) {
      var sc = b.scenarios || [];
      var runs = b.runs || {};
      var res = ((b.data || {}).results) || {};
      return kv(Object.keys(runs).slice(0, 6).map(function (k) { return [human(k), runs[k].status, ts(runs[k].started_at)]; })) +
        (sc.length ? '<div class="tablewrap pc-scroll"><table class="pc-table"><thead><tr><th>Scenario</th><th>World</th><th>Version</th><th>Frozen</th><th>Latest result</th></tr></thead><tbody>' +
          sc.map(function (s) {
            var r = res[s.scenario_key] || {};
            var bks = Object.keys(r);
            return '<tr><td class="mono">' + esc(s.scenario_key) + '</td><td>' + esc(s.world) + '</td><td class="num">' + esc(s.version) + '</td><td>' + esc(ts(s.frozen_at)) + '</td><td>' + esc(bks.length ? bks.map(function (k) { var c = (r[k] || {}).comparison || {}; return k + ': ' + (fin(c.diff_usd) ? susd(c.diff_usd) : (r[k] || {}).status || 'see scenario'); }).join(' · ') : 'not run') + '</td></tr>';
          }).join('') + '</tbody></table></div>' : na(b.why || 'no frozen scenario', 'EMPTY')) +
        '<p class="pc-note">Research only; never production truth; books never summed.</p>';
    }, 'POS-TWIN NOT DEPLOYED');
    return card('twin', 'Digital twin', 'frozen scenarios · counterfactual worlds', e.state === 'NOT_DEPLOYED' ? null : e, body);
  }

  // ── risk: kill-switch recommendations ──────────────────────────────
  function risk() {
    var e = env('kills');
    var body = guard(e, function (b) {
      var recs = b.recommendations || [];
      var evals = ((b.data || {}).evaluations) || ((b.data || {}).criteria) || [];
      var html = recs.length ? recs.slice(0, 12).map(function (r) {
        return '<div class="pc-rec"' + tipAttr('applied ' + r.applied + ' · stops capital ' + r.stops_capital + ' · activates capital ' + r.activates_capital + '\nsource GET /api/command/profitability/kill-switches · ' + ts(r.created_at)) + '><b>RECOMMEND_PAUSE</b> · ' + esc(human(r.criterion)) + ' · ' + esc(r.book) + (r.strategy ? ' · ' + esc(r.strategy) : '') + ' <span class="muted">' + esc(ts(r.created_at)) + '</span></div>';
      }).join('') : '<p class="pc-note" style="margin:0 0 6px">No RECOMMEND_PAUSE record.</p>';
      if (Array.isArray(evals) && evals.length) {
        html += '<div class="tablewrap pc-scroll"><table class="pc-table"><thead><tr><th>Criterion</th><th>Book</th><th>Status</th><th>Why</th></tr></thead><tbody>' +
          evals.slice(0, 24).map(function (x) { return '<tr><td>' + esc(human(x.criterion)) + '</td><td>' + esc(x.book || '—') + '</td><td>' + esc(x.status || '—') + '</td><td>' + esc(short(x.why || '', 80)) + '</td></tr>'; }).join('') + '</tbody></table></div>';
      }
      return html + '<p class="pc-note">' + esc(b.effect || 'RECOMMEND_PAUSE_RECORD_ONLY') + ': a recommendation is a record for a human. It stops no capital, activates none, and this page offers no control.</p>';
    }, 'POS-TWIN NOT DEPLOYED');
    return card('risk', 'Risk · kill-switch recommendations', 'RECOMMEND_PAUSE only — never a control', e.state === 'NOT_DEPLOYED' ? null : e, body);
  }

  // ── coverage ───────────────────────────────────────────────────────
  function coverage() {
    var e = env('coverage');
    var body = guard(e, function (b) {
      var ls = b.league_status || {};
      if (ls.status === 'UNAVAILABLE') return na(ls.why, 'UNAVAILABLE');
      var st = ls.statuses || [];
      if (!st.length) return na('no league status for ' + (ls.day || 'today'), 'EMPTY');
      var cnt = {};
      st.forEach(function (r) { cnt[r.status] = (cnt[r.status] || 0) + 1; });
      var cls = {HEALTHY: 'OK', COVERAGE_INCIDENT: 'FAILED', UNAVAILABLE: 'UNAVAILABLE', REFUSING_BY_POLICY: 'INFO', EXPLICITLY_UNSUPPORTED: 'EMPTY'};
      return '<div class="pc-legend" style="margin:0 0 8px">' + Object.keys(cnt).map(function (k) { return '<span><span class="pc-st ' + (cls[k] || 'EMPTY') + '">' + esc(human(k).toUpperCase()) + '</span> ' + cnt[k] + '</span>'; }).join('') + '</div>' +
        '<div class="tablewrap pc-scroll"><table class="pc-table"><thead><tr><th>League</th><th>Status</th><th>Reason</th></tr></thead><tbody>' +
        st.map(function (r) { return '<tr><td>' + esc(r.league_name || r.league) + '</td><td><span class="pc-st ' + (cls[r.status] || 'EMPTY') + '">' + esc(human(r.status).toUpperCase()) + '</span></td><td>' + esc(short(r.reason || '', 110)) + '</td></tr>'; }).join('') + '</tbody></table></div>' +
        '<p class="pc-note">Day ' + esc(ls.day || '—') + ' (' + esc(ls.tz || '') + ').</p>';
    });
    return card('coverage', 'Coverage', 'league status', e, body, {wide: true});
  }

  // ── system health ─────────────────────────────────────────────────
  function health() {
    var ei = env('index'), eo = env('overview'), el = env('lol');
    var rows = Object.keys(READS).map(function (k) {
      var e = env(k);
      return '<tr><td class="mono">' + esc(READS[k].path.replace('/api/command', '')) + '</td><td>' + esc(READS[k].owner) + '</td><td>' + stPill(e.stale ? 'STALE' : e.state, e.why) + '</td><td>' + esc(e.computed_at ? age(e.computed_at) : '—') + '</td><td>' + esc(short(e.why || '', 90)) + '</td></tr>';
    }).join('');
    var comp = '';
    if (ei.state === 'OK') {
      var d = ei.body.data || {};
      comp = kv(Object.keys(d).map(function (k) { return [human(k), d[k].status, (d[k].duration_ms != null ? d[k].duration_ms + ' ms · ' : '') + age(d[k].started_at) + (d[k].error ? ' · ' + d[k].error : ''), 'pos_runs · ' + k + ' · ' + ts(d[k].started_at)]; }));
    }
    var build = '';
    if (eo.state === 'OK') {
      var bm = (eo.body.build_and_mode || {});
      var bd = bm.data || {};
      build = '<p class="pc-note" style="margin:0 0 8px">Serving commit <span class="mono">' + esc(bd.serving_commit ? bd.serving_commit.slice(0, 12) : 'UNAVAILABLE') + '</span> · cycle ' + esc(bd.cycle_state || bm.why || 'UNAVAILABLE') + (bd.last_cycle_age_s != null ? ' · last cycle ' + esc(Math.round(bd.last_cycle_age_s / 60)) + ' min ago' : '') + ' · funded submission ' + esc(bd.funded_submission || 'UNAVAILABLE') + '</p>';
    }
    var ef = env('floor'), ep = env('p5');
    var floorHtml = '';
    if (ef.state === 'OK') {
      var ag = ef.body.agents || [];
      floorHtml = '<h3 class="pc-h3">Agent heartbeats <span class="muted">GET /floor · ' + esc(age(ef.body.read_at) || '') + '</span></h3>' +
        '<div class="tablewrap pc-scroll"><table class="pc-table"><thead><tr><th>Agent</th><th>State</th><th>Heartbeat</th><th>Stale after</th><th>Detail</th></tr></thead><tbody>' +
        ag.map(function (a) {
          var hb = a.heartbeat || {};
          var st = a.state === 'STALE' ? 'STALE' : a.state === 'NOT_DEPLOYED' ? 'NOT_DEPLOYED' : 'OK';
          return '<tr><td>' + esc(a.display_name || a.agent) + '</td><td>' + stPill(st === 'OK' ? a.state : st, a.state_detail) + '</td><td' + tipAttr('source ' + (hb.source || '') + (hb.at ? '\n' + ts(hb.at) : '')) + '>' + esc(fin(hb.age_s) ? age(hb.at) : 'none recorded') + '</td><td class="num">' + esc(fin(hb.stale_after_s) ? Math.round(hb.stale_after_s / 60) + ' min' : '—') + '</td><td>' + esc(short(a.state_detail || '', 90)) + '</td></tr>';
        }).join('') + '</tbody></table></div>';
    } else {
      floorHtml = '<h3 class="pc-h3">Agent heartbeats</h3>' + na(ef.why, ef.state === 'NOT_DEPLOYED' ? 'NOT DEPLOYED' : 'UNAVAILABLE');
    }
    var p5Html = '';
    if (ep.state === 'OK') {
      var pv = ep.body, fb = pv.first_blocking || {}, fx = pv.first_blocking_external || {};
      p5Html = '<h3 class="pc-h3">P5 live-stream evidence <span class="muted">GET /p5/evidence · ' + esc(age(pv.evaluated_at) || '') + '</span></h3>' +
        '<div class="pc-p5 ' + (pv.verdict === 'LIVE_ADMISSIBLE' ? 'ok' : 'blocked') + '"><b>' + esc(pv.verdict || 'UNAVAILABLE') + '</b> · ' + esc((pv.proven || []).length + ' proven, ' + (pv.not_proven || []).length + ' not proven') +
        (fb.predicate ? '<div class="pc-note">First blocking: <span class="mono">' + esc(fb.predicate) + '</span> (' + esc(fb.blocker) + ') — ' + esc(fb.reason || '') + '</div>' : '') +
        (fx.predicate ? '<div class="pc-note">First external blocker: <span class="mono">' + esc(fx.predicate) + '</span> — ' + esc(fx.reason || '') + (fx.action ? ' · action: ' + esc(short(fx.action, 160)) : '') + '</div>' : '') +
        '</div>';
    } else {
      p5Html = '<h3 class="pc-h3">P5 live-stream evidence</h3>' + na(ep.why, ep.state === 'NOT_DEPLOYED' ? 'NOT DEPLOYED' : 'UNAVAILABLE');
    }
    var lolRun = el.state === 'OK' && el.body.last_run ? '<p class="pc-note">Lost-opportunity LEDGER last run ' + esc(el.body.last_run.status) + ' ' + esc(age(el.body.last_run.started_at) || '') + '.</p>' : '';
    return card('health', 'System health', 'every read this page makes', null,
      build + p5Html + floorHtml + '<h3 class="pc-h3">Research cycle</h3>' + comp + lolRun + '<h3 class="pc-h3">Every read on this page</h3><div class="tablewrap pc-scroll"><table class="pc-table"><thead><tr><th>Read</th><th>Owner</th><th>State</th><th>Computed</th><th>Why</th></tr></thead><tbody>' + rows + '</tbody></table></div>', {wide: true});
  }

  // ═══════════════════════════════════════════════════════════════════
  // MOUNT
  // ═══════════════════════════════════════════════════════════════════
  function sec(title) { return '<h2 class="pc-sec-h">' + esc(title) + '</h2>'; }

  function renderFull(el) {
    el.innerHTML = '<div class="pc-grid">' + sleevePanel(false) + '</div>' + answers() + heroTiles() +
      sec('North star') + '<div class="pc-grid">' + northStar() + '</div>' +
      sec('Today') + '<div class="pc-grid">' + todaysOpportunity() + blockersCard() + todaysAttribution() + capacity() + topOpportunities(12) + '</div>' +
      sec('Capital') + '<div class="pc-grid">' + capital() + drawdown() + '</div>' +
      sec('Forecast') + '<div class="pc-grid">' + forecast() + '</div>' +
      sec('Lost opportunities') + '<div class="pc-grid">' + lostOpportunities(false) + '</div>' +
      sec('Evidence & learning') + '<div class="pc-grid">' + ladder() + risk() + tournaments() + scorecards() + twin() + '</div>' +
      sec('Coverage & health') + '<div class="pc-grid">' + coverage() + health() + '</div>';
  }
  function renderCompact(el) {
    el.classList.add('pc-compact');
    el.innerHTML = '<div class="pc-grid">' + sleevePanel(true) + '</div>' + answers() + heroTiles() + '<div class="pc-grid">' + todaysOpportunity() + blockersCard() + topOpportunities(3) + lostOpportunities(true) + '</div>' +
      '<a class="pc-compact-link" href="profitability.html">Open the full profitability cockpit →</a>';
  }

  // ── tooltip (one floating element; hover and keyboard focus) ────────
  var tipEl = null;
  var redraw = null;
  function tipShow(target, x, y) {
    var t = target.getAttribute('data-tip');
    if (!t) return;
    if (!tipEl) { tipEl = document.createElement('div'); tipEl.className = 'pc-tip'; tipEl.setAttribute('role', 'tooltip'); document.body.appendChild(tipEl); }
    tipEl.textContent = t;
    tipEl.style.display = 'block';
    var w = tipEl.offsetWidth, h = tipEl.offsetHeight;
    var vw = window.innerWidth, vh = window.innerHeight;
    var left = Math.min(Math.max(8, x + 12), vw - w - 8);
    var top = y + 14 + h > vh ? y - h - 10 : y + 14;
    tipEl.style.left = left + 'px';
    tipEl.style.top = Math.max(8, top) + 'px';
  }
  function tipHide() { if (tipEl) tipEl.style.display = 'none'; }
  function wireTips(el) {
    el.addEventListener('mousemove', function (ev) {
      var t = ev.target.closest && ev.target.closest('[data-tip]');
      if (t) tipShow(t, ev.clientX, ev.clientY); else tipHide();
    });
    el.addEventListener('mouseleave', tipHide);
    el.addEventListener('focusin', function (ev) {
      var t = ev.target.closest && ev.target.closest('[data-tip]');
      if (t) { var r = t.getBoundingClientRect(); tipShow(t, r.left, r.bottom); }
    });
    el.addEventListener('focusout', tipHide);
    el.addEventListener('click', function (ev) {
      var sb = ev.target.closest && ev.target.closest('[data-sleeve]');
      if (sb) {
        var v = sb.getAttribute('data-sleeve');
        if (v === state.sleeve) return;
        state.sleeve = v;
        try { localStorage.setItem(SLEEVE_KEY, v); } catch (e) { /* per-viewer convenience only */ }
        READS.sleeves.q = '?sleeve=' + v;
        delete state.results.sleeves;
        if (redraw) redraw();
        read('sleeves').then(function () { if (redraw) redraw(); });
        return;
      }
      var b = ev.target.closest && ev.target.closest('[data-capsel]');
      if (b) { capSel = +b.getAttribute('data-capsel'); var c = el.querySelector('#pc-capacity'); if (c) c.outerHTML = capacity(); }
    });
  }

  function overall() {
    var keys = Object.keys(state.results);
    if (keys.some(function (k) { return env(k).state === 'AUTH'; })) return ['SIGN IN REQUIRED', 'pill-warn'];
    var core = ['ns', 'capital', 'capacity'].map(env);
    if (core.every(function (e) { return e.state === 'OK'; })) return core.some(function (e) { return e.stale; }) ? ['STALE', 'pill-warn'] : ['RESEARCH READ OK', 'pill-good'];
    if (core.some(function (e) { return e.state === 'OK'; })) return ['PARTIAL — SEE HEALTH', 'pill-warn'];
    return ['UNAVAILABLE', 'pill-bad'];
  }

  function mount(el, opts) {
    opts = opts || {};
    if (!el) return null;
    var compact = !!opts.compact;
    var keys = compact ? COMPACT_READS : Object.keys(READS);
    el.innerHTML = '<div class="deskstate" role="status" aria-live="polite">Reading the profitability read models…</div>';
    wireTips(el);
    redraw = function () { if (compact) renderCompact(el); else renderFull(el); };
    function cycle() {
      return Promise.all(keys.map(read)).then(function () {
        state.readAt = Date.now() / 1000;
        if (compact) renderCompact(el); else renderFull(el);
        if (!state.first) el.classList.add('pc-fresh');
        state.first = false;
        var pill = document.getElementById('pc-pill');
        if (pill && !compact) { var o = overall(); pill.textContent = o[0]; pill.className = 'pill ' + o[1]; }
        var asof = document.getElementById('pc-asof');
        if (asof && !compact) asof.textContent = 'Read ' + ts(state.readAt) + ' · refreshes every ' + Math.round(POLL_MS / 60000) + ' min';
      });
    }
    cycle();
    var timer = opts.poll === false ? null : setInterval(function () { if (!document.hidden) cycle(); }, POLL_MS);
    return {refresh: cycle, stop: function () { if (timer) clearInterval(timer); }};
  }

  function mountEquityWall() {
    var slot = document.getElementById('pc-equity');
    if (!slot) return;
    var W = root.BTEquityWall;
    if (W && typeof W.mount === 'function') {
      try { W.mount(slot, {compact: true}); slot.classList.add('mounted'); return; } catch (e) {
        slot.textContent = 'LIVE EQUITY WALL — the component failed to mount: ' + (e && e.message);
        return;
      }
    }
    slot.innerHTML = '<span class="pill pill-grey">LIVE EQUITY WALL</span><span>UNAVAILABLE — equity-wall.js did not load on this page, so the live PAPER / SMALL LIVE equity is not shown here. The figures below are unaffected.</span>';
  }

  root.BTCockpit = {mount: mount, version: '1.0.0', reads: READS};

  function boot() {
    mountEquityWall();
    var el = document.getElementById('pc-root');
    if (el) mount(el, {compact: false});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})(window);
