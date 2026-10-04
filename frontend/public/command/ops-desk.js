/* BETTOR COMMAND · OPERATIONS DESK (/ops) · Release 1
 *
 * Panels 2-10 of the operations release, on the global layer command-ops.js
 * (window.BTOps: the read broker, the executive header, incidents) and the
 * refusal table ops-taxonomy.js (window.BTOpsTaxonomy).
 *
 * SOURCES (production R29, GET only, COMMAND session, through BTOps.read):
 *   /api/command/coverage?days=2   funnel + sport matrix + PinnAPI supplement
 *                                  (shared with the header's poll, 120 s)
 *   /api/command/paper/derek       decisions (bounded by ?limit), the 24 h
 *                                  refusal summary, ENTRY orders and fills
 *   /api/command/paper/xavier      open positions, management orders
 *   /api/command/paper/stream      SSE of committed ledger entries (the
 *                                  blotter's live trigger; polling fallback)
 *   /api/command/floor             agents: state, work state, monitors
 *   /api/command/profitability/lost-opportunities   hindsight refusal ledger
 *   /api/command/equity/live       capital (the one Command Final loop)
 *   /api/command/release, /overview  (header state, shared)
 *
 * RULES. A value the API does not carry reads DATA NOT AVAILABLE (panel or
 * stage) / NOT AVAILABLE (cell) with the reason in its tooltip; never 0, never
 * interpolated, never a client-side estimate presented as a measurement.
 * Counts taken from a BOUNDED list say so ("in the latest N"). Nothing here
 * writes, admits, refuses, sizes or changes a threshold. */
(function () {
  'use strict';
  if (window.BTOpsDesk) { return; }
  var O = window.BTOps, TX = window.BTOpsTaxonomy;
  var main = document.getElementById('ops-main');
  if (!O || !TX) {
    if (main) {
      main.insertAdjacentHTML('afterbegin', '<div class="od-fatal">DATA NOT AVAILABLE: the operations layer (' +
        (!O ? 'command-ops.js' : 'ops-taxonomy.js') + ') did not load, so no panel can read production.</div>');
    }
    return;
  }
  var F = O.fmt, esc = F.esc, fin = F.fin, num = F.num, epoch = F.epoch;
  var DNA = 'DATA NOT AVAILABLE';

  // ── small renderers ───────────────────────────────────────────────
  function dna(why) { return '<span class="od-dna" title="' + esc(why || 'not served by the production API') + '">' + DNA + '</span>'; }
  function na(why) { return '<span class="od-na" title="' + esc(why || 'not carried for this row') + '">NOT AVAILABLE</span>'; }
  function nf(v, why) { return fin(v) ? F.int(v) : na(why); }
  function pill(t, text, title) { return '<span class="od-pill" data-tone="' + t + '"' + (title ? ' title="' + esc(title) + '"' : '') + '>' + esc(text) + '</span>'; }
  function agoAt(sec) { return fin(sec) ? '<span data-age-at="' + sec + '">' + F.age(F.now() - sec) + ' ago</span>' : '—'; }
  function pct(v, d) { return fin(v) ? (v * 100).toFixed(d == null ? 1 : d) + '%' : null; }
  function pp(v, d) { return fin(v) ? (v > 0 ? '+' : '') + v.toFixed(d == null ? 2 : d) + 'pp' : null; }
  function prob(v) { return fin(v) ? (v * 100).toFixed(1) + '%' : null; }
  function price(v) { return fin(v) ? v.toFixed(3) : null; }
  function or(v, why) { return v == null ? na(why) : v; }
  function clsChip(c) {
    var k = c === 'SOFTWARE' ? 'sw' : c === 'ECONOMIC' ? 'ec' : 'un';
    return '<span class="od-cls od-cls-' + k + '">' + (c === 'SOFTWARE' ? 'SW' : c === 'ECONOMIC' ? 'ECON' : 'UNCL') + '</span>';
  }
  function short(s, n) { s = String(s == null ? '' : s); return s.length > n ? s.slice(0, n - 1) + '…' : s; }

  /* read state of one broker result: data to show + the panel status pill */
  function rs(res) {
    if (!res) { return {data: null, tone: 'dim', text: 'READING', title: 'first read in flight', at: null}; }
    if (res.ok) { return {data: res.data, tone: 'good', text: 'LIVE', title: 'GET ' + res.url + ' → ' + res.http + ' in ' + res.ms + ' ms at ' + F.clock(res.at / 1000), at: res.at / 1000}; }
    var lg = res.lastGood;
    if (lg && lg.ok) {
      return {data: lg.data, tone: 'warn', text: 'STALE', stale: true, at: lg.at / 1000,
              title: 'latest read failed (' + (res.why || res.state) + '); showing the last good read from ' + F.clock(lg.at / 1000)};
    }
    return {data: null, tone: res.state === 'SIGNED_OUT' ? 'warn' : 'bad', text: res.state === 'SIGNED_OUT' ? 'SIGN-IN REQUIRED' : (res.state || 'ERROR'),
            title: res.why || res.state, at: null};
  }
  function sec(s) { return s && typeof s === 'object' && 'status' in s ? s : null; }   // {status, why, data}

  /* one panel: header (title, kicker, status pill, source, as-of) + body + foot */
  function frame(id, o) {
    var el = document.getElementById(id);
    if (!el) { return; }
    var st = o.state || {};
    var html = '<header class="od-ph"><div class="od-ph-l"><h2>' + esc(o.title) + '</h2>' +
      (o.kicker ? '<span class="od-kick">' + o.kicker + '</span>' : '') + '</div><div class="od-ph-r">' +
      (o.tools || '') + (st.text ? pill(st.tone, st.text, st.title) : '') +
      '<span class="od-src" title="' + esc(o.srcTitle || '') + '">' + esc(o.src || '') + '</span>' +
      (fin(st.at) ? '<span class="od-asof">' + agoAt(st.at) + '</span>' : '') + '</div></header>' +
      '<div class="od-pb">' + (o.body || '') + '</div>' + (o.foot ? '<div class="od-pf">' + o.foot + '</div>' : '');
    if (el.__html === html) { return; }
    var keep = [].map.call(el.querySelectorAll('.od-scroll'), function (s) { return s.scrollTop; });
    el.innerHTML = html;
    el.__html = html;
    [].forEach.call(el.querySelectorAll('.od-scroll'), function (s, i) { if (keep[i]) { s.scrollTop = keep[i]; } });
  }
  setInterval(function () {
    if (document.hidden) { return; }
    var t = F.now();
    [].forEach.call(document.querySelectorAll('[data-age-at]'), function (e) {
      var a = Number(e.getAttribute('data-age-at'));
      if (isFinite(a)) { e.textContent = F.age(t - a) + ' ago'; }
    });
  }, 5000);

  /* a sortable table; cols: [{k, h, title, cls, sort(row)->value, cell(row)->html}] */
  function table(id, cols, rows, opt) {
    opt = opt || {};
    var st = SORT[id] || opt.sort || {};
    if (st.k) {
      var c = cols.filter(function (x) { return x.k === st.k; })[0];
      if (c && c.sort) {
        rows = rows.slice().sort(function (a, b) {
          var x = c.sort(a), y = c.sort(b);
          if (x == null && y == null) { return 0; }
          if (x == null) { return 1; }
          if (y == null) { return -1; }
          return (x < y ? -1 : x > y ? 1 : 0) * (st.dir === 'asc' ? 1 : -1);
        });
      }
    }
    var h = '<div class="od-scroll' + (opt.tall ? ' od-tall' : '') + '"><table class="od-t" data-table="' + id + '"><thead><tr>' +
      cols.map(function (c) {
        var s = c.sort ? ' data-sort="' + c.k + '" tabindex="0" role="button" aria-sort="' + (st.k === c.k ? (st.dir === 'asc' ? 'ascending' : 'descending') : 'none') + '"' : '';
        return '<th class="' + (c.cls || '') + (st.k === c.k ? ' od-sorted-' + (st.dir || 'desc') : '') + '"' + s + (c.title ? ' title="' + esc(c.title) + '"' : '') + '>' + esc(c.h) + '</th>';
      }).join('') + '</tr></thead><tbody>';
    if (!rows.length) {
      h += '<tr class="od-empty"><td colspan="' + cols.length + '">' + (opt.empty || 'no rows') + '</td></tr>';
    }
    rows.forEach(function (r, i) {
      var key = opt.key ? opt.key(r, i) : null;
      var open = key && OPEN[id] && OPEN[id][key];
      h += '<tr' + (key ? ' data-exp="' + esc(key) + '" class="od-exp' + (open ? ' od-open' : '') + (opt.rowCls ? ' ' + opt.rowCls(r) : '') + '"' : (opt.rowCls ? ' class="' + opt.rowCls(r) + '"' : '')) + '>' +
        cols.map(function (c) { return '<td class="' + (c.cls || '') + '" data-l="' + esc(c.h) + '"><div class="od-c">' + c.cell(r) + '</div></td>'; }).join('') + '</tr>';
      if (open && opt.detail) { h += '<tr class="od-detail"><td colspan="' + cols.length + '">' + opt.detail(r) + '</td></tr>'; }
    });
    return h + '</tbody></table></div>';
  }
  var SORT = {}, OPEN = {}, RERENDER = {};
  document.addEventListener('click', function (ev) {
    var th = ev.target.closest && ev.target.closest('th[data-sort]');
    if (th) {
      var t = th.closest('table').getAttribute('data-table'), k = th.getAttribute('data-sort');
      var cur = SORT[t] || {};
      SORT[t] = {k: k, dir: cur.k === k && cur.dir === 'desc' ? 'asc' : 'desc'};
      if (RERENDER[t]) { RERENDER[t](); }
      return;
    }
    var tr = ev.target.closest && ev.target.closest('tr[data-exp]');
    if (tr && !ev.target.closest('a,button,select,input')) {
      var tb = tr.closest('table').getAttribute('data-table'), key = tr.getAttribute('data-exp');
      OPEN[tb] = OPEN[tb] || {};
      OPEN[tb][key] = !OPEN[tb][key];
      if (RERENDER[tb]) { RERENDER[tb](); }
    }
  });
  document.addEventListener('keydown', function (ev) {
    if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.matches && ev.target.matches('th[data-sort]')) { ev.preventDefault(); ev.target.click(); }
  });

  // ── shared reads ──────────────────────────────────────────────────
  var R = {derek: null, xavier: null, floor: null, lol: null};
  var LIMITS = [100, 250, 500];
  var oppLimit = 100;
  try { var sl = Number(localStorage.getItem('bt.ops.oppLimit')); if (LIMITS.indexOf(sl) >= 0) { oppLimit = sl; } } catch (e) { /* storage blocked */ }
  function derekUrl() { return '/api/command/paper/derek' + (oppLimit === 100 ? '' : '?limit=' + oppLimit); }
  var derekPoll = null;
  function startDerek() {
    if (derekPoll) { derekPoll.stop(); }
    derekPoll = O.poll(derekUrl(), 30000, function (r) { R.derek = r; renderOpp(); renderBlotter(); renderAgents(); renderRefusals(); renderMatrix(); });
  }

  // ── sport / strategy vocabulary ────────────────────────────────────
  var STRAT = {
    PINNACLE_COMPLETED_GAME_PAPER: {s: 'CG V3', t: 'Investment policy: completed-game, Pinnacle-only, taker (PINNACLE_COMPLETED_GAME_PAPER)', exec: 'TAKER'},
    PINNACLE_COMPLETED_GAME_MAKER_PAPER: {s: 'CG MAKER', t: 'Investment policy, resting bids (maker entry)', exec: 'MAKER'},
    PINNACLE_EXPLORATION_PAPER: {s: 'EXPLORE', t: 'Training / simulated execution (exploration)', exec: null},
    PINNACLE_ONLY_PAPER_BENCHMARK: {s: 'STRICT', t: 'Strict Pinnacle-only benchmark', exec: null},
    DEREK_ENTRY_POLICY_V2: {s: 'DEREK V2', t: "Derek's two-model research policy", exec: null}
  };
  function stratShort(s) { return (STRAT[s] && STRAT[s].s) || (s ? short(s, 18) : '—'); }
  function stratTitle(s) { return (STRAT[s] && STRAT[s].t) || s || 'no strategy recorded'; }
  function sportOfRow(r) {
    var lab = r && r.label || {};
    return O.sportOf(r && (r.us_market_slug || r.market) || '', lab.sport_family);
  }
  function leagueOfRow(r) {
    var lab = r && r.label || {};
    if (lab.competition) { return String(lab.competition); }
    var slug = String(r && (r.us_market_slug || r.market) || '');
    return slug ? slug.split('-')[0].toUpperCase() : '—';
  }
  function marketOf(lab) {
    lab = lab || {};
    var m = lab.market_type ? String(lab.market_type).replace(/_/g, ' ') : null;
    if (!m) { return null; }
    if (lab.line != null && lab.line !== '') { m += ' ' + lab.line; }
    if (lab.period && lab.period !== 'FULL_GAME') { m += ' · ' + String(lab.period).replace(/_/g, ' '); }
    return m;
  }
  function execOf(o) {
    if (!o) { return null; }
    var m = STRAT[o.strategy] && STRAT[o.strategy].exec;
    if (m) { return m; }
    var tif = String(o.time_in_force || '').toUpperCase();
    if (tif === 'IOC' || tif === 'FOK') { return 'TAKER'; }
    if (tif) { return 'RESTING (' + tif + ')'; }
    return null;
  }

  // ═════ 2. LIVE FUNNEL ═══════════════════════════════════════════════
  var STAGES = [
    {h: 'PROVIDER EVENTS', k: 'provider_events', def: 'every provider event the scheduled cycle saw (ext_candidate_outcomes)'},
    {h: 'NORMALIZED', k: 'normalized_events', def: 'reach ≥ 3 in coverage_integrity: the probability AND freshness stages passed'},
    {h: 'VENUE EVENTS', k: 'venue_discovered', def: 'a venue event was discovered for the provider event (reach ≥ 4, or stopped at identity for a reason other than VENUE_ABSENT)'},
    {h: 'EVENTS MATCHED', k: 'mapped_events', def: 'mapped to a venue contract (reach ≥ 4)'},
    {h: 'MARKETS MATCHED', k: null, why: 'the R29 coverage read counts provider EVENTS, not markets: no market-level matched count is served'},
    {h: 'SETTLEMENT SUPPORTED', k: 'settlement_supported', def: 'reach ≥ 5; NULL with its reason when the collection ledger records no row past stage 4'},
    {h: 'PROBABILITY SUPPORTED', k: null, why: 'R29 folds the probability stage into NORMALIZED (reach ≥ 3); no separate count is served'},
    {h: 'FRESH', k: null, why: 'R29 folds the freshness stage into NORMALIZED (reach ≥ 3); no separate count is served'},
    {h: 'EV EVALUATED', k: 'evaluated_events', def: 'a sealed valuation of the event that day (external_valuations)'},
    {h: 'POSITIVE EV', k: null, why: 'no event-level positive-EV count is served; per-decision gross edge and net EV are in Live opportunities'},
    {h: 'AGENT REVIEWED', k: 'decided_events', def: 'a PAPER decision (ENTER / REFUSE) recorded on the valuation (paper_decisions)'},
    {h: 'ENTER', k: 'entered_events', def: 'a decision that said ENTER'},
    {h: 'ORDERS', k: 'ordered_events', def: 'a PAPER ENTRY order on the decision (paper_orders)'},
    {h: 'FILLS', k: 'filled_events', def: 'a simulated fill of that order (paper_fills)'}
  ];
  /* one day's totals across leagues; a stage unmeasured in a league is not summed as 0 */
  function dayTotals(day) {
    var t = {}, miss = {}, leagues = (day && day.leagues) || [];
    STAGES.forEach(function (s) {
      if (!s.k) { return; }
      var sum = 0, measured = 0, unmeasured = [];
      leagues.forEach(function (L) {
        var v = num(L[s.k]);
        if (v == null) { unmeasured.push((L.league_name || L.league) + ': ' + ((L.unavailable || {})[s.k] || 'NULL')); } else { sum += v; measured++; }
      });
      t[s.k] = measured ? sum : null;
      miss[s.k] = unmeasured;
    });
    var cat = 0, catN = 0;
    leagues.forEach(function (L) { var v = num(L.venue_catalogue_events); if (v != null) { cat += v; catN++; } });
    t.venue_catalogue_events = catN ? cat : null;
    return {t: t, miss: miss, n: leagues.length};
  }
  function renderFunnel() {
    var C = rs(O.state.coverage), d = C.data;
    var body;
    if (!d) {
      body = '<div class="od-block">' + dna(C.title || 'coverage not read yet') + '</div>';
    } else if (d.status && d.status !== 'OK' && !(d.days && d.days.length)) {
      body = '<div class="od-block">' + dna((d.status || '') + ': ' + (d.why || 'the coverage read returned no days')) + '</div>';
    } else {
      var days = d.days || [];
      body = '<div class="od-funnel-wrap"><div class="od-funnel" role="table" aria-label="Coverage to fill funnel" style="--days:' + Math.max(1, days.length) + '">' +
        '<div class="od-fr od-fhead" role="row"><div class="od-fl" role="columnheader">Stage</div>' +
        days.map(function (day, i) { return '<div class="od-fc" role="columnheader">' + esc(day.day) + (i === 0 ? ' · TODAY' : '') + '<small>' + esc(d.tz || 'America/New_York') + ' day · ' + (day.leagues || []).length + ' leagues</small></div>'; }).join('') + '</div>';
      var tots = days.map(dayTotals);
      // largest absolute loss between consecutive MEASURED stages, per day
      var worst = tots.map(function (T) {
        var prev = null, best = null;
        STAGES.forEach(function (s, i) {
          if (!s.k) { return; }
          var v = T.t[s.k];
          if (v == null) { return; }
          if (prev && T.t[prev.k] != null) {
            var lost = T.t[prev.k] - v;
            if (lost > 0 && (!best || lost > best.lost)) { best = {i: i, lost: lost}; }
          }
          prev = s;
        });
        return best;
      });
      STAGES.forEach(function (s, i) {
        body += '<div class="od-fr" role="row"><div class="od-fl" role="rowheader" title="' + esc(s.k ? s.def : s.why) + '">' + esc(s.h) + '</div>';
        tots.forEach(function (T, di) {
          if (!s.k) { body += '<div class="od-fc od-fdna" role="cell">' + dna(s.why) + '</div>'; return; }
          var v = T.t[s.k];
          if (v == null) { body += '<div class="od-fc od-fdna" role="cell">' + dna('NOT MEASURED in any league: ' + (T.miss[s.k].join(' · ') || 'no league row')) + '</div>'; return; }
          // previous measured stage for the conversion
          var j = i - 1;
          while (j >= 0 && (!STAGES[j].k || T.t[STAGES[j].k] == null)) { j--; }
          var conv = j >= 0 && T.t[STAGES[j].k] > 0 ? v / T.t[STAGES[j].k] : null;
          var gap = j >= 0 && j < i - 1 ? ' (vs ' + STAGES[j].h.toLowerCase() + ')' : '';
          var hot = worst[di] && worst[di].i === i;
          var partial = T.miss[s.k].length ? '<i class="od-partial" title="' + esc('PARTIAL: not measured in ' + T.miss[s.k].join(' · ')) + '">PARTIAL</i>' : '';
          var lost = j >= 0 ? T.t[STAGES[j].k] - v : null;
          body += '<div class="od-fc' + (hot ? ' od-hot' : '') + (v === 0 && j >= 0 && T.t[STAGES[j].k] > 0 ? ' od-zero' : '') + '" role="cell">' +
            '<b>' + F.int(v) + '</b>' + partial +
            (conv != null ? '<span class="od-conv" title="' + esc('conversion from the previous measured stage' + gap) + '">' + pct(conv, 0) + gap + '</span>' : '') +
            (lost > 0 ? '<span class="od-lost">−' + F.int(lost) + '</span>' : '') +
            '<span class="od-bar" style="--w:' + (T.t.provider_events > 0 ? Math.max(1.5, Math.min(100, 100 * v / T.t.provider_events)).toFixed(1) : 0) + '%"></span></div>';
        });
        body += '</div>';
      });
      body += '<div class="od-fr od-fextra" role="row"><div class="od-fl" role="rowheader" title="us_premap: the venue catalogue holds only current listings (a venue-side supplement, matched or not)">VENUE CATALOGUE (all listed)</div>' +
        tots.map(function (T) { return '<div class="od-fc" role="cell">' + (T.t.venue_catalogue_events == null ? dna('venue catalogue count not served for these leagues') : '<b>' + F.int(T.t.venue_catalogue_events) + '</b><span class="od-conv">listed by the venue</span>') + '</div>'; }).join('') + '</div>';
      body += '</div>' + nflSlate(d.nfl_reconciliation) + '</div>';
    }
    frame('funnel', {
      title: 'Live funnel', kicker: 'unit: provider events · ET day windows · conversion from the previous measured stage · largest loss outlined',
      state: C, src: 'GET /api/command/coverage?days=2 · every 120 s',
      srcTitle: 'coverage_integrity (R29). Snapshots persist every 900 s; today is computed live. The API serves day windows only: a trailing-1 h or live-slate funnel for every sport is not available in R29 (NFL has a live slate reconciliation).',
      body: body,
      foot: 'Windows served: ET calendar day (today, yesterday). Trailing 1 h: ' + DNA + ' (no hourly window in R29). Live slate: NFL only (right). Stages the API does not measure read ' + DNA + ' — never 0.'
    });
  }
  function nflSlate(n) {
    if (!n) { return '<aside class="od-slate">' + dna('no NFL slate reconciliation in this coverage read') + '</aside>'; }
    var st = n.stages || [], re = n.reached || {};
    var h = '<aside class="od-slate"><div class="od-slate-h"><b>NFL LIVE SLATE</b><span>' + esc(n.day || '') + ' · ' + esc(n.status || '') + '</span></div>';
    if (n.status && n.status !== 'OK' && !st.length) { return h + dna(n.why || n.status) + '</aside>'; }
    h += '<div class="od-slate-f">' + st.map(function (s, i) {
      var v = num(re[s]), p = i ? num(re[st[i - 1]]) : null;
      return '<div class="od-sf' + (p != null && v != null && v < p ? ' od-drop' : '') + '"><span>' + esc(String(s).replace(/_/g, ' ')) + '</span><b>' + (v == null ? '—' : F.int(v)) + '</b></div>';
    }).join('') + '</div>';
    var seen = {};
    (n.games || []).forEach(function (g) { if (g.market_slug) { seen[g.market_slug] = 1; } });
    var games = (n.games || []).concat((n.missing || []).filter(function (m) { return !m.market_slug || !seen[m.market_slug]; }).map(function (m) { return Object.assign({stopped_at: 'NOT AT PROVIDER'}, m); }));
    if (games.length) {
      h += '<div class="od-scroll od-slate-g"><table class="od-t od-t-tight od-t-wrap"><thead><tr><th>Game</th><th>Kickoff</th><th>Stopped at</th><th>Reason</th></tr></thead><tbody>' +
        games.map(function (g) {
          return '<tr><td data-l="Game" title="' + esc(g.market_slug || '') + '"><div class="od-c">' + esc(short(g.title || g.market_slug, 34)) + '</div></td><td data-l="Kickoff" class="od-mono"><div class="od-c">' + esc(g.kickoff_local || (g.kickoff_utc ? F.clock(g.kickoff_utc) : '—')) + '</div></td><td data-l="Stopped at"><div class="od-c">' + esc(g.stopped_at || '—') + '</div></td><td data-l="Reason" class="od-reason" title="' + esc(g.reason || '') + '"><div class="od-c">' + esc(short(g.reason || '—', 46)) + '</div></td></tr>';
        }).join('') + '</tbody></table></div>';
    }
    return h + '<div class="od-slate-n">expected ' + nf(num(n.expected), 'expected count not served') + ' games · source nfl_reconciliation</div></aside>';
  }

  // ═════ 3. SPORT COVERAGE MATRIX ═════════════════════════════════════
  var HEALTH_RULE = 'Deterministic rule, per sport, on today\'s coverage rows (ET):\n' +
    'NOT MEASURED — no coverage row for the sport today.\n' +
    'BROKEN — provider events > 0 and a presence stage (normalized → venue → mapped → settlement → evaluated) measured 0 after a non-zero stage; ' +
    'or a league status COVERAGE_INCIDENT; or the venue lists events while no provider event reached the funnel.\n' +
    'NO_CURRENT_EVENTS — no provider events and no venue-listed events, no incident.\n' +
    'DEGRADED — evaluated > 0 but ENTER = 0, or ENTER > 0 but no order, or a league status other than HEALTHY, or a coverage alert today.\n' +
    'HEALTHY — evaluated > 0, ENTER > 0, orders when ENTER > 0, every league status HEALTHY, no alert today. The provider feed existing is never enough.';
  var PRESENCE = ['provider_events', 'normalized_events', 'venue_discovered', 'mapped_events', 'settlement_supported', 'evaluated_events'];
  var MCOLS = [['provider_events', 'Provider'], ['normalized_events', 'Normalized'], ['venue_discovered', 'Venue'], ['mapped_events', 'Mapped'],
               ['settlement_supported', 'Settlement'], ['evaluated_events', 'Evaluated'], ['decided_events', 'Reviewed'], ['entered_events', 'ENTER'],
               ['ordered_events', 'Orders'], ['filled_events', 'Fills'], ['venue_catalogue_events', 'Venue listed']];
  function sportRows(d) {
    var today = (d.days || [])[0] || {}, ls = d.league_status || {}, statuses = ls.statuses || [];
    var bySport = {};
    O.sports.forEach(function (s) { bySport[s] = {sport: s, leagues: {}, alerts: []}; });
    function L(key, fam, name) {
      var s = O.sportOf(key, fam);
      var b = bySport[s] || (bySport[s] = {sport: s, leagues: {}, alerts: []});
      return b.leagues[key] || (b.leagues[key] = {league: key, name: name || key, counts: {}, status: null, reason: null, unavailable: {}});
    }
    (today.leagues || []).forEach(function (r) {
      var x = L(r.league, r.sport_family, r.league_name);
      MCOLS.forEach(function (c) { x.counts[c[0]] = num(r[c[0]]); });
      x.unavailable = r.unavailable || {};
      x.ratios = r.ratios || {};
    });
    statuses.forEach(function (st) {
      var x = L(st.league, st.sport_family, st.league_name);
      x.status = st.status; x.reason = st.reason; x.stage = st.stage;
      var c = st.counts || {};
      MCOLS.forEach(function (k) { if (x.counts[k[0]] == null && c[k[0]] != null) { x.counts[k[0]] = num(c[k[0]]); } });
    });
    (d.alerts || []).forEach(function (a) {
      if (today.day && a.day !== today.day) { return; }
      var s = O.sportOf(a.league);
      if (bySport[s]) { bySport[s].alerts.push(a); }
    });
    return Object.keys(bySport).map(function (k) {
      var b = bySport[k], ls2 = Object.keys(b.leagues).map(function (x) { return b.leagues[x]; });
      var sum = {};
      MCOLS.forEach(function (c) {
        var vals = ls2.map(function (x) { return x.counts[c[0]]; }).filter(function (v) { return v != null; });
        sum[c[0]] = vals.length ? vals.reduce(function (a, v) { return a + v; }, 0) : null;
      });
      b.sum = sum; b.list = ls2;
      b.health = health(b);
      return b;
    });
  }
  function health(b) {
    if (!b.list.length) { return {h: 'NOT MEASURED', why: 'no coverage row for this sport today'}; }
    var s = b.sum;
    var bad = b.list.filter(function (x) { return x.status === 'COVERAGE_INCIDENT'; });
    if (bad.length) { return {h: 'BROKEN', why: 'COVERAGE_INCIDENT: ' + bad.map(function (x) { return x.name + ' — ' + (x.reason || x.stage || ''); }).join(' · ')}; }
    for (var li = 0; li < b.list.length; li++) {
      var c = b.list[li].counts, prev = null;
      for (var i = 0; i < PRESENCE.length; i++) {
        var v = c[PRESENCE[i]];
        if (v == null) { continue; }
        if (prev != null && prev > 0 && v === 0) { return {h: 'BROKEN', why: b.list[li].name + ': ' + PRESENCE[i].replace(/_/g, ' ') + ' = 0 after ' + prev + ' at the previous measured stage'}; }
        prev = v;
      }
    }
    if (!(s.provider_events > 0) && s.venue_catalogue_events > 0) {
      var rs2 = b.list.filter(function (x) { return x.reason; }).map(function (x) { return x.name + ': ' + x.reason; });
      return {h: 'BROKEN', why: 'the venue lists ' + s.venue_catalogue_events + ' event(s) but no provider event reached the funnel' + (rs2.length ? ' — ' + rs2.join(' · ') : '')};
    }
    if (!(s.provider_events > 0)) { return {h: 'NO_CURRENT_EVENTS', why: 'no provider events and no venue-listed events today' + (s.venue_catalogue_events == null ? ' (venue catalogue not measured)' : '')}; }
    if (s.evaluated_events > 0 && !(s.entered_events > 0)) { return {h: 'DEGRADED', why: s.evaluated_events + ' evaluated, 0 ENTER today'}; }
    if (s.entered_events > 0 && !(s.ordered_events > 0)) { return {h: 'DEGRADED', why: s.entered_events + ' ENTER, 0 orders today'}; }
    var notH = b.list.filter(function (x) { return x.status && x.status !== 'HEALTHY'; });
    if (notH.length) { return {h: 'DEGRADED', why: notH.map(function (x) { return x.name + ' ' + x.status + (x.reason ? ' — ' + x.reason : ''); }).join(' · ')}; }
    if (b.alerts.length) { return {h: 'DEGRADED', why: b.alerts.map(function (a) { return (a.detail && a.detail.statement) || a.kind; }).join(' · ')}; }
    if (s.evaluated_events > 0) { return {h: 'HEALTHY', why: 'evaluated ' + s.evaluated_events + ', ENTER ' + s.entered_events + ', orders ' + s.ordered_events + '; no stage collapses, no alert'}; }
    return {h: 'DEGRADED', why: 'provider events present but nothing evaluated' + (s.evaluated_events == null ? ' (evaluated not measured)' : '')};
  }
  var HT = {HEALTHY: 'good', DEGRADED: 'warn', BROKEN: 'bad', NO_CURRENT_EVENTS: 'dim', 'NOT MEASURED': 'dim'};
  function topRefusalBySport() {
    var D = rs(R.derek).data, opp = D && sec(D.opportunities), out = {};
    ((opp && opp.data) || []).forEach(function (r) {
      if (r.verdict === 'ENTER' || !r.refusal) { return; }
      var s = sportOfRow(r), c = TX.normalize(r.refusal);
      var m = out[s] || (out[s] = {});
      m[c] = (m[c] || 0) + 1;
    });
    return out;
  }
  function renderMatrix() {
    var C = rs(O.state.coverage), d = C.data, body;
    if (!d || !(d.days || d.league_status)) {
      body = '<div class="od-block">' + dna(C.title || (d && d.why) || 'coverage not read yet') + '</div>';
    } else {
      var rows = sportRows(d), tops = topRefusalBySport(), nDec = ((sec((rs(R.derek).data || {}).opportunities) || {}).data || []).length;
      var cols = [
        {k: 'sport', h: 'Sport', sort: function (b) { return O.sports.indexOf(b.sport); }, cell: function (b) { return '<b>' + esc(b.sport) + '</b><small>' + (b.list.length ? b.list.length + ' league' + (b.list.length > 1 ? 's' : '') : '') + '</small>'; }},
        {k: 'health', h: 'Health', title: HEALTH_RULE, sort: function (b) { return ['BROKEN', 'DEGRADED', 'HEALTHY', 'NO_CURRENT_EVENTS', 'NOT MEASURED'].indexOf(b.health.h) * -1; },
         cell: function (b) { return pill(HT[b.health.h] || 'dim', b.health.h.replace('_', ' '), b.health.why + '\n\n' + HEALTH_RULE); }}
      ].concat(MCOLS.map(function (c) {
        return {k: c[0], h: c[1], cls: 'od-num', sort: function (b) { return b.sum[c[0]]; }, cell: function (b) {
          if (!b.list.length) { return na('no coverage row for this sport today'); }
          var v = b.sum[c[0]];
          if (v == null) {
            var why = b.list.map(function (x) { return x.name + ': ' + ((x.unavailable || {})[c[0]] || 'not measured'); }).join(' · ');
            return na(why);
          }
          return F.int(v);
        }};
      })).concat([{k: 'blocker', h: 'Top blocker', cls: 'od-reason', cell: function (b) {
        var bits = [];
        var nh = b.list.filter(function (x) { return x.reason && x.status !== 'HEALTHY'; })[0];
        if (nh) { bits.push(nh.reason); }
        var t = tops[b.sport];
        if (t) {
          var k = Object.keys(t).sort(function (x, y) { return t[y] - t[x]; })[0];
          var c2 = TX.classify(k);
          bits.push(clsChip(c2.cls) + ' ' + esc(k) + ' ×' + t[k] + ' <small>(latest ' + nDec + ' decisions)</small>');
          return '<span title="' + esc(nh ? nh.reason : '') + '">' + (nh ? esc(short(nh.reason, 60)) + '<br>' : '') + bits[bits.length - 1] + '</span>';
        }
        return bits.length ? '<span title="' + esc(bits[0]) + '">' + esc(short(bits[0], 80)) + '</span>' : (b.list.length ? '<span class="od-soft">none recorded</span>' : na('no coverage row'));
      }}]);
      RERENDER['matrix'] = renderMatrix;
      body = table('matrix', cols, rows, {
        sort: {k: 'sport', dir: 'asc'},
        key: function (b) { return b.sport; },
        rowCls: function (b) { return 'od-h-' + (HT[b.health.h] || 'dim'); },
        detail: function (b) {
          if (!b.list.length) { return '<div class="od-soft">No league of this sport is in today\'s coverage read.</div>'; }
          return '<table class="od-t od-t-tight"><thead><tr><th>League</th><th>Status</th>' + MCOLS.map(function (c) { return '<th class="od-num">' + esc(c[1]) + '</th>'; }).join('') + '<th>Reason</th></tr></thead><tbody>' +
            b.list.map(function (x) {
              return '<tr><td>' + esc(x.name) + '<small>' + esc(x.league) + '</small></td><td>' + (x.status ? pill(x.status === 'HEALTHY' ? 'good' : x.status === 'COVERAGE_INCIDENT' ? 'bad' : 'warn', x.status) : '<span class="od-soft">—</span>') + '</td>' +
                MCOLS.map(function (c) { var v = x.counts[c[0]]; return '<td class="od-num">' + (v == null ? na((x.unavailable || {})[c[0]] || 'not measured') : F.int(v)) + '</td>'; }).join('') +
                '<td class="od-reason" title="' + esc(x.reason || '') + '">' + esc(short(x.reason || '—', 90)) + '</td></tr>';
            }).join('') + '</tbody></table>';
        }
      });
    }
    frame('coverage', {
      title: 'Sport coverage matrix', kicker: 'today · events per stage · click a sport for its leagues · health rule in the column tooltip',
      state: C, src: 'GET /api/command/coverage?days=2 · every 120 s', srcTitle: O.sportRule, body: body,
      foot: 'Mapped markets: ' + DNA + ' (coverage counts events, not markets). Probability supported: folded into Normalized in R29. ' + esc(O.sportRule)
    });
  }

  // ═════ 4. LIVE OPPORTUNITY TABLE ════════════════════════════════════
  var FILT = {sport: '', league: '', strategy: '', verdict: '', posev: false, cls: ''};
  try { var sf = JSON.parse(sessionStorage.getItem('bt.ops.oppFilter') || 'null'); if (sf && typeof sf === 'object') { Object.keys(FILT).forEach(function (k) { if (k in sf) { FILT[k] = sf[k]; } }); } } catch (e) { /* storage blocked */ }
  function econ(d) {
    var e = d.economics || {}, pd = d.policy_decision || {}, acq = e.acquisition || {};
    var gross = num(e.best_level_edge_pp); if (gross == null) { gross = num(pd.gross_edge_pp); }
    var netUsd = num(acq.expected_net_profit_usd); if (netUsd == null) { netUsd = num(pd.net_expected_profit_usd); }
    var netPp = e.fee_stop ? num(e.fee_stop.net_edge_pp) : null;
    var depth = num(e.depth_within_limit), depthBasis = 'displayed depth within the break-even limit (economics.depth_within_limit)';
    if (depth == null && e.levels && e.levels.length) { depth = num(e.levels[0].qty); depthBasis = 'displayed quantity at the best level (economics.levels[0])'; }
    var bookAge = num(e.book_age_s);
    if (bookAge == null && d.book && d.book.observed_at != null && d.decided_at != null) { var a = epoch(d.decided_at), b = epoch(d.book.observed_at); if (a != null && b != null) { bookAge = Math.max(0, a - b); } }
    var posEv = netUsd != null ? netUsd > 0 : netPp != null ? netPp > 0 : (e.net_ev_positive === true ? true : e.net_ev_positive === false ? false : null);
    return {gross: gross, netUsd: netUsd, netPp: netPp, depth: depth, depthBasis: depthBasis, bookAge: bookAge, posEv: posEv,
            threshold: num(e.threshold_edge_pp)};
  }
  function ordersByDecision(D) {
    var m = {};
    ((sec(D && D.orders) || {}).data || []).forEach(function (o) { if (o.decision_id) { (m[o.decision_id] = m[o.decision_id] || []).push(o); } });
    return m;
  }
  function renderOpp() {
    var S = rs(R.derek), D = S.data, opp = D && sec(D.opportunities), body, tools = '';
    tools = '<label class="od-ctl" title="How many of the most recent decisions to read (the endpoint\'s own limit; no pagination cursor exists in R29)">rows <select data-od="limit">' +
      LIMITS.map(function (n) { return '<option value="' + n + '"' + (n === oppLimit ? ' selected' : '') + '>' + n + '</option>'; }).join('') + '</select></label>';
    if (!D) { body = '<div class="od-block">' + dna(S.title || 'decisions not read yet') + '</div>'; }
    else if (!opp || opp.status !== 'OK') { body = '<div class="od-block">' + (opp && opp.status === 'EMPTY' ? '<span class="od-soft">' + esc(opp.why || 'no decision yet') + '</span>' : dna((opp && (opp.status + ': ' + (opp.why || ''))) || 'opportunities section absent')) + '</div>'; }
    else {
      var all = opp.data || [], obd = ordersByDecision(D);
      var sports = {}, leagues = {}, strats = {};
      all.forEach(function (r) { sports[sportOfRow(r)] = 1; leagues[leagueOfRow(r)] = 1; strats[r.strategy || ''] = 1; });
      var rows = all.filter(function (r) {
        if (FILT.sport && sportOfRow(r) !== FILT.sport) { return false; }
        if (FILT.league && leagueOfRow(r) !== FILT.league) { return false; }
        if (FILT.strategy && (r.strategy || '') !== FILT.strategy) { return false; }
        if (FILT.verdict === 'ENTER' && r.verdict !== 'ENTER') { return false; }
        if (FILT.verdict === 'PASS' && r.verdict === 'ENTER') { return false; }
        if (FILT.posev && econ(r).posEv !== true) { return false; }
        if (FILT.cls && (r.verdict === 'ENTER' || TX.classify(r.refusal).cls !== FILT.cls)) { return false; }
        return true;
      });
      function sel(k, label, opts) {
        return '<label class="od-ctl">' + label + ' <select data-of="' + k + '"><option value="">all</option>' + opts.map(function (o) {
          var v = Array.isArray(o) ? o[0] : o, t = Array.isArray(o) ? o[1] : o;
          return '<option value="' + esc(v) + '"' + (FILT[k] === v ? ' selected' : '') + '>' + esc(t) + '</option>';
        }).join('') + '</select></label>';
      }
      var filters = '<div class="od-filters">' +
        sel('sport', 'Sport', Object.keys(sports).sort()) + sel('league', 'League', Object.keys(leagues).sort()) +
        sel('strategy', 'Agent / strategy', Object.keys(strats).sort().map(function (s) { return [s, stratShort(s)]; })) +
        sel('verdict', 'Decision', [['ENTER', 'ENTER'], ['PASS', 'PASS / REFUSE']]) +
        sel('cls', 'Rejection', [['SOFTWARE', 'software'], ['ECONOMIC', 'economic'], ['UNCLASSIFIED', 'unclassified']]) +
        '<label class="od-ctl od-chk"><input type="checkbox" data-of="posev"' + (FILT.posev ? ' checked' : '') + '> positive EV only</label>' +
        '<span class="od-ctl od-off" title="The R29 decision record carries no in-play / pre-game flag or kickoff time">live / pre-game: ' + DNA + '</span>' +
        '<span class="od-count">' + rows.length + ' of ' + all.length + ' (latest ' + all.length + ' decisions; limit ' + oppLimit + ')' + '</span></div>';
      var anyModel = all.some(function (r) { return r.p_blended != null || r.p_internal != null; });
      var mkts = {}; all.forEach(function (r) { mkts[marketOf(r.label) || 'NOT RECORDED'] = 1; });
      var oneMarket = Object.keys(mkts).length === 1 ? Object.keys(mkts)[0] : null;
      var cols = [
        {k: 't', h: 'Time', cls: 'od-mono', sort: function (r) { return epoch(r.decided_at); }, cell: function (r) { return F.clock(r.decided_at); }},
        {k: 'sport', h: 'Sport', title: 'sport bucket · league (label.competition)', sort: function (r) { return sportOfRow(r) + leagueOfRow(r); }, cell: function (r) { var s1 = sportOfRow(r), l1 = leagueOfRow(r); return esc(s1) + (l1 && l1 !== s1 ? '<small>' + esc(l1) + '</small>' : ''); }},
        {k: 'event', h: 'Event', cls: 'od-ev', sort: function (r) { return (r.label || {}).event_title || r.us_market_slug; }, cell: function (r) { var l = r.label || {}; return '<span title="' + esc((l.event_title || '') + ' · ' + (r.us_market_slug || '')) + '">' + esc(l.event_title || r.us_market_slug || '—') + '</span>'; }},
      ].concat(oneMarket ? [] : [{k: 'mkt', h: 'Market', cell: function (r) { return or(marketOf(r.label) && esc(marketOf(r.label)), 'market type not recorded'); }}]).concat([
        {k: 'side', h: 'Side', cell: function (r) { var l = r.label || {}; return '<span title="' + esc((l.participant || '') + ' · ' + (r.holding_side || '')) + '">' + esc(short(l.participant || r.holding_side || '—', 15)) + '</span>'; }},
        {k: 'pp', h: 'Pinn p', title: 'Pinnacle de-vigged probability at the decision', cls: 'od-num', sort: function (r) { return num(r.p_pinnacle); }, cell: function (r) { var p = r.pinnacle || {}; return fin(num(r.p_pinnacle)) ? '<span title="' + esc((p.provider || '') + (fin(num(p.age_s)) ? ' · age ' + p.age_s + ' s (limit ' + p.limit_s + ' s)' : '')) + '">' + prob(num(r.p_pinnacle)) + '</span>' : na('no Pinnacle probability on this decision'); }}
      ]).concat(anyModel ? [{k: 'pm', h: 'Model p', cls: 'od-num', title: 'p_blended (two-model policy) or p_internal', sort: function (r) { return num(r.p_blended != null ? r.p_blended : r.p_internal); }, cell: function (r) { var v = num(r.p_blended != null ? r.p_blended : r.p_internal); return v == null ? na('this strategy uses no internal model') : prob(v); }}] : []).concat([
        {k: 'ask', h: 'Ask', title: 'venue best ask at the decision (bid in the tooltip)', cls: 'od-num', sort: function (r) { return num((r.book || {}).best_ask); }, cell: function (r) { var b = r.book || {}; return fin(num(b.best_ask)) ? '<span title="bid ' + esc(price(num(b.best_bid)) || '—') + ' / ask ' + esc(price(num(b.best_ask))) + '">' + price(num(b.best_ask)) + '</span>' : na('no venue ask recorded'); }},
        {k: 'gross', h: 'Gross', cls: 'od-num', sort: function (r) { return econ(r).gross; }, cell: function (r) { var e = econ(r); return e.gross == null ? na('no gross edge recorded') : '<span class="' + (e.gross > 0 ? 'od-pos' : 'od-neg') + '" title="' + esc(e.threshold != null ? 'threshold ' + e.threshold + ' pp' : '') + '">' + pp(e.gross) + '</span>'; }},
        {k: 'net', h: 'Net EV', cls: 'od-num', title: 'modelled net profit after the simulator\'s fees (economics.acquisition / policy_decision); or the net edge where the fee stopped the walk', sort: function (r) { var e = econ(r); return e.netUsd != null ? e.netUsd : e.netPp; },
         cell: function (r) { var e = econ(r); if (e.netUsd != null) { return '<span class="' + (e.netUsd > 0 ? 'od-pos' : 'od-neg') + '">' + F.sgnUsd(e.netUsd) + '</span>'; } if (e.netPp != null) { return '<span class="' + (e.netPp > 0 ? 'od-pos' : 'od-neg') + '" title="net edge at the level where fees consumed it (economics.fee_stop)">' + pp(e.netPp) + '</span>'; } return na('no net EV computed (refused before sizing)'); }},
        {k: 'fresh', h: 'Fresh', cls: 'od-num', title: 'Pinnacle quote age at decision / venue book age at decision', cell: function (r) { var p = r.pinnacle || {}, e = econ(r); var a = num(p.age_s), l = num(p.limit_s); return (a == null ? '—' : '<span class="' + (l != null && a > l ? 'od-neg' : '') + '">' + a.toFixed(1) + '</span>') + '/' + (e.bookAge == null ? '—' : e.bookAge.toFixed(1)) + 's'; }},
        {k: 'depth', h: 'Depth', cls: 'od-num', sort: function (r) { return econ(r).depth; }, cell: function (r) { var e = econ(r); return e.depth == null ? na('no depth recorded') : '<span title="' + esc(e.depthBasis) + '">' + F.int(e.depth) + '</span>'; }},
        {k: 'agent', h: 'Strategy', title: 'Derek records every PAPER entry decision; the strategy (policy) that decided', sort: function (r) { return r.strategy; }, cell: function (r) { return '<span title="' + esc('Derek · ' + stratTitle(r.strategy) + (r.policy_version ? ' · ' + r.policy_version : '')) + '">' + esc(stratShort(r.strategy)) + '</span>'; }},
        {k: 'verdict', h: 'Decision', title: 'ENTER, or the refusal code with its class (SW software · ECON economic · UNCL unclassified)', cls: 'od-reason', sort: function (r) { return r.verdict === 'ENTER' ? 'A' : 'B' + (r.refusal || ''); }, cell: function (r) {
          if (r.verdict === 'ENTER' || !r.refusal) { return pill(r.verdict === 'ENTER' ? 'good' : 'dim', r.verdict || '—'); }
          var c = TX.classify(r.refusal);
          return clsChip(c.cls) + ' <span title="' + esc((r.verdict || '') + ' · ' + c.code + ' · ' + c.stage + ' · ' + c.basis) + '">' + esc(short(c.code, 28)) + '</span>'; }},
        {k: 'qty', h: 'Size → order', title: 'proposed quantity on the decision → the PAPER ENTRY order the ledger accepted: quantity, execution method, recorded state', sort: function (r) { return num(r.proposed_qty); }, cell: function (r) {
          var v = num(r.proposed_qty), os = obd[r.decision_id];
          var prop = v == null ? (r.verdict === 'ENTER' ? na('no proposed quantity recorded') : '<span class="od-soft">—</span>') : F.int(v);
          if (!os) { return prop + (r.verdict === 'ENTER' ? '<small>' + na('no ENTRY order for this decision in the latest orders read') + '</small>' : ''); }
          return prop + ' → ' + F.int(os.reduce(function (a, o) { return a + (num(o.qty) || 0); }, 0)) + '<small>' + os.map(function (o) { return esc((execOf(o) || '') + ' · ' + String(o.state || '').replace(/_/g, ' ').toLowerCase()); }).join(', ') + '</small>'; }}
      ]);
      RERENDER['opp'] = renderOpp;
      if (oneMarket) { filters = filters.replace('<span class="od-count">', '<span class="od-ctl od-off" title="every decision in this read is on the same market type, so the column is folded">market: ' + esc(oneMarket) + ' (all)</span><span class="od-count">'); }
      body = filters + table('opp', cols, rows, {
        tall: true,
        sort: SORT.opp ? null : {k: 'net', dir: 'desc'},
        key: function (r) { return r.decision_id; },
        rowCls: function (r) { return r.verdict === 'ENTER' ? 'od-enter' : econ(r).posEv ? 'od-posev' : ''; },
        empty: 'no decision matches the filters',
        detail: function (r) { return oppDetail(r, obd[r.decision_id]); }
      });
    }
    frame('opportunities', {
      title: 'Live opportunities', kicker: 'PAPER decisions · positive EV first · click a row for the full decision record',
      state: S, src: 'GET /api/command/paper/derek · every 30 s', tools: tools, body: body,
      foot: 'Not carried by the R29 decision record (columns omitted): a separate fair price (Pinnacle de-vigged p is the fair value these policies use), an approved size distinct from the ledger order, in-play / pre-game state. Refusal class from the one frontend table (ops-taxonomy.js, R29 ' + esc(TX.sourceSha) + '); an unknown code is UNCLASSIFIED, never economic.'
    });
  }
  function oppDetail(r, os) {
    var e = r.economics || {}, c = TX.classify(r.refusal);
    var kv = [
      ['Decision', r.decision_id], ['Decided', F.stamp(r.decided_at)], ['Market', r.us_market_slug], ['Fixture', r.fixture], ['Intent', r.intent],
      ['Policy', r.policy_version], ['Strategy', stratTitle(r.strategy)], ['Valuation', r.valuation_id], ['Book observation', r.book_obs_id],
      ['Limit price', r.limit_price], ['Simulator', r.simulator_version],
      ['Refusal', r.refusal ? c.code + ' — ' + c.cls + ' · ' + c.stage + ' (' + c.basis + ')' : null],
      ['All refusals', (r.refusals || []).map(function (x) { return typeof x === 'string' ? x : (x.code || x.refusal || JSON.stringify(x)); }).join(', ') || null],
      ['Explanation', r.explanation]
    ];
    var lv = (e.levels || []).slice(0, 6);
    return '<div class="od-dgrid"><dl>' + kv.filter(function (x) { return x[1] != null && x[1] !== ''; }).map(function (x) { return '<dt>' + esc(x[0]) + '</dt><dd>' + esc(x[1]) + '</dd>'; }).join('') + '</dl>' +
      (lv.length ? '<div><b class="od-sub">Book levels at decision</b><table class="od-t od-t-tight"><thead><tr><th class="od-num">Price</th><th class="od-num">Qty</th><th class="od-num">Edge</th></tr></thead><tbody>' +
        lv.map(function (l) { return '<tr><td class="od-num">' + esc(price(num(l.price)) || '—') + '</td><td class="od-num">' + esc(F.int(num(l.qty)) || '—') + '</td><td class="od-num">' + esc(pp(num(l.edge_pp)) || '—') + '</td></tr>'; }).join('') + '</tbody></table></div>' : '') +
      (os ? '<div><b class="od-sub">Orders</b>' + os.map(function (o) { return '<div class="od-mono">' + esc(o.order_id) + ' · ' + esc(o.state) + ' · ' + esc(F.int(num(o.qty))) + ' @ ' + esc(price(num(o.limit_price))) + ' · filled ' + esc(F.int(num(o.filled_qty)) || '0') + '</div>'; }).join('') + '</div>' : '') +
      ((r.qualification_gaps || []).length ? '<div><b class="od-sub">Qualification gaps</b>' + r.qualification_gaps.map(function (g) { return '<div><span class="od-mono">' + esc(g.gap || '') + '</span> ' + esc(g.status || '') + '</div>'; }).join('') + '</div>' : '') + '</div>';
  }
  document.addEventListener('change', function (ev) {
    var t = ev.target;
    if (t.matches && t.matches('select[data-of],input[data-of]')) {
      var k = t.getAttribute('data-of');
      FILT[k] = t.type === 'checkbox' ? t.checked : t.value;
      try { sessionStorage.setItem('bt.ops.oppFilter', JSON.stringify(FILT)); } catch (e) { /* storage blocked */ }
      renderOpp();
    } else if (t.matches && t.matches('select[data-od="limit"]')) {
      var n = Number(t.value);
      if (LIMITS.indexOf(n) >= 0 && n !== oppLimit) {
        oppLimit = n;
        try { localStorage.setItem('bt.ops.oppLimit', String(n)); } catch (e) { /* storage blocked */ }
        R.derek = null; renderOpp(); startDerek();
      }
    } else if (t.matches && t.matches('select[data-ob]')) {
      BLOT.view = t.value; renderBlotter();
    }
  });

  // ═════ 5. ORDERS & FILLS BLOTTER (PAPER) ═════════════════════════════
  var ST_TONE = {PENDING_SIMULATION: 'dim', RESTING: 'info', PARTIALLY_FILLED: 'warn', CANCEL_PENDING: 'warn', FILLED: 'good', CANCELED: 'dim', CANCELLED: 'dim', REJECTED: 'bad', EXPIRED: 'bad'};
  var ST_SHORT = {PENDING_SIMULATION: 'PENDING SIM', PARTIALLY_FILLED: 'PARTIAL', CANCEL_PENDING: 'CANCEL PEND'};
  function stateChip(s) { return pill(ST_TONE[s] || 'dim', ST_SHORT[s] || String(s || '—').replace(/_/g, ' '), s ? 'paper ledger state ' + s : ''); }
  var BLOT = {view: 'orders', tape: [], stream: 'CONNECTING', streamWhy: '', lastSeq: null, es: null, errors: 0};
  function renderBlotter() {
    var S = rs(R.derek), D = S.data, X = rs(R.xavier).data;
    var ords = ((sec(D && D.orders) || {}).data || []).map(function (o) { return Object.assign({_src: 'ENTRY'}, o); })
      .concat(((sec(X && X.standing_orders) || {}).data || []).map(function (o) { return Object.assign({_src: 'MGMT'}, o); }));
    var fills = (sec(D && D.fills) || {}).data || [];
    var byOrder = {};
    fills.forEach(function (f) { (byOrder[f.order_id] = byOrder[f.order_id] || []).push(f); });
    var dec = {};
    ((sec(D && D.opportunities) || {}).data || []).forEach(function (r) { dec[r.decision_id] = r; });
    var streamTone = BLOT.stream === 'LIVE' ? 'good' : BLOT.stream === 'POLLING' ? 'warn' : BLOT.stream === 'PAUSED' ? 'dim' : 'warn';
    var tools = '<label class="od-ctl">view <select data-ob="1"><option value="orders"' + (BLOT.view === 'orders' ? ' selected' : '') + '>orders</option><option value="fills"' + (BLOT.view === 'fills' ? ' selected' : '') + '>fills</option><option value="tape"' + (BLOT.view === 'tape' ? ' selected' : '') + '>ledger tape (SSE)</option></select></label>' +
      pill(streamTone, 'SSE ' + BLOT.stream, BLOT.streamWhy || 'GET /api/command/paper/stream (committed ledger entries)');
    var body;
    if (!D && BLOT.view !== 'tape') { body = '<div class="od-block">' + dna(S.title || 'orders not read yet') + '</div>'; }
    else if (BLOT.view === 'orders') {
      var failed = ords.filter(function (o) { return /REJECTED|EXPIRED/.test(o.state || ''); }).length;
      function avgFill(o) {
        var fs = byOrder[o.order_id]; if (!fs) { return null; }
        var q = 0, c = 0; fs.forEach(function (f) { var fq = num(f.qty), fp = num(f.price); if (fq != null && fp != null) { q += fq; c += fq * fp; } });
        return q > 0 ? c / q : null;
      }
      var cols = [
        {k: 't', h: 'Time', cls: 'od-mono', sort: function (o) { return epoch(o.created_at); }, cell: function (o) { return F.clock(o.created_at); }},
        {k: 'ev', h: 'Event · side', cls: 'od-ev', cell: function (o) { var l = o.label || {}; return '<span title="' + esc((l.event_title || '') + ' · ' + (o.us_market_slug || '')) + '">' + esc(l.event_title || o.us_market_slug || '—') + '</span><small>' + esc((o.direction || '') + ' ' + (l.participant || o.holding_side || '')) + '</small>'; }},
        {k: 'role', h: 'Role · exec', cell: function (o) { return esc(String(o.role || o._src).replace('STANDING_PROTECTION', 'PROTECT')) + ' <span class="od-soft">' + esc(stratShort(o.strategy)) + '</span><small>' + (execOf(o) ? esc(execOf(o)) : na('time in force not recorded')) + ' · ' + (o._src === 'MGMT' ? 'Xavier' : 'Derek') + '</small>'; }},
        {k: 'qty', h: 'Fill / qty', cls: 'od-num', sort: function (o) { return num(o.qty); }, cell: function (o) { return nf(num(o.filled_qty), 'no filled quantity') + ' / ' + nf(num(o.qty), 'no quantity'); }},
        {k: 'lim', h: 'Limit · fill', cls: 'od-num', title: 'limit price · quantity-weighted price of this order\'s recorded fills (latest fills read)', cell: function (o) {
          var a = avgFill(o);
          return or(price(num(o.limit_price)), 'no limit price') + '<small>' + (a != null ? price(a) : num(o.filled_qty) > 0 ? na('the fills of this order are not in the latest fills read') : '—') + '</small>'; }},
        {k: 'st', h: 'State', cls: 'od-st', sort: function (o) { return o.state; }, cell: function (o) { return stateChip(o.state) + (o.terminal_reason && o.terminal_reason !== o.state ? '<small title="' + esc(o.terminal_reason) + '">' + esc(short(o.terminal_reason, 24)) + '</small>' : ''); }},
        {k: 'xe', h: 'Edge · exec', cls: 'od-num', title: 'expected: gross edge on the decision that produced the order · executed: decision Pinnacle p minus the average fill price', cell: function (o) {
          var r = dec[o.decision_id];
          if (!r) { return o._src === 'ENTRY' ? na('decision not in the latest decisions read') : '<span class="od-soft">—</span>'; }
          var e = econ(r), a = avgFill(o), x = a != null && num(r.p_pinnacle) != null ? (num(r.p_pinnacle) - a) * 100 : null;
          return (e.gross == null ? na('no gross edge on the decision') : pp(e.gross)) + '<small>' + (x == null ? '—' : '<span class="' + (x > 0 ? 'od-pos' : 'od-neg') + '">' + pp(x) + '</span>') + '</small>'; }}
      ];
      RERENDER['blot'] = renderBlotter;
      body = '<div class="od-mini">' + ords.length + ' orders in the latest reads · ' + (failed ? '<b class="od-neg">' + failed + ' rejected / expired</b>' : '0 rejected / expired') + ' · open states ' + ['PENDING_SIMULATION', 'RESTING', 'PARTIALLY_FILLED', 'CANCEL_PENDING'].map(function (s) { return s.toLowerCase().replace(/_/g, ' ') + ' ' + ords.filter(function (o) { return o.state === s; }).length; }).join(' · ') + '</div>' +
        table('blot', cols, ords, {sort: {k: 't', dir: 'desc'}, rowCls: function (o) { return /REJECTED|EXPIRED/.test(o.state || '') ? 'od-fail' : ''; }, empty: esc(((sec(D.orders) || {}).why) || 'no PAPER order')});
    } else if (BLOT.view === 'fills') {
      var fcols = [
        {k: 't', h: 'Filled', cls: 'od-mono', sort: function (f) { return epoch(f.filled_at); }, cell: function (f) { return F.clock(f.filled_at); }},
        {k: 'ev', h: 'Event', cls: 'od-ev', cell: function (f) { var l = f.label || {}; return esc(short(l.event_title || f.us_market_slug || '—', 30)); }},
        {k: 'side', h: 'Side', cell: function (f) { var l = f.label || {}; return esc(short((f.direction || '') + ' ' + (l.participant || f.holding_side || ''), 24)); }},
        {k: 'qty', h: 'Qty', cls: 'od-num', sort: function (f) { return num(f.qty); }, cell: function (f) { return nf(num(f.qty), 'no quantity'); }},
        {k: 'px', h: 'Price', cls: 'od-num', cell: function (f) { return or(price(num(f.price)), 'no price'); }},
        {k: 'fee', h: 'Fee', cls: 'od-num', cell: function (f) { return or(F.usd(num(f.fee_usd)), 'no fee recorded'); }},
        {k: 'gr', h: 'Gross', cls: 'od-num', cell: function (f) { return or(F.usd(num(f.gross_usd)), 'no gross recorded'); }},
        {k: 'src', h: 'Basis', cell: function (f) { return '<span title="' + esc(f.basis || '') + '">' + esc(f.event_source || '—') + '</span>'; }},
        {k: 'ag', h: 'Strategy', cell: function (f) { return esc(stratShort(f.strategy)); }}
      ];
      RERENDER['fills'] = renderBlotter;
      body = table('fills', fcols, fills, {sort: {k: 't', dir: 'desc'}, empty: esc(((sec(D.fills) || {}).why) || 'no simulated fill')});
    } else {
      body = BLOT.tape.length ? '<div class="od-scroll"><table class="od-t od-t-tight"><thead><tr><th>Seq</th><th>Committed</th><th>Kind</th><th class="od-num">Cash Δ</th><th class="od-num">Reserved Δ</th><th>Order</th><th>Fill</th></tr></thead><tbody>' +
        BLOT.tape.map(function (e) { return '<tr><td class="od-mono">' + esc(e.sequence) + '</td><td class="od-mono">' + esc(F.clock(e.committed_at)) + '</td><td>' + esc(e.kind || '—') + '</td><td class="od-num">' + esc(F.sgnUsd(num(e.cash_delta_usd)) || '—') + '</td><td class="od-num">' + esc(F.sgnUsd(num(e.reserved_delta_usd)) || '—') + '</td><td class="od-mono">' + esc(short(e.order_id || '—', 22)) + '</td><td class="od-mono">' + esc(short(e.fill_id || '—', 22)) + '</td></tr>'; }).join('') + '</tbody></table></div>'
        : '<div class="od-block"><span class="od-soft">No ledger entry committed since this page opened (stream ' + esc(BLOT.stream) + ').</span></div>';
    }
    frame('blotter', {
      title: 'Orders & fills · PAPER', kicker: 'simulated execution · live on committed ledger entries',
      state: S, src: 'paper/derek + paper/xavier · SSE paper/stream, else every 10 s', tools: tools, body: body,
      foot: 'States as recorded by the paper ledger (open: pending simulation, resting, partially filled, cancel pending; terminal: filled, canceled, expired, rejected). P&amp;L per order: ' + DNA + ' (positions carry it: see Capital). Bounded: the latest ' + oppLimit + ' ENTRY orders / fills and 100 management orders.'
    });
  }
  /* the SSE stream of committed ledger entries: a live trigger for the
     blotter (and a tape). Closed while hidden; reconnects with ?last=<seq> so
     no committed entry is skipped; after repeated errors falls back to polling. */
  var fallbackTimer = null;
  function refreshAfterLedger() {
    // the broker still enforces its per-endpoint minimum interval
    O.read(derekUrl(), {force: true}).then(function (r) { R.derek = r; renderBlotter(); renderOpp(); renderAgents(); });
    O.read('/api/command/paper/xavier', {force: true}).then(function (r) { R.xavier = r; renderBlotter(); renderCapital(); });
  }
  function openStream() {
    if (!window.EventSource) { BLOT.stream = 'POLLING'; BLOT.streamWhy = 'this browser has no EventSource'; startFallback(); return; }
    if (BLOT.es || document.hidden) { return; }
    var url = '/api/command/paper/stream' + (BLOT.lastSeq != null ? '?last=' + encodeURIComponent(BLOT.lastSeq) : '');
    var es;
    try { es = new EventSource(url, {withCredentials: true}); } catch (e) { BLOT.stream = 'POLLING'; BLOT.streamWhy = 'EventSource failed to open'; startFallback(); return; }
    BLOT.es = es; BLOT.stream = 'CONNECTING'; renderBlotter();
    es.addEventListener('snapshot', function (m) {
      BLOT.errors = 0; BLOT.stream = 'LIVE'; BLOT.streamWhy = 'snapshot received ' + F.clock(Date.now() / 1000);
      try { var j = JSON.parse(m.data); if (j.sequence != null) { BLOT.lastSeq = j.sequence; } } catch (e) { /* malformed frame: ignored */ }
      stopFallback(); renderBlotter();
    });
    es.addEventListener('ledger', function (m) {
      BLOT.errors = 0; BLOT.stream = 'LIVE';
      try {
        var j = JSON.parse(m.data), e = j.entry || {};
        BLOT.lastSeq = j.sequence != null ? j.sequence : BLOT.lastSeq;
        BLOT.tape.unshift({sequence: j.sequence, committed_at: j.committed_at || e.committed_at, kind: e.kind, cash_delta_usd: e.cash_delta_usd,
                           reserved_delta_usd: e.reserved_delta_usd, order_id: e.order_id, fill_id: e.fill_id});
        BLOT.tape = BLOT.tape.slice(0, 60);
      } catch (x) { /* malformed frame: ignored */ }
      refreshAfterLedger();
    });
    es.addEventListener('heartbeat', function () { BLOT.errors = 0; if (BLOT.stream !== 'LIVE') { BLOT.stream = 'LIVE'; renderBlotter(); } });
    es.addEventListener('unavailable', function (m) {
      var why = 'stream unavailable'; try { why = JSON.parse(m.data).why || why; } catch (e) { /* ignore */ }
      closeStream(); BLOT.stream = 'POLLING'; BLOT.streamWhy = why; startFallback(); renderBlotter();
    });
    es.onerror = function () {
      BLOT.errors += 1;
      if (BLOT.errors >= 4) { closeStream(); BLOT.stream = 'POLLING'; BLOT.streamWhy = 'the stream failed ' + BLOT.errors + ' times (sign-in or network); polling every 10 s'; startFallback(); }
      else { BLOT.stream = 'RECONNECTING'; BLOT.streamWhy = 'stream error ' + BLOT.errors + '; the browser reconnects'; }
      renderBlotter();
    };
  }
  function closeStream() { if (BLOT.es) { try { BLOT.es.close(); } catch (e) { /* ignore */ } BLOT.es = null; } }
  function startFallback() { if (!fallbackTimer) { fallbackTimer = setInterval(function () { if (!document.hidden) { refreshAfterLedger(); } }, 10000); } }
  function stopFallback() { if (fallbackTimer) { clearInterval(fallbackTimer); fallbackTimer = null; } }
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { if (BLOT.es) { closeStream(); BLOT.stream = 'PAUSED'; BLOT.streamWhy = 'tab hidden: the stream is closed and resumes from sequence ' + BLOT.lastSeq; } }
    else if (BLOT.stream === 'PAUSED' || BLOT.stream === 'CONNECTING') { openStream(); }
  });

  // ═════ 6. AGENT DESK ═════════════════════════════════════════════════
  function within24(t) { var e = epoch(t); return e != null && F.now() - e <= 86400; }
  function renderAgents() {
    var Fl = rs(R.floor), fl = Fl.data, D = rs(R.derek).data;
    var sum = sec(D && D.refusal_summary_24h), ords = (sec(D && D.orders) || {}).data || [], fills = (sec(D && D.fills) || {}).data || [];
    var X = rs(R.xavier).data, mg = (sec(X && X.standing_orders) || {}).data || [];
    // per-strategy 24 h counts from the full 24 h summary (not bounded)
    var per = {};
    ((sum && sum.data) || []).forEach(function (r) {
      var s = r.strategy || '(none)', p = per[s] || (per[s] = {n: 0, enter: 0, sw: 0, ec: 0, un: 0});
      var n = num(r.n) || 0;
      p.n += n;
      if (r.verdict === 'ENTER') { p.enter += n; } else { var c = TX.classify(r.reason).cls; if (c === 'SOFTWARE') { p.sw += n; } else if (c === 'ECONOMIC') { p.ec += n; } else { p.un += n; } }
    });
    function bounded(list, field, lim) {
      var n = list.filter(function (x) { return within24(x[field]); }).length;
      var full = list.length >= lim && list.length && within24(list[list.length - 1][field]);
      return {n: n, lb: full};
    }
    var tot = {n: 0, enter: 0, sw: 0, ec: 0, un: 0};
    Object.keys(per).forEach(function (s) { ['n', 'enter', 'sw', 'ec', 'un'].forEach(function (k) { tot[k] += per[s][k]; }); });
    var o24 = bounded(ords, 'created_at', oppLimit), f24 = bounded(fills, 'filled_at', oppLimit), m24 = bounded(mg, 'created_at', 100);
    function lb(x) { return F.int(x.n) + (x.lb ? '+' : ''); }
    var LBT = ' (+ = at least: the bounded list is full inside 24 h)';
    var body = '';
    if (!fl) { body += '<div class="od-block">' + dna(Fl.title || 'floor not read yet') + '</div>'; }
    else {
      var agents = fl.agents || [];
      var cols = [
        {k: 'a', h: 'Agent', sort: function (a) { return a.display_name || a.agent; }, cell: function (a) { return '<b>' + esc(a.display_name || a.agent) + '</b><small title="' + esc(a.role || '') + '">' + esc(short(a.role || '', 26)) + '</small>'; }},
        {k: 's', h: 'Status', sort: function (a) { return a.work_state || a.state; }, cell: function (a) {
          var ws = a.work_state || a.state || 'UNKNOWN', t = /STALE|NOT_DEPLOYED|BLOCKED|ERROR/.test(ws) ? 'bad' : /WAIT|PENDING|CHALLENG/.test(ws) ? 'warn' : /WORKING|REVIEW|EVALUAT/.test(ws) ? 'good' : 'dim';
          return pill(t, ws.replace(/_/g, ' '), (a.work_detail || '') + (a.work_state ? '' : ' (legacy state: work_state not served)')) + (a.work_since ? ' <small>' + agoAt(epoch(a.work_since)) + '</small>' : '');
        }},
        {k: 'w', h: 'Workload · focus', cls: 'od-reason od-wrap', cell: function (a) {
          var m = a.monitor || [], f = a.focus || a.last_output;
          return (m.length ? m.map(function (x) { return esc(x.label) + ' <b>' + (x.value == null ? na(x.why || 'not served') : esc(x.value)) + '</b>'; }).join(' · ') : na('no monitor served for this agent')) +
            (f ? '<small title="' + esc(f.summary || '') + '">' + esc(short(f.summary || f.kind || '', 70)) + '</small>' : ''); }}
      ];
      RERENDER['agents'] = renderAgents;
      body += table('agents', cols, agents, {sort: {k: 'a', dir: 'asc'}, empty: 'no agent on the floor read'});
    }
    // the PAPER entry funnel per strategy (Derek decides for every strategy; Xavier manages)
    var srows = Object.keys(per).map(function (s) { return {s: s, p: per[s]}; });
    var ocount = {}, fcount = {};
    ords.forEach(function (o) { if (within24(o.created_at)) { ocount[o.strategy] = (ocount[o.strategy] || 0) + 1; } });
    fills.forEach(function (f) { if (within24(f.filled_at)) { fcount[f.strategy] = (fcount[f.strategy] || 0) + 1; } });
    body += '<b class="od-sub">PAPER entry funnel by strategy · last 24 h · Derek decides, Xavier manages</b>';
    if (!sum) { body += '<div class="od-block">' + dna('the 24 h refusal summary was not read') + '</div>'; }
    else if (sum.status !== 'OK') { body += '<div class="od-block"><span class="od-soft">' + esc(sum.status + ': ' + (sum.why || '')) + '</span></div>'; }
    else {
      body += '<div class="od-scroll"><table class="od-t od-t-tight"><thead><tr><th>Strategy</th><th class="od-num" title="decisions recorded (received = evaluated: every received candidate is decided)">Eval</th><th class="od-num">ENTER</th><th class="od-num" title="' + esc('ENTRY orders created in 24 h' + LBT) + '">Orders</th><th class="od-num" title="' + esc('simulated fills in 24 h' + LBT) + '">Fills</th><th class="od-num" title="software refusals">SW</th><th class="od-num" title="economic refusals">ECON</th><th class="od-num" title="unclassified refusals">UNCL</th><th class="od-num" title="realized PAPER P&amp;L per strategy is not served by R29 (equity sleeves)">P&amp;L</th></tr></thead><tbody>' +
        srows.sort(function (a, b) { return b.p.n - a.p.n; }).map(function (x) {
          return '<tr><td title="' + esc(stratTitle(x.s)) + '">' + esc(stratShort(x.s)) + '</td><td class="od-num">' + F.int(x.p.n) + '</td><td class="od-num">' + F.int(x.p.enter) + '</td><td class="od-num">' + F.int(ocount[x.s] || 0) + (o24.lb ? '+' : '') + '</td><td class="od-num">' + F.int(fcount[x.s] || 0) + (f24.lb ? '+' : '') + '</td><td class="od-num">' + F.int(x.p.sw) + '</td><td class="od-num">' + F.int(x.p.ec) + '</td><td class="od-num">' + F.int(x.p.un) + '</td><td class="od-num">' + na('realized PAPER P&L per strategy is not served by R29') + '</td></tr>';
        }).join('') +
        '<tr class="od-total"><td>All strategies</td><td class="od-num">' + F.int(tot.n) + '</td><td class="od-num">' + F.int(tot.enter) + '</td><td class="od-num">' + lb(o24) + '</td><td class="od-num">' + lb(f24) + '</td><td class="od-num">' + F.int(tot.sw) + '</td><td class="od-num">' + F.int(tot.ec) + '</td><td class="od-num">' + F.int(tot.un) + '</td><td class="od-num">—</td></tr>' +
        '</tbody></table></div><div class="od-mini">Xavier: ' + lb(m24) + ' management orders in 24 h · ' + F.int(((sec(X && X.positions) || {}).data || []).length) + ' open PAPER positions managed.</div>';
    }
    frame('agents', {
      title: 'Agent desk', kicker: 'work state if served, else legacy state', state: Fl,
      src: 'GET /api/command/floor every 30 s · paper/derek 24 h summary', body: body,
      foot: 'Evaluated / ENTER / refusals: the full 24 h summary (paper_decisions). Orders / fills: from the bounded lists' + LBT + '.'
    });
  }

  // ═════ 7. LOST OPPORTUNITIES / REFUSALS ══════════════════════════════
  function renderRefusals() {
    var S = rs(R.derek), D = S.data, sum = sec(D && D.refusal_summary_24h), L = rs(R.lol).data;
    var opp = ((sec(D && D.opportunities) || {}).data) || [];
    var body;
    if (!D) { body = '<div class="od-block">' + dna(S.title || 'decisions not read yet') + '</div>'; }
    else if (!sum || sum.status !== 'OK') { body = '<div class="od-block">' + (sum && sum.status === 'EMPTY' ? '<span class="od-soft">' + esc(sum.why) + '</span>' : dna((sum && sum.status + ': ' + sum.why) || '24 h refusal summary absent')) + '</div>'; }
    else {
      var codes = {}, total = 0;
      sum.data.forEach(function (r) {
        if (r.verdict === 'ENTER') { return; }
        var c = TX.classify(r.reason), n = num(r.n) || 0, x = codes[c.code] || (codes[c.code] = {c: c, n: 0, strats: {}, markets: {}, sports: {}});
        x.n += n; total += n; x.strats[r.strategy || '(none)'] = (x.strats[r.strategy || '(none)'] || 0) + n;
      });
      opp.forEach(function (r) {
        if (r.verdict === 'ENTER' || !r.refusal) { return; }
        var x = codes[TX.normalize(r.refusal)]; if (!x) { return; }
        x.markets[r.us_market_slug] = 1; x.sports[sportOfRow(r)] = 1;
      });
      var hind = {};
      ((L && L.data && L.data.summary && L.data.summary.by_refusal_reason) || []).forEach(function (h) { hind[TX.normalize(h.refusal)] = h; });
      function section(cls, title, desc) {
        var rows = Object.keys(codes).map(function (k) { return codes[k]; }).filter(function (x) { return x.c.cls === cls; }).sort(function (a, b) { return b.n - a.n; });
        var n = rows.reduce(function (a, x) { return a + x.n; }, 0);
        var h = '<div class="od-rsec od-rsec-' + cls.toLowerCase() + '"><div class="od-rsec-h"><b>' + title + '</b><span>' + F.int(n) + ' decisions · ' + (total ? pct(n / total, 1) : '—') + ' of refused</span><small>' + desc + '</small></div>';
        if (!rows.length) { return h + '<div class="od-soft od-block">none in the last 24 h</div></div>'; }
        return h + '<div class="od-scroll"><table class="od-t od-t-tight"><thead><tr><th>Code</th><th>Stage</th><th class="od-num" title="decisions · share of all refused decisions">Decisions</th><th title="distinct markets and sports among the latest ' + opp.length + ' decisions">Seen in*</th><th>Strategies</th><th title="settled hindsight from the lost-opportunity ledger (RESEARCH): good / false / unknowable refusals">Hindsight</th></tr></thead><tbody>' +
          rows.map(function (x) {
            var hh = hind[x.c.code];
            return '<tr><td class="od-reason" title="' + esc(x.c.code + ' — ' + x.c.basis + (x.c.source ? ' · ' + x.c.source : '')) + '"><span class="od-mono">' + esc(x.c.code) + '</span></td><td>' + esc(x.c.stage.replace(/^\d_/, '')) + '</td><td class="od-num">' + F.int(x.n) + '<small>' + (total ? pct(x.n / total, 1) : '—') + '</small></td><td>' + F.int(Object.keys(x.markets).length) + ' mkt' + (Object.keys(x.sports).length ? ' · ' + esc(Object.keys(x.sports).join(', ')) : '') + '</td><td class="od-wrap">' + esc(Object.keys(x.strats).map(stratShort).join(', ')) + '</td><td>' +
              (hh ? '<span title="settled refusals of this code classified by the lost-opportunity ledger">G ' + F.int(num(hh.GOOD_REFUSAL) || 0) + ' · F ' + F.int(num(hh.FALSE_REFUSAL) || 0) + ' · U ' + F.int(num(hh.UNKNOWABLE) || 0) + '</span>' : '<span class="od-soft">—</span>') + '</td></tr>';
          }).join('') + '</tbody></table></div></div>';
      }
      body = '<div class="od-rgrid">' +
        section('SOFTWARE', 'SOFTWARE LOSSES', 'an input, capability or record was missing: engineering / data can clear it') +
        section('ECONOMIC', 'ECONOMIC LOSSES', 'decided on the evidence: no edge after fees, depth, a risk rail, measured staleness, stated terms, owner policy') +
        '</div>' + section('UNCLASSIFIED', 'UNCLASSIFIED', 'not in the R29 refusal table: shown on its own, never assumed economic') + collectionLosses() + trend(L);
    }
    frame('refusals', {
      title: 'Lost opportunities · refusals', kicker: 'last 24 h PAPER decisions · software and economic kept apart',
      state: S, src: 'paper/derek refusal_summary_24h · lost-opportunities every 120 s', body: body,
      foot: 'Counts are decisions (one per evaluated market side per cycle), from the full 24 h summary. *Markets / sports: among the latest ' + oppLimit + ' decisions only. Trend per code: ' + DNA + ' (one 24 h window in R29); the 30-day hindsight trend is the lost-opportunity ledger. Classification: ops-taxonomy.js (R29 ' + esc(TX.sourceSha) + ').'
    });
  }
  /* events lost between collection stages today (coverage): the stage is
     known, the per-row code is not in this read, so the class is not guessed */
  function collectionLosses() {
    var d = rs(O.state.coverage).data, today = d && (d.days || [])[0];
    if (!today) { return ''; }
    var pairs = [['provider_events', 'normalized_events', 'probability / freshness (stages 1–2)'], ['normalized_events', 'venue_discovered', 'venue discovery (identity)'], ['venue_discovered', 'mapped_events', 'mapping (identity)'], ['mapped_events', 'evaluated_events', 'settlement / valuation (to a sealed valuation)']];
    var rows = [];
    (today.leagues || []).forEach(function (L) {
      pairs.forEach(function (p) {
        var a = num(L[p[0]]), b = num(L[p[1]]);
        if (a != null && b != null && a - b > 0) { rows.push({league: L.league_name || L.league, sport: O.sportOf(L.league, L.sport_family), stage: p[2], lost: a - b, from: a}); }
      });
    });
    ((d.league_status || {}).statuses || []).forEach(function (s) {
      if (s.status && s.status !== 'HEALTHY' && /NOT_REQUESTED|BUDGET/.test(s.reason || '')) {
        rows.push({league: s.league_name || s.league, sport: O.sportOf(s.league, s.sport_family), stage: 'collector did not request it (SOFTWARE: collector capacity)', lost: num((s.counts || {}).venue_catalogue_events), from: null, reason: s.reason, sw: true});
      }
    });
    rows.sort(function (x, y) { return (y.lost || 0) - (x.lost || 0); });
    return '<div class="od-rsec od-rsec-stage"><div class="od-rsec-h"><b>COLLECTION-STAGE LOSSES · TODAY</b><span>events lost before a PAPER decision</span><small>from coverage stage counts; the per-row refusal code is not in this read, so these carry no class unless the reason states it</small></div>' +
      (rows.length ? '<div class="od-scroll"><table class="od-t od-t-tight"><thead><tr><th>League</th><th>Sport</th><th>Lost at</th><th class="od-num">Events lost</th><th class="od-num">Of</th></tr></thead><tbody>' +
        rows.slice(0, 14).map(function (r) { return '<tr><td>' + esc(r.league) + '</td><td>' + esc(r.sport) + '</td><td title="' + esc(r.reason || '') + '">' + (r.sw ? clsChip('SOFTWARE') + ' ' : '') + esc(r.stage) + '</td><td class="od-num">' + (r.lost == null ? na('venue catalogue count not served') : F.int(r.lost) + (r.sw ? ' <small>venue-listed</small>' : '')) + '</td><td class="od-num">' + (r.from == null ? '—' : F.int(r.from)) + '</td></tr>'; }).join('') + '</tbody></table></div>'
        : '<div class="od-soft od-block">no collection-stage loss recorded today</div>') + '</div>';
  }
  function trend(L) {
    var t = L && L.data && L.data.summary && L.data.summary.trend_30d;
    if (!L) { return '<div class="od-mini">Hindsight trend: ' + dna((rs(R.lol).title) || 'lost-opportunity ledger not read') + '</div>'; }
    if (!t || !t.length) { return '<div class="od-mini">Hindsight trend: ' + dna((L.why) || 'no settled refusal classified in 30 days') + '</div>'; }
    var max = Math.max.apply(null, t.map(function (d) { return (d.good || 0) + (d.false_refusal || 0) + (d.unknowable || 0); }).concat([1]));
    return '<div class="od-trend"><b class="od-sub">Refusal hindsight · settled · 30 days (RESEARCH, lost-opportunity ledger)</b><div class="od-bars">' +
      t.map(function (d) {
        var g = d.good || 0, f = d.false_refusal || 0, u = d.unknowable || 0;
        return '<span class="od-tb" title="' + esc(d.day + ': good ' + g + ' · false ' + f + ' · unknowable ' + u + (d.missed_hypothetical_usd != null ? ' · missed (hypothetical) ' + d.missed_hypothetical_usd : '')) + '"><i class="g" style="height:' + (100 * g / max) + '%"></i><i class="f" style="height:' + (100 * f / max) + '%"></i><i class="u" style="height:' + (100 * u / max) + '%"></i></span>';
      }).join('') + '</div><div class="od-legend"><i class="g"></i>good refusal <i class="f"></i>false refusal <i class="u"></i>unknowable</div></div>';
  }

  // ═════ 8. PINNAPI HEALTH ══════════════════════════════════════════════
  function renderPinn() {
    var C = rs(O.state.coverage), d = C.data, body;
    var cap = O.meteredCap;
    var knownCap = '<div class="od-known"><b>KNOWN LIMITATION · deployed SHA ' + esc(cap.sha) + '</b> candidate discovery requests at most <b>' + cap.value + '</b> metered sport keys per cycle (' + esc(cap.constant) + ', ' + esc(cap.source) + '); competitions beyond it are budget-dropped. Fix in progress.</div>';
    if (!d) { body = '<div class="od-block">' + dna(C.title || 'coverage not read yet') + '</div>' + knownCap; }
    else {
      var sup = d.provider_supplement || null, col = (d.league_status || {}).collector || null, census = sup && sup.census;
      var statuses = (d.league_status || {}).statuses || [];
      var covered = statuses.filter(function (s) { return num((s.counts || {}).provider_events) > 0; }).map(function (s) { return s.league_name || s.league; });
      var rows = [
        ['Connection state', sup ? pill(sup.status === 'OK' ? 'good' : 'bad', sup.status, sup.why || '') : dna('no PinnAPI supplement in the coverage read (heartbeat not recent or not recorded)')],
        ['Last message (heartbeat age)', sup && fin(num(sup.age_s)) ? '<b>' + F.age(num(sup.age_s)) + '</b> <small>at the coverage read</small>' : dna('heartbeat age not served')],
        ['Data age', sup && fin(num(sup.age_s)) && fin(epoch(d.as_of)) ? agoAt(epoch(d.as_of) - num(sup.age_s)) : dna('not derivable without a heartbeat age')],
        ['Events matched (census)', census && fin(num(census.matched_events)) ? '<b>' + F.int(num(census.matched_events)) + '</b> <small>provider events matched to venue contracts</small>' : dna('census not served')],
        ['Venue contracts by feed state', census && census.states ? Object.keys(census.states).map(function (k) { return esc(k.replace(/_/g, ' ').toLowerCase()) + ' <b>' + F.int(num(census.states[k])) + '</b>'; }).join(' · ') + (census.total_contracts != null ? ' <small>of ' + F.int(num(census.total_contracts)) + '</small>' : '') : dna('census not served')],
        ['Leagues with provider events today', covered.length ? esc(covered.join(', ')) : dna('no league counted a provider event today')],
        ['Collector: requested this cycle', col ? esc((col.requested || []).join(', ') || 'none') + (col.budget != null ? ' <small>budget ' + esc(col.budget) + '</small>' : '') + (col.at ? ' · ' + agoAt(epoch(col.at)) : '') : dna('collector cycle record not served')],
        ['Collector: budget drops', col ? ((col.budget_dropped || []).length ? '<b class="od-neg">' + esc(col.budget_dropped.join(', ')) + '</b>' : 'none this cycle') : dna('collector cycle record not served')],
        ['Quote updates / min', dna('not served by any R29 command read (the feed state route needs the admin token)')],
        ['Stale events', dna('not served by any R29 command read')],
        ['Parser errors', dna('not served by any R29 command read')],
        ['Provider errors', dna('not served by any R29 command read')],
        ['Credit consumption', dna('not served by any R29 command read; the metered discovery budget is the collector budget above')]
      ];
      body = '<dl class="od-kv">' + rows.map(function (r) { return '<dt>' + esc(r[0]) + '</dt><dd>' + r[1] + '</dd>'; }).join('') + '</dl>' + knownCap;
    }
    frame('pinnapi', {title: 'PinnAPI health', kicker: 'provider-side telemetry', state: C, src: 'coverage provider_supplement + collector · 120 s', body: body});
  }

  // ═════ 9. CAPITAL / EXPOSURE ═════════════════════════════════════════
  function renderCapital() {
    var eq = O.state.equity, L = eq && eq.live, P = L && L.paper, X = rs(R.xavier), xd = X.data, body;
    var st = !eq ? {tone: 'dim', text: 'READING'} : eq.status === 'OK' ? {tone: P && P.status === 'OK' ? 'good' : 'warn', text: P ? 'PAPER ' + P.status : 'NO PAPER', title: P && P.why || '', at: eq.okAt ? eq.okAt / 1000 : null} : {tone: 'warn', text: eq.status || 'ERROR', title: 'equity/live read: ' + (eq.status || '')};
    if (!P) { body = '<div class="od-block">' + dna(eq && eq.status && eq.status !== 'OK' ? 'equity/live: ' + eq.status : 'PAPER equity not read yet') + '</div>'; }
    else {
      var m = P.marks_as_of || {}, lim = num(m.stale_mark_after_s) || num(P.stale_after_s) || 300;
      var markAge = num(m.newest_age_s), fresh = P.status === 'OK' && markAge != null && markAge <= lim;
      var op = P.open_positions || {}, ex = P.exposure || {};
      function tile(k, v, sub, t) { return '<div class="od-tile"' + (t ? ' data-tone="' + t + '"' : '') + '><span>' + k + '</span><b>' + v + '</b>' + (sub ? '<small>' + sub + '</small>' : '') + '</div>'; }
      body = '<div class="od-tiles">' +
        tile('Equity', or(F.usd(num(P.equity_usd), 0), 'equity not served'), esc(P.equity_treatment || '')) +
        tile('Cash', or(F.usd(num(P.cash_usd), 0), 'cash not served')) +
        tile('Available', or(F.usd(num(P.available_usd), 0), 'available not served'), 'reserved ' + (F.usd(num(P.reserved_usd), 0) || '—')) +
        tile('Deployed', or(F.usd(num(ex.cost_basis_usd), 0), 'cost basis not served'), 'cost basis of open positions') +
        tile('Open positions', or(F.int(num(op.count)), 'count not served'), (op.marked != null ? op.marked + ' marked · ' + op.unmarked + ' unmarked · ' + op.stale_marks + ' stale' : '')) +
        tile('Realized P&amp;L', or(F.sgnUsd(num(P.realized_pnl_usd)), 'realized not served'), 'PAPER', num(P.realized_pnl_usd) > 0 ? 'good' : num(P.realized_pnl_usd) < 0 ? 'bad' : null) +
        tile('Unrealized P&amp;L', fresh ? or(F.sgnUsd(num(P.unrealized_pnl_usd)), 'unrealized not served') : '<span class="od-stale" title="' + esc('marks ' + (markAge == null ? 'age unknown' : F.age(markAge) + ' old') + ' (stale after ' + F.age(lim) + ')' + (P.why ? ' · ' + P.why : '')) + '">STALE</span>', fresh ? 'newest mark ' + F.age(markAge) + ' old' : 'shown only on fresh marks') +
        tile('Day change', or(F.sgnUsd(num((P.day_change || {}).usd)), 'day change not served'), (P.day_change && fin(num(P.day_change.pct)) ? num(P.day_change.pct).toFixed(2) + '%' : '')) +
        '</div>';
      // exposure from the managed positions (labels carry sport / event)
      var pos = (sec(xd && xd.positions) || {}).data;
      if (!pos) { body += '<div class="od-mini">Exposure by sport / strategy / event: ' + dna(X.title || 'paper/xavier positions not read') + '</div>'; }
      else if (!pos.length) { body += '<div class="od-mini od-soft">No open PAPER position.</div>'; }
      else {
        function cost(p) { var c = num(p.cost_basis_usd); return c != null ? c : num(p.acquisition_cost_usd); }
        function group(fn) {
          var g = {};
          pos.forEach(function (p) { var k = fn(p), c = cost(p); if (c == null) { return; } var x = g[k] || (g[k] = {k: k, usd: 0, n: 0}); x.usd += c; x.n++; });
          return Object.keys(g).map(function (k) { return g[k]; }).sort(function (a, b) { return b.usd - a.usd; });
        }
        var totalCost = pos.reduce(function (a, p) { return a + (cost(p) || 0); }, 0);
        function bars(title, list) {
          return '<div class="od-expo"><b class="od-sub">' + title + '</b>' + list.slice(0, 6).map(function (x) {
            return '<div class="od-xr"><span title="' + esc(x.k) + '">' + esc(short(x.k, 26)) + '</span><i style="--w:' + (totalCost > 0 ? (100 * x.usd / totalCost).toFixed(1) : 0) + '%"></i><b>' + F.usd(x.usd, 0) + '</b><small>' + x.n + '</small></div>';
          }).join('') + '</div>';
        }
        body += '<div class="od-expo-grid">' + bars('By sport', group(sportOfRow)) + bars('By strategy', group(function (p) { return stratShort(p.strategy); })) + bars('By event', group(function (p) { return (p.label || {}).event_title || p.us_market_slug; })) + '</div>';
        var top = pos.slice().sort(function (a, b) { return (cost(b) || 0) - (cost(a) || 0); }).slice(0, 6);
        body += '<b class="od-sub">Largest exposures</b><div class="od-scroll"><table class="od-t od-t-tight"><thead><tr><th>Position</th><th class="od-num">Open qty</th><th class="od-num">Cost basis</th><th class="od-num">Unrealized</th><th>Strategy</th></tr></thead><tbody>' +
          top.map(function (p) { var l = p.label || {}; var u = num(p.unrealized_pnl_usd); return '<tr><td title="' + esc(p.position_key || '') + '">' + esc(short((l.participant || p.holding_side || '') + ' · ' + (l.event_title || p.us_market_slug || ''), 40)) + '</td><td class="od-num">' + nf(num(p.open_qty), 'no open qty') + '</td><td class="od-num">' + or(F.usd(cost(p), 0), 'no cost basis') + '</td><td class="od-num">' + (!fresh ? '<span class="od-stale">STALE</span>' : u == null ? na('position not marked') : '<span class="' + (u >= 0 ? 'od-pos' : 'od-neg') + '">' + F.sgnUsd(u) + '</span>') + '</td><td>' + esc(stratShort(p.strategy)) + '</td></tr>'; }).join('') + '</tbody></table></div>';
      }
    }
    var SL = L && L.small_live_bettor;
    frame('capital', {
      title: 'Capital · exposure', kicker: 'PAPER book · fictional USD', state: st,
      src: 'equity/live (one shared loop, 10 s) · paper/xavier 15 s', body: body,
      foot: 'SMALL LIVE: ' + esc(SL ? SL.status + (SL.why ? ' — ' + SL.why : '') : 'not served') + '. Legacy mirror shown in the header, never summed with PAPER. Unrealized P&amp;L shows only while the newest mark is inside its stale bound.'
    });
  }

  // ═════ 10. INCIDENTS ═════════════════════════════════════════════════
  function renderIncidents() {
    var inc = O.deriveIncidents(O.state), K = O.known, ins = O.incidentInputs(O.state);
    var SEVT = {CRITICAL: 'bad', WARNING: 'warn', INFO: 'dim'};
    function row(x, known) {
      return '<tr class="' + (known ? 'od-known-row' : '') + '"><td>' + pill(SEVT[x.severity] || 'dim', x.severity) + '</td><td class="od-wrap"><b>' + esc(x.title) + '</b><small title="' + esc(x.detail || '') + '">' + esc(short(x.detail || '', 120)) + '</small>' + (known ? '<small class="od-klabel">' + esc(x.label) + '</small>' : '') + '</td>' +
        '<td class="od-mono" title="' + esc(x.first_seen_basis || '') + '">' + (known ? esc(x.first_seen) : (fin(x.first_seen) ? F.stamp(x.first_seen).slice(5, 16) : '—')) + '</td><td class="od-mono">' + (known ? 'ongoing' : (fin(x.last_seen) ? F.stamp(x.last_seen).slice(5, 16) : '—')) + '</td>' +
        '<td class="od-wrap">' + esc([].concat(x.sports || [], x.components || []).join(', ') || '—') + '</td></tr>';
    }
    var body = '<div class="od-scroll"><table class="od-t od-t-tight"><thead><tr><th>Sev</th><th>Incident</th><th>First seen</th><th>Last seen</th><th>Affected</th></tr></thead><tbody>' +
      '<tr class="od-group"><td colspan="5">LIVE · derived from recorded state</td></tr>' +
      (inc.length ? inc.map(function (x) { return row(x, false); }).join('') : '<tr><td colspan="5" class="od-soft">' + (ins.read.length ? 'No live-derived incident in the reads that succeeded (' + esc(ins.read.join(', ')) + ')' + (ins.missing.length ? '; not read: ' + esc(ins.missing.join(', ')) : '') + '.' : DNA + ': no incident input could be read.') + '</td></tr>') +
      '<tr class="od-group"><td colspan="5">KNOWN ISSUES · deployed SHA ' + esc(O.deployedSha) + ' (static, labelled)</td></tr>' +
      K.map(function (x) { return row(x, true); }).join('') + '</tbody></table></div>';
    var crit = inc.filter(function (x) { return x.severity === 'CRITICAL'; }).length;
    var none = ins.read.length ? (ins.missing.length ? 'NONE IN ' + ins.read.length + '/5 READS' : 'NONE LIVE') : 'UNKNOWN · NO READ';
    frame('incidents', {
      title: 'Incidents · system status', kicker: 'live-derived kept apart from known issues',
      state: {tone: crit ? 'bad' : inc.length ? 'warn' : ins.read.length === 5 ? 'good' : 'warn', text: crit ? crit + ' CRITICAL' : inc.length ? inc.length + ' WARNING' : none,
              title: 'derived from the release, coverage, overview, equity and floor reads; read: ' + (ins.read.join(', ') || 'none') + (ins.missing.length ? '; not read: ' + ins.missing.join(', ') : '')},
      src: 'release · coverage · overview · equity · floor', body: body
    });
  }

  // ── wiring ────────────────────────────────────────────────────────
  O.on(function (s) {
    if (s.coverage !== renderFunnel.last) { renderFunnel.last = s.coverage; renderFunnel(); renderMatrix(); renderPinn(); renderRefusals(); }
    if (s.equity !== renderCapital.last) { renderCapital.last = s.equity; renderCapital(); }
    renderIncidents();
  });
  startDerek();
  O.poll('/api/command/paper/xavier', 15000, function (r) { R.xavier = r; renderBlotter(); renderCapital(); renderAgents(); });
  O.poll('/api/command/floor', 30000, function (r) { R.floor = r; O.set('floor', r); renderAgents(); });
  O.poll('/api/command/profitability/lost-opportunities', 120000, function (r) { R.lol = r; renderRefusals(); });
  openStream();
  [renderFunnel, renderMatrix, renderOpp, renderBlotter, renderAgents, renderRefusals, renderPinn, renderCapital, renderIncidents].forEach(function (f) { try { f(); } catch (e) { /* a panel that throws shows nothing new; others continue */ } });
  window.BTOpsDesk = {version: 'COMMAND_OPS_DESK_R1', econ: econ, health: health, sportRows: sportRows, dayTotals: dayTotals, stages: STAGES, healthRule: HEALTH_RULE};
})();
