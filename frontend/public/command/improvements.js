/* BETTOR COMMAND · THE IMPROVEMENT PIPELINE BOARD.
 *
 * "EVIDENCE -> HYPOTHESIS -> PEER CHALLENGE -> OWNER RESPONSE -> EXPERIMENT
 *  -> INDEPENDENT EVALUATION -> ELIGIBLE CHANGE -> CONTROLLED RELEASE ->
 *  FORWARD RESULT. Agents challenge each other; disagreements are preserved;
 *  consensus is never manufactured."
 *
 * TWO READS, BOTH GET, BOTH SAME-ORIGIN, BOTH AUTHENTICATED BY THE HttpOnly
 * COMMAND COOKIE (this script never sees a credential):
 *   /api/command/improvements           the board
 *   /api/command/improvements/{id}      one item's full trail
 * The item id is checked against the server's id grammar before it is used.
 *
 * NOTHING IS INVENTED HERE. Every stage, owner, count, disagreement and
 * "next required actor" is the server's, derived from the ledger. A read
 * that fails says why (SIGNED OUT / NOT RELEASED / UNAVAILABLE); the runner's
 * last pass is shown with its age and marked STALE when it is old. A payload
 * marked fixture is labelled FIXTURE on the page. Nothing moves on its own:
 * a card glows once only when a poll shows its stage really changed.
 */
(function () {
  'use strict';

  var BOARD = '/api/command/improvements';
  var ITEM = '/api/command/improvements/';
  var ID_RE = /^impr:[0-9a-f]{24}$/;
  var POLL_MS = 30000;
  var STAGES = ['EVIDENCE', 'HYPOTHESIS', 'PEER_CHALLENGE', 'OWNER_RESPONSE',
                'EXPERIMENT', 'INDEPENDENT_EVALUATION', 'ELIGIBLE_CHANGE',
                'CONTROLLED_RELEASE', 'FORWARD_RESULT'];
  var TERMINAL = ['ROLLED_BACK', 'CLOSED'];
  var LABEL = {EVIDENCE: 'Evidence', HYPOTHESIS: 'Hypothesis',
               PEER_CHALLENGE: 'Peer challenge', OWNER_RESPONSE: 'Owner response',
               EXPERIMENT: 'Experiment', INDEPENDENT_EVALUATION: 'Independent evaluation',
               ELIGIBLE_CHANGE: 'Eligible change', CONTROLLED_RELEASE: 'Controlled release',
               FORWARD_RESULT: 'Forward result', ROLLED_BACK: 'Rolled back', CLOSED: 'Closed'};
  var WHO = {EVIDENCE: 'runner · owner', HYPOTHESIS: 'owner agent',
             PEER_CHALLENGE: 'Karen + a peer', OWNER_RESPONSE: 'owner',
             EXPERIMENT: 'owner · engineering', INDEPENDENT_EVALUATION: 'not the proposer',
             ELIGIBLE_CHANGE: 'evaluator · human', CONTROLLED_RELEASE: 'human only',
             FORWARD_RESULT: 'evaluator · human'};
  var AGENTS = {
    DEREK: {name: 'Derek', accent: '#9fe3bf', initial: 'D'},
    XAVIER: {name: 'Xavier', accent: '#9fd2f2', initial: 'X'},
    AUDREY: {name: 'Audrey', accent: '#cdb6f6', initial: 'A'},
    KAREN: {name: 'Karen', accent: '#ff9a8f', initial: 'K'},
    EDDIE: {name: 'Eddie', accent: '#6fe0d2', initial: 'E'},
    SCOUT: {name: 'Scout', accent: '#f5b072', initial: 'S'},
    CHIEF_ALLOCATOR: {name: 'Chief Allocator', accent: '#ecc66d', initial: 'CA'}
  };
  var OWNERS = ['DEREK', 'XAVIER', 'AUDREY', 'EDDIE', 'SCOUT', 'CHIEF_ALLOCATOR'];
  var SOURCE = {KAREN_UPHELD_CHALLENGE: 'Upheld Karen challenge', AUDREY_FINDING: 'Audrey finding',
                COVERAGE_INCIDENT: 'Coverage incident', EDDIE_SKIP_EXECUTION: 'Eddie skip',
                FALSE_REFUSAL: 'False refusal', TOURNAMENT_VERDICT: 'Tournament verdict',
                AGENT_FINDING: 'Agent finding', HUMAN_REPORTED: 'Reported by a person'};
  var CLASS = {RUNNER: 'Runner', OWNER_AGENT: 'Owner', PEER_AGENT: 'Peer', CHALLENGER: 'Challenger',
               INDEPENDENT_EVALUATOR: 'Independent evaluator', HUMAN: 'Human', ENGINEERING: 'Engineering'};

  var S = {read: null, item: null, itemRead: null, prev: null, moved: {}, timer: null,
           lastFocus: null, filter: {owner: 'ALL', prot: false, dispute: false, q: ''}};

  // ── helpers (presentation only) ──────────────────────────────────────
  function $(id) { return document.getElementById(id); }
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  function num(v) { return typeof v === 'number' && isFinite(v) ? v : null; }
  function age(s) {
    s = num(s);
    if (s === null) { return '—'; }
    s = Math.max(0, Math.round(s));
    if (s < 60) { return s + 's'; }
    if (s < 3600) { return Math.floor(s / 60) + 'm'; }
    if (s < 86400) { return Math.floor(s / 3600) + 'h ' + Math.floor(s % 3600 / 60) + 'm'; }
    return Math.floor(s / 86400) + 'd ' + Math.floor(s % 86400 / 3600) + 'h';
  }
  function epoch(iso) { var t = Date.parse(iso); return isFinite(t) ? t / 1000 : null; }
  function ago(iso) {
    var e = epoch(iso);
    return e === null ? '—' : age(Date.now() / 1000 - e) + ' ago';
  }
  function utc(iso) {
    var e = epoch(iso);
    return e === null ? '—' : new Date(e * 1000).toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
  }
  function agent(a) {
    var k = String(a || '').toUpperCase();
    return AGENTS[k] || {name: a || '—', accent: '#c9d6ea', initial: String(a || '?').charAt(0).toUpperCase(), person: true};
  }
  function avatar(a, small) {
    var m = agent(a);
    return '<span class="im-av' + (small ? ' sm' : '') + (m.person ? ' person' : '') +
      '" style="--c:' + esc(m.accent) + '" aria-hidden="true">' + esc(m.initial) + '</span>';
  }
  var ICON = {
    lock: '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="3" y="7" width="10" height="7" rx="1.6"/><path d="M5.2 7V5.2a2.8 2.8 0 0 1 5.6 0V7"/></svg>',
    doc: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 1.8h5.2L12.4 5v9.2H4z"/><path d="M9 1.8V5h3.4M6 8.2h4.2M6 10.8h4.2"/></svg>',
    split: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 4.5h5l2 3.5-2 3.5h-5M13.5 4.5h-2.4M13.5 11.5h-2.4"/></svg>',
    arrow: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9M8.5 4.5 12 8l-3.5 3.5"/></svg>',
    close: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg>',
    check: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>',
    miss: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.5v5.5M8 11.6v.4"/></svg>',
    branch: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="4.5" cy="3.5" r="1.6"/><circle cx="4.5" cy="12.5" r="1.6"/><circle cx="11.5" cy="5.5" r="1.6"/><path d="M4.5 5.1v5.8M11.5 7.1c0 2.4-2.2 3-7 3.8"/></svg>'
  };

  // ── transport ────────────────────────────────────────────────────────
  function get(path) {
    var started = Date.now() / 1000;
    if ((path !== BOARD && path.indexOf(ITEM) !== 0) || path.indexOf('..') >= 0) {
      return Promise.resolve({status: 'UNAVAILABLE', why: 'refused path', at: started});
    }
    return fetch(path, {method: 'GET', credentials: 'same-origin', cache: 'no-store',
                        headers: {Accept: 'application/json'}})
      .then(function (r) {
        if (r.status === 401 || r.status === 403) {
          return {status: 'SIGNED_OUT', at: started,
                  why: 'Your COMMAND session is missing or has expired. Sign in on the COMMAND homepage.'};
        }
        if (r.status === 404 && path === BOARD) {
          return {status: 'NOT_RELEASED', at: started,
                  why: 'The serving API does not have /api/command/improvements yet; it ships with the next API release.'};
        }
        if (r.status === 404) { return {status: 'NOT_FOUND', at: started, why: 'No such improvement item.'}; }
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (j) {
            var d = (j && j.detail) || {};
            return {status: 'UNAVAILABLE', at: started,
                    why: 'The API answered HTTP ' + r.status + (d.reason ? ' · ' + d.reason : '') + '.'};
          });
        }
        return r.json().then(function (j) { return {status: 'OK', data: j, at: started}; },
          function () { return {status: 'UNAVAILABLE', at: started, why: 'The answer was not JSON.'}; });
      }, function (e) {
        return {status: 'UNAVAILABLE', at: started,
                why: 'The API could not be reached (' + (e && e.name || 'network') + ').'};
      });
  }
  function isFixture(d) { return !!(d && (d.fixture === true || d.label === 'FIXTURE')); }

  // ── status, KPIs, filters ────────────────────────────────────────────
  function renderStatus() {
    var r = S.read, html, tone = 'off', text = 'Connecting…';
    if (r) {
      if (r.status === 'OK') {
        tone = 'live';
        text = (isFixture(r.data) ? 'FIXTURE' : 'LIVE') + ' · read ' + age(Date.now() / 1000 - r.at) + ' ago';
      } else if (r.status === 'SIGNED_OUT') { tone = 'warn'; text = 'SIGNED OUT'; }
      else if (r.status === 'NOT_RELEASED') { tone = 'stale'; text = 'NOT YET RELEASED'; }
      else { tone = 'warn'; text = 'UNAVAILABLE'; }
    }
    html = '<span class="im-pill" data-tone="' + tone + '" title="' + esc(r && r.why || 'GET ' + BOARD) + '"><i></i><span>' + esc(text) + '</span></span>';
    var run = r && r.status === 'OK' ? r.data.runner : null;
    if (r && r.status === 'OK') {
      if (!run) {
        html += '<span class="im-pill" data-tone="warn" title="' + esc((r.data.sections && r.data.sections.runner && r.data.sections.runner.why) || 'no runner record') + '"><i></i><span>RUNNER UNAVAILABLE</span></span>';
      } else if (!run.last_run_at) {
        html += '<span class="im-pill" data-tone="stale" title="' + esc(run.why || '') + '"><i></i><span>RUNNER · NO PASS RECORDED</span></span>';
      } else {
        html += '<span class="im-pill" data-tone="' + (run.stale ? 'stale' : 'live') + '" title="Source: improve_runs · last pass ' + esc(utc(run.last_run_at)) + ' · status ' + esc(run.status) + (run.stale ? ' · older than ' + esc(age(run.stale_after_s)) : '') + '"><i></i><span>' + (run.stale ? 'RUNNER STALE · ' : 'RUNNER · ') + esc(age(run.age_s)) + ' ago</span></span>';
      }
    }
    $('im-status').innerHTML = html;
    $('im-fixture').hidden = !(r && r.status === 'OK' && isFixture(r.data));
  }

  function kpi(label, value, hint, tone) {
    return '<div class="im-kpi' + (tone ? ' ' + tone : '') + '" title="' + esc(hint) + '"><b class="mono">' + esc(value) + '</b><span>' + esc(label) + '</span></div>';
  }
  function renderKpis() {
    var r = S.read, el = $('im-kpis');
    if (!r || r.status !== 'OK' || !r.data.available) {
      el.innerHTML = ['Items', 'Protected', 'Open disagreements', 'Dissent kept', 'Awaiting a human'].map(function (l) {
        return '<div class="im-kpi na" title="' + esc(r && (r.why || (r.data && r.data.unavailable_why)) || 'reading') + '"><b class="mono">UNAVAILABLE</b><span>' + esc(l) + '</span></div>';
      }).join('');
      return;
    }
    var c = r.data.counts || {}, src = 'Source: GET ' + BOARD + ' · as of ' + utc(r.data.as_of);
    el.innerHTML = kpi('Items', c.items, src) +
      kpi('Protected', c.protected, src + ' · risk, accounting, settlement, execution authorization, financial controls, credentials or live capital limits', 'lock') +
      kpi('Open disagreements', c.open_disagreements, src + ' · live, unresolved', c.open_disagreements ? 'bad' : '') +
      kpi('Dissent kept', c.dissent_preserved, src + ' · decided by a third party; the losing position stays on the record', c.dissent_preserved ? 'warn' : '') +
      kpi('Awaiting a human', c.awaiting_human, src + ' · the next step is a person\'s', c.awaiting_human ? 'human' : '');
  }

  function renderFilters() {
    var f = S.filter, html = '<div class="im-chips" role="group" aria-label="Owner">';
    ['ALL'].concat(OWNERS).forEach(function (o) {
      var on = f.owner === o;
      html += '<button type="button" class="im-chip' + (on ? ' on' : '') + '" data-owner="' + o + '" aria-pressed="' + on + '">' +
        (o === 'ALL' ? 'All owners' : avatar(o, true) + esc(agent(o).name)) + '</button>';
    });
    html += '</div><div class="im-toggles">' +
      '<button type="button" class="im-chip' + (f.prot ? ' on' : '') + '" data-toggle="prot" aria-pressed="' + f.prot + '">' + ICON.lock + 'Protected</button>' +
      '<button type="button" class="im-chip' + (f.dispute ? ' on' : '') + '" data-toggle="dispute" aria-pressed="' + f.dispute + '">' + ICON.split + 'Disagreements</button>' +
      '<label class="im-search"><span class="sr">Search items</span><input id="im-q" type="search" placeholder="Search title or id" value="' + esc(f.q) + '" autocomplete="off"></label></div>';
    $('im-filters').innerHTML = html;
  }

  function visible(c) {
    var f = S.filter;
    if (f.owner !== 'ALL' && c.owner_agent !== f.owner) { return false; }
    if (f.prot && !(c.protected && c.protected.is_protected)) { return false; }
    if (f.dispute && !(c.disagreements && c.disagreements.total)) { return false; }
    if (f.q) {
      var q = f.q.toLowerCase();
      if ((c.title || '').toLowerCase().indexOf(q) < 0 && (c.item_id || '').indexOf(q) < 0) { return false; }
    }
    return true;
  }

  // ── the board ────────────────────────────────────────────────────────
  function tone(n) {
    if (!n || !n.actor_class) { return 'done'; }
    if (n.code === 'EVALUATION_FAILED' || n.code === 'AWAITING_ROLLBACK_DECISION') { return 'bad'; }
    if (/HUMAN/.test(n.actor_class)) { return 'human'; }
    return 'agent';
  }
  function disBadge(d, big) {
    if (!d || !d.total) { return ''; }
    if (d.open) {
      return '<span class="im-badge bad" title="' + d.open + ' disagreement(s) open: each party\'s position is on the record and no one has decided.">' + ICON.split + (big ? d.open + ' open disagreement' + (d.open > 1 ? 's' : '') : 'Open dispute') + '</span>';
    }
    if (d.decided) {
      return '<span class="im-badge warn" title="Decided by a third party. The losing position is preserved; this is a decision, not consensus.">' + ICON.split + 'Dissent kept</span>';
    }
    return '<span class="im-badge" title="Resolved by a concession or a withdrawal.">' + ICON.split + 'Resolved</span>';
  }
  function lock(p, big) {
    if (!p || !p.is_protected) { return ''; }
    return '<span class="im-lock' + (big ? ' big' : '') + '" title="Protected area: ' + esc((p.areas || []).join(', ')) + ' · requires human review and ' + esc(p.required_independent_reviews) + ' independent reviews">' + ICON.lock + (big ? esc((p.areas || []).join(' · ')) : '<span class="sr">Protected</span>') + '</span>';
  }
  function card(c) {
    var n = c.next_required || {}, rv = c.reviews || {}, moved = S.moved[c.item_id];
    return '<button type="button" class="im-card' + (c.protected && c.protected.is_protected ? ' prot' : '') + (moved ? ' im-moved' : '') +
      '" data-item="' + esc(c.item_id) + '" aria-label="' + esc(c.title + ' · ' + (LABEL[c.stage] || c.stage) + ' · ' + (n.label || '')) + '">' +
      '<span class="im-card-top">' + avatar(c.owner_agent) + '<span class="im-owner"><b>' + esc(agent(c.owner_agent).name) + '</b><small>owner</small></span>' +
      '<span class="im-card-flags">' + disBadge(c.disagreements) + lock(c.protected) + '</span></span>' +
      '<span class="im-card-title">' + esc(c.title) + '</span>' +
      '<span class="im-card-meta"><span class="im-src-chip">' + esc(SOURCE[c.source_kind] || c.source_kind) + '</span>' +
      '<span title="Distinct evidence records cited by the item">' + ICON.doc + '<b class="mono">' + esc(c.evidence_count) + '</b> evidence</span>' +
      (rv.passes || (c.stage_seq >= 5 && c.stage_seq < 98) ? '<span title="Independent PASS reviews / required">' + ICON.check + '<b class="mono">' + esc(rv.passes || 0) + '/' + esc(rv.required || 1) + '</b></span>' : '') +
      '<span class="im-age" title="Last recorded ' + esc(utc(c.updated_at)) + '">' + esc(age(c.age_s)) + '</span></span>' +
      '<span class="im-next ' + tone(n) + '">' + ICON.arrow + '<span>' + esc(n.label || 'UNKNOWN') + '</span></span>' +
      '</button>';
  }
  function column(stage, cards, total) {
    var term = stage === 'TERMINAL';
    var label = term ? 'Closed · rolled back' : LABEL[stage];
    return '<section class="im-col' + (cards.length ? '' : ' empty') + (term ? ' term' : '') + (stage === 'CONTROLLED_RELEASE' ? ' gate' : '') + '" aria-label="' + esc(label) + '">' +
      '<header class="im-col-h"><span class="im-col-n mono">' + (term ? '∎' : STAGES.indexOf(stage) + 1) + '</span><span class="im-col-t"><b>' + esc(label) + '</b><small>' + esc(term ? 'terminal' : WHO[stage]) + '</small></span>' +
      '<span class="im-col-c mono" title="' + esc(total) + ' item(s) at this stage' + (total !== cards.length ? ', ' + cards.length + ' shown' : '') + '">' + esc(cards.length) + '</span></header>' +
      '<div class="im-col-body">' + (cards.length ? cards.map(card).join('') : '<p class="im-col-empty">' + (stage === 'CONTROLLED_RELEASE' ? 'Nothing released. A release needs a named human and an exact-SHA gate receipt.' : 'No item here.') + '</p>') + '</div></section>';
  }
  function renderBoard() {
    var r = S.read, el = $('im-board'), st = $('im-state');
    if (!r) { return; }
    if (r.status !== 'OK') {
      st.hidden = false;
      st.className = 'im-state ' + (r.status === 'NOT_RELEASED' ? 'warn' : 'bad');
      st.innerHTML = '<b>' + esc(r.status.replace('_', ' ')) + '</b> · ' + esc(r.why) + (r.status === 'SIGNED_OUT' ? ' <a href="/">Open COMMAND</a>' : '');
      el.innerHTML = '';
      return;
    }
    var d = r.data;
    if (!d.available) {
      st.hidden = false; st.className = 'im-state warn';
      st.innerHTML = '<b>UNAVAILABLE</b> · ' + esc(d.unavailable_why || 'the ledger could not be read');
      el.innerHTML = '';
      return;
    }
    var items = d.items || [];
    var shown = items.filter(visible);
    st.hidden = items.length > 0;
    st.className = 'im-state';
    st.innerHTML = items.length ? '' : 'The ledger holds no improvement item yet. Items appear when the runner finds an upheld Karen challenge, an Audrey finding, a coverage incident, an Eddie skip, a false refusal or a tournament verdict.';
    var by = {}, total = {};
    STAGES.concat(['TERMINAL']).forEach(function (s) { by[s] = []; total[s] = 0; });
    items.forEach(function (c) { var k = TERMINAL.indexOf(c.stage) >= 0 ? 'TERMINAL' : c.stage; if (total[k] !== undefined) { total[k]++; } });
    shown.forEach(function (c) { var k = TERMINAL.indexOf(c.stage) >= 0 ? 'TERMINAL' : c.stage; if (by[k]) { by[k].push(c); } });
    el.innerHTML = '<div class="im-cols">' + STAGES.concat(['TERMINAL']).map(function (s) { return column(s, by[s], total[s]); }).join('') + '</div>' +
      (items.length && !shown.length ? '<p class="im-none">No item matches the filters.</p>' : '');
    $('im-disclosure').textContent = d.disclosure || '';
    $('im-src').innerHTML = 'Source: <code>GET ' + BOARD + '</code> · as of ' + esc(utc(d.as_of)) + ' · ' + esc(items.length) + ' item(s) · read only';
  }

  // ── the drawer ───────────────────────────────────────────────────────
  function link(l) {
    if (!l) { return ''; }
    var label = '<code>' + esc(l.kind) + '</code> <span class="mono">' + esc(l.id) + '</span>';
    var href = l.api || l.page;
    var ok = l.exists === false ? ' <span class="im-miss" title="This cited record was not found when read">' + ICON.miss + 'NOT FOUND</span>' : (l.exists === true ? ' <span class="im-ok" title="The cited record exists">' + ICON.check + '</span>' : '');
    return href ? '<a class="im-ref" href="' + esc(href) + '" title="' + esc(l.api ? 'Open the record (read only)' : 'Open the agent page') + '">' + label + '</a>' + ok : '<span class="im-ref">' + label + '</span>' + ok;
  }
  function bodyText(b) {
    if (!b) { return ''; }
    var keys = [['hypothesis', 'Hypothesis'], ['challenge', 'Challenge'], ['response', 'Response'],
                ['reason', 'Reason'], ['statement', 'Statement'], ['design', 'Design'],
                ['description', 'Candidate'], ['note', 'Note'], ['meaning', 'Meaning']];
    var out = '';
    keys.forEach(function (k) {
      if (typeof b[k[0]] === 'string' && b[k[0]].trim()) {
        out += '<p class="im-quote"><span>' + esc(k[1]) + '</span>' + esc(b[k[0]]) + '</p>';
      }
    });
    if (b.metric && typeof b.metric === 'object') {
      out += '<p class="im-kv"><span>Pre-registered metric</span><code>' + esc(b.metric.name) + '</code> ' + esc(b.metric.direction) + ' · threshold <b class="mono">' + esc(b.metric.threshold) + '</b></p>';
    }
    if (num(b.metric_value) !== null) {
      out += '<p class="im-kv"><span>Measured</span><b class="mono">' + esc(b.metric_value) + '</b>' + (num(b.samples) !== null ? ' on <b class="mono">' + esc(b.samples) + '</b> samples' : '') + '</p>';
    }
    return out;
  }
  function path(p) {
    return '<ol class="im-path" aria-label="Stage path">' + (p || []).map(function (s) {
      return '<li class="' + (s.current ? 'cur' : s.reached ? 'done' : '') + '" title="' + esc(s.label + (s.reached ? ' · reached ' + utc(s.at) : ' · not reached') + (s.events ? ' · ' + s.events + ' record(s)' : '')) + '"><i></i><span>' + esc(s.label) + '</span></li>';
    }).join('') + '</ol>';
  }
  function disagreement(x) {
    var st = x.state === 'OPEN' ? 'bad' : x.state === 'DECIDED' ? 'warn' : '';
    return '<article class="im-dis ' + st + '"><header><span class="im-badge ' + st + '">' + esc(x.state) + '</span><span>' + esc(LABEL[x.stage] || (x.stage === 'ORIGIN' ? 'Origin (the signal)' : x.stage)) + ' · opened ' + esc(utc(x.opened_at)) + '</span></header>' +
      '<div class="im-pos">' + (x.positions || []).map(function (p) {
        return '<div class="im-pos-one"><b>' + avatar(p.party, true) + esc(agent(p.party).name) + (p.stance ? ' · ' + esc(p.stance) : '') + '</b><p>' + esc(p.position) + '</p><small>' + link(p.source) + '</small></div>';
      }).join('') + '</div>' +
      (x.state === 'OPEN' ? '<p class="im-dis-res">Open. Nobody has decided; both positions stand.</p>' :
        '<p class="im-dis-res"><b>' + esc(x.state === 'DECIDED' ? 'Decided by ' : x.state === 'CONCEDED' ? 'Conceded by ' : 'Withdrawn by ') + esc(agent(x.resolved_by).name) + '</b> · ' + esc(utc(x.resolved_at)) + '<br>' + esc(x.resolution) +
        (x.resolution_source ? '<br><small>' + link(x.resolution_source) + '</small>' : '') +
        (x.consensus ? '' : '<br><em>Not consensus: the other position is preserved.</em>') + '</p>') +
      '</article>';
  }
  function trailRow(t) {
    var chips = '';
    if (t.stance) { chips += '<span class="im-tag ' + (t.stance === 'DISPUTE' || t.stance === 'REFUTED' ? 'bad' : '') + '">' + esc(t.stance) + '</span>'; }
    if (t.outcome) { chips += '<span class="im-tag ' + (t.outcome === 'PASS' || t.outcome === 'HELD' ? 'good' : t.outcome === 'FAIL' || t.outcome === 'DEGRADED' ? 'bad' : '') + '">' + esc(t.outcome) + '</span>'; }
    var extra = '';
    if (t.patch) {
      extra += '<div class="im-patch">' + ICON.branch + '<div><b>Candidate patch · reference only</b>' +
        (t.patch.branch ? '<span>branch <code>' + esc(t.patch.branch) + '</code></span>' : '') +
        (t.patch.commit_sha ? '<span>commit <code>' + esc(t.patch.commit_sha) + '</code></span>' : '') +
        (t.patch.pr_url ? '<span>PR <a href="' + esc(t.patch.pr_url) + '" rel="noopener noreferrer" target="_blank">' + esc(t.patch.pr_url.replace('https://github.com/', '')) + '</a></span>' : '') +
        (t.patch.tests_ref ? '<span>tests <code>' + esc(t.patch.tests_ref) + '</code></span>' : '') +
        '<small>The system never pushes, merges or deploys; a person and the engineering process do.</small></div></div>';
    }
    if (t.gate_receipt) {
      extra += '<div class="im-patch gate">' + ICON.lock + '<div><b>Exact-SHA gate receipt</b><span><code>' + esc(t.gate_receipt.sha) + '</code></span><span>' + esc(t.gate_receipt.ref) + '</span>' + (t.release_ref ? '<span>release ' + esc(t.release_ref) + '</span>' : '') + '</div></div>';
    }
    if (t.monitoring) {
      extra += '<p class="im-kv"><span>Monitoring window</span>' + esc(utc(t.monitoring.start)) + ' → ' + esc(utc(t.monitoring.end)) + '</p>';
    }
    if (t.rollback_ref) { extra += '<p class="im-kv"><span>Rollback</span>' + esc(t.rollback_ref) + '</p>'; }
    var refs = [];
    if (t.source) { refs.push('<span class="im-refs-l">source</span> ' + link(t.source)); }
    (t.experiments || []).forEach(function (l) { refs.push('<span class="im-refs-l">experiment</span> ' + link(l)); });
    (t.evidence || []).slice(0, 8).forEach(function (l) { refs.push('<span class="im-refs-l">evidence</span> ' + link(l)); });
    return '<li class="im-ev' + (t.is_transition ? ' tr' : '') + '"><span class="im-ev-dot" aria-hidden="true"></span>' +
      '<div class="im-ev-h"><b>' + esc(t.stage_label) + '</b>' + (t.is_transition ? '' : '<small class="muted"> · additional</small>') + chips +
      '<span class="im-ev-at" title="' + esc(utc(t.at)) + '">' + esc(ago(t.at)) + '</span></div>' +
      '<div class="im-ev-who">' + avatar(t.actor, true) + '<span>' + esc(agent(t.actor).name) + '</span><span class="im-tag cls">' + esc(CLASS[t.actor_class] || t.actor_class) + '</span>' +
      (t.recorded_by && t.recorded_by !== t.actor ? '<small>recorded by ' + esc(t.recorded_by) + '</small>' : '') + '</div>' +
      bodyText(t.body) + extra + (refs.length ? '<div class="im-refs">' + refs.join('') + '</div>' : '') + '</li>';
  }
  function renderDrawer() {
    var el = $('im-drawer'), r = S.itemRead;
    if (!S.item) { el.hidden = true; $('im-scrim').hidden = true; return; }
    el.hidden = false; $('im-scrim').hidden = false;
    document.body.classList.add('im-locked');
    var head = '<header class="im-d-top"><span class="mono im-d-id">' + esc(S.item) + '</span><button type="button" class="im-x" id="im-close" aria-label="Close the trail">' + ICON.close + '</button></header>';
    if (!r) { el.innerHTML = head + '<p class="im-d-wait">Reading the trail…</p>'; return; }
    if (r.status !== 'OK') {
      el.innerHTML = head + '<p class="im-state bad"><b>' + esc(r.status.replace('_', ' ')) + '</b> · ' + esc(r.why) + '</p>'; return;
    }
    var d = r.data, it = d.item || {}, n = it.next_required || {}, p = it.protected || {};
    el.innerHTML = head +
      '<div class="im-d-body">' +
      (isFixture(d) ? '<p class="im-fixture-inline">FIXTURE · not production</p>' : '') +
      '<div class="im-d-owner">' + avatar(it.owner_agent) + '<div><small>' + esc(SOURCE[it.source_kind] || it.source_kind) + ' · owner</small><b>' + esc(agent(it.owner_agent).name) + '</b></div>' + disBadge(it.disagreements, true) + '</div>' +
      '<h2 id="im-d-title">' + esc(it.title) + '</h2>' +
      path(d.path) +
      '<div class="im-next big ' + tone(n) + '">' + ICON.arrow + '<div><small>Next required</small><b>' + esc(n.label || 'UNKNOWN') + '</b></div></div>' +
      (p.is_protected ? '<div class="im-protect">' + lock(p, true) + '<p>Requires human review and <b>' + esc(p.required_independent_reviews) + '</b> independent reviews. Classified from: ' + esc(Object.keys(p.basis || {}).map(function (k) { return k.toLowerCase().replace(/_/g, ' ') + ' (“' + p.basis[k] + '”)'; }).join(', ') || 'the source record') + '. A classification can be escalated, never relaxed.</p></div>' : '') +
      '<section class="im-sec"><h3>Problem</h3><p class="im-problem">' + esc(it.problem_statement) + '</p><p class="im-fine">' + esc(it.statement_basis === 'RUNNER_SUMMARY_OF_CITED_RECORDS' ? 'Summarised by the runner from the cited records; every agent statement below is the agent\'s own record.' : 'Statement basis: ' + it.statement_basis) + '</p></section>' +
      '<section class="im-sec"><h3>Reviews</h3><p class="im-kv"><span>Independent PASS</span><b class="mono">' + esc((it.reviews || {}).passes || 0) + ' of ' + esc((it.reviews || {}).required || 1) + '</b>' +
      Object.keys((it.reviews || {}).reviewers || {}).map(function (k) { return ' <span class="im-tag ' + (it.reviews.reviewers[k] === 'PASS' ? 'good' : it.reviews.reviewers[k] === 'FAIL' ? 'bad' : '') + '">' + esc(k) + ' · ' + esc(it.reviews.reviewers[k]) + '</span>'; }).join('') + '</p></section>' +
      ((d.disagreements || []).length ? '<section class="im-sec"><h3>Disagreements · preserved</h3>' + d.disagreements.map(disagreement).join('') + '</section>' : '') +
      '<section class="im-sec"><h3>Trail · ' + esc((d.trail || []).length) + ' record(s)</h3><ol class="im-trail">' + (d.trail || []).map(trailRow).join('') + '</ol></section>' +
      '<section class="im-sec"><h3>Evidence · ' + esc((d.evidence || []).length) + '</h3><ul class="im-evid">' + (d.evidence || []).map(function (l) { return '<li>' + link(l) + '</li>'; }).join('') + '</ul></section>' +
      '<p class="im-fine">' + esc(d.disclosure || '') + '</p>' +
      '<p class="im-fine">Source: <code>GET ' + esc(ITEM + S.item) + '</code> · as of ' + esc(utc(d.as_of)) + '</p>' +
      '</div>';
  }
  function openItem(id, push) {
    if (!ID_RE.test(id)) { return; }
    S.lastFocus = document.activeElement;
    S.item = id; S.itemRead = null;
    if (push) {
      try { history.pushState({item: id}, '', location.pathname + '?item=' + encodeURIComponent(id)); } catch (e) { /* file preview */ }
    }
    renderDrawer();
    $('im-drawer').focus();
    get(ITEM + encodeURIComponent(id)).then(function (r) {
      if (S.item !== id) { return; }
      S.itemRead = r; renderDrawer();
    });
  }
  function closeItem(push) {
    if (!S.item) { return; }
    S.item = null; S.itemRead = null;
    document.body.classList.remove('im-locked');
    renderDrawer();
    if (push) {
      try { history.pushState({}, '', location.pathname); } catch (e) { /* file preview */ }
    }
    if (S.lastFocus && S.lastFocus.focus) { S.lastFocus.focus(); }
  }

  // ── the read loop ────────────────────────────────────────────────────
  function load() {
    return get(BOARD).then(function (r) {
      if (r.status === 'OK' && r.data && r.data.items) {
        var now = {};
        r.data.items.forEach(function (c) { now[c.item_id] = c.stage; });
        S.moved = {};
        if (S.prev) {
          Object.keys(now).forEach(function (k) { if (S.prev[k] && S.prev[k] !== now[k]) { S.moved[k] = true; } });
        }
        S.prev = now;
      }
      S.read = r;
      renderStatus(); renderKpis(); renderBoard();
      S.moved = {};          // the glow marks this poll's change only, once
      if (S.item && r.status === 'OK') {
        get(ITEM + encodeURIComponent(S.item)).then(function (x) { if (S.item) { S.itemRead = x; renderDrawer(); } });
      }
    });
  }
  function schedule() {
    clearTimeout(S.timer);
    S.timer = setTimeout(function () {
      if (document.hidden) { schedule(); return; }
      load().then(schedule, schedule);
    }, POLL_MS);
  }

  document.addEventListener('click', function (e) {
    var t = e.target.closest ? e.target : null;
    if (!t) { return; }
    var c = t.closest('[data-item]');
    if (c) { openItem(c.getAttribute('data-item'), true); return; }
    if (t.closest('#im-close') || t.closest('#im-scrim')) { closeItem(true); return; }
    var o = t.closest('[data-owner]');
    if (o) { S.filter.owner = o.getAttribute('data-owner'); renderFilters(); renderBoard(); return; }
    var g = t.closest('[data-toggle]');
    if (g) { var k = g.getAttribute('data-toggle'); S.filter[k] = !S.filter[k]; renderFilters(); renderBoard(); return; }
    if (t.closest('#im-refresh')) { load(); }
  });
  document.addEventListener('input', function (e) {
    if (e.target && e.target.id === 'im-q') { S.filter.q = e.target.value.trim().slice(0, 80); renderBoard(); }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && S.item) { closeItem(true); }
  });
  window.addEventListener('popstate', function () {
    var id = new URLSearchParams(location.search).get('item');
    if (id && ID_RE.test(id)) { openItem(id, false); } else { closeItem(false); }
  });
  setInterval(function () { if (S.read && !document.hidden) { renderStatus(); } }, 15000);

  renderFilters();
  var first = new URLSearchParams(location.search).get('item');
  load().then(function () {
    if (first && ID_RE.test(first)) { openItem(first, false); }
    schedule();
  });
}());
