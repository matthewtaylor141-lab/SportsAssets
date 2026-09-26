/* BETTOR EV ENGINE · OPERATING DESK — the read, rendered.
 *
 * ONE SOURCE. Everything here comes from a single authenticated same-origin
 * GET of /api/command/bettor/desk. There is no second feed, no fixture, and
 * NO DISPLAY ARITHMETIC: a figure this page cannot read shows as an em dash,
 * never as zero. A zero on this screen is a zero in the ledger.
 *
 * NO QUERY STRING, DELIBERATELY. BTCore.endpoint() refuses any path carrying
 * `?` or `#` -- the guard that stops this page being re-pointed at another
 * host or carrying a token in a URL. The route's own defaults are therefore
 * what the desk reads, and that is the correct trade.
 *
 * THE BOOKS ARE NEVER SUMMED. Autonomous research, the controlled
 * demonstration, historical benchmarks and the acceptance position render as
 * separate books with their own totals, because a copied, historical or
 * synthetic fill is not autonomous Bettor performance. There is no grand
 * total on this page and adding one would be the specific misreport it
 * exists to prevent.
 *
 * INTERNAL IDENTIFIERS LIVE IN AUDIT. Experiment ids, policy names and
 * position ids are real provenance and are preserved -- inside the audit
 * disclosure on each section, not in headings or trading summaries.
 */
(function () {
  'use strict';

  var C = window.BTCore;
  var DESK_PATH = '/api/command/bettor/desk';
  var POLL_MS = 60000;

  function esc(v) { return C ? C.esc(v) : String(v == null ? '' : v); }
  function dash(v) {
    return (v === null || v === undefined || v === '') ? '—' : esc(v);
  }
  function num(v) {
    return (typeof v === 'number' && isFinite(v)) ? v : null;
  }
  function usd(v) {
    var n = num(v);
    return n === null ? '—' : (C ? C.usd(n, 2) : '$' + n.toFixed(2));
  }
  function signedUsd(v) {
    var n = num(v);
    if (n === null) { return '—'; }
    return (n > 0 ? '+' : n < 0 ? '−' : '') + usd(Math.abs(n));
  }
  function cents(v) {
    var n = num(v);
    return n === null ? '—' : (n * 100).toFixed(1) + '¢';
  }
  function qty(v) {
    var n = num(v);
    return n === null ? '—' : String(Number(n.toFixed(4)));
  }
  function tone(v) {
    var n = num(v);
    return n === null ? 'muted' : n > 0 ? 'good' : n < 0 ? 'bad' : '';
  }
  function epochIso(v) {
    var n = num(v);
    if (n === null) { return '—'; }
    try { return new Date(n * 1000).toISOString().replace('.000', ''); }
    catch (e) { return '—'; }
  }
  function json(v) {
    try { return esc(JSON.stringify(v, null, 2)); } catch (e) { return '—'; }
  }
  function fact(k, v, cls) {
    return '<div class="fact"><span class="k">' + esc(k) + '</span>' +
      '<span class="v ' + (cls || '') + '">' + v + '</span></div>';
  }
  function card(id, title, sub, body) {
    return '<section class="card" id="' + esc(id) + '"><h2>' + esc(title) +
      (sub ? '<span class="sub">' + esc(sub) + '</span>' : '') +
      '</h2><div class="card-body">' + body + '</div></section>';
  }
  function audit(label, obj) {
    return '<details class="audit"><summary>' + esc(label) +
      '</summary><pre>' + json(obj) + '</pre></details>';
  }

  function controls(d) {
    var c = d.controls || {};
    var mode = c.research_mode || {};
    var build = c.build_identity || {};
    var armed = mode.env_flag_set === true;
    var body = '<div class="facts three">' +
      fact('Mode', '<span class="pill ' + (armed ? 'pill-blue' : 'pill-warn') +
        '">' + dash(mode.label) + '</span>') +
      fact('Scheduler flag', dash(mode.env_flag) + ' = ' +
        (mode.env_flag_set === true ? 'set'
          : mode.env_flag_set === false ? 'UNSET' : '—')) +
      fact('Submits orders', '<span class="pill ' +
        (mode.submits_orders === false ? 'pill-good' : 'pill-bad') + '">' +
        (mode.submits_orders === false ? 'NO' : 'CHECK') + '</span>') +
      fact('Live build', '<span class="mono">' + dash(build.build) + '</span>') +
      fact('Source digest', '<span class="mono">' +
        dash(build.source_sha256_12) + '</span>') +
      fact('Writer pid', dash(build.pid)) +
      fact('Last cycle', '<span class="mono">' + epochIso(c.last_cycle_at) +
        '</span>') +
      fact('Cycle state', dash(c.cycle_state)) +
      fact('Cycle label', '<span class="pill ' +
        (String(c.cycle_label || '').indexOf('ZERO_EVALUATED') === 0
          ? 'pill-warn' : 'pill-blue') + '">' + dash(c.cycle_label) +
        '</span>') +
      '</div>' +
      audit('Audit — control row, heartbeat and writer identity', {
        control_state: c.control_state,
        scheduler_heartbeat: c.scheduler_heartbeat,
        build_identity: build, audit: c.audit
      });
    return card('controls', 'Controls',
      'research mode, scheduler and live build', body);
  }

  function opportunities(d) {
    var o = d.opportunities || {};
    var fv = o.fair_value_source || {};
    var out = '';

    if (o.verdict === 'NO_TRADE') {
      var reasons = o.no_trade_reasons || {};
      var keys = Object.keys(reasons);
      out += '<div class="verdict"><b>NO_TRADE</b>' +
        esc(o.no_trade_note || 'No candidate was admissible.') + '</div>';
      out += keys.length
        ? '<div class="tablewrap"><table class="dt"><thead><tr>' +
          '<th>Blocking gate</th><th class="num">Candidates</th></tr>' +
          '</thead><tbody>' + keys.sort().map(function (k) {
            return '<tr><td class="mono">' + esc(k) + '</td>' +
              '<td class="num">' + esc(reasons[k]) + '</td></tr>';
          }).join('') + '</tbody></table></div>'
        : '<p class="muted">No blocking gate was recorded this cycle.</p>';
    } else if (o.verdict) {
      out += '<div class="verdict good"><b>' + esc(o.verdict) + '</b>' +
        esc(o.admissible_count) + ' admissible candidate(s).</div>';
    }

    out += '<div class="facts three" style="margin-top:12px">' +
      fact('Markets considered', dash(o.markets_considered)) +
      fact('Candidates read', dash((o.candidates || []).length)) +
      fact('Admissible', dash(o.admissible_count)) +
      fact('Fair value source', dash(fv.name)) +
      fact('Source kind', dash(fv.kind)) +
      fact('Internally trained model', '<span class="pill ' +
        (fv.is_an_internally_trained_model === false ? 'pill-warn'
          : 'pill-grey') + '">' +
        (fv.is_an_internally_trained_model === false
          ? 'NO — EXTERNAL BOOK' : '—') + '</span>') +
      '</div>';

    var funnel = o.funnel || {};
    var fk = Object.keys(funnel);
    if (fk.length) {
      out += '<h3 class="h3">Funnel, per probability source</h3>' +
        '<div class="tablewrap"><table class="dt"><thead><tr><th>Source</th>' +
        '<th class="num">Venue open</th><th class="num">Provider events</th>' +
        '<th class="num">With a price</th><th class="num">Mapped</th>' +
        '<th class="num">Evaluated</th><th class="num">Written</th></tr>' +
        '</thead><tbody>' + fk.sort().map(function (k) {
          var v = funnel[k] || {};
          return '<tr><td>' + esc(k) + '</td><td class="num">' +
            dash(v.venue_markets_open_and_fresh) + '</td><td class="num">' +
            dash(v.provider_events) + '</td><td class="num">' +
            dash(v.with_pinnacle_h2h) + '</td><td class="num">' +
            dash(v.mapped_to_a_venue_contract) + '</td><td class="num">' +
            dash(v.evaluated) + '</td><td class="num">' +
            dash(v.written) + '</td></tr>';
        }).join('') + '</tbody></table></div>';
    }

    var led = o.first_refusal_per_mapped_candidate || [];
    if (led.length) {
      out += '<h3 class="h3">Every mapped candidate, at its first refusal' +
        '</h3><div class="tablewrap"><table class="dt"><thead><tr>' +
        '<th>Venue contract</th><th>Priced outcome</th><th>Stage</th>' +
        '<th>First refusal</th><th class="num">Edge</th></tr></thead><tbody>' +
        led.map(function (r) {
          return '<tr><td class="mono">' + dash(r.us_market_slug) + '</td>' +
            '<td>' + dash(r.priced_outcome) + '</td><td>' + dash(r.stage) +
            '</td><td class="mono">' + dash(r.first_refusal || 'ADMITTED') +
            '</td><td class="num ' + tone(r.edge) + '">' +
            (num(r.edge) === null ? '—' : cents(r.edge)) + '</td></tr>';
        }).join('') + '</tbody></table></div>';
    }

    var cands = o.candidates || [];
    if (cands.length) {
      out += '<h3 class="h3">Decision detail</h3><div class="tablewrap">' +
        '<table class="dt"><thead><tr><th>Contract</th>' +
        '<th class="num">Fair value</th><th class="num">Ask</th>' +
        '<th class="num">Fee</th><th class="num">Net edge</th>' +
        '<th class="num">Depth</th><th class="num">Proposed qty</th>' +
        '<th>Action</th><th>Reason</th></tr></thead><tbody>' +
        cands.map(function (x) {
          return '<tr><td class="mono">' +
            dash(x.us_market_slug || x.market || x.condition_id) + '</td>' +
            '<td class="num">' + cents(x.p_pay) + '</td>' +
            '<td class="num">' + cents(x.ask) + '</td>' +
            '<td class="num">' + cents(x.fee_per) + '</td>' +
            '<td class="num ' + tone(x.edge) + '">' + cents(x.edge) + '</td>' +
            '<td class="num">' +
            qty(x.displayed_depth === undefined ? x.available_depth
                                               : x.displayed_depth) + '</td>' +
            '<td class="num">' + qty(x.qty) + '</td>' +
            '<td>' + dash(x.action) + '</td>' +
            '<td class="muted">' +
            dash((x.refusals || [])[0] || x.why) + '</td></tr>';
        }).join('') + '</tbody></table></div>';
    }

    out += audit('Audit — experiment, policy, calibration and raw candidates', {
      audit: o.audit, calibration: o.calibration, refusals: o.refusals,
      venue_errors: o.venue_errors, candidates: cands
    });
    return card('opportunities', 'Opportunities',
      'what the engine evaluated, and what stopped it', out);
  }

  function orders(d) {
    var o = d.orders || {};
    var rows = o.open_inventory_rows || [];
    var body = '<p class="muted">' + esc(o.note || '') + '</p>';
    body += rows.length
      ? '<div class="tablewrap"><table class="dt"><thead><tr>' +
        '<th>Position</th><th>Contract</th><th class="num">Qty</th>' +
        '<th class="num">Basis</th><th>Status</th></tr></thead><tbody>' +
        rows.map(function (r) {
          return '<tr><td class="mono">' + dash(r.position_id) + '</td>' +
            '<td class="mono">' + dash(r.us_market_slug || r.market) +
            '</td><td class="num">' +
            qty(r.qty === undefined ? r.filled_qty : r.qty) + '</td>' +
            '<td class="num">' + usd(r.basis_usd) + '</td><td>' +
            dash(r.status || r.state) + '</td></tr>';
        }).join('') + '</tbody></table></div>'
      : '<p class="muted">No simulated order or fill in this window.</p>';
    body += audit('Audit — order and fill rows', rows);
    return card('orders', 'Orders',
      'simulated only — every fill is modelled', body);
  }

  function positions(d) {
    var p = d.positions || {};
    var books = p.books || {};
    var names = Object.keys(books);
    var body = '<p class="muted">' + esc(p.note || '') + '</p>';
    if (!names.length) {
      body += '<p class="muted">No position in any book.</p>';
    }
    names.sort().forEach(function (name) {
      var b = books[name] || {};
      var rows = b.rows || [];
      body += '<div class="book"><h3>' + esc(name) +
        ' <span class="pill pill-grey">' + esc(b.count) + '</span></h3>' +
        '<div class="lanenote">its own book — not added to any other</div>';
      if (rows.length) {
        body += '<div class="tablewrap"><table class="dt"><thead><tr>' +
          '<th>Position</th><th>Policy</th><th class="num">Residual</th>' +
          '<th class="num">Realised</th><th>Latest management</th>' +
          '<th>Trace</th></tr></thead><tbody>' + rows.map(function (r) {
            var pid = r.position_id || r.id;
            var href = String(p.trace_link_template || '')
              .replace('{position_id}',
                       encodeURIComponent(String(pid || '')));
            return '<tr><td class="mono">' + dash(pid) + '</td><td>' +
              dash(r.policy) + '</td><td class="num">' +
              qty(r.residual_qty) + '</td><td class="num ' +
              tone(r.realized_cash) + '">' + signedUsd(r.realized_cash) +
              '</td><td>' + dash(r.last_action || r.decision) + '</td><td>' +
              (pid ? '<a class="mono" href="' + esc(href) + '">trace</a>'
                   : '—') + '</td></tr>';
          }).join('') + '</tbody></table></div>';
      }
      body += '</div>';
    });
    body += audit('Audit — every position row, with its original identifiers',
                  books);
    return card('positions', 'Positions', 'separate books, never summed',
                body);
  }

  function management(d) {
    var p = d.positions || {};
    var rows = [];
    Object.keys(p.books || {}).forEach(function (k) {
      ((p.books[k] || {}).rows || []).forEach(function (r) {
        rows.push({book: k, row: r});
      });
    });
    var body = rows.length
      ? '<div class="tablewrap"><table class="dt"><thead><tr><th>Book</th>' +
        '<th>Position</th><th>Latest decision</th><th>Reason</th>' +
        '<th>Decided at</th><th>Next review</th></tr></thead><tbody>' +
        rows.map(function (x) {
          var r = x.row;
          return '<tr><td>' + esc(x.book) + '</td><td class="mono">' +
            dash(r.position_id || r.id) + '</td><td>' +
            dash(r.last_action || r.decision) + '</td><td class="muted">' +
            dash(r.last_reason || r.why) + '</td><td class="mono">' +
            dash(r.last_decision_at || r.newest_decision_ts) + '</td><td>' +
            dash(r.next_review) + '</td></tr>';
        }).join('') + '</tbody></table></div>'
      : '<p class="muted">No managed position in any book.</p>';
    body += '<p class="muted" style="margin-top:10px">A position with no ' +
      'observed runtime decision is shown as having none. An absent ' +
      'management action is never displayed as a hold.</p>';
    return card('management', 'Management',
      'the latest decision on each holding, and why', body);
  }

  function performance(d) {
    var perf = d.performance || {};
    var lanes = perf.per_lane || {};
    var names = Object.keys(lanes);
    var body = '<p class="muted">' + esc(perf.note || '') + '</p>';
    body += names.length
      ? '<div class="tablewrap"><table class="dt"><thead><tr><th>Book</th>' +
        '<th class="num">Realised</th><th class="num">Fees</th>' +
        '<th class="num">Unrealised</th><th class="num">Total</th>' +
        '<th class="num">Marked</th><th class="num">Unmarked</th></tr>' +
        '</thead><tbody>' + names.sort().map(function (k) {
          var v = lanes[k] || {};
          return '<tr><td>' + esc(k) + '</td><td class="num ' +
            tone(v.realised) + '">' + signedUsd(v.realised) +
            '</td><td class="num">' + usd(v.fees) + '</td><td class="num ' +
            tone(v.unrealised) + '">' + signedUsd(v.unrealised) +
            '</td><td class="num ' + tone(v.total) + '">' +
            signedUsd(v.total) + '</td><td class="num">' + dash(v.marked) +
            '</td><td class="num ' + (num(v.unmarked) ? 'warn' : '') + '">' +
            dash(v.unmarked) + '</td></tr>';
        }).join('') + '</tbody></table></div>'
      : '<p class="muted">No lane reported a figure.</p>';
    body += '<div class="facts" style="margin-top:12px">' +
      fact('Mark basis', dash(perf.mark_basis)) +
      fact('Why not a midpoint', dash(perf.why_not_a_midpoint)) + '</div>';
    body += audit('Audit — per-lane accounting as read', perf);
    return card('performance', 'Performance',
      'per book — realised, fees, unrealised, unmarked', body);
  }

  function funded(d) {
    var c = d.controls || {};
    var mode = c.research_mode || {};
    var lim = d.open_limitations || {};

    /* THE CHECKS ARE DERIVED FROM THE READ, NOT DECLARED HERE. A check this
     * page cannot evaluate is UNKNOWN and blocks, exactly as an unevaluable
     * risk rail does. */
    var checks = [
      {name: 'Funded submission disabled',
       ok: d.funded_submission === 'DISABLED',
       detail: 'The lane writes with order_submitted CHECK FALSE and the ' +
         'venue-boundary gate authorises every submission independently ' +
         'of this page.'},
      {name: 'Research mode labelled and armed',
       ok: mode.env_flag_set === true,
       detail: 'Scheduler flag ' + (mode.env_flag || '—') + '; the control ' +
         'row must also read true for the lane to cycle.'},
      {name: 'Account binding',
       ok: null,
       detail: 'No funded account is bound. The exact account, its capital ' +
         'limit, per-order limit, exposure limit and daily loss stop are ' +
         'owner-supplied and none is set. Activation cannot display an ' +
         'account it has not been given.'},
      {name: 'Venue book freshness basis',
       ok: false,
       detail: String(lim.venue_book_freshness || 'unresolved')},
      {name: 'Settlement compatibility',
       ok: false,
       detail: String(lim.settlement_compatibility || 'unresolved')},
      {name: 'Market scope metadata',
       ok: false,
       detail: String(lim.market_scope_metadata || 'unresolved')},
      {name: 'Autonomous entry on current markets',
       ok: (d.opportunities || {}).verdict !== 'NO_TRADE',
       detail: 'A funded account must not be armed on a lane that has not ' +
         'yet admitted an entry on current markets under its own gates.'}
    ];
    var blocking = checks.filter(function (x) { return x.ok !== true; });

    var body = '<p class="muted">Every control below is an existing ' +
      'execution control, surfaced. This page sends nothing: the buttons ' +
      'are inert while activation is locked, and activation is locked ' +
      'while any check is unresolved.</p>';

    body += '<ul class="checks">' + checks.map(function (x) {
      var pill = x.ok === true ? '<span class="pill pill-good">PASS</span>'
        : x.ok === false ? '<span class="pill pill-bad">BLOCKING</span>'
        : '<span class="pill pill-warn">UNKNOWN</span>';
      return '<li>' + pill + '<span class="cw"><b>' + esc(x.name) +
        '</b><span>' + esc(x.detail) + '</span></span></li>';
    }).join('') + '</ul>';

    body += '<div class="facts three limits" style="margin-top:12px">' +
      fact('Bound account', '<span class="pill pill-warn">NONE BOUND</span>') +
      fact('Capital limit', '—') + fact('Per-order limit', '—') +
      fact('Exposure limit', '—') + fact('Daily loss stop', '—') +
      fact('Emergency halt', '<span class="pill pill-grey">available via ' +
        'the existing control row</span>') + '</div>';

    body += '<div class="activate">' +
      '<button type="button" disabled aria-disabled="true">' +
      'Activate funded trading</button>' +
      '<button type="button" disabled aria-disabled="true">Pause</button>' +
      '<button type="button" disabled aria-disabled="true">Cancel all' +
      '</button><button type="button" disabled aria-disabled="true">' +
      'Emergency halt</button>' +
      '<span class="why">Locked — ' + esc(blocking.length) +
      ' unresolved check(s): ' +
      esc(blocking.map(function (x) { return x.name; }).join('; ')) +
      '</span></div>';

    body += '<h3 class="h3">Open limitations, stated</h3><ul class="openlim">' +
      Object.keys(lim).filter(function (k) {
        return typeof lim[k] === 'string';
      }).map(function (k) {
        return '<li><b>' + esc(k) + '</b> — ' + esc(lim[k]) + '</li>';
      }).join('') + '</ul>';

    return card('funded', 'Activation',
      'locked while any readiness check is unresolved', body);
  }

  function render(d) {
    var host = document.getElementById('desk');
    var state = document.getElementById('deskstate');
    var mode = (d.controls || {}).research_mode || {};
    var pill = document.getElementById('mode-pill');
    if (pill) {
      pill.textContent = mode.label || 'MODE —';
      pill.className = 'pill ' + (mode.env_flag_set ? 'pill-blue'
                                                    : 'pill-warn');
    }
    var fp = document.getElementById('funded-pill');
    if (fp) {
      fp.textContent = 'FUNDED ' + (d.funded_submission || '—');
      fp.className = 'pill ' + (d.funded_submission === 'DISABLED'
        ? 'pill-good' : 'pill-bad');
    }
    var asof = document.getElementById('asof');
    if (asof) { asof.textContent = 'Read ' + (d.as_of || '—'); }

    host.innerHTML = controls(d) + opportunities(d) + orders(d) +
      positions(d) + management(d) + performance(d) + funded(d);
    host.hidden = false;
    if (state) { state.hidden = true; }
  }

  function fail(msg) {
    var state = document.getElementById('deskstate');
    var host = document.getElementById('desk');
    if (host) { host.hidden = true; }
    if (!state) { return; }
    state.hidden = false;
    /* A FAILED READ IS SAID, NEVER RENDERED AS ZEROS. */
    state.innerHTML = '<div class="verdict"><b>The desk could not read its ' +
      'data</b>' + esc(msg) + '<br>An unread desk shows no numbers rather ' +
      'than zeros.</div>';
  }

  function load() {
    var path;
    try { path = C.endpoint(DESK_PATH); }
    catch (e) {
      fail('The desk path is not an allowed read endpoint.');
      return;
    }
    fetch(path, {credentials: 'same-origin',
                 headers: {Accept: 'application/json'},
                 cache: 'no-store'})
      .then(function (r) {
        if (r.status === 401 || r.status === 403) {
          if (window.BTUnlock && window.BTUnlock.prompt) {
            window.BTUnlock.prompt();
          }
          throw new Error('This desk needs a Command session (HTTP ' +
                          r.status + ').');
        }
        if (!r.ok) {
          throw new Error('The desk read returned HTTP ' + r.status + '.');
        }
        return r.json();
      })
      .then(render)
      .catch(function (e) { fail(e && e.message ? e.message : String(e)); });
  }

  function start() {
    if (!C || typeof C.endpoint !== 'function') {
      fail('core.js did not load, so the read guard is absent.');
      return;
    }
    load();
    setInterval(load, POLL_MS);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else { start(); }

  window.BettorDesk = {load: load, render: render, VERSION: '1.0.0'};
}());
