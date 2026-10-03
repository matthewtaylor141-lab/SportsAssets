/* BETTOR EV ENGINE · SMALL LIVE — PAPER vs ACTUAL, rendered.
 *
 * ONE SOURCE: an authenticated same-origin GET of /api/command/small-live
 * (execmirror_view.small_live). The path passes BTCore.endpoint(); the only
 * query string ever appended is built here from two fixed allowlists
 * (view, status), never from free text.
 *
 * MANAGEMENT MUST NEVER INFER WHICH FIGURE IS SIMULATED. Every trade shows
 * two columns that are never merged: "PAPER — SIMULATED" and
 * "ACTUAL — <VENUE>", each with its own badge. A figure the read does not
 * carry renders as "unavailable" -- never as zero, and nothing is computed
 * here that the server did not send.
 */
(function () {
  'use strict';

  var C = window.BTCore;
  var PATH = '/api/command/small-live';
  var POLL_MS = 30000;
  var VIEWS = ['paper', 'polymarket', 'kalshi'];
  var STATUSES = ['open', 'filled', 'closed', 'refused'];
  var state = {view: null, status: null, data: null, timer: null};

  function esc(v) { return C ? C.esc(v) : String(v == null ? '' : v); }
  function has(v) { return v !== null && v !== undefined && v !== ''; }
  function num(v) { return (typeof v === 'number' && isFinite(v)) ? v : null; }
  var NA = '<span class="sl-na">unavailable</span>';
  function txt(v) { return has(v) ? esc(v) : NA; }
  function qty(v) {
    var n = num(v);
    return n === null ? NA : esc(String(Number(n.toFixed(6))));
  }
  function px(v) {
    var n = num(v);
    return n === null ? NA : esc((n * 100).toFixed(2) + '¢');
  }
  function usd(v, d) {
    var n = num(v);
    if (n === null) { return NA; }
    var s = Math.abs(n).toFixed(d == null ? 2 : d);
    return esc((n < 0 ? '−$' : '$') + s);
  }
  function signedPx(v) {
    var n = num(v);
    if (n === null) { return NA; }
    return esc((n > 0 ? '+' : n < 0 ? '−' : '') + (Math.abs(n) * 100).toFixed(2) + '¢');
  }
  function signedQty(v) {
    var n = num(v);
    if (n === null) { return NA; }
    return esc((n > 0 ? '+' : n < 0 ? '−' : '') + String(Number(Math.abs(n).toFixed(6))));
  }
  function signedUsd(v) {
    var n = num(v);
    if (n === null) { return NA; }
    return esc((n > 0 ? '+' : n < 0 ? '−' : '') + '$' + Math.abs(n).toFixed(4));
  }
  function ms(v) {
    var n = num(v);
    return n === null ? NA : esc(n.toLocaleString('en-US') + ' ms');
  }
  function pct(v) {
    var n = num(v);
    return n === null ? NA : esc((n * 100).toFixed(2) + '%');
  }
  function pp(v) {
    var n = num(v);
    return n === null ? NA : esc(n.toFixed(2) + ' pp');
  }
  function when(v) {
    if (!has(v) || !isFinite(Date.parse(v))) { return NA; }
    return '<span class="mono">' +
      esc(new Date(v).toISOString().replace('.000', '').replace('T', ' ')) + '</span>';
  }
  function pill(text, tone) {
    return '<span class="pill pill-' + (tone || 'grey') + '">' + esc(text) + '</span>';
  }
  function fact(k, v) {
    return '<div class="fact"><span class="k">' + esc(k) + '</span>' +
      '<span class="v">' + v + '</span></div>';
  }
  function json(v) {
    try { return esc(JSON.stringify(v, null, 2)); } catch (e) { return ''; }
  }
  function statusTone(s) {
    return s === 'filled' ? 'good' : s === 'refused' ? 'bad'
      : s === 'open' ? 'blue' : 'grey';
  }

  /* ── header strip: mirror control, account snapshot, venues ─────────── */
  function strip(d) {
    var c = d.control || {};
    var a = d.account || {};
    var cTone = c.state === 'ENABLED' ? 'good' : c.state === 'STOPPED' ? 'bad' : 'warn';
    var pillEl = document.getElementById('mirror-pill');
    if (pillEl) {
      pillEl.className = 'pill pill-' + cTone;
      pillEl.textContent = 'MIRROR ' + (c.state || 'UNAVAILABLE');
    }
    var venues = (d.venues || []).map(function (v) {
      var tone = v.status === 'MIRROR_ENABLED' ? 'good'
        : v.status === 'NOT_CONNECTED' ? 'grey'
        : v.status === 'MIRROR_STOPPED' ? 'bad' : 'warn';
      return '<li><b>' + esc(v.venue) + '</b> ' + pill(v.status, tone) +
        '<span class="muted"> — ' + txt(v.why) + '</span></li>';
    }).join('');
    var ctl = '<div class="facts three">' +
      fact('Mirror', pill(c.state || 'UNAVAILABLE', cTone)) +
      fact('Cutover', when(c.cutover_at)) +
      fact('Scale', num(c.scale) === null ? NA : esc('1 : ' + Number(c.scale).toLocaleString('en-US'))) +
      fact('Cap per order', usd(c.cap_usd_per_order)) +
      fact('Account', has(c.account_fingerprint_prefix)
        ? '<span class="mono">' + esc(c.account_fingerprint_prefix) + '…</span>' : NA) +
      fact('Control revision', txt(c.revision)) +
      '</div>';
    var acct = a.status === 'AVAILABLE'
      ? '<div class="facts three">' +
        fact('Snapshot at', when(a.at)) +
        fact('Buying power', usd(a.buying_power_usd)) +
        fact('Balance', usd(a.current_balance_usd)) +
        fact('Positions', txt(a.positions_count)) +
        fact('Open orders', txt(a.open_orders_count)) +
        fact('Reconciled with venue', a.reconciled === true ? pill('YES', 'good')
          : a.reconciled === false ? pill('DIFFERENCE', 'bad') : NA) +
        '</div>'
      : '<p class="muted">Account snapshot unavailable — ' + txt(a.why) + '.</p>';
    return '<section class="card"><h2>Mirror control &amp; ACTUAL account</h2>' +
      '<div class="card-body"><p class="sl-basis">' + txt(d.basis) + '</p>' + ctl + '<h3 class="h3">ACTUAL account snapshot (venue)</h3>' + acct +
      '<h3 class="h3">Venues</h3><ul class="sl-venues">' + venues + '</ul>' +
      '</div></section>';
  }

  /* ── filter chips ────────────────────────────────────────────────────── */
  function chip(kind, value, label, count) {
    var on = state[kind] === value;
    return '<button type="button" class="sl-chip' + (on ? ' on' : '') +
      '" data-' + kind + '="' + esc(value == null ? '' : value) + '" aria-pressed="' +
      (on ? 'true' : 'false') + '">' + esc(label) +
      (count === undefined ? '' : ' <span class="sl-count">' +
        (num(count) === null ? '—' : esc(count)) + '</span>') + '</button>';
  }
  function filters(d) {
    var k = (d && d.counts) || {};
    var v = k.view || {};
    var side = state.view === 'paper' ? (k.paper_status || {}) : (k.actual_status || {});
    var el = document.getElementById('livefilters');
    if (!el) { return; }
    el.innerHTML = '<div class="sl-chiprow"><span class="sl-chiplabel">Venue</span>' +
      chip('view', null, 'ALL') + chip('view', 'paper', 'PAPER', v.paper) +
      chip('view', 'polymarket', 'POLYMARKET', v.polymarket) +
      chip('view', 'kalshi', 'KALSHI', v.kalshi) + '</div>' +
      '<div class="sl-chiprow"><span class="sl-chiplabel">Status' +
      (state.view === 'paper' ? ' (paper side)' : state.view === 'kalshi' ? '' : ' (actual side)') +
      '</span>' + chip('status', null, 'all') +
      STATUSES.map(function (s) { return chip('status', s, s, side[s]); }).join('') + '</div>';
  }

  /* ── one trade ──────────────────────────────────────────────────────── */
  function decision(d) {
    var src = d.present ? '' : '<p class="muted">' + txt(d.why) + '</p>';
    return '<div class="sl-decision"><h3 class="h3">Decision</h3>' + src +
      '<div class="facts three">' +
      fact('Decided at', when(d.decided_at)) +
      fact('Decision', has(d.decision_id) ? '<span class="mono">' + esc(d.decision_id) + '</span>' : NA) +
      fact('Strategy / policy', txt(d.strategy) + ' · ' + txt(d.policy_version)) +
      fact('Verdict', has(d.verdict) ? pill(d.verdict, d.verdict === 'ENTER' ? 'good' : 'warn') : NA) +
      fact('Pinnacle probability', pct(d.p_pinnacle)) +
      fact('Executable venue price', px(d.executable_price)) +
      fact('Limit', px(d.limit_price)) +
      fact('Gross edge', pp(d.gross_edge_pp)) +
      fact('Edge at executable price', pp(d.edge_at_executable_pp)) +
      fact('Expected net EV', usd(d.net_ev_usd)) +
      fact('Net EV per contract', usd(d.net_ev_per_contract_usd, 4)) +
      fact('Valuation · provider', txt(d.valuation_id) + ' · ' + txt(d.provider)) +
      '</div></div>';
  }

  function paperCol(p) {
    var head = '<div class="sl-colhead"><span>PAPER — SIMULATED</span>' +
      pill('SIMULATED', 'blue') + '</div>';
    if (!p.present) {
      return '<div class="sl-col sl-paper">' + head +
        '<p class="muted">' + txt(p.why) + '</p></div>';
    }
    return '<div class="sl-col sl-paper">' + head + '<div class="facts">' +
      fact('Paper order', '<span class="mono">' + esc(p.order_id) + '</span>') +
      fact('Status', has(p.status) ? pill(p.status, statusTone(p.status)) + ' ' + txt(p.state) : NA) +
      fact('Quantity', qty(p.qty)) +
      fact('Limit / wire price', px(p.limit_price) + ' / ' + px(p.wire_price)) +
      fact('Simulated fill qty', qty(p.simulated_filled_qty)) +
      fact('Avg simulated fill price', px(p.avg_simulated_fill_price)) +
      fact('Simulated fees', usd(p.simulated_fees_usd)) +
      fact('First simulated fill', when(p.first_simulated_fill_at)) +
      fact('Paper P&L (group, realized)', num(p.group_realized_pnl_usd) === null
        ? NA + (p.group_pnl_why_unavailable
          ? '<span class="sl-why">' + esc(p.group_pnl_why_unavailable) + '</span>' : '')
        : usd(p.group_realized_pnl_usd)) +
      '</div></div>';
  }

  function actualCol(a) {
    var head = '<div class="sl-colhead"><span>ACTUAL — ' + esc(a.venue || 'VENUE') +
      '</span>' + pill('ACTUAL', 'good') + '</div>';
    var excl = a.excluded
      ? '<div class="verdict"><b>Not sent — ' + esc(a.exclusion) + '</b>' +
        (a.exclusion_detail && a.exclusion_detail.why ? esc(a.exclusion_detail.why) : '') +
        (num(a.would_have_cost_usd) !== null ? ' Would have cost ' + usd(a.would_have_cost_usd) + '.' : '') +
        '</div>' : '';
    var why = a.why_no_fill_figures && !a.excluded
      ? '<p class="muted">' + esc(a.why_no_fill_figures) + '</p>' : '';
    return '<div class="sl-col sl-actual">' + head + excl + why + '<div class="facts">' +
      fact('Status', has(a.status) ? pill(a.status, statusTone(a.status)) + ' ' + txt(a.state) : NA) +
      fact('Scaled qty → intended', qty(a.scaled_qty) + ' → ' + txt(a.intended_qty)) +
      fact('Rounding delta', signedQty(a.rounding_delta)) +
      fact('Intended notional', usd(a.intended_notional_usd)) +
      fact('Venue order', has(a.venue_order_id) ? '<span class="mono">' + esc(a.venue_order_id) + '</span>' : NA) +
      fact('Submitted price', px(a.submitted_price)) +
      fact('Acknowledged at', when(a.acknowledged_at)) +
      fact('Venue state', txt(a.venue_state)) +
      fact('Filled qty', qty(a.filled_qty)) +
      fact('Avg fill price', px(a.avg_fill_price)) +
      fact('Fees', usd(a.fees_usd, 4)) +
      fact('Submit latency', ms(a.submit_latency_ms)) +
      fact('Decision → venue ack', ms(a.decision_to_accept_ms)) +
      '</div></div>';
  }

  function difference(x) {
    return '<details class="sl-more" open><summary>Difference (actual vs paper)</summary>' +
      (x.why ? '<p class="muted">' + esc(x.why) + '</p>' : '') +
      '<div class="facts three">' +
      fact('Submitted − paper wire price', signedPx(x.submitted_minus_paper_wire_price)) +
      fact('Live − paper fill price', signedPx(x.live_minus_paper_fill_wire_price)) +
      fact('Price, adverse per contract', signedPx(x.price_adverse_per_contract)) +
      fact('Slippage vs submitted (adverse)', signedPx(x.slippage_vs_submitted_adverse_per_contract)) +
      fact('Expected live qty (paper fill ÷ scale)', qty(x.expected_live_qty_from_paper_fill)) +
      fact('Live − expected qty', signedQty(x.live_minus_expected_qty)) +
      fact('Rounded qty', signedQty(x.rounded_qty)) +
      fact('Excluded qty (scaled)', qty(x.excluded_scaled_qty)) +
      fact('Live fees − paper fees ÷ scale', signedUsd(x.live_fees_minus_scaled_paper_fees_usd)) +
      fact('Fee per contract, live − paper', signedUsd(x.fee_per_contract_live_minus_paper_usd)) +
      fact('Paper first fill → live ack', ms(x.paper_first_fill_to_live_ack_ms)) +
      fact('Paper first fill → live first fill', ms(x.paper_first_fill_to_live_first_fill_ms)) +
      '</div><p class="sl-why">' + txt(x.basis) + '</p></details>';
  }

  function management(m) {
    var xp = m.xavier_paper || {};
    var xa = m.xavier_actual || {};
    var rec = m.audrey_reconciliation;
    var f = (m.audrey_findings || []).map(function (a) {
      return '<li>' + pill(a.severity, a.severity === 'CRITICAL' ? 'bad'
        : a.severity === 'WARNING' ? 'warn' : 'grey') + ' ' + esc(a.kind) +
        ' <span class="muted mono">' + esc(a.finding_id) + '</span></li>';
    }).join('');
    return '<details class="sl-more"><summary>Management — Xavier &amp; Audrey</summary>' +
      '<div class="facts three">' +
      fact('Xavier · paper handoff', has(xp.handoff_id) ? '<span class="mono">' + esc(xp.handoff_id) + '</span>'
        : NA + (xp.why_unavailable ? '<span class="sl-why">' + esc(xp.why_unavailable) + '</span>' : '')) +
      fact('Xavier · latest paper recommendation', txt(xp.latest_recommendation) +
        (has(xp.latest_review_at) ? ' · ' + when(xp.latest_review_at) : '')) +
      fact('Xavier · ACTUAL position', xa.present
        ? '<span class="mono">' + esc(xa.handoff_id) + '</span> · ' + txt(xa.state) +
          ' · action ' + txt(xa.latest_action) + ' · unrealized ' + usd(xa.unrealized_usd)
        : NA + '<span class="sl-why">' + txt(xa.why_unavailable) + '</span>') +
      fact('Audrey · chain reconciliation', rec
        ? pill(rec.status, rec.status === 'MATCHED' ? 'good' : rec.status === 'DISCREPANCY' ? 'bad' : 'warn')
        : NA + '<span class="sl-why">' + txt(m.audrey_reconciliation_why_unavailable) + '</span>') +
      '</div>' +
      '<h3 class="h3">Audrey findings for this decision</h3>' +
      (f ? '<ul class="sl-findings">' + f + '</ul>'
        : '<p class="muted">' + txt(m.audrey_findings_why_empty) + '</p>') +
      '</details>';
  }

  function trade(r) {
    var s = r.strategy || {};
    var lab = r.label || {};
    var title = [lab.team, lab.market, lab.line, lab.side].filter(has).join(' · ');
    return '<article class="card sl-trade"><h2>' +
      '<span class="mono">' + esc(r.market) + '</span>' +
      (title ? '<span class="sub">' + esc(title) + '</span>' : '') +
      pill(r.role, 'grey') + pill(s.kind || 'UNKNOWN', s.kind === 'TRAINING' ? 'warn' : 'blue') +
      '<span class="sub">' + esc(r.intent) + ' · ' + when(r.created_at) + '</span></h2>' +
      '<div class="card-body">' + decision(r.decision || {}) +
      '<div class="sl-cols">' + paperCol(r.paper || {}) + actualCol(r.actual || {}) + '</div>' +
      difference(r.difference || {}) + management(r.management || {}) +
      '<details class="audit"><summary>Audit — the full row as read</summary><pre>' +
      json(r) + '</pre></details></div></article>';
  }

  function empty(d) {
    if (state.view === 'kalshi') {
      var k = (d.venues || []).filter(function (v) { return v.venue === 'KALSHI'; })[0] || {};
      return '<div class="verdict"><b>Kalshi is not connected — ' + txt(k.why) + '.</b>' +
        txt(k.detail) + '</div>';
    }
    var e = d.empty_state || {};
    var head = e.live_order_placed === false
      ? esc(e.headline || 'No live order has been placed yet.')
      : 'No order matches this filter.';
    var reasons = (e.live_order_placed === false ? (e.reasons || []) : []).map(function (s) {
      return '<li>' + esc(s) + '</li>';
    }).join('');
    return '<div class="verdict"><b>' + head + '</b>' +
      (reasons ? '<ul class="sl-reasons">' + reasons + '</ul>' : '') + '</div>';
  }

  function render(d) {
    state.data = d;
    var stateEl = document.getElementById('livestate');
    var rowsEl = document.getElementById('liverows');
    document.getElementById('livestrip').innerHTML = d.status === 'UNAVAILABLE' && !d.control
      ? '<div class="verdict"><b>The execution mirror is unavailable</b>' + txt(d.why) + '</div>'
      : strip(d);
    filters(d);
    var rows = d.rows || [];
    var notice = (d.empty_state && d.empty_state.live_order_placed === false && rows.length)
      ? '<div class="verdict"><b>' + esc(d.empty_state.headline) + '</b><ul class="sl-reasons">' +
        (d.empty_state.reasons || []).map(function (s) { return '<li>' + esc(s) + '</li>'; }).join('') +
        '</ul></div>' : '';
    stateEl.innerHTML = rows.length ? notice : empty(d);
    rowsEl.innerHTML = rows.map(trade).join('');
    var asof = document.getElementById('asof');
    if (asof) { asof.textContent = 'Read at ' + new Date().toISOString().replace('.000', ''); }
  }

  function fail(msg) {
    var el = document.getElementById('livestate');
    el.innerHTML = '<div class="verdict"><b>The small-live view could not be read</b>' +
      esc(msg) + '<br>An unread view shows no numbers rather than zeros.</div>';
    document.getElementById('liverows').innerHTML = '';
  }

  function query() {
    var q = [];
    if (VIEWS.indexOf(state.view) >= 0) { q.push('view=' + state.view); }
    if (STATUSES.indexOf(state.status) >= 0) { q.push('status=' + state.status); }
    return q.length ? '?' + q.join('&') : '';
  }

  function syncHash() {
    var q = query().slice(1);
    try { history.replaceState(null, '', q ? '#' + q : location.pathname); } catch (e) { /* ignore */ }
  }

  function readHash() {
    var h = (location.hash || '').slice(1).split('&');
    h.forEach(function (kv) {
      var p = kv.split('=');
      if (p[0] === 'view' && VIEWS.indexOf(p[1]) >= 0) { state.view = p[1]; }
      if (p[0] === 'status' && STATUSES.indexOf(p[1]) >= 0) { state.status = p[1]; }
    });
  }

  function load() {
    var path;
    try { path = C.endpoint(PATH); }
    catch (e) { fail('The small-live path is not an allowed read endpoint.'); return; }
    fetch(path + query(), {credentials: 'same-origin', cache: 'no-store',
                           headers: {Accept: 'application/json'}})
      .then(function (r) {
        if (r.status === 401 || r.status === 403) {
          if (window.BTUnlock && window.BTUnlock.open) { window.BTUnlock.open(); }
          throw new Error('This view needs a Command session (HTTP ' + r.status + ').');
        }
        if (!r.ok) { throw new Error('The small-live read returned HTTP ' + r.status + '.'); }
        return r.json();
      })
      .then(render)
      .catch(function (e) { fail(e && e.message ? e.message : String(e)); });
  }

  function onClick(e) {
    var b = e.target.closest ? e.target.closest('.sl-chip') : null;
    if (!b) { return; }
    if (b.hasAttribute('data-view')) {
      var v = b.getAttribute('data-view');
      state.view = VIEWS.indexOf(v) >= 0 ? v : null;
      if (state.view === 'kalshi') { state.status = null; }
    } else if (b.hasAttribute('data-status')) {
      var s = b.getAttribute('data-status');
      state.status = STATUSES.indexOf(s) >= 0 ? s : null;
    }
    syncHash();
    filters(state.data);
    document.getElementById('livestate').textContent = 'Reading…';
    load();
  }

  function start() {
    if (!C || typeof C.endpoint !== 'function') {
      fail('core.js did not load, so the read guard is absent.');
      return;
    }
    readHash();
    filters(null);
    document.getElementById('livefilters').addEventListener('click', onClick);
    load();
    state.timer = setInterval(load, POLL_MS);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else { start(); }

  window.BettorSmallLive = {load: load, render: render, VERSION: '1.0.0'};
}());
