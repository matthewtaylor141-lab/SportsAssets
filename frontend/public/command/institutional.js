/* BETTOR EV ENGINE · INSTITUTIONAL VIEW — rendered from the read models.
 *
 * FIVE QUESTIONS, ANSWERED ONLY FROM WHAT THE SERVER SENDS:
 *   1 What does BETTOR own (PAPER and ACTUAL separately)?
 *   2 Why (thesis / decision)?
 *   3 What is each position worth now?
 *   4 What can go wrong (risk / regime)?
 *   5 Is BETTOR adding value (attribution, Xavier value-add, calibration,
 *     quality)?
 *
 * EVERY READ IS AN AUTHENTICATED SAME-ORIGIN GET under /api/command/ that
 * passes BTCore.endpoint(); the only query strings appended are fixed
 * literals in READS below. Some reads are built by other workstreams and may
 * not exist yet: a 404, a 5xx, a network failure or a non-JSON body renders
 * that section as "unavailable" with the reason -- never as zero, and never
 * by inventing a figure. PAPER and ACTUAL are never summed here. Nothing on
 * this page writes, and no venue is contacted.
 */
(function () {
  'use strict';

  var C = window.BTCore;
  var POLL_MS = 60000;
  var READS = {
    smallLive: {path: '/api/command/small-live', q: ''},
    paperXavier: {path: '/api/command/paper/xavier', q: ''},
    agents: {path: '/api/command/agents', q: ''},
    xavierMgmt: {path: '/api/command/xavier/management', q: ''},
    karen: {path: '/api/command/karen', q: ''},
    allocator: {path: '/api/command/intel/allocator', q: ''},
    calibration: {path: '/api/command/intel/calibration', q: ''},
    attribution: {path: '/api/command/intel/attribution', q: ''},
    sizing: {path: '/api/command/intel/sizing', q: ''},
    risk: {path: '/api/command/intel/risk', q: ''},
    regime: {path: '/api/command/intel/regime', q: ''},
    coverage: {path: '/api/command/coverage', q: '?tz=America/New_York&days=7'},
    postmortems: {path: '/api/command/postmortems', q: '?limit=25'},
    quality: {path: '/api/command/quality', q: ''},
    findings: {path: '/api/command/agents/findings', q: ''}
  };
  var SECTIONS = [
    {id: 'paper', title: 'PAPER', sub: 'what BETTOR owns · simulated execution', reads: ['paperXavier', 'smallLive'], render: renderPaper},
    {id: 'actual', title: 'ACTUAL', sub: 'what BETTOR owns · live venue account', reads: ['smallLive'], render: renderActual},
    {id: 'portfolio', title: 'PORTFOLIO', sub: 'what each position is worth now · attribution and sizing', reads: ['attribution', 'sizing'], render: renderMany},
    {id: 'derek', title: 'DEREK', sub: 'why: the thesis and the decision', reads: ['agents'], render: renderDerek},
    {id: 'xavier', title: 'XAVIER', sub: 'management against holding', reads: ['xavierMgmt'], render: renderMany},
    {id: 'audrey', title: 'AUDREY', sub: 'postmortems of every closed position', reads: ['postmortems'], render: renderPostmortems},
    {id: 'karen', title: 'KAREN', sub: 'challenges', reads: ['karen'], render: renderMany},
    {id: 'allocator', title: 'ALLOCATOR', sub: 'capital allocation (recommendation only)', reads: ['allocator'], render: renderMany},
    {id: 'risk', title: 'RISK', sub: 'what can go wrong · risk and regime', reads: ['risk', 'regime'], render: renderMany},
    {id: 'calibration', title: 'CALIBRATION', sub: 'is the probability any good', reads: ['calibration', 'quality'], render: renderCalibration},
    {id: 'execution', title: 'EXECUTION', sub: 'fill rate, slippage, admission', reads: ['quality'], render: renderExecution},
    {id: 'coverage', title: 'COVERAGE', sub: 'provider → fill funnel per league per day', reads: ['coverage'], render: renderCoverage, wide: true},
    {id: 'quality', title: 'QUALITY', sub: 'the five-domain scorecard', reads: ['quality'], render: renderQuality, wide: true},
    {id: 'collab', title: 'AGENT COLLABORATION', sub: 'evidence → hypothesis → peer challenge → paper experiment', reads: ['findings', 'agents'], render: renderCollab, wide: true}
  ];
  var state = {results: {}, timer: null, unlockOffered: false};

  /* ── formatting: a missing figure is "unavailable", never 0 ───────── */
  var NA = '<span class="sl-na">unavailable</span>';
  function esc(v) { return C ? C.esc(v) : String(v == null ? '' : v); }
  function has(v) { return v !== null && v !== undefined && v !== ''; }
  function num(v) { return (typeof v === 'number' && isFinite(v)) ? v : null; }
  function isObj(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
  function txt(v) { return has(v) ? esc(v) : NA; }
  function usd(v) {
    var n = num(v);
    if (n === null) { return NA; }
    return esc((n < 0 ? '−$' : '$') + Math.abs(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}));
  }
  function share(v) { var n = num(v); return n === null ? NA : esc((n * 100).toFixed(1) + '%'); }
  function fmt(v) {
    if (v === null || v === undefined || v === '') { return NA; }
    if (typeof v === 'number') { return isFinite(v) ? esc(Math.abs(v) >= 1000 ? v.toLocaleString('en-US', {maximumFractionDigits: 2}) : String(Number(v.toFixed(6)))) : NA; }
    if (typeof v === 'boolean') { return v ? 'yes' : 'no'; }
    if (Array.isArray(v)) { return esc(v.length + ' item' + (v.length === 1 ? '' : 's')); }
    if (isObj(v)) { return esc(Object.keys(v).length + ' field' + (Object.keys(v).length === 1 ? '' : 's')); }
    var s = String(v);
    return esc(s.length > 160 ? s.slice(0, 157) + '…' : s);
  }
  function pill(text, tone) { return '<span class="pill pill-' + (tone || 'grey') + '">' + esc(text) + '</span>'; }
  function fact(k, v) { return '<div class="fact"><span class="k">' + esc(k) + '</span><span class="v">' + v + '</span></div>'; }
  function label(k) { return String(k).replace(/_/g, ' '); }
  function tone(s) {
    s = String(s || '').toUpperCase();
    if (/^(OK|MEASURED|ALIGNED|MATCHED|SUSTAINED|SUPPORTED|ENABLED|IMPROVING|COMPLETE)/.test(s)) { return 'good'; }
    if (/(UNAVAILABLE|ERROR|CRITICAL|MISALIGNED|REFUTED|NOT_SUPPORTED|DETERIORATING|STOPPED)/.test(s)) { return 'bad'; }
    if (/(INSUFFICIENT|UNPROVEN|WARNING|EMPTY|STALE|PENDING)/.test(s)) { return 'warn'; }
    return 'grey';
  }

  /* ── finding things in shapes this page did not define ───────────── */
  function findArray(o, names, depth) {
    if (!o || (depth || 0) > 3) { return null; }
    if (Array.isArray(o)) { return null; }
    if (!isObj(o)) { return null; }
    for (var i = 0; i < names.length; i++) { if (Array.isArray(o[names[i]])) { return o[names[i]]; } }
    var ks = Object.keys(o);
    for (var j = 0; j < ks.length; j++) {
      var got = findArray(o[ks[j]], names, (depth || 0) + 1);
      if (got) { return got; }
    }
    return null;
  }
  function findScalar(o, names, depth) {
    if (!isObj(o) || (depth || 0) > 3) { return null; }
    for (var i = 0; i < names.length; i++) {
      var v = o[names[i]];
      if (v !== null && v !== undefined && typeof v !== 'object') { return v; }
    }
    var ks = Object.keys(o);
    for (var j = 0; j < ks.length; j++) {
      var got = findScalar(o[ks[j]], names, (depth || 0) + 1);
      if (got !== null && got !== undefined) { return got; }
    }
    return null;
  }

  /* ── the generic renderer: facts, then up to two tables ─────────── */
  function scalarsOf(o, max) {
    var out = [];
    Object.keys(o || {}).forEach(function (k) {
      var v = o[k];
      if (out.length < max && (v === null || typeof v !== 'object')) { out.push([k, v]); }
    });
    return out;
  }
  function table(rows, max) {
    rows = (rows || []).filter(isObj);
    if (!rows.length) { return '<p class="muted">No rows.</p>'; }
    var cols = [];
    rows.slice(0, 10).forEach(function (r) {
      Object.keys(r).forEach(function (k) {
        var v = r[k];
        if (cols.length < 7 && cols.indexOf(k) < 0 && (v === null || typeof v !== 'object')) { cols.push(k); }
      });
    });
    var body = rows.slice(0, max || 8).map(function (r) {
      return '<tr>' + cols.map(function (c) {
        return '<td' + (typeof r[c] === 'number' ? ' class="num"' : '') + '>' + fmt(r[c]) + '</td>';
      }).join('') + '</tr>';
    }).join('');
    var more = rows.length > (max || 8) ? '<p class="muted">' + esc(rows.length - (max || 8)) + ' more not shown.</p>' : '';
    return '<div class="tablewrap"><table class="dt"><thead><tr>' +
      cols.map(function (c) { return '<th>' + esc(label(c)) + '</th>'; }).join('') +
      '</tr></thead><tbody>' + body + '</tbody></table></div>' + more;
  }
  function generic(d) {
    if (!isObj(d)) { return Array.isArray(d) ? table(d) : '<p>' + fmt(d) + '</p>'; }
    var html = '';
    if (has(d.status)) { html += '<p>' + pill(d.status, tone(d.status)) + (has(d.why) ? ' <span class="sl-why">' + esc(d.why) + '</span>' : '') + '</p>'; }
    var facts = scalarsOf(d, 12).filter(function (kv) { return kv[0] !== 'status' && kv[0] !== 'why'; });
    if (facts.length) {
      html += '<div class="facts three">' + facts.map(function (kv) { return fact(label(kv[0]), fmt(kv[1])); }).join('') + '</div>';
    }
    var tables = 0;
    Object.keys(d).forEach(function (k) {
      if (tables < 2 && Array.isArray(d[k]) && d[k].length && isObj(d[k][0])) {
        html += '<h3 class="h3">' + esc(label(k)) + '</h3>' + table(d[k]);
        tables++;
      }
    });
    var nested = 0;
    Object.keys(d).forEach(function (k) {
      if (nested < 3 && isObj(d[k])) {
        var s = scalarsOf(d[k], 8);
        if (s.length) {
          html += '<h3 class="h3">' + esc(label(k)) + '</h3><div class="facts three">' +
            s.map(function (kv) { return fact(label(kv[0]), fmt(kv[1])); }).join('') + '</div>';
          nested++;
        }
      }
    });
    return html || '<p class="muted">The read returned no displayable fields.</p>';
  }
  function unavailable(key) {
    var r = state.results[key] || {};
    return '<div class="verdict"><b>' + esc(READS[key].path) + ' is unavailable</b>' +
      esc(r.error || 'not read yet') + '<br>An unread view shows no numbers rather than zeros.</div>';
  }
  function each(keys, fn) {
    return keys.map(function (k) {
      var r = state.results[k];
      if (!r || !r.ok) { return unavailable(k); }
      return fn(r.data, k);
    }).join('');
  }
  function renderMany(keys) {
    return each(keys, function (d, k) {
      return (keys.length > 1 ? '<h3 class="h3">' + esc(READS[k].path) + '</h3>' : '') + generic(d);
    });
  }

  /* ── PAPER / ACTUAL: never summed ───────────────────────────────── */
  function positionsTable(rows) {
    rows = (rows || []).filter(isObj);
    if (!rows.length) { return '<p class="muted">No open positions in this read.</p>'; }
    return table(rows.map(function (p) {
      return {market: p.us_market_slug || p.market || p.title || p.position_key,
              side: p.holding_side || p.side, qty: num(p.open_qty) !== null ? p.open_qty : (p.qty !== undefined ? p.qty : p.live_held),
              cost_basis_usd: p.cost_basis_usd, mark_value_usd: has(p.mark_value_usd) ? p.mark_value_usd : p.marked_value_usd,
              unrealized_usd: has(p.unrealized_usd) ? p.unrealized_usd : p.unrealized_pnl_usd,
              thesis: p.thesis || p.reason || p.strategy};
    }), 12);
  }
  function renderPaper() {
    var px = state.results.paperXavier, sl = state.results.smallLive;
    var html = '';
    if (px && px.ok) {
      html += '<p class="sl-basis">' + pill('PAPER — SIMULATED', 'blue') + ' positions Xavier manages on the paper book</p>' +
        positionsTable(findArray(px.data, ['positions', 'open_positions']));
    } else { html += unavailable('paperXavier'); }
    if (sl && sl.ok) {
      var rows = (findArray(sl.data, ['rows']) || []).filter(function (r) { return isObj(r) && isObj(r.paper); });
      html += '<h3 class="h3">Paper orders in the small-live mirror</h3>' +
        (rows.length ? table(rows.map(function (r) { return r.paper; })) : '<p class="muted">No mirrored paper rows.</p>');
    }
    return html;
  }
  function renderActual() {
    var sl = state.results.smallLive;
    if (!sl || !sl.ok) { return unavailable('smallLive'); }
    var d = sl.data || {};
    var acct = isObj(d.account) ? d.account : {};
    var control = isObj(d.control) ? d.control : {};
    var html = '<p class="sl-basis">' + pill('ACTUAL — VENUE', 'good') + ' read from the venue\'s own records</p>' +
      '<div class="facts three">' + fact('Mirror', has(control.state) ? pill(control.state, tone(control.state)) : NA) +
      fact('Account snapshot', txt(acct.status)) + fact('Snapshot at', txt(acct.at)) + '</div>';
    var pos = findArray(acct, ['positions']) || findArray(d, ['actual_positions', 'handoffs']);
    html += '<h3 class="h3">Actual positions</h3>' + positionsTable(pos);
    var rows = (findArray(d, ['rows']) || []).filter(function (r) { return isObj(r) && isObj(r.actual); });
    if (rows.length) { html += '<h3 class="h3">Actual orders</h3>' + table(rows.map(function (r) { return r.actual; })); }
    return html;
  }

  /* ── DEREK: the thesis ──────────────────────────────────────────── */
  function renderDerek() {
    return each(['agents'], function (d) {
      var derek = isObj(d.derek) ? d.derek : (findArray(d, ['agents']) || []).filter(function (a) {
        return isObj(a) && String(a.name || a.agent || a.id || '').toUpperCase() === 'DEREK';
      })[0];
      return generic(derek || d);
    });
  }

  /* ── AUDREY: postmortems, PAPER and ACTUAL apart ────────────────── */
  var COMP = [['selection_edge_usd', 'Selection edge'], ['execution_slippage_usd', 'Execution slippage'],
              ['fees_usd', 'Fees'], ['management_value_usd', 'Management (Xavier vs hold)'],
              ['outcome_variance_usd', 'Outcome variance']];
  function bookBlock(name, b) {
    if (!isObj(b)) { return '<div class="book"><h3>' + esc(name) + '</h3>' + NA + '</div>'; }
    var s = b.summary || {};
    var comps = COMP.map(function (c) {
      var x = s[c[0]] || {};
      return fact(c[1], usd(x.sum) + (num(x.unmeasured) ? ' <span class="sl-why">' + esc(x.unmeasured) + ' unmeasured</span>' : ''));
    }).join('');
    var rows = (b.positions || []).map(function (p) {
      return {position: p.us_market_slug || p.position_key, closed: p.closed_at ? new Date(p.closed_at * 1000).toISOString().slice(0, 16).replace('T', ' ') : null,
              realized_usd: p.realized_pnl_usd, selection: p.selection_edge_usd, slippage: p.execution_slippage_usd,
              fees: p.fees_usd, management: p.management_value_usd, variance: p.outcome_variance_usd, unexplained: p.unexplained_usd};
    });
    return '<div class="book"><h3>' + esc(name) + ' ' + pill(b.status || '—', tone(b.status)) + '</h3>' +
      '<div class="facts three">' + fact('Closed positions', fmt(s.positions)) + fact('Realized P&L', usd(s.realized_pnl_usd)) +
      fact('Unexplained', usd(s.unexplained_usd)) + comps + '</div>' +
      (rows.length ? table(rows, 10) : '<p class="muted">' + esc(b.why || 'No closed positions.') + '</p>') + '</div>';
  }
  function renderPostmortems() {
    return each(['postmortems'], function (d) {
      return '<p class="iv-src">' + esc(d.identity || '') + '</p>' + bookBlock('PAPER', d.paper) + bookBlock('ACTUAL', d.actual);
    });
  }

  /* ── QUALITY / CALIBRATION / EXECUTION ─────────────────────────── */
  function metricCard(m) {
    var v = m.value;
    var shown = v === null || v === undefined ? NA
      : (m.unit === 'share' ? share(v) : fmt(v));
    var ci = isObj(m.ci) ? ('CI ' + fmt(m.ci.low) + ' … ' + fmt(m.ci.high) + ' (' + esc(m.ci.method) + ')') : 'CI n/a';
    var tr = isObj(m.trend) ? (has(m.trend.direction) ? esc(m.trend.direction) + ' vs prior ' + fmt(m.trend.prior_value) : 'trend: ' + esc(m.trend.why || 'n/a')) : 'trend n/a';
    var nd = (has(m.numerator) || has(m.denominator)) ? fmt(m.numerator) + ' / ' + fmt(m.denominator) : '';
    return '<div class="iv-metric"><div class="row1"><span class="nm">' + esc(m.name) + '</span>' +
      '<span>' + pill(m.status, tone(m.status)) + ' <span class="val">' + shown + '</span></span></div>' +
      '<div class="meta">' + (nd ? nd + ' · ' : '') + 'n=' + fmt(m.sample) + ' · ' + ci + ' · ' + tr +
      (m.last_measured_at ? ' · measured ' + esc(new Date(m.last_measured_at * 1000).toISOString().slice(0, 16).replace('T', ' ')) : '') + '</div>' +
      (has(m.blocker) || has(m.why) ? '<div class="meta">Blocker: ' + esc(m.blocker || m.why) + '</div>' : '') +
      (has(m.next_improvement) ? '<div class="meta">Next: ' + esc(m.next_improvement) + '</div>' : '') + '</div>';
  }
  function domain(d, name) { return (isObj(d) && isObj(d.domains) && Array.isArray(d.domains[name])) ? d.domains[name] : null; }
  function renderQuality() {
    return each(['quality'], function (d) {
      var p = d.profitability || {};
      var html = '<div class="verdict"><b>Profitability: PAPER ' + esc(p.paper || 'unavailable') + ' · ACTUAL ' + esc(p.actual || 'unavailable') + '</b>' +
        esc((p.rule && p.rule.test) || '') + '</div>';
      ['ENGINEERING', 'AGENTS', 'INVESTMENT_INTELLIGENCE', 'EXECUTION', 'PROFITABILITY_EVIDENCE'].forEach(function (k) {
        var ms = domain(d, k);
        html += '<div class="iv-dom">' + esc(label(k)) + '</div>' + (ms ? ms.map(metricCard).join('') : NA);
      });
      return html;
    });
  }
  function renderCalibration() {
    var html = '';
    var c = state.results.calibration;
    html += (c && c.ok) ? generic(c.data) : unavailable('calibration');
    var q = state.results.quality;
    var ms = q && q.ok ? domain(q.data, 'INVESTMENT_INTELLIGENCE') : null;
    if (ms) { html += '<h3 class="h3">From the quality scorecard</h3>' + ms.map(metricCard).join(''); }
    return html;
  }
  function renderExecution() {
    return each(['quality'], function (d) {
      var ms = domain(d, 'EXECUTION');
      return ms ? ms.map(metricCard).join('') : NA;
    });
  }

  /* ── COVERAGE: the funnel ──────────────────────────────────────── */
  var STAGE_COLS = [['provider_events', 'Provider'], ['normalized_events', 'Normalized'], ['venue_discovered', 'Venue'],
                    ['mapped_events', 'Mapped'], ['settlement_supported', 'Settle-supp.'], ['evaluated_events', 'Evaluated'],
                    ['entered_events', 'ENTER'], ['refused_events', 'REFUSE'], ['ordered_events', 'Order'], ['filled_events', 'Fill']];
  function renderCoverage() {
    return each(['coverage'], function (d) {
      var days = Array.isArray(d.days) ? d.days : [];
      var alerts = Array.isArray(d.alerts) ? d.alerts : [];
      var html = '<p class="iv-src">Unit: ' + esc(d.unit || '—') + ' · day: ' + esc(d.tz || '—') +
        (d.today_computed_live ? ' · today computed live (not yet persisted)' : '') + '</p>';
      html += alerts.length ? '<div class="verdict"><b>' + esc(alerts.length) + ' collapse alert(s)</b><ul class="sl-reasons">' +
        alerts.slice(0, 8).map(function (a) {
          return '<li>' + pill(a.severity, tone(a.severity)) + ' ' + esc(a.league_name || a.league) + ' · ' + esc(a.kind) + ' at ' +
            esc(a.stage_to) + ' · ' + esc(a.day) + (a.audrey_finding_id ? ' · Audrey finding ' + '<span class="mono">' + esc(a.audrey_finding_id) + '</span>' : ' · ' + esc(a.audrey_refusal || '')) + '</li>';
        }).join('') + '</ul></div>' : '<p class="muted">No collapse alert in the window.</p>';
      if (!days.length) { return html + '<p class="muted">' + esc(d.why || 'No funnel rows.') + '</p>'; }
      var day = days[0];
      var rows = (day.leagues || []).filter(isObj);
      html += '<h3 class="h3">' + esc(day.day) + '</h3><div class="tablewrap"><table class="dt"><thead><tr><th>League</th>' +
        STAGE_COLS.map(function (c) { return '<th class="num">' + esc(c[1]) + '</th>'; }).join('') + '</tr></thead><tbody>' +
        rows.map(function (r) {
          return '<tr><td>' + esc(r.league_name || r.league) + '</td>' + STAGE_COLS.map(function (c) {
            var v = r[c[0]];
            return '<td class="num">' + (v === null || v === undefined ? NA : esc(v)) + '</td>';
          }).join('') + '</tr>';
        }).join('') + '</tbody></table></div>';
      return html;
    });
  }

  /* ── AGENT COLLABORATION ───────────────────────────────────────── */
  function renderCollab() {
    var f = state.results.findings;
    if (!f || !f.ok) { return unavailable('findings'); }
    var rows = findArray(f.data, ['findings', 'rows', 'items']) || (Array.isArray(f.data) ? f.data : []);
    if (!rows.length) { return generic(f.data); }
    return table(rows.map(function (x) {
      return {finding: x.title || x.finding_id, proposer: x.proposer, stage: x.stage, updated: x.updated_at,
              production_effect: x.production_effect};
    }), 12);
  }

  /* ── THE FIVE QUESTIONS ────────────────────────────────────────── */
  function data(k) { var r = state.results[k]; return r && r.ok ? r.data : null; }
  function count(a) { return Array.isArray(a) ? a.length : null; }
  function questions() {
    var px = data('paperXavier'), sl = data('smallLive'), pm = data('postmortems'), q = data('quality');
    var paperN = count(findArray(px, ['positions', 'open_positions']));
    var actualN = count(findArray(sl && sl.account, ['positions']) || findArray(sl, ['actual_positions', 'handoffs']));
    var thesis = findScalar(data('agents'), ['thesis', 'rationale', 'reason', 'summary']);
    var worth = findScalar(data('attribution'), ['marked_value_usd', 'portfolio_value_usd', 'equity_usd', 'mark_value_usd']);
    var risk = findScalar(data('regime'), ['regime', 'label', 'state']) || findScalar(data('risk'), ['verdict', 'status', 'state']);
    var mgmt = pm && pm.paper && pm.paper.summary && pm.paper.summary.management_value_usd ? pm.paper.summary.management_value_usd.sum : null;
    var prof = q && q.profitability ? q.profitability : {};
    var cal = (domain(q, 'INVESTMENT_INTELLIGENCE') || [])[0];
    var qs = [
      ['1', 'What does BETTOR own?', 'PAPER ' + (paperN === null ? NA : esc(paperN) + ' open') + ' · ACTUAL ' + (actualN === null ? NA : esc(actualN) + ' open') + ' — never summed'],
      ['2', 'Why?', has(thesis) ? fmt(thesis) : NA],
      ['3', 'What is it worth now?', num(worth) !== null ? usd(worth) : NA],
      ['4', 'What can go wrong?', has(risk) ? pill(risk, tone(risk)) : NA],
      ['5', 'Is BETTOR adding value?', 'Profitability ' + pill(prof.paper || 'unavailable', tone(prof.paper || 'UNAVAILABLE')) +
        ' · Xavier vs hold ' + usd(mgmt) + ' · calibration ' + (cal && cal.value !== null && cal.value !== undefined ? fmt(cal.value) + ' (' + esc(cal.status) + ')' : NA)]
    ];
    document.getElementById('ivquestions').innerHTML = qs.map(function (x) {
      return '<div class="iv-q"><div class="n">Question ' + esc(x[0]) + '</div><div class="t">' + esc(x[1]) + '</div><div class="a">' + x[2] + '</div></div>';
    }).join('');
  }

  /* ── rendering and reading ─────────────────────────────────────── */
  function sources(keys) {
    return '<p class="iv-src">' + keys.map(function (k) {
      var r = state.results[k];
      var s = !r ? 'READING' : r.ok ? 'OK' : (r.http ? 'HTTP ' + r.http : 'ERROR');
      return pill(s, !r ? 'grey' : r.ok ? 'good' : 'bad') + '<span class="mono">' + esc(READS[k].path) + '</span>';
    }).join(' &nbsp; ') + '</p>';
  }
  function render() {
    var grid = document.getElementById('ivgrid');
    grid.innerHTML = SECTIONS.map(function (s) {
      var body;
      try { body = s.render(s.reads); } catch (e) { body = '<div class="verdict"><b>This section could not be rendered</b>' + esc(e && e.message) + '</div>'; }
      return '<section class="card' + (s.wide ? ' wide' : '') + '" id="iv-' + esc(s.id) + '" aria-label="' + esc(s.title) + '">' +
        '<h2>' + esc(s.title) + ' <span class="sub">' + esc(s.sub) + '</span></h2><div class="card-body">' +
        sources(s.reads) + body + '</div></section>';
    }).join('');
    questions();
    var keys = Object.keys(READS);
    var ok = keys.filter(function (k) { return state.results[k] && state.results[k].ok; }).length;
    var p = document.getElementById('iv-pill');
    if (p) { p.className = 'pill pill-' + (ok === keys.length ? 'good' : ok ? 'warn' : 'bad'); p.textContent = ok + ' / ' + keys.length + ' READS'; }
    document.getElementById('ivstate').textContent = ok === keys.length ? '' :
      (keys.length - ok) + ' read(s) unavailable; their sections say so and show no numbers.';
    var asof = document.getElementById('asof');
    if (asof) { asof.textContent = 'Read at ' + new Date().toISOString().replace('.000', ''); }
  }
  function read(key) {
    var r = READS[key];
    var path;
    try { path = C.endpoint(r.path); } catch (e) {
      state.results[key] = {ok: false, error: 'not an allowed read endpoint'};
      return Promise.resolve();
    }
    return fetch(path + r.q, {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}})
      .then(function (resp) {
        if (resp.status === 401 || resp.status === 403) {
          if (!state.unlockOffered && window.BTUnlock && window.BTUnlock.open) { state.unlockOffered = true; window.BTUnlock.open(); }
          state.results[key] = {ok: false, http: resp.status, error: 'needs a Command session (HTTP ' + resp.status + ')'};
          return;
        }
        if (!resp.ok) {
          state.results[key] = {ok: false, http: resp.status, error: resp.status === 404 ? 'not deployed on this build (HTTP 404)' : 'HTTP ' + resp.status};
          return;
        }
        return resp.json().then(function (j) { state.results[key] = {ok: true, data: j}; },
          function () { state.results[key] = {ok: false, error: 'the body was not JSON'}; });
      })
      .catch(function (e) { state.results[key] = {ok: false, error: (e && e.message) || 'network failure'}; });
  }
  function load() {
    return Promise.all(Object.keys(READS).map(read)).then(render, render);
  }
  function start() {
    if (!C || typeof C.endpoint !== 'function') {
      document.getElementById('ivstate').innerHTML = '<div class="verdict"><b>core.js did not load</b>The read guard is absent, so nothing is read.</div>';
      return;
    }
    render();
    load();
    state.timer = setInterval(load, POLL_MS);
  }

  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', start); } else { start(); }

  window.BettorInstitutional = {load: load, render: render, READS: READS, SECTIONS: SECTIONS, VERSION: '1.0.0'};
}());
