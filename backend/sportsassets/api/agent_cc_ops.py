"""THE PAPER EXPERIMENT AS THE DEFAULT VIEW OF THE DEREK, XAVIER AND AUDREY PAGES.

The three agent pages used to lead with the FUNDED system (derek_entry_
decisions, the funded book, FUNDED_LANE_NOT_CONFIGURED) under a paper banner,
so management saw "zero decisions" and "no funded book" while the paper
session held hundreds of decisions. This module makes the ACTIVE PAPER
SESSION the headline of every page:

  1. THREE FRESHNESS STAMPS, never conflated (`fresh_html`, `CC.ops.fresh`):
     the last successful server read (the page's own clock), the paper
     runtime's own heartbeat (paper_session_health), and the last committed
     ledger transaction (max committed_at in the one paper ledger). An
     unchanged cash balance is not stale; a recent read is not agent health.
  2. THE BRIEF'S KEY FIGURES are paper figures (`CC.ops.kpis`).
  3. THE OPERATIONAL PANELS (`ops_html`, `CC.ops.panels`), read from ONE GET
     of /api/command/paper/operations?agent=<agent> (bettor_paper_ops):
       Derek   the EXPERIMENTAL benchmark decisions -- each policy with its
               version and labels (the completed-game policy's CONDITIONAL /
               EXPERIMENTAL label) -- SEPARATELY from his original two-model
               research decisions; counts, the eligibility funnel, refusals
               and the recent decisions with verdict, refusal, edge and the
               one-line explanation;
       Xavier  every paper position handed to him with its strategy, his
               reviews, standing protection orders, settlements (including
               SETTLED_AT_VENUE_PRICE) and what is pending;
       Audrey  the paper account (cash, reserved, equity, realized and
               unrealized P&L by strategy), her audit findings and the daily
               report.
  4. THE CHARACTER'S POSE follows the PAPER RUNTIME's heartbeat (`CC.ops.
     mode`); the funded agent_status record is reported only in the funded
     section.

The funded panels are kept, unchanged, inside a collapsed section labelled
"Funded system (inactive)" -- secondary, never the headline.

A FAILED READ IS NEVER A ZERO: a failed GET renders UNAVAILABLE with the
reason, a 401 renders SIGN-IN REQUIRED, an EMPTY section says "nothing
recorded" with the server's reason. When a later poll fails, the last
successful read stays on screen with the failure and its time stated above
it.
"""
from __future__ import annotations

import html as _html

OPS_ROUTE = "/api/command/paper/operations"

#: The panels each page's ops read fills, in page order: (id, title, source)
OPS_PANELS = {
    "derek": (
        ("p-ops-standing", "Derek's standing entry orders",
         "paper_orders (role ENTRY, open): resting maker bids and marketable "
         "entries, with price, size, rationale, expiry and cancellation "
         "conditions"),
        ("p-ops-bench", "Experimental benchmark decisions",
         "paper_decisions where strategy is PINNACLE_ONLY_PAPER_BENCHMARK or "
         "PINNACLE_COMPLETED_GAME_PAPER"),
        ("p-ops-research", "Derek's original research decisions",
         "paper_decisions where strategy is DEREK_ENTRY_POLICY_V2"),
    ),
    "xavier": (
        ("p-ops-handoffs", "Positions Xavier owns",
         "paper_handoffs (every one, with its strategy), the positions of "
         "the one paper ledger, his latest review per position and the "
         "entry decision's scenario economics"),
        ("p-ops-reviews", "Xavier's management reviews",
         "paper_xavier_reviews"),
        ("p-ops-protection", "Standing protection and management orders",
         "paper_orders (role other than ENTRY)"),
        ("p-ops-settlements", "Settlements and what is pending",
         "paper_settlements (WON, LOST, VOID_REFUND, SETTLED_AT_VENUE_PRICE) "
         "and open positions awaiting settlement"),
    ),
    "audrey": (
        ("p-ops-account", "Paper account performance",
         "bettor_paper_ledger.balances over the one paper ledger; P&amp;L by "
         "the strategy each position keeps for life"),
        ("p-ops-performance", "Agent performance by strategy",
         "paper_decisions, paper_orders and the positions of the one paper "
         "ledger, per strategy"),
        ("p-ops-opsaudit", "Operational audit: recommendations to Derek and "
         "Xavier",
         "paper_recommendations and their events (migration 189): Audrey's "
         "evidence, the owner's response and every later measurement; "
         "OPERATIONAL, never a claim of profitable learning"),
        ("p-ops-findings", "Audrey's event-driven audits and findings",
         "paper_audrey_findings (event audits from the paper learning "
         "record: first fill, handoff, management fill, settlement, "
         "SETTLED_AT_VENUE_PRICE, exceptional outcome, ledger)"),
        ("p-ops-learning", "Lessons and improvement proposals",
         "GET /api/command/paper/learning (agents.paper_learning): lessons, "
         "proposals, evaluation status, activation state"),
        ("p-ops-report", "Audrey's daily report",
         "paper_audrey_reports (latest version per day)"),
    ),
}

OPS_TITLES = {"derek": "Derek's paper decisions",
              "xavier": "Xavier's paper portfolio management",
              "audrey": "Audrey's paper audit and account"}

OPS_CSS = r"""
.cc-fresh{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:12px;overflow:hidden;margin:0 0 14px}
.cc-fresh>div{background:var(--panel);padding:10px 12px;min-width:0}
.cc-fresh .fv{font:600 14px/1.3 var(--display);margin-top:4px;overflow-wrap:anywhere}
.cc-fresh .fs{font:11.5px/1.35 var(--mono);color:var(--ink-3);margin-top:3px;overflow-wrap:anywhere}
.cc-fresh .fchip{display:inline-block;font:700 9.5px/1 var(--mono);letter-spacing:.08em;padding:3px 5px;border-radius:4px;border:1px solid currentColor;margin-left:6px;vertical-align:middle}
.fchip.RECENT,.fchip.LIVE,.fchip.OK{color:var(--ok)}.fchip.STALE,.fchip.POLLING,.fchip.RECONNECTING{color:var(--warn)}.fchip.UNAVAILABLE,.fchip.SIGN-IN{color:var(--bad)}
.cc-fresh .fconn{grid-column:1/-1;font:11.5px/1.35 var(--mono);color:var(--ink-2);background:var(--panel-2)}
.cc-oph{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px}
.ops-stale{margin:0 0 10px;padding:8px 10px;border-radius:9px;border:1px solid var(--warn);color:var(--warn);font:600 12px/1.4 var(--mono);overflow-wrap:anywhere}
.ops-fail .big{color:var(--bad)}
.strat{border:1px solid var(--line-2);border-radius:14px;background:var(--panel-2);padding:12px 14px;margin-bottom:14px;min-width:0}
.strat-h h3{margin:6px 0 4px;font:650 15.5px/1.3 var(--display)}
.strat-meta{font:11.5px/1.45 var(--mono);color:var(--ink-3);overflow-wrap:anywhere}
.schip{display:inline-block;font:700 10px/1 var(--mono);letter-spacing:.1em;padding:4px 6px;border-radius:5px;border:1px solid currentColor}
.schip.k-ORIGINAL_RESEARCH{color:var(--info)}.schip.k-EXPERIMENTAL_BENCHMARK{color:var(--warn)}.schip.k-TRAINING{color:var(--neutral);border-style:dashed}.schip.k-OTHER{color:var(--neutral)}
.econ{display:inline-block;margin-top:4px;font:700 10px/1.3 var(--mono);letter-spacing:.06em;color:#1a1204;background:var(--warn);border-radius:4px;padding:3px 5px;overflow-wrap:anywhere}
.ocounts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin:10px 0}
.ocounts>div{background:var(--panel);padding:8px 10px;min-width:0}.ocounts .v{font:600 16px/1.25 var(--display);margin-top:3px;overflow-wrap:anywhere}
.funnel{list-style:none;margin:6px 0 10px;padding:0}
.funnel li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 10px;align-items:center;padding:5px 0;border-top:1px dashed var(--line);font-size:12.5px}
.funnel li:first-child{border-top:0}
.funnel .fn{overflow-wrap:anywhere}.funnel .fc{font:600 12.5px/1 var(--mono);font-variant-numeric:tabular-nums}
.funnel .fb{grid-column:1/-1;height:5px;border-radius:3px;background:var(--line)}
.funnel .fb i{display:block;height:100%;border-radius:3px;background:var(--acc)}
.funnel .ff{grid-column:1/-1;font:11px/1.3 var(--mono);color:var(--ink-3)}
.olist{list-style:none;margin:6px 0 10px;padding:0}
.olist li{padding:5px 0;border-top:1px dashed var(--line);font-size:12.5px;overflow-wrap:anywhere}.olist li:first-child{border-top:0}
details.dec{border:1px solid var(--line);border-radius:10px;background:var(--panel);margin-bottom:8px;min-width:0}
details.dec>summary{list-style:none;cursor:pointer;display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center;padding:9px 11px;font-size:12.5px}
details.dec>summary::-webkit-details-marker{display:none}
details.dec .dt{font:11.5px/1.3 var(--mono);color:var(--ink-3)}
details.dec .ref{font:600 11px/1.3 var(--mono);color:var(--ink-2);overflow-wrap:anywhere;min-width:0}
details.dec .dm{flex-basis:100%;font:12px/1.35 var(--sans);color:var(--ink-2);overflow-wrap:anywhere}
details.dec .decb{padding:0 11px 10px;border-top:1px solid var(--line)}
details.dec .expl{margin:8px 0;font-size:12.5px;line-height:1.45;overflow-wrap:anywhere}
.orow{border:1px solid var(--line);border-radius:10px;background:var(--panel-2);padding:9px 11px;margin-bottom:8px;min-width:0}
.orow .oh{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;font-size:12.5px}
.orow .om{font:11.5px/1.4 var(--mono);color:var(--ink-3);margin-top:4px;overflow-wrap:anywhere}
.ostat{font:650 10px/1 var(--mono);letter-spacing:.06em;padding:3px 5px;border-radius:4px;border:1px solid currentColor;white-space:nowrap}
.ostat.WON,.ostat.OPEN,.ostat.HOLD{color:var(--ok)}.ostat.LOST{color:var(--bad)}.ostat.SETTLED_AT_VENUE_PRICE,.ostat.PENDING{color:var(--warn)}.ostat.VOID_REFUND{color:var(--neutral)}
.pfig6{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-bottom:10px}
.pfig6>div{background:var(--panel);padding:9px 11px;min-width:0}.pfig6 .v{font:600 17px/1.25 var(--display);margin-top:3px;overflow-wrap:anywhere}
.pfig6 .v .ns{color:var(--warn);font:700 12px/1.3 var(--mono)}
.cc-funded{overflow-x:hidden;max-width:100%;margin-top:28px;border:1px dashed var(--line-2);border-radius:16px;background:var(--bg-2);padding:0 14px}
.cc-funded>summary{list-style:none;cursor:pointer;padding:14px 4px;display:flex;flex-wrap:wrap;gap:6px 12px;align-items:baseline}
.cc-funded>summary::-webkit-details-marker{display:none}
.cc-funded>summary h2{margin:0;font:600 18px/1.2 var(--serif);color:var(--ink-2)}
.cc-funded>summary .fsub{font:11.5px/1.4 var(--mono);color:var(--ink-3)}
.cc-funded>summary::after{content:"Show";margin-left:auto;font:600 11px/1 var(--mono);color:var(--ink-3);border:1px solid var(--line-2);border-radius:6px;padding:5px 8px}
.cc-funded[open]>summary::after{content:"Hide"}
.cc-funded[open]{padding-bottom:14px}
.cc-funded .evs,.cc-funded a.ev,.cc-funded .mandate,.cc-funded .chips{overflow-wrap:anywhere;flex-wrap:wrap}
.cc-funded .fstate{font:12px/1.45 var(--mono);color:var(--ink-3);margin:0 0 10px;overflow-wrap:anywhere}
/* the character: names above, status and captions below; nothing over the figure */
.cc-stagewrap{display:flex;flex-direction:column;gap:8px;min-width:0}
.cc-stagewrap .cc-ov{position:static;padding:0;background:none;pointer-events:auto}
.cc-stagewrap .cc-ov h1{text-shadow:none}
.cc-stagewrap .cc-cap{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;font:600 10.5px/1.35 var(--mono);color:var(--ink-3)}
.cc-stagewrap .cc-cap .cc-placeholder,.cc-stagewrap .cc-cap .fbnote{position:static;max-width:none;text-align:left}
.cc-stagewrap .cc-cap .cc-ctl{position:static;margin-left:auto}
.cc-stagewrap:has(.cc-3d-on) .fbnote{display:none}
.cc-stagewrap:has(.cc-real-model) .cc-placeholder{display:none}
.cc-stagewrap:has(.cc-real-model.cc-candidate) .cc-placeholder{display:inline-block}
.cc-stagewrap .cc-st{position:static;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 12px;overflow-wrap:anywhere}
.cc-stagewrap .cc-stage{min-height:400px}
/* the one account strip and the sign-in prompt */
.cc-acct{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin:0 0 14px}
.cc-acct .acct7{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:8px;margin-top:6px}
.cc-acct .acct7>div{min-width:0}.cc-acct small{display:block;font:600 10px/1.3 var(--mono);letter-spacing:.06em;color:var(--ink-3);text-transform:uppercase}
.cc-acct b{display:block;font:600 15px/1.3 var(--display);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.cc-signin{margin:0 0 14px;padding:12px 14px;border-radius:12px;border:1px solid var(--warn);background:color-mix(in oklab,var(--warn) 12%,var(--panel));color:var(--ink);font-size:13.5px;line-height:1.5;overflow-wrap:anywhere}
.cc-signin b{color:var(--warn);font:700 12px/1.3 var(--mono);letter-spacing:.08em}
.cc-signin a{font-weight:700;color:var(--acc-2)}
.mname{min-width:0}.mname .mn1{font:650 13.5px/1.3 var(--display);color:var(--ink);overflow-wrap:anywhere}.mname .mn2{font:12px/1.35 var(--sans);color:var(--ink-2);overflow-wrap:anywhere}
details.dec .dfig{flex-basis:100%;font:12px/1.4 var(--mono);color:var(--ink-2);overflow-wrap:anywhere}
.pboxes{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.6fr);gap:8px;margin-top:8px}
.pbox{border:1px solid var(--line);border-radius:10px;padding:8px 10px;min-width:0}.pbox .v{font:600 16px/1.3 var(--display)}
.pbox.real{border-color:color-mix(in oklab,var(--ok) 45%,var(--line))}.pbox.risk{border-style:dashed;border-color:color-mix(in oklab,var(--warn) 45%,var(--line))}
.unm{color:var(--warn)}.recon{font:650 12px/1.4 var(--mono);margin:0 0 10px;padding:7px 10px;border-radius:8px;border:1px solid currentColor;overflow-wrap:anywhere}.recon.ok{color:var(--ok)}.recon.bad{color:var(--bad)}
/* long technical strings wrap; nothing is clipped at phone width */
.cc-p.ops,.cc-p.paper,.cc-raw,.cc-acct,.cc-fresh{overflow-wrap:anywhere}
.cc-p.ops time,.cc-fresh time,.cc-acct time,.cc-signin time,.cc-kpis time,.cc-funded time{white-space:normal}
.orow .oh>*,.ocounts .v,.ocounts .v *,.cc-kpi .s,.cc-kpi .v,.olist li *{min-width:0;overflow-wrap:anywhere;white-space:normal}
.schip,.ostat{white-space:normal;overflow-wrap:anywhere;max-width:100%}
@media (max-width:1000px){.cc-acct .acct7{grid-template-columns:repeat(4,minmax(0,1fr))}.pboxes{grid-template-columns:1fr}.cc-fresh{grid-template-columns:1fr}.pfig6{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:760px){.cc-acct .acct7{grid-template-columns:repeat(2,minmax(0,1fr))}.cc-stagewrap .cc-stage{min-height:300px}.cc-kpi .v{font-size:17px}.ocounts{grid-template-columns:repeat(2,minmax(0,1fr))}.cc-top .meta{flex-wrap:wrap;white-space:normal}}
"""


def _panel(pid: str, title: str, src: str) -> str:
    return ('<section class="cc-p ops" id="%s" data-status="READING" '
            'data-ops-panel aria-labelledby="%s-h"><header><h2 id="%s-h">%s '
            '<span class="sim">SIMULATED</span></h2><span class="pill st-MISSING"'
            ' data-pill>READING</span><span class="src">%s</span></header>'
            '<div data-body><p class="boot">Reading&#8230;</p></div></section>'
            % (pid, pid, pid, title, src))


#: THE SEVEN ACCOUNT FIGURES every page shows, in this order, from the one
#: account read (bettor_paper_ops.account_section -> ledger balances()). The
#: homepage (paper.js) uses the same keys, labels and formatting rule.
ACCOUNT_FIGURES = (
    ("cash_usd", "Cash"), ("reserved_usd", "Reserved"),
    ("available_usd", "Available"), ("open_position_value_usd", "Position value"),
    ("total_equity_usd", "Equity"), ("realized_pnl_usd", "Realized P&L"),
    ("unrealized_pnl_usd", "Unrealized P&L"))

#: WHERE A SIGNED-OUT VIEWER SIGNS IN: the COMMAND homepage's unlock (the
#: frame's parent, opened in the top window); unframed, the desk page.
SIGNIN_HREF = "/"


def fresh_html() -> str:
    """The sign-in prompt (hidden until a read answers 401/403), the three
    freshness stamps and the one account strip, before any read (filled by
    CC.ops.fresh / CC.ops.accountStrip)."""
    return ('<div class="cc-signin" id="cc-signin" role="alert" hidden></div>'
            '<section class="cc-acct" id="paper-acct" aria-label="Paper account '
            'figures" data-status="READING"><div class="lbl">Paper account '
            '&#183; reading&#8230;</div></section>' + ('<section class="cc-fresh" id="paper-fresh" aria-label="Freshness '
            'of the paper view" aria-live="polite">'
            '<div data-fresh="read"><div class="lbl">Last successful server read'
            '</div><div class="fv">reading&#8230;</div></div>'
            '<div data-fresh="heartbeat"><div class="lbl">Last agent heartbeat '
            '(paper runtime)</div><div class="fv">reading&#8230;</div></div>'
            '<div data-fresh="ledger"><div class="lbl">Last ledger transaction'
            '</div><div class="fv">reading&#8230;</div></div>'
            '<div class="fconn" data-fresh="conn">Live updates: connecting'
            '&#8230;</div></section>'))


def ops_html(kind: str) -> str:
    body = "".join(_panel(pid, t, src) for pid, t, src in OPS_PANELS[kind])
    return ('<h2 class="cc-h2 cc-oph" id="paper-ops">%s <span class="sim">'
            'LIVE MARKET DATA · SIMULATED EXECUTION</span></h2>'
            '<p class="note">GET %s?agent=%s &#183; the active paper session, '
            'one account, one ledger. The funded system is inactive and is '
            'shown only in its own section at the end of this page.</p>'
            '<div class="cc-panels" id="cc-ops" data-cc-ops>%s</div>'
            % (_html.escape(OPS_TITLES[kind]), OPS_ROUTE, kind, body))


def funded_open() -> str:
    return ('<details class="cc-funded" id="funded"><summary><h2>Funded '
            'system (inactive)</h2><span class="fsub">secondary &#183; real-money '
            'execution is disabled; these are the funded lane\'s own records, '
            'never the paper experiment</span></summary>'
            '<p class="fstate" id="cc-funded-state">Funded agent status: not '
            'read yet.</p><div class="cc-kpis" id="cc-funded-kpis"></div>')


FUNDED_CLOSE = "</details>"

# ════════════════════════════════════════════════════════════════════
# THE RENDERERS (pure: they take the read's outcome and return HTML)
# ════════════════════════════════════════════════════════════════════
_OPS_CORE_JS_RAW = r"""
(function (AG, CC) {
  'use strict';
  var esc = AG.esc, isObj = AG.isObj;
  var SIGNIN = 'SIGN-IN REQUIRED';
  var KIND_LABEL = {ORIGINAL_RESEARCH: 'ORIGINAL RESEARCH', EXPERIMENTAL_BENCHMARK: 'EXPERIMENTAL BENCHMARK', TRAINING: 'TRAINING / SIMULATED EXECUTION'};
  function num(v) { return typeof v === 'number' && isFinite(v); }
  function sec(s) { return isObj(s) && (s.status === 'OK' || s.status === 'EMPTY' || s.status === 'UNAVAILABLE') ? s : null; }
  function ok(s) { s = sec(s); return s && s.status === 'OK' ? s.data : null; }
  function n(v) { return num(v) ? (Number.isInteger(v) ? v.toLocaleString('en-US') : String(+v.toFixed(4))) : null; }
  function ago(s) { return num(s) ? (s < 0 ? 'in ' + AG.dur(-s) : AG.dur(s) + ' ago') : ''; }
  function when(v) { var e = AG.toEpoch(v); if (e === null) return null; var ny = CC.paper && CC.paper.nyTime ? CC.paper.nyTime(e) : null; return AG.ts(e) + (ny ? ' <span class="mute">(' + esc(ny) + ')</span>' : ''); }

  // ── A FAILED READ IS NEVER A ZERO ─────────────────────────────────
  function failure(o, url) {
    if (!o) return {state: 'UNAVAILABLE', why: 'not read yet'};
    if (o.kind === 'LOCKED') return {state: SIGNIN, why: 'the COMMAND session is missing or expired (GET ' + url + ' answered 401). Sign in on the COMMAND homepage, then reload this page.'};
    if (o.kind === 'NOT_DEPLOYED') return {state: 'UNAVAILABLE', why: 'GET ' + url + ' answered 404: the serving API does not have the paper operations read yet (it ships with the next API release)'};
    if (o.kind !== 'OK') return {state: 'UNAVAILABLE', why: o.why || o.kind || 'the read failed'};
    var j = o.json;
    if (!isObj(j)) return {state: 'UNAVAILABLE', why: 'the response was not a JSON object'};
    var whole = sec(j.operations) || sec(j.overview);
    if (whole && whole.status !== 'OK') return {state: 'UNAVAILABLE', why: whole.why || 'the server named no reason'};
    return null;
  }
  function failBox(f) {
    return '<div class="plain ops-fail" data-ops-fail="' + esc(f.state) + '"><p class="big">' + esc(f.state) + '</p><p>' + esc(f.why) + '</p><p class="mute">Nothing is shown in place of the records: this is not a zero and not an empty list.</p></div>';
  }
  function secBox(s, what) {
    s = sec(s);
    if (!s) return failBox({state: 'UNAVAILABLE', why: 'the response carried no ' + what + ' section'});
    if (s.status === 'UNAVAILABLE') return failBox({state: 'UNAVAILABLE', why: s.why || 'the read failed'});
    return CC.emptyLine(s.why);
  }
  function stStatus(s) { s = sec(s); return s ? s.status : 'UNAVAILABLE'; }

  // ── THE THREE FRESHNESS STAMPS ───────────────────────────────────
  // st = {json (last OK read or null), okAt, tryAt, fail, stream, streamNote}; now = the page's clock (epoch s)
  function serverAge(j, st, at, now) {      // an age measured on the server's clock, advanced by the time since the read
    if (!num(at) || !isObj(j) || !num(j.as_of)) return null;
    return (j.as_of - at) + (num(st.okAt) && num(now) ? Math.max(0, now - st.okAt) : 0);
  }
  function stamp(label, value, sub, chip) {
    return {label: label, value: value, sub: sub || '', chip: chip || null};
  }
  function fresh(st, now) {
    st = st || {}; var j = isObj(st.json) ? st.json : null, f = st.fail, out = {};
    var gone = f && f.state === SIGNIN;
    // 1. the last successful server read (the page's own clock), never agent health
    out.read = stamp('Last successful server read',
      num(st.okAt) ? AG.ts(st.okAt) + ' <span class="mute">' + esc(ago(now - st.okAt)) + '</span>' : (gone ? SIGNIN : 'no successful read yet'),
      (f ? 'last attempt ' + (num(st.tryAt) ? AG.ts(st.tryAt) + ' ' : '') + esc(f.state) + ': ' + esc(f.why) + '. ' : '')
        + (j && num(j.as_of) ? 'Server clock at that read ' + AG.ts(j.as_of) + '. ' : '') + 'A recent read is not agent health.',
      f ? (gone ? 'SIGN-IN' : 'UNAVAILABLE') : (num(st.okAt) ? 'OK' : null));
    // 2. the paper runtime's own heartbeat
    var fr = j && isObj(j.freshness) ? j.freshness : {}, hb = sec(fr.agent_heartbeat);
    if (!j) out.heartbeat = stamp('Last agent heartbeat (paper runtime)', gone ? SIGNIN : 'UNAVAILABLE', f ? esc(f.why) : 'not read yet', gone ? 'SIGN-IN' : 'UNAVAILABLE');
    else if (!hb) out.heartbeat = stamp('Last agent heartbeat (paper runtime)', 'UNAVAILABLE', 'the read carried no heartbeat section', 'UNAVAILABLE');
    else if (hb.status !== 'OK') out.heartbeat = stamp('Last agent heartbeat (paper runtime)', hb.status === 'EMPTY' ? 'NO HEARTBEAT YET' : 'UNAVAILABLE', esc(hb.why || 'no reason given'), hb.status === 'EMPTY' ? 'STALE' : 'UNAVAILABLE');
    else {
      var h = hb.data || {}, age = serverAge(j, st, h.heartbeat_at, now), lim = num(h.stale_after_s) ? h.stale_after_s : 300;
      var la = isObj(h.last_attempt) ? h.last_attempt : null;
      var stale = age === null ? null : age > lim;
      out.heartbeat = stamp('Last agent heartbeat (paper runtime)',
        num(h.heartbeat_at) ? AG.ts(h.heartbeat_at) + ' <span class="mute">' + esc(ago(age)) + '</span>' : 'no pass has run in this session',
        (num(h.passes) ? h.passes + ' pass(es)' : '? passes') + ' · ' + (num(h.errors) ? h.errors + ' error(s)' : '? errors')
          + (la && num(la.written_at) ? ' · last scheduling attempt ' + AG.ts(la.written_at) + (la.refusal ? ' refused ' + esc(la.refusal) + (la.why ? ' (' + esc(la.why) + ')' : '') : la.ran ? ' ran' : '') : '')
          + (h.last_error ? ' · last error: ' + esc(h.last_error) : '') + ' · stale after ' + AG.dur(lim) + '. Source: ' + esc(h.source || 'paper_session_health'),
        stale === null ? 'STALE' : stale ? 'STALE' : 'RECENT');
    }
    // 3. the last committed ledger transaction: when cash last moved, nothing more
    var lg = j ? sec(fr.ledger) : null;
    if (!j) out.ledger = stamp('Last ledger transaction', gone ? SIGNIN : 'UNAVAILABLE', f ? esc(f.why) : 'not read yet', gone ? 'SIGN-IN' : 'UNAVAILABLE');
    else if (!lg || lg.status !== 'OK') out.ledger = stamp('Last ledger transaction', lg && lg.status === 'EMPTY' ? 'NO LEDGER ENTRY' : 'UNAVAILABLE', esc((lg && lg.why) || 'the read carried no ledger section'), lg && lg.status === 'EMPTY' ? null : 'UNAVAILABLE');
    else {
      var l = lg.data || {}, lage = serverAge(j, st, l.committed_at, now);
      out.ledger = stamp('Last ledger transaction',
        num(l.committed_at) ? AG.ts(l.committed_at) + ' <span class="mute">' + esc(ago(lage)) + '</span>' : 'no commit time',
        (l.kind ? esc(l.kind) + ' · ' : '') + (num(l.sequence) ? 'entry #' + l.sequence + ' · ' : '') + (num(l.entries) ? l.entries + ' entries · ' : '') + 'an unchanged balance is not a stale one', null);
    }
    var sm = st.stream || 'CONNECTING';
    out.conn = 'Live updates: <span class="fchip ' + esc(sm === 'LIVE' ? 'LIVE' : sm === 'SIGN-IN' ? 'SIGN-IN' : sm === 'POLLING' ? 'POLLING' : sm === 'RECONNECTING' ? 'RECONNECTING' : 'UNAVAILABLE') + '">' + esc(sm === 'SIGN-IN' ? 'SIGN-IN REQUIRED' : sm) + '</span> '
      + esc(st.streamNote || '') + (sm === 'LIVE' ? ' · ledger stream open; the agents\' records are re-read every 30 s' : sm === 'SIGN-IN' ? ' · the stream and the reads need a COMMAND session' : ' · the stream is not open: every read is repeated every 15 s');
    return out;
  }
  function freshHtml(fs) {
    function cell(k) { var s = fs[k]; return '<div data-fresh="' + k + '"><div class="lbl">' + esc(s.label) + (s.chip ? '<span class="fchip ' + esc(s.chip) + '">' + esc(s.chip) + '</span>' : '') + '</div><div class="fv">' + s.value + '</div><div class="fs">' + s.sub + '</div></div>'; }
    return cell('read') + cell('heartbeat') + cell('ledger') + '<div class="fconn" data-fresh="conn">' + fs.conn + '</div>';
  }

  // ── THE CHARACTER FOLLOWS THE PAPER RUNTIME, NOT A PAGE READ ──────
  function mode(st, now) {
    st = st || {}; var j = isObj(st.json) ? st.json : null;
    if (!j) return {mode: 'unavailable', recorded: null, why: st.fail ? st.fail.state + ': ' + st.fail.why : 'the paper runtime status has not been read yet'};
    var hb = sec((j.freshness || {}).agent_heartbeat);
    if (!hb || hb.status !== 'OK') return {mode: 'unavailable', recorded: null, why: 'paper runtime heartbeat ' + (hb ? hb.status + ': ' + (hb.why || '') : 'not in the read')};
    var h = hb.data || {}, age = serverAge(j, st, h.heartbeat_at, now), lim = num(h.stale_after_s) ? h.stale_after_s : 300;
    if (age === null) return {mode: 'unavailable', recorded: null, why: 'no paper pass has run in this session'};
    if (age > lim) return {mode: 'unavailable', recorded: null, age: age, why: 'paper runtime heartbeat stale: last ' + AG.dur(age) + ' ago (limit ' + AG.dur(lim) + ')'};
    return {mode: 'monitoring', recorded: 'PAPER_PASS', age: age, why: null, activity: 'paper runtime heartbeat ' + AG.dur(age) + ' ago'};
  }

  // ── KEY FIGURES (the brief) ──────────────────────────────────────
  function kpi(label, value, sub) { return {label: label, value: value, sub: sub || ''}; }
  function fromSec(s, fn) { s = sec(s); if (!s) return ['UNAVAILABLE', 'not in the read']; if (s.status === 'UNAVAILABLE') return ['UNAVAILABLE', esc(s.why || '')]; if (s.status === 'EMPTY') return ['NONE RECORDED', esc(s.why || '')]; return fn(s.data); }
  function strat(j, key) { return (Array.isArray(j.strategies) ? j.strategies : []).filter(function (s) { return isObj(s) && s.strategy === key; })[0] || null; }
  function kpis(kind, st) {
    st = st || {}; var j = isObj(st.json) ? st.json : null, f = st.fail;
    if (!j) { var w = f ? f.state : 'UNAVAILABLE', why = f ? esc(f.why) : 'not read yet'; return [kpi('Paper view', w, why)]; }
    var out = [];
    if (kind === 'derek') {
      [['DEREK_ENTRY_POLICY_V2', 'Research decisions (two-model)'], ['PINNACLE_ONLY_PAPER_BENCHMARK', 'Benchmark decisions (strict)'], ['PINNACLE_COMPLETED_GAME_PAPER', 'Completed-game decisions']].forEach(function (p) {
        var s = strat(j, p[0]), r = s ? fromSec(s.counts, function (c) { return [n(c.decisions), n(c.enter) + ' ENTER · ' + n(c.refuse) + ' REFUSE · ' + n(c.last_24h) + ' in 24 h']; }) : ['UNAVAILABLE', 'not in the read'];
        out.push(kpi(p[1], r[0], r[1]));
      });
      var last = null; (j.strategies || []).forEach(function (s) { var c = ok(s && s.counts); if (c && num(c.latest_at) && (last === null || c.latest_at > last)) last = c.latest_at; });
      out.push(kpi('Latest decision', last === null ? 'none recorded' : AG.ts(last), 'any strategy'));
    } else if (kind === 'xavier') {
      var h = fromSec(j.handoffs, function (d) { return [n(d.length), 'every paper position handed over (newest ' + d.length + ')']; });
      var hc = ok(j.handoff_counts); if (hc) h = [n(hc.reduce(function (a, r) { return a + (num(r.n) ? r.n : 0); }, 0)), hc.map(function (r) { return n(r.n) + ' ' + esc(r.strategy); }).join(' · ')];
      out.push(kpi('Positions handed to Xavier', h[0], h[1]));
      var p = fromSec(j.pending_settlements, function (d) { return [n(d.count), 'open, awaiting the venue\'s settlement']; });
      if (sec(j.pending_settlements) && j.pending_settlements.status === 'EMPTY') p = ['0', 'no open position awaits settlement'];
      out.push(kpi('Pending settlement', p[0], p[1]));
      var so = fromSec(j.standing_summary, function (d) { var o = d.filter(function (r) { return r.open; }).reduce(function (a, r) { return a + (num(r.n) ? r.n : 0); }, 0); return [n(o), 'open protection / management orders']; });
      out.push(kpi('Open protection orders', so[0], so[1]));
      var se = fromSec(j.settlement_counts, function (d) { var t = d.reduce(function (a, r) { return a + (num(r.n) ? r.n : 0); }, 0); return [n(t), d.map(function (r) { return n(r.n) + ' ' + esc(r.outcome); }).join(' · ')]; });
      out.push(kpi('Settlements', se[0], se[1]));
    } else {
      var a = fromSec(j.account, function (d) { return [num(d.total_equity_usd) ? CC.usd(d.total_equity_usd) : '<span class="ns">NOT STATED</span>', esc(d.equity_basis || '')]; });
      out.push(kpi('Paper equity', a[0], a[1]));
      var r2 = fromSec(j.account, function (d) { return [num(d.realized_pnl_usd) ? CC.usdS(d.realized_pnl_usd) : 'not sent', 'unrealized ' + (num(d.unrealized_pnl_usd) ? CC.usdS(d.unrealized_pnl_usd) : 'NOT STATED')]; });
      out.push(kpi('Realized P&L', r2[0], r2[1]));
      var fc = fromSec(j.finding_counts, function (d) { var t = 0, w = 0; d.forEach(function (r) { t += num(r.n) ? r.n : 0; if (r.severity !== 'INFO') w += num(r.n) ? r.n : 0; }); return [n(t), n(w) + ' warning or critical']; });
      out.push(kpi('Audit findings', fc[0], fc[1]));
      var dr = fromSec(j.daily_report, function (d) { return [esc(d.report_day || '?'), 'version ' + esc(d.version) + (d.final ? ' · final' : ' · provisional') + (d.reconciles === true ? ' · reconciles' : d.reconciles === false ? ' · DOES NOT RECONCILE' : '')]; });
      out.push(kpi('Latest daily report', dr[0], dr[1]));
    }
    return out;
  }

  // ── DEREK ─────────────────────────────────────────────────────────
  // ── MARKET NAMES (server: bettor_paper_ops.market_name) ───────────
  function mname(r) {
    var m = isObj(r && r.market) ? r.market : {};
    var title = m.title || null, selc = m.selection || null;
    return {primary: selc || title || 'Market name not recorded', secondary: [selc ? title : null, m.detail].filter(function (x) { return x; }).join(' · '),
            warn: (m.warnings || []).map(function (w) { return '<span class="mdw">' + esc(w) + '</span>'; }).join('')};
  }
  function nameHtml(r) {
    var x = mname(r);
    return '<div class="mname"><div class="mn1" data-market-name>' + esc(x.primary) + '</div>' + (x.secondary ? '<div class="mn2">' + esc(x.secondary) + '</div>' : '') + x.warn + '</div>';
  }
  function refusalHtml(code, words) {
    return code ? '<span class="ref" title="' + esc(code) + '">' + esc(words || String(code).replace(/_/g, ' ').toLowerCase()) + '</span>' : '';
  }
  // ── DEREK ─────────────────────────────────────────────────────────
  function decRow(d) {
    d = isObj(d) ? d : {};
    var tech = [['decision id', d.decision_id], ['market slug', d.us_market_slug], ['order intent', d.intent], ['strategy', d.strategy], ['policy version', d.policy_version], ['refusal code', d.refusal], ['economics label', d.economics_label], ['name source', d.market && d.market.source]];
    return '<details class="dec" data-decision="' + esc(d.decision_id || '') + '" data-strategy="' + esc(d.strategy || '') + '"><summary><span class="dt">' + (AG.toEpoch(d.decided_at) !== null ? AG.ts(d.decided_at) : 'time not recorded') + '</span>'
      + '<span class="vb vb-' + esc(d.verdict) + '">' + esc(d.verdict || '?') + '</span>' + refusalHtml(d.refusal, d.refusal_words)
      + '<span class="dm">' + nameHtml(d) + '</span>'
      + '<span class="dfig">price ' + (CC.price(d.purchase_price) || '<span class="mute">not recorded</span>') + ' · Pinnacle ' + (CC.prob(d.p_pinnacle) || '—') + ' · edge ' + (CC.pp(d.edge_pp) || '<span class="mute">not recorded</span>') + ' · fees ' + (CC.usd(d.fees_usd) || '<span class="mute">not recorded</span>') + ' · net EV ' + (CC.usdS(d.net_ev_usd) || '<span class="mute">not recorded</span>') + '</span></summary><div class="decb">'
      + '<p class="expl">' + esc(d.explanation || 'no explanation recorded') + '</p>'
      + '<p class="note" style="margin:0">p internal ' + (CC.prob(d.p_internal) || '—') + ' · p blended ' + (CC.prob(d.p_blended) || '—') + (num(d.proposed_qty) ? ' · proposed ' + n(d.proposed_qty) + ' @ limit ' + (CC.price(d.limit_price) || '?') : '') + '</p>'
      + CC.techDetails(tech) + '</div></details>';
  }
  function orderRow(o) {
    o = isObj(o) ? o : {};
    var tech = [['order id', o.order_id], ['decision id', o.decision_id], ['group id', o.group_id], ['market slug', o.us_market_slug], ['order intent', o.intent], ['terminal reason', o.terminal_reason], ['policy version', o.policy_version]];
    return '<div class="orow" data-entry-order="' + esc(o.order_id || '') + '"><div class="oh"><span class="ostat ' + (o.filled_qty ? 'OPEN' : 'VOID_REFUND') + '">' + esc(o.state || '?') + '</span>' + nameHtml(o) + '</div>'
      + '<div class="om">' + (n(o.filled_qty) || '0') + ' of ' + (n(o.qty) || '?') + ' filled · purchase price ' + (CC.price(o.avg_fill_price) || ('limit ' + (CC.price(o.limit_price) || '?'))) + ' · cost ' + (CC.usd(o.cost_usd) || '—') + ' · fees ' + (CC.usd(o.fees_usd) || '—') + ' · Pinnacle ' + (CC.prob(o.p_pinnacle) || '—') + ' · edge ' + (CC.pp(o.edge_pp) || '—')
      + (o.unfilled_reason_words ? ' · not filled: ' + esc(o.unfilled_reason_words) : '') + ' · ' + (AG.toEpoch(o.created_at) !== null ? AG.ts(o.created_at) : '') + '</div>' + CC.techDetails(tech) + '</div>';
  }
  function funnel(s) {
    var f = sec(s.funnel); if (!f || f.status !== 'OK') return secBox(f, 'funnel');
    var st = Array.isArray(f.data.stages) ? f.data.stages : [], top = st.length && num(st[0].n) && st[0].n > 0 ? st[0].n : null;
    return '<p class="lbl" style="margin:10px 0 2px">Eligibility funnel</p><ol class="funnel">' + st.map(function (x) {
      var w = top && num(x.n) ? Math.max(0, Math.min(100, 100 * x.n / top)) : 0;
      return '<li data-stage="' + esc(x.stage) + '"><span class="fn">' + esc(String(x.stage || '?').replace(/_/g, ' ')) + '</span><span class="fc">' + (n(x.n) || '?') + '</span><span class="fb"><i style="width:' + w.toFixed(1) + '%"></i></span>'
        + (num(x.failed_here) && x.failed_here ? '<span class="ff">' + n(x.failed_here) + ' stopped here' + (num(x.not_evaluated_here) && x.not_evaluated_here ? ', ' + n(x.not_evaluated_here) + ' not evaluated' : '') + '</span>' : '') + '</li>';
    }).join('') + '</ol><p class="note">' + esc(f.data.basis || '') + (num(f.data.without_conditions) && f.data.without_conditions ? ' · ' + n(f.data.without_conditions) + ' decision(s) carry no recorded conditions' : '') + '</p>';
  }
  function stratBlock(s) {
    var c = sec(s.counts), cd = ok(s.counts), vers = cd && Array.isArray(cd.policy_versions) && cd.policy_versions.length ? cd.policy_versions : [s.version];
    var head = '<header class="strat-h"><span class="schip k-' + esc(s.kind || 'OTHER') + '">' + esc(s.label || KIND_LABEL[s.kind] || s.kind) + '</span><h3>' + esc(s.title || s.strategy) + '</h3>'
      + '<div class="strat-meta">strategy <span class="mono">' + esc(s.strategy) + '</span> · policy version ' + vers.map(function (v) { return '<span class="mono">' + esc(v) + '</span>'; }).join(', ') + '</div>'
      + (s.economics_label ? '<span class="econ" title="economics label">' + esc(s.economics_label) + '</span>' : '') + '</header>'
      + '<p class="note" style="margin:8px 0 0">' + esc(s.summary || '') + '</p>'
      + (s.disclosure ? '<details class="tech"><summary>Disclosure</summary><p class="note" style="margin:6px 0 0">' + esc(s.disclosure) + '</p></details>' : '');
    var counts = cd ? '<div class="ocounts"><div><div class="lbl">Decisions</div><div class="v">' + n(cd.decisions) + '</div></div><div><div class="lbl">ENTER</div><div class="v">' + n(cd.enter) + '</div></div><div><div class="lbl">REFUSE</div><div class="v">' + n(cd.refuse) + '</div></div>'
      + '<div><div class="lbl">Last 24 h</div><div class="v">' + n(cd.last_24h) + '</div></div><div><div class="lbl">Markets</div><div class="v">' + n(cd.markets) + '</div></div><div><div class="lbl">Latest</div><div class="v" style="font-size:12.5px">' + (num(cd.latest_at) ? AG.ts(cd.latest_at) : '—') + '</div></div></div>' : secBox(c, 'counts');
    var rf = sec(s.refusals), rfd = ok(s.refusals);
    var refs = rfd ? '<p class="lbl" style="margin:10px 0 2px">Refusals by reason</p><ul class="olist">' + rfd.map(function (r) { return '<li><b class="mono">' + n(r.n) + '</b> · ' + refusalHtml(r.refusal, r.refusal_words) + '</li>'; }).join('') + '</ul>' : (rf && rf.status === 'EMPTY' ? '' : secBox(rf, 'refusals'));
    var rc = sec(s.recent), rcd = ok(s.recent);
    var recent = '<p class="lbl" style="margin:10px 0 6px">Recent decisions (newest ' + (rcd ? rcd.length : '') + (cd ? ' of ' + n(cd.decisions) : '') + ')</p>' + (rcd ? rcd.map(decRow).join('') : secBox(rc, 'recent decisions'));
    var os_ = sec(s.orders), osd = ok(s.orders);
    var orders = '<p class="lbl" style="margin:10px 0 6px">Entry orders (every one, newest first)</p>' + (osd ? osd.map(orderRow).join('') : (os_ && os_.status === 'EMPTY' ? '<p class="note">' + esc(os_.why) + '</p>' : secBox(os_, 'entry orders')));
    return '<div class="strat" data-strategy="' + esc(s.strategy) + '" data-kind="' + esc(s.kind || '') + '">' + head + counts + funnel(s) + refs + orders + recent + '</div>';
  }
  function derek(j) {
    var all = Array.isArray(j.strategies) ? j.strategies.filter(isObj) : [];
    function part(kind, none) {
      var ss = all.filter(function (s) { return s.kind === kind; });
      if (!ss.length) return {status: 'UNAVAILABLE', html: failBox({state: 'UNAVAILABLE', why: none})};
      var sts = ss.map(function (s) { return stStatus(s.counts); });
      var status = sts.indexOf('OK') >= 0 ? 'OK' : sts.indexOf('UNAVAILABLE') >= 0 ? 'UNAVAILABLE' : 'EMPTY';
      return {status: status, html: ss.map(stratBlock).join('')};
    }
    var b = part('EXPERIMENTAL_BENCHMARK', 'the read carried no experimental benchmark strategy');
    b.html = '<p class="note"><b>Experimental paper execution</b> on the fictional account: not evidence of qualified or proven profitability, never Derek\'s research decisions. Each policy is counted on its own.</p>' + b.html;
    var t = all.filter(function (s) { return s.kind === 'TRAINING'; });
    if (t.length) {
      b.html += '<p class="note" style="margin-top:14px"><b>Training / simulated execution</b>: the bounded exploration strategy may take positions that fail the 0.5 pp edge or after-fee rule, to generate forward experience. Its expected value can be negative; that is a research cost, not investment performance.</p>' + t.map(stratBlock).join('');
      if (b.status !== 'OK' && t.some(function (s) { return stStatus(s.counts) === 'OK'; })) b.status = 'OK';
    }
    var r = part('ORIGINAL_RESEARCH', 'the read carried no research strategy');
    r.html = '<p class="note">Derek\'s own two-model policy. Counted apart from the benchmarks.' + controlNote(j) + '</p>' + r.html;
    return {'p-ops-standing': standingPanel(j), 'p-ops-bench': b, 'p-ops-research': r};
  }
  function standingPanel(j) {
    var s_ = sec(j.standing_entry_orders), d = ok(j.standing_entry_orders);
    if (!d) return {status: s_ ? s_.status : 'UNAVAILABLE', html: s_ && s_.status === 'EMPTY' ? '<p class="note">' + esc(s_.why) + '</p>' : secBox(s_, 'standing entry orders')};
    return {status: 'OK', html: '<p class="note">An order is not a fill: it reserves cash on the one ledger until it fills, expires or is cancelled. Resting bids fill only when observed liquidity strictly crosses the price after the queue ahead.</p>' + d.map(function (o) {
      var cc = Array.isArray(o.cancel_conditions) ? o.cancel_conditions : [];
      return '<div class="orow" data-standing="' + esc(o.order_id) + '"><div class="oh">' + schip(o.strategy) + '<b>' + esc(o.participant || o.us_market_slug) + '</b><span class="ostat OPEN">' + esc(o.state) + '</span></div>'
        + '<div class="om">' + esc(o.order_type) + ' ' + esc(o.time_in_force) + ' · BUY ' + esc(o.holding_side) + ' · ' + n(o.qty) + ' @ ' + (num(o.limit_price) ? Number(o.limit_price).toFixed(2) : '?') + ' · filled ' + n(o.filled_qty) + ' · reserved ' + (CC.usd(o.reserved_remaining_usd) || '?') + (num(o.queue_ahead_qty) ? ' · queue ahead ' + n(o.queue_ahead_qty) : '') + (AG.toEpoch(o.expires_at) !== null ? ' · expires ' + AG.ts(o.expires_at) : '') + '</div>'
        + (o.rationale ? '<div class="om">' + esc(o.rationale) + '</div>' : '')
        + (cc.length ? '<details class="tech"><summary>Cancellation conditions</summary><ul class="olist">' + cc.map(function (c) { return '<li><b>' + esc(String(c.condition || '').replace(/_/g, ' ').toLowerCase()) + '</b> · ' + esc(c.rule || '') + '</li>'; }).join('') + '</ul></details>' : '') + '</div>';
    }).join('')};
  }
  function opsAuditPanel(j) {
    var s_ = sec(j.operational_audit), d = ok(j.operational_audit);
    if (!d) return {status: s_ ? s_.status : 'UNAVAILABLE', html: s_ && s_.status === 'EMPTY' ? '<p class="note">' + esc(s_.why) + '</p>' : secBox(s_, 'operational audit')};
    return {status: 'OK', html: '<p class="note">Operational recommendations, each with its evidence, the owner\'s response and later measurements. An operational improvement is never reported as profitable learning.</p>' + d.map(function (r) {
      var b = isObj(r.baseline) ? r.baseline : {}, ev = Array.isArray(r.events) ? r.events : [];
      return '<div class="orow" data-rec="' + esc(r.recommendation_id) + '"><div class="oh"><span class="ostat ' + (r.status === 'IMPROVED' ? 'OPEN' : r.status === 'NOT_IMPROVED' ? 'LOST' : 'OPEN') + '">' + esc(r.status) + '</span><b>' + esc(String(r.kind || '').replace(/_/g, ' ').toLowerCase()) + '</b> · to ' + esc(r.owner_agent) + ' · ' + esc(r.category) + '</div>'
        + '<div class="om">' + esc(r.recommendation) + '</div><div class="om">metric ' + esc(r.metric) + ' · baseline ' + esc(b.value) + '</div>'
        + (ev.length ? '<ul class="olist">' + ev.map(function (e) { return '<li><b>' + esc(e.actor === 'SYSTEM' ? 'Automated template' : e.actor) + '</b> ' + esc(String(e.kind).replace(/_/g, ' ').toLowerCase()) + ' · ' + esc(e.body) + '</li>'; }).join('') + '</ul>' : '') + '</div>';
    }).join('')};
  }
  function controlNote(j) {
    var c = ok(j.controls); if (!c) return '';
    var row = c.filter(function (r) { return r.control_key === 'PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2'; })[0];
    return row ? ' Its paper entry switch is <b>' + (row.enabled ? 'ON' : 'OFF') + '</b>' + (row.why ? ' (' + esc(row.why) + ')' : '') + '.' : '';
  }

  // ── XAVIER ────────────────────────────────────────────────────────
  function schip(s) { return '<span class="schip k-' + (s === 'DEREK_ENTRY_POLICY_V2' ? 'ORIGINAL_RESEARCH' : s === 'PINNACLE_EXPLORATION_PAPER' ? 'TRAINING' : /^PINNACLE_/.test(s || '') ? 'EXPERIMENTAL_BENCHMARK' : 'OTHER') + '">' + esc(s === 'PINNACLE_EXPLORATION_PAPER' ? 'TRAINING · ' + s : (s || 'strategy not recorded')) + '</span>'; }
  function labelOf(r) { var lb = isObj(r.label) ? r.label : {}; return [lb.participant, lb.market_type, r.fixture].filter(function (x) { return x; }).map(esc).join(' · '); }
  function scenRows(list) {
    return '<ul class="olist">' + list.map(function (c) {
      var r = c.payoff_range_usd;
      return '<li data-scenario="' + esc(c.condition) + '"><b>' + esc(String(c.condition).replace(/_/g, ' ').toLowerCase()) + '</b> · venue pays ' + esc(String(c.venue_payout || 'not stated').replace(/_/g, ' ').toLowerCase()) + ' · payoff ' + (Array.isArray(r) ? (CC.usdS(r[0]) || '?') + ' to ' + (CC.usdS(r[1]) || '?') : 'UNKNOWN') + ' · probability <b class="unm">UNMEASURED</b></li>';
    }).join('') + '</ul>';
  }
  function ownedCard(p) {
    p = isObj(p) ? p : {};
    var rz = p.realized || {}, rm = p.remaining, rc = p.recommendation;
    var tech = [['group id', p.group_id], ['decision id', p.decision_id], ['market slug', p.us_market_slug], ['order intent', p.intent], ['review id', rc && rc.review_id]];
    var real = '<div class="pbox real"><div class="lbl">Realized profit (booked on the ledger)</div><div class="v">' + (CC.usdS(rz.realized_pnl_usd) || '$0.00') + '</div><div class="om">' + (rz.settlement ? 'settled ' + esc(rz.settlement.outcome) + ' · payout ' + (CC.usd(rz.settlement.payout_usd) || '?') : 'nothing settled or sold yet') + '</div></div>';
    var risk = rm ? '<div class="pbox risk"><div class="lbl">Remaining risk (conditional, not realized)</div><div class="om">' + n(rm.open_qty) + ' contracts held · cost basis ' + (CC.usd(rm.cost_basis_usd) || '?') + ' · marked ' + (num(rm.marked_value_usd) ? CC.usd(rm.marked_value_usd) + ' (unrealized ' + CC.usdS(rm.unrealized_pnl_usd) + ')' : '<span class="ns">NO MARK</span>') + '</div>'
      + '<p class="lbl" style="margin:8px 0 2px">Ordinary completion</p><div class="om">wins ' + (CC.usdS((rm.ordinary_completion || {}).win_usd) || '?') + ' · loses ' + (CC.usdS((rm.ordinary_completion || {}).lose_usd) || '?') + ' · Pinnacle at entry ' + (CC.prob((rm.ordinary_completion || {}).p_pinnacle_at_decision) || '—') + (num(rm.conditional_ev_at_decision_usd) ? ' · conditional EV at entry ' + CC.usdS(rm.conditional_ev_at_decision_usd) : '') + (rm.economics_label ? ' <span class="econ">' + esc(rm.economics_label) + '</span>' : '') + '</div>'
      + '<p class="lbl" style="margin:8px 0 2px">Exceptional settlement (postponed, abandoned, suspended)</p>' + (rm.exceptional_settlement && rm.exceptional_settlement.length ? scenRows(rm.exceptional_settlement) : '') + '<p class="note" style="margin:2px 0 0">' + esc(rm.exceptional_note || '') + '</p>'
      + (rm.open_management_orders ? '<div class="om">' + n(rm.open_management_orders) + ' open protection / management order(s)</div>' : '') + '</div>' : '';
    var rec = rc ? '<div class="om">Current recommendation: <b>' + esc(rc.recommendation || 'NONE') + '</b>' + (rc.refusal ? ' · ' + refusalHtml(rc.refusal, rc.refusal_words) : '') + ' · ' + esc(rc.trigger || '') + ' · ' + (AG.toEpoch(rc.reviewed_at) !== null ? AG.ts(rc.reviewed_at) : '') + '</div>' + (isObj(rc.selection) && Object.keys(rc.selection).length ? '<details class="tech"><summary>Selection</summary>' + AG.kv(rc.selection) + '</details>' : '') : '<div class="om">No review recorded yet</div>';
    return '<div class="orow owned" data-group="' + esc(p.group_id) + '" data-strategy="' + esc(p.strategy) + '"><div class="oh">' + schip(p.strategy) + '<span class="ostat ' + (p.status === 'OPEN' ? 'OPEN' : /WON/.test(p.status) ? 'WON' : /LOST/.test(p.status) ? 'LOST' : 'SETTLED_AT_VENUE_PRICE') + '">' + esc(p.status || '?') + '</span></div>'
      + nameHtml(p) + rec + '<div class="pboxes">' + real + risk + '</div>' + CC.techDetails(tech) + '</div>';
  }
  function xavier(j) {
    var out = {}, pos = ok(j.positions) || [], setl = ok(j.settlements) || [];
    function statusOf(g) {
      var s = setl.filter(function (x) { return x.group_id === g; })[0];
      if (s) return '<span class="ostat ' + esc(s.outcome) + '">' + esc(s.outcome) + '</span>';
      var p = pos.filter(function (x) { return x.group_id === g; })[0];
      return p ? '<span class="ostat OPEN">OPEN · ' + (n(p.open_qty) || '?') + ' held</span>' : '<span class="mute">no open position in this read</span>';
    }
    var own = sec(j.owned_positions), od = ok(j.owned_positions), counts = ok(j.handoff_counts), ex = ok(j.exposure);
    var expo = ex ? '<div class="pfig6" data-exposure><div><div class="lbl">Open positions</div><div class="v">' + n(ex.open_positions) + '</div></div><div><div class="lbl">Cost basis at risk</div><div class="v">' + (CC.usd(ex.cost_basis_at_risk_usd) || '?') + '</div></div><div><div class="lbl">Marked value</div><div class="v">' + (num(ex.marked_value_usd) ? CC.usd(ex.marked_value_usd) : '<span class="ns">NOT STATED</span>') + '</div></div>'
      + '<div><div class="lbl">If every open contract loses</div><div class="v">' + (CC.usdS(ex.max_loss_usd) || '?') + '</div></div><div><div class="lbl">If every open contract wins</div><div class="v">' + (CC.usdS(ex.max_gain_usd) || '?') + '</div></div><div><div class="lbl">Realized (booked)</div><div class="v">' + (CC.usdS(ex.realized_pnl_usd) || '?') + '</div></div></div><p class="note">' + esc(ex.basis || '') + '</p>' : secBox(j.exposure, 'exposure');
    out['p-ops-handoffs'] = {status: stStatus(j.owned_positions), html: expo + (counts ? '<p class="note">Handed to Xavier: ' + counts.map(function (r) { return schip(r.strategy) + ' ' + n(r.n); }).join(' · ') + '</p>' : '') + (od ? od.map(ownedCard).join('') : secBox(own, 'owned positions'))};
    var rv = sec(j.reviews), rvd = ok(j.reviews), rc = ok(j.review_counts);
    out['p-ops-reviews'] = {status: stStatus(j.reviews), html: (rc ? '<ul class="olist">' + rc.map(function (r) { return '<li>' + schip(r.strategy) + ' <b>' + esc(r.recommendation) + '</b> × ' + n(r.n) + (AG.toEpoch(r.latest_at) !== null ? ' · latest ' + AG.ts(r.latest_at) : '') + '</li>'; }).join('') + '</ul>' : '')
      + (rvd ? rvd.map(function (r) { return '<div class="orow" data-review="' + esc(r.review_id) + '"><div class="oh">' + schip(r.strategy) + '<b>' + esc(r.recommendation || 'REFUSED') + '</b>' + (r.refusal ? '<span class="ref">' + esc(r.refusal) + '</span>' : '') + '<span class="mute">' + esc(r.trigger || '') + '</span></div><div class="om">' + (AG.toEpoch(r.reviewed_at) !== null ? AG.ts(r.reviewed_at) : '?') + '</div>' + CC.techDetails([['review id', r.review_id], ['group id', r.group_id]]) + '</div>'; }).join('') : secBox(rv, 'reviews'))};
    var so = sec(j.standing_orders), sod = ok(j.standing_orders), ss = ok(j.standing_summary);
    out['p-ops-protection'] = {status: stStatus(j.standing_orders), html: (ss ? '<ul class="olist">' + ss.map(function (r) { return '<li>' + schip(r.strategy) + ' ' + esc(r.role) + ' · ' + esc(r.state) + ' × ' + n(r.n) + (r.open ? ' <span class="ostat OPEN">OPEN</span>' : '') + '</li>'; }).join('') + '</ul>' : '')
      + (sod ? sod.map(function (o) { return '<div class="orow" data-order="' + esc(o.order_id) + '"><div class="oh">' + schip(o.strategy) + '<b>' + esc(o.role) + '</b><span>' + esc(o.direction || '') + ' ' + (n(o.qty) || '?') + ' @ ' + (CC.price(o.limit_price) || '?') + '</span><span class="ostat ' + (o.open ? 'OPEN' : 'VOID_REFUND') + '">' + esc(o.state) + '</span></div><div class="om">filled ' + (n(o.filled_qty) || '0') + ' · created ' + (AG.toEpoch(o.created_at) !== null ? AG.ts(o.created_at) : '?') + (AG.toEpoch(o.expires_at) !== null ? ' · expires ' + AG.ts(o.expires_at) : '') + '</div>' + CC.techDetails([['order id', o.order_id], ['group id', o.group_id], ['market slug', o.us_market_slug]]) + '</div>'; }).join('') : secBox(so, 'standing orders'))};
    var sc = ok(j.settlement_counts), sl = sec(j.settlements), pd = sec(j.pending_settlements), pdd = ok(j.pending_settlements);
    var pend = '<p class="lbl" style="margin:12px 0 6px">Pending settlement</p>' + (pdd ? '<p class="note">' + n(pdd.count) + ' open position(s) await the venue\'s authoritative settlement. ' + esc(pdd.rule || '') + '</p>'
      + (isObj(pdd.last_settle_step) ? '<p class="note">Last settle step' + (AG.toEpoch(pdd.last_settle_step.at) !== null ? ' at ' + AG.ts(pdd.last_settle_step.at) : '') + ': ' + ['settled', 'settled_at_venue_price', 'corrected', 'conflicts', 'waiting'].filter(function (k) { return num(pdd.last_settle_step[k]); }).map(function (k) { return esc(k.replace(/_/g, ' ')) + ' ' + n(pdd.last_settle_step[k]); }).join(' · ') + '</p>' : '')
      + (pdd.positions || []).map(function (p) { return '<div class="orow"><div class="oh">' + schip(p.strategy) + '<span class="ostat PENDING">PENDING</span><span>' + (n(p.open_qty) || '?') + ' held · cost ' + (CC.usd(p.cost_basis_usd) || '?') + '</span></div>' + nameHtml(p) + CC.techDetails([['position key', p.position_key], ['group id', p.group_id], ['market slug', p.us_market_slug]]) + '</div>'; }).join('') : (pd && pd.status === 'EMPTY' ? '<p class="note">' + esc(pd.why) + '</p>' : secBox(pd, 'pending settlements')));
    out['p-ops-settlements'] = {status: stStatus(j.settlements) === 'UNAVAILABLE' || stStatus(j.pending_settlements) === 'UNAVAILABLE' ? 'UNAVAILABLE' : (stStatus(j.settlements) === 'OK' || stStatus(j.pending_settlements) === 'OK') ? 'OK' : 'EMPTY',
      html: (sc ? '<ul class="olist">' + sc.map(function (r) { return '<li>' + schip(r.strategy) + ' <span class="ostat ' + esc(r.outcome) + '">' + esc(r.outcome) + '</span> × ' + n(r.n) + ' · paid ' + (CC.usd(r.payout_usd) || '?') + '</li>'; }).join('') + '</ul>' : '')
        + (ok(j.settlements) ? ok(j.settlements).map(function (s) { return '<div class="orow" data-settlement="' + esc(s.settlement_id) + '"><div class="oh">' + schip(s.strategy) + '<span class="ostat ' + esc(s.outcome) + '">' + esc(s.outcome) + '</span><span>' + (n(s.qty) || '?') + ' × ' + (CC.price(s.payout_per_contract) || '?') + ' = ' + (CC.usd(s.payout_usd) || '?') + '</span></div><div class="om">settled ' + (AG.toEpoch(s.settled_at) !== null ? AG.ts(s.settled_at) : '?') + ' · version ' + esc(s.version) + ' · ' + esc(s.evidence_source || '') + '</div>' + CC.techDetails([['settlement id', s.settlement_id], ['position key', s.position_key], ['group id', s.group_id]]) + '</div>'; }).join('') : secBox(sl, 'settlements'))
        + pend};
    return out;
  }

  // ── AUDREY ────────────────────────────────────────────────────────
  var ACCT = [['cash_usd', 'Cash'], ['reserved_usd', 'Reserved'], ['available_usd', 'Available'], ['total_equity_usd', 'Total equity'], ['realized_pnl_usd', 'Realized P&L'], ['unrealized_pnl_usd', 'Unrealized P&L']];
  function audrey(j) {
    var out = {}, a = sec(j.account), ad = ok(j.account);
    var figs = ad ? '<div class="pfig6">' + ACCT.map(function (f) {
      var v = ad[f[0]], shown = num(v) ? (/pnl/.test(f[0]) ? CC.usdS(v) : CC.usd(v)) : (v === null ? '<span class="ns">NOT STATED</span>' : CC.nr('not sent'));
      return '<div data-acct="' + f[0] + '"><div class="lbl">' + esc(f[1]) + '</div><div class="v">' + shown + '</div></div>';
    }).join('') + '</div><p class="note">' + esc(ad.equity_basis || '') + ' · fees paid ' + (CC.usd(ad.fees_paid_usd) || '?') + ' · ' + n(ad.open_positions) + ' open position(s) · ledger entry #' + esc(ad.last_sequence) + (ad.ledger_consistent === true ? ', running balance agrees with the ledger sum' : ad.ledger_consistent === false ? ' · <b class="neg">LEDGER INCONSISTENT</b>' : '') + ' · reserved is ' + esc(ad.reserved_is || '?') + ' · real-money submission ' + esc(ad.real_money_submission || '?') + '.</p>' : secBox(a, 'account');
    var p = sec(j.pnl_by_strategy), pdt = ok(j.pnl_by_strategy);
    var pnl = '<p class="lbl" style="margin:12px 0 6px">P&amp;L by strategy</p>' + (pdt ? '<div class="tbl"><table><thead><tr><th>strategy</th><th class="num">realized</th><th class="num">unrealized</th><th class="num">open / closed</th><th class="num">fees</th></tr></thead><tbody>' + pdt.map(function (r) {
      return '<tr data-pnl="' + esc(r.strategy) + '"><td>' + schip(r.strategy) + '</td><td class="num">' + (CC.usdS(r.realized_pnl_usd) || '?') + '</td><td class="num">' + (num(r.unrealized_pnl_usd) ? CC.usdS(r.unrealized_pnl_usd) : '<span class="ns">NOT STATED</span><br><span class="mute">marked-only ' + (CC.usdS(r.unrealized_marked_only_usd) || '?') + ', ' + n(r.unmarked_positions) + ' unmarked</span>') + '</td><td class="num">' + n(r.open_positions) + ' / ' + n(r.closed_positions) + '</td><td class="num">' + (CC.usd(r.fees_usd) || '?') + '</td></tr>';
    }).join('') + '</tbody></table></div><p class="note">Realized from every position, open or closed; unrealized from marked open positions only. Strategies are never summed with the funded book.</p>' : (p && p.status === 'EMPTY' ? CC.emptyLine(p.why) : secBox(p, 'P&L by strategy')));
    var rcs = sec(j.reconciliation), rcd = ok(j.reconciliation);
    var recon = rcd ? '<p class="recon ' + (rcd.reconciled ? 'ok' : 'bad') + '" data-reconciled="' + (rcd.reconciled ? 'yes' : 'no') + '">' + (rcd.reconciled ? 'LEDGER RECONCILED' : 'LEDGER NOT RECONCILED') + ' · ' + (Array.isArray(rcd.checks) ? rcd.checks.filter(function (c) { return c.ok; }).length + ' of ' + rcd.checks.length + ' checks pass' : '') + (rcd.failed_checks && rcd.failed_checks.length ? ' · failed: ' + rcd.failed_checks.map(esc).join(', ') : '') + ' · ' + n(rcd.entries_count) + ' ledger entries</p>'
      + (Array.isArray(rcd.checks) ? '<details class="tech"><summary>Reconciliation checks</summary>' + AG.table(rcd.checks, null, {}) + '</details>' : '') : '<p class="recon bad">RECONCILIATION ' + esc(rcs ? rcs.status : 'UNAVAILABLE') + ' · ' + esc((rcs && rcs.why) || 'not in the read') + '</p>';
    out['p-ops-account'] = {status: stStatus(j.account), html: recon + figs + pnl};
    out['p-ops-opsaudit'] = opsAuditPanel(j);
    var pf = sec(j.performance_by_strategy), pfd = ok(j.performance_by_strategy);
    out['p-ops-performance'] = {status: stStatus(j.performance_by_strategy), html: pfd ? pfd.map(function (r) {
      var st_ = isObj(r.settled) ? Object.keys(r.settled).map(function (k) { return n(r.settled[k]) + ' ' + esc(k); }).join(' · ') : '';
      return '<div class="orow" data-perf="' + esc(r.strategy) + '"><div class="oh">' + schip(r.strategy) + '</div><div class="om">' + n(r.decisions) + ' decisions · ' + n(r.enter) + ' ENTER · ' + n(r.entry_orders) + ' entry orders (' + n(r.filled_orders) + ' filled) · ' + n(r.open_positions) + ' open / ' + n(r.closed_positions) + ' closed' + (st_ ? ' · settled ' + st_ : '') + '</div><div class="om">realized ' + (CC.usdS(r.realized_pnl_usd) || '$0.00') + ' (booked)</div></div>';
    }).join('') + '<p class="note">Each strategy on its own; never summed with the funded system.</p>' : secBox(pf, 'performance')};
    var fs = sec(j.findings), fd = ok(j.findings), fc = ok(j.finding_counts), ea = sec(j.event_audits), ead = ok(j.event_audits);
    var evh = '<p class="lbl" style="margin:0 0 6px">Event-driven audits (one per event)</p>' + (ead ? ead.slice(0, 12).map(function (f) {
      var d = isObj(f.detail) ? f.detail : {};
      return '<div class="orow" data-event-audit="' + esc(f.finding_id) + '"><div class="oh"><span class="ostat ' + (f.severity === 'INFO' ? 'OPEN' : 'LOST') + '">' + esc(f.severity) + '</span><b>' + esc(String(f.kind || '').replace(/^PAPER_EVENT_/, '').replace(/_/g, ' ').toLowerCase()) + '</b>' + (d.passed === true ? '<span class="ostat OPEN">PASSED</span>' : d.passed === false ? '<span class="ostat LOST">FAILED</span>' : '') + (d.strategy ? schip(d.strategy) : '') + '</div><div class="om">' + (AG.toEpoch(f.found_at) !== null ? AG.ts(f.found_at) : '?') + (f.improvement_task_id ? ' · improvement task ' + esc(f.improvement_task_id) : '') + '</div>' + CC.techDetails([['finding id', f.finding_id], ['subject', f.subject], ['chain', d.chain], ['detail', JSON.stringify(d)]]) + '</div>';
    }).join('') : (ea && ea.status === 'EMPTY' ? '<p class="note">' + esc(ea.why) + '</p>' : secBox(ea, 'event audits')));
    out['p-ops-findings'] = {status: stStatus(j.event_audits) === 'OK' ? 'OK' : stStatus(j.findings), html: evh + '<p class="lbl" style="margin:12px 0 6px">All findings by kind</p>' + (fc ? '<ul class="olist">' + fc.map(function (r) { return '<li><span class="ostat ' + (r.severity === 'INFO' ? 'OPEN' : 'LOST') + '">' + esc(r.severity) + '</span> ' + esc(r.kind) + ' × ' + n(r.n) + '</li>'; }).join('') + '</ul>' : '')
      + (fd ? fd.map(function (f) { return '<div class="orow" data-finding="' + esc(f.finding_id) + '"><div class="oh"><span class="ostat ' + (f.severity === 'INFO' ? 'OPEN' : 'LOST') + '">' + esc(f.severity) + '</span><b>' + esc(f.kind) + '</b></div><div class="om">' + (AG.toEpoch(f.found_at) !== null ? AG.ts(f.found_at) : '?') + (f.improvement_task_id ? ' · task ' + esc(f.improvement_task_id) : '') + '</div>' + CC.techDetails([['finding id', f.finding_id], ['subject', f.subject], ['detail', JSON.stringify(f.detail || {})]]) + '</div>'; }).join('') : secBox(fs, 'findings'))};
    out['p-ops-learning'] = learningPanel(j);
    var r = sec(j.daily_report), rd = ok(j.daily_report), list = ok(j.daily_reports);
    out['p-ops-report'] = {status: stStatus(j.daily_report), html: rd ? '<dl class="pdl"><dt>day</dt><dd>' + esc(rd.report_day) + ' (' + esc(rd.reporting_tz || '') + ')</dd><dt>version</dt><dd>' + esc(rd.version) + (rd.final ? ' · final' : ' · provisional until the day closes') + '</dd><dt>generated</dt><dd>' + (AG.toEpoch(rd.generated_at) !== null ? AG.ts(rd.generated_at) : '?') + '</dd><dt>reconciles</dt><dd>' + (rd.reconciles === true ? 'YES' : rd.reconciles === false ? '<b class="neg">NO</b>' : '?') + '</dd>'
        + (Array.isArray(rd.benchmark_sections) && rd.benchmark_sections.length ? '<dt>labelled sections</dt><dd>' + rd.benchmark_sections.map(esc).join(', ') + '</dd>' : '') + '</dl>'
        + (isObj(rd.summary) && Object.keys(rd.summary).length ? '<details class="tech"><summary>Report figures</summary>' + AG.kv(rd.summary) + '</details>' : '')
        + (Array.isArray(rd.reconciliation_checks) ? '<details class="tech"><summary>Reconciliation checks (' + rd.reconciliation_checks.length + ')</summary>' + AG.table(rd.reconciliation_checks, null, {}) + '</details>' : '')
        + (list && list.length > 1 ? '<p class="lbl" style="margin:12px 0 6px">Earlier days</p><ul class="olist">' + list.slice(1).map(function (x) { return '<li>' + esc(x.report_day) + ' · v' + esc(x.version) + (x.final ? ' · final' : '') + (x.reconciles === false ? ' · <b class="neg">does not reconcile</b>' : '') + '</li>'; }).join('') + '</ul>' : '')
      : secBox(r, 'daily report')};
    return out;
  }

  function learningPanel(j) {
    var l = sec(j.learning), ld = ok(j.learning);
    if (!ld) return {status: l ? l.status : 'UNAVAILABLE', html: secBox(l, 'learning')};
    var ag = isObj(ld.agents) ? ld.agents : {}, ac = ok(ld.activation_control);
    var html = '<p class="note">Forward records only. A proposal becomes ACTIVE only after an evaluated PASS, every activation check, a named human approver and the activation control' + (ac ? ' (currently <b>' + (ac.enabled ? 'ON' : 'OFF') + '</b>)' : '') + '. Counterfactual results are labelled ' + esc(ld.counterfactual_label || 'COUNTERFACTUAL') + '.</p>';
    var any = false;
    ['DEREK', 'XAVIER', 'AUDREY'].forEach(function (a) {
      var s_ = sec(ag[a]), d = ok(ag[a]);
      html += '<div class="orow" data-learning-agent="' + a + '"><div class="oh"><b>' + esc(a.charAt(0) + a.slice(1).toLowerCase()) + '</b></div>';
      if (!d) { html += (s_ && s_.status === 'EMPTY' ? '<div class="om">' + esc(s_.why) + '</div>' : secBox(s_, a + ' learning')) + '</div>'; return; }
      any = true;
      var pc = isObj(d.proposed_change) ? d.proposed_change : {}, ev = isObj(d.evaluation) ? d.evaluation : null;
      html += '<div class="om">Learned: ' + ((d.learned || []).length ? '</div><ul class="olist">' + d.learned.slice(0, 4).map(function (x) { return '<li>' + esc(x.statement) + ' <span class="mute">(' + n(x.records) + ' records, v' + esc(x.version) + ')</span></li>'; }).join('') + '</ul>' : 'nothing yet</div>');
      if (pc.status === 'NONE') html += '<div class="om">Proposed change: none · ' + esc(pc.why || '') + '</div>';
      else {
        var ch = isObj(pc.proposed_change) ? pc.proposed_change : {};
        var words = ch.parameter ? esc(String(ch.parameter).replace(/_/g, ' ')) + ' from ' + esc(ch.from) + ' to ' + esc(ch.to) + (ch.units ? ' ' + esc(ch.units) : '') + (ch.applies_to ? ' for ' + esc(ch.applies_to) : '') : esc(JSON.stringify(ch));
        var la = ev && isObj(ev.last_attempt) ? ev.last_attempt : {};
        var ep = Array.isArray(pc.evaluation_period) ? pc.evaluation_period : [];
        html += '<div class="om" data-proposal="' + esc(pc.proposal_id || '') + '">Proposed change: <b>' + words + '</b> · ' + esc(pc.rationale || '') + '</div>'
          + '<div class="om">Evaluation: <b class="evst">' + esc(ev ? ev.status : 'NOT EVALUATED') + '</b>' + (ev && ev.verdict ? ' · verdict ' + esc(ev.verdict) : '') + (num(la.outcomes) && num(la.min_evaluation_outcomes) ? ' · ' + n(la.outcomes) + ' of ' + n(la.min_evaluation_outcomes) + ' forward outcomes so far' : '') + (AG.toEpoch(ep[1]) !== null ? ' · evaluation period ends ' + AG.ts(ep[1]) : '') + ' · activation: <b>' + (d.active ? 'ACTIVE' : 'NOT ACTIVE') + '</b></div>'
          + CC.techDetails([['proposal id', pc.proposal_id], ['change class', pc.change_class], ['proposed change', JSON.stringify(ch)], ['evaluation detail', la.why], ['counts', JSON.stringify(la.counts || {})], ['training period', JSON.stringify(pc.training_period || [])], ['evaluation period', JSON.stringify(pc.evaluation_period || [])], ['source lessons', JSON.stringify(pc.source_lesson_ids || [])]]);
      }
      html += '</div>';
    });
    return {status: any ? 'OK' : 'EMPTY', html: html};
  }
  // ── THE ONE ACCOUNT, THE SAME ON EVERY PAGE ──────────────────────
  var ACCT7 = %%ACCOUNT_FIGURES%%;
  function figText(k, v) { return num(v) ? (/pnl/.test(k) ? CC.usdS(v) : CC.usd(v)) : v === null ? 'NOT STATED' : 'not sent'; }
  function accountFigures(a) { return ACCT7.map(function (f) { return {key: f[0], label: f[1], value: figText(f[0], isObj(a) ? a[f[0]] : undefined)}; }); }
  function accountStrip(st) {
    st = st || {}; var j = isObj(st.json) ? st.json : null, f = st.fail;
    if (!j) return {status: f && f.state === SIGNIN ? SIGNIN : 'UNAVAILABLE', html: '<div class="lbl">Paper account</div>' + failBox(f || {state: 'UNAVAILABLE', why: 'not read yet'})};
    var s_ = sec(j.account), a = ok(j.account);
    if (!a) return {status: s_ ? s_.status : 'UNAVAILABLE', html: '<div class="lbl">Paper account</div>' + secBox(s_, 'account')};
    return {status: 'OK', html: '<div class="lbl">Paper account · one ledger · LIVE MARKET DATA · SIMULATED EXECUTION</div><div class="acct7">' + accountFigures(a).map(function (x) {
      return '<div data-acct7="' + x.key + '"><small>' + esc(x.label) + '</small><b>' + (x.value === 'NOT STATED' ? '<span class="ns">NOT STATED</span>' : esc(x.value)) + '</b></div>'; }).join('') + '</div>'
      + '<p class="note" style="margin:6px 0 0">' + esc(a.equity_basis || '') + (num(a.last_sequence) ? ' · ledger entry #' + a.last_sequence : '') + ' · the same read on the homepage and every agent page' + (f ? ' · <b class="neg">last read failed: ' + esc(f.why) + '</b>' : '') + '</p>'};
  }
  // ── SIGNED OUT: A PROMPT, NEVER AN UNEXPLAINED FAILURE ───────────
  function signinHtml(framed, why) {
    return '<b>SIGN-IN REQUIRED</b> · ' + esc(why || 'your COMMAND session is missing or has expired') + '. Nothing below is current until you sign in. '
      + '<a class="cc-signin-go" href="' + (framed ? '%%SIGNIN%%' : AG.DESK) + '"' + (framed ? ' target="_top"' : '') + '>Sign in to COMMAND →</a> <span class="mute">Reads keep retrying every 15 s and resume on their own once you are signed in.</span>';
  }
  // ── ONE READ, EVERY PANEL ────────────────────────────────────────
  function panels(kind, st) {
    st = st || {}; var j = isObj(st.json) ? st.json : null, f = st.fail;
    var ids = {derek: ['p-ops-standing', 'p-ops-bench', 'p-ops-research'], xavier: ['p-ops-handoffs', 'p-ops-reviews', 'p-ops-protection', 'p-ops-settlements'], audrey: ['p-ops-account', 'p-ops-opsaudit', 'p-ops-performance', 'p-ops-findings', 'p-ops-learning', 'p-ops-report']}[kind] || [];
    var out = {};
    if (!j) { ids.forEach(function (id) { out[id] = {status: f && f.state === SIGNIN ? SIGNIN : 'UNAVAILABLE', html: failBox(f || {state: 'UNAVAILABLE', why: 'not read yet'})}; }); return out; }
    var r = kind === 'derek' ? derek(j) : kind === 'xavier' ? xavier(j) : audrey(j);
    var stale = f ? '<p class="ops-stale" data-ops-stale>LAST READ FAILED · ' + esc(f.state) + ': ' + esc(f.why) + '. Showing the last successful read' + (num(st.okAt) ? ' of ' + new Date(st.okAt * 1000).toISOString().slice(11, 19) + 'Z' : '') + '.</p>' : '';
    ids.forEach(function (id) { var p = r[id] || {status: 'UNAVAILABLE', html: failBox({state: 'UNAVAILABLE', why: 'no renderer output'})}; out[id] = {status: p.status, html: stale + p.html}; });
    return out;
  }
  function stageLine(st, now) {
    var m = mode(st, now);
    return {state: m.mode === 'unavailable' ? '<span class="sb sb-UNRECOGNISED"><i></i>PAPER RUNTIME ' + (st && st.fail && !st.json ? esc(st.fail.state) : 'UNAVAILABLE') + '</span>' : '<span class="sb sb-IDLE"><i></i>PAPER RUNTIME RUNNING</span>',
            hb: m.why ? esc(m.why) : esc(m.activity || '')};
  }
  CC.ops = {ACCT7: ACCT7, accountFigures: accountFigures, accountStrip: accountStrip, signinHtml: signinHtml, ownedCard: ownedCard, orderRow: orderRow, learningPanel: learningPanel, SIGNIN: SIGNIN, failure: failure, failBox: failBox, fresh: fresh, freshHtml: freshHtml, mode: mode, kpis: kpis, panels: panels, stageLine: stageLine,
            derek: derek, xavier: xavier, audrey: audrey, decRow: decRow};
})(AG, CC);
"""

import json as _json

OPS_CORE_JS = (_OPS_CORE_JS_RAW
               .replace("%%ACCOUNT_FIGURES%%",
                        _json.dumps([list(x) for x in ACCOUNT_FIGURES]))
               .replace("%%SIGNIN%%", SIGNIN_HREF))
