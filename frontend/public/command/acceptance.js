/* BETTOR · PM ACCEPTANCE + AUDIT MATRIX. window.BTAcceptance is the only
 * global it defines.
 *
 * ONE READ: GET data/audit-matrix.json (a static file in this directory,
 * maintained by the coordinator). No API call, no control, no approval: the
 * page only displays what the file records. A missing or malformed file
 * shows UNAVAILABLE with the reason -- never an empty "all clear".
 *
 * DELIVERY STATES are shown as a six-step track, each step distinct:
 *   BUILT -> TESTED -> GATED -> DEPLOYED -> PRODUCTION-READBACK ->
 *   FORWARD-VALIDATED
 * A row is shown LIVE only when its own STATUS begins with the word LIVE.
 */
(function (root) {
  'use strict';

  var SRC = 'data/audit-matrix.json';
  var STATES = ['BUILT', 'TESTED', 'GATED', 'DEPLOYED', 'PRODUCTION-READBACK', 'FORWARD-VALIDATED'];
  var COLS = [
    ['capability', 'Capability'], ['owner', 'Owner'], ['branch', 'Branch'], ['sha', 'SHA'],
    ['migration', 'Migration'], ['data_contract', 'Data contract'], ['authority', 'Authority'],
    ['unit_test', 'Unit test'], ['integration_test', 'Integration test'],
    ['capital_boundary_test', 'Capital-boundary test'], ['oos_counterfactual_test', 'OOS / counterfactual test'],
    ['audrey_audit', 'Audrey audit'], ['karen_review', 'Karen review'], ['command_center', 'Command center'],
    ['slack', 'Slack'], ['trading_floor_desk', 'Trading floor / desk'], ['exact_sha_gate', 'Exact-SHA gate'],
    ['deployment', 'Deployment'], ['production_readback', 'Production readback'],
    ['forward_sample', 'Forward sample'], ['economic_result', 'Economic result'],
    ['status', 'Status'], ['blocker', 'Blocker']
  ];
  var ui = {q: '', state: '', blockersOnly: false};
  var doc = null;

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  function stateIdx(s) { return STATES.indexOf(String(s || '').toUpperCase()); }
  function isLive(status) { return /^LIVE\b/.test(String(status || '').toUpperCase()); }
  function hasBlocker(r) {
    var b = String(r.blocker || '').trim();
    return !!b && !/^(none|n\/a|—|-)$/i.test(b) && !/^none\b/i.test(b);
  }
  /* A cell's tone, from its own words only. */
  function tone(v) {
    var s = String(v == null ? '' : v).toUpperCase();
    if (/^PASS|ACCEPTED|^DEPLOYED|RUNNERS OK/.test(s)) return 'good';
    if (/^PENDING|^NOT YET|^IN PLACE|^REPLAY|^NONE|NOT FORWARD|INSUFFICIENT|UNPROVEN/.test(s)) return 'warn';
    if (/^FAIL|^REJECT|UNAVAILABLE/.test(s)) return 'bad';
    if (/^NO RECORD|^NOT VERIFIED|^N\/A/.test(s)) return 'muted';
    return '';
  }

  function track(state) {
    var k = stateIdx(state);
    return '<ol class="ac-track" aria-label="Delivery state ' + esc(state || 'UNKNOWN') + '">' + STATES.map(function (s, i) {
      return '<li class="' + (i <= k ? 'on s' + i : '') + (i === k ? ' cur' : '') + '" title="' + esc(s + (i <= k ? ' — reached' : ' — not reached')) + '"><span></span></li>';
    }).join('') + '</ol><span class="ac-state s' + Math.max(k, 0) + (k < 0 ? ' unknown' : '') + '">' + esc(k < 0 ? 'UNKNOWN' : STATES[k]) + '</span>';
  }

  function statusCell(r) {
    var live = isLive(r.status);
    return '<span class="ac-status ' + (live ? 'live' : 'notlive') + '">' + (live ? 'LIVE · ' : '') + esc(r.status || 'NO STATUS') + '</span>';
  }

  function matches(r) {
    if (ui.state && String(r.delivery_state).toUpperCase() !== ui.state) return false;
    if (ui.blockersOnly && !hasBlocker(r)) return false;
    if (ui.q) {
      var hay = COLS.map(function (c) { return r[c[0]]; }).concat([r.candidate, r.delivery_state]).join(' ').toLowerCase();
      if (hay.indexOf(ui.q) < 0) return false;
    }
    return true;
  }

  function summary(rows) {
    var cnt = STATES.map(function (s) { return rows.filter(function (r) { return stateIdx(r.delivery_state) >= STATES.indexOf(s); }).length; });
    var exact = STATES.map(function (s) { return rows.filter(function (r) { return String(r.delivery_state).toUpperCase() === s; }).length; });
    var live = rows.filter(function (r) { return isLive(r.status); }).length;
    var blk = rows.filter(hasBlocker).length;
    return '<section class="ac-funnel" aria-label="Delivery funnel">' + STATES.map(function (s, i) {
      return '<button type="button" class="ac-stage s' + i + (ui.state === s ? ' sel' : '') + '" data-state="' + esc(s) + '" aria-pressed="' + (ui.state === s) + '">' +
        '<span class="k">' + esc(s) + '</span><span class="v">' + cnt[i] + '</span><span class="w">reached · ' + exact[i] + ' stopped here</span></button>';
    }).join('') + '</section>' +
      '<section class="ac-facts" aria-label="Totals">' +
      '<div><span class="k">Capabilities</span><span class="v">' + rows.length + '</span></div>' +
      '<div class="' + (live ? 'live' : '') + '"><span class="k">Status LIVE</span><span class="v">' + live + '</span><span class="w">' + (live ? 'as recorded in STATUS' : 'nothing is LIVE') + '</span></div>' +
      '<div class="blk"><span class="k">With a blocker</span><span class="v">' + blk + '</span></div>' +
      '<div><span class="k">Forward-validated</span><span class="v">' + exact[5] + '</span></div></section>';
  }

  function candidates(cs) {
    if (!cs || !cs.length) return '';
    return '<section class="ac-cands" aria-label="Release candidates">' + cs.map(function (c) {
      return '<article class="ac-cand"><header><b>' + esc(c.id) + '</b> <span class="mono">' + esc(c.sha) + '</span>' + track(c.state) + '</header>' +
        '<p>' + esc(c.contents) + '</p><dl>' +
        '<dt>Gate</dt><dd class="' + tone(c.gate) + '">' + esc(c.gate) + '</dd>' +
        '<dt>Deployment</dt><dd class="' + tone(c.deployment) + '">' + esc(c.deployment) + '</dd>' +
        '<dt>Readback</dt><dd class="' + tone(c.readback) + '">' + esc(c.readback) + '</dd></dl></article>';
    }).join('') + '</section>';
  }

  function table(rows) {
    var vis = rows.filter(matches);
    var head = '<tr><th scope="col" class="stick">Capability</th><th scope="col">Delivery state</th>' +
      COLS.slice(1).map(function (c) { return '<th scope="col">' + esc(c[1]) + '</th>'; }).join('') + '</tr>';
    var body = vis.map(function (r) {
      var b = hasBlocker(r);
      return '<tr class="' + (b ? 'has-blk' : '') + '">' +
        '<th scope="row" class="stick"><span class="cap">' + esc(r.capability) + '</span><span class="cand">' + esc(r.candidate || '') + '</span></th>' +
        '<td class="trk">' + track(r.delivery_state) + '</td>' +
        COLS.slice(1).map(function (c) {
          var k = c[0], v = r[k];
          if (k === 'status') return '<td>' + statusCell(r) + '</td>';
          if (k === 'blocker') return '<td class="blkcell">' + (b ? '<span class="ac-blk">' + esc(v) + '</span>' : '<span class="muted">' + esc(v || 'none recorded') + '</span>') + '</td>';
          var cls = (k === 'sha' || k === 'branch' || k === 'migration' || k === 'data_contract') ? 'mono' : tone(v);
          return '<td class="' + cls + '">' + esc(v == null || v === '' ? '—' : v) + '</td>';
        }).join('') + '</tr>';
    }).join('');
    return '<p class="ac-count" role="status">' + vis.length + ' of ' + rows.length + ' capabilities shown</p>' +
      '<div class="tablewrap ac-scroll" tabindex="0" role="region" aria-label="Acceptance and audit matrix (scrolls sideways)"><table class="ac-table"><thead>' + head + '</thead><tbody>' +
      (body || '<tr><td colspan="' + (COLS.length + 1) + '" class="muted">No capability matches these filters.</td></tr>') + '</tbody></table></div>';
  }

  function filters() {
    return '<form class="ac-filters" role="search" onsubmit="return false">' +
      '<label><span>Search</span><input type="search" id="ac-q" value="' + esc(ui.q) + '" placeholder="capability, branch, SHA, test…" autocomplete="off"></label>' +
      '<label><span>Delivery state</span><select id="ac-state"><option value="">All</option>' + STATES.map(function (s) { return '<option' + (ui.state === s ? ' selected' : '') + '>' + esc(s) + '</option>'; }).join('') + '</select></label>' +
      '<label class="chk"><input type="checkbox" id="ac-blk"' + (ui.blockersOnly ? ' checked' : '') + '> <span>Blockers only</span></label>' +
      '</form>';
  }

  function render(el) {
    var rows = (doc && doc.rows) || [];
    el.innerHTML = '<section class="ac-intro"><h1>Acceptance &amp; audit matrix</h1><p>' + esc(doc.disclosure || '') + '</p>' +
      '<p class="muted">Source: <span class="mono">command/' + esc(SRC) + '</span> · schema ' + esc(doc.schema || '—') + ' · as of ' + esc(doc.as_of || '—') + ' · ' + esc(doc.maintained_by || '') + '</p></section>' +
      summary(rows) + candidates(doc.candidates) + filters() + '<div id="ac-table">' + table(rows) + '</div>';
    wire(el);
  }
  function rerenderTable(el) {
    var t = el.querySelector('#ac-table');
    if (t) t.innerHTML = table(doc.rows || []);
    el.querySelectorAll('.ac-stage').forEach(function (b) {
      var on = b.getAttribute('data-state') === ui.state;
      b.classList.toggle('sel', on); b.setAttribute('aria-pressed', String(on));
    });
    var sel = el.querySelector('#ac-state'); if (sel) sel.value = ui.state;
  }
  function wire(el) {
    var q = el.querySelector('#ac-q'), st = el.querySelector('#ac-state'), bk = el.querySelector('#ac-blk');
    if (q) q.addEventListener('input', function () { ui.q = q.value.trim().toLowerCase(); rerenderTable(el); });
    if (st) st.addEventListener('change', function () { ui.state = st.value; rerenderTable(el); });
    if (bk) bk.addEventListener('change', function () { ui.blockersOnly = bk.checked; rerenderTable(el); });
    el.querySelectorAll('.ac-stage').forEach(function (b) {
      b.addEventListener('click', function () {
        var s = b.getAttribute('data-state'); ui.state = ui.state === s ? '' : s; rerenderTable(el);
      });
    });
  }

  function fail(el, why) {
    el.innerHTML = '<div class="pc-na ac-fail"><b>UNAVAILABLE</b> — ' + esc(why) + '. The matrix is not shown rather than shown empty.</div>';
    var p = document.getElementById('ac-pill'); if (p) { p.textContent = 'UNAVAILABLE'; p.className = 'pill pill-bad'; }
  }

  function boot() {
    var el = document.getElementById('ac-root');
    if (!el) return;
    fetch(SRC, {cache: 'no-store', credentials: 'same-origin', headers: {Accept: 'application/json'}})
      .then(function (r) {
        if (!r.ok) throw new Error(SRC + ' answered HTTP ' + r.status);
        return r.json();
      })
      .then(function (d) {
        if (!d || !Array.isArray(d.rows)) throw new Error(SRC + ' has no rows array');
        doc = d;
        render(el);
        var p = document.getElementById('ac-pill');
        if (p) {
          var live = d.rows.some(function (r) { return isLive(r.status); });
          p.textContent = live ? 'LIVE ROWS RECORDED' : 'NOTHING LIVE';
          p.className = 'pill ' + (live ? 'pill-good' : 'pill-warn');
        }
        var a = document.getElementById('ac-asof');
        if (a) a.textContent = 'Matrix as of ' + (d.as_of || '—') + ' · read ' + new Date().toISOString().slice(0, 16).replace('T', ' ') + 'Z';
      })
      .catch(function (e) { fail(el, (e && e.message) || 'read failed'); });
  }

  root.BTAcceptance = {states: STATES, columns: COLS.map(function (c) { return c[1]; }), isLive: isLive};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})(window);
