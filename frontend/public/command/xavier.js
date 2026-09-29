/* XAVIER · the position manager's decisions, read-only.
 *
 * One card per position under Xavier's responsibility: the chosen action,
 * every alternative (value, change vs HOLD, worst case, capital, blocker),
 * the reasoning, expected against realised economics, the unpaired
 * residual, the next review and what blocks it. Below, the latest daily
 * review, whose alternative figures are HYPOTHETICAL ESTIMATES and say so
 * on every row: an eventual winning outcome does not show that an order
 * which was never placed would have filled.
 *
 * UNKNOWN IS NOT ZERO. A figure the record does not carry renders as
 * UNKNOWN, never as 0 or a dash that could be read as one.
 *
 * Transport is the same as the rest of COMMAND: same-origin, the HttpOnly
 * cookie the browser attaches, no custom header, no credential in
 * JavaScript, nothing cached. It reads one route and writes nothing.
 */
(function (root) {
  'use strict';

  var API = '/api/command/xavier';
  var st = { data: null, error: null, at: null, loading: false };

  function esc(v) {
    return String(v === null || v === undefined ? '' : v)
      .replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                 '"': '&quot;', "'": '&#39;' }[c];
      });
  }

  function unknown(why) {
    return '<em class="xv-unknown"' + (why ? ' title="' + esc(why) + '"'
      : '') + '>UNKNOWN</em>';
  }

  function usd(v, why) {
    if (typeof v !== 'number' || !isFinite(v)) return unknown(why);
    var s = Math.abs(v).toFixed(2);
    return (v < 0 ? '−$' : '$') + s;
  }

  function num(v) {
    if (typeof v !== 'number' || !isFinite(v)) return unknown();
    return esc(String(Math.round(v * 1e6) / 1e6));
  }

  function when(iso) {
    if (!iso) return unknown();
    var t = Date.parse(iso);
    if (!isFinite(t)) return esc(iso);
    return esc(new Date(t).toISOString().replace('T', ' ').slice(0, 16)
      + 'Z');
  }

  function tone(action) {
    if (!action) return 'xv-wait';
    if (action === 'HOLD') return 'xv-hold';
    return 'xv-act';
  }

  function eligTone(e) {
    e = String(e || '');
    if (e === 'DISPATCHED') return 'xv-good';
    if (e.indexOf('BLOCKED') === 0 || e.indexOf('NOT_DISPATCHED') === 0) {
      return 'xv-bad';
    }
    return 'xv-wait';
  }

  function json(v) {
    if (v === null || v === undefined) return unknown();
    return '<pre class="xv-json">' + esc(JSON.stringify(v, null, 1))
      + '</pre>';
  }

  function altTable(alts) {
    if (!alts || !alts.length) {
      return '<p class="xv-note">The record lists no alternatives.</p>';
    }
    return '<div class="xv-scroll"><table class="xv-table"><thead><tr>'
      + '<th>Action</th><th>Value</th><th>Δ vs HOLD</th>'
      + '<th>Worst case</th><th>Capital</th><th>Blocker</th>'
      + '</tr></thead><tbody>' + alts.map(function (a) {
        return '<tr' + (a.chosen ? ' class="xv-chosen"' : '') + '><td>'
          + esc(a.action) + (a.chosen ? ' <b>chosen</b>' : '') + '</td><td>'
          + (a.blocker && a.value_usd === null ? '—'
             : usd(a.value_usd, 'not in the record')) + '</td><td>'
          + (a.increment_vs_hold_usd === null ? unknown()
             : usd(a.increment_vs_hold_usd)
               + (a.increment_basis && a.increment_basis !== 'RECORDED'
                  ? ' <small title="' + esc(a.increment_basis)
                    + '">derived</small>' : ''))
          + '</td><td>' + usd(a.worst_case_usd, 'not in the record')
          + '</td><td>' + usd(a.capital_usd, 'not in the record')
          + '</td><td>' + (a.blocker ? '<code>' + esc(a.blocker)
                           + '</code>' : '') + '</td></tr>';
      }).join('') + '</tbody></table></div>';
  }

  function blockers(list) {
    if (!list || !list.length) return '<p class="xv-note">None recorded.</p>';
    return '<ul class="xv-blockers">' + list.map(function (b) {
      return '<li><span class="xv-kind">' + esc(b.kind) + '</span> <code>'
        + esc(b.name) + '</code>' + (b.action ? ' (' + esc(b.action) + ')'
        : '') + '</li>';
    }).join('') + '</ul>';
  }

  function card(p) {
    var ex = p.expected_economics || {};
    var re = p.realised || {};
    var rx = p.residual_exposure || {};
    var reasoning = p.reasoning || {};
    return '<article class="xv-card">'
      + '<header class="xv-card-head"><div>'
      + '<p class="xv-slug">' + esc(p.us_market_slug || p.intent_id) + '</p>'
      + '<p class="xv-sub">' + esc(p.responsibility_state) + ' · decided '
      + when(p.decided_at) + '</p></div>'
      + '<div class="xv-chosen-action ' + tone(p.chosen_action) + '">'
      + esc(p.chosen_action || 'NOTHING SELECTABLE') + '</div></header>'
      + '<p class="xv-elig"><span class="xv-badge ' + eligTone(
        p.execution_eligibility) + '">' + esc(p.execution_eligibility)
      + '</span> next review ' + when(p.next_review_at) + '</p>'
      + (p.execution ? '<p class="xv-sub">execution ' + esc(
        p.execution.status) + ' · filled ' + num(p.execution.filled_qty)
        + '</p>' : '')
      + '<h4>Alternatives</h4>' + altTable(p.alternatives)
      + '<h4>Reasoning</h4>'
      + (reasoning.why ? '<p>' + esc(reasoning.why) + '</p>' : '')
      + json(reasoning)
      + '<div class="xv-grid">'
      + '<div><h4>Expected</h4><dl>'
      + '<dt>expected net</dt><dd>' + usd(ex.expected_net_usd) + '</dd>'
      + '</dl></div>'
      + '<div><h4>Realised so far (book)</h4><dl>'
      + '<dt>realised</dt><dd>' + usd(re.realised_usd) + '</dd>'
      + '<dt>basis still held</dt><dd>' + usd(re.remaining_basis_usd)
      + '</dd><dt>unrealised</dt><dd>'
      + (re.unrealised_usd === null || re.unrealised_usd === undefined
         ? unknown(re.unrealised_status) : usd(re.unrealised_usd))
      + '</dd><dt>provisional events</dt><dd>'
      + num(re.provisional_events) + '</dd></dl></div>'
      + '<div><h4>Residual exposure</h4><dl>'
      + '<dt>matched</dt><dd>' + num(rx.matched_qty) + '</dd>'
      + '<dt>unpaired</dt><dd>' + num(
        rx.unpaired_qty !== undefined ? rx.unpaired_qty
        : rx.unpaired_residual_qty) + '</dd></dl></div>'
      + '</div>'
      + '<h4>Blockers</h4>' + blockers(p.blockers)
      + '<p class="xv-sub">record <code>' + esc(p.xavier_decision_id)
      + '</code> · history <code>' + esc(API + '/' + p.intent_id)
      + '</code></p></article>';
  }

  function emptyState(w) {
    w = w || {};
    return '<section class="xv-panel xv-empty"><h3>No position is under '
      + 'Xavier’s responsibility</h3><p>This is not an all-clear. '
      + 'Why nothing is managed:</p><ul>' + (w.reasons || []).map(function (r) {
        return '<li><code>' + esc(r) + '</code></li>';
      }).join('') + '</ul>'
      + (w.account_why ? '<p class="xv-note">' + esc(w.account_why) + '</p>'
        : '')
      + '<p class="xv-note">Last cycle: ' + esc((w.last_cycle || {})
        .cycle_state || 'none recorded') + (w.last_cycle && w.last_cycle
        .servicing_refusal ? ' · servicing refused <code>'
        + esc(w.last_cycle.servicing_refusal) + '</code>' : '') + '</p>'
      + '</section>';
  }

  function review(r) {
    r = r || {};
    if (!r.available) {
      return '<section class="xv-panel"><h3>Daily review</h3><p>'
        + esc(r.why || 'no review recorded') + '</p></section>';
    }
    var fe = (r.forecast_error || {}).all || {};
    var alts = r.alternatives || {};
    return '<section class="xv-panel"><h3>Daily review · '
      + esc(r.review_date) + '</h3><p>' + esc(r.summary) + '</p>'
      + '<dl class="xv-inline"><dt>decisions reviewed</dt><dd>'
      + num(r.decisions_reviewed) + '</dd><dt>with outcomes</dt><dd>'
      + num(r.decisions_with_outcomes) + '</dd><dt>invalidated</dt><dd>'
      + num(r.invalidated) + '</dd><dt>mean error (per fixture)</dt><dd>'
      + usd(fe.mean_error_usd) + '</dd><dt>status</dt><dd>'
      + esc(fe.status || '') + '</dd></dl>'
      + '<h4>Alternatives not taken</h4>'
      + '<p class="xv-hypo" role="note"><b>HYPOTHETICAL ESTIMATES.</b> '
      + esc(alts.label || '') + '</p>'
      + '<div class="xv-scroll"><table class="xv-table"><thead><tr>'
      + '<th>Action</th><th>Estimate</th><th>Label</th><th>Fill basis</th>'
      + '<th>Could have filled</th></tr></thead><tbody>'
      + (alts.rows || []).map(function (a) {
        return '<tr><td>' + esc(a.action) + '</td><td>'
          + (a.estimate_usd === null || a.estimate_usd === undefined
             ? '<code>' + esc(a.no_estimate_because || '') + '</code>'
             : usd(a.estimate_usd)) + '</td><td>' + esc(a.kind)
          + '</td><td>' + esc(a.fill_basis || '') + '</td><td>'
          + esc(a.could_have_filled || '') + '</td></tr>';
      }).join('') + '</tbody></table></div>'
      + '<p class="xv-note">Models: '
      + esc((r.models || {}).promotion || '') + '</p></section>';
  }

  function render(data) {
    var d = data || st.data;
    if (st.error) {
      return '<section class="xv-panel xv-unavailable"><h3>XAVIER '
        + 'UNAVAILABLE</h3><p>' + esc(st.error.reason) + '</p><p class='
        + '"xv-note">' + esc(st.error.detail || '') + '</p><p class="xv-note">'
        + 'The read did not produce the records, so nothing is shown.</p>'
        + '</section>';
    }
    if (!d) return '<section class="xv-panel"><p>Reading Xavier…</p></section>';
    var sw = d.submission_switches || {};
    return '<div class="xv-root">'
      + '<section class="xv-panel xv-head"><h2>Xavier</h2><p class="xv-sub">'
      + esc((d.xavier || {}).what_it_is || '') + '</p><p class="xv-sub">'
      + 'Read-only · ' + num(d.position_count) + ' position(s) · as of '
      + when(d.as_of) + ' · funded submission '
      + (sw.FUNDED_SUBMISSION_ENABLED ? 'ENABLED' : 'DISABLED')
      + ' · exit submission '
      + (sw.FUNDED_EXIT_SUBMISSION_ENABLED ? 'ENABLED' : 'DISABLED')
      + '</p></section>'
      + (d.empty ? emptyState(d.why_nothing_is_managed)
         : (d.positions || []).map(card).join(''))
      + review(d.daily_review) + '</div>';
  }

  async function load(done) {
    st.loading = true;
    st.error = null;
    try {
      var res = await fetch(API, {
        credentials: 'same-origin', cache: 'no-store',
        headers: { 'Accept': 'application/json' }
      });
      if (res.status === 401 || res.status === 403) {
        st.error = { reason: 'COMMAND UNLOCK REQUIRED',
                     detail: 'Sign in on the main Command page first.' };
      } else if (!res.ok) {
        var body = null;
        try { body = await res.json(); } catch (e) { body = null; }
        var det = (body && body.detail) || {};
        st.error = { reason: det.reason || ('HTTP ' + res.status),
                     detail: det.detail || det.note || '' };
      } else {
        st.data = await res.json();
        st.at = new Date().toISOString();
      }
    } catch (e) {
      st.error = { reason: 'FEED UNREACHABLE', detail: String(e) };
    }
    st.loading = false;
    if (typeof done === 'function') done();
  }

  /* THE COMMAND CENTRE'S TAB: returns what to show now and loads once,
   * re-rendering the host when the read lands. */
  function panel(rerender) {
    if (!st.data && !st.error && !st.loading) load(rerender);
    return render();
  }

  root.BTXavier = { API: API, load: load, render: render, panel: panel,
                    state: st };

  function standalone() {
    var el = document.getElementById('xv-root');
    if (!el) return;
    el.innerHTML = render();
    load(function () { el.innerHTML = render(); });
  }
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', standalone);
    } else {
      standalone();
    }
  }
})(window);
