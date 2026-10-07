/* BETTOR Red Team & PM Acceptance -- read-only view of /api/command/red-team
 * and /api/command/pm-acceptance. No demo fallback and no fake green: an
 * unmeasured value is UNMEASURED, a field without machine evidence is listed
 * as such, and a failed read of EITHER endpoint clears every section. The
 * page never writes and can grant no authority. */
(function () {
  'use strict';
  var RED_URL = '/api/command/red-team';
  var PM_URL = '/api/command/pm-acceptance';
  var SECTIONS = ['rt-verdict', 'rt-gates', 'rt-controls', 'rt-golden', 'rt-board', 'rt-release', 'rt-foot'];
  /* the frozen acceptance_spec.json gate classes (display grouping only) */
  var CRITICAL = ['release_lineage', 'exact_sha', 'backend_tests', 'capital_critical', 'commit_guard',
    'engine_diagnostic', 'migration_integrity', 'no_oom', 'shared_worker_ump_isolated', 'held_freshness',
    'priority_freshness', 'truth_quorum', 'software_red_zero', 'canary', 'credential_classes',
    'historical_paper_immutable', 'live_authority_shadow'];
  var ECONOMIC = ['pmx_grpc_primary', 'kalshi_market_data_live', 'venue_health_isolated', 'settlement_proven',
    'digital_twin_certified', 'xavier_complete', 'positive_forward_edge', 'positive_capacity',
    'profitability_governor_positive', 'mechanism_breakers_green'];

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  function $(id) { return document.getElementById(id); }
  function v(x, d) {
    if (x == null || (typeof x === 'number' && isNaN(x))) return '<span class="cr-unk">UNMEASURED</span>';
    if (typeof x === 'number') return esc(d == null ? String(x) : x.toFixed(d));
    if (typeof x === 'boolean') return x ? '<span class="cr-ok">true</span>' : '<span class="cr-bad">false</span>';
    if (typeof x === 'object') return '<code class="rt-prov">' + esc(JSON.stringify(x).slice(0, 240)) + '</code>';
    return esc(x);
  }
  function usd(x) { return x == null || isNaN(+x) ? v(null) : esc('$' + (+x).toFixed(2)); }
  function st(s) {
    var c = s === 'GREEN' ? 'cr-ok' : (s === 'RED' ? 'cr-bad' : 'cr-unk');
    return '<span class="' + c + '">' + esc(s || 'UNKNOWN') + '</span>';
  }
  function chips(list) {
    return (list && list.length) ? '<div class="cr-blockers">' + list.map(function (b) {
      return '<code>' + esc(b) + '</code>';
    }).join('') + '</div>' : '<span class="cr-ok">none</span>';
  }
  function kv(rows) {
    return '<dl class="cr-kv">' + rows.map(function (r) { return '<dt>' + esc(r[0]) + '</dt><dd>' + r[1] + '</dd>'; }).join('') + '</dl>';
  }
  function card(t, rows) { return '<div class="cr-card"><h3>' + esc(t) + '</h3>' + kv(rows) + '</div>'; }
  function table(head, rows) {
    return '<div class="rv-table-wrap"><table class="rv-table"><thead><tr>' + head.map(function (h) {
      return '<th>' + esc(h) + '</th>';
    }).join('') + '</tr></thead><tbody>' + rows.join('') + '</tbody></table></div>';
  }

  function fail(msg) {
    var e = $('rt-error');
    e.hidden = false;
    e.textContent = msg;
    $('rt-root').setAttribute('data-state', 'unavailable');
    SECTIONS.forEach(function (id) { $(id).innerHTML = ''; });
    var s = $('rt-status');
    s.className = 'rv-status bad';
    s.textContent = 'UNAVAILABLE';
  }

  function verdict(red, pm) {
    var a = pm.pm_acceptance || {};
    var r = red.readiness || {};
    var au = pm.authority || {};
    var s = $('rt-status');
    s.className = 'rv-status ' + (a.pm_state === 'GREEN' ? 'ready' : (a.pm_state === 'RED' ? 'bad' : 'cash'));
    s.textContent = 'PM ' + (a.pm_state || 'UNKNOWN') + ' · ' + (a.capital_status || 'PAPER_SHADOW_ONLY');
    $('rt-verdict').innerHTML = '<h2>Verdict</h2><div class="cr-grid">'
      + '<div class="cr-card"><h3>PM acceptance (machine evidence)</h3><div class="rt-state rt-' + esc(a.pm_state) + '">'
      + esc(a.pm_state || 'UNKNOWN') + '</div>' + kv([['capital status', v(a.capital_status)],
        ['critical failures', v((a.critical_failures || []).length)],
        ['economic / evidence gaps', v((a.economic_or_evidence_gaps || []).length)],
        ['activates money', v(a.activates_money)]]) + '</div>'
      + '<div class="cr-card"><h3>Red-team readiness interlock</h3><div class="rt-state rt-'
      + esc(r.status === 'PAPER_SHADOW_ONLY' ? 'YELLOW' : 'GREEN') + '">' + esc(r.status || 'UNKNOWN') + '</div>'
      + kv([['auto activation', v(r.auto_activation)], ['implementation sha', v((r.implementation_sha || '').slice(0, 12) || null)]])
      + chips(r.blockers) + '</div></div>'
      + '<div class="rt-auth"><span>SMALL LIVE = ' + esc(au.small_live) + '</span><span>KALSHI LIVE MONEY = '
      + esc(au.kalshi_live_money) + '</span><span>ADRIANA = ' + esc(au.adriana) + '</span><span>HISTORICAL PAPER = '
      + esc(au.historical_paper) + '</span><span>AUTHORITY EXPANDED = ' + esc(String(au.authority_expanded)) + '</span></div>';
  }

  function gates(pm) {
    var a = pm.pm_acceptance || {};
    var g = a.gates || {};
    function rows(names, cls) {
      return names.map(function (n) {
        var x = g[n] || {};
        return '<tr class="' + (x.pass ? 'rt-pass' : 'rt-fail') + '"><td>' + esc(n) + '</td><td>'
          + (x.pass ? 'PASS' : 'FAIL') + '</td><td>' + esc(cls) + '</td><td class="rv-why">' + esc(x.why || '') + '</td></tr>';
      });
    }
    var prov = a.provenance || {};
    var ev = a.evidence_input || {};
    var pr = Object.keys(prov).sort().map(function (k) {
      return '<tr><td>' + esc(k) + '</td><td>' + v(ev[k]) + '</td><td class="rt-prov">' + esc(prov[k].source) + '</td></tr>';
    });
    var missing = a.fields_without_machine_evidence || [];
    $('rt-gates').innerHTML = '<h2>PM acceptance gates</h2><p class="rv-note">RED = a critical software, integrity, '
      + 'runtime, freshness, lineage or safety gate fails. YELLOW = software acceptable, economic proof missing. GREEN only '
      + 'when every gate passes, and even then live activation needs separate explicit approval.</p>'
      + table(['GATE', 'RESULT', 'CLASS', 'WHY'], rows(CRITICAL, 'CRITICAL').concat(rows(ECONOMIC, 'ECONOMIC')))
      + '<h3>Fields without machine evidence</h3>' + (missing.length ? chips(missing) : '<span class="cr-ok">none</span>')
      + '<h3>Evidence and its source</h3>' + table(['FIELD', 'VALUE', 'SOURCE'], pr);
  }

  function controls(red) {
    var c = (red.readiness || {}).controls || {};
    var chk = (red.readiness || {}).checks || {};
    var rows = Object.keys(c).sort().map(function (k) {
      return '<tr><td>' + esc(k) + '</td><td>' + st(c[k].status) + '</td><td>' + chips(c[k].blockers) + '</td></tr>';
    });
    var crow = Object.keys(chk).map(function (k) {
      return '<tr><td>' + esc(k) + '</td><td>' + v(chk[k]) + '</td></tr>';
    });
    var rc = (red.receipts || {}).counts || {};
    $('rt-controls').innerHTML = '<h2>Red-team controls</h2><p class="rv-note">Each control is bound into BETTOR and '
      + 'judged from runtime evidence; venue health is one entry per venue, never blended; connected is not fresh.</p>'
      + table(['CONTROL', 'STATUS', 'BLOCKERS'], rows)
      + '<h3>Readiness interlock checks</h3>' + table(['CHECK', 'PASS'], crow)
      + '<p class="rv-note">Append-only receipts: ' + Object.keys(rc).map(function (k) {
        return esc(k.replace('red_team_', '')) + ' ' + v(rc[k]);
      }).join(' · ') + '</p>';
  }

  function golden(pm) {
    var g = pm.golden || {};
    var rows = (g.cases || []).map(function (c) {
      return '<tr class="' + (c.pass ? 'rt-pass' : 'rt-fail') + '"><td>' + esc(c.id) + '</td><td>' + esc(c.result)
        + '</td><td>' + esc(c.source_kind || '') + '</td><td>' + v(c.production) + '</td></tr>';
    });
    var app = (g.appended_source_records || []).map(function (c) {
      return '<tr class="' + (c.pass ? 'rt-pass' : 'rt-fail') + '"><td>' + esc(c.id) + '</td><td>' + esc(c.result)
        + '</td><td>' + esc(c.source || '') + '</td><td>' + v(c.production) + '</td></tr>';
    });
    $('rt-golden').innerHTML = '<h2>Golden validation · production code</h2><p class="rv-note">' + v(g.passed) + ' / '
      + v(g.total) + ' frozen cases and ' + v(g.appended_passed) + ' / ' + v(g.appended_total) + ' appended source records '
      + 'through ' + esc(g.path || '') + '. Kalshi cases stay FROZEN_INTEGRATION_CASE.</p>'
      + table(['CASE', 'RESULT', 'SOURCE', 'PRODUCTION'], rows.concat(app));
  }

  function board(pm) {
    var b = pm.scoreboard || {};
    var m = b.mechanisms || {}, ms = b.mechanism_states || {};
    var rows = Object.keys(m).map(function (k) {
      var x = m[k] || {}, s = ms[k] || {};
      return '<tr><td>' + esc(k) + '</td><td>' + v(x.independent_events) + '</td><td>' + usd(x.expected_pnl) + '</td><td>'
        + usd(x.realized_pnl) + '</td><td>' + (x.forward_pnl_lcb_per_event == null ? v(null) : usd(x.forward_pnl_lcb_per_event))
        + '</td><td>' + esc(s.status || '') + '</td><td>' + chips(s.blockers) + '</td></tr>';
    });
    var r = b.routing || {}, rc = b.routing_counts || {}, rec = b.reconciliation || {}, lr = b.ledger_reconciliation || {};
    $('rt-board').innerHTML = '<h2>Forward scoreboard (thresholds ' + esc(b.thresholds_version) + ')</h2><p class="rv-note">'
      + 'A read layer over the PAPER ledger, never a second ledger. One value per independent event. Theoretical arbitrage '
      + 'is not realized P&amp;L; route savings are counterfactual.</p>'
      + table(['MECHANISM', 'INDEPENDENT EVENTS', 'EXPECTED', 'REALIZED', 'LOWER BOUND / EVENT', 'STATE', 'BLOCKERS'], rows)
      + '<div class="cr-grid">'
      + card('Routing savings', [['decisions', v(r.decisions)], ['comparable', v(r.comparable_decisions)],
        ['savings vs next best', usd(r.total_savings_vs_next_best)], ['Kalshi NO beat Kalshi YES', v(rc.kalshi_no_beat_kalshi_yes)],
        ['Kalshi beat PMUS', v(rc.kalshi_beat_pmus)], ['PMUS beat Kalshi', v(rc.pmus_beat_kalshi)]])
      + card('Reconciliation to the PAPER ledger', [['attribution identity', v(rec.green)], ['identity residual', usd(rec.residual)],
        ['ledger groups reconciled', v(lr.groups_reconciled) + ' / ' + v(lr.groups_counted)],
        ['groups still open', v(lr.groups_still_open_in_ledger)], ['ledger residual', usd(lr.residual_usd)],
        ['ledger reconciliation', v(lr.green)], ['PAPER ledger realized (all)', usd(b.paper_ledger_realized_total_usd)]])
      + '</div>';
  }

  function release(pm) {
    var r = pm.release_receipt;
    $('rt-release').innerHTML = '<h2>Release receipt</h2>' + (r ? kv([
      ['accepted base', v(r.accepted_base_sha)], ['tested', v(r.tested_sha)], ['release', v(r.release_sha)],
      ['deployed', v(r.deployed_sha)], ['descendant of base', v(r.descendant_of_base)],
      ['backend-tests', v(r.backend_tests_green)], ['capital-critical', v(r.capital_critical_green)],
      ['commit-guard', v(r.commit_guard_green)], ['engine-diagnostic', v(r.engine_diagnostic_green)],
      ['migration fingerprint', v(r.migration_fingerprint_match)]]) + chips(r.blockers)
      : '<p class="rv-note"><span class="cr-bad">NO RELEASE RECEIPT FOR THE SERVING SHA</span> · the pm-acceptance '
        + 'workflow posts it after reading the exact-SHA gates from GitHub.</p>');
  }

  function getJSON(url) {
    return fetch(url, {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}})
      .then(function (res) {
        if (res.status === 401 || res.status === 403) throw new Error('SIGN-IN REQUIRED: sign in on Command to read acceptance.');
        if (res.status === 404) throw new Error('NOT YET RELEASED: ' + url + ' is not deployed.');
        if (!res.ok) throw new Error('UNAVAILABLE: HTTP ' + res.status + ' from ' + url);
        return res.json();
      })
      .then(function (env) {
        if (!env || env.status !== 'OK' || !env.data) throw new Error('UNAVAILABLE: ' + ((env && env.why) || 'no data') + ' (' + url + ')');
        return env.data;
      });
  }

  function load() {
    $('rt-error').hidden = true;
    $('rt-root').setAttribute('data-state', 'loading');
    Promise.all([getJSON(RED_URL), getJSON(PM_URL)])
      .then(function (both) {
        var red = both[0], pm = both[1];
        verdict(red, pm); gates(pm); controls(red); golden(pm); board(pm); release(pm);
        var r = red.readiness || {};
        $('rt-foot').innerHTML = esc(r.version) + ' · ' + esc((pm.pm_acceptance || {}).version) + ' · read only · '
          + 'no order, cancel, sizing, funding or authority path';
        $('rt-root').setAttribute('data-state', 'ready');
      })
      .catch(function (e) { fail(e.message || String(e)); });
  }

  document.addEventListener('DOMContentLoaded', function () {
    $('rt-refresh').addEventListener('click', load);
    load();
  });
}());
