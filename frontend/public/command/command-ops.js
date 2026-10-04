/* BETTOR COMMAND · OPERATIONS RELEASE 1 · global layer (every Command page)
 *
 * 1. THE READ BROKER. Every read is a same-origin GET under /api/command/
 *    (the rule core.endpoint() enforces), one request in flight per URL, a
 *    per-endpoint minimum interval never faster than the existing pages read
 *    that endpoint, exponential backoff after a failure, and no polling while
 *    the tab is hidden. Nothing here can write: there is no other verb.
 * 2. THE FRONTEND BUILD. command/build.json is written by the Netlify build
 *    (frontend/scripts/write-build-info.mjs); the header shows FRONTEND
 *    <sha7> from it and compares it with nothing it cannot know.
 * 3. THE EXECUTIVE COMMAND HEADER: a thin, dense, sticky strip under the top
 *    bar -- environment, API, workers, PinnAPI, execution venue, API / worker
 *    SHA (a red MISMATCH when they differ), frontend SHA, last refresh,
 *    incidents, trading, PAPER and SMALL LIVE state. Every value is read from
 *    a production R29 (191b299) endpoint; a value the API does not serve is
 *    shown as DATA NOT AVAILABLE / UNAVAILABLE with the reason, never a guess.
 * 4. INCIDENTS, derived deterministically from recorded state, plus a short
 *    static list of KNOWN ISSUES of the deployed SHA, always labelled as such.
 *
 * SOURCES (R29, all GET, COMMAND session):
 *   /api/command/release          api.sha, workers.sha / boot_at, alignment
 *   /api/command/equity/live      paper.status / why / lane, small_live_bettor,
 *                                 actual.venues (shared with command-final's
 *                                 one equity loop; BTEquityWall where loaded)
 *   /api/command/coverage?days=2  provider_supplement (PinnAPI heartbeat),
 *                                 league_status.collector, alerts
 *   /api/command/overview         build_and_mode (collector cycle, funded
 *                                 submission switches)
 * No financial action, no synthetic value, no client-side guess. */
(function () {
  'use strict';
  if (window.BTOps) { return; }

  var PATH = (location.pathname || '/').replace(/\/+$/, '') || '/';
  var DEPLOYED_SHA = '191b299';   // labels the static KNOWN ISSUES only; never shown as live truth

  // ── formatting ────────────────────────────────────────────────────
  function esc(x) {
    return String(x == null ? '' : x).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  function fin(n) { return typeof n === 'number' && isFinite(n); }
  function num(v) {
    if (fin(v)) { return v; }
    if (typeof v === 'string' && v.trim() !== '' && isFinite(Number(v))) { return Number(v); }
    return null;
  }
  /* seconds since the epoch from epoch seconds, epoch ms or an ISO string */
  function epoch(t) {
    if (fin(t)) { return t > 1e12 ? t / 1000 : t; }
    if (typeof t === 'string' && t) {
      if (/^\d+(\.\d+)?$/.test(t)) { return epoch(Number(t)); }
      var x = Date.parse(t);
      return isNaN(x) ? null : x / 1000;
    }
    return null;
  }
  function nowS() { return Date.now() / 1000; }
  function age(s) {
    if (!fin(s)) { return '—'; }
    s = Math.max(0, s);
    if (s < 60) { return Math.round(s) + 's'; }
    if (s < 3600) { return Math.floor(s / 60) + 'm'; }
    if (s < 86400) { return Math.floor(s / 3600) + 'h ' + Math.floor(s % 3600 / 60) + 'm'; }
    return Math.floor(s / 86400) + 'd ' + Math.floor(s % 86400 / 3600) + 'h';
  }
  function ageOf(t) { var e = epoch(t); return e == null ? null : nowS() - e; }
  function clock(t) {
    var e = epoch(t);
    return e == null ? '—' : new Date(e * 1000).toISOString().slice(11, 19) + 'Z';
  }
  function stamp(t) {
    var e = epoch(t);
    return e == null ? '—' : new Date(e * 1000).toISOString().replace('T', ' ').slice(0, 19) + 'Z';
  }
  function usd(v, d) {
    if (!fin(v)) { return null; }
    d = d == null ? 2 : d;
    return (v < 0 ? '−$' : '$') + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d});
  }
  function sgnUsd(v, d) { return !fin(v) ? null : (v > 0 ? '+' : '') + usd(v, d); }
  function intf(v) { return fin(v) ? Math.round(v).toLocaleString('en-US') : null; }
  function pct(v, d) { return fin(v) ? (v * 100).toFixed(d == null ? 1 : d) + '%' : null; }
  function short(sha) { return sha ? String(sha).slice(0, 7) : null; }
  var fmt = {esc: esc, fin: fin, num: num, epoch: epoch, now: nowS, age: age, ageOf: ageOf, clock: clock,
             stamp: stamp, usd: usd, sgnUsd: sgnUsd, int: intf, pct: pct, short: short};

  // ── THE READ BROKER ───────────────────────────────────────────────
  /* The fastest any existing Command page reads each endpoint. A caller may
     ask for slower, never faster. Unlisted endpoints: 15 s. */
  var MIN_MS = {
    '/api/command/coverage': 120000,                       // profitability.js POLL_MS; snapshots persist every 900 s
    '/api/command/overview': 120000,                       // profitability.js POLL_MS
    '/api/command/profitability/lost-opportunities': 120000, // profitability.js POLL_MS
    '/api/command/release': 15000,
    '/api/command/floor': 10000,                           // command-final.js floor loop
    '/api/command/equity/live': 10000,                     // command-final.js direct equity loop
    '/api/command/paper/derek': 10000,
    '/api/command/paper/xavier': 15000,
    '/api/command/positions/rooms': 15000                  // position.js POLL_MS
  };
  /* reads small and slow enough to carry across page loads in this tab */
  var SESSION = {'/api/command/coverage': 1, '/api/command/overview': 1};
  var MAX_BACKOFF_MS = 300000;
  var cache = {};
  function pathOf(url) { return String(url).split('?')[0]; }
  function minMs(url) { return MIN_MS[pathOf(url)] || 15000; }
  function sessionGet(url) {
    if (!SESSION[pathOf(url)]) { return null; }
    try {
      var raw = sessionStorage.getItem('bt.ops.read:' + url);
      if (!raw) { return null; }
      var o = JSON.parse(raw);
      if (!o || !fin(o.at) || Date.now() - o.at > minMs(url)) { return null; }
      o.fromSession = true;
      return o;
    } catch (e) { return null; }
  }
  function sessionPut(url, res) {
    if (!SESSION[pathOf(url)] || !res.ok) { return; }
    try { sessionStorage.setItem('bt.ops.read:' + url, JSON.stringify(res)); } catch (e) { /* storage full or blocked: the next read refetches */ }
  }
  function classify(r, body, why) {
    if (r.status === 401 || r.status === 403) { return {state: 'SIGNED_OUT', why: 'SIGN-IN REQUIRED (HTTP ' + r.status + ')'}; }
    if (r.status === 404) { return {state: 'NOT_RELEASED', why: 'the serving API has no such route (HTTP 404)'}; }
    var reason = body && body.detail && (body.detail.reason || body.detail.detail);
    return {state: r.status >= 500 ? 'UNAVAILABLE' : 'ERROR', why: 'HTTP ' + r.status + (reason ? ' · ' + reason : '') + (why ? ' · ' + why : '')};
  }
  /* read(url) -> Promise<{ok, state, http, data, why, at, ms, lastGood}> ; never rejects */
  function read(url, opts) {
    opts = opts || {};
    if (String(url).indexOf('/api/command/') !== 0) {
      return Promise.resolve({ok: false, state: 'REFUSED', why: 'not a COMMAND read path', at: Date.now()});
    }
    var e = cache[url] || (cache[url] = {res: sessionGet(url), fails: 0, nextAt: 0, inflight: null});
    if (e.inflight) { return e.inflight; }
    var t = Date.now();
    // a forced read (a visible tab returning, a committed ledger entry) is still
    // never faster than half the endpoint's minimum interval
    if (opts.force && e.res && e.res.ok && t - (e.res.sent || e.res.at) < minMs(url) / 2) { return Promise.resolve(e.res); }
    if (e.res && e.res.ok && t - (e.res.sent || e.res.at) < minMs(url) - 500 && !opts.force) { return Promise.resolve(e.res); }
    if (e.fails && t < e.nextAt) { return Promise.resolve(e.res || {ok: false, state: 'BACKOFF', why: 'retrying after a failed read', at: t}); }
    var sent = Date.now();
    e.inflight = fetch(url, {method: 'GET', credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}})
      .then(function (r) {
        return r.text().then(function (txt) {
          var body = null, perr = null;
          try { body = txt ? JSON.parse(txt) : null; } catch (x) { perr = 'the response is not JSON'; }
          if (r.ok && !perr) { return {ok: true, state: 'OK', http: r.status, data: body, why: null}; }
          var c = classify(r, body, r.ok ? perr : null);
          return {ok: false, state: r.ok ? 'ERROR' : c.state, http: r.status, data: null, why: r.ok ? perr : c.why};
        });
      }, function () { return {ok: false, state: 'ERROR', http: 0, data: null, why: 'NETWORK: the API did not answer'}; })
      .then(function (res) {
        res.at = Date.now(); res.sent = sent; res.ms = res.at - sent; res.url = url;
        if (res.ok) { e.fails = 0; e.nextAt = 0; e.good = res; sessionPut(url, res); }
        else {
          e.fails += 1;
          e.nextAt = res.at + Math.min(MAX_BACKOFF_MS, minMs(url) * Math.pow(2, Math.min(e.fails, 5)));
          res.lastGood = e.good || null;
        }
        e.res = res; e.inflight = null;
        return res;
      });
    return e.inflight;
  }
  /* poll(url, everyMs, cb): read now and every max(everyMs, minimum) while
     visible; a return to the tab reads at once (still bounded by the minimum) */
  var pollers = [];
  function poll(url, everyMs, cb) {
    var every = Math.max(everyMs || 0, minMs(url));
    var p = {url: url, every: every, cb: cb, timer: null};
    function tick(force) {
      if (document.hidden) { return; }
      read(url, {force: !!force}).then(function (res) { try { cb(res); } catch (err) { /* one view never breaks another */ } });
    }
    p.tick = tick;
    p.timer = setInterval(tick, every);
    pollers.push(p);
    var cached = cache[url] && cache[url].res;
    if (cached) { try { cb(cached); } catch (err) { /* ignore */ } }
    tick();
    return {stop: function () { clearInterval(p.timer); }, now: function () { tick(true); }, every: every};
  }
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) { pollers.forEach(function (p) { p.tick(false); }); }
  });

  // ── THE FRONTEND BUILD ────────────────────────────────────────────
  var buildP = null;
  function build() {
    if (buildP) { return buildP; }
    // absolute, so a nested route (/positions/room/...) reads the same file: on the
    // command host /build.json is rewritten to /command/build.json (netlify.toml)
    var buildUrl = PATH.indexOf('/command/') === 0 ? '/command/build.json' : '/build.json';
    buildP = fetch(buildUrl, {method: 'GET', cache: 'no-store', credentials: 'same-origin'})
      .then(function (r) {
        if (!r.ok) { return {status: 'UNAVAILABLE', why: 'no build.json (HTTP ' + r.status + '): an unbuilt checkout or a build without the build-info step'}; }
        return r.text().then(function (t) {
          var j = null;
          try { j = JSON.parse(t); } catch (x) { return {status: 'UNAVAILABLE', why: 'build.json is not JSON (the host answered another page)'}; }
          if (!j || j.schema !== 'bt.frontend.build.v1') { return {status: 'UNAVAILABLE', why: 'build.json carries no bt.frontend.build.v1 record'}; }
          j.status = j.sha ? 'OK' : 'UNAVAILABLE';
          if (!j.sha) { j.why = j.sha_why || 'the build recorded no commit'; }
          return j;
        });
      }, function () { return {status: 'UNAVAILABLE', why: 'build.json could not be read'}; });
    return buildP;
  }

  // ── SPORT BUCKETS (the owner's nine) ───────────────────────────────
  var SPORTS = ['NFL', 'NCAAF', 'NBA', 'NCAAB', 'NHL', 'MLB', 'Soccer', 'Tennis', 'Other'];
  var KEY_SPORT = {americanfootball_nfl: 'NFL', americanfootball_ncaaf: 'NCAAF', basketball_nba: 'NBA',
                   basketball_ncaab: 'NCAAB', icehockey_nhl: 'NHL', baseball_mlb: 'MLB'};
  var TOKEN_SPORT = {nfl: 'NFL', cfb: 'NCAAF', ncaaf: 'NCAAF', nba: 'NBA', cbb: 'NCAAB', ncaab: 'NCAAB',
                     nhl: 'NHL', mlb: 'MLB', atp: 'Tennis', wta: 'Tennis', tennis: 'Tennis'};
  var NAME_SPORT = {'college football': 'NCAAF', 'college basketball': 'NCAAB', soccer: 'Soccer'};
  var SPORT_RULE = 'Bucket rule: provider competition key (americanfootball_nfl → NFL, _ncaaf → NCAAF, basketball_nba → NBA, ' +
    '_ncaab → NCAAB, icehockey_nhl → NHL, baseball_mlb → MLB, soccer_* → Soccer, tennis_* → Tennis), else the venue league ' +
    'token (nfl, cfb/ncaaf, nba, cbb/ncaab, nhl, mlb, atp/wta; first or second slug token, after an event-class prefix such as aec-), else the recorded sport family (soccer → Soccer, tennis → Tennis); ' +
    'anything else is Other, named by its own key.';
  function sportOf(key, family) {
    var k = String(key || '').toLowerCase().replace(/^venue:/, '');
    if (KEY_SPORT[k]) { return KEY_SPORT[k]; }
    if (k.indexOf('soccer_') === 0) { return 'Soccer'; }
    if (k.indexOf('tennis_') === 0) { return 'Tennis'; }
    if (NAME_SPORT[k]) { return NAME_SPORT[k]; }
    // venue slugs carry an event-class prefix before the league ("aec-mlb-atl-lad-2026-10-04"),
    // so the league token is the first or the second one
    var toks = k.split(/[-_:]/);
    if (TOKEN_SPORT[toks[0]]) { return TOKEN_SPORT[toks[0]]; }
    if (toks.length > 2 && TOKEN_SPORT[toks[1]]) { return TOKEN_SPORT[toks[1]]; }
    var f = String(family || '').toLowerCase();
    if (f.indexOf('soccer') === 0) { return 'Soccer'; }
    if (f.indexOf('tennis') === 0) { return 'Tennis'; }
    return 'Other';
  }

  // ── KNOWN ISSUES OF THE DEPLOYED SHA (static, labelled) ─────────────
  var KNOWN_LABEL = 'known issue in deployed SHA ' + DEPLOYED_SHA + '; fix in progress';
  var KNOWN = [
    {id: 'KNOWN-NCAAF-COLLECTOR-STARVATION', title: 'NCAAF COLLECTOR STARVATION', severity: 'CRITICAL',
     first_seen: '2026-10-03', sports: ['NCAAF'], components: ['collector discovery (ext_pinnacle_loop)', 'metered budget'],
     detail: 'Candidate discovery is capped at MAX_METERED_SPORTS_PER_CYCLE = 4 metered keys in the deployed build; the ' +
             '2026-10-03 NCAAF slate was absent and NCAAF is budget-dropped behind other competitions.',
     label: KNOWN_LABEL, known: true},
    {id: 'KNOWN-NFL-SETTLEMENT-SUPPORT', title: 'NFL SETTLEMENT SUPPORT DEGRADED', severity: 'CRITICAL',
     first_seen: '2026-10-04', sports: ['NFL'], components: ['settlement compatibility', 'fair value (football de-vig support)'],
     detail: 'The NFL slate is listed and mapped, but settlement / model support collapses downstream: 0 ENTER, 0 orders, ' +
             '0 fills for NFL in the deployed build.',
     label: KNOWN_LABEL, known: true}
  ];
  var METERED_CAP = {constant: 'MAX_METERED_SPORTS_PER_CYCLE', value: 4, sha: DEPLOYED_SHA,
                     source: 'workers/ext_pinnacle_loop.py:949 at ' + DEPLOYED_SHA};

  // ── INCIDENTS, DERIVED FROM RECORDED STATE (pure) ──────────────────
  var COLLECTOR_FRESH_S = 2700;   // coverage_integrity.COLLECTOR_FRESH_S (3 x 900 s)
  function sevRank(s) { return s === 'CRITICAL' ? 0 : s === 'WARNING' ? 1 : 2; }
  /* how many of the incident inputs were actually read (else "none" is unknown, not zero) */
  function incidentInputs(s) {
    s = s || {};
    var list = [['release', s.release && s.release.ok], ['coverage', s.coverage && s.coverage.ok], ['overview', s.overview && s.overview.ok],
                ['equity', s.equity && s.equity.status === 'OK'], ['floor', s.floor && s.floor.ok]];
    return {read: list.filter(function (x) { return x[1]; }).map(function (x) { return x[0]; }),
            missing: list.filter(function (x) { return !x[1]; }).map(function (x) { return x[0]; })};
  }
  function deriveIncidents(s) {
    s = s || {};
    var out = [], t = nowS();
    var rel = s.release && s.release.ok ? s.release.data : null;
    if (s.release && !s.release.ok && s.release.state !== 'SIGNED_OUT') {
      out.push({id: 'LIVE-API-READ-FAILING', title: 'COMMAND API READ FAILING', severity: 'CRITICAL',
                detail: 'GET /api/command/release: ' + (s.release.why || s.release.state) + (s.release.lastGood ? ' (last good read ' + age((Date.now() - s.release.lastGood.at) / 1000) + ' ago)' : ''),
                first_seen: null, first_seen_basis: 'this browser: the first failed read is not recorded server-side',
                last_seen: s.release.at ? s.release.at / 1000 : t, components: ['API'], sports: [], source: 'this browser'});
    }
    if (rel) {
      var al = rel.alignment || {}, a = rel.api || {}, w = rel.workers || {};
      if (al.verdict === 'MISALIGNED') {
        out.push({id: 'LIVE-SHA-MISMATCH', title: 'API / WORKER SHA MISMATCH', severity: 'CRITICAL',
                  detail: 'API ' + (a.short || short(a.sha) || '?') + ' ≠ WORKERS ' + (w.short || short(w.sha) || '?') +
                          ' (/api/command/release alignment MISALIGNED)',
                  first_seen: epoch(w.boot_at), first_seen_basis: 'worker boot that recorded its SHA (workers_boot.at)',
                  last_seen: epoch(rel.generated_at), components: ['API', 'workers'], sports: [], source: '/api/command/release'});
      } else if (al.verdict === 'UNKNOWN') {
        out.push({id: 'LIVE-SHA-UNKNOWN', title: 'API / WORKER SHA NOT COMPARABLE', severity: 'WARNING',
                  detail: al.why || 'API or worker SHA unavailable', first_seen: null, first_seen_basis: 'not recorded',
                  last_seen: epoch(rel.generated_at), components: ['API', 'workers'], sports: [], source: '/api/command/release'});
      }
    }
    var eq = s.equity && s.equity.live, p = eq && eq.paper;
    if (p && (p.status === 'STALE' || p.status === 'UNAVAILABLE')) {
      var lane = p.lane || {};
      out.push({id: 'LIVE-PAPER-' + p.status, title: 'PAPER EQUITY ' + p.status, severity: p.status === 'UNAVAILABLE' ? 'CRITICAL' : 'WARNING',
                detail: p.why || 'no reason recorded', first_seen: epoch(lane.heartbeat_at) || epoch((p.marks_as_of || {}).newest_at),
                first_seen_basis: 'last paper runtime heartbeat / newest mark (the state has held since then at most)',
                last_seen: epoch(eq.computed_at) || t, components: ['paper runtime', 'marks'], sports: [], source: '/api/command/equity/live'});
    }
    var cov = s.coverage && s.coverage.ok ? s.coverage.data : null;
    if (cov) {
      var sup = cov.provider_supplement || {};
      if (sup.status && sup.status !== 'OK') {
        out.push({id: 'LIVE-PINNAPI-HEARTBEAT', title: 'PINNAPI HEARTBEAT NOT CURRENT', severity: 'CRITICAL',
                  detail: (sup.why || 'provider supplement ' + sup.status) + (fin(sup.age_s) ? ' · last beat ' + age(sup.age_s) + ' ago' : ''),
                  first_seen: fin(sup.age_s) ? (epoch(cov.as_of) || t) - sup.age_s : null, first_seen_basis: 'last PinnAPI heartbeat',
                  last_seen: epoch(cov.as_of), components: ['PinnAPI feed'], sports: [], source: '/api/command/coverage provider_supplement'});
      }
      var coll = (cov.league_status || {}).collector || {};
      if (coll.budget_dropped && coll.budget_dropped.length) {
        out.push({id: 'LIVE-BUDGET-DROPS', title: 'COLLECTOR BUDGET DROPS', severity: 'WARNING',
                  detail: coll.budget_dropped.length + ' competition(s) not requested this cycle (metered budget ' + (coll.budget == null ? '?' : coll.budget) +
                          '): ' + coll.budget_dropped.join(', '),
                  first_seen: epoch(coll.at), first_seen_basis: 'collector cycle that recorded it (overwritten each cycle)',
                  last_seen: epoch(coll.at), components: ['collector discovery'], sports: coll.budget_dropped.map(function (k) { return sportOf(k); }),
                  source: '/api/command/coverage league_status.collector'});
      }
      var groups = {};
      (cov.alerts || []).forEach(function (a) {
        var k = [a.league, a.kind, a.stage_to].join('|');
        var g = groups[k] || (groups[k] = {a: a, first: null, last: null, days: {}, sev: a.severity});
        var at = epoch(a.detected_at);
        if (at != null) { g.first = g.first == null ? at : Math.min(g.first, at); g.last = g.last == null ? at : Math.max(g.last, at); }
        g.days[a.day] = 1;
        if (sevRank(a.severity) < sevRank(g.sev)) { g.sev = a.severity; }
      });
      var today = cov.days && cov.days[0] && cov.days[0].day;
      Object.keys(groups).forEach(function (k) {
        var g = groups[k], a = g.a, d = a.detail || {};
        out.push({id: 'LIVE-COVERAGE-' + k, title: 'COVERAGE ' + String(a.kind || 'ALERT').replace(/_/g, ' ') + ' · ' + (a.league_name || a.league),
                  severity: g.sev || 'WARNING', detail: d.statement || (a.stage_from + ' → ' + a.stage_to),
                  first_seen: g.first, first_seen_basis: 'coverage_collapse_alerts.detected_at (earliest in the window)',
                  last_seen: g.last, components: ['coverage integrity', a.stage_to], sports: [sportOf(a.league)],
                  source: '/api/command/coverage alerts', current: !!(today && g.days[today])});
      });
    }
    var ov = s.overview && s.overview.ok ? s.overview.data : null;
    var bm = ov && ov.build_and_mode && ov.build_and_mode.data;
    if (bm && fin(bm.last_cycle_age_s) && bm.last_cycle_age_s > COLLECTOR_FRESH_S) {
      out.push({id: 'LIVE-COLLECTOR-STALE', title: 'COLLECTOR CYCLE STALE', severity: 'WARNING',
                detail: 'last scheduled cycle ' + age(bm.last_cycle_age_s) + ' ago (fresh bound ' + age(COLLECTOR_FRESH_S) + ')' + (bm.cycle_why ? ' · ' + bm.cycle_why : ''),
                first_seen: epoch(bm.last_cycle_at), first_seen_basis: 'last collector cycle', last_seen: epoch(ov.read_at),
                components: ['collector'], sports: [], source: '/api/command/overview build_and_mode'});
    }
    var fl = s.floor && s.floor.ok ? s.floor.data : null;
    (fl && fl.agents || []).forEach(function (a) {
      if (a.state !== 'STALE' && a.state !== 'NOT_DEPLOYED') { return; }
      var hb = a.heartbeat || {};
      out.push({id: 'LIVE-AGENT-' + a.agent + '-' + a.state, title: (a.display_name || a.agent) + ' ' + a.state.replace('_', ' '),
                severity: a.state === 'STALE' ? 'WARNING' : 'INFO',
                detail: a.state === 'STALE' ? ('heartbeat ' + (fin(hb.age_s) ? age(hb.age_s) + ' old' : 'never recorded') + ' (stale after ' + age(hb.stale_after_s) + ')') : (a.deploy_why || 'not deployed on this API'),
                first_seen: epoch(hb.at), first_seen_basis: 'last heartbeat', last_seen: epoch(fl.read_at),
                components: ['agent ' + (a.slug || a.agent)], sports: [], source: '/api/command/floor'});
    });
    out.sort(function (x, y) { return sevRank(x.severity) - sevRank(y.severity) || (y.last_seen || 0) - (x.last_seen || 0); });
    return out;
  }

  // ── SHARED STATE ──────────────────────────────────────────────────
  var state = {release: null, equity: null, coverage: null, overview: null, floor: null, build: null};
  var subs = [];
  function emit() { subs.slice().forEach(function (cb) { try { cb(state); } catch (e) { /* ignore */ } }); }
  function on(cb) { subs.push(cb); try { cb(state); } catch (e) { /* ignore */ } }
  function set(k, v) { state[k] = v; emit(); }

  // ── THE EXECUTIVE HEADER ──────────────────────────────────────────
  var CELLS = [
    ['sha', 'SHA'], ['env', 'ENV'], ['api', 'API'], ['wrk', 'WORKERS'], ['pinn', 'PINNAPI'], ['venue', 'VENUE'],
    ['inc', 'INCIDENTS'], ['trading', 'TRADING'], ['paper', 'PAPER'], ['sl', 'SMALL LIVE'], ['fe', 'FRONTEND'], ['ref', 'REFRESHED']
  ];
  var hdr = null;
  function cell(id, tone, value, title, extra) {
    var el = document.getElementById('ops-h-' + id);
    if (!el) { return; }
    var b = el.querySelector('b');
    if (b.textContent !== value) { b.textContent = value; }
    el.setAttribute('data-tone', tone || 'dim');
    el.title = title || '';
    if (extra != null) { el.className = 'ops-h-cell' + (extra ? ' ' + extra : ''); }
  }
  function mountHeader() {
    if (document.getElementById('ops-hdr')) { return; }
    hdr = document.createElement('div');
    hdr.id = 'ops-hdr';
    hdr.className = 'ops-hdr';
    hdr.setAttribute('role', 'status');
    hdr.setAttribute('data-exec-header', 'COMMAND_OPS_R1');
    hdr.setAttribute('aria-label', 'Executive command header: production system state');
    hdr.innerHTML = CELLS.map(function (c) {
      var tag = c[0] === 'inc' ? 'a href="/ops#incidents"' : 'div';
      return '<' + tag + ' class="ops-h-cell" id="ops-h-' + c[0] + '" data-tone="dim"><span>' + c[1] + '</span><b>READING</b></' + tag.split(' ')[0] + '>';
    }).join('') + (PATH === '/ops' ? '' : '<a class="ops-h-cell ops-h-desk" href="/ops"><span>DESK</span><b>Open operations desk →</b></a>');
    var bar = document.querySelector('.bt-hq2-pagebar');
    if (bar) {
      document.body.appendChild(hdr);
      document.body.classList.add('ops-hdr-on');
    } else {
      document.body.insertBefore(hdr, document.body.firstChild);
      document.body.classList.add('ops-hdr-flow');
    }
    function measure() {
      // sit exactly under the top bar, whatever height the page's own CSS gave it
      if (bar && getComputedStyle(hdr).position === 'fixed') {
        var top = Math.max(0, Math.round(bar.getBoundingClientRect().bottom));
        if (top && hdr.style.top !== top + 'px') { hdr.style.top = top + 'px'; }
      }
      var h = Math.ceil(hdr.getBoundingClientRect().height) || 0;
      if (h) { document.documentElement.style.setProperty('--ops-hdr-h', h + 'px'); }
    }
    measure();
    if (window.ResizeObserver) { var ro = new ResizeObserver(measure); ro.observe(hdr); if (bar) { ro.observe(bar); } }
    window.addEventListener('resize', measure);
  }

  var newestOk = 0;
  function noteOk(res) { if (res && res.ok && res.at > newestOk) { newestOk = res.at; } }
  function renderHeader() {
    if (!hdr) { return; }
    var rel = state.release, R = rel && rel.ok ? rel.data : null;
    var eq = state.equity, L = eq && eq.live, P = L && L.paper, SL = L && L.small_live_bettor;
    var cov = state.coverage, C = cov && cov.ok ? cov.data : null;
    var ov = state.overview, BM = ov && ov.ok && ov.data && ov.data.build_and_mode ? ov.data.build_and_mode : null;
    var B = state.build;

    // ENV: the build's own context, the book, the small-live mode
    var envBits = [], envTone = 'dim', envTitle = [];
    if (B && B.status === 'OK') {
      var ctx = String(B.context || '').toLowerCase();
      envBits.push(ctx === 'production' ? 'PRODUCTION' : ctx === 'deploy-preview' ? 'PREVIEW' : ctx === 'branch-deploy' ? 'BRANCH' : (B.source === 'LOCAL' ? 'LOCAL' : String(B.context || 'UNKNOWN').toUpperCase()));
      envTitle.push('build context: ' + (B.context || '—') + ' (command/build.json)');
      envTone = ctx === 'production' ? 'good' : 'warn';
    } else {
      envBits.push(/(^|\.)command\.bettortoken\.com$/.test(location.hostname) ? 'PRODUCTION HOST' : 'UNVERIFIED');
      envTitle.push('no build record: ' + ((B && B.why) || 'reading') + '; the label is the hostname only');
      envTone = 'warn';
    }
    if (P) { envBits.push('PAPER'); envTitle.push('book: ' + (P.label || 'PAPER') + ' · ' + (P.money || '')); }
    if (SL && SL.status) { envBits.push(SL.status === 'SHADOW' ? 'SHADOW' : 'SMALL LIVE ' + SL.status); }
    cell('env', envTone, envBits.join(' · '), envTitle.join('\n'));

    // API: the release read itself
    if (!rel) { cell('api', 'dim', 'READING', 'GET /api/command/release'); }
    else if (rel.ok) { cell('api', 'good', 'UP · ' + rel.ms + 'ms', 'GET /api/command/release answered ' + rel.http + ' in ' + rel.ms + ' ms at ' + clock(rel.at / 1000)); }
    else { cell('api', rel.state === 'SIGNED_OUT' ? 'warn' : 'bad', rel.state === 'SIGNED_OUT' ? 'SIGN-IN REQUIRED' : 'DOWN', rel.why || rel.state); }

    // WORKERS: boot record + collector cycle heartbeat
    var w = R && R.workers;
    var bootTxt = w && w.status === 'OK' ? 'boot ' + age(ageOf(w.boot_at)) + ' ago' : null;
    var bmd = BM && BM.data;
    if (bmd && fin(bmd.last_cycle_age_s)) {
      var fresh = bmd.last_cycle_age_s <= COLLECTOR_FRESH_S;
      cell('wrk', fresh ? 'good' : 'bad', (fresh ? 'RUNNING' : 'CYCLE STALE') + ' · cycle ' + age(bmd.last_cycle_age_s),
           'collector cycle (overview.build_and_mode): ' + (bmd.cycle_state || '—') + ', last ' + stamp(bmd.last_cycle_at) +
           (bootTxt ? '\nworkers ' + bootTxt + ' (release.workers.boot_at ' + stamp(w.boot_at) + ')' : ''));
    } else if (w) {
      cell('wrk', w.status === 'OK' ? 'dim' : 'warn', w.status === 'OK' ? 'BOOTED · ' + age(ageOf(w.boot_at)) : 'UNAVAILABLE',
           (w.why || 'workers_boot ' + stamp(w.boot_at)) + '\ncollector cycle: ' + (ov && !ov.ok ? (ov.why || ov.state) : 'not read yet'));
    } else { cell('wrk', 'dim', rel && !rel.ok ? 'UNAVAILABLE' : 'READING', rel && rel.why || ''); }

    // PINNAPI: the feed heartbeat as coverage serves it
    var sup = C && C.provider_supplement;
    if (sup) {
      if (sup.status === 'OK') { cell('pinn', 'good', 'LIVE · ' + age(sup.age_s), 'PinnAPI heartbeat ' + age(sup.age_s) + ' old (coverage.provider_supplement, RECENT_TELEMETRY)'); }
      else { cell('pinn', 'bad', 'NOT CURRENT' + (fin(sup.age_s) ? ' · ' + age(sup.age_s) : ''), sup.why || sup.status); }
    } else { cell('pinn', 'dim', cov && !cov.ok ? 'UNAVAILABLE' : 'READING', cov && cov.why || 'GET /api/command/coverage (every 120 s)'); }

    // VENUE: the execution venues as equity/live records them (legacy mirror lanes)
    var V = L && L.actual && L.actual.venues;
    if (V) {
      var pm = V.polymarket_us || {}, ka = V.kalshi || {};
      var pmLane = pm.lane && pm.lane.state;
      cell('venue', pmLane === 'ENABLED' && pm.status === 'OK' ? 'good' : 'dim',
           'PM-US ' + (pmLane || pm.status || '—') + ' · KALSHI ' + (ka.status || '—'),
           'Polymarket US legacy mirror: lane ' + (pmLane || '—') + ', account ' + (pm.status || '—') + (pm.why ? ' (' + pm.why + ')' : '') +
           '\nKalshi: ' + (ka.status || '—') + (ka.why ? ' (' + ka.why + ')' : '') + '\nPAPER execution is simulated (no venue order).');
    } else { cell('venue', 'dim', eq && eq.status === 'SIGNED_OUT' ? 'SIGN-IN REQUIRED' : eq && eq.status === 'ERROR' ? 'UNAVAILABLE' : 'READING', 'GET /api/command/equity/live'); }

    // SHA: API vs workers (red MISMATCH when they differ)
    if (R) {
      var a = R.api || {}, wk = R.workers || {}, al = R.alignment || {};
      var as = a.short || short(a.sha) || 'UNAVAILABLE', ws = wk.short || short(wk.sha) || 'UNAVAILABLE';
      var t2 = 'API ' + (a.sha || a.why || '—') + '\nWORKERS ' + (wk.sha || wk.why || '—') + '\nalignment ' + (al.verdict || '—') + (al.matched_how ? ' (' + al.matched_how + ')' : '') + (al.why ? ' · ' + al.why : '');
      if (al.verdict === 'MISALIGNED') {
        cell('sha', 'bad', 'MISMATCH · API ' + as + ' ≠ WORKERS ' + ws, t2, 'mismatch');
        document.body.classList.add('ops-sha-mismatch');
      } else {
        cell('sha', al.verdict === 'ALIGNED' ? 'good' : 'warn', 'API ' + as + ' · WORKERS ' + ws + (al.verdict === 'ALIGNED' ? ' ✓' : ' · UNVERIFIED'), t2, '');
        document.body.classList.remove('ops-sha-mismatch');
      }
    } else { cell('sha', rel && !rel.ok ? 'warn' : 'dim', rel && !rel.ok ? 'UNAVAILABLE' : 'READING', rel && rel.why || 'GET /api/command/release'); }

    // FRONTEND: the build record only
    if (B) {
      if (B.status === 'OK') { cell('fe', B.source === 'LOCAL' ? 'warn' : 'dim', short(B.sha) + (B.source === 'LOCAL' ? ' · LOCAL' : ''), 'frontend build ' + B.sha + '\nbranch ' + (B.branch || '—') + ' · context ' + (B.context || '—') + (B.deploy_id ? '\nnetlify deploy ' + B.deploy_id + (B.site ? ' · site ' + B.site : '') : '') + '\nbuilt ' + (B.built_at || '—') + ' · source ' + (B.source || '—')); }
      else { cell('fe', 'warn', 'UNAVAILABLE', B.why || 'no build record'); }
    }

    // TRADING: funded submission switches (overview.build_and_mode)
    if (bmd) {
      var funded = bmd.funded_submission === 'ENABLED';
      cell('trading', funded ? 'bad' : 'dim', funded ? 'FUNDED SUBMISSION ENABLED' : 'NON-FUNDED · NO ORDER CAN BE SENT',
           (bmd.operating_mode || '') + '\nswitches: ' + JSON.stringify(bmd.submission_switches || {}));
    } else { cell('trading', 'dim', ov && !ov.ok ? 'UNAVAILABLE' : 'READING', ov && ov.why || 'GET /api/command/overview (every 120 s)'); }

    // PAPER: the paper runtime lane and equity status
    if (P) {
      var ln = P.lane || {};
      cell('paper', P.status === 'OK' ? 'good' : P.status === 'STALE' ? 'warn' : 'bad',
           (ln.state || 'UNKNOWN') + ' · ' + P.status,
           'paper lane ' + (ln.state || '—') + ', heartbeat ' + (fin(ln.heartbeat_age_s) ? age(ln.heartbeat_age_s) + ' ago' : '—') + (P.why ? '\n' + P.why : ''));
    } else { cell('paper', eq && eq.status && eq.status !== 'OK' && eq.status !== 'IDLE' ? 'warn' : 'dim', eq && eq.status === 'SIGNED_OUT' ? 'SIGN-IN REQUIRED' : eq && eq.status === 'ERROR' ? 'UNAVAILABLE' : 'READING', 'GET /api/command/equity/live'); }

    // SMALL LIVE: bettor_originated_status as served
    if (SL) { cell('sl', SL.status === 'ACTIVE' ? 'bad' : SL.status ? 'dim' : 'warn', SL.status || 'UNAVAILABLE', (SL.title || 'SMALL LIVE') + (SL.why ? '\n' + SL.why : '')); }
    else if (L) { cell('sl', 'warn', 'UNAVAILABLE', 'equity/live carried no small_live_bettor section'); }
    else if (eq && eq.status && eq.status !== 'OK' && eq.status !== 'IDLE') { cell('sl', 'warn', eq.status === 'SIGNED_OUT' ? 'SIGN-IN REQUIRED' : 'UNAVAILABLE', 'equity/live read: ' + eq.status + ' (the SMALL LIVE state is served there)'); }

    // INCIDENTS: live-derived + known
    var inc = deriveIncidents(state);
    var crit = inc.filter(function (x) { return x.severity === 'CRITICAL'; }).length;
    var warn = inc.filter(function (x) { return x.severity === 'WARNING'; }).length;
    var ins = incidentInputs(state);
    var noneTxt = ins.read.length ? (ins.missing.length ? 'NONE IN ' + ins.read.length + '/5 READS · ' : 'NONE LIVE · ') : 'UNKNOWN (NO READ) · ';
    cell('inc', crit ? 'bad' : warn ? 'warn' : ins.read.length ? 'good' : 'warn',
         (crit ? crit + ' CRIT · ' : '') + (warn ? warn + ' WARN · ' : '') + (crit || warn ? '' : noneTxt) + KNOWN.length + ' KNOWN',
         inc.map(function (x) { return x.severity + ' · ' + x.title; }).concat(KNOWN.map(function (k) { return 'KNOWN · ' + k.title + ' (' + k.label + ')'; })).join('\n'));

    // REFRESHED: the newest successful read behind this header
    [rel, cov, ov].forEach(noteOk);
    if (eq && eq.status === 'OK' && eq.okAt) { newestOk = Math.max(newestOk, eq.okAt); }
    cell('ref', newestOk ? (Date.now() - newestOk < 60000 ? 'good' : 'warn') : 'dim', newestOk ? clock(newestOk / 1000) : '—',
         newestOk ? 'newest successful header read ' + age((Date.now() - newestOk) / 1000) + ' ago (this browser)' : 'no successful read yet');
  }
  /* the REFRESHED age and the header's ages move with the clock, not with fake data */
  setInterval(function () { if (!document.hidden) { renderHeader(); } }, 5000);

  // ── HOME ENTRY POINT ──────────────────────────────────────────────
  function homeEntry() {
    if (PATH !== '/' && PATH !== '/index.html') { return; }
    var tries = 0;
    (function go() {
      var strip = document.getElementById('cf-command-strip');
      if (!strip) { if (tries++ < 80) { setTimeout(go, 150); } return; }
      if (document.getElementById('ops-home-entry')) { return; }
      var a = document.createElement('a');
      a.id = 'ops-home-entry'; a.className = 'ops-home-entry'; a.href = '/ops';
      a.innerHTML = '<span class="k">OPERATIONS DESK</span><span class="d">Live funnel · sport coverage · opportunities · orders &amp; fills · agents · refusals · PinnAPI · capital · incidents</span><b>Open operations desk →</b>';
      strip.insertAdjacentElement('afterend', a);
    })();
  }

  // ── WIRING ────────────────────────────────────────────────────────
  function equitySource(cb) {
    if (window.BTCommandFinal && typeof window.BTCommandFinal.onEquity === 'function') { window.BTCommandFinal.onEquity(cb); return 'command-final'; }
    if (window.BTEquityWall && typeof window.BTEquityWall.subscribe === 'function') { window.BTEquityWall.subscribe(cb); return 'equity-wall'; }
    poll('/api/command/equity/live', 15000, function (res) {
      cb({status: res.ok ? 'OK' : res.state, live: res.ok ? res.data : null, okAt: res.ok ? res.at : null, source: 'ops'});
    });
    return 'ops';
  }
  function start() {
    mountHeader();
    homeEntry();
    on(renderHeader);
    build().then(function (b) { set('build', b); });
    poll('/api/command/release', 15000, function (r) { set('release', r); });
    poll('/api/command/coverage?days=2', 120000, function (r) { set('coverage', r); });
    poll('/api/command/overview', 120000, function (r) { set('overview', r); });
    equitySource(function (st) {
      var cur = st || {};
      if (cur.status === 'OK' && !cur.okAt) { cur = Object.assign({}, cur, {okAt: Date.now()}); }
      set('equity', cur);
    });
  }

  window.BTOps = {
    version: 'COMMAND_OPS_R1', deployedSha: DEPLOYED_SHA, fmt: fmt, read: read, poll: poll, minMs: minMs,
    build: build, state: state, on: on, set: set, deriveIncidents: deriveIncidents, incidentInputs: incidentInputs, known: KNOWN,
    meteredCap: METERED_CAP, sports: SPORTS, sportOf: sportOf, sportRule: SPORT_RULE,
    collectorFreshS: COLLECTOR_FRESH_S
  };
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', start); } else { start(); }
})();
