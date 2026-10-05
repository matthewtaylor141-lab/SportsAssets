"""EDDIE'S AND SCOUT'S DESKS ON THEIR COMMAND CENTRE PAGES (migration 217).

Each page leads with the agent's desk: the live 3D character at an original,
procedural desk (cc_characters.js -- Eddie an institutional execution desk
with order-book / depth screens, Scout a research desk with sports-feed,
feature-test and weather / data panels) and, beside it, the desk panels.

EVERY VALUE ON THE DESK COMES FROM THE AGENT'S ENDPOINT. The page's data
module reads /api/command/eddie or /scout (the shared BOOT_JS) and hands the
JSON to `DESK.render`, which fills the panels, sets the character's pose
from the persisted heartbeat (`cc:mode`, the same rule as the other agents:
stale or FAILED is UNAVAILABLE) and redraws the 3D screens (`cc:desk`). A
value the record does not carry is shown as NOT MEASURED with its reason;
nothing animates as if meaningful when the read failed.

NO AUTHORITY IS IMPLIED. There is no submit, trade, approve, promote or
send control anywhere on the desk; the authority badge says SHADOW ONLY /
RESEARCH SHADOW ONLY in words.
"""
from __future__ import annotations

DESK_META = {
    "eddie": {"name": "Eddie", "role": "Head of Execution",
              "authority": "SHADOW ONLY · NO ORDER, CANCEL, VENUE OR "
                           "CAPITAL AUTHORITY",
              "blurb": ("Preserves Derek's theoretical edge between decision "
                        "and fill: spread, slippage, fees, adverse "
                        "selection, fill probability and capital-hours, "
                        "from recorded books and fills. Recommends in "
                        "SHADOW; never when the expected executable EV is "
                        "not positive. Does not predict outcomes."),
              "accent": "#5fb7ff"},
    "scout": {"name": "Scout", "role": "Market Intelligence",
              "authority": "RESEARCH SHADOW ONLY · NO TRADE, PORTFOLIO, "
                           "APPROVAL OR PROMOTION AUTHORITY",
              "blurb": ("Finds external information, ingests only sources "
                        "that pass the declared compliance check, and tests "
                        "each feature prospectively against PinnAPI. A "
                        "feature without out-of-sample value is REJECTED; "
                        "the verdict is never his."),
              "accent": "#6fd39a"},
}

#: The desk panels, in order: (key, label). Eddie's and Scout's lists are
#: the owner's acceptance standard.
PANELS = {
    "eddie": (("heartbeat", "Heartbeat"), ("current_task", "Current task"),
              ("alerts", "Alerts"),
              ("analysis", "Current execution analysis"),
              ("candidate", "Candidate contract"),
              ("fill", "Expected fill"),
              ("slippage", "Expected slippage"),
              ("policy", "Execution policy"),
              ("microstructure", "Microstructure"),
              ("capital_hours", "Capital-hours"),
              ("predicted_realized", "Predicted vs realized execution loss"),
              ("hypothesis", "Hypothesis"),
              ("finding", "Recent finding"),
              ("economic", "Historical economic contribution")),
    "scout": (("heartbeat", "Heartbeat"), ("current_task", "Current task"),
              ("alerts", "Alerts"),
              ("searches", "Active intelligence searches"),
              ("sources", "Data sources"),
              ("under_test", "Features under test"),
              ("hypothesis", "Hypotheses"), ("experiments", "Experiments"),
              ("verdicts", "Validated / rejected features"),
              ("finding", "Recent finding"),
              ("predictive", "Incremental predictive value"),
              ("economic", "Historical economic contribution")),
}

DESK_CSS = r"""
.pd{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:18px;margin:0 0 18px}
.pd *{min-width:0}
.pd-stage{position:sticky;top:64px;align-self:start;height:600px;max-height:calc(100vh - 80px);min-height:420px;border-radius:16px;overflow:hidden;border:1px solid var(--line-2);background:radial-gradient(120% 90% at 50% 0%,#141c27 0%,#090d13 60%,#05070a 100%)}
.pd-stage canvas{position:absolute;inset:0;width:100%;height:100%;display:block;opacity:0;transition:opacity .5s ease}
.pd-stage.cc-3d-on canvas{opacity:1}
.pd-stage .pd-fb{position:absolute;inset:0;display:flex;align-items:flex-end;justify-content:center}
.pd-stage.cc-3d-on .pd-fb{display:none}
.pd-fb svg{width:100%;height:100%}
.pd-ov{position:absolute;left:0;right:0;top:0;padding:14px 16px;background:linear-gradient(180deg,rgba(3,5,8,.85),rgba(3,5,8,0));pointer-events:none}
.pd-ov .pd-ai{display:inline-block;font:700 10px/1 var(--mono);letter-spacing:.16em;padding:4px 7px;border-radius:5px;border:1px solid var(--pd-acc);color:var(--pd-acc)}
.pd-ov h1{margin:6px 0 2px;font:600 34px/1.05 var(--display);color:#fff}
.pd-ov .pd-role{font:650 11px/1.2 var(--mono);letter-spacing:.18em;text-transform:uppercase;color:var(--pd-acc)}
.pd-auth{position:absolute;left:0;right:0;bottom:0;padding:10px 14px;background:linear-gradient(0deg,rgba(3,5,8,.92),rgba(3,5,8,0));font:650 11px/1.35 var(--mono);letter-spacing:.06em;color:#e6c56a}
.pd-why{position:absolute;right:12px;top:12px;font:600 10px/1.3 var(--mono);color:#7f8a99;max-width:45%;text-align:right}
.pd-panels{display:grid;grid-template-columns:1fr 1fr;gap:10px;align-content:start}
.pd-p{border:1px solid var(--line);border-radius:12px;padding:10px 12px;background:var(--panel);overflow-wrap:anywhere}
.pd-p.w{grid-column:1/-1}
.pd-p h4{margin:0 0 6px;font:650 10.5px/1.2 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--ink-3)}
.pd-p .v{font:600 15px/1.35 var(--sans);color:var(--ink)}
.pd-p .s{font:12px/1.4 var(--mono);color:var(--ink-2)}
.pd-p .nm{font:700 11.5px/1.3 var(--mono);color:#e0a94a}
.pd-p ul{margin:4px 0 0;padding-left:16px;font-size:12.5px}
.pd-depth{display:grid;grid-template-columns:auto 1fr auto;gap:4px 8px;align-items:center;font:12px/1.2 var(--mono)}
.pd-depth .bar{height:8px;border-radius:3px;background:var(--pd-acc)}
.pd-blurb{margin:8px 0 0;font-size:13px;color:var(--ink-2)}
body[data-cc-mode=unavailable] .pd-stage{filter:saturate(.4)}
/* phone width: long identity chips (versions, mandates) wrap, never scroll */
body.ag-eddie .ident>div,body.ag-scout .ident>div{min-width:0;flex:1 1 auto}
body.ag-eddie .chip,body.ag-scout .chip{white-space:normal;overflow-wrap:anywhere;max-width:100%}
body.ag-eddie .mandate,body.ag-scout .mandate{overflow-wrap:anywhere}
body.ag-eddie main,body.ag-scout main{overflow-x:clip}
@media (max-width:980px){.pd{grid-template-columns:1fr}.pd-stage{position:relative;top:0;height:440px;min-height:340px}}
@media (max-width:560px){.pd-panels{grid-template-columns:1fr}.pd-ov h1{font-size:26px}.pd-stage{height:340px;min-height:300px}}
"""


def _fallback_svg(kind: str) -> str:
    """The 2D desk, original vector art: the desk and its screen frames
    only. The screens are blank frames here -- the numbers live in the desk
    panels, drawn from the record."""
    acc = DESK_META[kind]["accent"]
    if kind == "eddie":
        frames = ('<rect x="40" y="150" width="150" height="90" rx="6"/>'
                  '<rect x="225" y="70" width="190" height="110" rx="6"/>'
                  '<rect x="450" y="150" width="150" height="90" rx="6"/>')
        extra = ('<line x1="40" y1="292" x2="600" y2="292" stroke="#8a97a8" '
                 'stroke-width="2"/>')
    else:
        frames = ('<rect x="40" y="150" width="150" height="90" rx="6"/>'
                  '<rect x="225" y="70" width="190" height="110" rx="6"/>'
                  '<rect x="450" y="150" width="150" height="90" rx="6"/>')
        extra = "".join('<rect x="%d" y="250" width="12" height="36" '
                        'fill="%s"/>' % (60 + i * 16, c) for i, c in
                        enumerate(["#3d5a40", "#6a4e2d", "#2f4a5a"] * 3))
    return ('<svg viewBox="0 0 640 400" role="img" aria-label="%s\'s desk '
            '(2D): desk and screen frames; the values are in the panels">'
            '<rect width="640" height="400" fill="#070a0f"/>'
            '<g fill="#04070b" stroke="%s" stroke-opacity=".55" '
            'stroke-width="2">%s</g>'
            '<rect x="20" y="288" width="600" height="14" rx="3" '
            'fill="#151a22"/>%s'
            '<circle cx="320" cy="210" r="34" fill="#1b2230"/>'
            '<rect x="282" y="246" width="76" height="60" rx="18" '
            'fill="#1b2230"/></svg>' % (DESK_META[kind]["name"], acc, frames,
                                        extra))


def desk_html(kind: str) -> str:
    m = DESK_META[kind]
    panels = "".join(
        '<div class="pd-p%s" data-desk-panel="%s"><h4>%s</h4>'
        '<div class="v" data-desk-field="%s"><span class="nm">reading'
        '&#8230;</span></div></div>' % (
            " w" if key in ("alerts", "analysis", "microstructure",
                            "predicted_realized", "searches", "sources",
                            "under_test", "experiments", "hypothesis",
                            "verdicts") else "", key, label, key)
        for key, label in PANELS[kind])
    return (
        '<section class="pd" id="desk" data-desk="%(k)s" style="--pd-acc:'
        '%(acc)s" aria-label="%(name)s\'s desk">'
        '<div class="pd-stage cc-stage" id="cc-stage" data-agent="%(k)s" '
        'data-cc-3d="off"><canvas aria-hidden="true"></canvas>'
        '<div class="pd-fb" id="cc-fallback">%(svg)s</div>'
        '<div class="pd-ov"><span class="pd-ai">&#9679; AI AGENT &#183; '
        'LIVE DESK</span><h1>%(name)s</h1><div class="pd-role">%(role)s'
        '</div></div><div class="pd-why" id="cc-fbnote"></div>'
        '<div class="pd-auth" data-authority>%(auth)s</div></div>'
        '<div><div class="pd-panels" id="desk-panels">%(panels)s</div>'
        '<p class="pd-blurb">%(blurb)s</p></div></section>' % {
            "k": kind, "acc": m["accent"], "name": m["name"],
            "role": m["role"], "auth": m["authority"], "blurb": m["blurb"],
            "svg": _fallback_svg(kind), "panels": panels})


# The pure half (`DESK.fields`) has no DOM access at definition time, so the
# tests run it under node against real payloads.
DESK_JS = r"""
var DESK = (function () {
  'use strict';
  var NM = 'NOT MEASURED';
  function esc(v) { return String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); }
  function isObj(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
  function num(v, d) { return typeof v === 'number' && isFinite(v) ? (Math.abs(v) < 1 && v !== 0 ? v.toFixed(d === undefined ? 4 : d) : String(Math.round(v * 100) / 100)) : null; }
  function nm(why) { return '<span class="nm">' + NM + '</span>' + (why ? '<div class="s">' + esc(why) + '</div>' : ''); }
  function val(v, why, d) { var n = num(v, d); return n === null ? (v === null || v === undefined ? nm(why) : esc(v)) : esc(n); }
  function list(xs, fn, empty) { return Array.isArray(xs) && xs.length ? '<ul>' + xs.map(function (x) { return '<li>' + fn(x) + '</li>'; }).join('') + '</ul>' : nm(empty); }
  function age(hb, rd) { return typeof hb === 'number' && typeof rd === 'number' ? Math.max(0, Math.round(rd - hb)) + ' s ago' : 'never recorded'; }
  // THE CHARACTER'S MODE: the persisted status record decides (the same
  // rule as the other agents' pages); stale / FAILED / unread = unavailable
  function modeOf(a, rd) {
    if (!isObj(a) || !a.state) return {mode: 'unavailable', why: (a && a.why) || 'no status recorded'};
    var hb = typeof a.last_heartbeat_at === 'number' ? a.last_heartbeat_at : null;
    if (hb === null) return {mode: 'unavailable', why: 'no heartbeat is recorded'};
    var iv = isObj(a.cadence) && typeof a.cadence.target_interval_s === 'number' ? a.cadence.target_interval_s : 300;
    var lim = Math.max(900, 3 * iv);
    if (typeof rd === 'number' && rd - hb > lim) return {mode: 'unavailable', why: 'heartbeat stale (' + Math.round(rd - hb) + ' s)'};
    if (a.state === 'FAILED') return {mode: 'unavailable', why: 'the agent recorded FAILED'};
    var m = {IDLE: 'monitoring', DECISION_RECORDED: 'monitoring', EVALUATING: 'reviewing', RECOVERING: 'reviewing', WAITING_FOR_EVIDENCE: 'waiting', WAITING_FOR_PROVIDER: 'waiting', BLOCKED: 'waiting'}[a.state];
    return m ? {mode: m, why: null} : {mode: 'unavailable', why: 'unrecognised state ' + a.state};
  }
  function common(d, json) {
    var hb = isObj(d.heartbeat) ? d.heartbeat : {};
    return {
      heartbeat: hb.state ? esc(hb.state) + '<div class="s">' + esc(age(hb.last_heartbeat_at, json.read_at)) + '</div>' : nm('the runner has not recorded a heartbeat'),
      current_task: d.current_task ? esc(d.current_task) : nm('no activity recorded'),
      alerts: Array.isArray(d.alerts) && d.alerts.length ? list(d.alerts, esc) : '<span class="s">none recorded</span>',
      finding: d.recent_finding ? esc(d.recent_finding) : nm('no finding recorded yet'),
      economic: isObj(d.economic_score) ? (d.economic_score.value === null || d.economic_score.value === undefined ? nm(d.economic_score.why) : esc(num(d.economic_score.value, 2)) + ' USD<div class="s">' + esc(d.economic_score.name) + '</div>') : nm('no score in this record')};
  }
  function eddie(d, json) {
    var f = common(d, json), a = d.current_analysis, u = isObj(a) && isObj(a.unmeasured) ? a.unmeasured : {};
    var pv = d.predicted_vs_realized;
    if (!isObj(a)) {
      ['analysis', 'candidate', 'fill', 'slippage', 'policy', 'microstructure', 'capital_hours'].forEach(function (k) { f[k] = nm('no candidate has been estimated yet'); });
    } else {
      var c = isObj(a.candidate) ? a.candidate : {};
      f.analysis = '<b>' + esc(a.recommendation) + '</b> <span class="s">(SHADOW · ' + esc(a.estimate_id) + ')</span><div class="s">' + esc(a.reason) + '</div><div class="s">theoretical edge ' + val(a.theoretical_edge_pp, u.theoretical_edge) + ' · net executable edge ' + val(a.expected_net_executable_edge_pp, u.net_executable_edge) + '</div>';
      f.candidate = esc(c.us_market_slug) + ' ' + esc(c.holding_side) + '<div class="s">' + esc(c.decision_id) + ' · qty ' + esc(num(c.proposed_qty, 2)) + ' ≤ ' + esc(num(c.limit_price, 3)) + '</div>';
      // R30C: a fill probability is always shown with the class it was fitted on (the paper simulator's rate is never live execution quality)
      var fe = isObj(a.fill_probability_evidence) ? a.fill_probability_evidence : {};
      f.fill = 'probability ' + val(a.expected_fill_probability, u.fill_probability, 3) + '<div class="s">fitted on ' + esc(fe.fitted_on || 'UNLABELLED') + (fe.live_ci_low !== undefined && fe.live_ci_low !== null ? ' · live interval ' + esc(num(fe.live_ci_low, 3)) + '–' + esc(num(fe.live_ci_high, 3)) : '') + ' · ' + esc(fe.live_use || 'not proof of live execution') + '</div><div class="s">max executable size ' + val(a.max_executable_qty, u.max_executable_size, 0) + '</div>';
      f.slippage = val(a.expected_slippage_pp, u.slippage);
      f.policy = a.execution_policy ? esc(a.execution_policy) : nm('no policy recorded');
      var ms = isObj(a.microstructure) ? a.microstructure : {}, b = isObj(a.book) ? a.book : {};
      var depth = b.best_acquisition !== undefined && b.best_acquisition !== null
        ? '<div class="pd-depth"><span>ask</span><span class="bar" style="width:' + Math.round(100 * Math.min(1, b.best_acquisition)) + '%"></span><span>' + esc(num(b.best_acquisition, 3)) + '</span><span>mid</span><span class="bar" style="opacity:.6;width:' + Math.round(100 * Math.min(1, b.mid || 0)) + '%"></span><span>' + val(b.mid, 'no mid', 3) + '</span><span>bid</span><span class="bar" style="opacity:.35;width:' + Math.round(100 * Math.min(1, b.best_exit || 0)) + '%"></span><span>' + val(b.best_exit, 'no exit side', 3) + '</span></div>' : nm('no recorded book');
      f.microstructure = depth + '<div class="s">' + Object.keys(ms).map(function (k) { var x = ms[k] || {}; return esc(k) + ': ' + (x.status === 'MEASURED' ? 'measured' : 'UNAVAILABLE (' + esc(x.why) + ')'); }).join(' · ') + '</div>';
      f.capital_hours = val(a.expected_capital_hours, u.capital_hours, 2);
    }
    f.predicted_realized = isObj(pv) ? 'predicted ' + val(pv.predicted_execution_loss_pp, 'the estimate left it unmeasured') + ' · realized ' + val(pv.realized_execution_loss_pp, 'not measurable') + '<div class="s">' + esc(pv.outcome_id) + '</div>' : nm('no estimated candidate has filled yet');
    f.hypothesis = d.hypothesis ? esc(d.hypothesis) : nm('no estimate yet');
    return f;
  }
  function scout(d, json) {
    var f = common(d, json);
    f.searches = list(d.active_searches, function (x) { return '<b>' + esc(x.feature) + '</b> <span class="s">' + esc(x.source_id) + '</span>'; }, 'no declared search');
    f.sources = list(d.data_sources, function (x) { return (x.compliance_passed ? '<b>PASS</b> ' : '<b>REFUSED</b> ') + esc(x.name) + ' <span class="s">' + esc(x.licensing_class) + '</span>'; }, 'no source declared');
    f.under_test = list(d.features_under_test, function (x) { return esc(x.feature) + ' <span class="s">' + esc(x.samples_settled) + '/' + esc(x.min_sample) + ' settled · ' + esc(x.samples_frozen) + ' frozen · ' + esc(x.observations) + ' observations</span>'; }, 'no feature under test');
    f.hypothesis = d.hypothesis ? esc(d.hypothesis) : nm('no hypothesis under test');
    f.experiments = list(d.experiments, function (x) { return esc(x.tournament_id) + ' <span class="s">' + esc(x.metric) + ' · ' + esc(x.progress) + '</span>'; }, 'no forward test frozen');
    f.verdicts = list(d.validated_rejected, function (x) { return '<b>' + esc(x.state) + '</b> ' + esc(x.feature) + ' <span class="s">improvement ' + val(x.improvement, 'n/a') + '</span>'; }, 'no tournament has reached its predeclared minimum sample');
    var ip = d.incremental_predictive_value;
    f.predictive = isObj(ip) ? val(ip.value, ip.why) : nm('not in this record');
    return f;
  }
  // PURE: the desk panels' HTML from the endpoint JSON
  function fields(kind, json) {
    json = isObj(json) ? json : {};
    var d = isObj(json.desk) ? json.desk : (isObj(json.sections) && isObj(json.sections.desk) && isObj(json.sections.desk.data) ? json.sections.desk.data : null);
    if (!d) return {_unavailable: 'the endpoint returned no desk record'};
    return kind === 'eddie' ? eddie(d, json) : scout(d, json);
  }
  function setAll(html) {
    if (typeof document === 'undefined') return;
    [].forEach.call(document.querySelectorAll('[data-desk-field]'), function (el) { el.innerHTML = html; });
  }
  function emit(name, detail) { try { window.dispatchEvent(new CustomEvent(name, {detail: detail})); } catch (_) { /* ignore */ } }
  function render(kind, json) {
    var f = fields(kind, json);
    if (f._unavailable) { setAll(nm(f._unavailable)); fail({kind: 'NO_DESK'}); return f; }
    Object.keys(f).forEach(function (k) { var el = document.querySelector('[data-desk-field="' + k + '"]'); if (el) el.innerHTML = f[k]; });
    var m = modeOf(json.agent, json.read_at);
    document.body.setAttribute('data-cc-mode', m.mode);
    var note = document.getElementById('cc-fbnote');
    if (note && m.why) note.textContent = 'UNAVAILABLE · ' + m.why;
    else if (note && note.textContent.indexOf('UNAVAILABLE') === 0) note.textContent = '';
    window.__DESK_LAST = json.desk || null;
    emit('cc:mode', {mode: m.mode, why: m.why});
    emit('cc:desk', {desk: json.desk || null});
    return f;
  }
  function fail(o) {
    setAll('<span class="nm">UNAVAILABLE</span><div class="s">' + esc((o && o.kind) || 'read failed') + '</div>');
    if (typeof document !== 'undefined') document.body.setAttribute('data-cc-mode', 'unavailable');
    emit('cc:mode', {mode: 'unavailable', why: 'the desk read failed'});
    emit('cc:desk', {desk: null});
  }
  return {fields: fields, modeOf: modeOf, render: render, fail: fail, NM: NM};
})();
// the shared BOOT_JS calls CC.onWorkspace / onWorkspaceFail when present
var CC = {onWorkspace: function (kind, json) { DESK.render(kind, json); }, onWorkspaceFail: function (o) { DESK.fail(o); }};
"""

# The character module is loaded only after the same capability checks as
# the other agents' pages; a failure keeps the 2D desk and every panel.
DESK_LOADER_JS = r"""
(function () {
  var stage = document.getElementById('cc-stage');
  if (!stage) return;
  var note = document.getElementById('cc-fbnote');
  function keep2d(why) { stage.setAttribute('data-cc-3d', 'off'); stage.setAttribute('data-cc-why', why); if (note && !note.textContent) note.textContent = '2D DESK · ' + why; }
  var nav = window.navigator || {};
  var conn = nav.connection || {};
  var reduced = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  if (conn.saveData) return keep2d('save-data is on');
  if (typeof nav.hardwareConcurrency === 'number' && nav.hardwareConcurrency <= 2) return keep2d('low-power device (' + nav.hardwareConcurrency + ' cores)');
  if (typeof nav.deviceMemory === 'number' && nav.deviceMemory <= 2) return keep2d('low-memory device');
  try {
    var c = document.createElement('canvas');
    var gl = c.getContext('webgl2') || c.getContext('webgl');
    if (!gl) return keep2d('WebGL unavailable');
  } catch (e) { return keep2d('WebGL unavailable'); }
  stage.setAttribute('data-cc-3d', 'loading');
  import('%%CHARACTERS%%').then(function (m) {
    return m.mount(stage, {agent: stage.getAttribute('data-agent'), reducedMotion: reduced, desk: window.__DESK_LAST || null});
  }).catch(function (e) { keep2d('3D desk failed to load (' + ((e && e.name) || 'Error') + ')'); });
})();
"""
