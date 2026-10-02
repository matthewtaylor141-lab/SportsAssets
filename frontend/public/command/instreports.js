/* INSTITUTIONAL REPORTS ON THE PAPER ACCOUNT (window.BTInstReports).

   Reads GET /api/command/paper/reports/* (sportsassets.paper_institutional)
   and renders the paper account to the standard management expects of a
   real-money account: NAV, P&L statement, performance, positions, blotters,
   attribution, risk, reconciliation, CSV exports and a printable management
   brief. A separate, clearly labelled tab reads the LIVE execution mirror
   (GET /api/command/execmirror) -- the live account is never mixed into the
   paper figures.

   READ-ONLY. No order, policy or control writer. Same fetch contract as
   experiment.js: same-origin credentials, 401/403 -> SIGN-IN REQUIRED,
   404 -> NOT YET RELEASED, anything else -> DISCONNECTED with the reason.
   UNAVAILABLE IS NEVER ZERO: a figure the server could not state is shown
   as UNAVAILABLE with its reason, a section that failed to read says so.
   CSP: no external script, no inline handler; charts are inline SVG. */
(function () {
'use strict';
var BASE = '/api/command/paper/reports/', MIRROR = '/api/command/execmirror';
var POLL_MS = 60000, TIMEOUT_MS = 25000, PAGE = 50;
var TABS = [['overview', 'Overview'], ['pnl', 'P&L statement'], ['performance', 'Performance'],
  ['positions', 'Positions'], ['blotter', 'Blotter'], ['attribution', 'Attribution'],
  ['risk', 'Risk'], ['reconciliation', 'Reconciliation'], ['mirror', 'Live execution mirror · 1:1,000']];
var NEEDS = {overview: ['summary', 'pnl'], pnl: ['pnl'], performance: ['performance'], positions: ['positions'],
  blotter: ['blotter'], attribution: ['attribution'], risk: ['risk'], reconciliation: ['reconciliation'], mirror: ['mirror']};
var EXPORTS = [['blotter_trades', 'Trades'], ['blotter_orders', 'Orders'], ['positions', 'Positions'], ['daily_pnl', 'Daily P&L']];
var st = {tab: 'overview', data: {}, fail: {}, at: {}, busy: {}, mounted: false, timer: null,
  chartPeriod: 'ALL', pnlPeriod: 'ALL', blot: 'trades', off: {trades: 0, orders: 0}, note: null};

// ── formatting ────────────────────────────────────────────────────────
function esc(x) { return String(x == null ? '' : x).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); }
function num(v) { return typeof v === 'number' && isFinite(v); }
function na(why) { return '<span class="ir-na" title="' + esc(why || 'not stated by the server') + '">UNAVAILABLE</span>'; }
function money(v, why) { if (!num(v)) return na(why); var s = Math.abs(v).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}); return (v < 0 ? '−$' : '$') + s; }
function signed(v, why) { if (!num(v)) return na(why); var cls = v > 0.004 ? 'ir-pos' : v < -0.004 ? 'ir-neg' : 'ir-flat'; var s = Math.abs(v).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}); return '<span class="' + cls + '">' + (v > 0.004 ? '+$' : v < -0.004 ? '−$' : '$') + s + '</span>'; }
function pctv(v, why, dp) { if (!num(v)) return na(why); return v.toFixed(dp == null ? 2 : dp) + '%'; }
function spct(v, why) { if (!num(v)) return na(why); var cls = v > 0 ? 'ir-pos' : v < 0 ? 'ir-neg' : 'ir-flat'; return '<span class="' + cls + '">' + (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(2) + '%</span>'; }
function ret(v, why) { return num(v) ? spct(v * 100) : na(why); }
function qty(v) { return num(v) ? v.toLocaleString('en-US', {maximumFractionDigits: 2}) : na(); }
function px(v) { return num(v) ? v.toFixed(4).replace(/0{1,2}$/, '') : na(); }
function ratio(v, why, dp) { return num(v) ? v.toFixed(dp == null ? 2 : dp) : na(why); }
function ts(at) { if (!num(at)) return '—'; var d = new Date(at * 1000); return d.toISOString().slice(0, 16).replace('T', ' ') + 'Z'; }
function ago(at) { if (!num(at)) return 'never'; var s = Math.max(0, Math.round(Date.now() / 1000 - at)); return s < 90 ? s + ' s ago' : s < 5400 ? Math.round(s / 60) + ' min ago' : s < 172800 ? Math.round(s / 3600) + ' h ago' : Math.round(s / 86400) + ' d ago'; }
function words(code) { return esc(String(code == null ? '' : code).replace(/_/g, ' ').toLowerCase()); }
function sec(name, key) { var j = st.data[name]; return j ? j[key] : null; }
function okd(s) { return s && s.status === 'OK' ? s.data : null; }
function bookChip(b) { b = b === 'TRAINING' ? 'TRAINING' : 'INVESTMENT'; return '<span class="ir-book ir-book-' + b + '" title="' + (b === 'TRAINING' ? 'Training book: may be negative expected value — a research cost, not investment performance' : 'Investment book') + '">' + b + '</span>'; }
function short(slug) { return esc(String(slug || '').replace(/^(aec|atc)-/, '')); }
function teamMark(t) { return '<span class="xp-team" title="' + esc(t.name + (t.logo && t.logo.kind === 'flag' ? ' · national flag' : '')) + '"><span class="xp-team-ini" aria-hidden="true">' + esc(t.initials || '?') + '</span>' + (t.logo && t.logo.url ? '<img src="' + esc(t.logo.url) + '" alt="" width="20" height="20" loading="lazy" decoding="async">' : '') + '</span>'; }
function market(x) {
  var m = (x && x.matchup) || [], mk = (x && x.market) || {}, slug = x && (x.us_market_slug || x.market_slug);
  var head = m.length ? '<span class="xp-mu">' + m.slice(0, 2).map(function (t) { return teamMark(t) + '<span class="xp-team-name">' + esc(t.name) + '</span>'; }).join('<span class="xp-vs">vs</span>') + '</span>'
    : '<span class="ir-mkt-t">' + esc(mk.title || short(slug)) + '</span>';
  var sel = mk.selection || x.participant;
  var code = (m.length || mk.title) ? '<span class="xp-code">' + short(slug) + '</span>' : '';
  return '<div class="ir-mkt">' + head + (sel ? '<span class="ir-mkt-s">' + esc(sel) + '</span>' : '') + code + '</div>';
}
function tip(text) { return '<span class="ir-tip" tabindex="0" role="note" aria-label="' + esc(text) + '" title="' + esc(text) + '">i</span>'; }

// ── reads ─────────────────────────────────────────────────────────────
function url(name) {
  if (name === 'mirror') return MIRROR;
  if (name === 'pnl') return BASE + 'pnl?period=ALL';
  if (name.indexOf('pnl:') === 0) return BASE + 'pnl?period=' + encodeURIComponent(name.slice(4));
  if (name === 'blotter') return BASE + 'blotter?kind=' + st.blot + '&limit=' + PAGE + '&offset=' + st.off[st.blot];
  return BASE + name;
}
function load(name, force) {
  if (st.busy[name]) return st.busy[name];
  if (!force && st.data[name] && st.at[name] && Date.now() - st.at[name] < POLL_MS / 2) return Promise.resolve();
  var ctl = typeof AbortController === 'function' ? new AbortController() : null;
  var t = setTimeout(function () { if (ctl) ctl.abort(); }, TIMEOUT_MS);
  var p = fetch(url(name), {credentials: 'same-origin', cache: 'no-store', signal: ctl ? ctl.signal : undefined, headers: {Accept: 'application/json'}})
    .then(function (r) {
      if (r.status === 401 || r.status === 403) { st.fail[name] = {kind: 'SIGNIN', why: 'your COMMAND session is missing or expired'}; return null; }
      if (r.status === 404) { st.fail[name] = {kind: 'NOT_RELEASED', why: 'the serving API does not have this read yet'}; return null; }
      if (!r.ok) { st.fail[name] = {kind: 'DISCONNECTED', why: 'the API answered HTTP ' + r.status}; return null; }
      return r.json();
    })
    .then(function (j) { if (j && typeof j === 'object') { st.data[name] = j; st.fail[name] = null; st.at[name] = Date.now(); } })
    .catch(function (e) { st.fail[name] = {kind: 'DISCONNECTED', why: (e && e.name === 'AbortError') ? 'the read timed out after ' + (TIMEOUT_MS / 1000) + ' s' : 'network error: ' + (e && e.message || e)}; })
    .then(function () { clearTimeout(t); st.busy[name] = null; refresh(); });
  st.busy[name] = p;
  return p;
}
function loadTab(force) {
  var need = (NEEDS[st.tab] || []).slice();
  if (st.tab === 'pnl' && st.pnlPeriod !== 'ALL') need = ['pnl:' + st.pnlPeriod];
  need.forEach(function (n) { load(n, force); });
  // the headline figures stay current on every tab
  if (need.indexOf('summary') < 0) load('summary', false);
}
function signedOut() { return Object.keys(st.fail).some(function (k) { return st.fail[k] && st.fail[k].kind === 'SIGNIN'; }); }

// ── shell ─────────────────────────────────────────────────────────────
function page() {
  return '<section class="ir" aria-label="Institutional reports on the paper account">'
    + '<div class="ir-head"><div><div class="ir-eyebrow">BETTORTOKEN COMMAND / REPORTS · INSTITUTIONAL</div><h1>Institutional reporting</h1>'
    + '<p class="ir-lede">The paper account reported as if it were real money: NAV, P&amp;L, performance, positions, blotters, attribution, risk and reconciliation — every figure derived from the one paper ledger.</p></div>'
    + '<div class="ir-head-side"><span class="ir-badge" title="Paper account — simulated execution on live market data; reported to institutional standards">PAPER ACCOUNT · SIMULATED EXECUTION · LIVE MARKET DATA</span>'
    + '<div class="ir-dl" role="group" aria-label="Downloads"><span class="ir-dl-l">CSV</span>' + EXPORTS.map(function (e) { return '<button class="button ghost small" data-ir-csv="' + e[0] + '">' + e[1] + '</button>'; }).join('')
    + '<button class="button primary small" data-ir-brief="1">Download management brief</button></div></div></div>'
    + '<div id="ir-status" class="ir-status">' + status() + '</div>'
    + '<div class="ir-tabs" role="tablist" aria-label="Report sections">' + TABS.map(function (t) { return '<button role="tab" data-ir-tab="' + t[0] + '" aria-selected="' + (st.tab === t[0]) + '" class="' + (st.tab === t[0] ? 'on' : '') + (t[0] === 'mirror' ? ' ir-tab-live' : '') + '">' + esc(t[1]) + '</button>'; }).join('') + '</div>'
    + '<div id="ir-body" class="ir-body" role="tabpanel">' + body() + '</div></section>';
}
function status() {
  var names = (NEEDS[st.tab] || []), f = null;
  names.forEach(function (n) { if (st.fail[n]) f = st.fail[n]; });
  if (signedOut()) return '<div class="ir-banner ir-signin" role="alert"><b>SIGN-IN REQUIRED</b> · your COMMAND session is missing or has expired, so nothing here is current. <a href="/" data-ir-signin="1">Sign in to COMMAND →</a></div>';
  if (st.note) return '<div class="ir-banner ir-down" role="status">' + esc(st.note) + '</div>';
  var s = st.data.summary;
  var live = s ? '<b>CONNECTED</b> · ledger read ' + ago(s.as_of) + ' · account ' + esc(s.account_id) + (okd(s.summary) && okd(s.summary).ledger ? ' · ledger seq #' + esc(okd(s.summary).ledger.last_sequence) : '') : 'Reading the paper ledger…';
  if (f && st.tab !== 'mirror') live = '<b>' + (f.kind === 'NOT_RELEASED' ? 'NOT YET RELEASED' : 'DISCONNECTED') + '</b> · ' + esc(f.why) + '. These are not zero balances: nothing current has been read for this section.';
  return '<div class="ir-banner ' + (f && st.tab !== 'mirror' ? 'ir-down' : s ? 'ir-up' : '') + '">' + live + '</div>';
}
function refresh() {
  if (!st.mounted) return;
  var s = document.getElementById('ir-status'), b = document.getElementById('ir-body');
  if (s) s.innerHTML = status();
  if (b) { var y = b.scrollTop; b.innerHTML = body(); b.scrollTop = y; bindChart(); }
}
function body() {
  var fn = {overview: overview, pnl: pnlTab, performance: perfTab, positions: posTab, blotter: blotTab,
    attribution: attrTab, risk: riskTab, reconciliation: recTab, mirror: mirrorTab}[st.tab] || overview;
  try { return fn(); } catch (e) { return card('Rendering error', '<p class="ir-note">This section could not be drawn (' + esc(e && e.message) + '). No figure is shown rather than a wrong one.</p>'); }
}
function card(title, inner, cls, sub) { return '<div class="ir-card ' + (cls || '') + '"><div class="ir-card-h"><h3>' + title + '</h3>' + (sub ? '<span class="ir-sub">' + sub + '</span>' : '') + '</div>' + inner + '</div>'; }
function skel(rows, h) { var o = ''; for (var i = 0; i < (rows || 4); i++) o += '<div class="ir-sk" style="height:' + (h || 16) + 'px"></div>'; return '<div class="ir-skel" aria-hidden="true">' + o + '</div>'; }
function pending(name, key, title, h) {
  var f = st.fail[name], s = sec(name, key);
  if (s && s.status !== 'OK') return card(title, '<p class="ir-unav"><b>UNAVAILABLE</b> · ' + esc(s.reason || s.why || 'no reason given') + '</p><p class="ir-note">The server could not state this section. Nothing is shown as zero.</p>', 'ir-wide');
  if (f && !st.data[name]) return card(title, '<p class="ir-unav"><b>' + (f.kind === 'SIGNIN' ? 'SIGN-IN REQUIRED' : f.kind === 'NOT_RELEASED' ? 'NOT YET RELEASED' : 'DISCONNECTED') + '</b> · ' + esc(f.why) + '</p>', 'ir-wide');
  return '<div class="ir-card ir-wide ir-loading" style="min-height:' + (h || 320) + 'px"><div class="ir-card-h"><h3>' + title + '</h3><span class="ir-sub">reading…</span></div>' + skel(6) + '</div>';
}
function kpi(label, value, sub, help) { return '<div class="ir-kpi"><div class="ir-kpi-l">' + esc(label) + (help ? tip(help) : '') + '</div><div class="ir-kpi-v">' + value + '</div>' + (sub ? '<div class="ir-kpi-s">' + sub + '</div>' : '') + '</div>'; }
function table(head, rows, cls, foot) {
  return '<div class="ir-tbl"><table class="' + (cls || '') + '"><thead><tr>' + head.map(function (h) { var n = h.charAt(0) === '#'; return '<th' + (n ? ' class="n"' : '') + '>' + esc(n ? h.slice(1) : h) + '</th>'; }).join('') + '</tr></thead><tbody>'
    + (rows.length ? rows.join('') : '<tr><td colspan="' + head.length + '" class="ir-empty">No records.</td></tr>') + '</tbody>' + (foot || '') + '</table></div>';
}
function td(v, n) { return '<td' + (n ? ' class="n"' : '') + '>' + v + '</td>'; }

// ── overview ──────────────────────────────────────────────────────────
function overview() {
  var S = okd(sec('summary', 'summary'));
  if (!S) return '<div class="ir-grid">' + pending('summary', 'summary', 'Account summary', 420) + '</div>';
  var nav = S.nav || {}, p = S.pnl || {}, per = S.periods || {}, hw = (S.high_water_mark || {}).daily_close || {}, iw = (S.high_water_mark || {}).intraday_snapshots || {}, ex = S.exposure || {}, cap = S.capital || {};
  var tiles = '<div class="ir-kpis ir-kpis-hero">'
    + '<div class="ir-kpi ir-kpi-nav"><div class="ir-kpi-l">Net asset value' + tip(nav.formula || '') + '</div><div class="ir-kpi-v ir-big">' + money(nav.nav_usd, nav.reason) + '</div><div class="ir-kpi-s">' + (num(nav.nav_usd) ? 'cash ' + money(ex.cash_usd) + ' + marked positions' : 'marked-only ' + money(nav.nav_marked_only_usd) + ' · ' + esc(nav.unmarked_positions) + ' unmarked') + '</div></div>'
    + kpi('Total P&L since inception', signed(p.total_pnl_usd, nav.reason), spct(p.total_return_pct, nav.reason) + ' on ' + money(cap.net_deposits_usd) + ' funded', 'NAV − net deposits')
    + kpi('Today', signed((per['1D'] || {}).net_pnl_usd, (per['1D'] || {}).reason), 'realized ' + signed((per['1D'] || {}).realized_pnl_usd))
    + kpi('Week to date', signed((per.WTD || {}).net_pnl_usd, (per.WTD || {}).reason), ret((per.WTD || {})['return']))
    + kpi('Month to date', signed((per.MTD || {}).net_pnl_usd, (per.MTD || {}).reason), ret((per.MTD || {})['return'])) + '</div>';
  var row2 = '<div class="ir-kpis">'
    + kpi('Realized P&L', signed(p.realized_pnl_usd), 'booked from sales & settlements')
    + kpi('Unrealized P&L', signed(p.unrealized_pnl_usd, 'a position has no available mark'), num(p.unrealized_pnl_usd) ? 'marked to the exit price' : 'marked-only ' + signed(p.unrealized_marked_only_usd))
    + kpi('Fees paid', money(p.fees_paid_usd), 'memo · already inside P&L', p.fees_note)
    + kpi('High-water mark', money(hw.high_water_mark_usd), 'drawdown now ' + money(hw.current_drawdown_usd) + ' (' + pctv(hw.current_drawdown_pct) + ')', (S.high_water_mark || {}).basis)
    + kpi('Max drawdown', '<span class="ir-neg">' + money(hw.max_drawdown_usd) + '</span>', pctv(hw.max_drawdown_pct) + ' daily close' + (num(iw.max_drawdown_usd) ? ' · intraday ' + money(iw.max_drawdown_usd) : ''), 'peak-to-trough; starting capital is the first peak')
    + kpi('Open exposure', money(ex.open_cost_basis_usd), esc(ex.open_positions) + ' open position' + (ex.open_positions === 1 ? '' : 's') + ' · max loss ' + money(ex.max_loss_if_all_lose_usd))
    + kpi('Gross / net exposure', money(ex.gross_exposure_usd, 'a position has no mark') + ' <span class="ir-dim">/</span> ' + money(ex.net_exposure_usd, 'a position has no mark'), 'marked value of open contracts', ex.net_formula)
    + kpi('Cash utilisation', pctv(ex.cash_utilisation_pct, 'NAV unavailable'), 'available ' + money(ex.available_cash_usd) + ' · reserved ' + money(ex.reserved_for_open_orders_usd), ex.utilisation_formula) + '</div>';
  var books = S.books || {}, inv = books.INVESTMENT || {}, tr = books.TRAINING || {};
  var bk = '<div class="ir-books">' + bookCard('INVESTMENT', inv) + bookCard('TRAINING', tr) + '</div>';
  return tiles + row2 + '<div class="ir-grid">' + card('Equity curve', equity(), 'ir-wide', periodSeg()) + card('Books — kept apart', bk + '<p class="ir-disc"><b>Training disclosure.</b> ' + esc(S.training_disclosure) + '</p>', 'ir-wide')
    + card('Capital & funding', table(['Date', 'Entry', '#Amount', 'Ledger seq'], (cap.funding_history || []).map(function (f) { return '<tr>' + td(ts(f.at)) + td(words(f.kind)) + td(money(f.amount_usd), 1) + td('#' + esc(f.seq)) + '</tr>'; })) + '<p class="ir-note">' + esc(cap.note || '') + '. Starting capital ' + money(cap.starting_capital_usd) + ' · net deposits ' + money(cap.net_deposits_usd) + '.</p>')
    + card('Period P&L', table(['Period', '#Net P&L', '#Realized', '#Fees', '#Return'], ['1D', 'WTD', 'MTD', 'YTD', 'ALL'].map(function (k) { var x = per[k] || {}; return '<tr>' + td({ '1D': 'Today', WTD: 'Week to date', MTD: 'Month to date', YTD: 'Year to date', ALL: 'Since inception' }[k]) + td(signed(x.net_pnl_usd, x.reason), 1) + td(signed(x.realized_pnl_usd), 1) + td(money(x.fees_usd), 1) + td(ret(x['return'], x.reason), 1) + '</tr>'; })) + '<p class="ir-note">Reporting day: ' + esc(S.reporting_tz) + '. Net P&amp;L = NAV at the end − NAV at the prior close − funding.</p>')
    + '</div>';
}
function bookCard(b, x) {
  return '<div class="ir-bookcard ir-bookcard-' + b + '"><div class="ir-bookcard-h">' + bookChip(b) + '<span class="ir-sub">' + esc((x.strategies || []).length) + ' strateg' + ((x.strategies || []).length === 1 ? 'y' : 'ies') + ' · ' + esc(x.open_positions || 0) + ' open · ' + esc(x.closed_positions || 0) + ' closed</span></div>'
    + '<div class="ir-bookcard-v">' + signed(x.net_pnl_usd, 'an open position in this book has no mark') + '</div><div class="ir-kpi-s">realized ' + signed(x.realized_pnl_usd) + ' · unrealized ' + signed(x.unrealized_pnl_usd, 'unmarked position') + ' · fees ' + money(x.fees_usd) + '</div>'
    + (b === 'TRAINING' ? '<div class="ir-negev">NEGATIVE-EV TRAINING · research cost, not investment performance</div>' : '') + '</div>';
}
function periodSeg() { return '<span class="ir-seg" role="group" aria-label="Chart period">' + ['1D', '1W', 'MTD', 'ALL'].map(function (k) { return '<button data-ir-cp="' + k + '" class="' + (st.chartPeriod === k ? 'on' : '') + '">' + k + '</button>'; }).join('') + '</span>'; }
function curvePoints() {
  var P = okd(sec('pnl', 'pnl_statement'));
  if (!P) return null;
  var k = st.chartPeriod, now = Date.now() / 1000, pts;
  if (k === '1D' || k === '1W') {
    var cut = now - (k === '1D' ? 86400 : 7 * 86400);
    pts = (P.intraday_curve || []).filter(function (p) { return num(p.at) && p.at >= cut; }).map(function (p) { return {x: p.at, v: p.nav_usd, label: ts(p.at)}; });
  } else {
    var rows = P.equity_curve || [];
    if (k === 'MTD') { var m = new Date().toISOString().slice(0, 7); rows = rows.filter(function (r) { return r.date.slice(0, 7) === m; }); }
    pts = rows.map(function (r) { return {x: Date.parse(r.date + 'T12:00:00Z') / 1000, v: r.nav_usd, label: r.date + ' · ' + String(r.nav_basis || '').replace(/_/g, ' ').toLowerCase()}; });
  }
  var hw = null;
  pts.forEach(function (p) { if (num(p.v)) { hw = hw == null ? p.v : Math.max(hw, p.v); } p.hw = hw; });
  return pts;
}
function equity() {
  var pts = curvePoints();
  if (pts == null) { var f = st.fail.pnl; return f ? '<p class="ir-unav"><b>' + (f.kind === 'SIGNIN' ? 'SIGN-IN REQUIRED' : 'UNAVAILABLE') + '</b> · ' + esc(f.why) + '</p>' : '<div class="ir-chart ir-sk-chart" aria-hidden="true"></div>'; }
  var have = pts.filter(function (p) { return num(p.v); });
  if (have.length < 2) return '<div class="ir-chart ir-chart-empty"><p>' + (have.length ? 'One NAV point in this period (' + money(have[0].v) + ') — a curve needs two.' : 'No NAV point is available in this period.') + ' Days whose NAV cannot be stated are gaps, never zero.</p></div>';
  var W = 1000, H = 300, padT = 12, padB = 8;
  var lo = Math.min.apply(null, have.map(function (p) { return p.v; })), hi = Math.max.apply(null, have.map(function (p) { return Math.max(p.v, p.hw); }));
  if (hi - lo < 1) { hi += 1; lo -= 1; }
  var span = hi - lo; lo -= span * 0.08; hi += span * 0.08;
  var x0 = pts[0].x, x1 = pts[pts.length - 1].x; if (x1 === x0) x1 = x0 + 1;
  function X(x) { return ((x - x0) / (x1 - x0)) * W; }
  function Y(v) { return padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB); }
  // segments: a gap (unavailable NAV) breaks the line
  var line = '', area = '', dd = '', seg = [];
  function flush() {
    if (seg.length) {
      line += 'M' + seg.map(function (p) { return X(p.x).toFixed(1) + ',' + Y(p.v).toFixed(1); }).join('L');
      area += 'M' + X(seg[0].x).toFixed(1) + ',' + H + 'L' + seg.map(function (p) { return X(p.x).toFixed(1) + ',' + Y(p.v).toFixed(1); }).join('L') + 'L' + X(seg[seg.length - 1].x).toFixed(1) + ',' + H + 'Z';
      dd += 'M' + seg.map(function (p) { return X(p.x).toFixed(1) + ',' + Y(p.hw).toFixed(1); }).join('L') + 'L' + seg.slice().reverse().map(function (p) { return X(p.x).toFixed(1) + ',' + Y(p.v).toFixed(1); }).join('L') + 'Z';
    }
    seg = [];
  }
  pts.forEach(function (p) { if (num(p.v)) seg.push(p); else flush(); }); flush();
  var start = (okd(sec('summary', 'summary')) || {}).capital || {};
  var base = num(start.starting_capital_usd) && start.starting_capital_usd > lo && start.starting_capital_usd < hi ? '<line x1="0" x2="' + W + '" y1="' + Y(start.starting_capital_usd).toFixed(1) + '" y2="' + Y(start.starting_capital_usd).toFixed(1) + '" class="ir-base" vector-effect="non-scaling-stroke"/>' : '';
  var grid = [0.25, 0.5, 0.75].map(function (f) { var y = (padT + f * (H - padT - padB)).toFixed(1); return '<line x1="0" x2="' + W + '" y1="' + y + '" y2="' + y + '" class="ir-grid-l" vector-effect="non-scaling-stroke"/>'; }).join('');
  var last = have[have.length - 1], first = have[0], ch = last.v - first.v;
  window.__irPts = pts.map(function (p) { return {f: (p.x - x0) / (x1 - x0), v: p.v, hw: p.hw, label: p.label}; });
  return '<div class="ir-chart-meta"><span><span class="ir-key ir-key-nav"></span>NAV</span><span><span class="ir-key ir-key-dd"></span>drawdown from high-water mark</span>' + (base ? '<span><span class="ir-key ir-key-base"></span>starting capital</span>' : '') + '<span class="ir-chart-ch">' + signed(ch) + ' over the period</span></div>'
    + '<div class="ir-chart" data-ir-chart="1"><div class="ir-yax"><span>' + money(hi) + '</span><span>' + money((hi + lo) / 2) + '</span><span>' + money(lo) + '</span></div>'
    + '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" role="img" aria-label="NAV over the selected period, ' + esc(first.label) + ' to ' + esc(last.label) + '">' + grid + base
    + '<path d="' + area + '" class="ir-area"/><path d="' + dd + '" class="ir-dd"/><path d="' + line + '" class="ir-line" vector-effect="non-scaling-stroke"/>'
    + '<line class="ir-cross" x1="0" x2="0" y1="0" y2="' + H + '" vector-effect="non-scaling-stroke" style="display:none"/></svg>'
    + '<div class="ir-ctip" role="status" aria-live="polite"></div></div>'
    + '<div class="ir-xax"><span>' + esc(first.label.split(' · ')[0]) + '</span><span>' + esc(last.label.split(' · ')[0]) + '</span></div>'
    + '<details class="ir-tblview"><summary>Table view</summary>' + table(['Point', '#NAV', '#High-water mark'], pts.map(function (p) { return '<tr>' + td(esc(p.label)) + td(money(p.v, 'NAV not stated for this point'), 1) + td(money(p.hw), 1) + '</tr>'; })) + '</details>';
}
function bindChart() {
  var wrap = document.querySelector('[data-ir-chart]');
  if (!wrap || wrap.__bound) return; wrap.__bound = true;
  var svg = wrap.querySelector('svg'), cross = wrap.querySelector('.ir-cross'), tipEl = wrap.querySelector('.ir-ctip');
  function move(e) {
    var pts = window.__irPts || []; if (!pts.length) return;
    var r = svg.getBoundingClientRect(), f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    var best = pts[0]; pts.forEach(function (p) { if (Math.abs(p.f - f) < Math.abs(best.f - f)) best = p; });
    cross.setAttribute('x1', best.f * 1000); cross.setAttribute('x2', best.f * 1000); cross.style.display = '';
    tipEl.innerHTML = '<b>' + money(best.v, 'NAV not stated') + '</b><span>' + esc(best.label) + '</span>' + (num(best.v) && num(best.hw) && best.hw - best.v > 0.005 ? '<span class="ir-neg">drawdown ' + money(best.hw - best.v) + '</span>' : '');
    tipEl.style.display = 'block';
    var left = best.f * r.width + (svg.getBoundingClientRect().left - wrap.getBoundingClientRect().left);
    tipEl.style.left = Math.max(0, Math.min(left - 80, wrap.clientWidth - 170)) + 'px';
  }
  svg.addEventListener('pointermove', move); svg.addEventListener('pointerdown', move);
  svg.addEventListener('pointerleave', function () { cross.style.display = 'none'; tipEl.style.display = 'none'; });
}

// ── P&L statement ─────────────────────────────────────────────────────
function pnlTab() {
  var key = st.pnlPeriod === 'ALL' ? 'pnl' : 'pnl:' + st.pnlPeriod;
  var seg = '<span class="ir-seg" role="group" aria-label="Statement period">' + ['1D', 'WTD', 'MTD', 'YTD', 'ALL'].map(function (k) { return '<button data-ir-pp="' + k + '" class="' + (st.pnlPeriod === k ? 'on' : '') + '">' + k + '</button>'; }).join('') + '</span>';
  var P = okd(sec(key, 'pnl_statement'));
  if (!P) return '<div class="ir-toolbar">' + seg + '</div><div class="ir-grid">' + pending(key, 'pnl_statement', 'P&L statement', 480) + '</div>';
  var T = P.totals || {};
  var tiles = '<div class="ir-kpis">' + kpi('Net P&L', signed(T.net_pnl_usd, T.reason), esc(T.start || '') + ' → ' + esc(T.end || '')) + kpi('Realized', signed(T.realized_pnl_usd)) + kpi('Unrealized change', signed(T.unrealized_change_usd, T.reason), 'mark-to-market movement', (P.columns || {}).unrealized_change_usd)
    + kpi('Fees', money(T.fees_usd), 'memo · inside P&L') + kpi('Return', ret(T['return'], 'a day in the period has no NAV'), esc(T.days) + ' day' + (T.days === 1 ? '' : 's'), P.return_formula) + '</div>';
  var rows = (P.days || []).map(function (r) {
    return '<tr>' + td(esc(r.date)) + td(money(r.nav_usd, r.nav_unavailable_reason), 1) + td('<span class="ir-basis ir-basis-' + esc(r.nav_basis) + '">' + words(r.nav_basis) + '</span>') + td(signed(r.realized_pnl_usd), 1) + td(signed(r.unrealized_change_usd, 'NAV not stated for this or the prior day'), 1) + td(money(r.fees_usd), 1)
      + td('<b>' + signed(r.net_pnl_usd, 'NAV not stated for this or the prior day') + '</b>', 1) + td(ret(r.daily_return, 'no return without both NAVs'), 1) + td(ret(r.cumulative_return), 1) + td(r.drawdown_usd > 0.005 ? '<span class="ir-neg">' + money(-r.drawdown_usd) + '</span>' : money(r.drawdown_usd), 1) + (num(r.funding_usd) && r.funding_usd ? '' : '') + '</tr>'
      + (num(r.funding_usd) && r.funding_usd ? '<tr class="ir-subrow"><td colspan="10">Funding on ' + esc(r.date) + ': ' + money(r.funding_usd) + ' (a capital flow, not P&amp;L)</td></tr>' : '');
  });
  var colhelp = Object.keys(P.columns || {}).map(function (k) { return '<li><b>' + words(k) + '</b> — ' + esc(P.columns[k]) + '</li>'; }).join('');
  return '<div class="ir-toolbar">' + seg + '<span class="ir-sub">Reporting day ' + esc(P.reporting_tz) + ' · newest first</span></div>' + tiles
    + '<div class="ir-grid">' + card('Daily P&L statement', table(['Date', '#NAV (close)', 'NAV basis', '#Realized', '#Unrealized Δ', '#Fees', '#Net P&L', '#Daily return', '#Cumulative', '#Drawdown'], rows, 'ir-dense'), 'ir-wide', esc((P.days || []).length) + ' days')
    + card('How each column is computed', '<ul class="ir-defs">' + colhelp + '<li><b>return</b> — ' + esc(P.return_formula) + '</li></ul>', 'ir-wide') + '</div>';
}

// ── performance ───────────────────────────────────────────────────────
function perfTab() {
  var P = okd(sec('performance', 'performance'));
  if (!P) return '<div class="ir-grid">' + pending('performance', 'performance', 'Performance', 480) + '</div>';
  var R = P.risk_adjusted || {}, A = P.positions_all_books || {}, B = P.positions_by_book || {};
  var why = R.reason ? words(R.reason) : null;
  var grid = '<div class="ir-kpis">'
    + kpi('Cumulative return', ret(P.cumulative_return, 'no daily return available'), 'geometric link of daily returns', (P.returns || {}).formula)
    + kpi('Return on deposits', ret(P.total_return_on_deposits, 'NAV unavailable'), 'NAV ÷ net deposits − 1')
    + kpi('Volatility (annualised)', num(R.volatility_annualised) ? pctv(R.volatility_annualised * 100) : na(why), esc(R.n_returns) + ' daily returns', R.volatility_formula)
    + kpi('Sharpe-style ratio', ratio(R.sharpe_ratio, why), 'paper results · risk-free 0', R.sharpe_formula)
    + kpi('Mean daily return', num(R.mean_daily_return) ? pctv(R.mean_daily_return * 100, null, 3) : na(why), 'sample σ ' + (num(R.stdev_daily_return) ? pctv(R.stdev_daily_return * 100, null, 3) : na(why)))
    + kpi('Win rate', num(A.win_rate) ? pctv(A.win_rate * 100, null, 1) : na('no closed position'), esc(A.wins) + ' won · ' + esc(A.losses) + ' lost · ' + esc(A.flat) + ' flat of ' + esc(A.closed_positions) + ' closed', (A.formulas || {}).win_rate)
    + kpi('Profit factor', ratio(A.profit_factor, words(A.profit_factor_reason)), 'gross wins ' + money(A.gross_wins_usd) + ' / losses ' + money(A.gross_losses_usd), (A.formulas || {}).profit_factor)
    + kpi('Expectancy', signed(A.expectancy_usd, 'no closed position'), 'per closed position', (A.formulas || {}).expectancy) + '</div>';
  function col(x) { return [num(x.win_rate) ? pctv(x.win_rate * 100, null, 1) : na('no closed position'), signed(x.average_win_usd, 'no winning position'), signed(x.average_loss_usd, 'no losing position'), ratio(x.profit_factor, words(x.profit_factor_reason)), signed(x.expectancy_usd, 'no closed position'), signed(x.total_realized_usd), esc(x.closed_positions)]; }
  var labels = ['Win rate', 'Average win', 'Average loss', 'Profit factor', 'Expectancy', 'Realized (closed)', 'Closed positions'];
  var ci = col(B.INVESTMENT || {}), ct = col(B.TRAINING || {}), ca = col(A);
  var cmp = table(['Statistic', '#Investment', '#Training', '#All books'], labels.map(function (l, i) { return '<tr>' + td(l) + td(ci[i], 1) + td(ct[i], 1) + td(ca[i], 1) + '</tr>'; }));
  function bw(x, w) { var p = x && x[w]; return p ? market(p) + '<div class="ir-kpi-s">' + signed(p.realized_pnl_usd) + ' · ' + words(p.outcome || 'sold') + ' · ' + esc(p.strategy) + '</div>' : na('no closed position'); }
  var rets = (P.returns || {}).daily || [];
  var bars = retBars(rets);
  var bys = table(['Strategy', 'Book', '#Closed', '#Win rate', '#Profit factor', '#Expectancy', '#Realized'], (P.positions_by_strategy || []).map(function (x) { return '<tr>' + td(esc(x.title || x.strategy) + '<div class="xp-code">' + esc(x.strategy) + '</div>') + td(bookChip(x.book)) + td(esc(x.closed_positions), 1) + td(num(x.win_rate) ? pctv(x.win_rate * 100, null, 1) : na(), 1) + td(ratio(x.profit_factor, words(x.profit_factor_reason)), 1) + td(signed(x.expectancy_usd, 'no closed position'), 1) + td(signed(x.total_realized_usd), 1) + '</tr>'; }));
  return grid + '<div class="ir-grid">' + card('Daily returns', bars, 'ir-wide', esc(R.n_returns) + ' available · ' + esc(R.n_unavailable) + ' unavailable')
    + card('Closed positions by book', cmp + '<p class="ir-disc"><b>Training disclosure.</b> ' + esc(P.training_disclosure) + '</p>')
    + card('Best & worst closed position', '<div class="ir-bw"><div><div class="ir-kpi-l">Best</div>' + bw(A, 'best_position') + '</div><div><div class="ir-kpi-l">Worst</div>' + bw(A, 'worst_position') + '</div></div><p class="ir-note">' + esc(P.basis) + '</p>')
    + card('By strategy', bys, 'ir-wide') + '</div>';
}
function retBars(rets) {
  var have = rets.filter(function (r) { return num(r.daily_return); });
  if (!have.length) return '<div class="ir-chart ir-chart-empty"><p>No daily return is available yet. A day without both NAVs has no return — never 0.</p></div>';
  var W = 1000, H = 160, m = Math.max.apply(null, have.map(function (r) { return Math.abs(r.daily_return); })) || 1, n = rets.length, bw = W / n;
  var bars = rets.map(function (r, i) {
    if (!num(r.daily_return)) return '<rect x="' + (i * bw + bw * 0.15).toFixed(1) + '" y="' + (H / 2 - 1) + '" width="' + Math.max(1, bw * 0.7).toFixed(1) + '" height="2" class="ir-bar-na"><title>' + esc(r.date) + ': return unavailable</title></rect>';
    var h = Math.abs(r.daily_return) / m * (H / 2 - 6), y = r.daily_return >= 0 ? H / 2 - h : H / 2;
    return '<rect x="' + (i * bw + bw * 0.15).toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + Math.max(1, bw * 0.7).toFixed(1) + '" height="' + Math.max(1, h).toFixed(1) + '" rx="2" class="' + (r.daily_return >= 0 ? 'ir-bar-pos' : 'ir-bar-neg') + '"><title>' + esc(r.date) + ': ' + (r.daily_return * 100).toFixed(3) + '%</title></rect>';
  }).join('');
  return '<div class="ir-chart ir-chart-sm"><div class="ir-yax"><span>+' + (m * 100).toFixed(2) + '%</span><span>0%</span><span>−' + (m * 100).toFixed(2) + '%</span></div><svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" role="img" aria-label="Daily returns"><line x1="0" x2="' + W + '" y1="' + H / 2 + '" y2="' + H / 2 + '" class="ir-base" vector-effect="non-scaling-stroke"/>' + bars + '</svg></div>'
    + '<div class="ir-xax"><span>' + esc(rets[0].date) + '</span><span>' + esc(rets[rets.length - 1].date) + '</span></div>';
}

// ── positions ─────────────────────────────────────────────────────────
function posTab() {
  var P = okd(sec('positions', 'positions'));
  if (!P) return '<div class="ir-grid">' + pending('positions', 'positions', 'Open positions', 420) + '</div>';
  var rows = (P.positions || []).map(function (x) {
    var prot = x.protection_orders || [];
    var protTxt = prot.length ? prot.map(function (o) { return '<div class="ir-prot"><b>' + words(o.role) + '</b> ' + esc(o.direction) + ' ' + qty(o.qty - (o.filled_qty || 0)) + ' @ ' + px(o.limit_price) + ' · ' + words(o.state) + ' · ' + esc(o.time_in_force) + (num(o.expires_at) ? ' to ' + ts(o.expires_at) : '') + '</div>'; }).join('') : '<span class="ir-dim">none standing</span>';
    var mk = num(x.mark) ? px(x.mark) + (x.mark_status === 'STALE' ? ' <span class="ir-stale" title="mark observed ' + esc(ago(x.mark_observed_at)) + '">STALE</span>' : '') : na(x.mark_reason || 'no observed book');
    return '<tr>' + td(market(x)) + td(esc(x.side)) + td(qty(x.quantity), 1) + td(px(x.average_cost_usd), 1) + td(mk, 1) + td(money(x.market_value_usd, x.mark_reason || 'no mark'), 1) + td(signed(x.unrealized_pnl_usd, x.mark_reason || 'no mark'), 1) + td(pctv(x.pct_of_nav, 'NAV or mark unavailable', 3), 1)
      + td(bookChip(x.book) + '<div class="ir-strat">' + esc(x.strategy_title || x.strategy) + '</div>') + td(ts(x.opened_at)) + td(protTxt) + '</tr>';
  });
  var T = P.totals || {};
  var foot = '<tfoot><tr><td colspan="5">Total · ' + esc(P.count) + ' open</td>' + td(money(T.market_value_usd, 'a position is unmarked'), 1) + td(signed(T.unrealized_pnl_usd, 'a position is unmarked'), 1) + '<td colspan="4">cost basis ' + money(T.cost_basis_usd) + '</td></tr></tfoot>';
  return '<div class="ir-kpis">' + kpi('Open positions', esc(P.count), esc((P.by_book || {}).INVESTMENT || 0) + ' investment · ' + esc((P.by_book || {}).TRAINING || 0) + ' training') + kpi('Market value', money(T.market_value_usd, 'a position is unmarked')) + kpi('Unrealized P&L', signed(T.unrealized_pnl_usd, 'a position is unmarked')) + kpi('Cost basis', money(T.cost_basis_usd), 'incl. fees') + kpi('NAV', money(P.nav_usd, 'marks incomplete')) + '</div>'
    + '<div class="ir-grid">' + card('Open positions', table(['Market', 'Side', '#Qty', '#Avg cost', '#Mark', '#Market value', '#Unrealized', '#% NAV', 'Strategy', 'Opened', 'Xavier’s standing orders'], rows, 'ir-dense ir-pos-t', foot) + '<p class="ir-note">Average cost per contract incl. fees. Marks: ' + esc(P.mark_method) + '.</p>', 'ir-wide')
    + '<p class="ir-disc ir-wide"><b>Training disclosure.</b> ' + esc(P.training_disclosure) + '</p></div>';
}

// ── blotter ───────────────────────────────────────────────────────────
function blotTab() {
  var seg = '<span class="ir-seg" role="group" aria-label="Blotter">' + [['trades', 'Trades'], ['orders', 'Orders']].map(function (k) { return '<button data-ir-bl="' + k[0] + '" class="' + (st.blot === k[0] ? 'on' : '') + '">' + k[1] + '</button>'; }).join('') + '</span>';
  var j = st.data.blotter, s = j && j[st.blot];
  if (!s || !j || (j.__kind && j.__kind !== st.blot)) return '<div class="ir-toolbar">' + seg + '</div><div class="ir-grid">' + pending('blotter', st.blot, st.blot === 'trades' ? 'Trade blotter' : 'Order blotter', 520) + '</div>';
  if (s.status !== 'OK') return '<div class="ir-toolbar">' + seg + '</div><div class="ir-grid">' + pending('blotter', st.blot, 'Blotter') + '</div>';
  var rows = s.data || [], total = s.total || 0, off = st.off[st.blot];
  var pager = '<div class="ir-pager"><button class="button ghost small" data-ir-pg="-1" ' + (off <= 0 ? 'disabled' : '') + '>← Newer</button><span>' + (total ? (off + 1) + '–' + (off + rows.length) + ' of ' + total.toLocaleString('en-US') : '0 records') + '</span><button class="button ghost small" data-ir-pg="1" ' + (!(s.page || {}).has_more ? 'disabled' : '') + '>Older →</button></div>';
  var tbl = st.blot === 'trades'
    ? table(['Time (UTC)', 'Market', 'Role', 'Side', '#Qty', '#Price', '#Gross', '#Fee', '#Ledger cash', 'Ledger seq', 'Order', 'Strategy'], rows.map(function (x) {
      return '<tr>' + td(ts(x.filled_at)) + td(market(x)) + td(words(x.role)) + td(esc(x.direction) + ' ' + esc(x.holding_side)) + td(qty(x.qty), 1) + td(px(x.price), 1) + td(money(x.gross_usd), 1) + td(money(x.fee_usd), 1) + td(signed(x.ledger_cash_delta_usd, 'no ledger entry found'), 1) + td(x.ledger_seq != null ? '#' + esc(x.ledger_seq) : na('no ledger entry')) + td('<span class="ir-id" title="' + esc(x.order_id) + '">' + esc(String(x.order_id || '').slice(-10)) + '</span>') + td(bookChip(x.book) + '<div class="ir-strat">' + esc(x.strategy) + '</div>') + '</tr>'; }), 'ir-dense')
    : table(['Created (UTC)', 'Market', 'Role', 'Side', 'Type · TIF', 'Expires', '#Qty', '#Filled', '#Limit', '#Reserved', 'State', 'Strategy'], rows.map(function (x) {
      return '<tr>' + td(ts(x.created_at)) + td(market(x)) + td(words(x.role)) + td(esc(x.direction) + ' ' + esc(x.holding_side)) + td(esc(x.order_type) + ' · ' + esc(x.time_in_force)) + td(ts(x.expires_at)) + td(qty(x.qty), 1) + td(qty(x.filled_qty), 1) + td(px(x.limit_price), 1) + td(money(x.reserved_remaining_usd != null ? x.reserved_remaining_usd : x.reserved_usd), 1) + td('<span class="ir-state ir-state-' + esc(x.state) + '">' + words(x.state) + '</span>' + (x.terminal_reason && x.terminal_reason !== x.state ? '<div class="ir-strat">' + words(x.terminal_reason) + '</div>' : '')) + td(bookChip(x.book) + '<div class="ir-strat">' + esc(x.strategy) + '</div>') + '</tr>'; }), 'ir-dense');
  return '<div class="ir-toolbar">' + seg + pager + '</div><div class="ir-grid">' + card(st.blot === 'trades' ? 'Trade blotter · every simulated fill' : 'Order blotter · every paper order', tbl + pager, 'ir-wide', 'newest first') + '</div>';
}

// ── attribution ───────────────────────────────────────────────────────
function bars(rows, labelFn, valueKey, opts) {
  opts = opts || {};
  var vals = rows.map(function (r) { return r[valueKey]; });
  var m = Math.max.apply(null, vals.filter(num).map(Math.abs).concat([0.01]));
  return '<div class="ir-bars">' + rows.map(function (r) {
    var v = r[valueKey], has = num(v), w = has ? Math.abs(v) / m * 50 : 0;
    return '<div class="ir-barrow' + (r.book === 'TRAINING' ? ' ir-barrow-tr' : '') + '"><div class="ir-barlab">' + labelFn(r) + '</div><div class="ir-bartrack" title="' + esc(has ? v.toFixed(2) : 'unavailable') + '"><span class="ir-mid"></span>'
      + (has ? '<span class="ir-barfill ' + (v >= 0 ? 'ir-bar-pos' : 'ir-bar-neg') + (r.book === 'TRAINING' ? ' ir-tex' : '') + '" style="' + (v >= 0 ? 'left:50%' : 'right:50%') + ';width:' + w.toFixed(2) + '%"></span>' : '<span class="ir-bar-unav">UNAVAILABLE</span>') + '</div><div class="ir-barval">' + signed(v, opts.why || 'an open position is unmarked') + '</div></div>';
  }).join('') + '</div>';
}
function attrTab() {
  var A = okd(sec('attribution', 'attribution'));
  if (!A) return '<div class="ir-grid">' + pending('attribution', 'attribution', 'Attribution', 480) + '</div>';
  var bk = A.by_book || {};
  var strat = (A.by_strategy || []).map(function (r) { return Object.assign({}, r, {val: num(r.net_pnl_usd) ? r.net_pnl_usd : null}); });
  function sl(r) { return bookChip(r.book) + ' <b>' + esc(r.title || r.strategy) + '</b><div class="ir-strat">' + esc(r.version || r.strategy) + ' · realized ' + signed(r.realized_pnl_usd) + ' · unrealized ' + signed(r.unrealized_pnl_usd, 'unmarked') + ' · fees ' + money(r.fees_usd) + '</div>'; }
  var inv = strat.filter(function (r) { return r.book !== 'TRAINING'; }), tr = strat.filter(function (r) { return r.book === 'TRAINING'; });
  var stratHtml = '<div class="ir-bookhead">' + bookChip('INVESTMENT') + ' net ' + signed((bk.INVESTMENT || {}).net_pnl_usd, 'unmarked position') + '</div>' + (inv.length ? bars(inv, sl, 'val') : '<p class="ir-note">No investment strategy has a position.</p>')
    + '<div class="ir-bookhead ir-bookhead-tr">' + bookChip('TRAINING') + ' net ' + signed((bk.TRAINING || {}).net_pnl_usd, 'unmarked position') + '<span class="ir-negev">NEGATIVE-EV TRAINING</span></div>' + (tr.length ? bars(tr, sl, 'val') : '<p class="ir-note">No training position.</p>')
    + '<p class="ir-disc"><b>Training disclosure.</b> ' + esc(A.training_disclosure) + '</p>';
  function grp(rows, key) { return bars(rows.map(function (r) { return Object.assign({}, r, {val: r.net_pnl_usd}); }), function (r) { return bookChip(r.book) + ' <b>' + esc(String(r[key]).toUpperCase()) + '</b><div class="ir-strat">' + esc(r.positions) + ' positions · ' + esc(r.open_positions) + ' open · realized ' + signed(r.realized_pnl_usd) + '</div>'; }, 'val'); }
  var dec = table(['Agent', 'Decision', 'Book', '#Events', '#Realized', '#Unrealized'], (A.by_decision_type || []).map(function (r) { return '<tr>' + td(esc(r.agent)) + td(words(r.decision_type)) + td(bookChip(r.book)) + td(esc(r.events), 1) + td(signed(r.realized_pnl_usd), 1) + td(signed(r.unrealized_pnl_usd, 'unmarked'), 1) + '</tr>'; }));
  var pol = table(['Entry policy version', 'Book', '#Positions', '#Realized', '#Unrealized', '#Net'], (A.by_policy_version || []).map(function (r) { return '<tr>' + td('<span class="ir-id">' + esc(r.policy_version) + '</span>') + td(bookChip(r.book)) + td(esc(r.positions), 1) + td(signed(r.realized_pnl_usd), 1) + td(signed(r.unrealized_pnl_usd, 'unmarked'), 1) + td(signed(r.net_pnl_usd, 'unmarked'), 1) + '</tr>'; }));
  return '<div class="ir-books">' + bookCard('INVESTMENT', bk.INVESTMENT || {}) + bookCard('TRAINING', bk.TRAINING || {}) + '</div>'
    + '<div class="ir-grid">' + card('P&L by strategy', stratHtml, 'ir-wide', 'net = realized + unrealized')
    + card('By league', grp(A.by_league || [], 'league')) + card('By sport', grp(A.by_sport || [], 'sport'))
    + card('By agent decision type', dec, 'ir-wide') + card('By entry policy version', pol, 'ir-wide')
    + '<p class="ir-note ir-wide">' + esc(A.basis) + '</p></div>';
}

// ── risk ──────────────────────────────────────────────────────────────
function riskTab() {
  var R = okd(sec('risk', 'risk'));
  if (!R) return '<div class="ir-grid">' + pending('risk', 'risk', 'Risk', 480) + '</div>';
  var C = R.concentration || {}, cash = R.cash || {}, sc = R.scenario || {}, gn = R.gross_net || {}, oo = okd(R.open_orders);
  var conc = (C.largest || []).length ? '<div class="ir-conc">' + C.largest.map(function (x) {
    var w = num(x.pct_of_nav) ? Math.min(100, x.pct_of_nav / Math.max(0.0001, (C.largest[0].pct_of_nav || 1)) * 100) : 0;
    return '<div class="ir-concrow"><div class="ir-barlab">' + market(x) + '<div class="ir-strat">' + bookChip(x.book) + ' ' + esc(x.strategy) + (x.value_basis !== 'MARKED' ? ' · <span class="ir-stale">COST BASIS · UNMARKED</span>' : '') + '</div></div><div class="ir-conctrack"><span class="ir-concfill' + (x.book === 'TRAINING' ? ' ir-concfill-tr' : '') + '" style="width:' + w.toFixed(1) + '%"></span></div><div class="ir-barval">' + pctv(x.pct_of_nav, 'NAV unavailable', 3) + '<div class="ir-strat">' + money(x.value_usd) + '</div></div></div>';
  }).join('') + '</div>' : '<p class="ir-note">No open position: nothing is concentrated.</p>';
  function ex(rows, key) { return table([key.charAt(0).toUpperCase() + key.slice(1), '#Positions', '#Cost basis', '#Marked value', '#% NAV', '#Max loss'], rows.map(function (r) { return '<tr>' + td('<b>' + esc(r[key]) + '</b>' + (r.books ? ' ' + r.books.map(bookChip).join(' ') : '')) + td(esc(r.positions), 1) + td(money(r.cost_basis_usd), 1) + td(money(r.marked_value_usd, 'a position is unmarked'), 1) + td(pctv(r.pct_of_nav, 'NAV unavailable', 3) + (r.pct_basis !== 'MARKED' ? '<div class="ir-strat">cost basis</div>' : ''), 1) + td('<span class="ir-neg">' + money(r.max_loss_usd) + '</span>', 1) + '</tr>'; })); }
  var util = num(cash.cash_utilisation_pct) ? Math.max(0, Math.min(100, cash.cash_utilisation_pct)) : null;
  var cashHtml = '<div class="ir-util"><div class="ir-utiltrack">' + (util != null ? '<span style="width:' + util.toFixed(2) + '%"></span>' : '') + '</div><div class="ir-kpi-s">cash utilisation ' + pctv(cash.cash_utilisation_pct, 'NAV unavailable') + tip(cash.utilisation_formula || '') + '</div></div>'
    + table(['Cash', '#USD'], [['Ledger cash', cash.cash_usd], ['Reserved for open orders', cash.reserved_usd], ['Available', cash.available_usd]].map(function (r) { return '<tr>' + td(r[0]) + td(money(r[1]), 1) + '</tr>'; })) + '<p class="ir-note">Reserved is ' + esc(cash.reserved_is || 'part of cash') + '.</p>';
  var ooHtml = !oo ? '<p class="ir-unav"><b>UNAVAILABLE</b> · ' + esc((R.open_orders || {}).reason) + '</p>' : table(['Strategy', 'Role', 'Side', '#Orders', '#Remaining qty', '#Reserved', '#Limit notional'], (oo.by_strategy_role || []).map(function (r) { return '<tr>' + td(bookChip(r.book) + '<div class="ir-strat">' + esc(r.strategy) + '</div>') + td(words(r.role)) + td(esc(r.direction)) + td(esc(r.orders), 1) + td(qty(r.remaining_qty), 1) + td(money(r.reserved_usd), 1) + td(money(r.limit_notional_usd), 1) + '</tr>'; })) + '<p class="ir-note">' + esc(oo.note) + '</p>';
  return '<div class="ir-kpis">' + kpi('Largest position', pctv(C.largest_pct_of_nav, 'NAV unavailable', 3), 'of NAV') + kpi('Top 5 positions', pctv(C.top5_pct_of_nav, 'NAV unavailable', 3), 'of NAV') + kpi('Herfindahl index', ratio(C.herfindahl_index, 'no open position', 4), esc(C.positions) + ' positions', C.herfindahl_note)
    + kpi('Gross / net', money(gn.gross_exposure_usd, 'unmarked position') + ' <span class="ir-dim">/</span> ' + money(gn.net_exposure_usd, 'unmarked position'), 'locked both sides ' + money(gn.locked_both_sides_usd), gn.net_formula)
    + kpi('If every open position loses', '<span class="ir-neg">' + money(sc.max_loss_if_every_open_position_loses_usd) + '</span>', 'if all win ' + signed(sc.max_gain_if_every_open_position_wins_usd), sc.basis) + '</div>'
    + '<div class="ir-grid">' + card('Concentration · largest positions', conc, 'ir-wide', '% of NAV') + card('Cash & reservations', cashHtml) + card('Open-order commitments', ooHtml)
    + card('Exposure by event', ex(R.by_event || [], 'event'), 'ir-wide') + card('Exposure by league', ex(R.by_league || [], 'league')) + card('Exposure by sport', ex(R.by_sport || [], 'sport')) + '</div>';
}

// ── reconciliation ────────────────────────────────────────────────────
function recTab() {
  var R = okd(sec('reconciliation', 'reconciliation'));
  if (!R) return '<div class="ir-grid">' + pending('reconciliation', 'reconciliation', 'Reconciliation', 420) + '</div>';
  var L = R.ledger || {}, au = R.audrey || {}, ar = au.latest_daily_report;
  var pill = '<span class="ir-recpill ' + (R.reconciled ? 'ok' : 'bad') + '">' + (R.reconciled ? '✓ RECONCILED' : '✕ ' + esc(R.discrepancy_count) + ' DISCREPANC' + (R.discrepancy_count === 1 ? 'Y' : 'IES')) + '</span>';
  var checks = '<ul class="ir-checks">' + (R.checks || []).map(function (c) { return '<li class="' + (c.ok ? 'ok' : 'bad') + '"><span class="ir-ck" aria-hidden="true">' + (c.ok ? '✓' : '✕') + '</span><span><b>' + esc(c.description || words(c.check)) + '</b><small>' + esc(c.check) + ' · ' + esc(c.source) + (c.discrepancies ? ' · ' + c.discrepancies + ' found' : '') + '</small></span><span class="ir-ckst">' + (c.ok ? 'PASS' : 'FAIL') + '</span></li>'; }).join('') + '</ul>';
  var disc = (R.discrepancies || []).length ? table(['Check', 'Detail'], R.discrepancies.map(function (d) { var k = Object.keys(d).filter(function (x) { return x !== 'check'; }); return '<tr>' + td('<b>' + words(d.check) + '</b>') + td('<span class="ir-id">' + k.map(function (x) { return esc(x) + ': ' + esc(typeof d[x] === 'object' ? JSON.stringify(d[x]) : d[x]); }).join(' · ') + '</span>') + '</tr>'; })) : '<p class="ir-ok-note">No discrepancy: the ledger, the fills, the positions, the settlements and the open-order reservations agree.</p>';
  var cashOk = num(L.ledger_cash_usd) && num(L.computed_cash_usd) && Math.abs(L.ledger_cash_usd - L.computed_cash_usd) < 0.000001;
  return '<div class="ir-rechead">' + pill + '<span class="ir-sub">' + esc((R.counts || {}).fills) + ' fills · ' + esc((R.counts || {}).positions) + ' positions · ' + esc((R.counts || {}).settled_positions) + ' settled · ledger #' + esc(L.first_seq) + '–#' + esc(L.last_seq) + '</span></div>'
    + '<div class="ir-kpis">' + kpi('Ledger cash (balance)', money(L.ledger_cash_usd)) + kpi('Computed cash (Σ deltas)', money(L.computed_cash_usd), cashOk ? '<span class="ir-pos">agrees</span>' : '<span class="ir-neg">differs</span>') + kpi('Ledger reserved', money(L.reserved_usd)) + kpi('Open-order reservations', money(L.open_orders_reserved_usd))
    + kpi('Audrey’s daily report', ar ? (ar.reconciles ? '<span class="ir-pos">reconciles</span>' : '<span class="ir-neg">does not reconcile</span>') : na('no daily report yet'), ar ? esc(ar.report_day) + ' v' + esc(ar.version) + (ar.final ? ' · final' : ' · provisional') : '') + '</div>'
    + '<div class="ir-grid">' + card('Checks', checks, 'ir-wide', esc((R.checks || []).length) + ' checks') + card('Discrepancies', disc + (R.discrepancies_truncated ? '<p class="ir-note">List truncated at 500.</p>' : ''), 'ir-wide')
    + card('Ledger by entry kind', table(['Kind', '#Entries', '#Cash Δ', '#Reserved Δ', 'Seq range'], (L.by_kind || []).map(function (k) { return '<tr>' + td(words(k.kind)) + td(esc(k.count), 1) + td(signed(k.cash_delta_usd), 1) + td(money(k.reserved_delta_usd), 1) + td('#' + esc(k.first_seq) + '–#' + esc(k.last_seq)) + '</tr>'; })), 'ir-wide')
    + card('Audrey’s findings (7 days)', Object.keys(au.findings_7d_by_severity || {}).length ? '<ul class="ir-sev">' + Object.keys(au.findings_7d_by_severity).map(function (k) { return '<li class="ir-sev-' + esc(k) + '"><b>' + esc(au.findings_7d_by_severity[k]) + '</b> ' + esc(k.toLowerCase()) + '</li>'; }).join('') + '</ul>' : '<p class="ir-note">No findings recorded in the last 7 days.</p>') + '</div>';
}

// ── the live execution mirror (LIVE account, kept apart) ──────────────
function mirrorTab() {
  var f = st.fail.mirror, M = st.data.mirror;
  var head = '<div class="ir-livehead"><span class="ir-livebadge">LIVE ACCOUNT · REAL VENUE</span><span class="ir-sub">The live execution mirror replays paper decisions on the live account at 1:1,000. These are live-account figures, separate from every paper figure on the other tabs.</span></div>';
  if (!M) {
    if (f) return head + '<div class="ir-grid">' + card('Live execution mirror · 1:1,000', '<p class="ir-unav"><b>' + (f.kind === 'NOT_RELEASED' ? 'NOT YET RELEASED' : f.kind === 'SIGNIN' ? 'SIGN-IN REQUIRED' : 'DISCONNECTED') + '</b> · ' + esc(f.kind === 'NOT_RELEASED' ? 'the serving API does not have GET /api/command/execmirror yet' : f.why) + '.</p><p class="ir-note">Nothing has been read from the live account: no balance, P&amp;L or order is shown, and none is zero.</p>', 'ir-wide', 'GET /api/command/execmirror') + '</div>';
    return head + '<div class="ir-grid"><div class="ir-card ir-wide ir-loading" style="min-height:420px"><div class="ir-card-h"><h3>Live execution mirror · 1:1,000</h3><span class="ir-sub">reading…</span></div>' + skel(7) + '</div></div>';
  }
  return head + mirrorBody(M, false);
}
function mirrorBody(M, print) {
  var c = M.control || {}, a = M.account || {}, cov = M.coverage || {}, P = M.pnl || {};
  var stateTxt = c.stopped ? 'STOPPED' : c.enabled ? 'ENABLED' : 'DISABLED';
  var accUnav = a.status === 'UNAVAILABLE';
  var bal = (!accUnav && (a.balances || [])[0]) || {};
  var rec = !accUnav ? a.reconciliation || {} : {};
  var scale = num(c.scale) ? '1:' + Math.round(1 / (c.scale > 1 ? 1 / c.scale : c.scale)).toLocaleString('en-US') : '1:1,000';
  var hdr = '<div class="ir-mstat"><span class="ir-mpill ir-mpill-' + stateTxt + '">' + stateTxt + '</span>'
    + '<span><small>Cutover</small><b>' + ts(typeof c.cutover_at === 'number' ? c.cutover_at : Date.parse(c.cutover_at) / 1000) + '</b></span>'
    + '<span><small>Scale</small><b>' + esc(scale) + '</b>' + (c.rounding ? ' <small>' + words(c.rounding) + '</small>' : '') + '</span>'
    + '<span><small>Account</small><b class="ir-id">' + esc(c.account_fingerprint || '—') + '</b></span>'
    + '<span><small>Live balance</small><b>' + (accUnav ? na(a.why) : money(bal.currentBalance, 'not reported')) + '</b></span>'
    + '<span><small>Buying power</small><b>' + (accUnav ? na(a.why) : money(bal.buyingPower, 'not reported')) + '</b></span>'
    + '<span><small>Reconciliation</small>' + (accUnav ? '<span class="ir-recpill bad">UNAVAILABLE</span>' : '<span class="ir-recpill ' + (rec.reconciled ? 'ok' : 'bad') + '">' + (rec.reconciled ? '✓ RECONCILED' : '✕ ' + Object.keys(rec.differences || {}).length + ' DIFFERENCE(S)') + '</span>') + '</span>'
    + (num(c.max_order_usd) ? '<span><small>Max order</small><b>' + money(c.max_order_usd) + '</b></span>' : '') + '</div>'
    + (c.stopped && c.flatten_on_stop ? '<p class="ir-note">Stopped with flatten-on-stop' + (c.stop_done_at ? ' · flattened ' + esc(c.stop_done_at) : ' · flatten pending') + '.</p>' : '')
    + (accUnav ? '<p class="ir-unav"><b>LIVE ACCOUNT UNAVAILABLE</b> · ' + esc(a.why || 'no reason given') + '</p>' : '');
  var tiles = '<div class="ir-kpis ir-kpis-5">' + kpi('Paper P&L', signed(P.paper_total, 'not stated'), 'paper account') + kpi('Paper P&L ÷ 1,000 (expected live)', signed(P.expected_live_total, 'not stated'), 'what live should show at scale')
    + kpi('Actual live P&L', signed(P.live_total, 'not stated'), 'live account') + kpi('Difference', signed(P.difference, 'not stated'), 'actual − expected') + kpi('Tracking ratio', ratio(P.tracking_ratio, 'expected live P&L is zero or not stated', 3), 'actual ÷ expected') + '</div>';
  // explain the difference: the per-market `explained` parts summed
  var parts = {quantity_rounding: 0, fill_shortfall: 0, fees: 0, price_and_timing: 0}, anyPart = false;
  (P.markets || []).forEach(function (m) { var e = ((m.comparison || {}).explained) || {}; Object.keys(parts).forEach(function (k) { if (num(e[k])) { parts[k] += e[k]; anyPart = true; } }); });
  var LAB = {quantity_rounding: 'Quantity rounding', fill_shortfall: 'Fill shortfall', fees: 'Fees', price_and_timing: 'Price & timing'};
  var mx = Math.max.apply(null, Object.keys(parts).map(function (k) { return Math.abs(parts[k]); }).concat([0.01]));
  var expl = !anyPart ? '<p class="ir-note">No explained difference is reported yet.</p>' : '<div class="ir-bars">' + Object.keys(parts).map(function (k) { var v = parts[k], w = Math.abs(v) / mx * 50; return '<div class="ir-barrow"><div class="ir-barlab"><b>' + LAB[k] + '</b></div><div class="ir-bartrack"><span class="ir-mid"></span><span class="ir-barfill ' + (v >= 0 ? 'ir-bar-pos' : 'ir-bar-neg') + '" style="' + (v >= 0 ? 'left:50%' : 'right:50%') + ';width:' + w.toFixed(2) + '%"></span></div><div class="ir-barval">' + signed(v) + '</div></div>'; }).join('') + '</div><p class="ir-note">Sum of the parts ' + signed(Object.keys(parts).reduce(function (s, k) { return s + parts[k]; }, 0)) + ' vs difference ' + signed(P.difference, 'not stated') + '. Positive = live ahead of the scaled paper result.</p>';
  var mkts = table(['Market', '#Paper P&L', '#Expected live', '#Live P&L', '#Difference', '#Tracking', '#Paper qty', '#Live target / filled', '#Live fees'], (P.markets || []).map(function (m) { var p = m.paper || {}, l = m.live || {}, cmp = m.comparison || {}; return '<tr>' + td(market(typeof m.market === 'object' ? m.market : {us_market_slug: m.market})) + td(signed(p.pnl), 1) + td(signed(cmp.expected_live_pnl), 1) + td(signed(l.pnl, 'not stated'), 1) + td(signed(cmp.difference, 'not stated'), 1) + td(ratio(cmp.tracking_ratio, 'not stated', 3), 1) + td(qty(p.entry_qty), 1) + td(qty(l.entry_target_qty) + ' / ' + qty(l.entry_filled_qty), 1) + td(money(l.fees), 1) + '</tr>'; }), 'ir-dense');
  var orders = table(['Decided', 'Paper order', 'Market', 'Role', 'Strategy', '#Paper qty → expected', 'Live venue order', '#Accepted', 'Standing', '#Filled', '#Avg px', '#Fees', '#Latency', '#Rounding Δ', 'Exclusion'], (M.orders || []).map(function (o) {
    var p = o.paper || {}, l = o.live || {}, s = o.strategy || {};
    var tr = s.kind === 'TRAINING' ? bookChip('TRAINING') + (s.disclosure ? '<span class="ir-negev" title="' + esc(s.disclosure) + '">NEG-EV</span>' : '') : bookChip('INVESTMENT');
    return '<tr>' + td(ts(typeof o.decided_at === 'number' ? o.decided_at : Date.parse(o.decided_at) / 1000)) + td('<span class="ir-id" title="' + esc(o.paper_order_id) + '">' + esc(String(o.paper_order_id || '').slice(-10)) + '</span><div class="ir-strat">' + words(p.state) + ' · filled ' + qty(p.filled_qty) + '</div>') + td(market(typeof o.market === 'object' ? o.market : {us_market_slug: o.market})) + td(words(o.role) + '<div class="ir-strat">' + esc(o.order_type || '') + ' ' + esc(o.tif || '') + (o.post_only ? ' · post-only' : '') + '</div>') + td(tr + '<div class="ir-strat">' + esc(s.strategy || '') + '</div>')
      + td(qty(p.qty) + ' → <b>' + qty(o.expected_scaled_qty) + '</b>', 1) + td(l.venue_order_id ? '<span class="ir-id" title="' + esc(l.venue_order_id) + '">' + esc(String(l.venue_order_id).slice(-10)) + '</span>' : '<span class="ir-dim">not sent</span>') + td(l.exclusion && l.qty == null ? '<span class="ir-dim">—</span>' : qty(l.qty), 1) + td(l.state ? '<span class="ir-state ir-state-' + esc(l.state) + '">' + words(l.venue_state || l.state) + '</span>' : '—') + td(qty(l.filled_qty), 1) + td(num(l.avg_px) ? px(l.avg_px) : '—', 1) + td(num(l.fees_usd) ? money(l.fees_usd) : '—', 1)
      + td(num(l.submit_latency_ms) ? Math.round(l.submit_latency_ms) + ' ms' + (num(l.decision_to_accept_ms) ? '<div class="ir-strat">decision→accept ' + Math.round(l.decision_to_accept_ms) + ' ms</div>' : '') : '—', 1) + td(num(l.rounding_delta) ? qty(l.rounding_delta) : '—', 1)
      + td(l.exclusion ? '<span class="ir-excl" title="' + esc(l.exclusion_detail || '') + '">' + esc(l.exclusion) + '</span>' + (l.exclusion_detail ? '<div class="ir-strat">' + esc(l.exclusion_detail) + '</div>' : '') : (l.error ? '<span class="ir-neg">' + esc(l.error) + '</span>' : '—')) + '</tr>';
  }), 'ir-dense');
  var byState = cov.by_state || {};
  var covHtml = '<div class="ir-cov"><div class="ir-covbig">' + pctv(num(cov.mirrored_pct) ? (cov.mirrored_pct <= 1 ? cov.mirrored_pct * 100 : cov.mirrored_pct) : null, 'not stated', 1) + '<small>mirrored</small></div><div class="ir-kpi-s">' + esc(cov.mirrored) + ' of ' + esc(cov.paper_orders_seen) + ' paper orders mirrored · ' + esc(cov.excluded) + ' excluded</div></div>'
    + table(['State / exclusion', '#Orders'], Object.keys(byState).sort(function (x, y) { return byState[y] - byState[x]; }).map(function (k) { var parts2 = k.split(':'); return '<tr>' + td(words(parts2[0]) + (parts2[1] ? ' · <span class="ir-excl">' + esc(parts2[1]) + '</span>' : '')) + td(esc(byState[k]), 1) + '</tr>'; }));
  var ev = (M.events || []).slice(0, print ? 15 : 40);
  var evHtml = ev.length ? '<ul class="ir-events">' + ev.map(function (e) { return '<li><span class="ir-evt">' + ts(typeof e.at === 'number' ? e.at : Date.parse(e.at) / 1000) + '</span><b>' + words(e.kind) + '</b><span class="ir-id">' + esc(String(e.paper_order_id || e.mirror_id || '').slice(-10)) + '</span><span class="ir-evd">' + esc(typeof e.detail === 'object' ? JSON.stringify(e.detail) : e.detail || '') + '</span></li>'; }).join('') + '</ul>' : '<p class="ir-note">No mirror event recorded.</p>';
  return '<div class="ir-grid">' + card(esc(M.title || 'Live execution mirror · 1:1000'), hdr + '<p class="ir-note">' + esc(M.basis || '') + '</p>', 'ir-wide ir-live') + '</div>' + tiles
    + '<div class="ir-grid">' + card('Explaining the difference', expl, 'ir-live') + card('Coverage', covHtml, 'ir-live')
    + card('Per-market comparison', mkts, 'ir-wide ir-live', 'paper vs live')
    + card('Orders · paper → live venue', orders, 'ir-wide ir-live', esc((M.orders || []).length) + ' orders')
    + card('Recent mirror events', evHtml, 'ir-wide ir-live') + '</div>';
}

// ── CSV downloads ─────────────────────────────────────────────────────
function csv(name) {
  st.note = 'Preparing ' + name.replace(/_/g, ' ') + ' CSV…'; refresh();
  fetch(BASE + 'export/' + name + '.csv', {credentials: 'same-origin', cache: 'no-store'})
    .then(function (r) {
      if (r.status === 401 || r.status === 403) throw new Error('sign-in required');
      if (r.status === 404) throw new Error('this export is not released on the serving API yet');
      if (!r.ok) throw new Error('the API answered HTTP ' + r.status + ' — no file in place of data that could not be read');
      var cd = r.headers.get('content-disposition') || '', m = /filename="([^"]+)"/.exec(cd);
      return r.blob().then(function (b) { return {b: b, fn: m ? m[1] : 'paper_' + name + '.csv'}; });
    })
    .then(function (x) { var u = URL.createObjectURL(x.b), a = document.createElement('a'); a.href = u; a.download = x.fn; document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(u); a.remove(); }, 1500); st.note = null; refresh(); })
    .catch(function (e) { st.note = 'CSV not downloaded: ' + (e && e.message || e); refresh(); setTimeout(function () { st.note = null; refresh(); }, 8000); });
}

// ── the management brief (printable) ──────────────────────────────────
function brief() {
  st.note = 'Assembling the management brief…'; refresh();
  var names = ['summary', 'pnl', 'performance', 'positions', 'attribution', 'risk', 'reconciliation', 'mirror'];
  Promise.all(names.map(function (n) { return load(n, false) || Promise.resolve(); })).then(function () {
    st.note = null; refresh();
    var host = document.getElementById('ir-print');
    if (!host) { host = document.createElement('div'); host.id = 'ir-print'; document.body.appendChild(host); }
    host.innerHTML = briefHtml();
    document.body.classList.add('ir-printing');
    var done = function () { document.body.classList.remove('ir-printing'); window.removeEventListener('afterprint', done); };
    window.addEventListener('afterprint', done);
    setTimeout(function () { try { window.print(); } catch (e) { done(); } }, 60);
  });
}
function briefHtml() {
  var S = okd(sec('summary', 'summary')) || null, now = new Date();
  var out = '<div class="irb"><header class="irb-h"><div><div class="irb-brand">BETTORTOKEN COMMAND</div><h1>Paper account — management brief</h1><p>Account ' + esc((st.data.summary || {}).account_id || '—') + ' · as of ' + esc(ts((st.data.summary || {}).as_of)) + ' · printed ' + esc(now.toISOString().slice(0, 16).replace('T', ' ')) + 'Z</p></div><div class="irb-badge">PAPER ACCOUNT<br>Simulated execution · live market data</div></header>'
    + '<p class="irb-basis">' + esc((st.data.summary || {}).basis || 'Paper account — simulated execution on live market data; reported to institutional standards') + '. Figures marked UNAVAILABLE could not be stated and are never zero.</p>';
  function sect(title, html) { return '<section class="irb-s"><h2>' + esc(title) + '</h2>' + html + '</section>'; }
  function tbl(rows) { return '<table class="irb-t">' + rows.map(function (r) { return '<tr><th>' + esc(r[0]) + '</th><td>' + r[1] + '</td></tr>'; }).join('') + '</table>'; }
  if (S) {
    var p = S.pnl || {}, per = S.periods || {}, hw = (S.high_water_mark || {}).daily_close || {}, ex = S.exposure || {};
    out += sect('Key figures', '<div class="irb-cols">' + tbl([['Net asset value', money(S.nav.nav_usd, S.nav.reason)], ['Net deposits', money((S.capital || {}).net_deposits_usd)], ['Total P&L', signed(p.total_pnl_usd)], ['Total return', spct(p.total_return_pct)], ['Realized P&L', signed(p.realized_pnl_usd)], ['Unrealized P&L', signed(p.unrealized_pnl_usd, 'unmarked position')], ['Fees (in P&L)', money(p.fees_paid_usd)]])
      + tbl([['Today', signed((per['1D'] || {}).net_pnl_usd, (per['1D'] || {}).reason)], ['Week to date', signed((per.WTD || {}).net_pnl_usd, (per.WTD || {}).reason)], ['Month to date', signed((per.MTD || {}).net_pnl_usd, (per.MTD || {}).reason)], ['High-water mark', money(hw.high_water_mark_usd)], ['Max drawdown', money(hw.max_drawdown_usd) + ' (' + pctv(hw.max_drawdown_pct) + ')'], ['Open positions', esc(ex.open_positions)], ['Cash utilisation', pctv(ex.cash_utilisation_pct, 'NAV unavailable')]]) + '</div>');
    var saved = st.chartPeriod; st.chartPeriod = 'ALL';
    out += sect('Equity curve (since inception)', '<div class="irb-chart">' + equity().replace(/<details[\s\S]*<\/details>/, '') + '</div>');
    st.chartPeriod = saved;
    var b = S.books || {};
    out += sect('Investment and training books (never netted)', tbl([['Investment — net P&L', signed((b.INVESTMENT || {}).net_pnl_usd, 'unmarked position') + ' · realized ' + signed((b.INVESTMENT || {}).realized_pnl_usd)], ['Training — net P&L', signed((b.TRAINING || {}).net_pnl_usd, 'unmarked position') + ' · realized ' + signed((b.TRAINING || {}).realized_pnl_usd)]]) + '<p class="irb-disc"><b>Training disclosure.</b> ' + esc(S.training_disclosure) + '</p>');
  } else out += sect('Key figures', '<p>UNAVAILABLE · the summary could not be read.</p>');
  var PF = okd(sec('performance', 'performance'));
  if (PF) { var R = PF.risk_adjusted || {}, A = PF.positions_all_books || {}; out += sect('Performance', tbl([['Cumulative return', ret(PF.cumulative_return, 'none')], ['Volatility (annualised)', num(R.volatility_annualised) ? pctv(R.volatility_annualised * 100) : na(R.reason)], ['Sharpe-style ratio', ratio(R.sharpe_ratio, R.reason)], ['Win rate (closed)', num(A.win_rate) ? pctv(A.win_rate * 100, null, 1) : na()], ['Profit factor', ratio(A.profit_factor, A.profit_factor_reason)], ['Expectancy per closed position', signed(A.expectancy_usd, 'none')]]) + '<p class="irb-note">' + esc(R.sharpe_formula) + '</p>'); }
  var AT = okd(sec('attribution', 'attribution'));
  if (AT) out += sect('P&L by strategy', '<table class="irb-t irb-grid"><tr><th>Strategy</th><th>Book</th><th>Realized</th><th>Unrealized</th><th>Net</th></tr>' + (AT.by_strategy || []).map(function (r) { return '<tr><td>' + esc(r.title || r.strategy) + '</td><td>' + esc(r.book) + '</td><td>' + signed(r.realized_pnl_usd) + '</td><td>' + signed(r.unrealized_pnl_usd, 'unmarked') + '</td><td>' + signed(r.net_pnl_usd, 'unmarked') + '</td></tr>'; }).join('') + '</table>');
  var PO = okd(sec('positions', 'positions'));
  if (PO) out += sect('Largest open positions', '<table class="irb-t irb-grid"><tr><th>Market</th><th>Side</th><th>Qty</th><th>Market value</th><th>Unrealized</th><th>% NAV</th><th>Book</th></tr>' + (PO.positions || []).slice(0, 12).map(function (x) { var mk = x.market || {}; var mu = (x.matchup || []).map(function (t) { return t.name; }).join(' vs '); return '<tr><td>' + esc(mu || mk.title || x.us_market_slug) + (mk.selection ? ' · ' + esc(mk.selection) : '') + '</td><td>' + esc(x.side) + '</td><td>' + qty(x.quantity) + '</td><td>' + money(x.market_value_usd, 'no mark') + '</td><td>' + signed(x.unrealized_pnl_usd, 'no mark') + '</td><td>' + pctv(x.pct_of_nav, 'n/a', 3) + '</td><td>' + esc(x.book) + '</td></tr>'; }).join('') + '</table><p class="irb-note">' + esc(PO.count) + ' open positions in total.</p>');
  var RK = okd(sec('risk', 'risk'));
  if (RK) { var C = RK.concentration || {}; out += sect('Risk', tbl([['Largest position', pctv(C.largest_pct_of_nav, 'n/a', 3) + ' of NAV'], ['Top 5 positions', pctv(C.top5_pct_of_nav, 'n/a', 3) + ' of NAV'], ['Reserved for open orders', money((RK.cash || {}).reserved_usd)], ['Available cash', money((RK.cash || {}).available_usd)], ['If every open position loses', money((RK.scenario || {}).max_loss_if_every_open_position_loses_usd)]])); }
  var RC = okd(sec('reconciliation', 'reconciliation'));
  out += sect('Reconciliation', RC ? '<p><b>' + (RC.reconciled ? 'RECONCILED' : RC.discrepancy_count + ' DISCREPANCIES') + '</b> · ' + esc((RC.checks || []).length) + ' checks · ledger cash ' + money((RC.ledger || {}).ledger_cash_usd) + ' vs computed ' + money((RC.ledger || {}).computed_cash_usd) + '</p>' : '<p>UNAVAILABLE · the reconciliation could not be read.</p>');
  var M = st.data.mirror, mf = st.fail.mirror;
  out += '<section class="irb-s irb-live"><h2>LIVE ACCOUNT · Live execution mirror · 1:1,000</h2><p class="irb-note">Live-account figures, kept separate from every paper figure above.</p>'
    + (M ? (function () { var P = M.pnl || {}, c = M.control || {}, cov = M.coverage || {}; return tbl([['Mirror state', c.stopped ? 'STOPPED' : c.enabled ? 'ENABLED' : 'DISABLED'], ['Paper P&L', signed(P.paper_total, 'n/a')], ['Expected live (paper ÷ 1,000)', signed(P.expected_live_total, 'n/a')], ['Actual live P&L', signed(P.live_total, 'n/a')], ['Difference', signed(P.difference, 'n/a')], ['Tracking ratio', ratio(P.tracking_ratio, 'n/a', 3)], ['Coverage', esc(cov.mirrored) + ' of ' + esc(cov.paper_orders_seen) + ' paper orders mirrored']]); })()
      : '<p>' + (mf && mf.kind === 'NOT_RELEASED' ? 'NOT YET RELEASED · the live mirror read is not on the serving API.' : 'UNAVAILABLE · ' + esc(mf ? mf.why : 'not read')) + ' No live figure is shown, and none is zero.</p>') + '</section>';
  return out + '<footer class="irb-f">BettorToken Command · paper account management brief · read-only · no order submission path</footer></div>';
}

// ── events ────────────────────────────────────────────────────────────
function onClick(e) {
  var t = e.target.closest && e.target.closest('[data-ir-tab],[data-ir-cp],[data-ir-pp],[data-ir-bl],[data-ir-pg],[data-ir-csv],[data-ir-brief],[data-ir-signin]');
  if (!t || !st.mounted) return;
  var d = t.dataset;
  if (d.irSignin) { if (window.BTUnlock && typeof window.BTUnlock.open === 'function') { e.preventDefault(); window.BTUnlock.open(); } return; }
  e.preventDefault();
  if (d.irTab) { st.tab = d.irTab; try { history.replaceState(null, '', '#instreports/' + st.tab); } catch (_) {} document.querySelectorAll('[data-ir-tab]').forEach(function (b) { var on = b.dataset.irTab === st.tab; b.classList.toggle('on', on); b.setAttribute('aria-selected', on); }); loadTab(false); refresh(); try { localStorage.setItem('ir-tab', st.tab); } catch (_) {} return; }
  if (d.irCp) { st.chartPeriod = d.irCp; refresh(); return; }
  if (d.irPp) { st.pnlPeriod = d.irPp; loadTab(false); refresh(); return; }
  if (d.irBl) { st.blot = d.irBl; delete st.data.blotter; load('blotter', true); refresh(); return; }
  if (d.irPg) { st.off[st.blot] = Math.max(0, st.off[st.blot] + Number(d.irPg) * PAGE); load('blotter', true); refresh(); return; }
  if (d.irCsv) { csv(d.irCsv); return; }
  if (d.irBrief) { brief(); return; }
}
document.addEventListener('click', onClick);
document.addEventListener('error', function (e) { var t = e.target; if (t && t.matches && t.matches('.ir .xp-team img')) t.remove(); }, true);
document.addEventListener('visibilitychange', function () { if (!document.hidden && st.mounted) loadTab(true); });

function mount(sub) {
  var ids = TABS.map(function (t) { return t[0]; });
  if (sub && ids.indexOf(sub) >= 0) st.tab = sub;
  else if (!st.mounted && !sub) { try { var s = localStorage.getItem('ir-tab'); if (s && ids.indexOf(s) >= 0) st.tab = s; } catch (_) {} }
  st.mounted = true;
  // app.js renders page() before calling mount(sub): bring the tab bar and
  // body in line with the tab the address names
  document.querySelectorAll('[data-ir-tab]').forEach(function (b) { var on = b.dataset.irTab === st.tab; b.classList.toggle('on', on); b.setAttribute('aria-selected', on); });
  refresh();
  // keep the active tab visible in the scrolling tab bar (horizontal only:
  // app.js re-renders on every feed poll and the page must not jump)
  var bar = document.querySelector('.ir-tabs'), on = bar && bar.querySelector('button.on');
  if (on && (on.offsetLeft < bar.scrollLeft || on.offsetLeft + on.offsetWidth > bar.scrollLeft + bar.clientWidth)) bar.scrollLeft = Math.max(0, on.offsetLeft - 16);
  loadTab(false);
  if (!st.timer) st.timer = setInterval(function () { if (!document.hidden && st.mounted) loadTab(true); }, POLL_MS);
}
function stop() { st.mounted = false; if (st.timer) { clearInterval(st.timer); st.timer = null; } }
function badge() { return signedOut() ? 'SIGN-IN REQUIRED' : st.data.summary ? 'PAPER · REPORTS' : 'PAPER · READING'; }

window.BTInstReports = {page: function () { return page(); }, mount: mount, stop: stop, badge: badge, state: function () { return st; }};
})();
