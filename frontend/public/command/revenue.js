/* BETTOR Revenue Readiness -- read-only view of /api/command/revenue-readiness.
 * No demo fallback: a failed read says so and shows nothing else. No writes. */
(function () {
  'use strict';
  var URL_ = '/api/command/revenue-readiness';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  function usd(x) {
    if (x == null || isNaN(+x)) return '—';
    var v = +x, s = Math.abs(v) >= 1000 ? Math.round(Math.abs(v)).toLocaleString('en-US')
      : Math.abs(v).toFixed(2);
    return (v < 0 ? '−$' : '$') + s;
  }
  function num(x, d) { return x == null || isNaN(+x) ? '—' : (+x).toFixed(d == null ? 2 : d); }
  function $(id) { return document.getElementById(id); }

  function fail(msg) {
    var e = $('rv-error');
    e.hidden = false;
    e.textContent = msg;
    $('rv-root').setAttribute('data-state', 'unavailable');
    ['rv-plan', 'rv-cash', 'rv-agents', 'rv-tournament', 'rv-regimes', 'rv-waterfall', 'rv-foot']
      .forEach(function (id) { $(id).innerHTML = ''; });
    var st = $('rv-status');
    st.className = 'rv-status bad';
    st.textContent = 'UNAVAILABLE';
  }

  function plan(d) {
    var r = d.daily_revenue_readiness;
    var st = $('rv-status');
    st.className = 'rv-status ' + (r.overall_status === 'CASH' ? 'cash' : 'ready');
    st.textContent = r.overall_status === 'CASH' ? 'CASH · nothing proven' : 'READY · proven capacity';
    var m = [
      ['Management bankroll', usd(r.management_bankroll_usd), r.bankroll_basis || ''],
      ['Proven positive capacity', usd(r.proven_positive_capacity_usd), 'demonstrated, never extrapolated'],
      ['Planned deployment', usd(r.planned_capital_usd), 'eligible + positive + capacity-proven only'],
      ['CASH', usd(r.cash_usd), 'everything else'],
      ['Expected daily net P&L', usd(r.expected_daily_profit_usd),
        r.expected_range_usd ? 'range ' + usd(r.expected_range_usd[0]) + ' – ' + usd(r.expected_range_usd[1]) : 'range UNMEASURED'],
      ['Earned capital today', Array.isArray(r.what_has_earned_capital_today)
        ? esc(r.what_has_earned_capital_today.join(', ')) : esc(r.what_has_earned_capital_today), '']
    ];
    $('rv-plan').innerHTML = '<h2>Daily revenue plan</h2><p class="rv-note">As of ' + esc(r.as_of)
      + '. Turnover is never a target: positive executable capacity is a ceiling.</p><div class="rv-metrics">'
      + m.map(function (x) {
        return '<div class="rv-metric"><small>' + x[0] + '</small><b>' + x[1] + '</b><span>' + esc(x[2]) + '</span></div>';
      }).join('') + '</div>';
    var reasons = r.reasons_unused_capital_is_cash || [];
    $('rv-cash').innerHTML = '<h2>Why unused capital remains CASH</h2><p class="rv-note">Active strategies: '
      + esc((r.active_strategies || []).join(', ') || 'none') + ' · disabled: '
      + esc((r.disabled_strategies || []).join(', ') || 'none') + ' · regimes eligible: '
      + esc((r.positive_capacity_regimes || []).length) + ', abstained: ' + esc(r.abstained_regimes) + '</p>'
      + (reasons.length ? '<ul class="rv-reasons">' + reasons.map(function (x) {
        return '<li><code>' + esc(x) + '</code></li>';
      }).join('') + '</ul>' : '<p class="rv-note">No blocker recorded.</p>');
  }

  function agents(d) {
    var a = d.agent_scoreboard || {};
    var rows = Object.keys(a).map(function (k) {
      var x = a[k];
      return '<tr><td><b>' + esc(x.display_name) + '</b><br><small>' + esc(x.role) + '</small></td>'
        + '<td><span class="rv-badge rv-' + esc(x.license) + '">' + esc(x.license) + '</span></td>'
        + '<td class="rv-why">' + esc(x.current_authority) + '</td>'
        + '<td>' + esc(x.independent_events) + '</td>'
        + '<td>' + usd(x.expected_contribution_usd) + '</td>'
        + '<td>' + usd(x.realized_contribution_usd) + '</td>'
        + '<td>' + (x.lower_bound_incremental_value_per_event_usd == null ? 'UNMEASURED'
          : usd(x.lower_bound_incremental_value_per_event_usd)) + '</td>'
        + '<td class="rv-why">' + esc(x.exact_blocker || '—') + '</td></tr>';
    }).join('');
    $('rv-agents').innerHTML = '<h2>Agent scoreboard</h2><p class="rv-note">Economic certification only: it never '
      + 'grants or changes authority. $ contribution is measured against each agent\'s predeclared baseline.</p>'
      + '<div class="rv-table-wrap"><table class="rv-table"><thead><tr><th>AGENT</th><th>CERTIFICATION</th>'
      + '<th>AUTHORITY (UNCHANGED)</th><th>EVENTS</th><th>EXPECTED</th><th>REALIZED $ CONTRIBUTION</th>'
      + '<th>LOWER BOUND / EVENT</th><th>EXACT BLOCKER</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
  }

  function tournament(d) {
    var t = d.strategy_tournament || {};
    var s = d.strategies || {};
    var rows = '<tr><td><span class="rv-badge rv-CASH-champ">CASH</span></td><td>INCUMBENT'
      + (t.selected === 'CASH' ? ' · CHAMPION' : '') + '</td><td>—</td><td>—</td><td>—</td><td class="rv-why">'
      + esc(t.selected === 'CASH' ? t.reason : 'challenger selected: ' + t.selected) + '</td></tr>'
      + (t.rankings || []).map(function (r) {
        var x = s[r.name] || {};
        return '<tr><td><b>' + esc(r.name) + '</b></td><td>' + esc(x.lifecycle || '') + (t.selected === r.name
          ? ' · CHAMPION' : r.eligible ? ' · CHALLENGER' : '') + '</td><td>' + esc(r.independent_events)
          + '</td><td>' + usd(r.mean_net_per_event) + '</td><td>' + usd(r.lower_bound_net_per_event)
          + '</td><td class="rv-why">' + esc((r.blockers || []).join('; ') || 'ELIGIBLE') + '</td></tr>';
      }).join('');
    $('rv-tournament').innerHTML = '<h2>Strategy tournament</h2><p class="rv-note">CASH is the incumbent. A strategy '
      + 'wins only on an absolute positive event-clustered lower bound with enough independent events; never by '
      + 'being least negative.</p><div class="rv-table-wrap"><table class="rv-table"><thead><tr><th>CANDIDATE</th>'
      + '<th>STATE</th><th>EVENTS</th><th>MEAN NET / EVENT</th><th>LOWER BOUND / EVENT</th><th>BLOCKERS</th></tr>'
      + '</thead><tbody>' + rows + '</tbody></table></div>';
  }

  function regimes(d) {
    var r = d.regime_matrix || [];
    $('rv-regimes').innerHTML = '<h2>Regime matrix</h2><p class="rv-note">sport × family × regime. No global '
      + 'approval overrides a failing regime.</p><div class="rv-legend"><span class="rv-badge rv-GREEN">ELIGIBLE</span>'
      + '<span class="rv-badge rv-AMBER">SHADOW</span><span class="rv-badge rv-RED">CASH / ABSTAIN</span></div>'
      + (r.length ? '<div class="rv-regimes">' + r.map(function (x) {
        return '<div class="rv-cell rv-c-' + esc(x.colour) + '"><b>' + esc(x.sport) + ' · ' + esc(x.family) + ' · '
          + esc(x.regime) + '</b><small>' + esc(x.status) + ' · ' + esc(x.reason) + '</small><small>events '
          + esc(x.independent_events) + ' · calibration ' + esc(x.calibration_status) + ' · LB EV '
          + (x.lower_bound_ev_per_contract == null ? 'UNMEASURED' : num(x.lower_bound_ev_per_contract, 4))
          + '</small></div>';
      }).join('') + '</div>' : '<p class="rv-note">No segment evaluated in the window.</p>');
  }

  function waterfall(d) {
    var w = d.contribution_waterfall || {};
    var steps = w.steps || [];
    var max = Math.max.apply(null, steps.map(function (s) { return Math.abs(s.usd || 0); }).concat([1]));
    $('rv-waterfall').innerHTML = '<h2>Contribution waterfall</h2><p class="rv-note">' + esc(w.positions)
      + ' closed positions over ' + esc(w.events) + ' independent events, from the frozen records. Counterfactual '
      + 'P&amp;L is never shown as realized.</p><div class="rv-wf">' + steps.map(function (s) {
        var pct = s.usd == null ? 0 : Math.abs(s.usd) / max * 100;
        return '<div class="rv-wf-row"><span>' + esc(s.step) + '</span><span class="bar">'
          + (s.usd == null ? '' : '<i class="' + (s.usd < 0 ? 'neg' : 'pos') + '" style="left:0;width:' + pct.toFixed(1)
            + '%"></i>') + '</span>' + (s.usd == null ? '<span class="na" title="' + esc(s.why) + '">UNMEASURED</span>'
          : '<span class="v">' + usd(s.usd) + '</span>') + '</div>';
      }).join('') + '</div>';
  }

  function foot(d, env) {
    $('rv-foot').innerHTML = esc(d.version) + ' · ' + esc(env.label) + ' · ' + esc(env.authority)
      + ' · authority changed: ' + esc(String(d.authority_changed)) + ' · SMALL LIVE: ' + esc(d.small_live)
      + ' · package ' + esc(d.package) + ' · ' + esc(d.settled_positions) + ' settled positions / '
      + esc(d.unique_events) + ' events';
  }

  function load() {
    $('rv-error').hidden = true;
    $('rv-root').setAttribute('data-state', 'loading');
    fetch(URL_, {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}})
      .then(function (res) {
        if (res.status === 401 || res.status === 403) throw new Error('SIGN-IN REQUIRED: sign in on Command to read revenue readiness.');
        if (res.status === 404) throw new Error('NOT YET RELEASED: the revenue readiness endpoint is not deployed.');
        if (!res.ok) throw new Error('UNAVAILABLE: HTTP ' + res.status);
        return res.json();
      })
      .then(function (env) {
        if (!env || env.status !== 'OK' || !env.data) {
          throw new Error('UNAVAILABLE: ' + ((env && env.why) || 'no data'));
        }
        var d = env.data;
        plan(d); agents(d); tournament(d); regimes(d); waterfall(d); foot(d, env);
        $('rv-root').setAttribute('data-state', 'ready');
      })
      .catch(function (e) { fail(e.message || String(e)); });
  }

  document.addEventListener('DOMContentLoaded', function () {
    $('rv-refresh').addEventListener('click', load);
    load();
  });
}());
