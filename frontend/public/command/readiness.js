/* BETTOR Completion Readiness -- read-only view of /api/command/completion-readiness.
 * No demo fallback and no fake green: an unmeasured value is shown as
 * UNMEASURED with its reason; a failed read clears every section. No writes. */
(function () {
  'use strict';
  var URL_ = '/api/command/completion-readiness';
  var SECTIONS = ['cr-verdict', 'cr-runtime', 'cr-market', 'cr-prob', 'cr-agents', 'cr-gates', 'cr-owner', 'cr-foot'];

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
    return esc(x);
  }
  function pct(x) { return x == null ? v(null) : esc((x * 100).toFixed(1) + '%'); }
  function kv(rows) {
    return '<dl class="cr-kv">' + rows.map(function (r) { return '<dt>' + esc(r[0]) + '</dt><dd>' + r[1] + '</dd>'; }).join('') + '</dl>';
  }
  function card(title, rows) { return '<div class="cr-card"><h3>' + esc(title) + '</h3>' + kv(rows) + '</div>'; }

  function fail(msg) {
    var e = $('cr-error');
    e.hidden = false;
    e.textContent = msg;
    $('cr-root').setAttribute('data-state', 'unavailable');
    SECTIONS.forEach(function (id) { $(id).innerHTML = ''; });
    var st = $('cr-status');
    st.className = 'rv-status bad';
    st.textContent = 'UNAVAILABLE';
  }

  function verdict(d) {
    var r = d.readiness || {};
    var st = $('cr-status');
    var cand = r.status === 'CAPITAL_CANDIDATE';
    st.className = 'rv-status ' + (cand ? 'ready' : 'cash');
    st.textContent = cand ? 'CAPITAL CANDIDATE · owner decides' : 'PAPER / SHADOW ONLY';
    var g = r.governors || {};
    $('cr-verdict').innerHTML = '<h2>Readiness verdict: ' + esc(r.status) + '</h2><p class="rv-note">'
      + 'Automatic activation: ' + v(r.auto_activation) + ' · capital authority granted: ' + v(r.capital_authority_granted)
      + ' · SMALL LIVE: ' + esc(d.small_live) + ' · authority changed: ' + v(d.authority_changed) + '</p>'
      + '<div class="cr-blockers">' + (r.blockers || []).map(function (b) { return '<code>' + esc(b) + '</code>'; }).join('')
      + '</div>' + kv([['Executable EV governor', v(g.executable_ev)], ['Daily revenue readiness', v(g.daily_revenue_readiness)],
        ['Strategy tournament champion', v(g.strategy_tournament)]]);
  }

  function runtime(d) {
    var w = (d.runtime || {}).shared_workers || {};
    var m = (d.runtime || {}).market_plane || {};
    var res = m.resources || {};
    $('cr-runtime').innerHTML = '<h2>Runtime</h2><p class="rv-note">' + esc(w.no_oom_basis || '') + '</p><div class="cr-grid">'
      + card('Shared workers', [['commit', v((w.commit_sha || '').slice(0, 12) || null)],
        ['minutes since process start', v(w.minutes_since_process_start, 1)], ['RSS MB', v(w.rss_mb, 1)],
        ['RSS high-water MB', v(w.peak_mb, 1)], ['memory limit MB (cgroup)', v(w.memory_limit_mb, 1)],
        ['high-water / limit', pct(w.rss_highwater_fraction)],
        ['market plane started here', v(w.universal_market_plane_started_here)]])
      + card('Dedicated market plane', [['state', v(m.state)], ['runtime label', v(m.runtime_label)],
        ['subscription mode', v(m.subscription_mode)], ['market-data streams', v(m.market_data_streams)],
        ['heartbeat age s', v(m.heartbeat_age_s, 1)], ['RSS MB', v(res.rss_mb, 1)],
        ['high-water / limit', pct(res.highwater_fraction)]]) + '</div>';
  }

  function market(d) {
    var md = d.market_data || {};
    var pf = md.priority_freshness || {};
    var rf = md.refdata || {};
    var mg = d.management_freshness || {};
    var pull = rf.universe_pull || {};
    $('cr-market').innerHTML = '<h2>Market data</h2><p class="rv-note">Snapshot: ' + v(md.snapshot)
      + '. Freshness targets are 95% and are never widened to manufacture green.</p><div class="cr-grid">'
      + card('Subscription', [['mode', v(md.subscription_mode)], ['market-data streams', v(md.market_data_streams)],
        ['expected streams', v(md.market_data_streams_expected)], ['firm stream budget', v(md.firm_stream_budget)],
        ['subscribed', v(md.subscribed)], ['fresh', v(md.fresh)]])
      + card('Freshness', [['priority current / denominator', v(pf.numerator) + ' / ' + v(pf.denominator)],
        ['priority rate', pct(pf.rate)], ['held positions fresh rate', pct(mg.fresh_rate)],
        ['held markable', v(mg.markable)], ['held open positions', v(mg.open_positions)]])
      + card('Reference data', [['active contracts', v(rf.active_contracts)], ['PMX listed', v(rf.pmx_listed)],
        ['PMX unlisted', v(rf.pmx_unlisted)], ['pending', v(rf.refdata_pending)], ['coverage', pct(rf.coverage_rate)],
        ['last full pull', v((pull.last_receipt || {}).status)]]) + '</div>';
  }

  function prob(d) {
    var p = d.probability || {};
    var e = d.executable_ev || {};
    var t = d.digital_twin || {};
    var ci = p.delta_ci90 || [];
    $('cr-prob').innerHTML = '<h2>Probability · profitability · digital twin</h2><p class="rv-note">The market prior is the '
      + 'incumbent. Confidence is never authority; counterfactual P&amp;L is never realized.</p><div class="cr-grid">'
      + card('Probability authority', [['authority', v(p.authority)], ['reason', v(p.reason)],
        ['decisions / events', v(p.decisions_scored) + ' / ' + v(p.independent_events)],
        ['BETTOR − market log loss', v(p.bettor_logloss_minus_market, 4)],
        ['90% CI', v(ci[0], 4) + ' … ' + v(ci[1], 4)], ['calibration (ECE10)', v(p.bettor_calibration_error_ece10, 3)],
        ['edge claimed', v(p.edge_claimed)]])
      + card('Executable EV (profitability bind)', [['verdict', v(e.verdict)], ['reason', v(e.reason)],
        ['decisions priced', v(e.decisions_priced)], ['mean net EV / contract', v(e.mean_net_ev_per_contract, 4)],
        ['portfolio lower bound / contract', v(e.portfolio_lower_bound_ev_per_contract, 4)],
        ['authority granted', v(e.authority_granted)]])
      + card('Digital twin (repaired IOC)', [['status', v(t.status)], ['fresh orders replayed', v(t.replayed)],
        ['agree', v(t.agree)], ['fill agreement', pct(t.fill_agreement_rate)], ['target', pct(t.target)],
        ['twin P&L reported', v(t.twin_pnl_reported)]])
      + card('Arbitrage · venue positions', [['Adriana', v((d.arbitrage || {}).verdict)],
        ['PMUS positions', v((d.venue_positions || {}).status)],
        ['credential needed', v((d.venue_positions || {}).credential_type || null)]]) + '</div>';
  }

  function agents(d) {
    var a = d.agents || {};
    var dr = d.daily_revenue_readiness || {};
    var rows = Object.keys(a).map(function (k) {
      var x = a[k];
      return '<tr><td><b>' + esc(x.display_name) + '</b></td><td><span class="rv-badge rv-' + esc(x.license) + '">'
        + esc(x.license) + '</span></td><td class="rv-why">' + esc(x.current_authority) + '</td><td>'
        + esc(x.independent_events) + '</td><td class="rv-why">' + esc(x.exact_blocker || '—') + '</td></tr>';
    }).join('');
    $('cr-agents').innerHTML = '<h2>Agents · daily revenue readiness: ' + v(dr.overall_status) + '</h2><p class="rv-note">'
      + 'Licences never grant or change authority. Full scoreboard, tournament and regimes on '
      + '<a href="revenue.html">Revenue</a>.</p>' + (rows ? '<div class="rv-table-wrap"><table class="rv-table"><thead><tr>'
      + '<th>AGENT</th><th>LICENCE</th><th>AUTHORITY (UNCHANGED)</th><th>EVENTS</th><th>EXACT BLOCKER</th></tr></thead><tbody>'
      + rows + '</tbody></table></div>' : '<p class="rv-note">Revenue reliability: ' + v(d.revenue_status) + '</p>');
  }

  function gates(d) {
    var g = d.gates || {};
    $('cr-gates').innerHTML = '<h2>Capital-readiness hard gates</h2><div class="rv-table-wrap"><table class="rv-table">'
      + '<thead><tr><th>GATE</th><th>VALUE</th><th>REASON WHEN NOT GREEN</th></tr></thead><tbody>'
      + Object.keys(g).map(function (k) {
        return '<tr><td>' + esc(k) + '</td><td>' + v(g[k].value) + '</td><td class="rv-why">'
          + (g[k].value ? '—' : esc(g[k].reason || '')) + '</td></tr>';
      }).join('') + '</tbody></table></div>';
  }

  function owner(d) {
    var o = d.owner_blockers || [];
    $('cr-owner').innerHTML = '<h2>Owner-controlled blockers</h2>' + (o.length ? '<ul class="cr-owner">' + o.map(function (x) {
      return '<li><b>' + esc(x.code) + '</b> · ' + esc(x.item) + (x.state ? ' (' + esc(x.state) + ')' : '')
        + (x.credential_type ? '<br>credential: <code>' + esc(x.credential_type) + '</code>' : '')
        + '<br><code>' + esc(x.action) + '</code></li>';
    }).join('') + '</ul>' : '<p class="rv-note">None recorded.</p>');
    $('cr-foot').innerHTML = esc(d.version) + ' · ' + esc(d.mode) + ' · as of ' + esc(new Date((d.as_of || 0) * 1000).toISOString());
  }

  function load() {
    $('cr-error').hidden = true;
    $('cr-root').setAttribute('data-state', 'loading');
    fetch(URL_, {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}})
      .then(function (res) {
        if (res.status === 401 || res.status === 403) throw new Error('SIGN-IN REQUIRED: sign in on Command to read readiness.');
        if (res.status === 404) throw new Error('NOT YET RELEASED: the completion readiness endpoint is not deployed.');
        if (!res.ok) throw new Error('UNAVAILABLE: HTTP ' + res.status);
        return res.json();
      })
      .then(function (env) {
        if (!env || env.status !== 'OK' || !env.data) throw new Error('UNAVAILABLE: ' + ((env && env.why) || 'no data'));
        var d = env.data;
        verdict(d); runtime(d); market(d); prob(d); agents(d); gates(d); owner(d);
        $('cr-root').setAttribute('data-state', 'ready');
      })
      .catch(function (e) { fail(e.message || String(e)); });
  }

  document.addEventListener('DOMContentLoaded', function () {
    $('cr-refresh').addEventListener('click', load);
    load();
  });
}());
