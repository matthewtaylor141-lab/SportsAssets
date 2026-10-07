/* BETTOR Venues & Claims -- read-only view of /api/command/venues.
 * No demo fallback and no fake green: an unmeasured value is UNMEASURED;
 * a failed read clears every section. KALSHI_HEALTH and POLYMARKET_HEALTH
 * are shown apart, as the API reads them. No writes. */
(function () {
  'use strict';
  var URL_ = '/api/command/venues';
  var SECTIONS = ['vn-health', 'vn-coverage', 'vn-claims', 'vn-arb', 'vn-foot'];

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
  function cents(x) { return x == null ? v(null) : esc((+x * 100).toFixed(1) + '¢'); }
  function usd(x) { return x == null || isNaN(+x) ? v(null) : esc('$' + (+x).toFixed(2)); }
  function kv(rows) {
    return '<dl class="cr-kv">' + rows.map(function (r) { return '<dt>' + esc(r[0]) + '</dt><dd>' + r[1] + '</dd>'; }).join('') + '</dl>';
  }
  function card(t, rows) { return '<div class="cr-card"><h3>' + esc(t) + '</h3>' + kv(rows) + '</div>'; }

  function fail(msg) {
    var e = $('vn-error');
    e.hidden = false;
    e.textContent = msg;
    $('vn-root').setAttribute('data-state', 'unavailable');
    SECTIONS.forEach(function (id) { $(id).innerHTML = ''; });
    var st = $('vn-status');
    st.className = 'rv-status bad';
    st.textContent = 'UNAVAILABLE';
  }

  function health(d) {
    var k = (d.health || {}).KALSHI_HEALTH || {};
    var p = (d.health || {}).POLYMARKET_HEALTH || {};
    var kh = k.health || {}, cat = k.catalogue || {}, fr = k.freshness || {}, fx = k.fixtures || {};
    var st = $('vn-status');
    st.className = 'rv-status cash';
    st.textContent = 'SHADOW · KALSHI LIVE MONEY ' + esc(d.kalshi_live_money);
    $('vn-health').innerHTML = '<h2>Venue health</h2><p class="rv-note">Two domains, never blended: a Kalshi 429 '
      + 'cannot make Polymarket stale; a Polymarket reconnect cannot make Kalshi healthy.</p><div class="cr-grid">'
      + card('KALSHI_HEALTH', [['state', v(k.state)], ['heartbeat age s', v(k.heartbeat_age_s, 1)],
        ['requests / ok / 429', v(kh.requests) + ' / ' + v(kh.ok) + ' / ' + v(kh.http_429)],
        ['backing off', v(kh.backing_off)], ['catalogue', cat.complete ? '<span class="cr-ok">COMPLETE</span>'
          : v(cat.stopped || (cat.complete === false ? 'TRUNCATED' : null))],
        ['sports markets', v(cat.sports_markets)], ['fixtures established / mapped to PMUS',
          v(fx.established) + ' / ' + v(fx.pmus_mapped)],
        ['book freshness', v(fr.numerator) + ' / ' + v(fr.denominator) + (fr.rate == null ? '' : ' (' + (fr.rate * 100).toFixed(1) + '%)')]])
      + card('POLYMARKET_HEALTH', [['state', v(p.state)], ['books (15 min)', v(p.books_15m)],
        ['errors (15 min)', v(p.errors_15m)], ['newest book age s', v(p.newest_book_age_s, 1)], ['source', v(p.source)]])
      + '</div>';
  }

  function coverage(d) {
    var c = ((d.health || {}).KALSHI_HEALTH || {}).coverage || {};
    var rows = Object.keys(c).sort().map(function (key) {
      var x = c[key];
      return '<tr><td>' + esc(key.replace('|', ' · ')) + '</td><td>' + v(x.fixture_established) + ' / ' + v(x.kalshi_events)
        + '</td><td>' + v(x.pmus_established) + ' / ' + v(x.kalshi_events) + '</td><td class="rv-why">'
        + esc(Object.keys(x.not_established_reasons || {}).map(function (r) { return r + ' ' + x.not_established_reasons[r]; }).join(' · ') || '—')
        + '</td></tr>';
    }).join('');
    $('vn-coverage').innerHTML = '<h2>Kalshi mapping coverage</h2><p class="rv-note">Structured facts only (league, '
      + 'league-namespaced teams, start); titles are never evidence. Ambiguous = NOT_ESTABLISHED.</p>'
      + (rows ? '<div class="rv-table-wrap"><table class="rv-table"><thead><tr><th>SPORT · FAMILY</th><th>FIXTURE ESTABLISHED</th>'
        + '<th>MAPPED TO PMUS</th><th>WHY NOT</th></tr></thead><tbody>' + rows + '</tbody></table></div>'
        : '<p class="rv-note">No Kalshi game event read yet: ' + v(null) + '</p>');
  }

  function claims(d) {
    var evs = d.claims || [];
    var html = evs.map(function (e) {
      var cl = (e.claims || []).map(function (c) {
        var r = c.route || {};
        var best = r.best_single || {};
        var cand = r.candidates || [];
        var rows = cand.map(function (x) {
          var isBest = best && x.venue === best.venue && x.market_id === best.market_id && x.side === best.side;
          return '<tr class="' + (isBest ? 'vn-best' : '') + '"><td>' + esc(x.venue) + '</td><td>' + esc(x.market_id)
            + '</td><td>' + esc(x.side) + '</td><td>' + cents(x.ask) + '</td><td>' + usd(x.fee) + '</td><td>'
            + (x.all_in_per_contract ? cents(x.all_in_per_contract) : '—') + '</td><td>'
            + (x.eligible ? '<span class="cr-ok">✓</span>' : '<span class="cr-bad">' + esc(x.reason) + '</span>') + '</td></tr>';
        }).join('');
        var ch = r.chosen;
        return '<div class="vn-claim"><h3>Claim <code>' + esc((c.fingerprint || '').slice(0, 16)) + '…</code> · '
          + esc(c.aliases.length) + ' executable alias' + (c.aliases.length === 1 ? '' : 'es') + '</h3>'
          + '<div class="rv-table-wrap"><table class="rv-table"><thead><tr><th>VENUE</th><th>CONTRACT</th><th>SIDE</th>'
          + '<th>ASK</th><th>FEES (' + esc(r.qty) + ')</th><th>ALL-IN</th><th>FRESH / ELIGIBLE</th></tr></thead><tbody>' + rows
          + '</tbody></table></div><p class="rv-note">Chosen: ' + (ch ? esc(ch.topology) + ' · all-in ' + usd(ch.all_in)
            + ' for ' + esc(r.qty) : '<span class="cr-bad">' + esc(r.refusal || 'NO ROUTE') + '</span>') + '</p></div>';
      }).join('');
      var ref = (e.refused || []).map(function (x) {
        return '<span class="vn-tag">' + esc(x.venue + ' ' + x.market_id + ' ' + x.side) + ': '
          + esc((x.refusals || []).join(', ')) + '</span>';
      }).join(' ');
      return '<div class="vn-event">' + esc(e.event_key) + '</div>' + cl
        + (ref ? '<p class="rv-note">Not a provable claim: ' + ref + '</p>' : '');
    }).join('');
    $('vn-claims').innerHTML = '<h2>Claims · every executable path · best all-in route</h2><p class="rv-note">Equivalent '
      + 'contracts are collapsed to one claim only by payoff in every state; their quotes are never collapsed. Unknown fee, stale '
      + 'book or no book is ineligible, never zero.</p>' + (html || '<p class="rv-note">No claim assembled yet.</p>');
  }

  function arb(d) {
    var a = d.arbitrage || {};
    var s = a.scan || {};
    function rowsOf(list, refused) {
      return (list || []).map(function (o) {
        var legs = (o.legs || []).map(function (l) { return esc(l.venue + ' ' + l.market_id + ' ' + l.side); }).join('<br>');
        return '<tr><td>' + esc(o.event_key) + '</td><td>' + esc(o.topology || '—') + '<br><small>' + esc(o.basis || '') + '</small></td><td>'
          + legs + '</td><td>' + v(o.qty) + '</td><td>' + usd(o.principal) + '</td><td>' + usd(o.fees) + '</td><td>'
          + usd(o.slippage_buffers) + '</td><td>' + usd(o.guaranteed_payout) + '</td><td>' + usd(o.guaranteed_net_profit)
          + '</td><td>' + (o.roi == null ? v(null) : esc((o.roi * 100).toFixed(2) + '%')) + '</td><td class="rv-why">'
          + (refused ? esc(o.primary_code) : 'GUARANTEED_AFTER_COSTS') + '</td></tr>';
      }).join('');
    }
    var head = '<thead><tr><th>EVENT</th><th>TOPOLOGY</th><th>CHOSEN CONTRACTS</th><th>QTY</th><th>PRINCIPAL</th><th>FEES</th>'
      + '<th>SLIPPAGE BUFFERS</th><th>GUARANTEED PAYOUT</th><th>GUARANTEED NET</th><th>ROI</th><th>VERDICT</th></tr></thead>';
    $('vn-arb').innerHTML = '<h2>Adriana · claim-first arbitrage (SHADOW)</h2><p class="rv-note">Scan ' + v(s.scan_id) + ' · '
      + v(s.structures) + ' structures · ' + v(s.opportunities) + ' guaranteed after costs · ' + v(s.refusals) + ' refused. '
      + 'Topology is read off the contracts actually chosen. Adriana holds no submit, cancel or capital authority.</p>'
      + '<div class="rv-table-wrap"><table class="rv-table">' + head + '<tbody>' + rowsOf(a.opportunities, false)
      + rowsOf(a.refusals, true) + '</tbody></table></div>';
    $('vn-foot').innerHTML = esc(d.version) + ' · ' + esc(d.mode) + ' · Adriana ' + esc(d.adriana) + ' · authority changed: '
      + esc(String(d.authority_changed));
  }

  function load() {
    $('vn-error').hidden = true;
    $('vn-root').setAttribute('data-state', 'loading');
    fetch(URL_, {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}})
      .then(function (res) {
        if (res.status === 401 || res.status === 403) throw new Error('SIGN-IN REQUIRED: sign in on Command to read venues.');
        if (res.status === 404) throw new Error('NOT YET RELEASED: the venues endpoint is not deployed.');
        if (!res.ok) throw new Error('UNAVAILABLE: HTTP ' + res.status);
        return res.json();
      })
      .then(function (env) {
        if (!env || env.status !== 'OK' || !env.data) throw new Error('UNAVAILABLE: ' + ((env && env.why) || 'no data'));
        var d = env.data;
        health(d); coverage(d); claims(d); arb(d);
        $('vn-root').setAttribute('data-state', 'ready');
      })
      .catch(function (e) { fail(e.message || String(e)); });
  }

  document.addEventListener('DOMContentLoaded', function () {
    $('vn-refresh').addEventListener('click', load);
    load();
  });
}());
