/* BETTOR HEADQUARTERS · the trading floor's data model (ES module).
 *
 * The 3D floor (floor-scene.js) and its in-world screens (floor-screens.js)
 * draw ONLY what this model holds:
 *   floor     GET /api/command/floor         handed in by floor.js (setFloor)
 *   equity    GET /api/command/equity/live   PAPER and SMALL LIVE, separately
 *   coverage  GET /api/command/coverage      floor.js's own read when present
 *   xavier    GET /api/command/floor/xavier  Xavier's recorded assessments
 * Same origin, session cookie, no-store, GET only (window.BTFloor.read).
 *
 * TRUTH RULES (pinned by backend/tests/test_floor_hq_truth_ui.py):
 *   - no invented number: missing is UNAVAILABLE with its reason, an old read
 *     is STALE with its age, a fixture payload is labelled FIXTURE;
 *   - desk states are the floor API's derived states, translated one to one;
 *     only WORKING_ON / REVIEWING / CHALLENGING on a fresh read count as
 *     "at work" (the only states allowed to drive work motion);
 *   - PAPER and SMALL LIVE (and each legacy venue) stay separate figures: no
 *     line of this file adds one book to another. */

/* Zone names and short titles for the in-world signs; names, accents and
 * routes come from floor-core.js (BTFloor.SEATS: Allie / Chief Allocator). */
const ZONE = {
  derek: ['Discovery & entry', 'Chief Investment Officer'],
  karen: ['Adversarial review', 'Red team'],
  scout: ['Research & signals', 'Market intelligence'],
  allocator: ['Capital allocation', 'Chief Allocator'],
  eddie: ['Execution & microstructure', 'Head of Execution'],
  audrey: ['Audit & reconciliation', 'Risk & audit'],
  xavier: ['Portfolio management', 'Portfolio Manager']
};

/* THE FLOOR API STATE -> what the floor shows. One to one. */
export const STATE = {
  WORKING_ON:   {label: 'WORKING',     color: '#4fe0a8', tone: 'work',  active: true},
  REVIEWING:    {label: 'REVIEWING',   color: '#68b6ff', tone: 'work',  active: true},
  CHALLENGING:  {label: 'CHALLENGING', color: '#ff7d8e', tone: 'work',  active: true},
  WAITING:      {label: 'WAITING',     color: '#eab768', tone: 'wait',  active: false},
  BLOCKED:      {label: 'BLOCKED',     color: '#ff9a5c', tone: 'wait',  active: false},
  IDLE:         {label: 'NO TASK',     color: '#9fb0c4', tone: 'idle',  active: false},
  STALE:        {label: 'STALE',       color: '#7d8796', tone: 'stale', active: false},
  NOT_DEPLOYED: {label: 'OFFLINE',     color: '#5b6576', tone: 'off',   active: false},
  UNAVAILABLE:  {label: 'UNAVAILABLE', color: '#5b6576', tone: 'off',   active: false}
};

export function createModel(B) {
  const SEATS = B.SEATS.map((s) => ({agent: s.agent, slug: s.slug, name: s.name, accent: s.accent,
    zone: (ZONE[s.slug] || [s.short])[0], role: (ZONE[s.slug] || [null, s.short])[1], href: '/' + s.slug}));
  const BY_SLUG = {}, BY_AGENT = {};
  SEATS.forEach((s) => { BY_SLUG[s.slug] = s; BY_AGENT[s.agent] = s; });

  const epoch = (v) => { if (v == null) return null; if (typeof v === 'number') return isFinite(v) ? v : null; const t = Date.parse(v); return isNaN(t) ? null : t / 1000; };
  const ago = (t) => { const e = epoch(t); return e == null ? 'never' : B.ago(e); };
  const clock = (t) => { const e = epoch(t); return e == null ? '—' : new Date(e * 1000).toISOString().slice(11, 19) + ' UTC'; };
  const usd = (v) => {
    if (v == null || typeof v !== 'number' || !isFinite(v)) return null;
    const s = Math.abs(v).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    return (v < 0 ? '−$' : '$') + s;
  };
  const signedUsd = (v) => { const s = usd(v); return s == null ? null : (v > 0 ? '+' : '') + s; };
  const words = (s) => String(s == null ? '' : s).replace(/_/g, ' ');

  const R = {
    floor: {status: 'LOADING', data: null, at: null, why: null, lastOk: null},
    equity: {status: 'LOADING', data: null, at: null, why: null, lastOk: null, changedAt: null},
    coverage: {status: 'LOADING', data: null, at: null, why: null, lastOk: null},
    detail: {}
  };
  let selected = null;
  const subs = [];
  const emit = (kind) => subs.forEach((fn) => { try { fn(kind); } catch (e) { if (window.console) console.warn('floor model subscriber', e); } });
  const store = (slot, res) => { slot.status = res.status; slot.at = res.at; slot.why = res.why || null; if (res.status === 'OK') { slot.data = res.data; slot.lastOk = res.at; } };

  /* floor.js hands the floor payload over: (payload of the last good read, meta) */
  function setFloor(p, meta) {
    const m = meta || {};
    R.floor.status = p ? (m.stale ? 'STALE' : (m.status || 'OK')) : (m.status || 'NONE');
    R.floor.why = m.why || null;
    if (p) { R.floor.data = p; if (!m.stale) R.floor.lastOk = Date.now() / 1000; }
    else R.floor.data = null;
    emit('floor');
  }
  function onEquity(u) {
    const prev = R.equity.data && R.equity.data.seq;
    store(R.equity, u.current);
    const seq = R.equity.data && R.equity.data.seq;
    // the server raises seq only when the content genuinely changed
    if (u.current.status === 'OK' && prev != null && seq != null && seq !== prev) R.equity.changedAt = Date.now() / 1000;
    emit('equity');
  }
  function readDetail(slug) {
    return B.read('/api/command/floor/' + slug).then((res) => { store(R.detail[slug] || (R.detail[slug] = {}), res); emit('detail:' + slug); });
  }
  /* coverage: floor.js already reads it every minute (window.__floor.coverage);
   * read it ourselves only when that controller is absent */
  let covAt = null;
  function syncCoverage() {
    const c = window.__floor && window.__floor.coverage;
    if (c) {
      if (c.at !== covAt || c.status !== R.coverage.status) {
        covAt = c.at; R.coverage.status = c.status; R.coverage.why = c.why || null;
        if (c.status === 'OK') { R.coverage.data = c.data; R.coverage.lastOk = c.at; }
        emit('coverage');
      }
      return true;
    }
    return false;
  }
  function start() {
    B.poller('/api/command/equity/live', 8000, onEquity);
    const xv = () => { if (!document.hidden) readDetail('xavier'); };
    xv(); setInterval(xv, 60000);
    setTimeout(() => { if (!syncCoverage()) B.poller('/api/command/coverage?tz=America%2FNew_York&days=1', 120000, (u) => { store(R.coverage, u.current); emit('coverage'); }); }, 4000);
    setInterval(syncCoverage, 5000);
  }

  const floorData = () => R.floor.data;
  const floorFresh = () => R.floor.status === 'OK';
  function agentOf(slug) {
    const f = floorData(); if (!f) return null;
    const seat = BY_SLUG[slug];
    return (f.agents || []).find((a) => a.slug === slug || (seat && a.agent === seat.agent)) || null;
  }
  function desk(slug) {
    const seat = BY_SLUG[slug], a = agentOf(slug);
    let code = !a ? 'UNAVAILABLE' : a.state in STATE ? a.state : 'UNAVAILABLE';
    if (code === 'WAITING' && a.status_row && /^(BLOCKED|FAILED)$/.test(a.status_row.state || '')) code = 'BLOCKED';
    const meta = STATE[code], level = a && a.authority && a.authority.level || '';
    return {
      seat, slug, name: seat.name, role: seat.role, zone: seat.zone, accent: seat.accent,
      href: (a && a.workspace) || seat.href, code, apiState: a ? a.state : null,
      label: meta.label, color: meta.color, tone: meta.tone, active: !!meta.active && floorFresh(),
      shadow: /SHADOW/.test(level), authority: level,
      detail: a ? (a.state_detail || '') : (R.floor.why || 'The floor has not been read yet.'),
      since: a ? a.state_since : null,
      heartbeatAt: a && a.heartbeat ? a.heartbeat.at : null,
      deployed: a ? a.deployed !== false : null, deployWhy: a ? a.deploy_why : null,
      monitor: a ? (a.monitor || []) : [],
      last: a ? (a.last_output || a.focus || null) : null,
      readStale: !!a && !floorFresh(), readAt: R.floor.lastOk, readWhy: R.floor.why
    };
  }
  function edges() {
    const f = floorData(); if (!f) return [];
    return (f.edges || []).map((e) => {
      const a = BY_AGENT[e.from], b = BY_AGENT[e.to];
      if (!a || !b || a === b) return null;
      return {from: a.slug, to: b.slug, kind: e.kind, label: B.edgeLabel(e.kind), count: e.count || 1, at: e.at, summary: e.summary};
    }).filter(Boolean);
  }
  function equity() {
    const d = R.equity.data, v = d && d.actual && d.actual.venues || {};
    return {status: R.equity.status, why: R.equity.why, readAt: R.equity.lastOk, fresh: R.equity.status === 'OK', changedAt: R.equity.changedAt,
            paper: d && d.paper || null, small: d && d.small_live_bettor || null, pm: v.polymarket_us || null, kalshi: v.kalshi || null, fixture: B.isFixture(d)};
  }
  /* one line per real paper position: its real mark and when it was observed */
  function tickers() {
    const p = equity().paper, rows = p && p.open_positions && p.open_positions.rows || [];
    return rows.map((r) => ({market: r.market || r.key || 'UNKNOWN MARKET', side: r.side || '', state: r.mark_state,
      price: typeof r.mark_price === 'number' ? r.mark_price : null, at: r.mark_observed_at,
      pnl: typeof r.unrealized_pnl_usd === 'number' ? r.unrealized_pnl_usd : null, cost: r.cost_basis_usd, why: r.why_unmarked}));
  }
  const decisions = () => { const f = floorData(); return f ? (f.feed || []) : []; };
  const ranking = () => { const f = floorData(); return f ? (f.opportunities || []) : []; };
  function coverage() {
    const c = R.coverage.data, ls = c && c.league_status;
    if (R.coverage.status !== 'OK') return {status: R.coverage.status, why: R.coverage.why, rows: [], counts: {}};
    if (!ls || ls.status === 'UNAVAILABLE') return {status: 'UNAVAILABLE', why: ls && ls.why || 'NO_LEAGUE_STATUS', rows: [], counts: {}};
    const counts = {};
    (ls.statuses || []).forEach((r) => { counts[r.status] = (counts[r.status] || 0) + 1; });
    return {status: 'OK', day: ls.day, tz: ls.tz, rows: ls.statuses || [], counts, readAt: R.coverage.lastOk};
  }
  function xavierDecisions() {
    const x = R.detail.xavier, d = x && x.data;
    if (!d) return {status: x ? x.status : 'LOADING', why: x && x.why, rows: []};
    return {status: 'OK', readAt: x.lastOk, rows: (d.outputs || []).filter((o) => o.kind === 'xavier_management_assessments')};
  }
  const fixture = () => B.isFixture(R.floor.data) || B.isFixture(R.equity.data) || B.isFixture(R.coverage.data);

  return {SEATS, BY_SLUG, BY_AGENT, STATE, reads: R, setFloor, start, subscribe: (fn) => subs.push(fn),
    desk, desks: () => SEATS.map((s) => desk(s.slug)), edges, equity, tickers, decisions, ranking, coverage, xavierDecisions, fixture,
    selected: () => selected, setSelected: (s) => { selected = s; },
    util: {usd, signedUsd, clock, ago, epoch, words, esc: B.esc}};
}
