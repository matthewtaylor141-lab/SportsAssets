"""THE OPERATING DESK, SERVED FROM THE API'S OWN ORIGIN.

WHY HERE AND NOT ONLY ON THE STATIC SITE. The static Command bundle lives
under `frontend/public/command/` and reaches its users through Netlify,
which builds from the branch Netlify tracks. This repository has no Netlify
credential -- no NETLIFY_AUTH_TOKEN and no site id among its secrets -- so
that publish is not a step this session can take. The API origin IS
deployable, through the same API-only release route every backend change
uses, and it is SAME-ORIGIN with `/api/command/*` by construction rather
than by a proxy rule. So the desk is served here too, and that is the URL
that works today.

IT IS GENERATED, NOT HAND-COPIED. `tools/build_desk_page.py` inlines
`desk.html`, `desk.css` and `desk.js` from the static bundle; a test
compares the two for drift on the substantive markers. Editing this file by
hand is how the two copies would diverge.

IT IS READ-ONLY AND GATED. The route sits behind `require_command` like
every other COMMAND read, sends no order, holds no credential, and its only
fetch is the one same-origin desk read.
"""

DESK_PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#090d14">
  <meta name="description" content="Bettor EV Engine — the operating desk: opportunities, orders, positions, management, performance and controls.">
  <title>Bettor EV Engine | Operating Desk</title>
  <style>/* BETTOR EV ENGINE · OPERATING DESK — presentation only.
 *
 * It styles what `/api/command/bettor/desk` returns and computes nothing.
 * Every figure on this page comes from that read; there is no display
 * arithmetic that could disagree with the accounting ledger.
 *
 * The palette is taken from command.css's own surfaces so the two pages
 * read as one product rather than two. Tokens are declared once here and
 * every rule below uses them. */

:root {
  --dk-bg: #090d14;
  --dk-surface: #0f141c;
  --dk-surface-2: #141b25;
  --dk-line: #1e2733;
  --dk-line-soft: #18202b;
  --dk-ink: #e6ebf2;
  --dk-ink-2: #9fb0c4;
  --dk-ink-3: #64788f;
  --dk-accent: #4c9ffe;
  --dk-good: #3ddc97;
  --dk-warn: #f2b544;
  --dk-bad: #ff6b6b;
  --dk-radius: 10px;
}

* { box-sizing: border-box; }

.deskbody {
  margin: 0;
  background: var(--dk-bg);
  color: var(--dk-ink);
  font: 14px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI",
        Roboto, sans-serif;
  -webkit-font-smoothing: antialiased;
}

/* ── the bar ─────────────────────────────────────────────────────── */
.deskbar {
  display: flex; flex-wrap: wrap; gap: 12px 20px;
  align-items: center; justify-content: space-between;
  padding: 14px 20px;
  border-bottom: 1px solid var(--dk-line);
  background: linear-gradient(180deg, #0c1119, #090d14);
  position: sticky; top: 0; z-index: 5;
}
/* THE BRAND ROW MUST WRAP. At 390px an unwrapped row of a mark, a name and
 * two pills measured 442px and pushed the whole document sideways -- the
 * page-level horizontal scroll a browser check caught. It wraps, the name
 * may shrink, and the pills keep their own line if they need one. */
.deskbar-brand {
  display: flex; align-items: center; gap: 8px 10px;
  flex-wrap: wrap; min-width: 0; max-width: 100%;
}
.deskbar-brand .pill { flex: 0 1 auto; min-width: 0; }
.deskname { min-width: 0; overflow-wrap: anywhere; }
.deskmark {
  width: 28px; height: 28px; border-radius: 8px;
  display: grid; place-items: center;
  background: var(--dk-accent); color: #04121f;
  font-weight: 700; font-size: 15px;
}
.deskname { font-weight: 650; letter-spacing: .2px; }
.desknav { display: flex; flex-wrap: wrap; gap: 4px; }
.desknav a {
  color: var(--dk-ink-2); text-decoration: none;
  padding: 6px 10px; border-radius: 8px; font-size: 13px;
}
.desknav a:hover, .desknav a:focus-visible {
  color: var(--dk-ink); background: var(--dk-surface-2); outline: none;
}

/* ── layout ──────────────────────────────────────────────────────── */
.deskmain {
  padding: 20px 16px; max-width: 1180px; margin: 0 auto; min-width: 0;
}
.deskstate { color: var(--dk-ink-2); padding: 24px 0; }
.deskfoot {
  display: flex; flex-wrap: wrap; gap: 8px 20px;
  justify-content: space-between;
  padding: 16px 20px 28px; color: var(--dk-ink-3); font-size: 12px;
  border-top: 1px solid var(--dk-line-soft); margin-top: 8px;
}

.card {
  background: var(--dk-surface);
  border: 1px solid var(--dk-line);
  border-radius: var(--dk-radius);
  margin: 0 0 16px;
  overflow: hidden;
}
.card > h2 {
  margin: 0; padding: 13px 16px; font-size: 14px; font-weight: 640;
  border-bottom: 1px solid var(--dk-line-soft);
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
}
.card > h2 .sub {
  font-weight: 400; font-size: 12px; color: var(--dk-ink-3);
}
.card-body { padding: 14px 16px; }

/* facts: a label/value grid that stays readable at phone width */
.facts { display: grid; gap: 10px 18px; grid-template-columns: 1fr; }
@media (min-width: 640px) {
  .facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .facts.three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
.fact { min-width: 0; }
.fact .k {
  display: block; font-size: 11px; letter-spacing: .06em;
  text-transform: uppercase; color: var(--dk-ink-3);
}
.fact .v {
  display: block; font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}

/* pills */
.pill {
  display: inline-block; padding: 2px 9px; border-radius: 999px;
  font-size: 11px; font-weight: 600; letter-spacing: .04em;
  border: 1px solid transparent; white-space: nowrap;
}
.pill-blue { background: #10263c; color: #8fc4ff; border-color: #1d3e5e; }
.pill-good { background: #102b22; color: var(--dk-good); border-color: #1d4437; }
.pill-warn { background: #2e2413; color: var(--dk-warn); border-color: #4a3a18; }
.pill-bad  { background: #2e1616; color: var(--dk-bad);  border-color: #4a2020; }
.pill-grey { background: #171d26; color: var(--dk-ink-2); border-color: #242e3b; }

/* tables */
/* A wide table scrolls IN ITS OWN CONTAINER. The page never scrolls
 * sideways; the table does. */
.tablewrap { overflow-x: auto; max-width: 100%; min-width: 0; }
.card, .card-body, .book { min-width: 0; max-width: 100%; }
h3.h3 { font-size: 13px; margin: 16px 0 6px; }
table.dt { width: 100%; border-collapse: collapse; font-size: 13px; }
table.dt th, table.dt td {
  text-align: left; padding: 8px 10px;
  border-bottom: 1px solid var(--dk-line-soft); white-space: nowrap;
}
table.dt th {
  font-size: 11px; letter-spacing: .06em; text-transform: uppercase;
  color: var(--dk-ink-3); font-weight: 600;
}
table.dt td.num, table.dt th.num {
  text-align: right; font-variant-numeric: tabular-nums;
}
table.dt tr:last-child td { border-bottom: 0; }
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}
.muted { color: var(--dk-ink-3); }
.good { color: var(--dk-good); }
.bad  { color: var(--dk-bad); }
.warn { color: var(--dk-warn); }

/* the NO_TRADE banner: a result, stated, never an empty panel */
.verdict {
  border-radius: 8px; padding: 12px 14px; margin: 0 0 12px;
  border: 1px solid #4a3a18; background: #221b0e; color: #ffd98a;
}
.verdict.good { border-color: #1d4437; background: #0f241d; color: #9ff0cd; }
.verdict b { display: block; font-size: 15px; margin-bottom: 2px; }

/* books: separate, never summed */
.book { border: 1px solid var(--dk-line); border-radius: 8px;
        padding: 10px 12px; margin: 0 0 10px; background: var(--dk-surface-2); }
.book h3 { margin: 0 0 6px; font-size: 13px; }
.book .lanenote { font-size: 12px; color: var(--dk-ink-3); }

/* audit: internal identifiers live here and nowhere else */
details.audit {
  border-top: 1px dashed var(--dk-line); margin-top: 12px; padding-top: 10px;
}
details.audit > summary {
  cursor: pointer; color: var(--dk-ink-2); font-size: 12px;
  letter-spacing: .04em; text-transform: uppercase;
}
details.audit > summary:focus-visible { outline: 1px solid var(--dk-accent); }
details.audit pre {
  margin: 10px 0 0; padding: 10px; overflow-x: auto;
  background: #0b1016; border: 1px solid var(--dk-line-soft);
  border-radius: 8px; font-size: 12px; color: var(--dk-ink-2);
}

/* readiness checklist */
ul.checks { list-style: none; margin: 0; padding: 0; }
ul.checks li {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 8px 0; border-bottom: 1px solid var(--dk-line-soft);
}
ul.checks li:last-child { border-bottom: 0; }
ul.checks .cw { min-width: 0; }
ul.checks .cw b { display: block; font-weight: 600; font-size: 13px; }
ul.checks .cw span { color: var(--dk-ink-3); font-size: 12px; }

/* the activation control, locked */
.activate {
  margin-top: 12px; display: flex; gap: 12px; align-items: center;
  flex-wrap: wrap;
}
.activate button {
  padding: 9px 16px; border-radius: 8px; font: inherit; font-weight: 600;
  border: 1px solid #2b3644; background: #161d27; color: var(--dk-ink-3);
}
.activate button[disabled] { cursor: not-allowed; opacity: .75; }
/* A LIVE CONTROL LOOKS LIVE. The locked one keeps the muted treatment
 * above; the ones that actually send are legible as buttons, and the
 * emergency stop is the only red thing on the page. */
.activate button:not([disabled]) {
  cursor: pointer; color: var(--dk-ink-1); border-color: #3a4757;
  background: #1b2430;
}
.activate button:not([disabled]):hover { background: #222d3b; }
.activate button.danger:not([disabled]) {
  border-color: #6b2531; background: #2a1419; color: #ffd9de;
}
.activate button:focus-visible {
  outline: 2px solid var(--dk-accent, #6aa9ff); outline-offset: 2px;
}
.activate .why { font-size: 12px; color: var(--dk-warn); }

/* the operator token field and the two proposal forms */
.optoken, .ctlform {
  margin-top: 8px; display: flex; gap: 10px; align-items: center;
  flex-wrap: wrap;
}
.optoken input, .ctlform input {
  padding: 8px 10px; border-radius: 8px; font: inherit; font-size: 13px;
  border: 1px solid #2b3644; background: #10151d; color: var(--dk-ink-1);
  min-width: 0; flex: 1 1 170px; max-width: 320px;
}
.optoken input:focus-visible, .ctlform input:focus-visible {
  outline: 2px solid var(--dk-accent, #6aa9ff); outline-offset: 1px;
}
.ctlform button {
  padding: 8px 14px; border-radius: 8px; font: inherit; font-weight: 600;
  cursor: pointer; border: 1px solid #3a4757; background: #1b2430;
  color: var(--dk-ink-1);
}
.optoken .why, .ctlform .why {
  flex: 1 1 100%; font-size: 12px; color: var(--dk-ink-3);
}

.limits { font-size: 12px; }
.openlim { font-size: 12.5px; color: var(--dk-ink-2); }
.openlim li { margin-bottom: 8px; }

@media (prefers-reduced-motion: reduce) {
  * { animation: none !important; transition: none !important; }
}
</style>
</head>
<body class="deskbody">
  <a class="skip-link" href="#deskmain">Skip to the desk</a>

  <header class="deskbar">
    <div class="deskbar-brand">
      <span class="deskmark">B</span>
      <span class="deskname">Bettor EV Engine</span>
      <span id="mode-pill" class="pill pill-blue">MODE —</span>
      <span id="funded-pill" class="pill pill-grey">FUNDED —</span>
    </div>
    <nav class="desknav" aria-label="Desk sections">
      <a href="#controls">Controls</a>
      <a href="#opportunities">Opportunities</a>
      <a href="#orders">Orders</a>
      <a href="#positions">Positions</a>
      <a href="#management">Management</a>
      <a href="#performance">Performance</a>
      <a href="#demonstration">Demonstration</a>
      <a href="#funded">Activation</a>
      <a href="/command/">Command</a>
    </nav>
  </header>

  <main id="deskmain" class="deskmain">
    <div id="deskstate" class="deskstate" role="status" aria-live="polite">
      Opening the desk…
    </div>
    <div id="desk" hidden></div>
  </main>

  <footer class="deskfoot">
    <span id="asof">—</span>
    <span>Reads need only the Command session. The controls send to the lane’s existing control paths and need an operator token held in memory only. This page submits no order to any venue.</span>
  </footer>

  <script>
window.BTCore = {
  esc: function (v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
              "'": "&#39;"}[c];
    });
  },
  usd: function (v, d) {
    d = (d === undefined) ? 0 : d;
    return new Intl.NumberFormat("en-US", {
      style: "currency", currency: "USD",
      minimumFractionDigits: d, maximumFractionDigits: d}).format(v);
  },
  endpoint: function (path) {
    if (typeof path !== "string"
        || !/^\/api\/command(?:\/|$)/.test(path)
        || path.indexOf("..") >= 0 || path.indexOf("\\") >= 0
        || /[?#]/.test(path)) {
      throw new Error("Only configured same-origin /api/command/ read " +
                      "endpoints are allowed.");
    }
    return path;
  }
};
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
   * THE OPERATOR TOKEN IS HELD IN MEMORY AND NOWHERE ELSE. It is typed per
   * session, kept in this closure, never written to localStorage, never
   * placed in a URL, and it is gone when the tab closes. The desk's READ
   * needs only the Command session; a WRITE needs this token, because
   * anybody who may look at the numbers must not thereby be able to halt
   * the lane.
   */
  var OPTOKEN = '';
  var LAST_CONTROL = null;

  function controlHeaders() {
    var h = {Accept: 'application/json',
             'Content-Type': 'application/json'};
    if (OPTOKEN) { h['X-Admin-Token'] = OPTOKEN; }
    return h;
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
    if (!OPTOKEN) {
      LAST_CONTROL = {action: action, ok: false,
                      error: 'enter the operator token first — a control ' +
                             'action needs it and the desk never stores it'};
      render(LAST_DESK || {});
      return;
    }
    LAST_CONTROL = {action: action, ok: null, error: null,
                    sent_at: new Date().toISOString()};
    render(LAST_DESK || {});
    fetch(path, {method: 'POST', credentials: 'same-origin',
                 headers: controlHeaders(), cache: 'no-store',
                 body: JSON.stringify(payload || {})})
      .then(function (r) {
        return r.json().then(function (j) {
          return {status: r.status, body: j};
        }, function () { return {status: r.status, body: null}; });
      })
      .then(function (res) {
        var b = res.body || {};
        LAST_CONTROL = {
          action: action, status: res.status,
          /* THE SERVER'S OWN VERDICT. A 200 is not success: `ok` on the
           * body is the readback, and a refusal carries its name. */
          ok: (res.status >= 200 && res.status < 300) ? (b.ok === true)
                                                     : false,
          refusal: (b.detail && b.detail.reason) || b.refusal || null,
          detail: b,
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
    var cls = r.ok === true ? 'good' : r.ok === null ? '' : 'bad';
    var what = r.ok === true ? 'TOOK' : r.ok === null ? 'SENT — awaiting the '
      + 'readback' : (r.refusal ? 'REFUSED — ' + r.refusal
                                : 'DID NOT TAKE');
    return '<div class="verdict ' + cls + '"><b>' + esc(r.action) + ' — ' +
      esc(what) + '</b>' + esc(r.error || '') +
      (r.detail ? '</div>' + audit('Audit — the control response as read',
                                   r.detail)
                : '</div>');
  }

  function funded(d) {
    var a = d.activation || {};
    var st = a.control_state || {};
    var lim = d.open_limitations || {};
    var prop = a.proposed_limits || {};
    var pl = prop.proposed || {};
    var acct = a.proposed_account || {};
    var rails = a.enforced_risk_rails || {};

    /* THE CHECKS ARE DERIVED FROM THE READ, NOT DECLARED HERE. A check this
     * page cannot evaluate is UNKNOWN and blocks, exactly as an unevaluable
     * risk rail does. */
    var checks = [
      {name: 'Funded submission disabled',
       ok: d.funded_submission === 'DISABLED',
       detail: 'The lane writes with order_submitted CHECK FALSE and the ' +
         'venue-boundary gate authorises every submission independently ' +
         'of this page.'},
      {name: 'Account named',
       ok: a.account_named === true ? true : null,
       detail: a.account_named
         ? ('Recorded: ' + String(acct.name || '—') + ' at ' +
            String(acct.venue || '—') + '. Recording a name is not ' +
            'approving it.')
         : ('No funded account is recorded. Use the account control below; ' +
            'the ACCOUNTING_UNCERTAIN account is refused by name.')},
      {name: 'Account approved by the owner',
       ok: a.account_approved_by_owner === true,
       detail: 'An account this panel recorded is the owner’s stated ' +
         'intent. Approval is the owner’s act and is not granted here.'},
      {name: 'Limit set recorded',
       ok: a.limits_recorded === true ? true : null,
       detail: a.limits_recorded
         ? ('Capital ' + usd(pl.capital_usd) + ', per order ' +
            usd(pl.per_order_usd) + ', exposure ' +
            usd(pl.max_exposure_usd) + ', daily loss stop ' +
            usd(pl.daily_loss_stop_usd) + '. PROPOSED — the enforced ' +
            'rails are frozen in code and are unchanged.')
         : 'No limit set is recorded. All four limits are required.'},
      {name: 'Limits approved by the owner',
       ok: a.limits_approved_by_owner === true,
       detail: 'A recorded limit set does not become an enforced rail. The ' +
         'enforced rails are in the audit below with their own digest.'},
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
      fact('Funded orders', '<span class="pill ' +
        (st.funded_orders === 0 ? 'pill-good' : 'pill-bad') + '">' +
        dash(st.funded_orders) + '</span>') +
      fact('Bound account', acct.name
        ? esc(acct.name) + ' <span class="pill pill-warn">RECORDED, NOT ' +
          'APPROVED</span>'
        : '<span class="pill pill-warn">NONE RECORDED</span>') +
      fact('Enforced rail digest', '<span class="mono">' +
        dash(rails.limitsSha) + '</span>') +
      '</div>';

    /* THE OPERATOR CREDENTIAL. In memory only, and said so. */
    body += '<h3 class="h3">Operator token</h3><div class="optoken">' +
      '<input id="optoken" type="password" autocomplete="off" ' +
      'spellcheck="false" placeholder="operator token (held in memory ' +
      'only)" value="' + esc(OPTOKEN) + '">' +
      '<span class="why">A control action needs this token. It is kept in ' +
      'this page’s memory, never stored, never put in a URL, and gone when ' +
      'the tab closes. Reads need only the Command session.</span></div>';

    body += controlResultHtml();

    body += '<div class="activate">' +
      '<button type="button" id="btn-activate" disabled ' +
      'aria-disabled="true">Activate funded trading</button>' +
      '<button type="button" id="btn-pause">Pause research lane</button>' +
      '<button type="button" id="btn-resume">Resume research lane</button>' +
      '<button type="button" id="btn-cancel">Cancel working orders' +
      '</button><button type="button" id="btn-halt" class="danger">' +
      'Emergency halt</button>' +
      '<span class="why">Activation locked — ' + esc(blocking.length) +
      ' unresolved check(s): ' +
      esc(blocking.map(function (x) { return x.name; }).join('; ')) +
      '</span></div>';

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
    var tok = document.getElementById('optoken');
    if (tok) {
      tok.addEventListener('input', function () { OPTOKEN = tok.value; });
    }
    function on(id, fn) {
      var el = document.getElementById(id);
      if (el) { el.addEventListener('click', fn); }
    }
    function val(id) {
      var el = document.getElementById(id);
      return el ? el.value : '';
    }
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
</script>
</body>
</html>
"""

#: The markers a drift test holds both copies to.
CONTRACT_MARKERS = (
    "Bettor EV Engine", "Controls", "Opportunities", "Orders", "Positions",
    "Management", "Performance", "Activation", "NO_TRADE",
    "/api/command/bettor/desk", "audit",
)
