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
  /* The last read, kept so a control result can be shown without
   * waiting for the next poll. It is never a substitute for one:
   * every control action triggers a fresh read. */
  var LAST_DESK = null;

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


  function demonstration(d) {
    var m = d.demonstration || {};
    var rec = m.reconciliation || {};
    var rows = rec.positions || [];
    var body = '<div class="verdict"><b>' +
      esc(m.label || 'CONTROLLED DEMONSTRATION') + '</b>' +
      esc('Its inputs are chosen, not observed. It proves the deployed ' +
          'software carries a position from entry through management, a ' +
          'reduction, an exit and reconciled accounting. It is not ' +
          'evidence that such a trade existed or would have filled, and ' +
          'it is excluded from strategy performance.') + '</div>';
    if (!rows.length) {
      body += '<p class="muted">The demonstration has not been run in ' +
        'this database. Nothing is inferred from its absence.</p>';
      body += audit('Audit — the demonstration contract', m);
      return card('demonstration', 'Demonstration',
        'software proof, in its own book', body);
    }
    rows.forEach(function (r) {
      body += '<div class="book"><h3 class="mono">' + dash(r.position_id) +
        '</h3><div class="lanenote">its own book — never added to ' +
        'autonomous performance</div><div class="facts three">' +
        fact('Bought', qty(r.bought_qty) + ' @ ' + cents(r.avg_buy_price)) +
        fact('Sold', qty(r.sold_qty) + ' @ ' + cents(r.avg_sell_price)) +
        fact('Held now', qty(r.held_qty)) +
        fact('Cost of purchases', usd(r.cost_of_purchases_usd)) +
        fact('Proceeds of sales', usd(r.proceeds_of_sales_usd)) +
        fact('Fees', usd(r.fees_usd)) +
        fact('Realised gross', signedUsd(r.realised_gross_usd),
             tone(r.realised_gross_usd)) +
        fact('Realised net of fees', signedUsd(r.realised_net_of_fees_usd),
             tone(r.realised_net_of_fees_usd)) +
        fact('Open inventory mark', dash(r.open_inventory_mark)) +
        fact('Decisions', dash(r.decisions)) +
        fact('Orders', dash(r.orders)) +
        fact('Fills', dash(r.fills)) +
        fact('Quantity identity', '<span class="pill ' +
          (r.quantity_identity_holds ? 'pill-good' : 'pill-bad') + '">' +
          (r.quantity_identity_holds ? 'BOUGHT − SOLD = HELD'
                                     : 'DOES NOT HOLD') + '</span>') +
        fact('Settlement rows', dash(r.outcome_rows)) +
        '</div>';
      var ds = r.decision_trail || [];
      if (ds.length) {
        body += '<h3 class="h3">Every management decision, in order</h3>' +
          '<div class="tablewrap"><table class="dt"><thead><tr>' +
          '<th>Decided at</th><th>Action</th><th class="num">Qty</th>' +
          '<th>Operating state</th><th class="num">Hold value</th>' +
          '<th>Reason</th></tr></thead><tbody>' + ds.map(function (x) {
            return '<tr><td class="mono">' + epochIso(x.decision_ts) +
              '</td><td>' + dash(x.selected_action) + '</td>' +
              '<td class="num">' + qty(x.selected_qty) + '</td><td>' +
              dash(x.operating_state) + '</td><td class="num">' +
              (num(x.hold_value_usd) === null ? '—'
                                              : usd(x.hold_value_usd)) +
              '</td><td class="muted">' + dash(x.selection_reason) +
              '</td></tr>';
          }).join('') + '</tbody></table></div>';
      }
      var os = r.order_trail || [];
      if (os.length) {
        body += '<h3 class="h3">Orders and the fills against them</h3>' +
          '<div class="tablewrap"><table class="dt"><thead><tr>' +
          '<th>Placed</th><th>Side</th><th class="num">Qty</th>' +
          '<th class="num">Limit</th><th class="num">Filled</th>' +
          '<th class="num">Avg fill</th><th class="num">Fees</th>' +
          '<th>State</th><th>Modelled</th></tr></thead><tbody>' +
          os.map(function (x) {
            return '<tr><td class="mono">' + epochIso(x.placed_at) +
              '</td><td>' + dash(x.side) + '</td><td class="num">' +
              qty(x.qty) + '</td><td class="num">' + cents(x.limit_price) +
              '</td><td class="num">' + qty(x.filled_qty) +
              '</td><td class="num">' + cents(x.avg_fill_price) +
              '</td><td class="num">' + usd(x.fees_usd) + '</td><td>' +
              dash(x.state) + '</td><td>' +
              (x.is_modelled ? '<span class="pill pill-warn">MODELLED' +
                 '</span>' : '<span class="pill pill-bad">CHECK</span>') +
              '</td></tr>';
          }).join('') + '</tbody></table></div>';
      }
      body += '</div>';
    });
    body += '<div class="facts" style="margin-top:12px">' +
      fact('Counts toward strategy performance',
           '<span class="pill pill-warn">NO</span>') +
      fact('Residual settlement', esc(String(m.residual_settlement || '—'))) +
      '</div>';
    body += audit('Audit — chosen inputs, deployed components and the ' +
                  'reconciliation as read', m);
    return card('demonstration', 'Demonstration',
      'software proof, in its own book', body);
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

  /* ── THE CONTROLS, AND THE CREDENTIAL THEY NEED ──────────────────────
   *
   * A CONTROL PANEL WHOSE BUTTONS DO NOTHING IS WORSE THAN NO PANEL, so
   * these send. They send to /api/command/bettor/control/{action} -- the
   * lane's existing control surface -- and each one reports the server's
   * READBACK rather than the fact that a request was made.
   *
   * THIS PAGE HOLDS NO CREDENTIAL. A read needs the Command session; a
   * write needs a scoped operator session, because anybody who may look at
   * the numbers must not thereby be able to halt the lane. Both are
   * HttpOnly cookies the server sets, so nothing is stored by this script
   * and nothing is ever put in a URL.
   */
  /* ── THE CONTROL CREDENTIAL: A SCOPED SESSION, NOT A SERVICE TOKEN ──
   *
   * WHAT CHANGED AND WHY. The first wiring asked the operator to paste the
   * ADMIN token into this page -- a service credential with the whole admin
   * API behind it, living in a browser tab. It is gone. The operator signs
   * in with the OPERATOR password, the server mints a SCOPED, short-lived
   * HttpOnly cookie that opens the desk's control actions and nothing else,
   * and this page never holds a credential of any kind: the password goes
   * straight to the session endpoint in one request body and is not kept.
   *
   * READS STILL NEED ONLY THE COMMAND SESSION, and a read credential is
   * refused for a write by the server, with a name. */
  var CONTROL_PATH = '/api/command/session/control';
  var LAST_CONTROL = null;
  var CONTROL_SESSION = null;     /* {expires_at} as the server reported it */

  function jsonHeaders() {
    return {Accept: 'application/json', 'Content-Type': 'application/json'};
  }

  function signInOperator() {
    var el = document.getElementById('oppass');
    var pass = el ? el.value : '';
    if (el) { el.value = ''; }          /* not kept, not even in the field */
    LAST_CONTROL = {action: 'operator sign-in', ok: null};
    render(LAST_DESK || {});
    fetch(CONTROL_PATH, {method: 'POST', credentials: 'same-origin',
                         headers: jsonHeaders(), cache: 'no-store',
                         body: JSON.stringify({password: pass})})
      .then(function (r) {
        return r.json().then(function (j) {
          return {status: r.status, body: j};
        }, function () { return {status: r.status, body: null}; });
      })
      .then(function (res) {
        var b = res.body || {};
        if (res.status === 503) {
          LAST_CONTROL = {action: 'operator sign-in', ok: false,
            refusal: (b.detail && b.detail.reason) || 'NOT_CONFIGURED',
            detail: b.detail || b};
        } else if (b.ok) {
          CONTROL_SESSION = {expires_at: b.expires_at, scope: b.scope};
          LAST_CONTROL = {action: 'operator sign-in', ok: true, detail: b};
        } else {
          /* 401 with `ok: false` -- the refusal is in the STATUS as well as
           * the body, so a caller cannot read a rejected password as a
           * session by looking at the status line alone. */
          LAST_CONTROL = {
            action: 'operator sign-in', ok: false,
            refusal: (b.detail && b.detail.reason) || null,
            error: 'that password was not accepted'};
        }
        load();
      })
      .catch(function (e) {
        LAST_CONTROL = {action: 'operator sign-in', ok: false,
                        error: e && e.message ? e.message : String(e)};
        render(LAST_DESK || {});
      });
  }

  function sendControl(action, payload) {
    var path;
    try { path = C.endpoint('/api/command/bettor/control/' + action); }
    catch (e) {
      LAST_CONTROL = {action: action, ok: false,
                      error: 'that control path is not an allowed endpoint'};
      render(LAST_DESK || {});
      return;
    }
    LAST_CONTROL = {action: action, ok: null, error: null,
                    sent_at: new Date().toISOString()};
    render(LAST_DESK || {});
    /* THE COOKIE CARRIES THE AUTHORISATION. No token is read from the page,
     * and none is put in the URL. */
    fetch(path, {method: 'POST', credentials: 'same-origin',
                 headers: jsonHeaders(), cache: 'no-store',
                 body: JSON.stringify(payload || {})})
      .then(function (r) {
        return r.json().then(function (j) {
          return {status: r.status, body: j};
        }, function () { return {status: r.status, body: null}; });
      })
      .then(function (res) {
        var b = res.body || {};
        var d = (b.detail && (b.detail.action || b.detail.refusal))
                ? b.detail : b;
        LAST_CONTROL = {
          action: action, status: res.status,
          /* THE SERVER'S OWN VERDICT. A 200 is not success: `ok` on the body
           * is the readback, and a refusal carries its name. 401 and 403
           * mean the control session is missing or is a read credential. */
          ok: (res.status >= 200 && res.status < 300) ? (b.ok === true)
                                                     : false,
          refusal: (b.detail && (b.detail.refusal || b.detail.reason))
                   || b.refusal || null,
          needs_sign_in: (res.status === 401 || res.status === 403),
          detail: d,
          at: new Date().toISOString()
        };
        load();
      })
      .catch(function (e) {
        LAST_CONTROL = {action: action, ok: false,
                        error: e && e.message ? e.message : String(e)};
        render(LAST_DESK || {});
      });
  }

  function controlResultHtml() {
    var r = LAST_CONTROL;
    if (!r) { return ''; }
    var d = r.detail || {};
    var cls = r.ok === true ? 'good' : r.ok === null ? '' : 'bad';
    /* THREE FACTS, NOT ONE. What was asked for, what the server read back
     * afterwards, and -- when it did not take -- why. A control panel that
     * collapses these into a tick is how a failed halt gets read as a
     * halt. */
    var what = r.ok === true
      ? (d.verdict ? 'APPLIED — ' + d.verdict : 'APPLIED')
      : r.ok === null ? 'SENT — awaiting the server readback'
      : (r.refusal ? 'REFUSED — ' + r.refusal : 'NOT APPLIED');
    var lines = '';
    if (d.requested !== undefined && d.requested !== null) {
      lines += '<div class="fact"><span class="k">requested</span>' +
        '<span class="v mono">' + esc(JSON.stringify(d.requested)) +
        '</span></div>';
    }
    if (d.applied !== undefined) {
      lines += '<div class="fact"><span class="k">applied (read back)' +
        '</span><span class="v mono">' +
        esc(d.applied === null ? 'nothing' : JSON.stringify(d.applied)) +
        '</span></div>';
    }
    if (d.failed) {
      lines += '<div class="fact"><span class="k">failed</span>' +
        '<span class="v mono">' + esc(JSON.stringify(d.failed)) +
        '</span></div>';
    }
    return '<div class="verdict ' + cls + '"><b>' + esc(r.action) + ' — ' +
      esc(what) + '</b>' + esc(r.error || '') + '</div>' +
      (lines ? '<div class="facts">' + lines + '</div>' : '') +
      (r.detail ? audit('Audit — the control response as read', r.detail)
                : '');
  }

  function funded(d) {
    var a = d.activation || {};
    var st = a.control_state || {};
    var lim = d.open_limitations || {};
    var prop = a.proposed_limits || {};
    var pl = prop.proposed || {};
    var acct = a.proposed_account || {};
    var rails = a.enforced_risk_rails || {};

    /* THE CHECKS ARE THE SERVER'S OWN, READ BACK. They used to be assembled
     * here from whatever fields the page could see, which meant the screen
     * and the endpoint could disagree about readiness. `activation.readiness`
     * is computed by `bettor_desk_controls.readiness` -- the same function
     * the activation endpoint refuses with -- so what is shown is what would
     * be enforced. A check the server could not evaluate is UNKNOWN and
     * blocks, exactly as an unevaluable risk rail does. */
    var ready = a.readiness || {};
    var checks = (ready.checks || []).map(function (c) {
      return {name: String(c.check || '').replace(/_/g, ' '),
              ok: c.met === true ? true : c.met === false ? false : null,
              detail: String(c.detail == null ? '' : c.detail)};
    });
    if (!checks.length) {
      checks = [{name: 'readiness', ok: null,
                 detail: 'the server did not return its readiness checks, ' +
                   'so activation is treated as blocked.'}];
    }
    var blocking = checks.filter(function (x) { return x.ok !== true; });

    var body = '<p class="muted">Every control below is an existing ' +
      'execution control, surfaced and live. This page submits no order: ' +
      'the controls that REMOVE authority work, and the one that would ' +
      'GRANT it — funded activation — stays locked while any check is ' +
      'unresolved.</p>';

    body += '<ul class="checks">' + checks.map(function (x) {
      var pill = x.ok === true ? '<span class="pill pill-good">PASS</span>'
        : x.ok === false ? '<span class="pill pill-bad">BLOCKING</span>'
        : '<span class="pill pill-warn">UNKNOWN</span>';
      return '<li>' + pill + '<span class="cw"><b>' + esc(x.name) +
        '</b><span>' + esc(x.detail) + '</span></span></li>';
    }).join('') + '</ul>';

    /* LIVE CONTROL STATE, READ FROM THE ROWS. */
    var ra = st.research_lane_armed || {};
    var fp = st.funded_executor_paused || {};
    body += '<h3 class="h3">Control state, as the rows read now</h3>' +
      '<div class="facts three limits">' +
      fact('Research lane', '<span class="pill ' +
        (ra.value === true ? 'pill-blue' : 'pill-warn') + '">' +
        (ra.value === true ? 'ARMED' : ra.value === false ? 'PAUSED' : '—') +
        '</span>') +
      fact('Funded executor', '<span class="pill ' +
        (fp.value === true ? 'pill-good' : 'pill-bad') + '">' +
        (fp.value === true ? 'PAUSED' : fp.value === false ? 'NOT PAUSED'
                                                           : '—') +
        '</span>') +
      fact('Working modelled orders', dash(st.working_modelled_orders)) +
      /* WHAT THE NUMBER COUNTS, ON ITS LABEL. `live_orders` is the funded
       * lane's own table and its rows are HISTORY -- production holds
       * 166,585 from the earlier live beta. Calling that "funded orders"
       * beside a research desk invited the worst possible misreading. */
      fact('Orders this lane submitted', '<span class="pill pill-good">' +
        dash(st.funded_orders_this_lane_submitted) + '</span>') +
      fact('live_orders rows (funded lane history)',
        '<span class="mono">' + dash(st.live_orders_rows_all_time) +
        '</span>') +
      /* THE ID IS THE IDENTITY. A display name is recorded for the audit
       * and decides nothing: the account is resolved against the canonical
       * registry by id, and the paused account is stopped by its own row. */
      fact('Bound account', acct.account_id
        ? '<span class="mono">' + esc(acct.account_id) + '</span>' +
          (acct.name ? ' ' + esc(acct.name) : '') +
          ' <span class="pill ' +
          (acct.venue_class === 'TEST' ? 'pill-blue' : 'pill-warn') + '">' +
          esc(acct.venue_class || 'VENUE CLASS UNKNOWN') + '</span>'
        : '<span class="pill pill-warn">NONE RECORDED</span>') +
      /* AND WHETHER IT HAS BEEN AUTHORISED, which is a separate record. */
      fact('Authorization', (st.authorization && st.authorization.venue_class)
        ? '<span class="pill pill-good">' +
          esc(st.authorization.venue_class) + ' VENUE AUTHORISED</span>' +
          ' <span class="mono">' + esc(st.authorization.venue || '') +
          '</span>'
        : '<span class="pill pill-warn">NONE</span>') +
      fact('Enforced rail digest', '<span class="mono">' +
        dash(rails.limitsSha) + '</span>') +
      '</div>';

    /* THE OPERATOR SIGN-IN. No service credential, and none is stored. */
    var signedIn = CONTROL_SESSION && CONTROL_SESSION.expires_at;
    body += '<h3 class="h3">Operator session</h3><div class="optoken">' +
      '<input id="oppass" type="password" autocomplete="current-password" ' +
      'spellcheck="false" placeholder="operator password">' +
      '<button type="button" id="btn-signin">Start control session</button>' +
      '<span class="why">' +
      (signedIn
        ? 'Control session active until ' +
          esc(epochIso(CONTROL_SESSION.expires_at)) + ', scope ' +
          esc(CONTROL_SESSION.scope || 'control') + '. '
        : 'Controls need an operator session. ') +
      'The password is sent once to this origin\u2019s session endpoint; ' +
      'the server returns a scoped, short-lived HttpOnly cookie that opens ' +
      'the control actions and nothing else. This page holds no admin ' +
      'token and no service credential, and stores nothing.</span></div>';

    body += controlResultHtml();

    body += '<div class="activate">' +
      /* IT SENDS, DELIBERATELY. A disabled button establishes nothing --
       * anybody can POST -- so the request is made and the SERVER's refusal,
       * with the prerequisites it found unmet, is what appears. */
      '<button type="button" id="btn-activate">Request funded activation' +
      '</button>' +
      '<button type="button" id="btn-pause">Pause research lane</button>' +
      '<button type="button" id="btn-resume">Resume research lane</button>' +
      '<button type="button" id="btn-cancel">Cancel working orders' +
      '</button><button type="button" id="btn-halt" class="danger">' +
      'Emergency halt</button>' +
      '<span class="why">Activation is refused by the server while ' +
      esc(blocking.length) + ' check(s) are unmet: ' +
      esc(blocking.map(function (x) { return x.name; }).join('; ')) +
      '. Pressing it returns that refusal rather than activating ' +
      'anything.</span></div>';

    /* THE TWO PROPOSAL FORMS. They record intent; they enforce nothing. */
    body += '<h3 class="h3">Account configuration</h3>' +
      '<div class="ctlform">' +
      '<input id="acct-name" placeholder="account name" value="' +
      esc(acct.name || '') + '">' +
      '<input id="acct-venue" placeholder="venue" value="' +
      esc(acct.venue || 'PMUS') + '">' +
      '<input id="acct-id" placeholder="account id (optional)" value="' +
      esc(acct.account_id || '') + '">' +
      '<button type="button" id="btn-account">Record account</button>' +
      '<span class="why">Recording names an account for the activation ' +
      'checklist. It binds nothing, switches nothing, and cannot select ' +
      'the paused ACCOUNTING_UNCERTAIN account.</span></div>';

    body += '<h3 class="h3">Limits</h3><div class="ctlform">' +
      '<input id="lim-capital" inputmode="decimal" placeholder="capital $" ' +
      'value="' + esc(pl.capital_usd == null ? '' : pl.capital_usd) + '">' +
      '<input id="lim-order" inputmode="decimal" ' +
      'placeholder="per order $" value="' +
      esc(pl.per_order_usd == null ? '' : pl.per_order_usd) + '">' +
      '<input id="lim-expo" inputmode="decimal" ' +
      'placeholder="max exposure $" value="' +
      esc(pl.max_exposure_usd == null ? '' : pl.max_exposure_usd) + '">' +
      '<input id="lim-loss" inputmode="decimal" ' +
      'placeholder="daily loss stop $" value="' +
      esc(pl.daily_loss_stop_usd == null ? '' : pl.daily_loss_stop_usd) +
      '">' +
      '<button type="button" id="btn-limits">Record limits</button>' +
      '<span class="why">All four are required. These are the owner’s ' +
      'stated limits for the checklist — the ENFORCED rails are frozen in ' +
      'code and are shown in the audit below.</span></div>';

    body += '<h3 class="h3">Open limitations, stated</h3><ul class="openlim">' +
      Object.keys(lim).filter(function (k) {
        return typeof lim[k] === 'string';
      }).map(function (k) {
        return '<li><b>' + esc(k) + '</b> — ' + esc(lim[k]) + '</li>';
      }).join('') + '</ul>';

    body += audit('Audit — the control contract, the live rows and the ' +
                  'enforced rails', a);

    return card('funded', 'Activation',
      'controls are live; activation stays locked', body);
  }

  /* THE BUTTONS ARE BOUND AFTER EVERY RENDER, because the card is
   * re-rendered from scratch on each read and a listener bound to a removed
   * node is a control that silently stopped working. */
  function bindControls() {
    var pw = document.getElementById('oppass');
    if (pw) {
      pw.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { signInOperator(); }
      });
    }
    function on(id, fn) {
      var el = document.getElementById(id);
      if (el) { el.addEventListener('click', fn); }
    }
    function val(id) {
      var el = document.getElementById(id);
      return el ? el.value : '';
    }
    on('btn-signin', signInOperator);
    on('btn-activate', function () {
      sendControl('activate', {by: 'DESK'});
    });
    on('btn-pause', function () { sendControl('pause', {by: 'DESK'}); });
    on('btn-resume', function () {
      sendControl('resume', {by: 'DESK', scope: 'research'});
    });
    on('btn-cancel', function () {
      sendControl('cancel-working-orders', {by: 'DESK'});
    });
    on('btn-halt', function () {
      /* AN EMERGENCY STOP IS CONFIRMED ONCE. It is durable and it stops
       * three separate things; a stray click must not take the lane down. */
      if (window.confirm('Emergency halt: stop the research lane, pause ' +
                         'the funded executor, cancel working modelled ' +
                         'orders and take the durable operator stop?')) {
        sendControl('halt', {by: 'DESK', reason: 'OPERATOR_HALT_FROM_DESK'});
      }
    });
    on('btn-account', function () {
      sendControl('account', {by: 'DESK', account: {
        name: val('acct-name'), venue: val('acct-venue'),
        account_id: val('acct-id')}});
    });
    on('btn-limits', function () {
      sendControl('limits', {by: 'DESK', limits: {
        capital_usd: val('lim-capital'),
        per_order_usd: val('lim-order'),
        max_exposure_usd: val('lim-expo'),
        daily_loss_stop_usd: val('lim-loss')}});
    });
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

    LAST_DESK = d;
    host.innerHTML = controls(d) + opportunities(d) + orders(d) +
      positions(d) + management(d) + demonstration(d) + performance(d) +
      funded(d);
    host.hidden = false;
    if (state) { state.hidden = true; }
    bindControls();
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
