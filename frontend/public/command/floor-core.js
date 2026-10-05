/* BETTOR HEADQUARTERS · shared floor model (classic script, no globals but
 * window.BTFloor). Reads GET /api/command/floor (same origin, session
 * cookie, no-store) and describes each agent's REAL state. Nothing here
 * invents activity: an absent read is UNKNOWN/UNAVAILABLE with its reason,
 * an old read is labelled STALE with its age, a fixture payload is
 * labelled FIXTURE on every surface. Used by floor.js and workspace.js. */
(function () {
  'use strict';

  /* THE SEVEN DESKS in the candidate-review order (Derek -> Karen -> Scout
   * -> Archer (named Eddie until migration 266) -> Allocator -> Audrey ->
   * Xavier). Static identity only:
   * names, colours and links -- never state. */
  var SEATS = [
    {agent: 'DEREK', slug: 'derek', name: 'Derek', short: 'CIO · Alpha',
     role: 'Chief Investment Officer · entry decisions', accent: '#9fe3bf', initial: 'D'},
    {agent: 'KAREN', slug: 'karen', name: 'Karen', short: 'Red team',
     role: 'Red team · evidence challenges (zero authority)', accent: '#ff9a8f', initial: 'K'},
    {agent: 'SCOUT', slug: 'scout', name: 'Scout', short: 'Intelligence',
     role: 'Market intelligence · research shadow', accent: '#f5b072', initial: 'S'},
    {agent: 'ARCHER', slug: 'archer', name: 'Archer', short: 'Execution',
     role: 'Head of Execution · shadow only', accent: '#6fe0d2', initial: 'A'},
    {agent: 'CHIEF_ALLOCATOR', slug: 'allocator', name: 'Allie', short: 'Chief Allocator',
     role: 'Chief Allocator · capital allocation · shadow sleeve', accent: '#ecc66d', initial: 'A'},
    {agent: 'AUDREY', slug: 'audrey', name: 'Audrey', short: 'Risk & audit',
     role: 'Risk, audit & management reporting', accent: '#cdb6f6', initial: 'A'},
    {agent: 'XAVIER', slug: 'xavier', name: 'Xavier', short: 'Portfolio Mgmt',
     role: 'Portfolio manager · position management', accent: '#9fd2f2', initial: 'X'}
  ];
  var BY_AGENT = {}, BY_SLUG = {};
  SEATS.forEach(function (s) { BY_AGENT[s.agent] = s; BY_SLUG[s.slug] = s; });

  /* THE HISTORICAL ALIAS (migration 266): the execution agent EDDIE was
   * renamed ARCHER. An API answer -- from a serving build before the rename,
   * or a record written before it -- may still name EDDIE / eddie. Every
   * read is passed through dealias() once, so every surface speaks ARCHER:
   * an agent-id value naming the alias becomes the agent it now names and
   * its object carries historical_alias: 'EDDIE' (shown, never silently
   * relabelled); an object key that IS the alias (a map keyed by agent)
   * moves to the archer name, and a compound field (eddie_estimates) is
   * answered under the archer name as well, the original kept beside it.
   * Nothing else is changed. */
  var ALIASES = {EDDIE: 'ARCHER', eddie: 'archer', Eddie: 'Archer'};
  var ALIAS_FIELDS = {agent: 1, agent_id: 1, slug: 1, from: 1, to: 1, actor: 1,
    proposer: 1, owner_agent: 1, assignee: 1, counterpart: 1, from_agent: 1,
    to_agent: 1, created_by: 1, recorded_by: 1, kind_agent: 1};
  function aliasKey(k) {
    if (Object.prototype.hasOwnProperty.call(ALIASES, k)) return ALIASES[k];
    var m = /(^|_)(eddie|EDDIE)(_|$)/.exec(k);
    return m ? k.replace(/(^|_)(eddie|EDDIE)(?=_|$)/, function (x, a, b) { return a + ALIASES[b]; }) : null;
  }
  function dealias(v, depth) {
    depth = depth || 0;
    if (depth > 12 || v === null || typeof v !== 'object') return v;
    if (Array.isArray(v)) { for (var i = 0; i < v.length; i++) v[i] = dealias(v[i], depth + 1); return v; }
    var keys = Object.keys(v);
    for (var j = 0; j < keys.length; j++) {
      var k = keys[j], x = v[k];
      if (ALIAS_FIELDS[k] && typeof x === 'string' && Object.prototype.hasOwnProperty.call(ALIASES, x)) {
        v[k] = ALIASES[x];
        if (!v.historical_alias) v.historical_alias = 'EDDIE';
      } else {
        v[k] = dealias(x, depth + 1);
      }
      var nk = aliasKey(k);
      if (nk && !Object.prototype.hasOwnProperty.call(v, nk)) {
        v[nk] = v[k];
        // a map keyed by agent: the alias key MOVES (no seat is listed
        // twice); a compound field (eddie_estimates) is answered under both
        if (Object.prototype.hasOwnProperty.call(ALIASES, k)) delete v[k];
      }
    }
    return v;
  }
  function canonicalSlug(slug) {
    var s = String(slug == null ? '' : slug);
    return Object.prototype.hasOwnProperty.call(ALIASES, s.toLowerCase()) ? ALIASES[s.toLowerCase()] : s;
  }

  /* STATE PRESENTATION. `motion` is what the floor may animate for it. */
  var STATES = {
    WORKING_ON:   {label: 'Working',          color: '#50d8ac', tone: 'green',  motion: 'work'},
    REVIEWING:    {label: 'Reviewing',        color: '#6fb6ff', tone: 'blue',   motion: 'review'},
    CHALLENGING:  {label: 'Challenging',      color: '#ff8395', tone: 'red',    motion: 'review'},
    WAITING:      {label: 'Waiting',          color: '#e9be74', tone: 'amber',  motion: 'idle'},
    IDLE:         {label: 'Idle',             color: '#93a3b8', tone: 'slate',  motion: 'idle'},
    STALE:        {label: 'Stale',            color: '#5d6878', tone: 'dim',    motion: 'still'},
    NOT_DEPLOYED: {label: 'Not yet deployed', short: 'Not deployed', color: '#56627a', tone: 'off',    motion: 'still'},
    UNKNOWN:      {label: 'Unknown',          color: '#4a5566', tone: 'off',    motion: 'still'},
    WORKING:      {label: 'Working',          color: '#50d8ac', tone: 'green',  motion: 'work'},
    WAITING_FOR_FRESH_EVIDENCE:{label:'Waiting for fresh evidence',color:'#e9be74',tone:'amber',motion:'idle'},
    BLOCKED_ON_MARKET_DATA:{label:'Blocked on market data',color:'#ff9d78',tone:'red',motion:'idle'},
    HANDOFF_PENDING:{label:'Handoff pending',color:'#82b8ff',tone:'blue',motion:'review'},
    IDLE_NO_OPEN_WORK:{label:'No open work',color:'#93a3b8',tone:'slate',motion:'idle'}
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
  function stateOf(a) { var w=a&&a.work_state; if(w&&STATES[w])return w; return a&&STATES[a.state]?a.state:'UNKNOWN'; }
  function stateMeta(a) { return STATES[stateOf(a)]; }

  /* ONE READ. Resolves {status, data, why, httpStatus, at}:
   *   OK | SIGNED_OUT | NOT_RELEASED | UNAVAILABLE */
  /* a serving build from before migration 266 knows Archer only by his
   * historical address (/eddie): an answer of 404 for an /archer path is
   * asked once more there -- the same read, labelled by dealias() */
  var ARCHER_SEGMENT = /\/archer(?=\/|\?|$)/;
  function read(path, noAlias) {
    var url = path || '/api/command/floor';
    if (url.indexOf('/api/command/') !== 0) return Promise.reject(new Error('refused: not a command read'));
    if (!noAlias && ARCHER_SEGMENT.test(url)) {
      return read(url, true).then(function (res) {
        return res.status === 'NOT_RELEASED' ? read(url.replace(ARCHER_SEGMENT, '/eddie'), true) : res;
      });
    }
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
        return r.json().then(function (j) { return {status: 'OK', data: dealias(j), httpStatus: 200, at: started}; },
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
    reducedMotion: reducedMotion, ALIASES: ALIASES, dealias: dealias,
    canonicalSlug: canonicalSlug};
})();
