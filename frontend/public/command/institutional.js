/* BETTOR EV ENGINE · INSTITUTIONAL VIEW — rendered from the read models.
 *
 * FIVE QUESTIONS, ANSWERED ONLY FROM WHAT THE SERVER SENDS:
 *   1 What does BETTOR own (PAPER and ACTUAL separately)?
 *   2 Why (thesis / decision)?
 *   3 What is each position worth now?
 *   4 What can go wrong (risk / regime / the P5 stream gate)?
 *   5 Is BETTOR adding value (postmortems, Xavier vs hold, calibration,
 *     Karen, the forward-sample verdict)?
 *
 * EVERY READ IS AN AUTHENTICATED SAME-ORIGIN GET under /api/command/ that
 * passes BTCore.endpoint(); the only query strings appended are the fixed
 * literals in READS below. Field names are the ones the routes return
 * (pinned by backend/tests/fixtures/institutional/, captured from the real
 * routes). A failed read (404, 5xx, network, non-JSON), an envelope that
 * says UNAVAILABLE or EMPTY, or a field the server leaves null renders as
 * "UNAVAILABLE — <why>" with the server's own reason: never as 0 and never
 * as an invented figure. PAPER and ACTUAL are never summed. Everything from
 * /api/command/intel/* is SHADOW (no authority) and is labelled so; nothing
 * shadow is ever called live. Nothing on this page writes, and no venue is
 * contacted.
 */
(function (root) {
  'use strict';

  var C = root.BTCore;
  var POLL_MS = 60000;
  var READS = {
    intel: {path: '/api/command/intel', q: ''},
    allocator: {path: '/api/command/intel/allocator', q: ''},
    calibration: {path: '/api/command/intel/calibration', q: ''},
    attribution: {path: '/api/command/intel/attribution', q: ''},
    sizing: {path: '/api/command/intel/sizing', q: ''},
    risk: {path: '/api/command/intel/risk', q: ''},
    regime: {path: '/api/command/intel/regime', q: ''},
    karen: {path: '/api/command/karen', q: ''},
    karenChallenges: {path: '/api/command/karen/challenges', q: '?limit=50'},
    xavierMgmt: {path: '/api/command/xavier/management', q: ''},
    coverage: {path: '/api/command/coverage', q: '?tz=America/New_York&days=7'},
    postmortems: {path: '/api/command/postmortems', q: '?limit=25'},
    quality: {path: '/api/command/quality', q: ''},
    p5: {path: '/api/command/p5/evidence', q: ''},
    smallLive: {path: '/api/command/small-live', q: '?limit=25'},
    agents: {path: '/api/command/agents', q: ''},
    findings: {path: '/api/command/agents/findings', q: ''}
  };
  var SHADOW_KEYS = ['intel', 'allocator', 'calibration', 'attribution', 'sizing', 'risk', 'regime'];
  var SECTIONS = [
    {id: 'paper', title: 'PAPER', sub: 'what BETTOR owns · simulated execution, fictional USD', reads: ['xavierMgmt', 'smallLive', 'risk', 'postmortems'], render: renderPaper},
    {id: 'actual', title: 'ACTUAL', sub: 'what BETTOR owns · the venue account at 1:1,000', reads: ['smallLive', 'xavierMgmt', 'risk', 'postmortems'], render: renderActual},
    {id: 'portfolio', title: 'PORTFOLIO', sub: 'attribution and sizing per position', reads: ['attribution', 'sizing'], render: renderPortfolio, shadow: true},
    {id: 'derek', title: 'DEREK', sub: 'why: the decision and its evidence', reads: ['agents', 'smallLive'], render: renderDerek},
    {id: 'xavier', title: 'XAVIER', sub: 'management against holding', reads: ['xavierMgmt'], render: renderXavier},
    {id: 'audrey', title: 'AUDREY', sub: 'postmortems of every closed position · independent risk recompute', reads: ['postmortems', 'risk'], render: renderAudrey},
    {id: 'karen', title: 'KAREN', sub: 'red-team challenges · no authority', reads: ['karen', 'karenChallenges'], render: renderKaren, wide: true},
    {id: 'allocator', title: 'ALLIE · CHIEF ALLOCATOR', sub: 'capital allocation of the sleeve (recommendation only)', reads: ['allocator', 'intel'], render: renderAllocator, shadow: true},
    {id: 'risk', title: 'RISK', sub: 'what can go wrong · exposure and regime', reads: ['risk', 'regime'], render: renderRisk, shadow: true},
    {id: 'calibration', title: 'CALIBRATION', sub: 'is the probability any good', reads: ['calibration', 'quality'], render: renderCalibration, shadow: true},
    {id: 'execution', title: 'EXECUTION', sub: 'fill rate, slippage, admission, latency', reads: ['quality', 'smallLive'], render: renderExecution},
    {id: 'p5', title: 'INSTITUTIONAL STREAM / P5', sub: 'the live stream book rule, evaluated at runtime', reads: ['p5'], render: renderP5, wide: true},
    {id: 'coverage', title: 'COVERAGE', sub: 'provider → fill funnel per league per day', reads: ['coverage'], render: renderCoverage, wide: true},
    {id: 'quality', title: 'QUALITY', sub: 'the five-domain scorecard', reads: ['quality'], render: renderQuality, wide: true},
    {id: 'collab', title: 'AGENT COLLABORATION', sub: 'evidence → hypothesis → peer challenge → paper experiment', reads: ['findings', 'agents'], render: renderCollab, wide: true}
  ];
  var state = {results: {}, timer: null, unlockOffered: false};

  /* ── formatting: a missing figure is UNAVAILABLE with a reason, never 0 ── */
  function esc(v) {
    if (C && typeof C.esc === 'function') { return C.esc(v); }
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  function has(v) { return v !== null && v !== undefined && v !== ''; }
  function num(v) { return (typeof v === 'number' && isFinite(v)) ? v : null; }
  function isObj(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
  function arr(v) { return Array.isArray(v) ? v : []; }
  function obj(v) { return isObj(v) ? v : {}; }
  function NA(why) {
    return '<span class="iv-na">UNAVAILABLE — ' + esc(has(why) ? why : 'not reported by the read') + '</span>';
  }
  function unm(o, k) { return isObj(o) && isObj(o.unmeasured) ? o.unmeasured[k] : null; }
  function money(n) {
    return (n < 0 ? '−$' : '$') + Math.abs(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  }
  function usd(v, why) { var n = num(v); return n === null ? NA(why) : esc(money(n)); }
  function share(v, why) { var n = num(v); return n === null ? NA(why) : esc((n * 100).toFixed(1) + '%'); }
  function prob(v, why) { var n = num(v); return n === null ? NA(why) : esc(n.toFixed(3)); }
  function n0(v, why) {
    var n = num(v);
    if (n === null) { return NA(why); }
    return esc(Math.abs(n) >= 1000 ? n.toLocaleString('en-US', {maximumFractionDigits: 2}) : String(Number(n.toFixed(6))));
  }
  function txt(v, why) { return has(v) ? esc(typeof v === 'boolean' ? (v ? 'yes' : 'no') : v) : NA(why); }
  function yn(v, why) { return v === true ? 'yes' : v === false ? 'no' : NA(why); }
  function when(v, why) {
    if (!has(v)) { return NA(why); }
    var d = typeof v === 'number' ? new Date(v * 1000) : new Date(String(v));
    if (isNaN(d.getTime())) { return esc(v); }
    return esc(d.toISOString().slice(0, 16).replace('T', ' ') + 'Z');
  }
  function clip(s, n) { s = String(s == null ? '' : s); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
  function pill(text, tone) { return '<span class="pill pill-' + (tone || 'grey') + '">' + esc(text) + '</span>'; }
  function fact(k, v) { return '<div class="fact"><span class="k">' + esc(k) + '</span><span class="v">' + v + '</span></div>'; }
  function facts(list, cls) { return '<div class="facts ' + (cls || 'three') + '">' + list.map(function (f) { return fact(f[0], f[1]); }).join('') + '</div>'; }
  function label(k) { return String(k).replace(/_/g, ' '); }
  function tone(s) {
    s = String(s || '').toUpperCase();
    if (/(UNAVAILABLE|ERROR|CRITICAL|MISALIGNED|REFUTED|NOT_SUPPORTED|DETERIORATING|STOPPED|BLOCKED|NOT_PROVEN|NO_TRADE|FAILED|REJECTED|HIGH|INCIDENT)/.test(s)) { return 'bad'; }
    if (/^(OK|MEASURED|ALIGNED|MATCHED|SUSTAINED|SUPPORTED|ENABLED|IMPROVING|COMPLETE|PROVEN|UPHELD|NORMAL|AVAILABLE|FILLED|HEALTHY|EXACT)/.test(s)) { return 'good'; }
    if (/(INSUFFICIENT|UNPROVEN|WARNING|EMPTY|STALE|PENDING|REDUCE|OPEN|RESPONDED|MEDIUM|UNTESTED|ABSENT|READY_FOR)/.test(s)) { return 'warn'; }
    return 'grey';
  }
  function statusPill(s) { return has(s) ? pill(s, tone(s)) : NA('no status'); }
  var SHADOW_PILL = '<span class="pill pill-blue iv-shadow">SHADOW · NO AUTHORITY</span>';
  var PAPER_PILL = '<span class="pill pill-blue">PAPER — SIMULATED</span>';
  var ACTUAL_PILL = '<span class="pill pill-good">ACTUAL — VENUE</span>';

  function table(cols, rows, max, emptyText) {
    rows = arr(rows);
    if (!rows.length) { return '<p class="muted">' + esc(emptyText || 'No rows.') + '</p>'; }
    max = max || 10;
    var body = rows.slice(0, max).map(function (r) {
      return '<tr>' + cols.map(function (c) {
        return '<td' + (c[2] ? ' class="num"' : '') + '>' + c[1](r) + '</td>';
      }).join('') + '</tr>';
    }).join('');
    return '<div class="tablewrap"><table class="dt"><thead><tr>' +
      cols.map(function (c) { return '<th' + (c[2] ? ' class="num"' : '') + '>' + esc(c[0]) + '</th>'; }).join('') +
      '</tr></thead><tbody>' + body + '</tbody></table></div>' +
      (rows.length > max ? '<p class="muted">' + esc(rows.length - max) + ' more not shown.</p>' : '');
  }

  /* ── reads, results and envelopes ─────────────────────────────────── */
  function result(key) { return state.results[key]; }
  function data(key) { var r = result(key); return r && r.ok ? r.data : null; }
  function failWhy(key) {
    var r = result(key);
    if (!r) { return READS[key].path + ' not read yet'; }
    if (!r.ok) { return READS[key].path + ': ' + (r.error || 'read failed'); }
    var d = r.data;
    if (isObj(d) && (d.status === 'UNAVAILABLE' || d.status === 'EMPTY')) { return READS[key].path + ' ' + d.status + ': ' + (d.why || 'no reason given'); }
    return null;
  }
  function blockFor(key) {
    var r = result(key);
    var d = r && r.ok ? r.data : null;
    var st = !r ? 'UNAVAILABLE' : !r.ok ? 'UNAVAILABLE' : d.status;
    var why = !r ? 'not read yet' : !r.ok ? (r.error || 'read failed') : (d.why || 'no reason given');
    return '<div class="verdict iv-block"><b>' + esc(READS[key].path) + ' · ' + esc(st) + '</b>' +
      NA(why) + '<br><span class="muted">No figure is shown in place of a missing read.</span></div>';
  }
  // the read itself failed (HTTP / network / not JSON) or an envelope says
  // UNAVAILABLE, or an envelope says EMPTY with no data at all
  function gate(key, fn) {
    var r = result(key);
    if (!r || !r.ok) { return blockFor(key); }
    var d = r.data;
    if (!isObj(d)) { return blockFor(key); }
    if (d.status === 'UNAVAILABLE') { return blockFor(key); }
    if (d.status === 'EMPTY' && (d.data === null || d.data === undefined) && SHADOW_KEYS.indexOf(key) >= 0) { return blockFor(key); }
    return fn(d);
  }
  function envelopeLine(d) {
    return '<p class="iv-src">' + SHADOW_PILL + ' ' + statusPill(d.status) + (has(d.why) ? ' <span class="iv-why">' + esc(d.why) + '</span>' : '') +
      ' · run <span class="mono">' + esc(d.run_id || '—') + '</span>' + (has(d.computed_at) ? ' · computed ' + when(d.computed_at) : '') +
      ' · authority ' + esc(d.authority || '—') + '</p>';
  }
  // a {status, why, data} section inside a payload (agents, karen, paper)
  function section(sec, fn, what) {
    if (!isObj(sec)) { return NA((what || 'section') + ' absent from the read'); }
    if (sec.status === 'UNAVAILABLE') { return NA(sec.why); }
    if (sec.status === 'EMPTY' || !arr(sec.data).length) { return '<p class="muted">EMPTY — ' + esc(sec.why || 'no rows') + '</p>'; }
    return fn(sec.data);
  }

  /* ── shared slices of the real payloads ───────────────────────────── */
  function xavierPositions(kind, st) {
    var xm = data('xavierMgmt');
    if (!xm || !Array.isArray(xm.positions)) { return null; }
    return xm.positions.filter(function (p) { return p.position_kind === kind && (!st || p.state === st); });
  }
  function riskBook(book) {
    var r = data('risk');
    if (!r || r.status === 'UNAVAILABLE') { return {why: failWhy('risk') || 'risk read unavailable'}; }
    var b = obj(obj(r.data)[book]);
    if (b.status !== 'OK' || !isObj(b.data)) { return {why: 'SHADOW risk ' + book + ': ' + (b.status || 'EMPTY') + ' — ' + (b.why || 'no report')}; }
    return {rep: b.data};
  }
  function pmBook(book) {
    var pm = data('postmortems');
    if (!pm) { return {why: failWhy('postmortems')}; }
    var b = obj(pm[book]);
    if (!isObj(pm[book])) { return {why: 'book ' + book + ' absent from the postmortem read'}; }
    if (b.status !== 'OK' || !num(obj(b.summary).positions)) { return {why: (b.status || 'EMPTY') + ' — ' + (b.why || 'no closed position')}; }
    return {b: b, s: obj(b.summary)};
  }
  function slRows() { var sl = data('smallLive'); return sl ? arr(sl.rows) : null; }
  function actualMarks() {
    var rows = slRows();
    if (!rows) { return null; }
    var seen = {}, out = [];
    rows.forEach(function (r) {
      var a = obj(r.actual);
      if (a.group_pnl_kind === 'UNREALIZED_MARK' && num(a.group_pnl_usd) !== null && !seen[r.group_id]) {
        seen[r.group_id] = true;
        out.push({group_id: r.group_id, market: r.market, pnl: a.group_pnl_usd, as_of: a.group_pnl_as_of, source: a.group_pnl_source});
      }
    });
    return out;
  }

  /* ── PAPER / ACTUAL: never summed ───────────────────────────────── */
  function positionsTable(ps, kind) {
    var marks = {};
    if (kind === 'ACTUAL') { arr(actualMarks()).forEach(function (m) { marks[m.group_id] = m; }); }
    var cols = [
      ['Market', function (p) { return '<span class="mono">' + esc(clip(p.market || p.group_id, 40)) + '</span>'; }],
      ['Side', function (p) { return txt(p.holding_side); }],
      ['Qty', function (p) { return n0(p.open_qty, 'qty not recorded'); }, true],
      ['First fill', function (p) { return when(p.first_fill_at, 'no fill time'); }],
      ['Thesis', function (p) { return txt(obj(p.thesis).state, 'no thesis state'); }],
      ['Evidence', function (p) { return p.evidence ? txt(p.evidence.state) : NA(p.why_no_review); }]
    ];
    if (kind === 'ACTUAL') {
      cols.push(['Worth now (unrealized)', function (p) {
        var m = marks[p.group_id];
        return m ? usd(m.pnl) : NA(p.state === 'OPEN' ? 'no fresh Xavier venue mark for this group' : 'position ' + (p.state || 'not open'));
      }, true]);
    } else {
      cols.push(['Worth now', function () { return NA('open paper positions are not marked by these reads'); }]);
    }
    return table(cols, ps, 12, 'No ' + kind + ' position in the Xavier read.');
  }
  function renderPaper() {
    var html = '<p class="iv-basis">' + PAPER_PILL + ' simulated execution on a fictional $500,000 account · never summed with ACTUAL</p>';
    var open = xavierPositions('PAPER', 'OPEN'), all = xavierPositions('PAPER');
    var rk = riskBook('PAPER'), pm = pmBook('paper');
    var eq = rk.rep ? obj(rk.rep.equity) : null;
    var sl = data('smallLive');
    html += facts([
      ['Open positions', open ? esc(open.length) : NA(failWhy('xavierMgmt'))],
      ['Positions handed to Xavier', all ? esc(all.length) : NA(failWhy('xavierMgmt'))],
      ['Paper equity (SHADOW risk read)', eq ? usd(eq.current_equity_usd, unm(eq, 'current_equity_usd')) : NA(rk.why)],
      ['Gross exposure (SHADOW)', rk.rep ? usd(rk.rep.gross_exposure_usd, unm(rk.rep, 'gross_exposure_usd')) : NA(rk.why)],
      ['Realized P&L, closed (Audrey)', pm.s ? usd(pm.s.realized_pnl_usd) + ' <span class="iv-why">n=' + esc(pm.s.positions) + '</span>' : NA(pm.why)],
      ['Legacy mirror · paper orders', sl ? esc('open ' + obj(obj(sl.counts).paper_status).open + ' · filled ' + obj(obj(sl.counts).paper_status).filled) : NA(failWhy('smallLive'))]
    ]);
    html += '<h3 class="h3">Open paper positions (Xavier read)</h3>' + (open ? positionsTable(open, 'PAPER') : NA(failWhy('xavierMgmt')));
    return html;
  }
  function noFill(a) { return a.why_no_fill_figures || 'no venue fill record for this order'; }
  function renderActual() {
    var sl = data('smallLive');
    var html = '<p class="iv-basis">' + ACTUAL_PILL + ' read from the venue\'s own records · never summed with PAPER</p>';
    if (!sl) { return html + blockFor('smallLive'); }
    var ctl = obj(sl.control), acct = obj(sl.account), launch = obj(sl.launch), bc = obj(launch.book_currency);
    var acctOk = acct.status === 'AVAILABLE';
    html += facts([
      ['Mirror control', has(ctl.state) ? statusPill(ctl.state) : NA('no control row')],
      ['Cap per order', usd(ctl.cap_usd_per_order, 'no cap recorded')],
      ['Scale', has(ctl.scale) ? esc('1:' + Number(ctl.scale).toLocaleString('en-US')) : NA('no scale recorded')],
      ['Actual orders possible now', launch.actual_orders_possible_now === true ? pill('yes', 'good') : launch.actual_orders_possible_now === false ? pill('no', 'bad') + ' <span class="iv-why">' + esc(arr(launch.why_not).join('; ')) + '</span>' : NA('launch state not reported')],
      ['Account snapshot', acctOk ? pill('AVAILABLE', 'good') + ' ' + when(acct.at) : NA(acct.why || acct.status)],
      ['Balance (venue)', acctOk ? usd(acct.current_balance_usd, 'balance not in snapshot') : NA(acct.why)],
      ['Buying power', acctOk ? usd(acct.buying_power_usd, 'not in snapshot') : NA(acct.why)],
      ['Venue positions (snapshot)', acctOk ? n0(acct.positions_count, 'not in snapshot') : NA(acct.why)],
      ['Reconciled', acctOk ? yn(acct.reconciled, 'no reconciliation recorded') : NA(acct.why)]
    ]);
    if (has(bc.why)) { html += '<p class="iv-src">Book currency: ' + esc(bc.why) + '</p>'; }
    var open = xavierPositions('ACTUAL', 'OPEN');
    var rk = riskBook('ACTUAL'), pm = pmBook('actual');
    html += facts([
      ['Open actual positions (Xavier)', open ? esc(open.length) : NA(failWhy('xavierMgmt'))],
      ['Gross exposure (SHADOW)', rk.rep ? usd(rk.rep.gross_exposure_usd) : NA(rk.why)],
      ['Realized P&L, closed (Audrey)', pm.s ? usd(pm.s.realized_pnl_usd) + ' <span class="iv-why">n=' + esc(pm.s.positions) + '</span>' : NA(pm.why)]
    ]);
    html += '<h3 class="h3">Actual positions (Xavier read)</h3>' + (xavierPositions('ACTUAL') ? positionsTable(xavierPositions('ACTUAL'), 'ACTUAL') : NA(failWhy('xavierMgmt')));
    var rows = arr(sl.rows);
    html += '<h3 class="h3">Actual orders (venue)</h3>' + table([
      ['Market', function (r) { return '<span class="mono">' + esc(clip(r.market, 34)) + '</span>'; }],
      ['Role', function (r) { return txt(r.role); }],
      ['Status', function (r) { return statusPill(obj(r.actual).status); }],
      ['Intended', function (r) { return n0(obj(r.actual).intended_qty, 'no intended qty'); }, true],
      ['Filled', function (r) { var a = obj(r.actual); return a.excluded ? NA('excluded: ' + a.exclusion) : n0(a.filled_qty, noFill(a)); }, true],
      ['Avg px', function (r) { var a = obj(r.actual); return a.excluded ? '—' : prob(a.avg_fill_price, noFill(a)); }, true],
      ['Fees', function (r) { var a = obj(r.actual); return a.excluded ? '—' : usd(a.fees_usd, noFill(a)); }, true]
    ], rows, 8, (obj(sl.empty_state).headline || 'No actual order in this read.'));
    return html;
  }

  /* ── PORTFOLIO (SHADOW): attribution and sizing ─────────────────── */
  var ATTR = [['realized_pnl_usd', 'Realized P&L'], ['model_edge_usd', 'Model edge'], ['execution_edge_usd', 'Execution edge'],
              ['slippage_usd', 'Slippage'], ['fees_usd', 'Fees'], ['management_usd', 'Management'], ['outcome_variance_usd', 'Outcome variance']];
  function renderPortfolio() {
    var html = gate('attribution', function (d) {
      var sum = obj(obj(d.data).summary);
      var out = envelopeLine(d);
      ['PAPER', 'ACTUAL'].forEach(function (book) {
        var s = sum[book];
        out += '<div class="book"><h3>' + (book === 'PAPER' ? PAPER_PILL : ACTUAL_PILL) + ' attribution</h3>';
        if (!isObj(s)) { out += NA('no ' + book + ' summary in this run') + '</div>'; return; }
        out += facts([['Positions', n0(s.positions)], ['Reconciling', n0(s.reconciling)]].concat(ATTR.map(function (a) {
          return [a[1], usd(s[a[0]], unm(s, a[0])) + (num(s[a[0] + '_measured_n']) !== null ? ' <span class="iv-why">n=' + esc(s[a[0] + '_measured_n']) + '</span>' : '')];
        }))) + '</div>';
      });
      out += '<h3 class="h3">Per position</h3>' + table([
        ['Book', function (r) { return txt(r.book); }],
        ['Market', function (r) { return '<span class="mono">' + esc(clip(r.us_market_slug || r.subject_id, 30)) + '</span>'; }],
        ['Qty', function (r) { return n0(r.entry_qty); }, true],
        ['P', function (r) { return prob(r.p_decision, unm(r, 'p_decision')); }, true],
        ['Fill VWAP', function (r) { return prob(r.fill_vwap, unm(r, 'fill_vwap')); }, true],
        ['Realized', function (r) { return usd(r.realized_pnl_usd, unm(r, 'realized_pnl_usd')); }, true],
        ['Reconciles', function (r) { return yn(r.reconciles, 'not reconciled'); }]
      ], d.rows, 8, 'No attributed position.');
      return out;
    });
    html += '<h3 class="h3">Sizing · shadow size beside the paper and actual size</h3>' + gate('sizing', function (d) {
      var sd = obj(d.data);
      return envelopeLine(d) + facts([['Applied', yn(d.applied)], ['Shadow total', usd(sd.shadow_usd_total)], ['Regime used', txt(sd.regime)]]) + table([
        ['Market', function (r) { return '<span class="mono">' + esc(clip(r.us_market_slug || r.decision_id, 30)) + '</span>'; }],
        ['Paper', function (r) { return usd(r.paper_usd, unm(r, 'paper_usd')); }, true],
        ['Shadow', function (r) { return usd(r.shadow_usd, unm(r, 'shadow_usd')); }, true],
        ['Actual', function (r) { return usd(r.actual_usd, unm(r, 'actual_usd')); }, true],
        ['Binding constraint', function (r) { return txt(r.binding_constraint); }]
      ], d.rows, 8, d.why || 'No sized decision.');
    });
    return html;
  }

  /* ── DEREK: the decision ───────────────────────────────────────── */
  function renderDerek() {
    var html = gate('agents', function (d) {
      var dk = arr(d.agents).filter(function (a) { return a.agent_id === 'DEREK'; })[0];
      if (!dk) { return NA('DEREK absent from the agents index'); }
      var w = obj(dk.waiting_on);
      var refusals = Object.keys(obj(w.refusals));
      var out = facts([
        ['State', statusPill(dk.state || dk.status)], ['Activity', txt(dk.activity, dk.why)],
        ['Runs', n0(dk.runs, 'no run recorded')], ['Last heartbeat', when(dk.last_heartbeat_at, 'no heartbeat')],
        ['Probability model', txt(dk.model_version, 'no model recorded')],
        ['Policy', txt(dk.policy_version) + (dk.policy_version_approved === false ? ' <span class="iv-why">not an approved policy</span>' : '')],
        ['Evaluated (latest run)', n0(w.evaluated, 'not reported')],
        ['Waiting on', refusals.length ? esc(refusals.join(', ')) : NA('nothing reported')]
      ]);
      var ho = obj(d.handoffs);
      out += '<h3 class="h3">Handoffs Derek → Xavier</h3>' + section(ho, function (rows) {
        return table([
          ['Entry intent', function (r) { return '<span class="mono">' + esc(clip(r.entry_intent_id, 28)) + '</span>'; }],
          ['Derek decision', function (r) { return has(r.derek_decision_id) ? '<span class="mono">' + esc(r.derek_decision_id) + '</span>' : NA('not recorded on the handoff'); }],
          ['Confirmed qty', function (r) { return n0(r.confirmed_qty); }, true],
          ['Owner', function (r) { return txt(r.owner_agent); }],
          ['At', function (r) { return when(r.handoff_at); }]
        ], rows, 6);
      }, 'handoffs');
      return out;
    });
    var sl = data('smallLive');
    html += '<h3 class="h3">Qualified decisions (execution intents)</h3>';
    if (!sl) { return html + NA(failWhy('smallLive')); }
    var dec = obj(sl.decisions);
    if (dec.status !== 'OK') { return html + NA((dec.status || 'UNAVAILABLE') + ' — ' + (dec.why || 'no decisions read')); }
    return html + table([
      ['Market', function (r) { return '<span class="mono">' + esc(clip(obj(r.decision).market, 28)) + '</span>'; }],
      ['P', function (r) { return prob(obj(r.decision).probability, 'no probability'); }, true],
      ['Authority', function (r) { return txt(obj(r.decision).probability_authority); }],
      ['Fresh', function (r) { return yn(obj(r.decision).probability_fresh, 'not reported'); }],
      ['Net EV', function (r) { return usd(obj(r.decision).net_expected_profit_usd, 'not recorded on the decision'); }, true],
      ['Policy', function (r) { return txt(obj(r.decision).policy_version, 'no policy version on the decision'); }],
      ['Actual', function (r) { return statusPill(obj(r.actual).state); }]
    ], dec.rows, 6, 'No qualified decision.');
  }

  /* ── XAVIER: management ───────────────────────────────────────── */
  function renderXavier() {
    return gate('xavierMgmt', function (d) {
      var p = obj(d.policy), s = obj(d.summary);
      var html = facts([
        ['Policy', txt(p.policy_id) + ' ' + statusPill(p.status)],
        ['Approved', p.approved === true ? pill('yes', 'good') + ' ' + txt(p.approved_by) : pill('no', 'warn') + ' <span class="iv-why">' + esc(p.why_not_approved || '') + '</span>'],
        ['Places orders', yn(d.places_orders)],
        ['Positions', n0(s.positions)], ['Open', n0(s.open_positions)],
        ['Open without a review', n0(s.open_without_review)],
        ['Reviews overdue', n0(s.reviews_overdue)],
        ['Reallocate (SHADOW) recommended', n0(s.reallocate_shadow_recommended)],
        ['Xavier vs hold (value-add rows)', num(s.value_add_rows) ? n0(s.value_add_rows) : NA('0 value-add rows: ' + obj(d.rules).value_add)]
      ]);
      var ev = obj(s.by_evidence_state), th = obj(s.by_thesis_state);
      html += '<p class="iv-src">Evidence: ' + (Object.keys(ev).map(function (k) { return esc(k + ' ' + ev[k]); }).join(' · ') || '—') +
        ' &nbsp; Thesis: ' + (Object.keys(th).map(function (k) { return esc(k + ' ' + th[k]); }).join(' · ') || '—') + '</p>';
      if (d.status === 'EMPTY') { return html + '<p class="muted">EMPTY — ' + esc(d.why) + '</p>'; }
      return html + table([
        ['Book', function (r) { return r.position_kind === 'ACTUAL' ? pill('ACTUAL', 'good') : pill('PAPER', 'blue'); }],
        ['Market', function (r) { return '<span class="mono">' + esc(clip(r.market, 30)) + '</span>'; }],
        ['State', function (r) { return txt(r.state); }],
        ['Qty', function (r) { return n0(r.open_qty); }, true],
        ['Review', function (r) { return r.latest_review ? when(r.latest_review.at) : NA(r.why_no_review); }],
        ['Recommendation', function (r) { return has(r.recommendation) ? esc(typeof r.recommendation === 'object' ? (r.recommendation.action || JSON.stringify(r.recommendation)) : r.recommendation) : NA('no assessment'); }],
        ['Vs hold', function (r) {
          if (!isObj(r.value_add)) { return NA('no value-add row'); }
          var inc = obj(obj(r.value_add.incremental).ACTUAL_XAVIER_minus_HOLD_TO_SETTLEMENT);
          return inc.available === true ? usd(inc.pnl_usd) : NA(inc.why || r.value_add.status);
        }, true]
      ], arr(d.positions).filter(function (r) { return r.state === 'OPEN'; }).concat(arr(d.positions).filter(function (r) { return r.state !== 'OPEN'; })), 10);
    });
  }

  /* ── AUDREY: postmortems, PAPER and ACTUAL apart ────────────────── */
  var COMP = [['selection_edge_usd', 'Selection edge'], ['execution_slippage_usd', 'Execution slippage'],
              ['fees_usd', 'Fees'], ['management_value_usd', 'Management (Xavier vs hold)'],
              ['outcome_variance_usd', 'Outcome variance']];
  function bookBlock(name, b, pillHtml) {
    if (!isObj(b)) { return '<div class="book"><h3>' + pillHtml + '</h3>' + NA('book ' + name + ' absent from the read') + '</div>'; }
    var s = obj(b.summary);
    if (b.status !== 'OK' || !num(s.positions)) {
      return '<div class="book"><h3>' + pillHtml + ' ' + statusPill(b.status) + '</h3>' + NA(b.why || 'no closed position') + '</div>';
    }
    var comps = COMP.map(function (c) {
      var x = obj(s[c[0]]);
      return [c[1], usd(x.sum, (x.unmeasured || 0) + ' unmeasured, ' + (x.measured || 0) + ' measured') + (num(x.unmeasured) ? ' <span class="iv-why">' + esc(x.unmeasured) + ' unmeasured</span>' : '')];
    });
    return '<div class="book"><h3>' + pillHtml + ' ' + statusPill(b.status) + '</h3>' +
      facts([['Closed positions', n0(s.positions)], ['Complete decompositions', n0(s.complete)], ['Realized P&L', usd(s.realized_pnl_usd)],
             ['Unexplained', usd(s.unexplained_usd)]].concat(comps)) +
      table([
        ['Position', function (p) { return '<span class="mono">' + esc(clip(p.us_market_slug || p.position_key, 30)) + '</span>'; }],
        ['Closed', function (p) { return when(p.closed_at); }],
        ['Realized', function (p) { return usd(p.realized_pnl_usd); }, true],
        ['Selection', function (p) { return usd(p.selection_edge_usd, obj(obj(p.components).selection_edge_usd).why); }, true],
        ['Mgmt', function (p) { return usd(p.management_value_usd, obj(obj(p.components).management_value_usd).why); }, true],
        ['Variance', function (p) { return usd(p.outcome_variance_usd, obj(obj(p.components).outcome_variance_usd).why); }, true]
      ], b.positions, 6) + '</div>';
  }
  function renderAudrey() {
    var html = gate('postmortems', function (d) {
      return '<p class="iv-src">' + esc(d.identity || '') + ' · ' + esc(d.schedule || '') + '</p>' +
        bookBlock('paper', d.paper, PAPER_PILL) + bookBlock('actual', d.actual, ACTUAL_PILL);
    });
    var r = data('risk');
    html += '<h3 class="h3">Independent recompute of the SHADOW risk report</h3>';
    if (!r || r.status !== 'OK') { return html + NA(failWhy('risk') || 'risk read has no run'); }
    var checks = arr(r.audrey_checks);
    if (!checks.length) { return html + NA('no Audrey recompute check in run ' + (r.run_id || '—')); }
    var bad = checks.filter(function (c) { return c.agrees === false; });
    return html + '<p>' + esc(checks.length - bad.length + ' of ' + checks.length + ' metrics agree') + (bad.length ? ' · ' + pill(bad.length + ' DISAGREE', 'bad') : '') + '</p>' +
      (bad.length ? table([['Book', function (c) { return txt(c.book); }], ['Metric', function (c) { return txt(c.metric); }],
                          ['Primary', function (c) { return n0(c.primary_value); }, true], ['Audrey', function (c) { return n0(c.audrey_value); }, true],
                          ['Finding', function (c) { return txt(c.finding_id, obj(c.detail).finding_unwritten_reason || 'no finding'); }]], bad, 8) : '');
  }

  /* ── KAREN: per-challenge record + metrics ─────────────────────── */
  var KAREN_METRICS = ['evidence_grounding', 'valid_defect_discovery', 'challenge_precision', 'false_block_rate', 'time_to_challenge', 'downstream_improvement'];
  function karenMetric(m, name) {
    if (!isObj(m)) { return fact(label(name), NA('metric absent from the read')); }
    var shown;
    if (m.measurable !== true || m.value === null || m.value === undefined) {
      shown = NA(m.why);
    } else if (m.unit === 'seconds') {
      shown = esc(m.value + ' s') + ' <span class="iv-why">' + esc(n0Text(m.numerator) + ' s / ' + n0Text(m.denominator)) +
        (num(m.median_s) !== null ? ' · median ' + m.median_s + ' s' : '') + '</span>';
    } else if (name === 'valid_defect_discovery') {
      shown = esc(m.value) + ' <span class="iv-why">' + esc(n0Text(m.numerator) + ' / ' + n0Text(m.denominator)) + (num(m.rate) !== null ? ' · ' + (m.rate * 100).toFixed(1) + '%' : '') + '</span>';
    } else {
      shown = share(m.value) + ' <span class="iv-why">' + esc(n0Text(m.numerator) + ' / ' + n0Text(m.denominator)) + '</span>';
    }
    return '<div class="fact" title="' + esc(m.definition || '') + '"><span class="k">' + esc(label(name)) + '</span><span class="v">' + shown + '</span></div>';
  }
  function n0Text(v) { return num(v) === null ? '—' : String(v); }
  function refs(list) {
    list = arr(list).filter(function (e) { return isObj(e) && e.kind !== 'karen_challenges'; });
    if (!list.length) { return NA('no evidence reference'); }
    return list.map(function (e) { return '<span class="mono">' + esc(e.kind + ' ' + e.id) + '</span>'; }).join(', ');
  }
  function challengeCard(c) {
    var peer = has(c.responded_by)
      ? statusPill(c.response_stance) + ' by ' + esc(c.responded_by) + ' ' + when(c.responded_at) + ' — “' + esc(clip(c.response, 240)) + '”' +
        (arr(c.response_evidence_refs).length ? ' · evidence ' + refs(c.response_evidence_refs) : '')
      : NA('awaiting ' + (c.target_agent || 'the target') + '\'s response');
    var evalu = has(c.outcome)
      ? statusPill(c.outcome) + ' by ' + esc(c.resolved_by) + ' ' + when(c.resolved_at) + ' — ' + esc(clip(c.outcome_reason, 240))
      : c.state === 'WITHDRAWN' ? pill('WITHDRAWN', 'grey') : NA('not yet evaluated by a third party');
    var fb = !c.blocked ? 'not blocking (no false-block test applies)'
      : c.false_block === true ? pill('FALSE BLOCK', 'bad') + ' assessed by ' + esc(c.false_block_assessed_by) + ' ' + when(c.false_block_assessed_at)
      : c.false_block === false ? pill('BLOCK JUSTIFIED', 'good') + ' assessed by ' + esc(c.false_block_assessed_by)
      : NA('blocking; false-block not yet assessed');
    var impact = (has(c.improvement_finding_id) || has(c.improvement_proposal_id))
      ? 'improvement ' + '<span class="mono">' + esc(c.improvement_finding_id || c.improvement_proposal_id) + '</span> linked by ' + esc(c.improvement_linked_by) +
        (isObj(c.downstream_impact) ? ' — ' + esc(Object.keys(c.downstream_impact).map(function (k) { return k + ': ' + c.downstream_impact[k]; }).join('; ')) : '')
      : NA(c.state === 'UPHELD' ? 'upheld; no improvement linked yet' : 'no improvement linked (' + (c.state || '—') + ')');
    return '<div class="iv-ch"><div class="row1"><span class="mono">' + esc(c.challenge_id) + '</span>' +
      '<span>' + statusPill(c.state) + ' ' + pill(c.severity || '—', tone(c.severity)) + '</span></div>' +
      '<p class="iv-claim">' + esc(clip(c.claim, 400)) + '</p>' +
      '<dl class="iv-dl">' +
      '<dt>Target agent</dt><dd>' + txt(c.target_agent) + '</dd>' +
      '<dt>Target decision</dt><dd><span class="mono">' + esc((c.target_kind || '—') + ' ' + (c.target_id || '—')) + '</span>' + (has(c.finding_id) ? ' · finding <span class="mono">' + esc(c.finding_id) + '</span>' : '') + '</dd>' +
      '<dt>Evidence</dt><dd>' + refs(c.evidence || c.evidence_refs) + '</dd>' +
      '<dt>Category</dt><dd>' + txt(c.detector, 'no detector recorded') + '</dd>' +
      '<dt>Raised</dt><dd>' + when(c.challenged_at) + (num(c.time_to_challenge_s) !== null ? ' · ' + esc(c.time_to_challenge_s) + ' s after the record' : '') + '</dd>' +
      '<dt>Peer response</dt><dd>' + peer + '</dd>' +
      '<dt>Independent evaluation</dt><dd>' + evalu + '</dd>' +
      '<dt>False-block outcome</dt><dd>' + fb + '</dd>' +
      '<dt>Downstream impact</dt><dd>' + impact + '</dd>' +
      '</dl></div>';
  }
  function renderKaren() {
    var k = data('karen');
    var html = '';
    if (!k) {
      html += blockFor('karen');
    } else {
      var prof = obj(k.profile), met = isObj(k.metrics) ? k.metrics : null;
      html += facts([['Authority', txt(prof.authority)], ['Role', txt(prof.role)], ['Production effect', txt(k.production_effect)],
                     ['Challenges counted', met ? n0(met.challenges_counted) : NA(obj(obj(k.sections).metrics).why)],
                     ['By state', met ? esc(Object.keys(obj(met.by_state)).map(function (s) { return s + ' ' + met.by_state[s]; }).join(' · ')) : NA(obj(obj(k.sections).metrics).why)]]);
      html += '<h3 class="h3">Metrics · numerator / denominator</h3>';
      html += met ? '<div class="facts three">' + KAREN_METRICS.map(function (n) { return karenMetric(obj(met.metrics)[n], n); }).join('') + '</div>'
                  : NA(obj(obj(k.sections).metrics).why || 'metrics read failed');
    }
    // every state (OPEN … UPHELD/REJECTED/WITHDRAWN), not just the open ones
    var kc = data('karenChallenges');
    var sec = kc ? kc.challenges : (k ? obj(k.sections).recent_challenges : null);
    html += '<h3 class="h3">Challenges</h3>';
    if (!sec) { return html + NA(failWhy('karenChallenges')); }
    return html + section(sec, function (rows) {
      return '<div class="iv-chs">' + rows.slice(0, 12).map(challengeCard).join('') + '</div>' +
        (rows.length > 12 ? '<p class="muted">' + esc(rows.length - 12) + ' more not shown.</p>' : '');
    }, 'challenges');
  }

  /* ── ALLOCATOR (SHADOW) ───────────────────────────────────────── */
  function renderAllocator() {
    return gate('allocator', function (d) {
      var a = obj(d.data), inp = obj(a.inputs);
      return envelopeLine(d) + facts([
        ['Sleeve', usd(a.sleeve_usd)], ['Budget after regime', usd(a.budget_usd)], ['Allocated (SHADOW)', usd(a.allocated_usd)],
        ['Unallocated', usd(a.unallocated_usd)], ['Regime', txt(inp.regime)], ['Candidates', n0(a.candidates)]
      ]) + table([
        ['#', function (r) { return n0(r.rank); }, true],
        ['Kind', function (r) { return txt(r.candidate_kind); }],
        ['Market', function (r) { return '<span class="mono">' + esc(clip(r.us_market_slug, 26)) + '</span>'; }],
        ['EV', function (r) { return n0(r.ev, r.ev_why || unm(r, 'ev')); }, true],
        ['Shadow $', function (r) { return usd(r.shadow_usd); }, true],
        ['Binding', function (r) { return txt(r.binding_constraint); }]
      ], a.allocation, 8, 'No candidate.');
    });
  }

  /* ── RISK (SHADOW): exposure and regime ───────────────────────── */
  function riskBlock(book, pillHtml) {
    var rb = riskBook(book);
    if (!rb.rep) { return '<div class="book"><h3>' + pillHtml + '</h3>' + NA(rb.why) + '</div>'; }
    var r = rb.rep, eq = obj(r.equity), lq = obj(r.liquidity);
    return '<div class="book"><h3>' + pillHtml + ' risk</h3>' + facts([
      ['Open positions', n0(r.open_positions)], ['Gross exposure', usd(r.gross_exposure_usd)],
      ['Largest game', usd(r.max_game_exposure_usd)], ['Largest cluster', usd(r.largest_cluster_exposure_usd)],
      ['Game HHI', n0(r.game_concentration_hhi, unm(r, 'game_concentration_hhi'))],
      ['Liquidity at risk', usd(lq.liquidity_at_risk_usd, Object.keys(obj(lq.unmeasured)).map(function (k) { return lq.unmeasured[k]; }).join('; ') || lq.basis)],
      ['Equity', usd(eq.current_equity_usd, unm(eq, 'current_equity_usd'))], ['Drawdown', usd(eq.current_drawdown_usd, unm(eq, 'current_drawdown_usd'))],
      ['Daily P&L', usd(eq.daily_pnl_usd, unm(eq, 'daily_pnl_usd'))]
    ]) + '</div>';
  }
  function renderRisk() {
    var html = gate('regime', function (d) {
      var g = obj(d.data);
      return envelopeLine(d) + '<div class="verdict"><b>Regime recommendation (SHADOW, not applied): ' + esc(g.recommendation || '—') + '</b>' +
        esc(arr(g.reasons).join('; ')) + '<br><span class="muted">' + esc((g.measured_signals === undefined ? '—' : g.measured_signals) + ' measured signal(s), min n ' + (g.min_n || '—')) + '</span></div>' +
        table([['Signal', function (s) { return txt(s.signal); }], ['Status', function (s) { return statusPill(s.status); }],
               ['Why', function (s) { return esc(clip(arr(s.reasons).join('; '), 80)); }]], g.signals, 10);
    });
    html += '<h3 class="h3">Exposure · PAPER and ACTUAL side by side, never summed</h3>' + gate('risk', function (d) {
      return envelopeLine(d) + riskBlock('PAPER', PAPER_PILL) + riskBlock('ACTUAL', ACTUAL_PILL);
    });
    return html;
  }

  /* ── QUALITY metric cards (CALIBRATION / EXECUTION / QUALITY) ─── */
  function metricCard(m) {
    var v = m.value;
    var shown = v === null || v === undefined ? NA(m.why || m.blocker)
      : m.unit === 'share' ? share(v) : typeof v === 'number' ? n0(v) : esc(v);
    var ci = isObj(m.ci) ? ('CI ' + n0(m.ci.low) + ' … ' + n0(m.ci.high) + ' (' + esc(m.ci.method) + ')') : 'CI n/a';
    var tr = isObj(m.trend) ? (has(m.trend.direction) ? esc(m.trend.direction) + ' vs prior ' + n0(m.trend.prior_value) : 'trend: ' + esc(m.trend.why || 'n/a')) : 'trend n/a';
    var nd = (has(m.numerator) || has(m.denominator)) ? esc(n0Text(m.numerator) + ' / ' + n0Text(m.denominator)) : '';
    return '<div class="iv-metric"><div class="row1"><span class="nm">' + esc(m.name) + '</span>' +
      '<span>' + statusPill(m.status) + ' <span class="val">' + shown + '</span></span></div>' +
      '<div class="meta">' + (nd ? nd + ' · ' : '') + 'n=' + esc(n0Text(m.sample)) + ' · ' + ci + ' · ' + tr +
      (m.last_measured_at ? ' · measured ' + when(m.last_measured_at) : '') + '</div>' +
      (has(m.blocker) || has(m.why) ? '<div class="meta">Blocker: ' + esc(m.blocker || m.why) + '</div>' : '') +
      (has(m.next_improvement) ? '<div class="meta">Next: ' + esc(m.next_improvement) + '</div>' : '') + '</div>';
  }
  function domain(d, name) { return (isObj(d) && isObj(d.domains) && Array.isArray(d.domains[name])) ? d.domains[name] : null; }
  function renderQuality() {
    return gate('quality', function (d) {
      var p = obj(d.profitability);
      var html = '<div class="verdict"><b>Profitability: PAPER ' + esc(p.paper || 'UNAVAILABLE') + ' · ACTUAL ' + esc(p.actual || 'UNAVAILABLE') + ' (evaluated separately)</b>' +
        esc(obj(p.rule).test || '') + '</div>';
      ['ENGINEERING', 'AGENTS', 'INVESTMENT_INTELLIGENCE', 'EXECUTION', 'PROFITABILITY_EVIDENCE'].forEach(function (k) {
        var ms = domain(d, k);
        html += '<div class="iv-dom">' + esc(label(k)) + '</div>' + (ms ? '<div class="iv-metrics">' + ms.map(metricCard).join('') + '</div>' : NA('domain ' + k + ' absent from the scorecard'));
      });
      return html;
    });
  }
  function renderCalibration() {
    var html = gate('calibration', function (d) {
      var c = obj(d.data), ov = obj(c.overall), dr = obj(c.drift);
      var out = envelopeLine(d) + facts([
        ['Production probabilities modified', yn(d.production_probabilities_modified)],
        ['Records read', n0(c.records_read)], ['Overlays', esc(arr(d.overlays).length)]]);
      var srcs = Object.keys(ov);
      if (!srcs.length) { return out + NA('no calibration source in this run'); }
      out += table([
        ['Source', function (s) { return txt(s); }],
        ['n', function (s) { return n0(ov[s].n); }, true],
        ['Brier', function (s) { return n0(ov[s].brier, unm(ov[s], 'brier')); }, true],
        ['Brier CI', function (s) { var o = ov[s]; return num(o.brier_ci_low) !== null ? esc(o.brier_ci_low + ' … ' + o.brier_ci_high) : NA(unm(o, 'brier_ci_low')); }],
        ['Log loss', function (s) { return n0(ov[s].log_loss, unm(ov[s], 'log_loss')); }, true],
        ['ECE', function (s) { return n0(obj(ov[s].reliability).expected_calibration_error, 'no reliability table'); }, true],
        ['Drift (Brier Δ)', function (s) { var x = obj(dr[s]); return n0(x.brier_change, unm(x, 'brier_change')); }, true],
        ['Small sample', function (s) { return yn(ov[s].small_sample); }]
      ], srcs, 6);
      return out;
    });
    var q = data('quality');
    var ms = q ? domain(q, 'INVESTMENT_INTELLIGENCE') : null;
    html += '<h3 class="h3">From the quality scorecard</h3>' + (ms ? ms.map(metricCard).join('') : NA(failWhy('quality') || 'domain absent'));
    return html;
  }
  var LAT = [['receipt_to_decision_ms', 'Receipt → decision'], ['decision_to_intent_ms', 'Decision → intent'], ['intent_to_submit_ms', 'Intent → submit'],
             ['submit_to_ack_ms', 'Submit → ack'], ['ack_to_first_fill_ms', 'Ack → first fill'], ['decision_to_first_fill_ms', 'Decision → first fill']];
  function renderExecution() {
    var html = gate('quality', function (d) {
      var ms = domain(d, 'EXECUTION');
      return ms ? ms.map(metricCard).join('') : NA('EXECUTION domain absent from the scorecard');
    });
    var sl = data('smallLive');
    html += '<h3 class="h3">Latency (small-live decisions, ms)</h3>';
    if (!sl) { return html + NA(failWhy('smallLive')); }
    var lat = obj(obj(sl.decisions).latency_ms);
    return html + table([
      ['Leg', function (l) { return esc(l[1]); }],
      ['n', function (l) { return n0(obj(lat[l[0]]).n, 'not measured'); }, true],
      ['p50', function (l) { return n0(obj(lat[l[0]]).p50, 'not measured'); }, true],
      ['p95', function (l) { return n0(obj(lat[l[0]]).p95, 'not measured'); }, true]
    ], LAT, 10);
  }

  /* ── INSTITUTIONAL STREAM / P5 ─────────────────────────────────── */
  function blocker(b) {
    if (!isObj(b)) { return NA('none'); }
    return '<span class="mono">' + esc(b.predicate) + '</span> ' + pill(b.blocker || '—', b.blocker === 'EXTERNAL' ? 'warn' : 'bad') +
      ' ' + esc(b.reason || '') + (has(b.action) ? '<br><span class="iv-why">Action: ' + esc(b.action) + '</span>' : '');
  }
  function evidenceOf(p) {
    var re = p.runtime_evidence;
    if (!isObj(re)) { return NA('no runtime evidence for this predicate'); }
    var parts = Object.keys(re).map(function (k) {
      var v = re[k];
      if (v === null || v === undefined) { return k + ': none'; }
      if (Array.isArray(v)) { return k + ': ' + v.length; }
      if (isObj(v)) { return k + ': ' + (v.status || Object.keys(v).length + ' field(s)'); }
      return k + ': ' + v;
    });
    return esc(clip(parts.join(' · '), 140));
  }
  function renderP5() {
    return gate('p5', function (d) {
      var re = obj(d.runtime_evidence), sb = obj(re.same_book), tot = obj(sb.totals), need = obj(tot.supported_needs), st = obj(re.stream);
      var dp = obj(d.deciding_process), art = obj(d.artifact);
      var html = '<div class="verdict' + (d.verdict === 'LIVE_ADMISSIBLE' ? ' good' : '') + '"><b>' + esc(d.rule || 'P5') + ' v' + esc(d.rule_version || '—') + ': ' + esc(d.verdict || 'UNAVAILABLE') + '</b>' +
        esc((arr(d.proven).length) + ' of ' + arr(d.predicates).length + ' predicates PROVEN · evaluated ' ) + when(d.evaluated_at) + '</div>';
      html += facts([
        ['First blocking', blocker(d.first_blocking)],
        ['First blocking EXTERNAL', blocker(d.first_blocking_external)],
        ['Owner approved', d.owner_approved === true ? pill('yes', 'good') : pill('no', 'warn') + ' <span class="iv-why">' + esc(art.stored_status || '') + '</span>']
      ], 'two');
      html += '<h3 class="h3">Runtime evidence</h3>' + facts([
        ['Stream (workers)', statusPill(st.status) + ' <span class="iv-why">' + esc(arr(st.processes).length + ' process(es) · live bound ' + (st.live_bound_s || '—') + ' s') + '</span>'],
        ['Newest stream record', when(st.newest_recorded_at_epoch, 'no stream evidence recorded')],
        ['Deciding process stream', statusPill(dp.stream_state) + ' <span class="iv-why">' + esc(dp.stream_state_why || '') + '</span>'],
        ['Decision price source', txt(dp.decision_price_source)],
        ['Same-book premise', statusPill(sb.status)],
        ['Same-book comparable samples', n0(tot.comparable, 'not reported') + ' <span class="iv-why">of ' + esc(n0Text(need.min_comparable)) + ' needed</span>'],
        ['Same-book agree rate', share(tot.agree_rate, num(tot.comparable) === 0 ? 'no comparable sample' : 'not reported') + ' <span class="iv-why">needs ≥ ' + esc(num(need.min_agree_rate) !== null ? (need.min_agree_rate * 100).toFixed(0) + '%' : '—') + '</span>'],
        ['Same-book disagreements', n0(tot.disagree, 'not reported')],
        ['Credentials missing (names only)', arr(dp.pmx_missing).length ? esc(arr(dp.pmx_missing).join(', ')) : 'none']
      ]);
      html += '<h3 class="h3">Predicates</h3>' + table([
        ['Predicate', function (p) { return '<span class="mono">' + esc(p.predicate) + '</span>'; }],
        ['Status', function (p) { return statusPill(p.status); }],
        ['Blocker', function (p) { return has(p.blocker) ? esc(p.blocker) : '—'; }],
        ['Reason', function (p) { return has(p.reason) ? esc(clip(p.reason, 60)) : '—'; }],
        ['Runtime evidence', evidenceOf]
      ], d.predicates, 20);
      return html + focusUniverse(obj(d.focus_universe), obj(d.same_book_samples), obj(d.c12_proofs));
    });
  }
  /* the stream's focus universe (what BETTOR holds and evaluates), the same-book
     samples S1 counts and the C12 decision-time proofs -- UNMEASURED stays so */
  function kv(o, n) {
    var k = Object.keys(o);
    return k.length ? esc(clip(k.map(function (x) { return x + ' ' + o[x]; }).join(' · '), n || 160)) : 'none';
  }
  function focusUniverse(fu, sb, c12) {
    var req = obj(sb.required);
    var html = '<h3 class="h3">P5 focus universe</h3>';
    if (fu.status !== 'MEASURED') {
      html += '<p class="muted">' + statusPill(fu.status || 'UNAVAILABLE') + ' ' + esc(fu.why || 'not reported') + '</p>';
    } else {
      html += facts([
        ['Focus universe', n0(fu.count, 'not reported') + ' <span class="iv-why">bound ' + esc(n0Text(fu.bound)) + ' · computed </span>' + when(fu.computed_at)],
        ['Exact identity', n0(fu.exact, 'not reported') + ' <span class="iv-why">UNAVAILABLE ' + esc(n0Text(fu.unavailable)) + '</span>'],
        ['Per tier', kv(obj(fu.per_tier))],
        ['Unavailable reasons', kv(obj(fu.unavailable_reasons))]
      ], 'two');
      html += table([
        ['#', function (m) { return esc(m.rank); }, true],
        ['Tier', function (m) { return '<span class="mono">' + esc(m.tier) + '</span>'; }],
        ['Retail contract', function (m) { return esc(clip(m.retail_slug || '—', 44)) + (has(m.outcome_side) ? ' <span class="iv-why">' + esc(m.outcome_side) + '</span>' : ''); }],
        ['Institutional symbol', function (m) { return has(m.institutional_symbol) ? '<span class="mono">' + esc(m.institutional_symbol) + '</span>' : NA(m.unavailable_reason || 'unmapped'); }],
        ['Identity', function (m) { return statusPill(m.identity_status); }],
        ['Live-eligible', function (m) { return m.grants_live_eligibility ? pill('yes', 'warn') : 'no'; }]
      ], arr(fu.members), 32);
    }
    html += '<h3 class="h3">Same-book samples (S1)</h3>';
    if (sb.status !== 'MEASURED') {
      html += '<p class="muted">' + statusPill(sb.status || 'UNAVAILABLE') + ' ' + esc(sb.why || 'not reported') + '</p>';
    } else {
      html += facts([
        ['Comparable samples', n0(sb.comparable_count, 'not reported') + ' <span class="iv-why">of ' + esc(n0Text(req.min_comparable)) + ' needed · ' + esc(n0Text(sb.sample_count)) + ' probed</span>'],
        ['Agreement', num(sb.agreement_pct) !== null ? esc(sb.agreement_pct.toFixed(1) + '%') : NA(sb.agreement_pct_why || 'no comparable sample')],
        ['Required agreement', num(req.min_agree_pct) !== null ? esc('≥ ' + req.min_agree_pct.toFixed(0) + '%') : '—'],
        ['Incomparable reasons', kv(obj(sb.incomparable_reasons), 200)]
      ], 'two');
      html += table([
        ['Symbol', function (c) { return '<span class="mono">' + esc(c.symbol) + '</span>'; }],
        ['Verdict', function (c) { return statusPill(c.verdict); }],
        ['Tier', function (c) { return esc(c.focus_tier || '—'); }],
        ['Probed', function (c) { return when(c.probed_at); }]
      ], arr(sb.current_comparable_contracts), 12);
    }
    html += '<h3 class="h3">C12 decision-time proofs</h3>';
    if (c12.status !== 'MEASURED') {
      return html + '<p class="muted">' + statusPill(c12.status || 'UNAVAILABLE') + ' ' + esc(c12.why || 'not reported') + '</p>';
    }
    return html + '<p class="iv-src">' + esc(n0Text(c12.count) + ' proof(s) · ') + kv(obj(c12.by_status)) + '</p>' + table([
      ['Recorded', function (r) { return when(r.recorded_at); }],
      ['Intent', function (r) { return '<span class="mono">' + esc(clip(r.execution_intent_id || '—', 18)) + '</span>'; }],
      ['Symbol', function (r) { return has(r.stream_symbol) ? '<span class="mono">' + esc(r.stream_symbol) + '</span>' : '—'; }],
      ['Epoch', function (r) { return esc(n0Text(r.connection_epoch)); }, true],
      ['Book age s', function (r) { return num(r.book_age_s) !== null ? esc(r.book_age_s.toFixed(2)) : '—'; }, true],
      ['Price', function (r) { return num(r.decision_executable_price) !== null ? esc(r.decision_executable_price) : '—'; }, true],
      ['Proof', function (r) { return statusPill(r.proof_status) + (has(r.refusal) ? ' <span class="iv-why">' + esc(clip(r.refusal, 40)) + '</span>' : ''); }]
    ], arr(c12.records), 10);
  }

  /* ── COVERAGE: the funnel ──────────────────────────────────────── */
  var STAGE_FIELD = {provider: 'provider_events', normalized: 'normalized_events', venue_discovered: 'venue_discovered',
                     mapped: 'mapped_events', settlement_supported: 'settlement_supported', evaluated: 'evaluated_events',
                     decided: 'decided_events', entered: 'entered_events', ordered: 'ordered_events', filled: 'filled_events'};
  function renderCoverage() {
    return gate('coverage', function (d) {
      var days = arr(d.days), alerts = arr(d.alerts), stages = arr(d.stages);
      var ps = obj(d.provider_supplement);
      var html = '<p class="iv-src">Unit: ' + esc(d.unit || '—') + ' · day: ' + esc(d.tz || '—') +
        (d.today_computed_live ? ' · today computed from production tables (not yet persisted)' : '') +
        ' · provider heartbeat ' + (ps.status === 'OK' ? esc('age ' + ps.age_s + ' s') : NA(ps.why)) + '</p>';
      html += alerts.length ? '<div class="verdict"><b>' + esc(alerts.length) + ' collapse alert(s)</b><ul class="iv-list">' +
        alerts.slice(0, 8).map(function (a) {
          return '<li>' + statusPill(a.severity) + ' ' + esc(a.league_name || a.league) + ' · ' + esc(a.kind) + ' at ' +
            esc(a.stage_to) + ' · ' + esc(a.day) + (a.audrey_finding_id ? ' · Audrey finding <span class="mono">' + esc(a.audrey_finding_id) + '</span>' : ' · ' + esc(a.audrey_refusal || '')) + '</li>';
        }).join('') + '</ul></div>' : '<p class="muted">No collapse alert in the window.</p>';
      html += nflReconciliation(obj(d.nfl_reconciliation)) + leagueHealth(obj(d.league_status));
      if (!days.length) { return html + '<p class="muted">' + esc(d.why || 'No funnel rows.') + '</p>'; }
      var day = days[0];
      var rows = arr(day.leagues).filter(isObj);
      var cols = [['League', function (r) { return esc(r.league_name || r.league); }]].concat(stages.map(function (s) {
        var f = STAGE_FIELD[s] || s;
        return [label(s), function (r) {
          if (isObj(r.unavailable) && has(r.unavailable[s])) { return NA(r.unavailable[s]); }
          return n0(r[f], 'stage not reported');
        }, true];
      })).concat([['Actual intents', function (r) { return n0(r.actual_intents, 'not reported'); }, true],
                  ['Actual filled', function (r) { return n0(r.actual_filled, 'not reported'); }, true]]);
      return html + '<h3 class="h3">' + esc(day.day) + '</h3>' + table(cols, rows, 20);
    });
  }
  /* every expected NFL game, stage by stage, to ENTER/REFUSE or a named stop */
  function stageCell(v) {
    if (v === null || v === undefined || v === false) { return '—'; }
    if (v === true) { return pill('yes', 'good'); }
    if (isObj(v)) {
      var s = v.verdict || v.status || v.state || v.outcome;
      var why = v.refusal || v.reason || v.why;
      return (has(s) ? statusPill(s) : pill('yes', 'good')) + (has(why) ? ' <span class="iv-why">' + esc(clip(why, 40)) + '</span>' : '');
    }
    return esc(clip(v, 40));
  }
  function nflReconciliation(r) {
    var html = '<h3 class="h3">NFL slate</h3>';
    if (r.status !== 'OK') {
      return html + '<p class="muted">' + statusPill(r.status || 'UNAVAILABLE') + ' ' + esc(r.why || 'not reported') + '</p>';
    }
    var reached = obj(r.reached), stages = arr(r.stages);
    html += '<p class="iv-src">' + esc((r.league_name || 'NFL') + ' · ' + r.day + ' (' + r.tz + ') · expected ' + n0Text(r.expected) + ' · missing ' + arr(r.missing).length) + '</p>';
    html += facts(stages.map(function (s) { return [label(s.toLowerCase()), n0(reached[s], 'not reported')]; }), 'three');
    return html + table([
      ['Game', function (g) { return esc(clip(g.title || g.market_slug, 40)); }],
      ['Kickoff ET', function (g) { return esc(g.kickoff_local || '—'); }]
    ].concat(stages.filter(function (s) { return s !== 'EXPECTED' && s !== 'VENUE_CONTRACT'; }).map(function (s) {
      return [label(s.toLowerCase()), function (g) { return stageCell(obj(g.stages)[s]); }];
    })).concat([['Stopped at', function (g) { return has(g.stopped_at) ? '<span class="mono">' + esc(g.stopped_at) + '</span> <span class="iv-why">' + esc(clip(g.reason || '', 50)) + '</span>' : '—'; }]]),
      arr(r.games), 20);
  }
  /* one health status per league: HEALTHY / REFUSING_BY_POLICY / EXPLICITLY_UNSUPPORTED / COVERAGE_INCIDENT / UNAVAILABLE */
  function leagueHealth(t) {
    var html = '<h3 class="h3">All-sports health</h3>';
    if (!Array.isArray(t.statuses)) {
      return html + '<p class="muted">' + statusPill(t.status || 'UNAVAILABLE') + ' ' + esc(t.why || 'not reported') + '</p>';
    }
    html += '<p class="iv-src">' + kv(obj(t.summary), 240) + '</p>';
    var C = function (k) { return function (r) { return n0(obj(r.counts)[k], 'not measured'); }; };
    return html + table([
      ['League', function (r) { return esc(r.league_name || r.league); }],
      ['Status', function (r) { return statusPill(r.status) + (has(r.stage) ? ' <span class="iv-why">at ' + esc(r.stage) + '</span>' : ''); }],
      ['Provider', C('provider_events'), true],
      ['Venue', C('venue_discovered'), true],
      ['Mapped', C('mapped_events'), true],
      ['Evaluated', C('evaluated_events'), true],
      ['Entered', C('entered_events'), true],
      ['Refused', C('refused_events'), true],
      ['Reason', function (r) { return esc(clip(r.reason || '—', 70)); }]
    ], t.statuses, 60);
  }

  /* ── AGENT COLLABORATION ───────────────────────────────────────── */
  function renderCollab() {
    var html = gate('findings', function (f) {
      var stages = arr(f.stages);
      return '<p class="iv-src">Stages: ' + esc(stages.join(' → ')) + ' · production effect ' + esc(f.production_effect || '—') + '</p>' +
        section(f.findings, function (rows) {
          return table([
            ['Finding', function (x) { return esc(clip(x.title || x.finding_id, 40)); }],
            ['Proposer', function (x) { return txt(x.proposer); }],
            ['Stage', function (x) { return statusPill(x.stage) + ' <span class="iv-why">' + esc(n0Text(x.stage_seq)) + '/' + esc(Math.max(stages.length - 1, 1)) + '</span>'; }],
            ['Statement', function (x) { return esc(clip(x.statement, 70)); }],
            ['Evidence', function (x) { return esc(arr(x.evidence_refs).length); }, true],
            ['Updated', function (x) { return when(x.updated_at); }],
            ['Production effect', function (x) { return txt(x.production_effect); }]
          ], rows, 12);
        }, 'findings');
    });
    var a = data('agents');
    html += '<h3 class="h3">Agent tasks</h3>' + (a ? section(a.tasks, function (rows) {
      return table([
        ['Kind', function (t) { return txt(t.kind); }], ['From', function (t) { return txt(t.created_by); }],
        ['To', function (t) { return txt(t.assignee); }], ['Outcome', function (t) { if (!has(t.outcome)) return '<span class="muted">open (no outcome yet)</span>'; var o = t.outcome; if (o && typeof o === 'object') { var k = o.verdict || o.status || o.state || o.outcome || o.result; return esc(clip(k ? String(k) + (o.why || o.reason ? ' — ' + String(o.why || o.reason) : '') : JSON.stringify(o), 160)); } return txt(o); }],
        ['Created', function (t) { return when(t.created_at); }]
      ], rows, 8);
    }, 'tasks') : NA(failWhy('agents')));
    return html;
  }

  /* ── THE FIVE QUESTIONS (the top strip) ────────────────────────── */
  function line(tag, body) { return '<div class="iv-line">' + tag + ' ' + body + '</div>'; }
  function questions() {
    var paperOpen = xavierPositions('PAPER', 'OPEN'), actualOpen = xavierPositions('ACTUAL', 'OPEN');
    var rp = riskBook('PAPER'), ra = riskBook('ACTUAL');
    var sl = data('smallLive'), acct = sl ? obj(sl.account) : null;
    var q1 = line(PAPER_PILL, paperOpen ? esc(paperOpen.length + ' open') + ' · exposure ' + (rp.rep ? usd(rp.rep.gross_exposure_usd) + ' ' + SHADOW_PILL : NA(rp.why)) : NA(failWhy('xavierMgmt'))) +
      line(ACTUAL_PILL, (actualOpen ? esc(actualOpen.length + ' open') : NA(failWhy('xavierMgmt'))) +
        ' · venue snapshot ' + (acct && acct.status === 'AVAILABLE' ? n0(acct.positions_count, 'not in snapshot') + ' position(s)' : NA(acct ? acct.why : failWhy('smallLive')))) +
      '<div class="iv-note">never summed</div>';

    var open = (paperOpen || []).concat(actualOpen || []);
    var withThesis = open.filter(function (p) { return has(obj(p.thesis).thesis_id); });
    var dec = sl ? arr(obj(sl.decisions).rows)[0] : null;
    var d0 = dec ? obj(dec.decision) : null;
    var q2 = (paperOpen || actualOpen) ? (withThesis.length
      ? esc(withThesis.length + ' of ' + open.length + ' open positions carry an entry thesis')
      : NA('NO_ENTRY_THESIS on all ' + open.length + ' open positions (Xavier read)')) : NA(failWhy('xavierMgmt'));
    q2 += '<div class="iv-line">Latest decision: ' + (d0 ? '<span class="mono">' + esc(clip(d0.market, 24)) + '</span> p ' + prob(d0.probability, 'none') +
      ' · ' + esc(d0.probability_authority || '') + ' · ' + esc(d0.policy_version || '') : NA(failWhy('smallLive') || 'no qualified decision')) + '</div>';

    var eq = rp.rep ? obj(rp.rep.equity) : null;
    var marks = actualMarks();
    var q3 = line(PAPER_PILL, NA('open paper positions are not marked by these reads') + ' · account equity ' +
        (eq ? usd(eq.current_equity_usd, unm(eq, 'current_equity_usd')) + ' (fictional) ' + SHADOW_PILL : NA(rp.why))) +
      line(ACTUAL_PILL, marks === null ? NA(failWhy('smallLive')) : marks.length
        ? marks.slice(0, 3).map(function (m) { return '<span class="mono">' + esc(clip(m.market, 22)) + '</span> ' + usd(m.pnl) + ' unrealized'; }).join(' · ')
        : NA('no actual position carries a fresh venue mark')) +
      (acct && acct.status === 'AVAILABLE' ? '<div class="iv-line">Venue balance ' + usd(acct.current_balance_usd, 'not in snapshot') + '</div>' : '');

    var g = data('regime'), p5 = data('p5'), launch = sl ? obj(sl.launch) : null;
    var regime = g && g.status === 'OK' ? obj(g.data) : null;
    var q4 = '<div class="iv-line">Regime ' + (regime ? pill(regime.recommendation, tone(regime.recommendation)) + ' ' + SHADOW_PILL + ' <span class="iv-why">' + esc(clip(arr(regime.reasons)[0] || '', 90)) + '</span>' : NA(failWhy('regime'))) + '</div>' +
      '<div class="iv-line">P5 stream book ' + (p5 ? statusPill(p5.verdict) + (isObj(p5.first_blocking_external) ? ' <span class="iv-why">first external: ' + esc(p5.first_blocking_external.predicate + ' ' + p5.first_blocking_external.reason) + '</span>' : '') : NA(failWhy('p5'))) + '</div>' +
      '<div class="iv-line">Actual orders possible ' + (launch && typeof launch.actual_orders_possible_now === 'boolean' ? (launch.actual_orders_possible_now ? pill('yes', 'good') : pill('no', 'bad') + ' <span class="iv-why">' + esc(arr(launch.why_not).join('; ')) + '</span>') : NA(failWhy('smallLive') || 'not reported')) + '</div>' +
      '<div class="iv-line">Largest game ' + line(PAPER_PILL, rp.rep ? usd(rp.rep.max_game_exposure_usd) : NA(rp.why)) + line(ACTUAL_PILL, ra.rep ? usd(ra.rep.max_game_exposure_usd) : NA(ra.why)) + '</div>';

    var qd = data('quality'), prof = qd ? obj(qd.profitability) : null;
    var pp = pmBook('paper'), pa = pmBook('actual');
    var mg = function (b) { return b.s ? usd(obj(b.s.management_value_usd).sum, 'unmeasured') : NA(b.why); };
    var k = data('karen'), kp = k && isObj(k.metrics) ? obj(obj(k.metrics.metrics).challenge_precision) : null;
    var cal = data('calibration'), ov = cal && cal.status === 'OK' ? obj(obj(cal.data).overall) : null;
    var calSrc = ov ? Object.keys(ov)[0] : null;
    var q5 = '<div class="iv-line">Forward sample ' + (prof ? 'PAPER ' + statusPill(prof.paper) + ' · ACTUAL ' + statusPill(prof.actual) : NA(failWhy('quality'))) + '</div>' +
      line(PAPER_PILL, 'realized ' + (pp.s ? usd(pp.s.realized_pnl_usd) + ' (n=' + esc(pp.s.positions) + ')' : NA(pp.why)) + ' · Xavier vs hold ' + mg(pp)) +
      line(ACTUAL_PILL, 'realized ' + (pa.s ? usd(pa.s.realized_pnl_usd) + ' (n=' + esc(pa.s.positions) + ')' : NA(pa.why)) + ' · Xavier vs hold ' + mg(pa)) +
      '<div class="iv-line">Calibration ' + (calSrc ? 'Brier ' + n0(ov[calSrc].brier, unm(ov[calSrc], 'brier')) + ' n=' + esc(n0Text(ov[calSrc].n)) + ' ' + SHADOW_PILL : NA(failWhy('calibration') || 'no source')) + '</div>' +
      '<div class="iv-line">Karen precision ' + (kp ? (kp.measurable ? share(kp.value) + ' (' + esc(kp.numerator + '/' + kp.denominator) + ')' : NA(kp.why)) : NA(failWhy('karen'))) + '</div>';

    return [['1', 'What does BETTOR own?', q1], ['2', 'Why?', q2], ['3', 'What is it worth now?', q3],
            ['4', 'What can go wrong?', q4], ['5', 'Is BETTOR adding value?', q5]].map(function (x) {
      return '<div class="iv-q"><div class="n">Question ' + esc(x[0]) + '</div><div class="t">' + esc(x[1]) + '</div><div class="a">' + x[2] + '</div></div>';
    }).join('');
  }

  /* ── rendering and reading ─────────────────────────────────────── */
  function sources(keys) {
    return '<p class="iv-src">' + keys.map(function (k) {
      var r = state.results[k];
      var s = !r ? 'READING' : r.ok ? (isObj(r.data) && (r.data.status === 'UNAVAILABLE' || r.data.status === 'EMPTY') ? r.data.status : 'OK') : (r.http ? 'HTTP ' + r.http : 'ERROR');
      return pill(s, !r ? 'grey' : tone(s)) + '<span class="mono">' + esc(READS[k].path) + '</span>';
    }).join(' &nbsp; ') + '</p>';
  }
  function sectionHtml(s) {
    var body;
    try { body = s.render(s.reads); } catch (e) { body = '<div class="verdict"><b>This section could not be rendered</b>' + NA(e && e.message) + '</div>'; }
    return '<section class="card' + (s.wide ? ' wide' : '') + '" id="iv-' + esc(s.id) + '" aria-label="' + esc(s.title) + '">' +
      '<h2>' + esc(s.title) + (s.shadow ? ' ' + SHADOW_PILL : '') + ' <span class="sub">' + esc(s.sub) + '</span></h2><div class="card-body">' +
      sources(s.reads) + body + '</div></section>';
  }
  // pure: the whole page's HTML from a set of read results (used by the
  // fixture tests under node, and by render() in the browser)
  function renderAll(results) {
    if (results) { state.results = results; }
    var qs;
    try { qs = questions(); } catch (e) { qs = '<div class="verdict"><b>The five questions could not be rendered</b>' + NA(e && e.message) + '</div>'; }
    return {questions: qs, sections: SECTIONS.map(function (s) { return {id: s.id, title: s.title, html: sectionHtml(s)}; })};
  }
  function render() {
    var out = renderAll();
    document.getElementById('ivgrid').innerHTML = out.sections.map(function (s) { return s.html; }).join('');
    document.getElementById('ivquestions').innerHTML = out.questions;
    var keys = Object.keys(READS);
    var ok = keys.filter(function (k) {
      var r = state.results[k];
      return r && r.ok && !(isObj(r.data) && r.data.status === 'UNAVAILABLE');
    }).length;
    var p = document.getElementById('iv-pill');
    if (p) { p.className = 'pill pill-' + (ok === keys.length ? 'good' : ok ? 'warn' : 'bad'); p.textContent = ok + ' / ' + keys.length + ' READS'; }
    document.getElementById('ivstate').textContent = ok === keys.length ? '' :
      (keys.length - ok) + ' read(s) failed or are UNAVAILABLE; their sections say so with the reason and show no numbers.';
    var asof = document.getElementById('asof');
    if (asof) { asof.textContent = 'Read at ' + new Date().toISOString().replace('.000', ''); }
  }
  // the reason a non-2xx answer gives, from FastAPI's {"detail": ...}
  function httpError(status, body) {
    var d = isObj(body) ? body.detail : null;
    var why = isObj(d) ? [d.reason, d.detail].filter(has).join(': ') : (has(d) ? String(d) : '');
    if (status === 404) { return 'not deployed on this build (HTTP 404)'; }
    return 'HTTP ' + status + (why ? ' · ' + why : '');
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
          if (!state.unlockOffered && root.BTUnlock && root.BTUnlock.open) { state.unlockOffered = true; root.BTUnlock.open(); }
          state.results[key] = {ok: false, http: resp.status, error: 'needs a Command session (HTTP ' + resp.status + ')'};
          return;
        }
        if (!resp.ok) {
          return resp.json().then(function (j) { state.results[key] = {ok: false, http: resp.status, error: httpError(resp.status, j)}; },
            function () { state.results[key] = {ok: false, http: resp.status, error: httpError(resp.status, null)}; });
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

  root.BettorInstitutional = {load: load, render: render, renderAll: renderAll, httpError: httpError,
                              READS: READS, SECTIONS: SECTIONS, VERSION: '2.0.0'};
  if (root.document && !root.BETTOR_INSTITUTIONAL_NO_AUTOSTART) {
    if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', start); } else { start(); }
  }
}(typeof window !== 'undefined' ? window : this));
