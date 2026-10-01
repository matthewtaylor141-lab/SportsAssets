/* THE PAPER EXPERIMENT ON THE HOMEPAGE. Reads GET /api/command/paper/experiment
   (every figure a persisted record) and renders it inside the management
   office. Read-only: no order, policy or control writer.

   CONNECTED IS NOT THE SAME AS FILLED. A successful read means the database
   answered; "no filled positions" is then a measured state with its causes.
   A failed read is shown as DISCONNECTED with its reason (the last good read
   stays visible, labelled), and a 401/403 asks for sign-in -- never zeros. */
(function () {
'use strict';
var URL_ = '/api/command/paper/experiment', POLL_MS = 30000, TIMEOUT_MS = 20000;
var st = {json: null, fail: null, okAt: null, busy: false};
var TITLES = {
  PINNACLE_COMPLETED_GAME_PAPER: 'Investment policy (0.5 pp, taker)',
  PINNACLE_COMPLETED_GAME_MAKER_PAPER: 'Investment policy · resting bids',
  PINNACLE_EXPLORATION_PAPER: 'Training / simulated execution',
  DEREK_ENTRY_POLICY_V2: 'Derek research (entries off)',
  PINNACLE_ONLY_PAPER_BENCHMARK: 'Strict benchmark (entries off)'};
function esc(x) { return String(x == null ? '' : x).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); }
function num(v) { return typeof v === 'number' && isFinite(v); }
function usd(v) { return num(v) ? v.toLocaleString('en-US', {style: 'currency', currency: 'USD'}) : '—'; }
function pct(v) { return num(v) ? (v * 100).toFixed(1) + '%' : '—'; }
function pp(v) { return num(v) ? v.toFixed(2) + ' pp' : '—'; }
function px(v) { return num(v) ? v.toFixed(2) : '—'; }
function ago(at, now) { if (!num(at)) return 'never'; var s = Math.max(0, Math.round(now - at)); return s < 90 ? s + ' s ago' : s < 5400 ? Math.round(s / 60) + ' min ago' : Math.round(s / 3600) + ' h ago'; }
function hhmm(at) { return num(at) ? new Date(at * 1000).toISOString().slice(11, 16) + 'Z' : '—'; }
function ok(s) { return s && s.status === 'OK' ? s.data : null; }
function chip(s) { var t = s === 'PINNACLE_EXPLORATION_PAPER' ? 'TRAINING' : s === 'PINNACLE_COMPLETED_GAME_MAKER_PAPER' ? 'MAKER' : s === 'PINNACLE_COMPLETED_GAME_PAPER' ? 'INVESTMENT' : 'RESEARCH'; return '<span class="xp-chip xp-' + t + '" title="' + esc(s) + '">' + t + '</span>'; }
function short(slug) { return esc(String(slug || '').replace(/^(aec|atc)-/, '')); }
function secNote(s, what) { return '<p class="xp-note">' + esc(what) + ': ' + esc(s ? (s.status + (s.why ? ' · ' + s.why : '')) : 'not in the read') + '</p>'; }

function load() {
  if (st.busy) return; st.busy = true;
  var ctl = typeof AbortController === 'function' ? new AbortController() : null;
  var t = setTimeout(function () { if (ctl) ctl.abort(); }, TIMEOUT_MS);
  fetch(URL_, {credentials: 'same-origin', cache: 'no-store', signal: ctl ? ctl.signal : undefined, headers: {Accept: 'application/json'}})
    .then(function (r) {
      if (r.status === 401 || r.status === 403) { st.fail = {kind: 'SIGNIN', why: 'your COMMAND session is missing or expired'}; return null; }
      if (r.status === 404) { st.fail = {kind: 'NOT_RELEASED', why: 'the serving API does not have the experiment read yet'}; return null; }
      if (!r.ok) { st.fail = {kind: 'DISCONNECTED', why: 'the API answered HTTP ' + r.status}; return null; }
      return r.json();
    })
    .then(function (j) { if (j && typeof j === 'object') { st.json = j; st.fail = null; st.okAt = Date.now() / 1000; } })
    .catch(function (e) { st.fail = {kind: 'DISCONNECTED', why: (e && e.name === 'AbortError') ? 'the read timed out after ' + (TIMEOUT_MS / 1000) + ' s' : 'network error: ' + (e && e.message || e)}; })
    .then(function () { clearTimeout(t); st.busy = false; });
}

function banner(now) {
  var j = st.json, f = st.fail;
  if (f && f.kind === 'SIGNIN') return '<div class="xp-banner xp-signin"><b>SIGN-IN REQUIRED</b> · ' + esc(f.why) + '. Nothing below is current until you sign in.</div>';
  if (f && !j) return '<div class="xp-banner xp-down"><b>' + (f.kind === 'NOT_RELEASED' ? 'NOT YET RELEASED' : 'DISCONNECTED') + '</b> · ' + esc(f.why) + '. These are not zero balances: nothing has been read.</div>';
  if (!j) return '<div class="xp-banner">Reading the experiment…</div>';
  var s = j.state || {};
  var head = '<b>CONNECTED</b> · database read ' + ago(j.as_of, now) + ' · ' + esc(s.headline || '');
  if (f) head = '<b>DISCONNECTED</b> · last read failed (' + esc(f.why) + '). Showing the last good read from ' + ago(st.okAt, now) + ' — figures below may be out of date.';
  return '<div class="xp-banner ' + (f ? 'xp-down' : 'xp-up') + '">' + head + '</div>';
}

function sessionCard(j, now) {
  var s = ok(j.session) || {}, fr = (ok(j.freshness) || {}).stamps || {};
  var sess = s.session || {}, pass = s.paper_pass || {}, col = s.collector || {};
  function stamp(k, label) { var x = fr[k] || {}; return '<li><span>' + esc(label) + '</span><b class="xp-v-' + esc(x.verdict || 'NONE') + '">' + (num(x.at) ? ago(x.at, now) : 'none yet') + '</b></li>'; }
  return '<div class="xp-card"><h3>Session & data freshness</h3>'
    + '<p class="xp-k">' + esc(sess.session_id || 'no active session') + (num(sess.started_at) ? ' · started ' + new Date(sess.started_at * 1000).toISOString().slice(0, 16).replace('T', ' ') + 'Z' : '') + '</p>'
    + '<ul class="xp-list">' + stamp('valuation', 'Latest valuation') + stamp('decision', 'Latest decision') + stamp('book', 'Latest order book') + stamp('pass', 'Agent pass') + stamp('collector', 'Collection cycle') + stamp('ledger', 'Latest ledger entry') + '</ul>'
    + '<p class="xp-note">Pass ' + (pass.ran ? 'ran' : 'did not run') + (num(pass.elapsed_s) ? ' in ' + pass.elapsed_s.toFixed(1) + ' s' : '') + (pass.refusal ? ' · ' + esc(pass.refusal) : '') + ' · collector ' + esc(col.label || col.state || '—') + ' · build ' + esc(String(j.serving_build || col.build || '—').slice(0, 7)) + ' · real money ' + esc(j.real_money) + '</p></div>';
}

function funnelCard(j) {
  var o = ok(j.opportunities), r = ok(j.refusals) || {};
  if (!o) return '<div class="xp-card"><h3>Opportunities evaluated (24 h)</h3>' + secNote(j.opportunities, 'opportunities') + '</div>';
  var rows = Object.keys(o.by_strategy || {}).map(function (k) {
    var s = o.by_strategy[k], top = (r[k] || []).filter(function (x) { return x.reason !== 'ENTER'; }).slice(0, 3);
    return '<tr><td>' + chip(k) + ' ' + esc(TITLES[k] || k) + '</td><td class="n">' + s.decisions + '</td><td class="n">' + s.enter + '</td><td class="xp-why">' + top.map(function (x) { return '<span title="' + esc(x.reason) + '">' + x.n + ' · ' + esc(x.words || x.reason.replace(/_/g, ' ').toLowerCase()) + '</span>'; }).join('<br>') + '</td></tr>';
  }).join('');
  var att = o.attempts_6h || {}, attTxt = Object.keys(att).map(function (k) { var v = att[k]; return esc(k.replace(/_/g, ' ').toLowerCase()) + ' ' + Object.keys(v).reduce(function (a, b) { return a + v[b]; }, 0); }).join(' · ');
  return '<div class="xp-card xp-wide"><h3>Opportunities evaluated (24 h)</h3><p class="xp-k">' + o.valuations + ' valuations · ' + o.fixtures + ' fixtures · ' + o.sports + ' sports</p>'
    + '<div class="xp-tbl"><table><thead><tr><th>Strategy</th><th class="n">Decisions</th><th class="n">Entered</th><th>Top refusals</th></tr></thead><tbody>' + rows + '</tbody></table></div>'
    + (attTxt ? '<p class="xp-note">Every attempt recorded (6 h): ' + attTxt + '</p>' : '') + '</div>';
}

function closestCard(j) {
  var c = ok(j.closest);
  if (!c || !c.length) return '<div class="xp-card"><h3>Closest investment opportunities</h3>' + secNote(j.closest, 'closest') + '</div>';
  return '<div class="xp-card xp-wide"><h3>Closest investment opportunities (24 h)</h3><div class="xp-tbl"><table><thead><tr><th>Market</th><th class="n">Pinnacle</th><th class="n">Price</th><th class="n">Gross</th><th class="n">Fee</th><th class="n">After fees</th><th>Result</th></tr></thead><tbody>'
    + c.map(function (x) { return '<tr><td>' + short(x.us_market_slug) + '</td><td class="n">' + (num(x.p_pinnacle) ? x.p_pinnacle.toFixed(4) : '—') + '</td><td class="n">' + px(x.price) + '</td><td class="n">' + pp(x.gross_pp) + '</td><td class="n">' + (num(x.fee_pc) ? (x.fee_pc * 100).toFixed(2) + ' pp' : '—') + '</td><td class="n">' + pp(x.net_pp) + '</td><td class="xp-why">' + esc(x.verdict === 'ENTER' ? 'ENTERED' : String(x.refusal || '').replace(/_/g, ' ').toLowerCase()) + '</td></tr>'; }).join('')
    + '</tbody></table></div><p class="xp-note">The investment rule: Pinnacle probability minus price ≥ 0.5 pp at every level used, and expected profit after the taker fee strictly positive.</p></div>';
}

function ordersCard(j, now) {
  var o = ok(j.orders);
  var ent = o ? o.entry_orders || [] : [], mg = o ? o.management_orders || [] : [];
  var body = !o ? secNote(j.orders, 'orders') : (!ent.length && !mg.length) ? '<p class="xp-note">No open orders right now.</p>'
    : '<div class="xp-tbl"><table><thead><tr><th>Strategy</th><th>Market</th><th>Role</th><th>Type</th><th class="n">Price</th><th class="n">Qty / filled</th><th class="n">Reserved</th><th>Expires</th></tr></thead><tbody>'
    + ent.concat(mg).map(function (x) { return '<tr title="' + esc(x.rationale || '') + '"><td>' + chip(x.strategy) + '</td><td>' + short(x.us_market_slug) + '</td><td>' + esc(x.role) + '</td><td>' + esc(x.order_type + ' ' + x.time_in_force) + '</td><td class="n">' + px(x.limit_price) + '</td><td class="n">' + x.qty + ' / ' + x.filled_qty + '</td><td class="n">' + usd(x.reserved_remaining_usd) + '</td><td>' + hhmm(x.expires_at) + '</td></tr>'; }).join('')
    + '</tbody></table></div>';
  return '<div class="xp-card xp-wide"><h3>Standing orders <span class="xp-sub">' + ent.length + ' entry · ' + mg.length + ' protection</span></h3>' + body + '<p class="xp-note">An order is not a fill: it reserves cash until it fills, expires or is cancelled.</p></div>';
}

function positionsCard(j) {
  var p = ok(j.positions), f = ok(j.fills) || [];
  if (!p) return '<div class="xp-card xp-wide"><h3>Positions</h3>' + secNote(j.positions, 'positions') + '</div>';
  var open = p.open_positions || [];
  var rows = open.map(function (x) { return '<tr><td>' + chip(x.strategy) + ' <span class="xp-lab">' + esc(x.label) + '</span></td><td>' + short(x.market) + '</td><td>' + esc(x.side) + '</td><td class="n">' + x.open_qty + '</td><td class="n">' + usd(x.cost_basis_usd) + '</td><td class="n">' + (num(x.unrealized_pnl_usd) ? usd(x.unrealized_pnl_usd) : 'not marked') + '</td></tr>'; }).join('');
  var strat = Object.keys(p.by_strategy || {}).map(function (k) { var a = p.by_strategy[k]; return '<tr><td>' + chip(k) + ' ' + esc(a.title) + '</td><td class="n">' + a.open + ' / ' + a.positions + '</td><td class="n">' + usd(a.realized_pnl_usd) + '</td><td class="n">' + usd(a.unrealized_pnl_usd) + '</td><td class="n">' + usd(a.fees_usd) + '</td></tr>'; }).join('');
  var ex = p.exploration_limits || {}, lim = ex.limits || {};
  var exTxt = lim.max_aggregate_exposure_usd ? '<p class="xp-note"><b>Exploration budget</b> · exposure ' + usd(ex.exposure_usd) + ' of ' + usd(lim.max_aggregate_exposure_usd) + ' · realized losses ' + usd(ex.realized_losses_usd) + ' of the ' + usd(lim.loss_stop_usd) + ' stop · ' + usd(lim.max_entry_cost_usd) + ' max per position incl. fees · one per fixture' + (ex.loss_stop_reached ? ' · <b>LOSS STOP REACHED</b>' : '') + '. Negative expected value here is a research cost, not investment performance.</p>' : '';
  return '<div class="xp-card xp-wide"><h3>Positions <span class="xp-sub">' + open.length + ' open · ' + f.length + ' recent fills</span></h3>'
    + (open.length ? '<div class="xp-tbl"><table><thead><tr><th>Strategy</th><th>Market</th><th>Side</th><th class="n">Open qty</th><th class="n">Cost basis</th><th class="n">Unrealized</th></tr></thead><tbody>' + rows + '</tbody></table></div>' : '<p class="xp-note">No filled positions are open. The account is connected; the refusals above say why nothing has filled.</p>')
    + (strat ? '<p class="xp-k" style="margin-top:12px">By strategy (one ledger; strategies sum to the account)</p><div class="xp-tbl"><table><thead><tr><th>Strategy</th><th class="n">Open / all</th><th class="n">Realized</th><th class="n">Unrealized</th><th class="n">Fees</th></tr></thead><tbody>' + strat + '</tbody></table></div>' : '')
    + exTxt
    + (f.length ? '<p class="xp-k" style="margin-top:12px">Latest simulated fills</p><ul class="xp-list">' + f.slice(0, 6).map(function (x) { return '<li><span>' + chip(x.strategy) + ' ' + esc(x.role) + ' ' + esc(x.direction) + ' ' + x.qty + ' @ ' + px(x.price) + ' · ' + short(x.us_market_slug) + '</span><b>fee ' + usd(x.fee_usd) + ' · ledger #' + esc(x.ledger_seq) + '</b></li>'; }).join('') + '</ul>' : '')
    + '</div>';
}

function agentsCard(j, now) {
  var a = ok(j.agents);
  if (!a) return '<div class="xp-card"><h3>Agent activity</h3>' + secNote(j.agents, 'agents') + '</div>';
  var d = (a.derek || {}).latest_decision || {}, x = a.xavier || {}, au = a.audrey || {};
  var recs = (au.recommendations || []).slice(0, 4);
  return '<div class="xp-card"><h3>Agent activity</h3><ul class="xp-list">'
    + '<li><span>Derek · latest decision</span><b>' + (num(d.at) ? ago(d.at, now) + ' · ' + esc(d.verdict) + (d.refusal ? ' ' + esc(String(d.refusal).replace(/_/g, ' ').toLowerCase()) : '') : 'none') + '</b></li>'
    + '<li><span>Xavier · reviews (24 h)</span><b>' + (x.reviews_24h || 0) + ' · ' + (x.handoffs || 0) + ' handoffs</b></li>'
    + '<li><span>Audrey · findings</span><b>' + (au.latest_findings || []).length + ' latest</b></li></ul>'
    + (recs.length ? '<p class="xp-k" style="margin-top:10px">Audrey\'s operational recommendations</p><ul class="xp-recs">' + recs.map(function (r) { var resp = (r.events || []).filter(function (e) { return e.kind === 'RESPONSE'; })[0]; return '<li><b>' + esc(r.status) + '</b> · to ' + esc(r.owner_agent) + ' · ' + esc(r.recommendation) + '<br><i>' + (num(r.agent_responses) ? r.agent_responses : 0) + ' agent responses · ' + (num(r.measurements) ? r.measurements : 0) + ' measurements' + (num(r.automated_acknowledgements) && r.automated_acknowledgements ? ' · automated acknowledgement (template, not an agent review)' : '') + '</i>' + (resp ? '<br><i>' + esc(resp.actor) + ': ' + esc(String(resp.body).slice(0, 160)) + '…</i>' : '') + '</li>'; }).join('') + '</ul><p class="xp-note">Operational improvements only — never reported as profitable learning.</p>' : '<p class="xp-note">Audrey\'s operational audit runs every 10 minutes.</p>')
    + '</div>';
}

function throughputCard(j) {
  var t = ok(j.throughput);
  if (!t) return '';
  return '<div class="xp-card"><h3>Acquisition throughput (6 h)</h3><div class="xp-tbl"><table><thead><tr><th>Hour</th><th class="n">Book reads</th><th class="n">Shared</th><th class="n">Failed</th><th class="n">Cut by deadline</th></tr></thead><tbody>'
    + t.hours.map(function (h) { return '<tr><td>' + hhmm(h.hour) + '</td><td class="n">' + h.book_reads + '</td><td class="n">' + h.shared_reads + '</td><td class="n">' + h.failed_reads + '</td><td class="n">' + h.decisions_cut_by_deadline + '</td></tr>'; }).join('')
    + '</tbody></table></div><p class="xp-note">A shared read answers a decision from this server\'s own seconds-old venue read (same freshness limits), saving a venue request.</p></div>';
}

function html() {
  var now = Date.now() / 1000, j = st.json;
  var out = '<section class="xp" aria-label="The paper experiment"><div class="xp-head"><div><span class="office-eyebrow">THE EXPERIMENT</span><h2>What the team is doing with the paper account.</h2></div><span class="xp-sub">Every figure is a stored record · simulated execution · live market data</span></div>' + banner(now);
  if (j) out += '<div class="xp-grid">' + sessionCard(j, now) + agentsCard(j, now) + funnelCard(j) + closestCard(j) + ordersCard(j, now) + positionsCard(j) + throughputCard(j) + '</div>';
  return out + '</section>';
}

window.BTExperiment = {html: html, load: load, state: function () { return st; }};
load();
setInterval(load, POLL_MS);
document.addEventListener('visibilitychange', function () { if (!document.hidden) load(); });
})();
