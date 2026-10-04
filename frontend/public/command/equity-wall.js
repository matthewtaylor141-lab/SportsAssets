/* THE LIVE EQUITY WALL. window.BTEquityWall -- the only global it defines.
 *
 * Two separate live accounts, read from GET /api/command/equity/live and
 * /api/command/equity/curve (same-origin, HttpOnly session cookie, GET only):
 *   $500,000 PAPER EXPERIMENT   fictional money, simulated execution
 *   SMALL LIVE CAPITAL          real money at 1:1,000, PER VENUE
 *                               (Polymarket US / Kalshi), never combined
 * The two are never added together anywhere in this file.
 *
 * NOTHING HERE MOVES ON ITS OWN. A figure changes only when the server's
 * payload changes (its ETag/seq): the odometer rolls and the card flashes
 * green/red only on a genuine change, in the direction of that change. The
 * equity line is a STEP line through recorded points (a value holds until
 * an input changes) -- never interpolated, never smoothed, never synthetic.
 * When a source stops updating the card is STALE: desaturated, a banner with
 * the age, and the line FROZEN at the last genuine input. UNAVAILABLE shows
 * the server's exact reason and no number.
 *
 * API
 *   BTEquityWall.mount(el, {compact}) -> {destroy()}   any number of mounts
 *   BTEquityWall.subscribe(cb) -> unsubscribe          cb(state) on every
 *        genuine change (one shared poll loop for every mount/subscriber)
 *   BTEquityWall.state()                               the latest state
 *   BTEquityWall.paint(canvas, {book, venue})          draw one account onto
 *        a 2D canvas (e.g. a WebGL screen texture) from the same state
 *   BTEquityWall.refresh()                             poll now
 * One poll loop per page (~5 s, If-None-Match -> 304), paused while hidden.
 */
(function () {
  'use strict';
  if (window.BTEquityWall) { return; }

  var LIVE = '/api/command/equity/live';
  var CURVE = '/api/command/equity/curve';
  var WINDOWS = ['1d', '7d', '30d'];
  var CURVE_REFRESH_MS = 60000;
  var REDUCED = !!(window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  var uid = 0;

  // ── formatting ─────────────────────────────────────────────────────
  function fin(n) { return typeof n === 'number' && isFinite(n); }
  var USD = new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD',
    minimumFractionDigits: 2, maximumFractionDigits: 2});
  var USD0 = new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD',
    maximumFractionDigits: 0});
  function money(n) { return fin(n) ? USD.format(n) : '—'; }
  function signed(n) {
    if (!fin(n)) { return '—'; }
    if (n === 0) { return USD.format(0); }
    return (n > 0 ? '+' : '−') + USD.format(Math.abs(n));
  }
  function pct(n) {
    if (!fin(n)) { return ''; }
    var a = Math.abs(n), s = a >= 1 ? a.toFixed(2) : a >= 0.01 ? a.toFixed(3) : a.toFixed(4);
    return (n > 0 ? '+' : n < 0 ? '−' : '') + s + '%';
  }
  function esc(x) {
    return String(x == null ? '' : x).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  function ts(iso) { var t = Date.parse(iso || ''); return isNaN(t) ? null : t / 1000; }
  function ageText(s) {
    if (!fin(s)) { return 'unknown age'; }
    if (s < 90) { return Math.max(0, Math.floor(s)) + 's'; }
    if (s < 5400) { return Math.floor(s / 60) + 'm'; }
    if (s < 172800) { return (s / 3600).toFixed(1) + 'h'; }
    return (s / 86400).toFixed(1) + 'd';
  }
  var ET = {timeZone: 'America/New_York'};
  function clock(t, withDate) {
    if (!fin(t)) { return '—'; }
    var d = new Date(t * 1000);
    var o = {hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: ET.timeZone};
    if (withDate) { o.month = 'short'; o.day = 'numeric'; }
    return d.toLocaleString('en-US', o) + ' ET';
  }
  function dayOf(t) { return fin(t) ? new Date(t * 1000).toLocaleString('en-US', {month: 'short', day: 'numeric', timeZone: ET.timeZone}) : ''; }
  function dir(n) { return !fin(n) || n === 0 ? 'flat' : n > 0 ? 'up' : 'down'; }
  function arrow(n) { return !fin(n) || n === 0 ? '■' : n > 0 ? '▲' : '▼'; }

  // ── the shared store: ONE poll loop for the page ──────────────────
  var S = {live: null, etag: null, status: 'IDLE', error: null, okAt: 0,
    checkedAt: 0, offset: 0, seq: null, curves: {}, subs: [], timer: null,
    inflight: false, fails: 0, started: false};

  function serverNow() { return Date.now() / 1000 + S.offset; }
  function state() {
    return {status: S.status, error: S.error, live: S.live, seq: S.seq,
      okAt: S.okAt, checkedAt: S.checkedAt, serverNow: serverNow(),
      curves: S.curves};
  }
  function emit() {
    var st = state();
    S.subs.slice().forEach(function (cb) { try { cb(st); } catch (e) { /* a bad subscriber never breaks the loop */ } });
  }
  function schedule(ms) {
    clearTimeout(S.timer);
    if (!S.subs.length) { S.started = false; return; }
    S.timer = setTimeout(tick, ms);
  }
  function nextDelay() {
    var base = ((S.live && S.live.poll_after_s) || 5) * 1000;
    return S.fails ? Math.min(60000, base * Math.pow(2, S.fails)) : base;
  }
  function tick() {
    if (S.inflight) { return; }
    if (document.hidden) { schedule(nextDelay()); return; }
    S.inflight = true;
    var h = {Accept: 'application/json'};
    if (S.etag) { h['If-None-Match'] = S.etag; }
    var sent = Date.now();
    fetch(LIVE, {credentials: 'same-origin', cache: 'no-store', headers: h})
      .then(function (r) {
        if (r.status === 304) { return {code: 304}; }
        if (r.status === 401 || r.status === 403) { return {code: r.status}; }
        if (!r.ok) { return {code: r.status, err: 'HTTP ' + r.status}; }
        return r.json().then(function (j) { return {code: 200, j: j, etag: r.headers.get('ETag')}; });
      })
      .then(function (got) {
        var now = Date.now();
        if (got.code === 304) {
          S.checkedAt = now; S.status = 'OK'; S.error = null; S.fails = 0;
        } else if (got.code === 200 && got.j && got.j.schema === 'bt.equity.v1') {
          var changed = got.j.seq !== S.seq || got.j.etag !== (S.live && S.live.etag);
          S.live = got.j; S.etag = got.etag || got.j.etag; S.seq = got.j.seq;
          S.offset = (got.j.computed_at || (sent + now) / 2000) - (sent + now) / 2000;
          S.okAt = S.checkedAt = now; S.status = 'OK'; S.error = null; S.fails = 0;
          if (changed) { refreshCurves(true); }
        } else if (got.code === 401 || got.code === 403) {
          S.status = 'SIGNED_OUT'; S.live = null; S.etag = null; S.curves = {};
          S.error = 'Sign in to Command to see live equity.'; S.fails = 0;
        } else {
          S.status = 'ERROR'; S.fails++;
          S.error = got.err || 'The equity feed returned an unexpected payload.';
        }
      })
      .catch(function (e) {
        S.status = 'ERROR'; S.fails++;
        S.error = 'The equity feed is unreachable (' + (e && e.message || 'network') + ').';
      })
      .then(function () { S.inflight = false; emit(); schedule(nextDelay()); });
  }

  function curveKey(book, venue, win) { return book + '|' + (venue || '') + '|' + win; }
  var wanted = {};   // keys some mount currently shows
  function fetchCurve(book, venue, win, force) {
    var k = curveKey(book, venue, win), c = S.curves[k];
    if (c && c.inflight) { return; }
    if (c && !force && Date.now() - c.at < CURVE_REFRESH_MS) { return; }
    if (c && force && Date.now() - c.at < 15000) { return; }
    S.curves[k] = c = c || {at: 0, data: null, etag: null};
    c.inflight = true;
    var q = '?book=' + book + (venue ? '&venue=' + venue : '') + '&window=' + win;
    var h = {Accept: 'application/json'};
    if (c.etag) { h['If-None-Match'] = c.etag; }
    fetch(CURVE + q, {credentials: 'same-origin', cache: 'no-store', headers: h})
      .then(function (r) {
        if (r.status === 304) { return null; }
        if (!r.ok) { throw new Error('HTTP ' + r.status); }
        return r.json().then(function (j) { c.etag = r.headers.get('ETag') || j.etag; return j; });
      })
      .then(function (j) { if (j) { c.data = j; c.err = null; } c.at = Date.now(); })
      .catch(function (e) { c.err = e.message; c.at = Date.now(); })
      .then(function () { c.inflight = false; emit(); });
  }
  function refreshCurves(force) {
    Object.keys(wanted).forEach(function (k) {
      if (!wanted[k]) { return; }
      var p = k.split('|'); fetchCurve(p[0], p[1] || null, p[2], force);
    });
  }
  setInterval(function () { if (S.subs.length && !document.hidden) { refreshCurves(false); } }, CURVE_REFRESH_MS);

  function subscribe(cb) {
    S.subs.push(cb);
    if (!S.started) { S.started = true; tick(); } else { try { cb(state()); } catch (e) { /* ignore */ } }
    return function () {
      S.subs = S.subs.filter(function (x) { return x !== cb; });
      if (!S.subs.length) { clearTimeout(S.timer); S.started = false; }
    };
  }
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && S.subs.length) { tick(); }
  });

  // ── the account model the views draw ───────────────────────────────
  function accountsOf(live) {
    if (!live) { return null; }
    var v = (live.actual && live.actual.venues) || {};
    return {paper: live.paper, polymarket_us: v.polymarket_us, kalshi: v.kalshi};
  }
  function curveFor(book, venue, win) {
    var c = S.curves[curveKey(book, venue, win)];
    return c ? c : null;
  }

  // ── the odometer: digits roll only when the value genuinely changes ─
  function odoHTML(text) {
    var out = '';
    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      if (ch >= '0' && ch <= '9') {
        out += '<span class="ew-d" data-i="' + i + '"><span class="ew-strip" style="transform:translateY(-' +
          ch + '0%)">' + '0123456789'.split('').map(function (n) { return '<span>' + n + '</span>'; }).join('') +
          '</span></span>';
      } else {
        out += '<span class="ew-c">' + esc(ch) + '</span>';
      }
    }
    return out;
  }
  function setOdo(node, value, animate) {
    var text = fin(value) ? USD.format(value) : '—';
    node.setAttribute('role', 'img');
    node.setAttribute('aria-label', text);
    var prev = node.getAttribute('data-text');
    if (prev === text) { return; }
    var shape = function (t) { return (t || '').replace(/\d/g, '0'); };
    if (!animate || REDUCED || !prev || shape(prev) !== shape(text)) {
      node.innerHTML = '<span class="ew-odo-in" aria-hidden="true">' + odoHTML(text) + '</span>';
      node.setAttribute('data-text', text);
      return;
    }
    var strips = node.querySelectorAll('.ew-d');
    var k = 0;
    for (var i = 0; i < text.length; i++) {
      if (text[i] >= '0' && text[i] <= '9') {
        var d = strips[k++];
        if (d && prev[i] !== text[i]) {
          d.firstChild.style.transform = 'translateY(-' + text[i] + '0%)';
        }
      }
    }
    node.setAttribute('data-text', text);
  }

  // ── the curve: a step line through recorded points ─────────────────
  function chartModel(acct, curve, now) {
    var pts = (curve && curve.points) || [];
    var since = ts(curve && curve.since), until = now;
    var series = pts.map(function (p) { return {t: p.t, v: p.v, cause: p.cause}; });
    var ok = acct && acct.status === 'OK', stale = acct && acct.status === 'STALE';
    var eq = acct && acct.equity_usd;
    var end = null;
    if (fin(eq) && (ok || stale)) {
      var last = series.length ? series[series.length - 1] : null;
      var changeAt = ts(acct.last_change_at) || ts(acct.source_at);
      if (ok) {
        // the live value, placed at its last genuine change, held to now
        var at = Math.max(last ? last.t : (since || now), changeAt || 0);
        if (!last || last.v !== eq) { series.push({t: Math.min(at, now), v: eq, cause: 'LIVE'}); }
        end = {t: now, v: eq, live: true};
      } else {
        // STALE: FROZEN at the last genuine input; the line does not reach now
        var st = Math.min(changeAt || (last ? last.t : now), now);
        if (!last || last.v !== eq) { series.push({t: Math.max(st, last ? last.t : st), v: eq, cause: 'LAST_READ'}); }
        end = {t: series.length ? series[series.length - 1].t : st, v: eq, live: false};
      }
    }
    if (!since) { since = series.length ? series[0].t : now - 86400; }
    return {series: series, since: since, until: until, end: end};
  }
  function renderChart(box, acct, curve, win, gid) {
    var now = serverNow();
    var m = chartModel(acct, curve && curve.data, now);
    var vals = m.series.filter(function (p) { return fin(p.v); }).map(function (p) { return p.v; });
    if (!vals.length) {
      var why = curve && curve.err ? 'curve unavailable: ' + curve.err
        : curve && curve.data && curve.data.why ? curve.data.why
          : curve && curve.inflight ? 'reading the recorded series…' : 'no recorded equity in this window';
      box.innerHTML = '<div class="ew-chart-empty">' + esc(why) + '</div>';
      return;
    }
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var span = hi - lo, pad = Math.max(span * 0.18, Math.max(Math.abs(hi) * 0.0004, 0.5));
    lo -= pad; hi += pad;
    var W = 1000, Hh = 300;
    var X = function (t) { return (Math.max(m.since, Math.min(m.until, t)) - m.since) / Math.max(1, m.until - m.since) * W; };
    var Y = function (v) { return Hh - (v - lo) / (hi - lo) * Hh; };
    var d = '', area = '', prev = null, startX = null, open = false;
    m.series.forEach(function (p) {
      if (!fin(p.v)) {
        if (open && prev) { d += 'H' + X(p.t).toFixed(1); area += 'H' + X(p.t).toFixed(1) + 'V' + Hh + 'H' + startX + 'Z'; }
        open = false; prev = null; return;
      }
      var x = X(p.t).toFixed(1), y = Y(p.v).toFixed(1);
      if (!open) { d += 'M' + x + ' ' + y; area += 'M' + x + ' ' + Hh + 'V' + y; startX = x; open = true; }
      else { d += 'H' + x + 'V' + y; area += 'H' + x + 'V' + y; }
      prev = p;
    });
    if (open && m.end) {
      var ex = X(m.end.t).toFixed(1);
      d += 'H' + ex; area += 'H' + ex + 'V' + Hh + 'H' + startX + 'Z';
    } else if (open) {
      area += 'V' + Hh + 'H' + startX + 'Z';
    }
    var grid = '';
    for (var g = 1; g <= 3; g++) { grid += '<line x1="0" x2="' + W + '" y1="' + (Hh * g / 4) + '" y2="' + (Hh * g / 4) + '"/>'; }
    var ylab = [0.25, 0.5, 0.75].map(function (f) {
      var v = hi - (hi - lo) * f;
      return '<span class="ew-ylab" style="top:' + (f * 100) + '%">' + esc(span > 50 ? USD0.format(v) : USD.format(v)) + '</span>';
    }).join('');
    var tl = function (t) {
      var o = win === '1d' ? {hour: '2-digit', minute: '2-digit', hour12: false} : {month: 'short', day: 'numeric'};
      o.timeZone = ET.timeZone; return new Date(t * 1000).toLocaleString('en-US', o);
    };
    var xlab = '<span class="ew-xlab" style="left:0">' + esc(tl(m.since)) + '</span><span class="ew-xlab" style="left:50%;transform:translateX(-50%)">' +
      esc(tl((m.since + m.until) / 2)) + '</span><span class="ew-xlab" style="right:0">now</span>';
    var endHTML = '';
    if (m.end) {
      var px = X(m.end.t) / W * 100, py = Y(m.end.v) / Hh * 100;
      endHTML = '<span class="ew-end ' + (m.end.live ? 'live' : 'frozen') + '" style="left:' + px + '%;top:' + py + '%"' +
        ' title="' + esc((m.end.live ? 'Live value ' : 'Frozen at last genuine input ') + money(m.end.v)) + '"></span>' +
        (m.end.live ? '' : '<span class="ew-frozen-tag' + (px > 55 ? ' left' : '') + '" style="left:' + px + '%;top:' + py + '%">FROZEN · STALE</span>');
    }
    box.innerHTML = '<div class="ew-plot" role="img" aria-label="' + esc('Equity, ' + win + ': from ' + money(vals[0]) + ' to ' + money(vals[vals.length - 1]) + ', ' + vals.length + ' recorded changes') + '">' +
      '<svg viewBox="0 0 ' + W + ' ' + Hh + '" preserveAspectRatio="none" aria-hidden="true">' +
      '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="ew-g0"/><stop offset="1" class="ew-g1"/></linearGradient></defs>' +
      '<g class="ew-grid">' + grid + '</g><path class="ew-area" fill="url(#' + gid + ')" d="' + area + '"/>' +
      '<path class="ew-line" d="' + d + '" vector-effect="non-scaling-stroke"/></svg>' +
      ylab + endHTML + '<span class="ew-cross" hidden></span><span class="ew-tip" hidden></span></div>' +
      '<div class="ew-xaxis">' + xlab + '</div>';
    var plot = box.firstChild, cross = plot.querySelector('.ew-cross'), tip = plot.querySelector('.ew-tip');
    var pts = m.series.slice();
    if (m.end) { pts.push({t: m.end.t, v: m.end.v, cause: m.end.live ? 'LIVE' : 'FROZEN'}); }
    function at(ev) {
      var r = plot.getBoundingClientRect();
      var cx = ((ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left) / r.width;
      var t = m.since + cx * (m.until - m.since), hit = null;
      for (var i = 0; i < pts.length; i++) { if (pts[i].t <= t) { hit = pts[i]; } }
      if (!hit || cx < 0 || cx > 1 || (m.end && t > m.end.t + 1 && !m.end.live)) { cross.hidden = tip.hidden = true; return; }
      cross.hidden = tip.hidden = false;
      cross.style.left = (cx * 100) + '%';
      tip.style.left = Math.min(Math.max(cx * 100, 12), 88) + '%';
      tip.innerHTML = '<b>' + esc(money(hit.v)) + '</b><span>' + esc(clock(t, true)) + '</span><span>' +
        esc(hit.cause === 'LEDGER' ? 'ledger moved (fill / settlement)' : hit.cause === 'MARK' ? 'marks moved' :
          hit.cause === 'ACCOUNT' ? 'venue account read changed' : hit.cause === 'GAP' ? 'incomplete marks: no stated equity' :
            hit.cause === 'LIVE' ? 'live value' : hit.cause === 'FROZEN' || hit.cause === 'LAST_READ' ? 'last genuine input (STALE)' :
              hit.cause === 'WINDOW_OPEN' ? 'value at window open' : 'first record') + '</span>';
    }
    plot.addEventListener('mousemove', at);
    plot.addEventListener('touchstart', at, {passive: true});
    plot.addEventListener('mouseleave', function () { cross.hidden = tip.hidden = true; });
  }

  // ── one account panel ──────────────────────────────────────────────
  var STATUS_TEXT = {OK: 'LIVE', STALE: 'STALE', UNAVAILABLE: 'UNAVAILABLE'};
  function chip(label, value, title, cls) {
    return '<span class="ew-chip ' + (cls || '') + '" title="' + esc(title || '') + '"><small>' + esc(label) + '</small><b>' + value + '</b></span>';
  }
  function pnlChip(label, n, title, extra) {
    return chip(label, '<i aria-hidden="true">' + arrow(n) + '</i>' + esc(signed(n)) + (extra ? ' <em>' + esc(extra) + '</em>' : ''),
      title, 'pnl ' + dir(n));
  }
  function bar(label, value, of, title, cls) {
    var w = fin(value) && fin(of) && of > 0 ? Math.max(0, Math.min(100, value / of * 100)) : 0;
    return '<div class="ew-bar ' + (cls || '') + '" title="' + esc(title || '') + '"><div class="ew-bar-h"><span>' + esc(label) + '</span><b>' + esc(money(value)) +
      '</b></div><div class="ew-track"><span style="width:' + w.toFixed(2) + '%"></span></div></div>';
  }
  function asOf(acct) {
    var t = ts(acct.source_at);
    return t ? clock(t, true) : '—';
  }
  function accountPanel(acct, view, opts) {
    // view = {key, book, venue, title, win}
    var st = acct ? acct.status : 'UNAVAILABLE';
    var lane = (acct && acct.lane) || {};
    var lanePill = lane.state ? '<span class="ew-pill lane ' + esc(String(lane.state).toLowerCase()) + '" title="' +
      esc('Lane state from the control record' + (lane.stop_done_at ? ' · stop completed ' + lane.stop_done_at : '')) + '">' + esc(lane.state.replace(/_/g, ' ')) + '</span>' : '';
    var head = '<div class="ew-acct-head"><span class="ew-acct-title">' + esc(view.title) + '</span>' + lanePill +
      '<span class="ew-status s-' + esc(st.toLowerCase()) + '" data-ew="status"><i aria-hidden="true"></i>' + esc(STATUS_TEXT[st] || st) +
      ' <span data-ew="age"></span></span></div>';
    if (!acct || st === 'UNAVAILABLE') {
      return head + '<div class="ew-unavail" role="status"><b>UNAVAILABLE</b><p>' + esc(acct ? acct.why : 'No record of this account was returned.') +
        '</p><small>Source: ' + esc(acct && acct.source || '—') + '</small></div>';
    }
    var src = 'Source: ' + (acct.source || '—') + ' · as of ' + asOf(acct);
    var dc = acct.day_change || {}, sc = acct.session_change || {};
    var exp = acct.exposure || {}, op = acct.open_positions || {};
    var tr = acct.equity_treatment || {};
    var banner = st === 'STALE' ? '<div class="ew-stale-banner" role="status"><b>STALE</b> <span data-ew="stale-age"></span> · ' + esc(acct.why || '') + '</div>' : '';
    var note = tr.unmarked_positions ? '<p class="ew-note warn" title="' + esc(tr.text || '') + '">Includes ' + tr.unmarked_positions +
      ' UNMARKED position' + (tr.unmarked_positions > 1 ? 's' : '') + ' at cost (' + esc(money(tr.unmarked_carried_at_cost_usd)) + ') · marked-only ' + esc(money(tr.equity_marked_only_usd)) + '</p>' : '';
    var equityTitle = 'Equity = ' + (tr.text || 'cash + marked value') + ' · ' + src;
    var big = '<div class="ew-equity-row"><div class="ew-equity" data-ew="equity" title="' + esc(equityTitle) + '"></div>' +
      '<div class="ew-day ' + dir(dc.usd) + '" title="' + esc('Session day (America/New_York) change · basis ' + (dc.basis || '—') + (dc.basis_at ? ' at ' + dc.basis_at : '') + (dc.why ? ' · ' + dc.why : '')) + '">' +
      (fin(dc.usd) ? '<i aria-hidden="true">' + arrow(dc.usd) + '</i>' + esc(signed(dc.usd)) + ' <span>' + esc(pct(dc.pct)) + '</span><small>today</small>' :
        '<small>today · ' + esc(dc.why ? 'no basis' : '—') + '</small>') + '</div></div>';
    var chips = '<div class="ew-chips">' +
      pnlChip('Realized', acct.realized_pnl_usd, 'Realized P&L · ' + (acct.realized_basis || 'ledger sums') + ' · ' + src) +
      (fin(acct.unrealized_pnl_usd) ? pnlChip('Unrealized', acct.unrealized_pnl_usd, 'Unrealized P&L · ' + (acct.unrealized_basis || '') + ' · marks as of ' + ((acct.marks_as_of || {}).newest_at || '—')) :
        chip('Unrealized', 'UNMARKED', acct.unrealized_basis || 'no mark', 'muted')) +
      (fin(sc.usd) ? pnlChip('Session', sc.usd, 'Change since ' + (sc.basis || 'session start') + (sc.basis_at ? ' (' + sc.basis_at + ')' : ''), pct(sc.pct)) :
        chip('Session', '—', sc.why || 'no session basis', 'muted')) +
      '</div>';
    if (opts.compact) {
      return head + banner + big + chips + '<div class="ew-chart compact" data-ew="chart"></div>' +
        '<div class="ew-foot"><span>' + esc(acct.book === 'PAPER' ? 'Paper ledger + observed books' : 'Venue account read') + '</span><span data-ew="asof">' + esc(asOf(acct)) + '</span></div>';
    }
    var eq = acct.equity_usd;
    var bars = '<div class="ew-bars">' +
      bar('Cash', acct.cash_usd, eq, 'Cash · ' + src) +
      bar('Available', acct.available_usd, eq, fin(acct.available_usd) ? 'Available capital (cash less reservations) · ' + src : (acct.available_why || 'not recorded'), 'avail') +
      bar('Exposure · cost', exp.cost_basis_usd, eq, 'Open cost basis · ' + src, 'expo') +
      bar('Exposure · marked', exp.marked_value_usd, eq, 'Marked value of marked open positions · marks as of ' + ((acct.marks_as_of || {}).newest_at || '—'), 'expo marked') +
      '</div>';
    var ma = acct.marks_as_of || {};
    var stats = '<dl class="ew-stats">' +
      '<div><dt>Open positions</dt><dd>' + (fin(op.count) ? op.count : '—') + '<small>' + (fin(op.count) ? (op.marked || 0) + ' marked · ' + (op.unmarked || 0) + ' unmarked' + (op.stale_marks ? ' · ' + op.stale_marks + ' stale' : '') : '') + '</small></dd></div>' +
      (acct.book === 'ACTUAL' ? '<div title="' + esc(acct.resting_orders_note || '') + '"><dt>Resting orders</dt><dd>' + (fin(acct.resting_orders) ? acct.resting_orders : '—') + '<small>not fills</small></dd></div>' :
        '<div><dt>Reserved</dt><dd>' + esc(money(acct.reserved_usd)) + '<small>part of cash</small></dd></div>') +
      '<div title="' + esc((ma.method || '') + (ma.oldest_at ? ' · oldest mark ' + ma.oldest_at : '')) + '"><dt>Last mark</dt><dd>' + esc(ma.newest_at ? clock(ts(ma.newest_at), false) : '—') + '<small>' +
        esc(ma.newest_at ? dayOf(ts(ma.newest_at)) + (ma.oldest_at && ma.oldest_at !== ma.newest_at ? ' · oldest ' + clock(ts(ma.oldest_at), false) : '') : '') +
        ' · ' + esc(acct.book === 'ACTUAL' ? 'venue cashValue at the account read' : 'top-of-book exit price') + '</small></dd></div>' +
      '</dl>';
    var toggles = '<div class="ew-win" role="group" aria-label="Chart window">' + WINDOWS.map(function (w) {
      return '<button type="button" data-ew-win="' + w + '" aria-pressed="' + (w === view.win) + '">' + w.toUpperCase() + '</button>';
    }).join('') + '</div>';
    return head + banner + big + note + chips +
      '<div class="ew-chart-wrap">' + toggles + '<div class="ew-chart" data-ew="chart"></div></div>' + bars + stats +
      '<div class="ew-foot"><span title="' + esc(acct.source || '') + '">' + esc((acct.source || '').split(' (')[0]) + '</span><span>as of <b data-ew="asof">' + esc(asOf(acct)) + '</b></span></div>';
  }

  // ── a mount ────────────────────────────────────────────────────────
  var STORE_KEY = 'btew.window.v1';
  function savedWin(k) { try { var o = JSON.parse(localStorage.getItem(STORE_KEY) || '{}'); return WINDOWS.indexOf(o[k]) >= 0 ? o[k] : '1d'; } catch (e) { return '1d'; } }
  function saveWin(k, w) { try { var o = JSON.parse(localStorage.getItem(STORE_KEY) || '{}'); o[k] = w; localStorage.setItem(STORE_KEY, JSON.stringify(o)); } catch (e) { /* per-viewer convenience only */ } }

  var VIEWS = [
    {key: 'paper', book: 'PAPER', venue: null, title: 'Account equity'},
    {key: 'polymarket_us', book: 'ACTUAL', venue: 'polymarket_us', title: 'Polymarket US · execution mirror'},
    {key: 'kalshi', book: 'ACTUAL', venue: 'kalshi', title: 'Kalshi'}
  ];

  function mount(el, opts) {
    if (!el) { throw new Error('BTEquityWall.mount needs an element'); }
    opts = opts || {};
    var id = ++uid, compact = !!opts.compact;
    var views = VIEWS.map(function (v) { return Object.assign({}, v, {win: compact ? '1d' : savedWin(v.key)}); });
    var prev = {}, sig = {}, flashT = {};
    el.classList.add('btew-host');
    el.innerHTML = '<section class="btew' + (compact ? ' compact' : '') + '" aria-label="Live equity: paper experiment and small live capital, shown separately">' +
      '<div class="btew-grid">' +
      '<article class="ew-card paper" data-ew-card="paper"><header class="ew-card-head"><div><span class="ew-eyebrow">Fictional money · simulated execution</span>' +
      '<h2>$500,000 PAPER EXPERIMENT</h2></div><span class="ew-badge paper">PAPER</span></header><div class="ew-body" data-ew-acct="paper"></div></article>' +
      '<article class="ew-card live" data-ew-card="live"><header class="ew-card-head"><div><span class="ew-eyebrow">Real money · 1:1,000 · each venue on its own</span>' +
      '<h2>SMALL LIVE CAPITAL</h2></div><span class="ew-badge live">LIVE $</span></header>' +
      '<p class="ew-sep-note">Venues are separate sub-accounts and are never added together. Paper is never added to live.</p>' +
      '<div class="ew-venue" data-ew-acct="polymarket_us"></div><div class="ew-venue" data-ew-acct="kalshi"></div></article>' +
      '</div><div class="btew-feed" data-ew="feed" role="status" aria-live="polite"></div></section>';
    var root = el.firstChild;

    root.addEventListener('click', function (e) {
      var b = e.target.closest('[data-ew-win]'); if (b) {
        var box = b.closest('[data-ew-acct]'), key = box && box.getAttribute('data-ew-acct');
        var v = views.filter(function (x) { return x.key === key; })[0]; if (!v) { return; }
        unwant(v); v.win = b.getAttribute('data-ew-win'); saveWin(key, v.win); sig[key] = null; want(v); draw(state(), true);
        return;
      }
      var s = e.target.closest('[data-ew-signin]');
      if (s && window.BTUnlock && typeof window.BTUnlock.open === 'function') { window.BTUnlock.open(); }
    });

    function want(v) {
      var k = curveKey(v.book, v.venue, v.win);
      wanted[k] = (wanted[k] || 0) + 1;
      fetchCurve(v.book, v.venue, v.win, false);
    }
    function unwant(v) {
      var k = curveKey(v.book, v.venue, v.win);
      if (wanted[k]) { wanted[k]--; }
    }

    function draw(st, structural) {
      var accts = accountsOf(st.live);
      var feed = root.querySelector('[data-ew="feed"]');
      if (!accts) {
        root.classList.toggle('signed-out', st.status === 'SIGNED_OUT');
        var msg = st.status === 'SIGNED_OUT' ? '<b>Sign in required.</b> Live equity is shown only to an authenticated Command session. <button type="button" data-ew-signin>Sign in</button>'
          : st.status === 'ERROR' ? '<b>Equity feed unavailable.</b> ' + esc(st.error || '') : 'Reading live equity…';
        views.forEach(function (v) {
          var box = root.querySelector('[data-ew-acct="' + v.key + '"]');
          if (box) { box.innerHTML = '<div class="ew-unavail pending" role="status"><b>' + (st.status === 'SIGNED_OUT' ? 'SIGN IN' : st.status === 'ERROR' ? 'UNAVAILABLE' : 'READING') + '</b><p>' + msg + '</p></div>'; }
          sig[v.key] = null;
        });
        feed.innerHTML = '';
        return;
      }
      root.classList.remove('signed-out');
      views.forEach(function (v) {
        var a = accts[v.key], box = root.querySelector('[data-ew-acct="' + v.key + '"]');
        if (!box) { return; }
        var curve = curveFor(v.book, v.venue, v.win);
        var s = JSON.stringify([a && a.status, a && a.why, a && a.equity_usd, a && a.day_change, a && a.realized_pnl_usd,
          a && a.unrealized_pnl_usd, a && a.session_change, a && a.open_positions && [a.open_positions.count, a.open_positions.unmarked, a.open_positions.stale_marks],
          a && a.cash_usd, a && a.available_usd, a && a.exposure, a && a.source_at, a && a.marks_as_of && a.marks_as_of.newest_at,
          a && a.lane && a.lane.state, v.win, curve && curve.at, curve && curve.err]);
        if (sig[v.key] !== s || structural) {
          var oldEq = prev[v.key];
          box.className = box.className.replace(/\bs-(ok|stale|unavailable)\b/g, '').trim() + ' s-' + (a ? a.status : 'UNAVAILABLE').toLowerCase();
          box.innerHTML = accountPanel(a, v, {compact: compact});
          var eqNode = box.querySelector('[data-ew="equity"]');
          if (eqNode) {
            // first paint at the OLD value, then roll to the new one
            if (fin(oldEq)) { setOdo(eqNode, oldEq, false); }
            var newEq = a && a.equity_usd;
            if (fin(oldEq) && fin(newEq) && oldEq !== newEq) {
              // eslint-disable-next-line no-unused-expressions
              eqNode.offsetWidth;
              setOdo(eqNode, newEq, true);
              var card = box;
              card.classList.remove('flash-up', 'flash-down');
              void card.offsetWidth;
              card.classList.add(newEq > oldEq ? 'flash-up' : 'flash-down');
              clearTimeout(flashT[v.key]);
              flashT[v.key] = setTimeout(function () { card.classList.remove('flash-up', 'flash-down'); }, 1600);
            } else {
              setOdo(eqNode, newEq, false);
            }
          }
          var chart = box.querySelector('[data-ew="chart"]');
          if (chart) { renderChart(chart, a, curve, v.win, 'ewg' + id + v.key); }
          prev[v.key] = a ? a.equity_usd : null;
          sig[v.key] = s;
        }
      });
      root.classList.toggle('feed-down', st.status === 'ERROR');
      feed.innerHTML = st.status === 'ERROR' ? '<b>FEED INTERRUPTED</b> · showing the last good read from ' + esc(ageText((Date.now() - st.okAt) / 1000)) + ' ago · ' + esc(st.error || '') : '';
      ages();
    }

    // ages are derived from the timestamps every second; no value moves
    function ages() {
      var accts = accountsOf(S.live); if (!accts) { return; }
      var now = serverNow();
      views.forEach(function (v) {
        var a = accts[v.key], box = root.querySelector('[data-ew-acct="' + v.key + '"]');
        if (!a || !box) { return; }
        var t = ts(a.source_at), age = t ? now - t : null;
        var n = box.querySelector('[data-ew="age"]');
        if (n) { n.textContent = a.status === 'UNAVAILABLE' ? '' : '· ' + ageText(age); }
        var sa = box.querySelector('[data-ew="stale-age"]');
        if (sa) { sa.textContent = 'last update ' + ageText(age) + ' ago'; }
      });
    }

    var unsub = subscribe(function (st) { draw(st, false); });
    views.forEach(want);
    var tick1 = setInterval(ages, 1000);
    draw(state(), true);
    return {destroy: function () {
      unsub(); clearInterval(tick1);
      views.forEach(unwant);
      el.innerHTML = ''; el.classList.remove('btew-host');
    }};
  }

  // ── paint: the same real values onto a 2D canvas (a WebGL screen) ───
  function paint(canvas, o) {
    o = o || {};
    var ctx = canvas && canvas.getContext && canvas.getContext('2d'); if (!ctx) { return false; }
    var W = canvas.width, H = canvas.height, accts = accountsOf(S.live);
    var key = o.book === 'ACTUAL' ? (o.venue || 'polymarket_us') : 'paper';
    var a = accts && accts[key], isPaper = key === 'paper';
    var accent = isPaper ? '#6fb6ff' : '#f0c26e';
    ctx.save();
    ctx.fillStyle = '#0a1018'; ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = accent; ctx.fillRect(0, 0, W, Math.max(4, H * 0.012));
    var u = H / 100;
    ctx.fillStyle = '#9fb0c6'; ctx.font = '600 ' + (4.2 * u) + 'px Inter,system-ui,sans-serif';
    ctx.fillText(isPaper ? '$500,000 PAPER EXPERIMENT · SIMULATED' : 'SMALL LIVE CAPITAL · ' + (key === 'kalshi' ? 'KALSHI' : 'POLYMARKET US') + ' · REAL MONEY', 4 * u, 10 * u);
    var st = !accts ? (S.status === 'SIGNED_OUT' ? 'SIGN IN' : S.status === 'ERROR' ? 'UNAVAILABLE' : 'READING') : a ? a.status : 'UNAVAILABLE';
    var stColor = st === 'OK' ? '#50d8ac' : st === 'STALE' ? '#e9be74' : '#ff8395';
    ctx.fillStyle = stColor; ctx.font = '700 ' + (4 * u) + 'px ui-monospace,monospace';
    var stText = (st === 'OK' ? 'LIVE' : st) + (a && a.source_at && st !== 'UNAVAILABLE' ? ' · ' + ageText(serverNow() - ts(a.source_at)) : '');
    ctx.textAlign = 'right'; ctx.fillText(stText, W - 4 * u, 10 * u); ctx.textAlign = 'left';
    if (!a || st === 'UNAVAILABLE' || !fin(a.equity_usd)) {
      ctx.fillStyle = '#ff8395'; ctx.font = '700 ' + (11 * u) + 'px ui-monospace,monospace'; ctx.fillText('UNAVAILABLE', 4 * u, 36 * u);
      ctx.fillStyle = '#aab6c8'; ctx.font = (3.6 * u) + 'px Inter,system-ui,sans-serif';
      var why = String((a && a.why) || S.error || 'no record'), line = '', y = 48 * u;
      why.split(' ').forEach(function (w) { if (ctx.measureText(line + w).width > W - 8 * u) { ctx.fillText(line, 4 * u, y); y += 5 * u; line = ''; } line += w + ' '; });
      ctx.fillText(line, 4 * u, y);
      ctx.restore(); return true;
    }
    if (st === 'STALE') { ctx.globalAlpha = 0.72; }
    ctx.fillStyle = '#eef3fb'; ctx.font = '600 ' + (15 * u) + 'px ui-monospace,SFMono-Regular,monospace';
    ctx.fillText(money(a.equity_usd), 4 * u, 34 * u);
    var dc = (a.day_change || {}).usd;
    ctx.fillStyle = dc > 0 ? '#50d8ac' : dc < 0 ? '#ff8395' : '#9fb0c6'; ctx.font = '600 ' + (4.6 * u) + 'px ui-monospace,monospace';
    ctx.fillText(fin(dc) ? arrow(dc) + ' ' + signed(dc) + ' today' : 'today: no basis', 4 * u, 44 * u);
    ctx.fillStyle = '#9fb0c6'; ctx.font = (3.6 * u) + 'px ui-monospace,monospace';
    ctx.fillText('Realized ' + signed(a.realized_pnl_usd) + '   Unrealized ' + (fin(a.unrealized_pnl_usd) ? signed(a.unrealized_pnl_usd) : 'UNMARKED') +
      '   Open ' + ((a.open_positions || {}).count == null ? '—' : a.open_positions.count), 4 * u, 52 * u);
    var c = curveFor(isPaper ? 'PAPER' : 'ACTUAL', isPaper ? null : key, '1d');
    var m = chartModel(a, c && c.data, serverNow());
    var vals = m.series.filter(function (p) { return fin(p.v); }).map(function (p) { return p.v; });
    if (vals.length) {
      var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals), pad = Math.max((hi - lo) * 0.2, Math.abs(hi) * 0.0004, 0.5);
      lo -= pad; hi += pad;
      var x0 = 4 * u, x1 = W - 4 * u, y0 = 60 * u, y1 = H - 6 * u;
      var X = function (t) { return x0 + (Math.max(m.since, Math.min(m.until, t)) - m.since) / Math.max(1, m.until - m.since) * (x1 - x0); };
      var Y = function (v) { return y1 - (v - lo) / (hi - lo) * (y1 - y0); };
      ctx.strokeStyle = 'rgba(160,180,210,.12)'; ctx.lineWidth = 1;
      for (var g = 1; g < 4; g++) { ctx.beginPath(); ctx.moveTo(x0, y0 + (y1 - y0) * g / 4); ctx.lineTo(x1, y0 + (y1 - y0) * g / 4); ctx.stroke(); }
      ctx.strokeStyle = st === 'STALE' ? '#8a96a8' : accent; ctx.lineWidth = Math.max(2, u * 0.6); ctx.beginPath();
      var open = false, lx = 0, ly = 0;
      m.series.forEach(function (p) {
        if (!fin(p.v)) { open = false; return; }
        if (!open) { ctx.moveTo(X(p.t), Y(p.v)); open = true; } else { ctx.lineTo(X(p.t), ly); ctx.lineTo(X(p.t), Y(p.v)); }
        ly = Y(p.v); lx = X(p.t);
      });
      if (open && m.end) { ctx.lineTo(X(m.end.t), ly); lx = X(m.end.t); }
      ctx.stroke();
      if (m.end) { ctx.fillStyle = m.end.live ? accent : '#8a96a8'; ctx.beginPath(); ctx.arc(lx, ly, Math.max(3, u), 0, Math.PI * 2); ctx.fill(); }
    }
    ctx.restore();
    return true;
  }

  window.BTEquityWall = {mount: mount, subscribe: subscribe, state: state, paint: paint,
    refresh: function () { tick(); }, version: 'equity-wall/1'};

  // ── the homepage: at the top, above the paper experiment ───────────
  // app.js re-renders the overview and calls BTPaper.mount(slot) each time;
  // the wall's host is created ONCE and re-inserted above that slot, so its
  // state survives every re-render. No slot (demo mode, other views) -> no
  // wall: the wall never shows illustrative data.
  var host = null, handle = null;
  function place(slot) {
    if (!slot || !slot.parentNode) { return; }
    if (!host) {
      host = document.createElement('div');
      host.className = 'btew-home';
      handle = mount(host, {compact: false});
    }
    if (host.nextSibling !== slot || host.parentNode !== slot.parentNode) { slot.parentNode.insertBefore(host, slot); }
  }
  if (window.BTPaper && typeof window.BTPaper.mount === 'function') {
    var orig = window.BTPaper.mount;
    window.BTPaper.mount = function (slot) { var r = orig.apply(this, arguments); try { place(slot); } catch (e) { /* the wall never breaks the page */ } return r; };
    place(document.getElementById('paper-home-slot'));
  }
}());
