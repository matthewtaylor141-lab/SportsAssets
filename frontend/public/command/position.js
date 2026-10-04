/* BETTORTOKEN COMMAND · POSITION ROOMS.
 *
 * "Management must be able to open EVERY active position and understand the
 * entire correlated economic position from one screen."
 *
 * TWO READS, BOTH GET, BOTH SAME-ORIGIN, BOTH AUTHENTICATED BY THE HttpOnly
 * COMMAND COOKIE (this script never sees a credential):
 *   /api/command/positions/rooms?book=PAPER|ACTUAL     the list
 *   /api/command/positions/room/{group_key}            one room
 * The only query string ever sent is built from a fixed allowlist; the room
 * key is validated against the server's own key grammar before it is used.
 *
 * NOTHING IS INVENTED HERE. Every figure is the server's; a figure the read
 * does not carry renders as UNAVAILABLE with the server's reason, a stale one
 * as STALE with its age. The only arithmetic is presentation (a countdown
 * from a real timestamp, the scale of a price track). PAPER and ACTUAL are
 * never shown summed, and ACTUAL venues are separate sections. A RESTING
 * order is drawn hollow and labelled -- it is not a fill, and a resting
 * order is NOT protection until it fills. ONLY FILLED QUANTITY IS
 * PROTECTION: the protection strip shows position qty, unprotected qty,
 * standing order qty, filled protection qty, the CONDITIONAL floor (IF
 * FILLED -- never the realized floor), the realized floor, the current
 * executable exit and the current worst-case exposure, every one the
 * server's (order_state_truth.protection_summary). The live pulse appears
 * only when the server says the game state is genuinely live and fresh.
 */
(function () {
  'use strict';

  var LIST = '/api/command/positions/rooms';
  var ROOM = '/api/command/positions/room/';
  var BOOKS = ['PAPER', 'ACTUAL'];
  var KEY_RE = /^(PAPER|ACTUAL-POLYMARKET|ACTUAL-KALSHI):(EVT|MKT):[a-z0-9][a-z0-9-]{0,200}$/;
  var POLL_MS = 15000;
  var S = {mode: null, book: 'PAPER', key: null, data: null, timer: null,
           tick: null, lastSig: null};

  // ── formatting (presentation only) ─────────────────────────────────
  function has(v) { return v !== null && v !== undefined && v !== ''; }
  function num(v) { return typeof v === 'number' && isFinite(v) ? v : null; }
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  function na(why) {
    return '<span class="pr-na" title="' + esc(why || 'not carried by the read') + '">UNAVAILABLE</span>';
  }
  function usd(v, why) {
    var n = num(v);
    if (n === null) { return na(why); }
    var s = Math.abs(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    return '<span class="money">' + (n < 0 ? '−$' : '$') + s + '</span>';
  }
  function sUsd(v, why) {
    var n = num(v);
    if (n === null) { return na(why); }
    var s = Math.abs(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    var cls = n > 0 ? 'positive' : n < 0 ? 'negative' : '';
    return '<span class="money ' + cls + '">' + (n > 0 ? '+$' : n < 0 ? '−$' : '$') + s + '</span>';
  }
  function cents(v, why) {
    var n = num(v);
    if (n === null) { return na(why); }
    var c = Math.round(n * 1000) / 10;
    return '<span class="money">' + (c % 1 === 0 ? c.toFixed(0) : c.toFixed(1)) + '¢</span>';
  }
  function centsTxt(v) {
    var n = num(v);
    if (n === null) { return '—'; }
    var c = Math.round(n * 1000) / 10;
    return (c % 1 === 0 ? c.toFixed(0) : c.toFixed(1)) + '¢';
  }
  function dollars(v) {
    var n = num(v);
    return n === null ? '—' : (n < 0 ? '−$' : '$') + Math.abs(n).toFixed(2);
  }
  function qty(v) {
    var n = num(v);
    return n === null ? na() : '<span class="money">' + n.toLocaleString('en-US', {maximumFractionDigits: 2}) + '</span>';
  }
  function qtyTxt(v) {
    var n = num(v);
    return n === null ? '—' : n.toLocaleString('en-US', {maximumFractionDigits: 2});
  }
  function pct(v) {
    var n = num(v);
    return n === null ? na() : '<span class="money">' + (n * 100).toFixed(1) + '%</span>';
  }
  function age(s) {
    var n = num(s);
    if (n === null) { return '—'; }
    if (n < 60) { return Math.round(n) + 's'; }
    if (n < 3600) { return Math.floor(n / 60) + 'm ' + Math.round(n % 60) + 's'; }
    if (n < 86400) { return Math.floor(n / 3600) + 'h ' + Math.floor(n % 3600 / 60) + 'm'; }
    return Math.floor(n / 86400) + 'd ' + Math.floor(n % 86400 / 3600) + 'h';
  }
  function when(iso) {
    var t = Date.parse(iso);
    if (!isFinite(t)) { return '—'; }
    return new Date(t).toISOString().replace('T', ' ').slice(0, 19) + 'Z';
  }
  function since(iso) {
    var t = Date.parse(iso);
    return isFinite(t) ? Math.max(0, (Date.now() - t) / 1000) : null;
  }
  function human(code) {
    return String(code || '').replace(/_/g, ' ').toLowerCase();
  }
  // an order's ROLE is its purpose, never its effect: a protective order is
  // protection only for what it has FILLED
  var ROLE = {STANDING_PROTECTION: 'protective sale order', HEDGE: 'hedge order'};
  function roleLabel(r) { return ROLE[r] || human(r); }
  function prov(src, at) {
    return esc((src || 'source not stated') + (at ? ' · ' + when(at) : ''));
  }
  function chip(text, cls, title) {
    return '<span class="pill ' + (cls || '') + '"' + (title ? ' title="' + esc(title) + '"' : '') + '>' + text + '</span>';
  }
  function fresh(f, a, src) {
    if (!f || f === 'UNAVAILABLE') { return chip('NO DATA', 'pr-chip-na', src || ''); }
    var cls = f === 'FRESH' ? 'green' : 'amber pr-stale';
    return chip((f === 'FRESH' ? 'FRESH ' : 'STALE ') + age(a), cls, src || '');
  }
  function label(o) { return o && o.instrument ? (o.instrument.label || o.instrument.slug) : '—'; }

  // ── STATE GLYPHS: shape + colour + label, each unmistakable ─────────
  var STATE = {
    PROPOSED: {t: 'PROPOSED', d: 'decided, not at the venue'},
    SUBMITTED: {t: 'SUBMITTED', d: 'sent, not yet acknowledged'},
    UNKNOWN: {t: 'UNKNOWN', d: 'state unknown: never treated as filled'},
    RESTING: {t: 'RESTING', d: 'on the book, nothing filled: NOT protection until it fills'},
    PARTIAL: {t: 'PARTIAL', d: 'part filled: only the filled part counts, the remainder rests'},
    FILLED: {t: 'FILLED', d: 'completely filled'},
    CANCEL_PENDING: {t: 'CANCEL PENDING', d: 'can still fill until confirmed'},
    CANCELLED: {t: 'CANCELLED', d: 'unfilled remainder gone'},
    EXPIRED: {t: 'EXPIRED', d: 'expired'},
    REJECTED: {t: 'REJECTED', d: 'refused'},
    EXCLUDED: {t: 'EXCLUDED', d: 'never sent'}
  };
  var CANON = ['PROPOSED', 'SUBMITTED', 'RESTING', 'PARTIAL', 'FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED', 'UNKNOWN'];
  function stateBadge(st, raw, sub) {
    var k = STATE[st] ? st : 'UNKNOWN';
    return '<span class="pr-st st-' + k + '" title="' + esc((STATE[k] || {}).d + (sub ? ' · ' + human(sub) : '') + (raw ? ' · recorded state ' + raw : '')) + '">' +
      '<i class="pr-glyph" aria-hidden="true"></i>' + esc((STATE[k] || {t: st}).t) + '</span>' +
      (sub === 'CANCEL_PENDING' ? '<span class="pr-st st-CANCEL_PENDING pr-sub-st" title="cancel requested, not confirmed: it can still fill"><i class="pr-glyph" aria-hidden="true"></i>CANCEL PENDING</span>' : '');
  }

  // ── transport ──────────────────────────────────────────────────────
  function get(path) {
    if (path.indexOf('/api/command/positions/') !== 0 || path.indexOf('..') >= 0) {
      return Promise.reject(new Error('refused path'));
    }
    return fetch(path, {method: 'GET', credentials: 'same-origin', cache: 'no-store',
                        headers: {Accept: 'application/json'}})
      .then(function (r) {
        if (r.status === 401 || r.status === 403) { var e = new Error('AUTH'); e.auth = true; throw e; }
        if (r.status === 404) { var n = new Error('NOT_FOUND'); n.status = 404; throw n; }
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (j) {
            var d = (j && j.detail) || {};
            var x = new Error('HTTP ' + r.status + (d.reason ? ' · ' + d.reason : ''));
            x.status = r.status; throw x;
          });
        }
        return r.json();
      });
  }
  function roomHref(key) {
    var onCmd = location.pathname.indexOf('/command/') === 0;
    return (onCmd ? 'position.html' : '/position') + '?g=' + encodeURIComponent(key);
  }
  function listHref(book) {
    var onCmd = location.pathname.indexOf('/command/') === 0;
    return (onCmd ? 'position.html' : '/positions') + (book && book !== 'PAPER' ? '?book=' + book : '');
  }

  function setState(msg, kind) {
    var el = document.getElementById('pr-state');
    if (!msg) { el.hidden = true; el.innerHTML = ''; return; }
    el.hidden = false;
    el.className = 'pr-state' + (kind ? ' pr-state-' + kind : '');
    el.innerHTML = msg;
  }

  function load(quiet) {
    var path = S.mode === 'room' ? ROOM + encodeURIComponent(S.key)
      : LIST + '?book=' + (BOOKS.indexOf(S.book) >= 0 ? S.book : 'PAPER');
    if (!quiet) { setState('Reading…'); }
    return get(path).then(function (j) {
      var sig = JSON.stringify(j).length + ':' + (j.as_of || '');
      var changed = sig !== S.lastSig;
      S.lastSig = sig;
      S.data = j;
      setState(null);
      document.getElementById('pr-asof').textContent = 'as of ' + when(j.as_of);
      if (changed || !quiet) { render(); }
    }).catch(function (e) {
      if (e.auth) {
        setState('SIGN-IN REQUIRED: your COMMAND session is missing or has expired. No figure is shown in the meantime. <a href="/">Sign in to COMMAND →</a>', 'warn');
      } else if (e.status === 404 && S.mode === 'room') {
        setState('NO SUCH POSITION ROOM: nothing of that book currently resolves to <code>' + esc(S.key) +
                 '</code>. It may have settled or closed. <a href="' + listHref() + '">All positions →</a>', 'warn');
      } else if (e.status === 404) {
        setState('NOT YET RELEASED: the serving API does not have the position rooms yet. They ship with the next API release.', 'warn');
      } else {
        setState('UNAVAILABLE: ' + esc(e.message) + '. No data is shown rather than stale or empty figures.', 'bad');
      }
      if (!S.data) { document.getElementById('pr-view').innerHTML = ''; }
    });
  }

  function render() {
    var v = document.getElementById('pr-view');
    v.innerHTML = S.mode === 'room' ? renderRoom(S.data) : renderList(S.data);
    tick();
  }

  // live countdowns from real timestamps (text only; no animation)
  function tick() {
    var els = document.querySelectorAll('[data-due]');
    for (var i = 0; i < els.length; i++) {
      var t = Date.parse(els[i].getAttribute('data-due'));
      if (!isFinite(t)) { els[i].textContent = '—'; continue; }
      var d = (t - Date.now()) / 1000;
      els[i].textContent = d >= 0 ? 'in ' + age(d) : 'overdue by ' + age(-d);
      els[i].classList.toggle('pr-overdue', d < 0);
    }
    var ages = document.querySelectorAll('[data-age-from]');
    for (var k = 0; k < ages.length; k++) {
      var s = since(ages[k].getAttribute('data-age-from'));
      if (s !== null) { ages[k].textContent = age(s) + ' ago'; }
    }
  }

  // ═══════════════════════════════════════════════════════════════════
  // THE LIST
  // ═══════════════════════════════════════════════════════════════════
  function crest(t, size) {
    if (!t) { return '<span class="pr-crest pr-crest-empty" aria-hidden="true">?</span>'; }
    var inner = t.logo && t.logo.url
      ? '<img src="' + esc(t.logo.url) + '" alt="" loading="lazy" onerror="this.replaceWith(document.createTextNode(\'' + esc(t.initials || '?') + '\'))">'
      : esc(t.initials || '?');
    return '<span class="pr-crest' + (t.logo && t.logo.url ? ' has-logo' : '') + (size ? ' pr-crest-' + size : '') + '" title="' + esc(t.name || '') + '">' + inner + '</span>';
  }
  function matchup(teams, title) {
    if (teams && teams.length === 2) {
      return '<div class="pr-matchup">' + crest(teams[0]) + '<span class="pr-mu-names"><b>' + esc(teams[0].name) +
        '</b><span class="muted">vs</span><b>' + esc(teams[1].name) + '</b></span>' + crest(teams[1]) + '</div>';
    }
    return '<div class="pr-matchup"><span class="pr-mu-names"><b>' + esc(title || 'Event not established') + '</b></span></div>';
  }
  function gameChip(status, live) {
    if (live) { return '<span class="pr-live"><i class="pr-pulse" aria-hidden="true"></i>LIVE</span>'; }
    if (status === 'STALE') { return chip('GAME STATE STALE', 'amber pr-stale'); }
    if (!status || status === 'UNAVAILABLE') { return chip('GAME STATE UNAVAILABLE', 'pr-chip-na'); }
    return chip(esc(status), status === 'FINAL' ? 'purple' : '');
  }

  function renderList(d) {
    var tabs = BOOKS.map(function (b) {
      return '<a class="pr-tab' + (b === d.book ? ' on' : '') + '" href="' + listHref(b) + '" data-book="' + b + '"' +
        (b === d.book ? ' aria-current="page"' : '') + '>' + (b === 'PAPER' ? 'Paper book' : 'Actual book') +
        '<small>' + (b === 'PAPER' ? 'simulated execution' : 'real money · per venue') + '</small></a>';
    }).join('');
    var html = '<section class="pr-hero"><div><div class="label">Position rooms · ' + esc(d.book) + '</div>' +
      '<h1>Every active position, as <em>one</em> correlated economic position.</h1>' +
      '<p class="soft">Legs on the same venue event and settlement variable are one room; a leg whose identity is not established stands alone, with the reason. Paper and actual are never added together.</p></div>' +
      '<nav class="pr-tabs" aria-label="Book">' + tabs + '</nav></section>';
    var venues = d.venues || {};
    Object.keys(venues).forEach(function (vn) {
      var v = venues[vn];
      var cn = v.connection || null;
      html += '<section class="pr-venue"><header class="pr-venue-head"><h2>' + esc(d.book === 'PAPER' ? 'Paper book' : 'Actual · ' + (cn ? cn.label : vn)) +
        '</h2>' + chip(esc(v.money_label || ''), d.book === 'PAPER' ? 'blue' : 'green') +
        (cn ? chip(esc(cn.display), cn.connected ? 'green' : 'amber', (cn.why ? cn.why + ' · ' : '') + (cn.source || '')) : '') +
        '<span class="muted pr-count">' + esc(v.count || 0) + ' active room' + ((v.count || 0) === 1 ? '' : 's') + '</span></header>';
      if (!v.available) {
        html += '<div class="pr-empty">' + na(v.why) + ' <span class="muted">' + esc(human(v.why)) + '</span></div>';
      } else if (!v.rooms || !v.rooms.length) {
        html += '<div class="pr-empty"><b>No active position.</b> <span class="muted">' + esc(human(v.empty_reason)) + '</span></div>';
      } else {
        html += '<div class="pr-cards">' + v.rooms.map(card).join('') + '</div>';
      }
      html += '</section>';
    });
    return html;
  }

  function card(r) {
    var ne = r.net_exposure || {};
    var states = r.orders_by_state || {};
    var st = Object.keys(states).filter(function (k) { return k !== 'FILLED' && k !== 'CANCELLED' && k !== 'EXPIRED'; })
      .map(function (k) { return stateBadge(k) + '<b class="money pr-n">×' + esc(states[k]) + '</b>'; }).join(' ');
    var x = r.xavier;
    var grouped = r.grouping === 'ESTABLISHED_EVENT_AND_SETTLEMENT_IDENTITY';
    return '<a class="pr-card" href="' + roomHref(r.group_key) + '">' +
      '<div class="pr-card-top">' + matchup(r.teams, (r.event || {}).title || ('Market ' + String(r.group_key).split(':').slice(2).join(':'))) + gameChip(r.game_status, r.game_live) + '</div>' +
      '<div class="pr-card-sub">' + (grouped ? chip((r.legs > 1 ? 'CORRELATED · ' + esc(r.legs) + ' LEGS' : 'EVENT IDENTITY · ' + esc(r.legs) + ' LEG'), 'blue', 'grouped by venue event + full-game winner settlement identity')
        : chip('UNGROUPED', 'amber', r.ungrouped_reason)) +
        '<span class="muted pr-ellip">' + esc((r.instruments || []).join(' · ') || (r.event || {}).title || '') + '</span></div>' +
      (grouped ? '' : '<div class="pr-why">' + esc(human(r.ungrouped_reason)) + '</div>') +
      '<div class="pr-card-kpis">' +
        '<div><span class="label">Worst outcome</span>' + (ne.worst ? sUsd(ne.worst.pnl_total_usd) + '<small class="muted">' + esc(outcomeName(r, ne.worst.outcome)) + '</small>' : na(r.net_exposure_reason)) + '</div>' +
        '<div><span class="label">Best outcome</span>' + (ne.best ? sUsd(ne.best.pnl_total_usd) + '<small class="muted">' + esc(outcomeName(r, ne.best.outcome)) + '</small>' : na(r.net_exposure_reason)) + '</div>' +
        '<div><span class="label">Unrealized</span>' + sUsd(r.unrealized_pnl_usd, r.unrealized_reason) + '</div>' +
        '<div><span class="label">Capital at risk</span>' + usd(r.capital_at_risk_usd, r.net_exposure_reason) + '</div>' +
      '</div>' +
      cardProtection(r.protection) +
      '<div class="pr-card-orders">' + (st || '<span class="muted">no live order</span>') + '</div>' +
      '<div class="pr-card-foot">' +
        (x ? '<span class="pr-x"><b>Xavier</b> ' + esc(x.recommendation || 'none recorded') + ' · ' + esc(human(x.evidence_state)) +
             ' · review <span data-due="' + esc(x.next_review_due_at || '') + '"></span></span>'
           : '<span class="pr-x muted"><b>Xavier</b> ' + esc(human(r.xavier_reason)) + '</span>') +
        fresh((r.freshness || {}).youngest_book_age_s === null || (r.freshness || {}).youngest_book_age_s === undefined ? 'UNAVAILABLE'
              : ((r.freshness || {}).stale_marks || []).length ? 'STALE' : 'FRESH',
              (r.freshness || {}).youngest_book_age_s, 'youngest observed book') +
      '</div></a>';
  }
  function cardProtection(p) {
    if (!p) { return ''; }
    return '<div class="pr-card-prot" title="' + esc(p.rule || '') + '">' +
      '<span><span class="label">Unprotected</span><b class="money">' + esc(qtyTxt(p.unprotected_qty)) + '</b><small class="muted">of ' + esc(qtyTxt(p.position_qty)) + '</small></span>' +
      '<span><span class="label">Filled protection</span><b class="money">' + esc(qtyTxt(p.filled_protection_qty)) + '</b></span>' +
      '<span class="pr-card-standing"><span class="label">Standing orders</span><b class="money">' + esc(qtyTxt(p.standing_order_qty)) + '</b><small>NOT protection until filled</small></span>' +
      '</div>';
  }
  function outcomeName(r, o) {
    return (r.outcome_labels || {})[o] || human(o);
  }

  // ═══════════════════════════════════════════════════════════════════
  // THE ROOM
  // ═══════════════════════════════════════════════════════════════════
  function renderRoom(r) {
    var labels = r.outcome_labels || {};
    var olab = function (o) { return labels[o] || human(o); };
    return header(r) + scoreboard(r.game_state || {}, r) + protectionStrip(r, olab) + economic(r, olab) +
      '<div class="pr-grid">' +
        '<div class="pr-col-main">' + ladder(r, olab) + scenarios(r, olab) + '</div>' +
        '<aside class="pr-col-side">' + xavierPanels(r, olab) + eddie(r.eddie || {}) + audreyKaren(r) + '</aside>' +
      '</div>' + provenance(r);
  }

  function header(r) {
    var grouped = r.kind === 'EVT';
    return '<section class="pr-room-head">' +
      '<a class="pr-back" href="' + listHref(r.book) + '">← All ' + esc(r.book === 'PAPER' ? 'paper' : 'actual') + ' positions</a>' +
      '<div class="pr-room-title"><h1>' + esc((r.event || {}).title || (r.legs[0] && r.legs[0].instrument.slug) || r.group_key) + '</h1>' +
      '<div class="pr-room-chips">' +
        chip(esc(r.book === 'PAPER' ? 'PAPER · SIMULATED' : 'ACTUAL · ' + r.venue), r.book === 'PAPER' ? 'blue' : 'green') +
        chip(esc((r.economic || {}).money_label || ''), '') +
        (grouped ? chip('ONE CORRELATED POSITION', 'blue', 'venue event ' + ((r.identity || {}).event_slug || '') + ' · ' + ((r.identity || {}).settlement_class || ''))
                 : chip('UNGROUPED · ' + esc(human(r.ungrouped_reason)), 'amber', r.ungrouped_reason)) +
        (r.connection ? chip(esc(r.connection.display), r.connection.connected ? 'green' : 'amber', r.connection.source || '') : '') +
        '<code class="pr-key" title="room key">' + esc(r.group_key) + '</code>' +
      '</div></div></section>';
  }

  // ── THE GAME MODULE ────────────────────────────────────────────────
  function scoreboard(g, r) {
    var teams = g.teams || [];
    var live = g.live === true;
    var stale = g.status === 'STALE';
    var status = live ? '<span class="pr-live big"><i class="pr-pulse" aria-hidden="true"></i>LIVE</span>'
      : stale ? '<span class="pr-sb-status stale">STALE · last reported ' + esc(g.reported_status || '—') + '</span>'
      : '<span class="pr-sb-status">' + esc(g.status || 'UNAVAILABLE') + '</span>';
    var scoreCell = function (side) {
      return '<div class="pr-sb-score ' + side + '" title="' + esc(g.score_reason || '') + '">' +
        (g.score && has(g.score[side]) ? esc(g.score[side]) : '<span class="pr-sb-dash">–</span>') + '</div>';
    };
    var t0 = teams[0], t1 = teams[1];
    return '<section class="pr-sb' + (live ? ' is-live' : '') + (stale ? ' is-stale' : '') + '" aria-label="Game state">' +
      '<div class="pr-sb-team">' + crest(t0, 'xl') + '<b>' + esc(t0 ? t0.name : (g.away_team || '—')) + '</b></div>' +
      scoreCell('away') +
      '<div class="pr-sb-mid">' + status +
        '<div class="pr-sb-period">' + (has(g.period) ? esc(g.period) : 'Period —') + ' · ' + (has(g.clock) ? esc(g.clock) : 'Clock —') + '</div>' +
        '<div class="pr-sb-note">' + (g.score_status === 'UNAVAILABLE' ? 'Score, inning and clock UNAVAILABLE: no authoritative live score source is connected' : '') + '</div>' +
      '</div>' +
      scoreCell('home') +
      '<div class="pr-sb-team right">' + crest(t1, 'xl') + '<b>' + esc(t1 ? t1.name : (g.home_team || '—')) + '</b></div>' +
      '<div class="pr-sb-foot">' +
        (g.observed_at ? fresh(g.freshness, g.age_s, g.source) + '<span class="muted">League report ' + esc(g.event_state_raw || '') + ' · ' + esc(g.source || '') + '</span>'
                       : chip('NO LEAGUE REPORT', 'pr-chip-na') + '<span class="muted">' + esc(human(g.reason)) + '</span>') +
        '<span class="muted">Scheduled ' + esc(when(g.scheduled_start)) + '</span>' +
        '<span class="muted" title="' + esc(g.situation_reason || '') + '">Situation ' + (g.situation ? esc(g.situation) : 'UNAVAILABLE') + '</span>' +
        (g.reason && stale ? '<span class="pr-warn-inline">' + esc(g.reason) + '</span>' : '') +
      '</div></section>';
  }

  // ── THE PROTECTION STRIP: only FILLED quantity is protection ───────
  function protectionStrip(r, olab) {
    var p = r.protection;
    if (!p) { return ''; }
    var ex = p.current_executable_exit || {};
    var wc = p.current_worst_case || {};
    var k = function (cls, lbl, val, sub, title) {
      return '<div class="pr-pk ' + cls + '" title="' + esc(title || '') + '"><span class="label">' + lbl + '</span><div class="pr-pk-v">' + val + '</div>' +
        (sub ? '<small>' + sub + '</small>' : '') + '</div>';
    };
    var condWhy = p.conditional_floor_reason ? human(p.conditional_floor_reason) : '';
    var orders = (p.orders || []).filter(function (o) { return o.standing_qty > 0 || o.pending_qty > 0 || o.filled_qty > 0; });
    var lines = orders.map(function (o) {
      return '<li>' + stateBadge(o.state, null, o.sub_state) + '<code class="pr-pline">' + esc(o.line) + '</code><span class="muted">' + esc(roleLabel(o.role)) + '</span></li>';
    }).join('');
    return '<section class="pr-protect" aria-label="Protection">' +
      '<header class="pr-ph"><h2>Protection</h2><span class="muted">only FILLED quantity is protection · a resting order is not protection until it fills</span></header>' +
      '<p class="pr-pline-main" title="' + esc(p.rule || '') + '"><code>' + esc(p.line || '') + '</code></p>' +
      '<div class="pr-pgrid">' +
        k('', 'Position qty', '<b class="money">' + esc(qtyTxt(p.position_qty)) + '</b>', 'held now + sold by filled protection', p.position_basis) +
        k('is-bad', 'Unprotected qty', '<b class="money">' + esc(qtyTxt(p.unprotected_qty)) + '</b>', 'position − filled protection', p.position_basis) +
        k('is-standing', 'Standing order qty', '<b class="money">' + esc(qtyTxt(p.standing_order_qty)) + '</b>', 'RESTING / PARTIAL remainder · NOT protection' + (num(p.pending_order_qty) ? ' · +' + esc(qtyTxt(p.pending_order_qty)) + ' not yet on the book' : ''), p.rule) +
        k('is-good', 'Filled protection qty', '<b class="money">' + esc(qtyTxt(p.filled_protection_qty)) + '</b>', 'filled protective sales + filled hedges', p.rule) +
        k('is-cond', 'Conditional floor · IF FILLED', usd(p.conditional_floor_if_filled_usd, condWhy || 'nothing standing'),
          num(p.conditional_floor_if_filled_usd) === null ? esc(condWhy || 'nothing standing') : 'CONDITIONAL · nothing of it is realized', p.conditional_floor_basis) +
        k('', 'Realized floor', sUsd(p.realized_floor_usd, r.economic && r.economic.net_exposure_reason), 'filled holdings only · realized protection ' + (num(p.realized_protection_usd) !== null ? dollars(p.realized_protection_usd) : '—'), p.realized_floor_basis) +
        k('', 'Executable exit now', usd(ex.proceeds_at_top_level_usd, ex.reason),
          ex.available ? (ex.covers_whole_position ? 'top of book covers it' : '<span class="amber">top level does not cover it all</span>') + (ex.stale ? ' · <span class="amber">STALE book</span>' : '') : esc(human(ex.reason)), ex.basis) +
        k('is-bad', 'Worst-case exposure now', usd(p.current_worst_case_exposure_usd, r.economic && r.economic.net_exposure_reason),
          wc.worst_outcome ? 'if ' + esc(olab(wc.worst_outcome.outcome)) : '', wc.basis) +
      '</div>' +
      (lines ? '<ul class="pr-plines">' + lines + '</ul>' : '') + '</section>';
  }

  // ── THE ECONOMIC STRIP ─────────────────────────────────────────────
  function economic(r, olab) {
    var e = r.economic || {};
    var ne = e.net_exposure || {};
    var dir = (e.directional || {}).contracts_paying_on || null;
    var lean = '';
    if (dir) {
      var keys = Object.keys(dir);
      var tot = keys.reduce(function (a, k) { return a + (num(dir[k]) || 0); }, 0);
      lean = '<div class="pr-lean" title="' + esc((e.directional || {}).basis || '') + '"><span class="label">Contracts paying on each outcome</span><div class="pr-lean-bar">' +
        keys.map(function (k, i) {
          var w = tot > 0 ? ((num(dir[k]) || 0) > 0 ? Math.max(1.5, dir[k] / tot * 100) : 0) : 100 / keys.length;
          return '<span class="pr-lean-seg s' + i + '" style="width:' + w.toFixed(1) + '%"></span>';
        }).join('') + '</div><div class="pr-lean-key">' + keys.map(function (k, i) {
          return '<span><i class="s' + i + '"></i>' + esc(k) + ' <b class="money">' + esc(qtyTxt(dir[k])) + '</b></span>';
        }).join('') + '</div></div>';
    }
    var k = function (lbl, val, sub, title) {
      return '<div class="pr-kpi" title="' + esc(title || '') + '"><span class="label">' + lbl + '</span><div class="pr-kpi-v">' + val + '</div>' +
        (sub ? '<small class="muted">' + sub + '</small>' : '') + '</div>';
    };
    return '<section class="pr-eco" aria-label="Economic position">' +
      k('Net exposure', ne.worst ? sUsd(ne.worst.pnl_total_usd) + '<span class="muted pr-to"> to </span>' + sUsd(ne.best.pnl_total_usd) : na(e.net_exposure_reason),
        ne.worst ? 'worst ' + esc(olab(ne.worst.outcome)) + ' · best ' + esc(olab(ne.best.outcome)) : '', 'P&L per settlement outcome of the filled holdings (scenario table)') +
      k('Mark value', usd(e.mark_value_usd, e.unrealized_reason), 'top-of-book exit', 'mark = best displayed exit price of the held side, from the latest observed book') +
      k('Unrealized P&L', sUsd(e.unrealized_pnl_usd, e.unrealized_reason), e.stale_marks && e.stale_marks.length ? '<span class="amber">STALE mark</span>' : 'open × mark − basis', 'gross of exit fees') +
      k('Realized P&L', sUsd(e.realized_pnl_usd), 'fees ' + (num(e.fees_usd) !== null ? '$' + e.fees_usd.toFixed(2) : '—') + (e.fees_known === false ? ' (incomplete)' : ''), 'sales and settlements, average cost incl. fees') +
      k('Capital at risk', usd(e.capital_at_risk_usd, e.net_exposure_reason), 'largest loss from here', e.capital_at_risk_basis) +
      k('Locked P&L', sUsd(e.locked_pnl_usd, e.net_exposure_reason), 'filled holdings only', 'min over played outcomes of total P&L of the FILLED holdings; standing orders excluded') +
      k('Expected P&L', sUsd(e.expected_pnl_usd, e.expected_reason), e.expected_pnl_usd === null || e.expected_pnl_usd === undefined ? esc(human(e.expected_reason)) : (((r.scenarios || {}).probabilities || {}).stale ? '<span class="amber">on a STALE recorded probability</span>' : 'on Xavier’s recorded probability'), ((r.scenarios || {}).probabilities || {}).source || e.expected_reason) +
      lean + '</section>';
  }

  // ── LEGS, ORDERS AND THE PRICE TRACK ───────────────────────────────
  function ladder(r, olab) {
    var byInst = {};
    var order = [];
    function slot(slug, side, lbl) {
      var k = slug + '|' + side;
      if (!byInst[k]) { byInst[k] = {label: lbl, slug: slug, side: side, legs: [], orders: []}; order.push(k); }
      return byInst[k];
    }
    (r.legs || []).forEach(function (lg) { slot(lg.instrument.slug, lg.holding_side, lg.instrument.label).legs.push(lg); });
    (r.orders || []).forEach(function (o) { slot(o.instrument.slug, o.holding_side, o.instrument.label).orders.push(o); });
    var html = '<section class="pr-panel"><header class="pr-ph"><h2>Legs &amp; orders</h2><span class="muted">entry → holding → correlated legs → proposed / standing / partial / filled</span></header>' +
      '<div class="pr-legend">' + CANON.map(function (s) { return stateBadge(s); }).join('') + '</div>';
    order.forEach(function (k) { html += instrument(byInst[k], r, olab); });
    return html + '</section>';
  }

  function instrument(I, r, olab) {
    var cur = (I.legs[0] || {}).current || ((I.orders[0] || {}).current) || {};
    var held = I.legs.reduce(function (a, lg) { return a + (num(lg.holding.open_qty) || 0); }, 0);
    var contract = (I.legs[0] || I.orders[0] || {}).contract || {};
    var pays = (contract.pays_on || []).map(olab).join(' / ');
    var html = '<article class="pr-inst">' +
      '<header class="pr-inst-head"><div><h3>' + esc(I.label || I.slug) + '</h3>' +
      '<div class="muted pr-contract" title="' + esc(contract.basis || '') + '">' + esc(contract.venue || '') + ' · <code>' + esc(I.slug) + '</code> · held side ' + esc(I.side) +
        (pays ? ' · pays on <b>' + esc(pays) + '</b>' : '') + (contract.identity_status && contract.identity_status !== 'ESTABLISHED' ? ' · <span class="amber">identity ' + esc(human(contract.identity_reason)) + '</span>' : '') + '</div></div>' +
      '<div class="pr-quote" title="' + prov(cur.source, cur.observed_at) + '">' +
        '<span><span class="label">Bid</span>' + cents(cur.bid, cur.reason) + '</span>' +
        '<span><span class="label">Ask</span>' + cents(cur.ask, cur.reason) + '</span>' +
        '<span><span class="label">Mark</span>' + cents(cur.mark, cur.reason) + '</span>' +
        fresh(cur.freshness, cur.age_s, cur.source) + '</div></header>';
    I.legs.forEach(function (lg) {
      var h = lg.holding || {};
      html += '<div class="pr-hold' + (lg.open ? '' : ' closed') + '">' +
        '<span class="pr-hold-tag">' + (lg.open ? 'HOLDING' : 'CLOSED') + '</span>' +
        '<span><b class="money">' + esc(qtyTxt(h.open_qty)) + '</b> <span class="muted">open of ' + esc(qtyTxt(h.bought_qty)) + ' bought</span></span>' +
        '<span><span class="muted">avg entry</span> ' + cents(h.avg_entry_price) + ' <span class="muted">(' + cents(h.avg_cost_incl_fees) + ' incl. fees)</span></span>' +
        '<span><span class="muted">basis</span> ' + usd(h.cost_basis_usd) + '</span>' +
        '<span><span class="muted">unrealized</span> ' + sUsd(lg.unrealized_pnl_usd, lg.unrealized_basis) + '</span>' +
        '<span><span class="muted">realized</span> ' + sUsd(h.realized_pnl_usd) + '</span>' +
        '<span class="muted pr-grp" title="' + esc((lg.account || {}).label || '') + '">group ' + esc(lg.group_id) + ' · ' + esc((lg.account || {}).account || '') + '</span>' +
        '</div>';
    });
    html += track(I, cur);
    I.orders.forEach(function (o) { html += orderRow(o, olab); });
    return html + '</article>';
  }

  // THE OWNER'S CANONICAL VISUAL: a price track with bid/ask band, mark,
  // entry and each live order's threshold, and the distance spelled out.
  function track(I, cur) {
    var pts = [];
    var add = function (v) { if (num(v) !== null) { pts.push(v); } };
    add(cur.bid); add(cur.ask);
    I.legs.forEach(function (lg) { add(lg.holding.avg_entry_price); });
    var live = I.orders.filter(function (o) { return ['PROPOSED', 'SUBMITTED', 'UNKNOWN', 'RESTING', 'PARTIAL', 'CANCEL_PENDING'].indexOf(o.state) >= 0; });
    live.forEach(function (o) { add(o.limit); });
    if (!pts.length) { return '<div class="pr-track-na">' + na(cur.reason) + ' <span class="muted">no price to place on a track: ' + esc(human(cur.reason)) + '</span></div>'; }
    var lo = Math.max(0, Math.min.apply(null, pts) - 0.04), hi = Math.min(1, Math.max.apply(null, pts) + 0.04);
    if (hi - lo < 0.12) { var m = (hi + lo) / 2; lo = Math.max(0, m - 0.06); hi = Math.min(1, m + 0.06); }
    var x = function (v) { return ((v - lo) / (hi - lo) * 100).toFixed(2) + '%'; };
    var ticks = '';
    var step = (hi - lo) > 0.4 ? 0.1 : (hi - lo) > 0.2 ? 0.05 : 0.02;
    for (var t = Math.ceil(lo / step) * step; t <= hi + 1e-9; t += step) {
      ticks += '<span class="pr-tick" style="left:' + x(t) + '">' + Math.round(t * 100) + '¢</span>';
    }
    var band = num(cur.bid) !== null && num(cur.ask) !== null
      ? '<span class="pr-band" style="left:' + x(cur.bid) + ';width:calc(' + x(cur.ask) + ' - ' + x(cur.bid) + ')" title="bid ' + centsTxt(cur.bid) + ' – ask ' + centsTxt(cur.ask) + '"></span>' : '';
    var marks = '';
    if (num(cur.bid) !== null) { marks += '<span class="pr-mk pr-mk-bid" style="left:' + x(cur.bid) + '"><b>BID ' + centsTxt(cur.bid) + '</b></span>'; }
    if (num(cur.ask) !== null) { marks += '<span class="pr-mk pr-mk-ask" style="left:' + x(cur.ask) + '"><b>ASK ' + centsTxt(cur.ask) + '</b></span>'; }
    I.legs.forEach(function (lg) {
      if (num(lg.holding.avg_entry_price) !== null) {
        marks += '<span class="pr-mk pr-mk-entry" style="left:' + x(lg.holding.avg_entry_price) + '" title="average entry ' + centsTxt(lg.holding.avg_entry_price) + '"><b>ENTRY ' + centsTxt(lg.holding.avg_entry_price) + '</b></span>';
      }
    });
    live.forEach(function (o) {
      if (num(o.limit) === null) { return; }
      var ref = o.direction === 'BUY' ? cur.ask : cur.bid;
      var gap = num(ref) !== null ? '<span class="pr-gap ' + (o.direction === 'BUY' ? 'down' : 'up') + '" style="left:' + x(Math.min(ref, o.limit)) + ';width:calc(' + x(Math.max(ref, o.limit)) + ' - ' + x(Math.min(ref, o.limit)) + ')"></span>' : '';
      marks += gap + '<span class="pr-th st-' + esc(o.state) + '" style="left:' + x(o.limit) + '" title="' + esc(o.direction + ' ' + centsTxt(o.limit) + ' · ' + o.state) + '"><i class="pr-glyph"></i><b>' + esc(o.direction) + ' ' + centsTxt(o.limit) + '</b></span>';
    });
    return '<div class="pr-track" role="img" aria-label="Price track: bid ' + centsTxt(cur.bid) + ', ask ' + centsTxt(cur.ask) + (live.length ? ', ' + live.map(function (o) { return o.direction + ' at ' + centsTxt(o.limit) + ' ' + o.state; }).join(', ') : '') + '">' +
      '<div class="pr-rail">' + band + marks + '</div><div class="pr-ticks">' + ticks + '</div></div>';
  }

  function orderRow(o, olab) {
    var q = num(o.qty) || 0, f = num(o.filled_qty) || 0;
    var p = q > 0 ? Math.min(100, f / q * 100) : 0;
    var dist = o.distance || null;
    var standing = ['RESTING', 'PARTIAL'].indexOf(o.state) >= 0;
    var head = '<div class="pr-ord-line">' + stateBadge(o.state, o.raw_state, o.sub_state) +
      '<b class="pr-ord-what">' + esc((o.role === 'STANDING_PROTECTION' ? 'STANDING ' : '') + o.direction) + ' ' + centsTxt(o.limit) + '</b>' +
      '<span class="muted">' + esc(roleLabel(o.role)) + '</span>' +
      (o.note ? '<span class="pr-note">' + esc(o.note) + '</span>' : '') + '</div>';
    // the canonical strip: STANDING SELL 70¢ · BID · ASK · DISTANCE · STATUS · FILLED x / N
    var strip = '<div class="pr-canon">' +
      '<span><span class="label">Bid</span>' + cents((o.current || {}).bid, (o.current || {}).reason) + '</span>' +
      '<span><span class="label">Ask</span>' + cents((o.current || {}).ask, (o.current || {}).reason) + '</span>' +
      '<span><span class="label">Distance</span>' + (dist ? (num(dist.distance) !== null ? '<b class="money">' + centsTxt(Math.abs(dist.distance)) + '</b><small class="muted">' + esc(dist.needs === 'AT_OR_THROUGH_NOW' ? 'at or through now' : dist.needs) + '</small>' : na(dist.reason)) : '<span class="muted">—</span>') + '</span>' +
      '<span><span class="label">Status</span>' + esc((STATE[o.state] || {t: o.state}).t) + '</span>' +
      '<span class="pr-fillcell"><span class="label">' + (o.protective ? 'Filled · protection' : 'Filled') + '</span><b class="money">' + esc(qtyTxt(f)) + ' / ' + esc(qtyTxt(q)) + '</b>' +
        '<span class="pr-prog st-' + esc(o.state) + '"><i style="width:' + p.toFixed(1) + '%"></i></span></span>' +
      (standing ? '<span class="pr-standcell"><span class="label">' + (o.protective ? 'Standing · NOT protection' : 'Standing') + '</span><b class="money">' + esc(qtyTxt(o.standing_qty !== undefined ? o.standing_qty : o.remaining_qty)) + '</b></span>' : '') +
      (num(o.avg_fill_price) !== null ? '<span><span class="label">Avg fill</span>' + cents(o.avg_fill_price) + '</span>' : '') +
      (num(o.fees_usd) !== null ? '<span><span class="label">Fees</span>' + usd(o.fees_usd) + '</span>' : '') +
      (num(o.pnl_usd) !== null ? '<span title="' + esc(o.pnl_basis || '') + '"><span class="label">P&amp;L</span>' + sUsd(o.pnl_usd) + '</span>' : '') +
      '</div>';
    var meta = '<div class="pr-ord-meta muted">' + esc(o.source) + ' · <code>' + esc(o.order_ref) + '</code> · ' + esc((o.account || {}).account || '') +
      ' · created ' + esc(when(o.created_at)) + (o.expires_at && standing ? ' · good till ' + esc(when(o.expires_at)) : '') +
      (o.terminal_reason ? ' · ' + esc(human(o.terminal_reason)) : '') + (o.decision_id ? ' · decision <code>' + esc(o.decision_id) + '</code>' : '') + '</div>';
    return '<div class="pr-ord st-' + esc(o.state) + '">' + head + strip + meta + (o.if_it_fills ? fillsCard(o.if_it_fills, olab, o) : '') + '</div>';
  }

  function fillsCard(f, olab, o) {
    if (!f.available) {
      return '<div class="pr-iff na"><span class="label">If this fills</span> ' + na(f.reason) + ' <span class="muted">' + esc(human(f.reason)) + '</span></div>';
    }
    var b = f.net_exposure_before || {}, a = f.net_exposure_after || {};
    var inv = f.resulting_inventory || {};
    var rows = (f.scenario_after || []).map(function (s) {
      return '<span class="pr-iff-o"><span class="muted">' + esc(olab(s.outcome)) + '</span> ' + sUsd(s.pnl_total_usd) + '</span>';
    }).join('');
    return '<div class="pr-iff"><header><span class="label">IF FILLED · conditional · ' + (o.state === 'PROPOSED' ? 'if placed and filled · ' : '') + esc(qtyTxt(f.remaining_qty)) + ' @ ' + centsTxt(f.at_price) + '</span>' +
      '<small class="muted" title="' + esc(f.fees) + '">gross of the hypothetical fill’s fees</small></header>' +
      '<div class="pr-iff-grid">' +
        '<div><span class="label">Realized on the fill</span>' + sUsd(f.realized_on_fill_usd) + '</div>' +
        '<div><span class="label">Resulting inventory</span><b class="money">' + esc(qtyTxt(inv.open_qty)) + '</b><small class="muted">avg ' + (num(inv.avg_basis_per_contract) !== null ? centsTxt(inv.avg_basis_per_contract) : '—') + '</small></div>' +
        '<div><span class="label">Locked P&amp;L</span>' + sUsd(b.locked_pnl_usd) + ' <span class="muted">→</span> ' + sUsd(a.locked_pnl_usd) + '</div>' +
        '<div><span class="label">Remaining exposure</span>' + usd(b.capital_at_risk_usd) + ' <span class="muted">→</span> ' + usd(f.remaining_exposure_after_usd) + '</div>' +
        '<div title="' + esc(f.expected_basis || f.expected_reason || '') + '"><span class="label">Expected P&amp;L</span>' + sUsd(f.expected_pnl_before_usd, f.expected_reason) + ' <span class="muted">→</span> ' + sUsd(f.expected_pnl_after_usd, f.expected_reason) + '</div>' +
      '</div><div class="pr-iff-outs"><span class="label">Then, per outcome</span>' + rows + '</div>' +
      (f.note ? '<div class="pr-warn-inline">' + esc(f.note) + '</div>' : '') + '</div>';
  }

  // ── SETTLEMENT SCENARIOS ───────────────────────────────────────────
  function scenarios(r, olab) {
    var sc = r.scenarios || {};
    if (!sc.available) {
      return '<section class="pr-panel"><header class="pr-ph"><h2>Settlement scenarios</h2></header>' + na(sc.reason) + ' <span class="muted">' + esc(human(sc.reason)) + '</span></section>';
    }
    var cols = [['Filled holdings now · realized basis', sc.current], ['IF FILLED · every standing order (conditional)', sc.with_standing_filled]];
    if (sc.with_standing_and_proposed_filled) { cols.push(['IF FILLED · + proposed (conditional)', sc.with_standing_and_proposed_filled]); }
    var outs = (sc.current.rows || []).map(function (x) { return x.outcome; });
    var max = 1;
    cols.forEach(function (c) { (c[1].rows || []).forEach(function (x) { max = Math.max(max, Math.abs(x.pnl_total_usd || 0)); }); });
    var head = '<tr><th scope="col">Outcome</th>' + cols.map(function (c) { return '<th scope="col">' + esc(c[0]) + '</th>'; }).join('') + '</tr>';
    var body = outs.map(function (o) {
      return '<tr><th scope="row">' + esc(o === 'VOID' ? 'Void (refund)' : olab(o) + ' win' + (o === 'DRAW' ? 's' : 's')).replace('Draw wins', 'Draw') + '</th>' + cols.map(function (c) {
        var x = (c[1].rows || []).find(function (y) { return y.outcome === o; }) || {};
        var v = num(x.pnl_total_usd);
        var w = v === null ? 0 : Math.abs(v) / max * 100;
        return '<td><div class="pr-sc-cell">' + sUsd(v) + '<span class="pr-sc-bar ' + (v < 0 ? 'neg' : 'pos') + '"><i style="width:' + w.toFixed(1) + '%"></i></span>' +
          '<small class="muted">payout ' + usd(x.payout_usd) + '</small></div></td>';
      }).join('') + '</tr>';
    }).join('');
    var foot = '<tr class="pr-sc-foot"><th scope="row">Floor · at risk</th>' + cols.map(function (c, i) {
      return '<td>' + sUsd(c[1].locked_pnl_usd) + ' · ' + usd(c[1].capital_at_risk_usd) + (i > 0 ? ' <small class="pr-if">IF FILLED</small>' : ' <small class="muted">realized</small>') + '</td>';
    }).join('') + '</tr>';
    return '<section class="pr-panel"><header class="pr-ph"><h2>Settlement scenarios</h2><span class="muted">total P&amp;L per outcome, from the recorded quantities and prices</span></header>' +
      '<div class="pr-sc-wrap"><table class="pr-sc">' + head + body + foot + '</table></div>' +
      '<p class="muted pr-fine">' + esc(sc.fees || '') + ' ' + esc((sc.current || {}).void_basis || '') + '</p></section>';
  }

  // ── XAVIER, THE DECISION MAKER ─────────────────────────────────────
  function xavierPanels(r, olab) {
    var xs = r.xavier || [];
    if (!xs.length) {
      return '<section class="pr-panel pr-xav"><header class="pr-ph"><h2>Xavier</h2></header><p class="muted">No group in this room is under Xavier’s management yet.</p></section>';
    }
    return xs.slice().sort(function (a, b) { return (a.status === 'OK' ? 0 : 1) - (b.status === 'OK' ? 0 : 1); })
      .map(function (x) { return xavier(x, olab); }).join('');
  }
  var REC_CLASS = {HOLD: 'hold', EXIT: 'exit', REDUCE: 'reduce', HEDGE: 'hedge', PROTECT: 'protect', WAITING_FOR_EVIDENCE: 'wait'};
  function xavier(x, olab) {
    var head = '<header class="pr-ph"><h2>Xavier · decision</h2><code class="muted">' + esc(x.group_id) + '</code></header>';
    if (x.status !== 'OK') {
      return '<section class="pr-panel pr-xav">' + head + '<div class="pr-xav-hero wait"><span class="label">Recommendation</span><b>UNAVAILABLE</b><small>' + esc(human(x.why)) + '</small></div>' +
        protection(x.protection) + '</section>';
    }
    var ev = x.evidence || {};
    var alts = (x.alternatives || []);
    var vmax = alts.reduce(function (a, y) { return Math.max(a, Math.abs(num(y.value_usd) || 0)); }, 1);
    var altRows = alts.map(function (a) {
      var v = num(a.value_usd);
      var w = v === null ? 0 : Math.abs(v) / vmax * 100;
      return '<li class="pr-alt' + (a.is_recommendation ? ' lead' : '') + (a.rankable ? '' : ' blocked') + '">' +
        '<span class="pr-alt-rank">' + (a.rank ? '#' + a.rank : '—') + '</span>' +
        '<span class="pr-alt-name">' + esc(a.action) + (a.mode ? ' <small class="muted">' + esc(a.mode) + '</small>' : '') + '</span>' +
        '<span class="pr-alt-bar"><i style="width:' + w.toFixed(1) + '%"></i></span>' +
        '<span class="pr-alt-v">' + (v === null ? '<span class="muted">—</span>' : usd(v)) + '</span>' +
        (a.blocker || a.note || a.state ? '<span class="pr-alt-why muted">' + esc(a.note || (a.state ? 'state ' + a.state : '') || human(a.blocker)) + (a.blocker && (a.note || a.state) ? ' · ' + esc(human(a.blocker)) : '') + '</span>' : '') +
        '</li>';
    }).join('');
    var why = x.why_leader_wins || null;
    var wait = (x.waiting_for || []).map(function (w) {
      return '<li><b>' + esc(human(w.what)) + '</b>' + (w.order_ref ? ' <code>' + esc(w.order_ref) + '</code> ' + esc(w.state || '') + (num(w.limit) !== null ? ' @ ' + centsTxt(w.limit) : '') + (num(w.distance) !== null ? ' · ' + centsTxt(Math.abs(w.distance)) + ' away' : '') : '') +
        (w.blocker ? ' <span class="muted">' + esc(w.action) + ': ' + esc(human(w.blocker)) + '</span>' : '') + (w.why ? ' <span class="muted">' + esc(w.why) + '</span>' : '') + '</li>';
    }).join('');
    var warn = (x.stale_evidence || []).map(function (w) {
      return '<li title="' + esc(w.source || '') + '">' + esc(human(w.what)) + ' <span class="muted">' + esc(w.detail || '') + '</span></li>';
    }).join('');
    var th = x.thesis || {};
    var nt = x.next_trigger || {};
    var pos = ((x.position || {}).legs || []).map(function (p) {
      var pm = (p.venue_prices || {}).POLYMARKET || {}, ks = (p.venue_prices || {}).KALSHI || {};
      return '<div class="pr-vp"><b>' + esc(p.instrument) + '</b> <span class="muted">' + esc(qtyTxt(p.open_qty)) + ' held · mark ' + centsTxt(p.mark) + ' · value ' + (num(p.mark_value_usd) !== null ? '$' + p.mark_value_usd.toFixed(2) : '—') + '</span>' +
        '<div class="pr-vp-row"><span class="pr-vp-v">Polymarket</span>' + (pm.status === 'UNAVAILABLE' ? na(pm.reason) : 'bid ' + cents(pm.bid, pm.reason) + ' · ask ' + cents(pm.ask, pm.reason) + ' ' + fresh(pm.freshness, pm.age_s, pm.source)) + '</div>' +
        '<div class="pr-vp-row"><span class="pr-vp-v">Kalshi</span>' + (ks.status === 'UNAVAILABLE' || num(ks.bid) === null ? na(ks.reason) + ' <small class="muted">' + esc(human(String(ks.reason || '').split(':')[0])) + '</small>' : 'bid ' + cents(ks.bid) + ' · ask ' + cents(ks.ask)) + '</div></div>';
    }).join('');
    var rec = x.display_recommendation || x.recommendation || 'NONE';
    return '<section class="pr-panel pr-xav">' + head +
      '<div class="pr-xav-hero ' + (REC_CLASS[rec] || 'hold') + '"><span class="label">Recommendation</span><b>' + esc(human(rec).toUpperCase()) + '</b>' +
        '<small>' + esc(x.display_basis || '') + '</small>' +
        '<div class="pr-xav-sub">assessed <span data-age-from="' + esc(x.assessed_at || '') + '"></span> · trigger ' + esc(human(x.trigger)) + '</div></div>' +
      '<div class="pr-xav-ev">' +
        '<div><span class="label">Probability</span>' + pct(ev.probability) + '<small class="muted">' + esc(ev.source || '—') + '</small></div>' +
        '<div><span class="label">Evidence</span>' + chip(esc(human(ev.state)), ev.state === 'FRESH_CURRENT_PROBABILITY' ? 'green' : 'amber') + '<small class="muted">' + esc(age(ev.age_now_s)) + ' old now</small></div>' +
        '<div><span class="label">Current EV</span>' + usd(x.current_ev_usd) + '<small class="muted" title="' + esc(x.current_ev_basis || '') + '">HOLD value</small></div>' +
        '<div><span class="label">Entry EV</span>' + usd(x.entry_ev_usd, 'no entry thesis') + '<small class="muted">at entry</small></div>' +
      '</div>' +
      (warn ? '<ul class="pr-xav-warn">' + warn + '</ul>' : '') +
      '<div class="pr-sub"><span class="label">Alternatives, ranked</span><ol class="pr-alts">' + altRows + '</ol></div>' +
      (why ? '<div class="pr-why-lead"><span class="label">Why the leader wins</span><p>' + esc(why.text) + '</p>' + (num(why.margin_over_runner_up) !== null ? '<small class="muted">margin over runner-up $' + why.margin_over_runner_up.toFixed(2) + ' · ' + esc(why.source) + '</small>' : '<small class="muted">' + esc(why.source) + '</small>') + '</div>' : '') +
      '<div class="pr-sub pr-next"><div><span class="label">Next scheduled review</span><b data-due="' + esc(x.next_review_due_at || '') + '"></b><small class="muted">' + esc(when(x.next_review_due_at)) + '</small></div>' +
        '<div><span class="label">Next trigger</span><small>' + esc((nt.events || []).join(' · ') || 'scheduled backstop only') + '</small></div></div>' +
      (wait ? '<div class="pr-sub"><span class="label">Waiting for</span><ul class="pr-wait">' + wait + '</ul></div>' : '') +
      '<div class="pr-sub"><span class="label">Thesis</span><div>' + chip(esc(human(th.state || 'UNKNOWN')), th.state === 'STILL_VALID' ? 'green' : th.state === 'THESIS_CHANGED' ? 'red' : 'amber') +
        ' <span class="muted">entry p ' + (num(th.entry_probability) !== null ? (th.entry_probability * 100).toFixed(1) + '%' : '—') + ' · ' + esc(th.expiry_basis || '') + '</span></div></div>' +
      protection(x.protection) +
      (pos ? '<div class="pr-sub"><span class="label">Position &amp; venue prices</span>' + pos + '</div>' : '') +
      '</section>';
  }
  function protection(p) {
    if (!p) { return ''; }
    var standing = p.standing_order_qty !== undefined ? p.standing_order_qty : p.unfilled_resting_protection_qty;
    return '<div class="pr-sub pr-prot"><span class="label">Protection · this group</span><div class="pr-prot-row">' +
      '<span class="pr-prot-f"><b class="money">' + esc(qtyTxt(p.filled_protection_qty)) + '</b><small>FILLED protection</small></span>' +
      '<span class="pr-prot-u"><b class="money">' + esc(qtyTxt(standing)) + '</b><small>UNFILLED · resting, not protection until filled</small></span>' +
      (p.unprotected_qty !== undefined ? '<span class="pr-prot-x"><b class="money">' + esc(qtyTxt(p.unprotected_qty)) + '</b><small>UNPROTECTED of ' + esc(qtyTxt(p.position_qty)) + '</small></span>' : '') +
      (p.conditional_floor_label ? '<span class="pr-prot-c"><b>' + (num(p.conditional_floor_if_filled_usd) !== null ? usd(p.conditional_floor_if_filled_usd) : '—') + '</b><small>CONDITIONAL FLOOR · IF FILLED</small></span>' : '') +
      '</div>' + (p.line ? '<code class="pr-pline">' + esc(p.line) + '</code>' : '') + '</div>';
  }

  function eddie(e) {
    var body = e.status === 'OK'
      ? (e.estimates || []).map(function (x) {
          return '<li><b>' + esc(x.recommendation) + '</b> <span class="muted">' + esc(x.recommendation_reason || '') + '</span>' +
            '<small class="muted">fill p ' + (num(x.expected_fill_probability) !== null ? (x.expected_fill_probability * 100).toFixed(0) + '%' : '—') +
            ' · net edge ' + (num(x.expected_net_executable_edge_pp) !== null ? x.expected_net_executable_edge_pp.toFixed(2) + 'pp' : '—') + ' · ' + esc(when(x.estimated_at)) + '</small></li>';
        }).join('')
      : '';
    return '<section class="pr-panel pr-eddie"><header class="pr-ph"><h2>Eddie · execution</h2>' + chip(esc(e.status || 'UNAVAILABLE'), e.status === 'OK' ? 'green' : 'pr-chip-na') + '</header>' +
      (body ? '<ul class="pr-list">' + body + '</ul>' : '<p class="muted">' + esc(human(e.why)) + '</p>') + '</section>';
  }

  function audreyKaren(r) {
    var a = r.audrey || {}, k = r.karen || {};
    var aCls = a.status === 'DISCREPANCY' ? 'red' : a.status === 'FINDINGS' ? 'amber' : a.status === 'RECONCILED' ? 'green' : '';
    var kCls = k.status === 'OPEN_CHALLENGE' ? 'red' : k.status === 'RESOLVED' ? 'green' : '';
    var kItems = (k.items || []).map(function (c) {
      return '<li><b>' + esc(human(c.detector)) + '</b> ' + chip(esc(c.severity), c.severity === 'HIGH' || c.severity === 'CRITICAL' ? 'red' : 'amber') + ' ' + chip(esc(c.state), '') +
        '<small class="muted">' + esc(c.claim) + '</small></li>';
    }).join('');
    var aItems = (a.findings || []).map(function (f) { return '<li>' + chip(esc(f.severity), f.severity === 'CRITICAL' ? 'red' : 'amber') + ' ' + esc(human(f.kind)) + ' <small class="muted">' + esc(when(f.found_at)) + '</small></li>'; }).join('') +
      (a.reconciliation || []).map(function (x) { return '<li>' + chip(esc(x.status), x.status === 'MATCHED' ? 'green' : 'red') + ' venue reconciliation · ' + esc(x.venue) + ' <small class="muted">' + esc(when(x.reconciled_at)) + '</small></li>'; }).join('');
    return '<section class="pr-panel"><header class="pr-ph"><h2>Audrey · audit</h2>' + chip(esc(human(a.status || 'UNAVAILABLE')).toUpperCase(), aCls) + '</header>' +
      (aItems ? '<ul class="pr-list">' + aItems + '</ul>' : '<p class="muted">No finding names this room.</p>') + '</section>' +
      '<section class="pr-panel"><header class="pr-ph"><h2>Karen · challenge</h2>' + chip(esc(human(k.status || 'NONE')).toUpperCase(), kCls) + '</header>' +
      (kItems ? '<ul class="pr-list">' + kItems + '</ul>' : '<p class="muted">No challenge names this room.</p>') + '</section>';
  }

  function provenance(r) {
    return '<footer class="pr-prov"><span class="label">Provenance</span><p class="muted">Room ' + esc(r.group_key) + ' · ' + esc(r.grouping) +
      ' · identity ' + esc((r.identity || {}).basis || '') + '. Marks: ' + esc((((r.legs || [])[0] || {}).current || {}).mark_method || '') +
      '. Read-only: this page cannot place, change or cancel anything.</p></footer>';
  }

  // ── boot ───────────────────────────────────────────────────────────
  function boot() {
    var q = new URLSearchParams(location.search);
    var g = q.get('g');
    var b = String(q.get('book') || 'PAPER').toUpperCase();
    if (g !== null) {
      if (!KEY_RE.test(g)) {
        setState('NOT A POSITION ROOM KEY: <code>' + esc(g) + '</code>. <a href="' + listHref() + '">All positions →</a>', 'warn');
        return;
      }
      S.mode = 'room'; S.key = g;
      document.title = 'Position room · BettorToken Command';
    } else {
      S.mode = 'list'; S.book = BOOKS.indexOf(b) >= 0 ? b : 'PAPER';
      var nav = document.querySelector('[data-nav="list"]');
      if (nav) { nav.setAttribute('aria-current', 'page'); }
    }
    document.getElementById('pr-refresh').addEventListener('click', function () { load(false); });
    load(false);
    S.timer = setInterval(function () { if (!document.hidden) { load(true); } }, POLL_MS);
    S.tick = setInterval(tick, 1000);
  }
  window.BTPositionRooms = {version: '1.0.0'};
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', boot); } else { boot(); }
}());
