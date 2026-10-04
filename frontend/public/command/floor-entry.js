/* THE TRADING FLOOR'S FRONT DOOR on the management homepage. Additive: it
 * wraps BTOffice.render (like management-v6.js) to place one prominent
 * entry above the account, and fills its seven desk lights from
 * GET /api/command/floor -- real states only; before a read, or without a
 * session, the lights are grey and say so. */
(function () {
  'use strict';
  if (!window.BTOffice || !BTOffice.render) return;
  var SEATS = [['derek', 'Derek'], ['karen', 'Karen'], ['scout', 'Scout'], ['eddie', 'Eddie'],
               ['allocator', 'Allocator'], ['audrey', 'Audrey'], ['xavier', 'Xavier']];
  var COLORS = {WORKING_ON: '#50d8ac', REVIEWING: '#6fb6ff', CHALLENGING: '#ff8395', WAITING: '#e9be74',
                IDLE: '#93a3b8', STALE: '#5d6878', NOT_DEPLOYED: '#3d4859'};
  var LABELS = {WORKING_ON: 'working', REVIEWING: 'reviewing', CHALLENGING: 'challenging', WAITING: 'waiting',
                IDLE: 'idle', STALE: 'stale', NOT_DEPLOYED: 'not yet deployed'};
  var last = null, note = 'Reading the floor…';
  function esc(x) { return String(x == null ? '' : x).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); }
  function lights() {
    var by = {};
    ((last && last.agents) || []).forEach(function (a) { by[a.slug] = a; });
    return SEATS.map(function (s) {
      var a = by[s[0]], st = a && COLORS[a.state] ? a.state : null;
      return '<span class="fe-desk" title="' + esc(s[1] + ': ' + (st ? LABELS[st] + (a.state_detail ? ' — ' + a.state_detail : '') : 'no state read')) + '"><i style="background:' + (st ? COLORS[st] : '#2a3442') + '"></i>' + esc(s[1]) + '</span>';
    }).join('');
  }
  function summary() {
    if (!last) return esc(note);
    var c = last.counts || {}, parts = [];
    ['WORKING_ON', 'REVIEWING', 'CHALLENGING', 'WAITING', 'IDLE', 'STALE', 'NOT_DEPLOYED'].forEach(function (k) { if (c[k]) parts.push(c[k] + ' ' + LABELS[k]); });
    return (last.fixture === true ? 'FIXTURE DATA · ' : '') + esc(parts.join(' · ') + ' · ' + (last.edges || []).length + ' collaboration link(s) in the last hour');
  }
  function html() {
    return '<a class="fe-door" href="/floor" aria-label="Enter the trading floor: seven agent desks, live states">' +
      '<span class="fe-glow" aria-hidden="true"></span>' +
      '<span class="fe-copy"><span class="fe-eyebrow">BETTOR HEADQUARTERS · NEW</span><strong>Enter the Trading Floor</strong>' +
      '<span class="fe-sub">Your seven agents at their desks — every desk lit by its recorded state, heartbeat and hand-offs.</span>' +
      '<span class="fe-lights" id="fe-lights">' + lights() + '</span><span class="fe-sum" id="fe-sum">' + summary() + '</span></span>' +
      '<span class="fe-cta">Enter the floor <b aria-hidden="true">→</b></span></a>';
  }
  function refresh() {
    var l = document.getElementById('fe-lights'), s = document.getElementById('fe-sum');
    if (l) l.innerHTML = lights();
    if (s) s.innerHTML = summary();
  }
  function read() {
    if (document.hidden) return;
    fetch('/api/command/floor', {credentials: 'same-origin', cache: 'no-store'}).then(function (r) {
      if (r.status === 401 || r.status === 403) { last = null; note = 'Sign in to see the desks’ live states.'; return null; }
      if (r.status === 404) { last = null; note = 'The floor read ships with the next API release; the floor opens with desks unlit.'; return null; }
      if (!r.ok) { note = 'Floor states unavailable (HTTP ' + r.status + ').'; return null; }
      return r.json();
    }).then(function (j) { if (j) { last = j; } refresh(); }).catch(function () { note = 'Floor states unavailable.'; refresh(); });
  }
  var render = BTOffice.render;
  BTOffice.render = function (ctx) {
    var out = render(ctx);
    var anchors = ['<nav class="mg-home-nav"', '<section class="office-capital"'];
    for (var i = 0; i < anchors.length; i++) {
      if (out.indexOf(anchors[i]) >= 0) return out.replace(anchors[i], html() + anchors[i]);
    }
    return out;
  };
  read();
  setInterval(read, 30000);
})();
