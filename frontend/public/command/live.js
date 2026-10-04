/* BETTOR EV ENGINE · LEGACY MIRROR VALIDATION — PAPER vs ACTUAL, rendered.
 * (The old 1:1,000 execution mirror: live orders COPIED from paper orders.
 * It is NOT the target SMALL LIVE — BETTOR ORIGINATED system.)
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
  function why(t) { return has(t) ? '<span class="sl-why">' + esc(t) + '</span>' : ''; }
  /* Audrey: NOT_MIRRORED is paper only (nothing sent) -- neutral grey. */
  function recTone(s) {
    return s === 'MATCHED' ? 'good' : s === 'DISCREPANCY' ? 'bad'
      : s === 'NOT_MIRRORED' ? 'grey' : 'warn';
  }
  function evidenceTone(s) {
    return s === 'FRESH_CURRENT_PROBABILITY' ? 'good'
      : s === 'STALE_ENTRY_TIME_PROBABILITY' ? 'warn'
      : s === 'PROBABILITY_UNAVAILABLE' ? 'bad' : 'grey';
  }
  function linkTone(s) {
    return s === 'PRESENT' ? 'good' : s === 'ABSENT' ? 'bad' : 'grey';
  }
  function linkWord(s) {
    return s === 'NOT_APPLICABLE' ? 'N/A' : s;
  }
  function due(n) {
    n = n || {};
    if (!has(n.due_by)) { return NA + why(n.why_unavailable); }
    return when(n.due_by) + (n.overdue_at_read ? ' ' + pill('OVERDUE', 'bad') : '') + why(n.basis);
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
    return '<div class="sl-decision"><h3 class="h3">Decision — PAPER ' + pill('SIMULATED', 'blue') + '</h3>' + src +
      '<div class="facts three">' +
      fact('Decided at', when(d.decided_at)) +
      fact('Decision', has(d.decision_id) ? '<span class="mono">' + esc(d.decision_id) + '</span>' : NA) +
      fact('Strategy / policy', txt(d.strategy) + ' · ' + txt(d.policy_version)) +
      fact('Verdict', has(d.verdict) ? pill(d.verdict, d.verdict === 'ENTER' ? 'good' : 'warn') : NA) +
      fact('Decision probability (blended)', pct(d.p_blended)) +
      fact('Pinnacle probability', pct(d.p_pinnacle)) +
      fact('Research model probability', pct(d.p_internal)) +
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
    var nofill = a.why_no_fill_figures && !a.excluded
      ? '<p class="muted">' + esc(a.why_no_fill_figures) + '</p>' : '';
    var mi = a.mirror_intent || {};
    var pnl = num(a.group_pnl_usd) === null
      ? NA + (a.group_pnl_why_unavailable
        ? '<span class="sl-why">' + esc(a.group_pnl_why_unavailable) + '</span>' : '')
      : usd(a.group_pnl_usd, 4) + ' ' + pill(a.group_pnl_kind === 'REALIZED' ? 'REALIZED'
        : 'UNREALIZED · MARK', a.group_pnl_kind === 'REALIZED' ? 'good' : 'grey') +
        (has(a.group_pnl_as_of) ? '<span class="sl-why">as of ' +
          esc(String(a.group_pnl_as_of).replace('T', ' ').replace(/\.\d+/, '')) + '</span>' : '');
    return '<div class="sl-col sl-actual">' + head + excl + nofill + '<div class="facts">' +
      fact('Retail account', has(a.account_fingerprint_prefix)
        ? '<span class="mono">' + esc(a.account_fingerprint_prefix) + '…</span>' : NA) +
      fact('Mirror intent', has(mi.mirror_id)
        ? '<span class="mono">' + esc(mi.mirror_id) + '</span>' +
          '<span class="sl-why">' + esc([mi.role, mi.intent, mi.order_type, mi.tif].filter(has).join(' · ')) + '</span>'
        : NA) +
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
      fact('Actual P&L (group)', pnl) +
      '</div></div>';
  }

  function difference(x) {
    return '<details class="sl-more" open><summary>Paper vs actual — difference</summary>' +
      (x.why ? '<p class="muted">' + esc(x.why) + '</p>' : '') +
      '<div class="facts three">' +
      fact('Submitted − paper wire price', signedPx(x.submitted_minus_paper_wire_price)) +
      fact('Live − paper fill price', signedPx(x.live_minus_paper_fill_wire_price)) +
      fact('Price, adverse per contract', signedPx(x.price_adverse_per_contract)) +
      fact('Slippage vs submitted (adverse)', signedPx(x.slippage_vs_submitted_adverse_per_contract)) +
      fact('Expected live qty (paper fill ÷ scale)', qty(x.expected_live_qty_from_paper_fill)) +
      fact('Live − expected qty', signedQty(x.live_minus_expected_qty)) +
      fact('Rounding: scaled → live qty', qty(x.scaled_qty) + ' → ' + qty(x.intended_live_qty)) +
      fact('Rounded qty (rounding delta)', signedQty(x.rounded_qty)) +
      fact('Excluded qty (scaled)', qty(x.excluded_scaled_qty)) +
      fact('Live fees − paper fees ÷ scale', signedUsd(x.live_fees_minus_scaled_paper_fees_usd)) +
      fact('Fee per contract, live − paper', signedUsd(x.fee_per_contract_live_minus_paper_usd)) +
      fact('Latency: decision → submit', ms(x.decision_to_submit_ms)) +
      fact('Latency: submit → venue ack', ms(x.submit_to_ack_ms) +
        (num(x.submit_latency_ms_recorded) !== null
          ? '<span class="sl-why">recorded submit latency ' + esc(x.submit_latency_ms_recorded) + ' ms</span>' : '')) +
      fact('Paper first fill → live ack', ms(x.paper_first_fill_to_live_ack_ms)) +
      fact('Paper first fill → live first fill', ms(x.paper_first_fill_to_live_first_fill_ms)) +
      '</div><p class="sl-why">' + txt(x.basis) + '</p></details>';
  }

  function protectionCol(side, p) {
    p = p || {};
    var paper = side === 'paper';
    var head = '<div class="sl-colhead"><span>' + (paper ? 'PAPER POSITION' : 'ACTUAL POSITION') +
      '</span>' + pill(paper ? 'SIMULATED' : 'ACTUAL', paper ? 'blue' : 'good') + '</div>';
    if (p.why_unavailable) {
      return '<div class="sl-col ' + (paper ? 'sl-paper' : 'sl-actual') + '">' + head +
        '<p class="muted">Protection unavailable — ' + esc(p.why_unavailable) + '</p></div>';
    }
    return '<div class="sl-col ' + (paper ? 'sl-paper' : 'sl-actual') + '">' + head + '<div class="facts">' +
      fact(paper ? 'Open qty' : 'Held qty', qty(paper ? p.open_qty : p.held_qty)) +
      (p.position_qty !== undefined ? fact('Position qty (held + sold by filled protection)', qty(p.position_qty)) : '') +
      fact('Standing orders (resting, NOT protection until filled)', qty(p.standing_resting_qty) +
        (num(p.standing_resting_orders) !== null ? '<span class="sl-why">' +
          esc(p.standing_resting_orders) + ' resting order(s)</span>' : '')) +
      fact('Filled protection', qty(p.filled_protection_qty)) +
      fact('Unprotected qty', qty(p.unprotected_qty)) +
      (paper && (p.pending_submission_qty === undefined || p.pending_submission_qty === null) ? '' : fact('Protective orders not yet submitted (NOT protection)', qty(p.pending_submission_qty))) +
      '</div></div>';
  }

  function chain(c) {
    var links = (c && c.links) || [];
    if (!links.length) { return '<p class="muted">' + NA + '</p>'; }
    return '<ol class="sl-chain">' + links.map(function (l) {
      return '<li>' + pill(linkWord(l.state), linkTone(l.state)) + ' <b>' + esc(l.label) + '</b>' +
        (has(l.ref) ? ' <span class="mono muted">' + esc(l.ref) + '</span>' : '') +
        why(l.why) + '</li>';
    }).join('') + '</ol>' + why(c.basis);
  }

  function discrepancies(rec) {
    var ds = (rec && rec.discrepancies) || [];
    if (!rec) { return ''; }
    if (!ds.length) { return '<p class="muted">No discrepancy recorded by Audrey for this group.</p>'; }
    return '<ul class="sl-findings">' + ds.map(function (d) {
      var rest = {};
      Object.keys(d || {}).forEach(function (k) { if (k !== 'code') { rest[k] = d[k]; } });
      return '<li>' + pill(d.code || 'DISCREPANCY', 'bad') +
        (Object.keys(rest).length ? ' <span class="mono muted">' + esc(JSON.stringify(rest)) + '</span>' : '') +
        '</li>';
    }).join('') + '</ul>';
  }

  /* the operating summary: what management checks first on every row */
  function ops(r) {
    var m = r.management || {};
    var xp = m.xavier_paper || {};
    var xa = m.xavier_actual || {};
    var pf = m.probability_freshness || {};
    var pr = m.protection || {};
    var nr = m.next_review || {};
    var rec = m.audrey_reconciliation;
    var c = r.chain || {};
    var total = (c.links || []).length;
    function prot(p) {
      p = p || {};
      if (p.why_unavailable) { return NA; }
      return 'filled ' + qty(p.filled_protection_qty) + ' · unprotected ' + qty(p.unprotected_qty) +
        ' · standing ' + qty(p.standing_resting_qty) + ' <span class="sl-why">(standing is not protection)</span>';
    }
    return '<div class="sl-ops facts three">' +
      fact('Xavier recommendation', '<span class="sl-tag">paper</span> ' + txt(xp.latest_recommendation) +
        '<br><span class="sl-tag">actual</span> ' + (xa.present ? txt(xa.latest_action) : NA)) +
      fact('Probability freshness', has(pf.evidence_state)
        ? pill(pf.evidence_state, evidenceTone(pf.evidence_state))
        : NA + why(pf.why_unavailable)) +
      fact('Protection', '<span class="sl-tag">paper</span> ' + prot(pr.paper) +
        '<br><span class="sl-tag">actual</span> ' + prot(pr.actual)) +
      fact('Next review', '<span class="sl-tag">paper</span> ' + (has((nr.paper || {}).due_by)
        ? when(nr.paper.due_by) + (nr.paper.overdue_at_read ? ' ' + pill('OVERDUE', 'bad') : '') : NA) +
        '<br><span class="sl-tag">actual</span> ' + (has((nr.actual || {}).due_by)
        ? when(nr.actual.due_by) + (nr.actual.overdue_at_read ? ' ' + pill('OVERDUE', 'bad') : '') : NA)) +
      fact('Audrey reconciliation', rec ? pill(rec.status, recTone(rec.status)) +
        (rec.discrepancies && rec.discrepancies.length
          ? ' <span class="bad">' + esc(rec.discrepancies.length) + ' discrepanc' +
            (rec.discrepancies.length === 1 ? 'y' : 'ies') + '</span>' : '')
        : NA) +
      fact('Paper / live chain', total
        ? pill(c.complete ? 'COMPLETE' : 'INCOMPLETE', c.complete ? 'good' : 'warn') + ' ' +
          esc((c.present_count || 0) + ' present · ' + (c.absent || []).length + ' absent · ' +
            (c.not_applicable_count || 0) + ' n/a')
        : NA) +
      '</div>';
  }

  function management(m) {
    var xp = m.xavier_paper || {};
    var xa = m.xavier_actual || {};
    var rec = m.audrey_reconciliation;
    var pf = m.probability_freshness || {};
    var pr = m.protection || {};
    var nr = m.next_review || {};
    var f = (m.audrey_findings || []).map(function (a) {
      return '<li>' + pill(a.severity, a.severity === 'CRITICAL' ? 'bad'
        : a.severity === 'WARNING' ? 'warn' : 'grey') + ' ' + esc(a.kind) +
        ' <span class="muted mono">' + esc(a.finding_id) + '</span></li>';
    }).join('');
    return '<details class="sl-more" open><summary>Management — Xavier &amp; Audrey</summary>' +
      '<div class="facts three">' +
      fact('Xavier · paper handoff', has(xp.handoff_id) ? '<span class="mono">' + esc(xp.handoff_id) + '</span>'
        : NA + (xp.why_unavailable ? '<span class="sl-why">' + esc(xp.why_unavailable) + '</span>' : '')) +
      fact('Xavier · latest paper recommendation', txt(xp.latest_recommendation) +
        (has(xp.latest_review_at) ? ' · ' + when(xp.latest_review_at) : '')) +
      fact('Xavier · ACTUAL position', xa.present
        ? '<span class="mono">' + esc(xa.handoff_id) + '</span> · ' + txt(xa.state) +
          ' · action ' + txt(xa.latest_action) + ' · unrealized ' + usd(xa.unrealized_usd)
        : NA + '<span class="sl-why">' + txt(xa.why_unavailable) + '</span>') +
      fact('Xavier · paper recommendation the actual follows', txt(xa.paper_recommendation_followed)) +
      fact('Probability evidence state', has(pf.evidence_state)
        ? pill(pf.evidence_state, evidenceTone(pf.evidence_state)) + why('read from ' + pf.evidence_state_source)
        : NA + why(pf.why_unavailable)) +
      fact('Review measure (as recorded)', has(pf.measure_source)
        ? esc(pf.measure_source) + (pf.measure_stale === true ? ' ' + pill('STALE', 'warn')
          : pf.measure_stale === false ? ' ' + pill('CURRENT', 'good') : '') +
          ' · p ' + pct(pf.measure_p) + why(pf.measure_why)
        : NA) +
      fact('Next review · paper', due(nr.paper)) +
      fact('Next review · actual', due(nr.actual)) +
      fact('Audrey · chain reconciliation', rec
        ? pill(rec.status, recTone(rec.status)) + why(rec.meaning) +
          (has(rec.reconciled_at) ? '<span class="sl-why">reconciled ' + esc(String(rec.reconciled_at).replace('T', ' ').replace(/\.\d+/, '')) + '</span>' : '')
        : NA + '<span class="sl-why">' + txt(m.audrey_reconciliation_why_unavailable) + '</span>') +
      '</div>' +
      '<h3 class="h3">Protection — standing (resting) is not filled</h3>' +
      '<div class="sl-cols">' + protectionCol('paper', pr.paper) + protectionCol('actual', pr.actual) + '</div>' +
      why(pr.rule) +
      '<h3 class="h3">Audrey discrepancies</h3>' +
      (rec ? discrepancies(rec) : '<p class="muted">' + NA + why(m.audrey_reconciliation_why_unavailable) + '</p>') +
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
      '<div class="card-body">' + ops(r) + decision(r.decision || {}) +
      '<div class="sl-cols">' + paperCol(r.paper || {}) + actualCol(r.actual || {}) + '</div>' +
      difference(r.difference || {}) + management(r.management || {}) +
      '<details class="sl-more"><summary>Audit — paper / live chain completeness</summary>' +
      chain(r.chain) + '</details>' +
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

  /* ── ONE DECISION -> PAPER + ACTUAL (siblings, never parent and child) ── */
  var LEGS = [['receipt_to_decision_ms', 'PinnAPI receipt → Derek decision'],
              ['decision_to_intent_ms', 'Decision → intent'],
              ['intent_to_submit_ms', 'Intent → actual submit'],
              ['submit_to_ack_ms', 'Submit → venue ack'],
              ['ack_to_first_fill_ms', 'Ack → first actual fill']];
  function latencyStrip(st) {
    st = st || {};
    return '<div class="sl-lat">' + LEGS.map(function (l) {
      var s = st[l[0]] || {};
      return '<div class="sl-latcell"><span class="k">' + esc(l[1]) + '</span>' +
        '<span class="v">p50 ' + ms(s.p50) + ' · p95 ' + ms(s.p95) + ' · p99 ' + ms(s.p99) +
        '</span><span class="sl-why">n = ' + esc(String(s.n || 0)) + '</span></div>';
    }).join('') + '</div>';
  }
  function actualTone(s) {
    return s === 'SUBMITTED' ? 'good' : s === 'REFUSED' || s === 'REJECTED' ? 'bad'
      : s === 'PAPER_ONLY' ? 'grey' : 'warn';
  }
  function admTone(s) {
    return s === 'LIVE_ADMISSIBLE' ? 'good' : s === 'NOT_ADMISSIBLE' ? 'bad' : 'grey';
  }
  function admission(x) {
    x = x || {};
    var extra = has(x.verdict) ? ' ' + why('verdict ' + x.verdict + (has(x.rule) ? ' · rule ' + x.rule : '')) : '';
    if (has(x.compatibility) || x.compatibility === null) {
      extra = ' ' + why('compatibility ' + (x.compatibility || 'unknown') +
        (has(x.research_disclosure) ? ' · ' + x.research_disclosure + ' (paper research only)' : ''));
    }
    return pill(x.status || 'UNAVAILABLE', admTone(x.status)) + extra + why(x.why);
  }
  function freshness(d) {
    if (d.probability_fresh === null || d.probability_fresh === undefined) { return NA; }
    return pill(d.probability_fresh ? 'FRESH' : 'STALE', d.probability_fresh ? 'good' : 'warn') +
      ' ' + why('age ' + (num(d.probability_age_s) === null ? 'unavailable'
        : num(d.probability_age_s).toFixed(1) + ' s') + ' · limit ' +
        (num(d.probability_age_limit_s) === null ? 'unavailable'
          : num(d.probability_age_limit_s).toFixed(0) + ' s'));
  }
  function decisionCard(r) {
    var d = r.decision || {}, p = r.simulated || {}, a = r.actual || {}, l = r.latency_ms || {};
    var pl = r.pnl || {}, m = r.management || {}, au = r.audit || {}, ad = a.admission || {};
    var sp = m.standing_protection || {}, fp = m.filled_protection || {};
    return '<article class="sl-dec">' +
      '<header class="sl-dechead"><b>DECISION</b> <span class="mono">' + txt(d.decision_id) +
      '</span> ' + pill(d.policy_version || 'unknown policy', 'blue') +
      '<div class="sl-decfacts">' + fact('Market', txt(d.market)) +
      fact('Strategy', txt(d.strategy)) +
      fact('PinnAPI probability', pct(d.probability)) +
      fact('Evidence', txt(d.probability_evidence)) +
      fact('Evidence freshness', freshness(d)) +
      fact('Authority', txt(d.probability_authority)) + fact('Expected edge', pp(d.gross_edge_pp)) +
      fact('Net EV after fees', usd(d.net_expected_profit_usd)) +
      fact('Settlement admission', admission(d.settlement_admission)) +
      fact('Book-currency admission', admission(d.book_currency_admission)) +
      fact('Receipt → decision', ms(l.receipt_to_decision_ms)) +
      fact('Decision → intent', ms(l.decision_to_intent_ms)) +
      fact('Intent → actual submit', ms(l.intent_to_submit_ms)) + '</div></header>' +
      '<div class="sl-decbranches">' +
      '<div class="sl-branch"><h4>' + pill('SIMULATED', 'grey') + ' Paper</h4>' +
      fact('Paper quantity', qty(p.target_qty)) + fact('Paper order', txt(p.paper_order_id)) +
      fact('State', txt(p.state)) + fact('Filled', qty(p.filled_qty)) +
      fact('Avg price', px(p.avg_fill_price)) + fact('Fees', usd(p.fees_usd)) +
      fact('P&L (simulated)', num(pl.simulated_realized_usd) === null
        ? NA + why(pl.simulated_why_unavailable) : usd(pl.simulated_realized_usd)) +
      why(p.why_unavailable) + '</div>' +
      '<div class="sl-branch"><h4>' + pill('ACTUAL', 'good') + ' Polymarket</h4>' +
      fact('1:1,000 target', qty(a.target_raw_qty)) + fact('Rounded quantity', txt(a.rounded_qty)) +
      fact('Admission', pill(ad.status || 'UNAVAILABLE', admTone(ad.status)) +
           why(ad.why_unavailable)) +
      fact('Lane', pill(a.state || 'unknown', actualTone(a.state)) +
           (a.refusal ? ' ' + why(a.refusal) : '')) +
      fact('Submitted', when(a.submitted_at)) + fact('Venue order ID', txt(a.venue_order_id)) +
      fact('Acknowledged', when(a.acknowledged_at)) + fact('Filled', qty(a.filled_qty)) +
      fact('Avg price', px(a.avg_fill_price)) + fact('Fees', usd(a.fees_usd)) +
      fact('P&L (actual, realized)', num(pl.actual_realized_usd) === null
        ? NA + why(pl.actual_why_unavailable) : usd(pl.actual_realized_usd)) +
      fact('Unrealized (actual)', usd(pl.actual_unrealized_usd)) +
      fact('Submit → ack', ms(l.submit_to_ack_ms)) +
      fact('Ack → first fill', ms(l.ack_to_first_fill_ms)) +
      fact('Retail account', txt(a.account_fingerprint_prefix)) + '</div>' +
      '</div>' +
      '<details class="sl-more"><summary>Management — Xavier · Audit — Audrey</summary>' +
      '<div class="facts">' +
      fact('Xavier recommendation', txt(m.xavier_recommendation)) +
      fact('Xavier actual action', txt(m.xavier_actual_action)) +
      fact('Probability freshness', has(m.probability_evidence_state)
        ? pill(m.probability_evidence_state, evidenceTone(m.probability_evidence_state)) +
          why(m.probability_limitation) : NA) +
      fact('Alternatives', has(m.alternatives) ? '<pre class="mono">' + json(m.alternatives) + '</pre>' : NA) +
      fact('Standing orders (resting, NOT protection until filled)', 'paper ' + qty(sp.paper_resting_qty) +
           ' · actual ' + qty(sp.actual_resting_qty)) +
      fact('Filled protection', 'paper ' + qty(fp.paper_filled_qty) +
           ' · actual ' + qty(fp.actual_filled_qty)) +
      fact('Next review', when(m.next_review_by)) +
      fact('Audrey reconciliation', has(au.audrey_status)
        ? pill(au.audrey_status, recTone(au.audrey_status)) + why(au.meaning)
        : NA + why(au.why_unavailable)) +
      fact('Discrepancies', has(au.discrepancies) && au.discrepancies.length
        ? '<pre class="mono">' + json(au.discrepancies) + '</pre>'
        : has(au.audrey_status) ? esc('none') : NA) +
      fact('Paper vs actual price', signedPx(au.paper_vs_actual_price_diff)) +
      '</div>' + why(m.why_unavailable) + '<p class="sl-why">' + esc(m.protection_rule || '') +
      '</p></details></article>';
  }
  function launchBlock(L) {
    if (!L) { return ''; }
    var lane = L.actual_lane || {}, bc = L.book_currency || {}, xp = L.xavier_management_policy || {};
    var md = L.market_data || {}, st = md.institutional_stream || {};
    var ready = L.actual_orders_possible_now === true;
    var why = (L.why_not || []).map(function (w) { return '<li>' + esc(w) + '</li>'; }).join('');
    var blockers = (L.intents_last_24h || []).map(function (b) {
      return '<li>' + esc(b.actual_state) + (b.refusal ? ' · ' + esc(b.refusal) : '') +
        ' — ' + esc(String(b.n)) + '</li>';
    }).join('');
    return '<h2 class="sl-h2">Launch control</h2>' +
      '<div class="verdict"><b>' + (ready ? 'Actual orders are possible now.'
        : 'No actual order can be sent now.') + '</b>' +
      (why ? '<ul class="sl-reasons">' + why + '</ul>' : '') + '</div>' +
      '<div class="facts">' +
      fact('Serving build', has(L.serving_build) ? '<span class="mono">' + esc(String(L.serving_build).slice(0, 12)) + '</span>'
        : NA + why_(L.serving_build_why_unavailable)) +
      fact('Actual lane', pill(lane.state || 'unknown', lane.state === 'ACTIVE' ? 'good' : 'warn')) +
      fact('Scale', has(lane.scale) ? esc('1 : ' + Number(lane.scale).toLocaleString('en-US')) : NA) +
      fact('Cap per order', usd(lane.max_order_usd)) +
      fact('Retail account', txt(lane.account_fingerprint_prefix)) +
      fact('Approved live book rules', (bc.approved_live_rules || []).length
        ? esc(bc.approved_live_rules.join(', ')) : pill('NONE', 'bad') + why_(bc.why)) +
      fact('Xavier management policy', pill(xp.status || 'unavailable',
        xp.status === 'APPROVED' ? 'good' : 'warn') + why_(xp.sha256 ? 'sha256 ' + String(xp.sha256).slice(0, 16) : xp.why)) +
      fact('Market data (this process)', txt(md.verdict)) +
      fact('Institutional stream', txt(st.state) + why_(st.why)) +
      '</div>' +
      (blockers ? '<details class="sl-more"><summary>Execution intents, last 24 h</summary><ul>' +
        blockers + '</ul></details>' : '');
  }
  function why_(t) { return why(t); }
  function decisionsBlock(ds) {
    if (!ds || ds.status !== 'OK') {
      return '<div class="verdict"><b>One decision → paper + actual</b>' +
        txt(ds && ds.why) + '</div>';
    }
    var rows = ds.rows || [];
    return '<h2 class="sl-h2">One decision → paper + actual</h2>' +
      '<p class="sl-why">' + esc(ds.basis || '') + '</p>' + latencyStrip(ds.latency_ms) +
      (rows.length ? rows.map(decisionCard).join('')
        : '<div class="verdict"><b>No qualified investment decision yet.</b>' +
          ' Every decision appears here with its simulated and actual branches.</div>');
  }

  function render(d) {
    state.data = d;
    var decEl = document.getElementById('livedecisions');
    if (decEl) { decEl.innerHTML = launchBlock(d.launch) + decisionsBlock(d.decisions); }
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
    el.innerHTML = '<div class="verdict"><b>The legacy mirror view could not be read</b>' +
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

  window.BettorSmallLive = {load: load, render: render, VERSION: '1.1.0'};
}());
