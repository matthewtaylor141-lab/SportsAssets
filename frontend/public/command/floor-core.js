/* BETTOR HEADQUARTERS · shared floor model (classic script, no globals but
 * window.BTFloor). Reads GET /api/command/floor (same origin, session
 * cookie, no-store) and describes each agent's REAL state. Nothing here
 * invents activity: an absent read is UNKNOWN/UNAVAILABLE with its reason,
 * an old read is labelled STALE with its age, a fixture payload is
 * labelled FIXTURE on every surface. Used by floor.js and workspace.js. */
(function () {
  'use strict';

  /* THE SEVEN DESKS in the candidate-review order (Derek -> Karen -> Scout
   * -> Eddie -> Allocator -> Audrey -> Xavier). Static identity only:
   * names, colours and links -- never state. */
  var SEATS = [
    {agent: 'DEREK', slug: 'derek', name: 'Derek', short: 'CIO · Alpha',
     role: 'Chief Investment Officer · entry decisions', accent: '#9fe3bf', initial: 'D'},
    {agent: 'KAREN', slug: 'karen', name: 'Karen', short: 'Red team',
     role: 'Red team · evidence challenges (zero authority)', accent: '#ff9a8f', initial: 'K'},
    {agent: 'SCOUT', slug: 'scout', name: 'Scout', short: 'Intelligence',
     role: 'Market intelligence · research shadow', accent: '#f5b072', initial: 'S'},
    {agent: 'EDDIE', slug: 'eddie', name: 'Eddie', short: 'Execution',
     role: 'Head of Execution · shadow only', accent: '#6fe0d2', initial: 'E'},
    {agent: 'CHIEF_ALLOCATOR', slug: 'allocator', name: 'Allie', short: 'Chief Allocator',
     role: 'Chief Allocator · capital allocation · shadow sleeve', accent: '#ecc66d', initial: 'A'},
    {agent: 'AUDREY', slug: 'audrey', name: 'Audrey', short: 'Risk & audit',
     role: 'Risk, audit & management reporting', accent: '#cdb6f6', initial: 'A'},
    {agent: 'XAVIER', slug: 'xavier', name: 'Xavier', short: 'Portfolio Mgmt',
     role: 'Portfolio manager · position management', accent: '#9fd2f2', initial: 'X'}
  ];
  var BY_AGENT = {}, BY_SLUG = {};
  SEATS.forEach(function (s) { BY_AGENT[s.agent] = s; BY_SLUG[s.slug] = s; });

  /* STATE PRESENTATION. `motion` is what the floor may animate for it. */
  var STATES = {
    WORKING_ON:   {label: 'Working',          color: '#50d8ac', tone: 'green',  motion: 'work'},
    REVIEWING:    {label: 'Reviewing',        color: '#6fb6ff', tone: 'blue',   motion: 'review'},
    CHALLENGING:  {label: 'Challenging',      color: '#ff8395', tone: 'red',    motion: 'review'},
    WAITING:      {label: 'Waiting',          color: '#e9be74', tone: 'amber',  motion: 'idle'},
    IDLE:         {label: 'Idle',             color: '#93a3b8', tone: 'slate',  motion: 'idle'},
    STALE:        {label: 'Stale',            color: '#5d6878', tone: 'dim',    motion: 'still'},
    NOT_DEPLOYED: {label: 'Not yet deployed', short: 'Not deployed', color: '#56627a', tone: 'off',    motion: 'still'},
    UNKNOWN:      {label: 'Unknown',          color: '#4a5566', tone: 'off',    motion: 'still'}
  };

  var EDGE_LABELS = {
    CHALLENGE_RAISED: 'Challenge raised', CHALLENGE_ANSWERED: 'Challenge answered',
    CHALLENGE_RESOLVED: 'Challenge resolved', HANDOFF: 'Position hand-off',
    CANDIDATE_REVIEW: 'Candidate review step', EXECUTION_ESTIMATE: 'Execution estimate'
  };

  function esc(x) {
    return String(x == null ? '' : x).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  function age(s) {
    if (s == null || !isFinite(s)) return 'never';
    s = Math.max(0, Math.round(s));
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm ' + (s % 60 ? (s % 60) + 's' : '');
    if (s < 86400) return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
    return Math.floor(s / 86400) + 'd ' + Math.floor((s % 86400) / 3600) + 'h';
  }
  function ago(epoch, now) {
    if (epoch == null) return 'never';
    return age((now || Date.now() / 1000) - epoch).trim() + ' ago';
  }
  function clock(epoch) {
    if (epoch == null) return '—';
    var d = new Date(epoch * 1000);
    return d.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
  }
  function edgeLabel(kind) {
    if (EDGE_LABELS[kind]) return EDGE_LABELS[kind];
    if (/^LOOP_/.test(kind)) return 'Loop · ' + kind.slice(5).replace(/_/g, ' ').toLowerCase();
    return String(kind || '').replace(/_/g, ' ').toLowerCase();
  }
  function stateOf(a) { return a && STATES[a.state] ? a.state : 'UNKNOWN'; }
  function stateMeta(a) { return STATES[stateOf(a)]; }

  /* ONE READ. Resolves {status, data, why, httpStatus, at}:
   *   OK | SIGNED_OUT | NOT_RELEASED | UNAVAILABLE */
  function read(path) {
    var url = path || '/api/command/floor';
    if (url.indexOf('/api/command/') !== 0) return Promise.reject(new Error('refused: not a command read'));
    var started = Date.now() / 1000;
    return fetch(url, {method: 'GET', credentials: 'same-origin', cache: 'no-store',
                       headers: {'Accept': 'application/json'}})
      .then(function (r) {
        if (r.status === 401 || r.status === 403) return {status: 'SIGNED_OUT', httpStatus: r.status, at: started,
          why: 'Your COMMAND session is missing or has expired.'};
        if (r.status === 404) return {status: 'NOT_RELEASED', httpStatus: 404, at: started,
          why: 'The serving API does not have ' + url + ' yet; it ships with the next API release.'};
        if (!r.ok) return {status: 'UNAVAILABLE', httpStatus: r.status, at: started,
          why: 'The API answered HTTP ' + r.status + '.'};
        return r.json().then(function (j) { return {status: 'OK', data: j, httpStatus: 200, at: started}; },
          function () { return {status: 'UNAVAILABLE', httpStatus: r.status, at: started,
                                why: 'The answer was not JSON.'}; });
      }, function (e) {
        return {status: 'UNAVAILABLE', httpStatus: 0, at: started,
                why: 'The API could not be reached (' + (e && e.name || 'network') + ').'};
      });
  }

  /* A FIXTURE payload says so (fixture: true); the page labels it. */
  function isFixture(d) { return !!(d && (d.fixture === true || d.label === 'FIXTURE')); }

  /* A poller that pauses while the tab is hidden and keeps the last good
   * payload (labelled stale) when a later read fails. */
  function poller(path, every, onUpdate) {
    var timer = null, last = null, lastOk = null, stopped = false;
    function tick() {
      timer = null;
      if (stopped) return;
      if (document.hidden) return;
      read(path).then(function (res) {
        if (res.status === 'OK') { lastOk = res; }
        last = res;
        onUpdate({current: res, lastOk: lastOk});
      }).finally(function () { if (!stopped && !document.hidden) timer = setTimeout(tick, every); });
    }
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden && !timer && !stopped) tick();
    });
    tick();
    return {refresh: function () { if (timer) { clearTimeout(timer); timer = null; } tick(); },
            stop: function () { stopped = true; if (timer) clearTimeout(timer); },
            last: function () { return last; }};
  }

  function reducedMotion() {
    return !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  window.BTFloor = {SEATS: SEATS, BY_AGENT: BY_AGENT, BY_SLUG: BY_SLUG, STATES: STATES,
    esc: esc, age: age, ago: ago, clock: clock, edgeLabel: edgeLabel, stateOf: stateOf,
    stateMeta: stateMeta, read: read, poller: poller, isFixture: isFixture,
    reducedMotion: reducedMotion};
})();
