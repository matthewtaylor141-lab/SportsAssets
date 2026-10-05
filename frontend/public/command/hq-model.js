/* BETTOR HEADQUARTERS · the 3D command center's data model (ES module).
 *
 * The headquarters scene (hq-scene.js), its in-world screens (hq-screens.js)
 * and the HUD (hq.js) draw ONLY what this model holds. Every read is a
 * same-origin GET under /api/command/ through window.BTFloor.read (session
 * cookie, no-store; BTFloor refuses any other path):
 *
 *   floor     /api/command/floor                 30 s  agent seats, work states, edges, feed, ranking
 *   equity    /api/command/equity/live           15 s  PAPER book, SMALL LIVE, legacy mirror (separate)
 *   derek     /api/command/paper/derek?limit=40  30 s  PAPER decisions, fills, opportunities
 *   coverage  /api/command/coverage?days=2      120 s  funnel, per-league coverage, alerts
 *   release   /api/command/release               60 s  API / worker SHAs and alignment
 *   curve     /api/command/equity/curve?book=PAPER&window=1d   60 s  equity history (PAPER)
 *   capital   /api/command/profitability/capital  read on demand (Capital / Risk view)
 *   xavier    /api/command/floor/xavier           60 s  Xavier's recorded assessments
 *   adriana   /api/command/floor/adriana          60 s  her census, opportunities, refusals (only once served)
 *
 * TRUTH RULES:
 *   - no invented number: a missing value is UNAVAILABLE with its reason, an
 *     old read is STALE with its age, a fixture payload is labelled FIXTURE;
 *   - desk states are the floor API's states (work_state first, then state),
 *     translated one to one; only an active state on a fresh read may drive
 *     work motion or a lit desk;
 *   - Adriana's Arbitrage Desk is a PHYSICAL desk only until the floor API
 *     serves an ADRIANA seat: it reads NOT DEPLOYED · UNVERIFIED and nothing
 *     on it moves or glows. Once served, her seat is a desk like any other --
 *     its state, mission, outputs and monitor are the floor API's;
 *   - PAPER and SMALL LIVE (and each legacy venue) stay separate figures: no
 *     line of this file adds one book to another. */

/* Zone names for the in-world signs; names, accents and links come from
 * floor-core.js (BTFloor.SEATS). */
const ZONE = {
  derek: ['Discovery & entry', 'Chief Investment Officer'],
  karen: ['Adversarial review', 'Red team'],
  scout: ['Research & signals', 'Market intelligence'],
  allocator: ['Capital allocation', 'Chief Allocator'],
  adriana: ['Arbitrage desk', 'Head of Arbitrage'],
  archer: ['Execution & microstructure', 'Head of Execution'],
  audrey: ['Audit & reconciliation', 'Risk & audit'],
  xavier: ['Portfolio management', 'Portfolio Manager']
};

/* Adriana: the eighth desk. Until the floor API serves her seat this is a
 * static identity only -- never a state. */
export const ADRIANA = {agent: 'ADRIANA', slug: 'adriana', name: 'Adriana', short: 'Arbitrage',
  role: 'Head of Arbitrage · cross-venue arbitrage · shadow only', accent: '#7fd6ff', planned: true,
  plannedWhy: 'The serving API has no ADRIANA seat in GET /api/command/floor: nothing on this desk is live until it does.'};

/* the floor plan: the desk order around the horseshoe, west to east */
export const ORDER = ['derek', 'karen', 'scout', 'allocator', 'adriana', 'archer', 'audrey', 'xavier'];

/* THE FLOOR API STATE -> what the headquarters shows. One to one. */
export const STATE = {
  WORKING_ON:   {label: 'WORKING',          color: '#4fe0a8', tone: 'work',  active: true},
  WORKING:      {label: 'WORKING',          color: '#4fe0a8', tone: 'work',  active: true},
  REVIEWING:    {label: 'REVIEWING',        color: '#6cc0ff', tone: 'work',  active: true},
  CHALLENGING:  {label: 'CHALLENGING',      color: '#ff7d8e', tone: 'work',  active: true},
  HANDOFF_PENDING: {label: 'HANDOFF PENDING', color: '#8fb8ff', tone: 'wait', active: false},
  WAITING_FOR_FRESH_EVIDENCE: {label: 'WAITING · FRESH EVIDENCE', color: '#eab768', tone: 'wait', active: false},
  BLOCKED_ON_MARKET_DATA: {label: 'BLOCKED · MARKET DATA', color: '#ff9d6e', tone: 'wait', active: false},
  WAITING:      {label: 'WAITING',          color: '#eab768', tone: 'wait',  active: false},
  BLOCKED:      {label: 'BLOCKED',          color: '#ff9d6e', tone: 'wait',  active: false},
  IDLE_NO_OPEN_WORK: {label: 'NO OPEN WORK', color: '#9fb0c4', tone: 'idle', active: false},
  IDLE:         {label: 'NO TASK',          color: '#9fb0c4', tone: 'idle',  active: false},
  STALE:        {label: 'STALE',            color: '#7d8796', tone: 'stale', active: false},
  NOT_DEPLOYED: {label: 'NOT DEPLOYED',     color: '#6b7587', tone: 'off',   active: false},
  UNAVAILABLE:  {label: 'UNAVAILABLE',      color: '#6b7587', tone: 'off',   active: false}
};

export function createModel(B, opts) {
  const o = Object.assign({curve: true}, opts || {});
  const base = B.SEATS.map((s) => Object.assign({}, s));
  // Adriana's desk sits between the Chief Allocator and Execution
  const all = base.concat(base.some((s) => s.slug === 'adriana') ? [] : [ADRIANA]);
  const SEATS = ORDER.map((slug) => all.find((s) => s.slug === slug)).filter(Boolean).map((s) => ({
    agent: s.agent, slug: s.slug, name: s.name, accent: s.accent, planned: !!s.planned, staticPlanned: !!s.planned,
    zone: (ZONE[s.slug] || [s.short])[0], role: (ZONE[s.slug] || [null, s.short])[1], title: s.role, href: '/' + s.slug}));
  const BY_SLUG = {}, BY_AGENT = {};
  SEATS.forEach((s) => { BY_SLUG[s.slug] = s; BY_AGENT[s.agent] = s; });

  const fin = (n) => typeof n === 'number' && isFinite(n);
  const epoch = (v) => { if (v == null) return null; if (typeof v === 'number') return isFinite(v) ? (v > 1e12 ? v / 1000 : v) : null; if (/^\d+(\.\d+)?$/.test(String(v))) return epoch(Number(v)); const t = Date.parse(v); return isNaN(t) ? null : t / 1000; };
  const ago = (t) => { const e = epoch(t); return e == null ? 'never' : B.ago(e); };
  const clock = (t) => { const e = epoch(t); return e == null ? '—' : new Date(e * 1000).toISOString().slice(11, 19) + ' UTC'; };
  const hm = (t) => { const e = epoch(t); return e == null ? '—' : new Date(e * 1000).toISOString().slice(11, 16) + 'Z'; };
  const usd = (v, d) => {
    if (!fin(v)) return null;
    const dd = d == null ? 2 : d;
    const s = Math.abs(v).toLocaleString('en-US', {minimumFractionDigits: dd, maximumFractionDigits: dd});
    return (v < 0 && Number(s.replace(/,/g, '')) !== 0 ? '−$' : '$') + s;
  };
  const signedUsd = (v, d) => { const s = usd(v, d); return s == null ? null : (v > 0 ? '+' : '') + s; };
  const compactUsd = (v) => {
    if (!fin(v)) return null;
    const a = Math.abs(v), sg = v < 0 ? '−' : '';
    if (a >= 1e6) return sg + '$' + (a / 1e6).toFixed(2) + 'M';
    if (a >= 1e4) return sg + '$' + (a / 1e3).toFixed(1) + 'k';
    return usd(v, 0);
  };
  const words = (s) => String(s == null ? '' : s).replace(/_/g, ' ');

  const slot = () => ({status: 'LOADING', data: null, at: null, why: null, lastOk: null, http: null});
  const R = {floor: slot(), equity: Object.assign(slot(), {changedAt: null}), derek: slot(), coverage: slot(), release: slot(), curve: slot(), capital: slot(), detail: {}};
  let selected = null;
  const subs = [];
  const emit = (kind) => subs.forEach((fn) => { try { fn(kind); } catch (e) { if (window.console) console.warn('BETTOR HQ model subscriber', e); } });
  const store = (s, res) => {
    s.status = res.status; s.at = res.at; s.why = res.why || null; s.http = res.httpStatus;
    if (res.status === 'OK') { s.data = res.data; s.lastOk = res.at; }
    else if (s.data) { s.status = 'STALE'; s.why = res.why || res.status; }
  };

  /* a planned seat the floor API now serves is a desk like any other: its
   * planned flag follows the latest floor read (never the other way round on
   * a failed read -- an unread floor leaves the flag as it was) */
  function syncPlanned() {
    const f = R.floor.data; if (!f || R.floor.status !== 'OK') return;
    SEATS.forEach((s) => { if (s.staticPlanned) s.planned = !(f.agents || []).some((a) => a.slug === s.slug || a.agent === s.agent); });
  }
  const signedOutCbs = [];
  function onRead(kind, s, extra) {
    return (u) => {
      const prev = kind === 'equity' && s.data ? s.data.seq : null;
      store(s, u.current);
      if (kind === 'equity') {
        const seq = s.data && s.data.seq;
        // the server raises seq only when the content genuinely changed
        if (u.current.status === 'OK' && prev != null && seq != null && seq !== prev) s.changedAt = Date.now() / 1000;
      }
      if (u.current.status === 'SIGNED_OUT') signedOutCbs.forEach((f) => f());
      if (extra) extra();
      emit(kind);
    };
  }
  const pollers = {};
  function start() {
    pollers.floor = B.poller('/api/command/floor', 30000, onRead('floor', R.floor, syncPlanned));
    pollers.equity = B.poller('/api/command/equity/live', 15000, onRead('equity', R.equity));
    pollers.derek = B.poller('/api/command/paper/derek?limit=40', 30000, onRead('derek', R.derek));
    pollers.release = B.poller('/api/command/release', 60000, onRead('release', R.release));
    setTimeout(() => { pollers.coverage = B.poller('/api/command/coverage?days=2', 120000, onRead('coverage', R.coverage)); }, 1200);
    if (o.curve) setTimeout(() => { pollers.curve = B.poller('/api/command/equity/curve?book=PAPER&window=1d', 60000, onRead('curve', R.curve)); }, 1800);
    const xv = () => { if (document.hidden) return; readDetail('xavier'); if (BY_SLUG.adriana && !BY_SLUG.adriana.planned) readDetail('adriana'); };
    setTimeout(xv, 2500); setInterval(xv, 60000);
  }
  function readDetail(slug) {
    return B.read('/api/command/floor/' + slug).then((res) => { store(R.detail[slug] || (R.detail[slug] = slot()), res); emit('detail:' + slug); });
  }
  let capitalAt = 0;
  function readCapital(force) {
    if (!force && Date.now() - capitalAt < 60000) return Promise.resolve(R.capital);
    capitalAt = Date.now();
    return B.read('/api/command/profitability/capital').then((res) => { store(R.capital, res); emit('capital'); return R.capital; });
  }
  function refresh() { Object.keys(pollers).forEach((k) => pollers[k] && pollers[k].refresh()); }

  const floorData = () => R.floor.data;
  const floorFresh = () => R.floor.status === 'OK';
  function agentOf(slug) {
    const f = floorData(); if (!f) return null;
    const seat = BY_SLUG[slug];
    return (f.agents || []).find((a) => a.slug === slug || (seat && a.agent === seat.agent)) || null;
  }
  function stateCode(a, seat) {
    if (!a) return seat && seat.planned ? 'NOT_DEPLOYED' : 'UNAVAILABLE';
    if (a.deployed === false) return 'NOT_DEPLOYED';
    if (a.work_state && STATE[a.work_state]) return a.work_state;
    if (a.state && STATE[a.state]) {
      if (a.state === 'WAITING' && a.status_row && /^(BLOCKED|FAILED)$/.test(a.status_row.state || '')) return 'BLOCKED';
      return a.state;
    }
    return 'UNAVAILABLE';
  }
  function collaborators(slug) {
    const seat = BY_SLUG[slug]; if (!seat) return [];
    const map = {};
    edges().forEach((e) => {
      if (e.from !== slug && e.to !== slug) return;
      const other = e.from === slug ? e.to : e.from;
      const m = map[other] || (map[other] = {slug: other, name: BY_SLUG[other] ? BY_SLUG[other].name : other, count: 0, at: null, kinds: {}});
      m.count += e.count; m.kinds[e.label] = (m.kinds[e.label] || 0) + e.count;
      if (e.at != null && (m.at == null || e.at > m.at)) m.at = e.at;
    });
    return Object.values(map).sort((a, b) => (b.at || 0) - (a.at || 0));
  }
  function desk(slug) {
    const seat = BY_SLUG[slug], a = agentOf(slug);
    const code = stateCode(a, seat), meta = STATE[code] || STATE.UNAVAILABLE, level = a && a.authority && a.authority.level || '';
    const planned = !!(seat && seat.planned && !a);
    const ch = a && a.challenges || {};
    let attention = null;
    if (planned) attention = null;
    else if (code === 'STALE') attention = 'Heartbeat ' + (a && a.heartbeat && fin(a.heartbeat.age_s) ? B.age(a.heartbeat.age_s) + ' old' : 'never recorded');
    else if (code === 'BLOCKED_ON_MARKET_DATA' || code === 'BLOCKED') attention = (a && (a.work_detail || a.state_detail)) || 'blocked';
    else if (fin(ch.open_against) && ch.open_against > 0) attention = ch.open_against + ' open challenge' + (ch.open_against > 1 ? 's' : '') + ' against this desk';
    return {
      seat, slug, name: seat.name, role: seat.role, zone: seat.zone, title: seat.title, accent: seat.accent, planned,
      href: (a && a.workspace) || (planned ? null : seat.href), code, apiState: a ? a.state : null, workState: a ? a.work_state || null : null,
      label: planned ? 'NOT DEPLOYED · UNVERIFIED' : meta.label, color: meta.color, tone: meta.tone, active: !!meta.active && floorFresh() && !planned,
      shadow: /SHADOW/.test(level), authority: level,
      mission: a ? (a.title || a.role || seat.title) : seat.title,
      detail: planned ? ADRIANA.plannedWhy : a ? (a.work_detail || a.state_detail || '') : (R.floor.why || 'The floor has not been read yet.'),
      since: a ? (a.work_since || a.state_since) : null,
      heartbeatAt: a && a.heartbeat ? a.heartbeat.at : null,
      deployed: planned ? false : a ? a.deployed !== false : null, deployWhy: planned ? 'ADRIANA_SEAT_NOT_SERVED' : a ? a.deploy_why : null,
      monitor: a ? (a.monitor || []) : [],
      last: a ? (a.last_output || a.focus || null) : null,
      attention, challenges: ch,
      readStale: !!a && !floorFresh(), readAt: R.floor.lastOk, readWhy: R.floor.why
    };
  }
  function edges() {
    const f = floorData(); if (!f) return [];
    return (f.edges || []).map((e) => {
      const a = BY_AGENT[e.from], b = BY_AGENT[e.to];
      if (!a || !b || a === b) return null;
      return {from: a.slug, to: b.slug, kind: e.kind, label: B.edgeLabel(e.kind), count: e.count || 1, at: epoch(e.at), firstAt: epoch(e.first_at), summary: e.summary || null, evidence: e.evidence || []};
    }).filter(Boolean);
  }
  function equity() {
    const d = R.equity.data, v = d && d.actual && d.actual.venues || {};
    return {status: R.equity.status, why: R.equity.why, readAt: R.equity.lastOk, fresh: R.equity.status === 'OK', changedAt: R.equity.changedAt,
            paper: d && d.paper || null, small: d && d.small_live_bettor || null, actual: d && d.actual || null, pm: v.polymarket_us || null, kalshi: v.kalshi || null,
            computedAt: d ? epoch(d.computed_at) : null, fixture: B.isFixture(d)};
  }
  /* one line per real paper position: its real mark and when it was observed */
  function tickers() {
    const p = equity().paper, rows = p && p.open_positions && p.open_positions.rows || [];
    return rows.map((r) => ({market: r.market || r.key || 'UNKNOWN MARKET', side: r.side || '', state: r.mark_state, strategy: r.strategy || null,
      price: fin(r.mark_price) ? r.mark_price : null, at: r.mark_observed_at, qty: fin(r.open_qty) ? r.open_qty : null,
      pnl: fin(r.unrealized_pnl_usd) ? r.unrealized_pnl_usd : null, cost: fin(r.cost_basis_usd) ? r.cost_basis_usd : null,
      value: fin(r.marked_value_usd) ? r.marked_value_usd : null, why: r.why_unmarked}));
  }
  const decisions = () => { const f = floorData(); return f ? (f.feed || []).map((x) => Object.assign({}, x, {at: epoch(x.at)})) : []; };
  const ranking = () => { const f = floorData(); return f ? (f.opportunities || []) : []; };
  function coverage() {
    const c = R.coverage.data, ls = c && c.league_status;
    if (!c) return {status: R.coverage.status, why: R.coverage.why, rows: [], counts: {}};
    if (!ls || ls.status === 'UNAVAILABLE') return {status: 'UNAVAILABLE', why: ls && ls.why || 'NO_LEAGUE_STATUS', rows: [], counts: {}};
    const counts = {};
    (ls.statuses || []).forEach((r) => { counts[r.status] = (counts[r.status] || 0) + 1; });
    return {status: R.coverage.status, day: ls.day, tz: ls.tz, rows: ls.statuses || [], counts, readAt: R.coverage.lastOk};
  }
  /* Adriana's recorded census output (GET /api/command/floor/adriana): her
   * proven opportunities and her refusals, as recorded -- never inferred */
  function adrianaWork() {
    const x = R.detail.adriana, d = x && x.data;
    if (!d) return {status: x ? x.status : 'LOADING', why: x && x.why, opportunities: [], refusals: [], timeline: []};
    const outs = d.outputs || [];
    return {status: 'OK', readAt: x.lastOk,
      opportunities: outs.filter((r) => r.kind === 'adriana_arb_opportunities').map((r) => Object.assign({}, r, {at: epoch(r.at)})),
      refusals: outs.filter((r) => r.kind === 'adriana_arb_refusals').map((r) => Object.assign({}, r, {at: epoch(r.at)})),
      timeline: (d.timeline || []).map((e) => Object.assign({}, e, {at: epoch(e.at)}))};
  }
  function xavierDecisions() {
    const x = R.detail.xavier, d = x && x.data;
    if (!d) return {status: x ? x.status : 'LOADING', why: x && x.why, rows: []};
    return {status: 'OK', readAt: x.lastOk, rows: (d.outputs || []).filter((r) => r.kind === 'xavier_management_assessments')};
  }
  /* the PAPER equity history (equity/curve), oldest first; a point is a real recorded equity */
  function curve() {
    const c = R.curve.data;
    if (!c) return {status: R.curve.status, why: R.curve.why, points: []};
    const raw = c.points || c.series || (c.data && (c.data.points || c.data.series)) || [];
    const pts = raw.map((p) => Array.isArray(p) ? {t: epoch(p[0]), v: p[1]} : {t: epoch(p.t != null ? p.t : p.at != null ? p.at : p.ts), v: fin(p.equity_usd) ? p.equity_usd : fin(p.v) ? p.v : fin(p.value) ? p.value : null})
      .filter((p) => p.t != null && fin(p.v)).sort((a, b) => a.t - b.t);
    return {status: R.curve.status, why: c.why || R.curve.why, points: pts, readAt: R.curve.lastOk, window: c.window || null};
  }
  const fixture = () => B.isFixture(R.floor.data) || B.isFixture(R.equity.data) || B.isFixture(R.coverage.data);
  /* management attention: the Mobile Command derivation (mobile-command.js
   * BTMobile.model.attention) over these same reads -- one rule set, two surfaces */
  function attention() {
    const M = window.BTMobile && window.BTMobile.model;
    if (!M) return {items: [], read: [], missing: ['release', 'equity', 'coverage', 'floor']};
    const st = {};
    ['release', 'equity', 'coverage', 'floor', 'derek'].forEach((k) => {
      const s = R[k];
      st[k] = s.status === 'LOADING' ? null : {ok: s.status === 'OK', state: s.status === 'STALE' ? 'ERROR' : s.status, why: s.why, at: s.at ? s.at * 1000 : null, data: s.data};
    });
    return M.attention(st);
  }
  /* the six funnel stages for today (coverage days[0]) -- the Mobile Command rule */
  function funnel() {
    const M = window.BTMobile && window.BTMobile.model;
    return M ? M.funnel(R.coverage.data, R.coverage.why || (R.coverage.status === 'LOADING' ? 'reading coverage' : R.coverage.status)) : {stages: [], why: 'Mobile Command model not loaded'};
  }

  return {SEATS, BY_SLUG, BY_AGENT, STATE, reads: R, start, refresh, readCapital, subscribe: (fn) => subs.push(fn), onSignedOut: (fn) => signedOutCbs.push(fn),
    attention, funnel,
    desk, desks: () => SEATS.map((s) => desk(s.slug)), edges, collaborators, equity, tickers, decisions, ranking, coverage, xavierDecisions, adrianaWork, curve, fixture,
    readDetail, firstFloor: () => new Promise((res) => { if (R.floor.status !== 'LOADING') { res(); return; } subs.push((k) => { if (k === 'floor') res(); }); }),
    selected: () => selected, setSelected: (s) => { selected = s; },
    util: {usd, signedUsd, compactUsd, clock, hm, ago, epoch, words, fin, esc: B.esc, age: B.age}};
}
