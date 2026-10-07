/* BETTOR HEADQUARTERS · COMMAND (ES module).
 *
 * The 3D headquarters (hq-scene.js) is the command center; this file lays the
 * simple layer over it: five places to go (COMMAND · FLOOR · MARKETS ·
 * CAPITAL / RISK · REPORTS), live HUD instrumentation, a compact work panel
 * when the camera enters a desk, and "Watch BETTOR Work", which follows the
 * RECORDED agent events of the latest floor read -- never a synthetic one.
 * On a phone it renders the pocket command center from the same components
 * (no 3D room).
 *
 * READ ONLY. Every figure comes from hq-model.js (same-origin GETs under
 * /api/command/ through window.BTFloor.read). A value the API does not serve
 * renders DATA NOT AVAILABLE with its reason -- never 0, never a default. PAPER
 * is simulated execution on a fictional account; SMALL LIVE is shown exactly
 * as served (SHADOW); PAPER and SMALL LIVE are never added together. There is
 * no order, cancel, limit or capital control anywhere on this page. */
import {createModel} from './hq-model.js';

const B = window.BTFloor;
const DNA = 'DATA NOT AVAILABLE';
const doc = document;
const PHONE = !!(window.matchMedia && matchMedia('(max-width: 760px)').matches);
const REDUCED = B.reducedMotion();
const HQ = createModel(B);
const U = HQ.util;
const esc = U.esc, fin = U.fin;
const VIEWS = ['command', 'floor', 'markets', 'capital', 'reports'];
const S = {view: 'command', desk: null, scene: null, watch: null, signedOut: false, tagsAt: 0, report: 'daily'};
const $ = (s) => doc.querySelector(s);
const $$ = (s) => Array.prototype.slice.call(doc.querySelectorAll(s));
function setHTML(sel, html) { const el = typeof sel === 'string' ? $(sel) : sel; if (el && el.innerHTML !== html) el.innerHTML = html; }
function dna(why) { return '<span class="dna" title="' + esc(why || '') + '">' + DNA + '</span>' + (why ? '<span class="why">' + esc(why) + '</span>' : ''); }
function guard(name, fn) {
  try { fn(); } catch (err) {
    const el = $('[data-render="' + name + '"]');
    if (el) el.innerHTML = dna('this instrument could not render: ' + (err && err.message || err));
    if (window.console) console.warn('BETTOR HQ render', name, err);
  }
}
const nowS = () => Date.now() / 1000;
const pct = (v) => fin(v) ? (v > 0 ? '+' : '') + v.toFixed(2) + '%' : null;
const tone = (v) => !fin(v) ? '' : v > 0 ? 'pos' : v < 0 ? 'neg' : '';
const stTone = (s) => /^(OK|HEALTHY|ALIGNED|RUNNING|SHADOW|CURRENT|ENTER)$/.test(s || '') ? 'good' : /STALE|WAIT|PARTIAL|UNKNOWN|DEGRADED|PENDING|REFUS|BLOCK/.test(s || '') ? 'warn' : /UNAVAILABLE|FAIL|MISALIGNED|CRITICAL|STOP|ERROR|INCIDENT/.test(s || '') ? 'bad' : '';
const portrait = (d) => d.planned ? null : 'team-demo/assets/models/portraits/' + d.slug + '.jpg';
const short = (m) => String(m || '').replace(/^a[a-z]c-|^tsc-/, '').replace(/-20\d\d-\d\d-\d\d(-[a-z0-9]+)?$/, '').toUpperCase();
/* market family by slug prefix -- the backend's own grouping (api/app.py kind_group) */
const FAMILY = {aec: 'Moneyline', atc: 'Moneyline', asc: 'Spreads', tsc: 'Totals'};
const family = (m) => FAMILY[String(m || '').split('-')[0]] || 'Other';
const league = (m) => { const t = String(m || '').split('-'); return t.length > 1 ? t[1].toUpperCase() : 'UNKNOWN'; };

/* ═══════════════════════════ STATUS (top bar) ═══════════════════════════ */
function feedsRead() {
  const ks = ['floor', 'equity', 'derek', 'coverage', 'release'];
  return {ok: ks.filter((k) => HQ.reads[k].status === 'OK').length, n: ks.length, stale: ks.filter((k) => HQ.reads[k].status === 'STALE').length};
}
function renderTop() {
  const f = feedsRead(), q = HQ.equity(), p = q.paper, sm = q.small, rel = HQ.reads.release.data, al = rel && rel.alignment || {};
  const bits = [];
  if (S.signedOut) bits.push('<span class="hq-pill" data-tone="warn"><i class="dot warn"></i>SIGN-IN REQUIRED</span>');
  else bits.push('<span class="hq-pill" data-tone="' + (f.ok === f.n ? 'good' : f.ok ? 'warn' : 'bad') + '" title="feeds read OK in this session"><i class="dot ' + (f.ok === f.n ? 'good' : f.ok ? 'warn' : 'bad') + '"></i><b>' + f.ok + '/' + f.n + '</b> FEEDS</span>');
  bits.push('<span class="hq-pill hide-phone" data-tone="' + stTone(al.verdict) + '" title="API and worker build SHAs (GET /api/command/release)">API <b>' + esc(rel && rel.api && rel.api.short || '—') + '</b> · WRK <b>' + esc(rel && rel.workers && rel.workers.short || '—') + '</b> ' + esc(al.verdict || (rel ? 'UNKNOWN' : '')) + '</span>');
  const lane = p && p.lane && p.lane.state;
  bits.push('<span class="hq-pill hide-phone" data-tone="' + stTone(lane || (p && p.status)) + '">PAPER <b>' + esc(lane || (p ? p.status : '—')) + '</b></span>');
  bits.push('<span class="hq-pill" data-tone="' + (sm && sm.status === 'SHADOW' ? 'good' : 'warn') + '" title="' + esc(sm && sm.why || '') + '">SMALL LIVE <b>' + esc(sm && sm.status || '—') + '</b></span>');
  bits.push('<span class="hq-pill hide-phone num" id="hq-clock">' + new Date().toISOString().slice(11, 19) + 'Z</span>');
  setHTML('#hq-status', bits.join(''));
}

/* ═══════════════════════════ CRITICAL ALERT (every view) ════════════════ */
/* A CRITICAL management-attention item (the Mobile Command attention rule)
 * dominates the screen: a red bar under the top bar on every view until the
 * read that raised it clears. Nothing here is dismissable by the viewer. */
function criticalItems() { return HQ.attention().items.filter((x) => x.sev === 'CRITICAL'); }
function alertHTML() {
  const c = criticalItems(); if (!c.length) return '';
  const x = c[0];
  return '<div class="alert-bar" role="alert"><span class="alert-sev">Critical</span><b class="alert-n num">' + c.length + '</b>' +
    '<div class="alert-txt"><b>' + esc(x.title) + '</b><span>' + esc(x.detail || '') + (x.at ? ' · ' + esc(U.hm(x.at)) : '') + (c.length > 1 ? ' · +' + (c.length - 1) + ' more critical' : '') + '</span></div>' +
    '<button type="button" class="btn" data-go="reports" data-report-pick="daily">Briefing</button></div>';
}
function renderAlert() {
  const html = alertHTML();
  setHTML('#hq-alert', html);
  doc.body.classList.toggle('has-critical', !!html);
}

/* ═══════════════════════════ FRESHNESS STRIP ═══════════════════════════ */
/* Every staleness that changes what management should trust, side by side:
 * PAPER marks, Xavier's current management state per position, desks
 * blocked on market data or with a stale heartbeat, and the feeds. Each
 * cell is a recorded count or N/A with the reason -- never a default. */
function freshness() {
  const p = HQ.equity().paper, op = p && p.open_positions, ma = (p && p.marks_as_of) || {};
  const cells = [];
  if (op && fin(op.count)) {
    const st = op.stale_marks || 0, un = op.unmarked || 0, n = op.count;
    cells.push({k: 'Marks', v: (n - st - un) + '/' + n + ' fresh', s: st + ' stale > ' + (ma.stale_mark_after_s || 300) + 's · ' + un + ' unmarked', tone: st + un === 0 ? 'good' : (st + un) > n * 0.25 ? 'bad' : 'warn'});
  } else cells.push({k: 'Marks', v: null, s: equityWhy() || 'equity/live carried no open positions', tone: 'warn'});
  const xd = HQ.xavierDecisions();
  if (xd.status === 'OK') {
    const cur = xd.rows.filter((r) => !r.superseded_by);
    const bad = cur.filter((r) => /WAITING_FOR_FRESH_EVIDENCE|UNAVAILABLE/.test(r.management_state || '') || /^(STALE|INVALID)$/.test(r.recommendation_state || ''));
    cells.push({k: 'Management', v: bad.length + ' of ' + cur.length + ' stale', s: bad.length ? 'Xavier waiting for fresh evidence on ' + bad.length + ' position' + (bad.length > 1 ? 's' : '') : 'every current review on fresh evidence', tone: !bad.length ? 'good' : bad.length > cur.length / 2 ? 'bad' : 'warn'});
  } else cells.push({k: 'Management', v: null, s: 'floor/xavier ' + (xd.status === 'LOADING' ? 'reading' : (xd.why || xd.status)), tone: 'warn'});
  const ds = HQ.desks().filter((d) => !d.planned), blocked = ds.filter((d) => /BLOCKED|STALE/.test(d.code));
  cells.push({k: 'Desks', v: HQ.reads.floor.data ? (blocked.length ? blocked.length + ' blocked / stale' : 'all current') : null, s: HQ.reads.floor.data ? (blocked.length ? blocked.map((d) => d.name + ' ' + U.words(d.code).toLowerCase()).join(' · ') : ds.length + ' desks with a fresh state') : (HQ.reads.floor.why || 'reading the floor'), tone: !HQ.reads.floor.data ? 'warn' : blocked.length ? 'bad' : 'good'});
  const f = feedsRead();
  cells.push({k: 'Feeds', v: f.ok + '/' + f.n + ' read', s: f.stale ? f.stale + ' serving the last good read (STALE)' : f.ok === f.n ? 'every poll answered this session' : 'some reads not answered yet', tone: f.ok === f.n ? 'good' : f.stale ? 'bad' : 'warn'});
  return cells;
}
function freshHTML() {
  const cells = freshness(), worst = cells.some((c) => c.tone === 'bad') ? 'bad' : cells.some((c) => c.tone === 'warn') ? 'warn' : 'good';
  return '<div class="fresh-strip" data-tone="' + worst + '"><span class="fresh-h">Freshness</span>' + cells.map((c) =>
    '<div class="fcell" data-tone="' + c.tone + '"><small>' + esc(c.k) + '</small><b class="num">' + (c.v != null ? esc(c.v) : '<span class="dna">N/A</span>') + '</b><span>' + esc(c.s || '') + '</span></div>').join('') + '</div>';
}
function renderFresh() { $$('[data-render="fresh"]').forEach((el) => setHTML(el, freshHTML())); }

/* ═══════════════════════════ CAPITAL (left HUD) ═════════════════════════ */
function equityWhy() { const q = HQ.equity(); return q.paper ? null : (S.signedOut ? 'sign-in required' : q.why || (q.status === 'LOADING' ? 'reading equity/live' : 'equity/live carried no paper section')); }
function sparkSVG(points, opts) {
  const o = Object.assign({w: 280, h: 54, cls: 'spark', grid: false}, opts || {});
  if (!points || points.length < 2) return '';
  const t0 = points[0].t, t1 = points[points.length - 1].t, vs = points.map((p) => p.v);
  let lo = Math.min.apply(null, vs), hi = Math.max.apply(null, vs);
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
  const pad = (hi - lo) * 0.12; lo -= pad; hi += pad;
  const X = (t) => (t1 > t0 ? (t - t0) / (t1 - t0) : 1) * o.w, Y = (v) => o.h - (v - lo) / (hi - lo) * o.h;
  // a STEP line: a recorded value holds until the next genuine change (equity-wall.js rule)
  let d = 'M' + X(points[0].t).toFixed(1) + ' ' + Y(points[0].v).toFixed(1);
  for (let i = 1; i < points.length; i++) d += ' H' + X(points[i].t).toFixed(1) + ' V' + Y(points[i].v).toFixed(1);
  const area = d + ' V' + o.h + ' H0 Z';
  let grid = '';
  if (o.grid) {
    for (let i = 0; i <= 3; i++) { const v = hi - (hi - lo) * i / 3, y = Y(v); if (i === 3) { grid += '<line x1="0" x2="' + o.w + '" y1="' + y.toFixed(1) + '" y2="' + y.toFixed(1) + '"/>'; continue; } grid += '<line x1="0" x2="' + o.w + '" y1="' + y.toFixed(1) + '" y2="' + y.toFixed(1) + '"/><text x="4" y="' + (y - 4).toFixed(1) + '">' + esc(U.compactUsd(v)) + '</text>'; }
    grid += '<text x="4" y="' + (o.h - 4) + '">' + esc((t1 - t0 > 43200 ? new Date(t0 * 1000).toISOString().slice(5, 10) + ' ' : '') + U.hm(t0)) + '</text><text x="' + (o.w - 4) + '" y="' + (o.h - 4) + '" text-anchor="end">' + esc(U.hm(t1)) + '</text>';
  }
  const last = points[points.length - 1];
  return '<svg class="' + o.cls + '" viewBox="0 0 ' + o.w + ' ' + o.h + '" preserveAspectRatio="none" role="img" aria-label="PAPER equity, ' + points.length + ' recorded points from ' + esc(U.hm(t0)) + ' to ' + esc(U.hm(t1)) + '">' +
    '<defs><linearGradient id="' + (o.cls === 'curve' ? 'hq-curve-fill' : 'hq-spark-fill') + '" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5fe1ff" stop-opacity=".28"/><stop offset="1" stop-color="#5fe1ff" stop-opacity="0"/></linearGradient></defs>' +
    (grid ? '<g class="grid">' + grid + '</g>' : '') + '<path class="area" d="' + area + '"/><path class="line" d="' + d + '"/>' +
    (o.cls === 'curve' ? '<circle class="end" r="4" cx="' + X(last.t).toFixed(1) + '" cy="' + Y(last.v).toFixed(1) + '"/>' : '') + '</svg>';
}
/* THE MANAGEMENT EPOCH (equity/live paper.management, bettor_paper_epoch):
   the primary money figures start at $500,000 on 2026-10-05 00:00 ET. The
   ledger since funding is PRE-MANAGEMENT HISTORY, shown apart, never mixed
   in. Absent the section, the old figures are shown only under that label. */
function mgmt() { const p = HQ.equity().paper, m = p && p.management; return m && (m.status === 'OK' || m.status === 'DOES_NOT_RECONCILE') ? m : null; }
function mgmtWhy() { const p = HQ.equity().paper, m = p && p.management; return !p ? equityWhy() : !m ? 'the API does not serve the management epoch yet' : (m.status || 'UNAVAILABLE') + (m.why ? ': ' + m.why : ''); }
const MG_LABEL = 'MANAGEMENT START: OCT 5, 2026 · OPENING EQUITY $500,000';
function mgLabelHTML(m) { return '<div class="mg-start" title="' + esc((m && m.source) || '') + '">' + esc((m && m.label) || MG_LABEL) + '</div>'; }
function mgCurve(m) {
  const cv = HQ.curve();
  if (!m) return {points: [], why: 'management epoch not served'};
  if (!m.rebase_exact) return {points: [], why: 'management equity curve not reconstructable: ' + (m.high_water_mark_rule || '')};
  const off = (m.opening || {}).rebase_offset_usd, t0 = m.epoch_start_at;
  if (!fin(off)) return {points: [], why: 'no re-base offset served'};
  const pts = [{t: t0, v: m.opening_equity_usd}].concat(cv.points.filter((x) => x.t >= t0).map((x) => ({t: x.t, v: x.v + off})));
  return {points: pts, why: cv.why || (cv.status === 'LOADING' ? 'reading equity/curve' : 'fewer than two recorded points since the management start')};
}
function mgKpis(m, kv) {
  const ex = m.exposure || {};
  return kv('Opening equity', U.usd(m.opening_equity_usd, 0), 'Oct 5, 2026 00:00 ET') +
    kv('Current equity', U.usd(m.equity_usd, 0), m.status === 'OK' ? 'reconciles to the ledger' : 'DOES NOT RECONCILE', m.status === 'OK' ? '' : 'warn') +
    kv('Realized P&L', U.signedUsd(m.realized_pnl_usd, 0), 'since management start', tone(m.realized_pnl_usd)) +
    kv('Unrealized P&L', U.signedUsd(m.unrealized_pnl_usd, 0), 'since management start', tone(m.unrealized_pnl_usd)) +
    kv('Total P&L', U.signedUsd(m.total_pnl_usd, 0), 'since management start', tone(m.total_pnl_usd)) +
    kv('Return', pct(m.return_pct), 'on $500,000', tone(m.return_pct)) +
    kv('Drawdown', fin(m.drawdown_usd) ? U.usd(m.drawdown_usd, 0) : null, 'from HWM ' + (U.usd(m.high_water_mark_usd, 0) || '—') + (fin(m.drawdown_pct) ? ' · ' + m.drawdown_pct.toFixed(2) + '%' : ''), m.drawdown_usd > 0 ? 'neg' : '') +
    kv('Cash', U.usd(m.cash_usd, 0), 'available') +
    kv('Reserved', U.usd(m.reserved_usd, 0), 'resting orders') +
    kv('Marked value', U.usd(m.marked_open_position_value_usd, 0), (ex.open_positions != null ? ex.open_positions + ' open' : '') + (ex.unmarked ? ' · ' + ex.unmarked + ' unmarked at basis' : '')) +
    kv('Exposure', U.usd(ex.basis_usd, 0), 'management basis of open positions');
}
function mgHistoryHTML(m, p) {
  const h = (m && m.pre_management_history) || {}, si = (h.since_funding || (p && p.since_inception) || {});
  const eq = h.ledger_equity_usd != null ? h.ledger_equity_usd : p && p.equity_usd;
  return '<details class="mg-hist"><summary>PRE-MANAGEMENT HISTORY <span class="dim">· the ledger since funding, not in the figures above</span></summary><p>' +
    esc('Ledger equity ' + (U.usd(eq, 0) || '—') + (si.usd != null ? ' · ' + U.signedUsd(si.usd, 0) + ' (' + pct(si.pct) + ') since funding' : '') +
      ' · ledger realized ' + (U.signedUsd(h.ledger_realized_pnl_usd != null ? h.ledger_realized_pnl_usd : p && p.realized_pnl_usd, 0) || '—') +
      ' · ledger fees ' + (U.usd(h.ledger_fees_paid_usd != null ? h.ledger_fees_paid_usd : p && p.fees_paid_usd, 0) || '—') +
      (h.positions_closed_before_epoch != null ? ' · ' + h.positions_closed_before_epoch + ' positions closed before the start' : '') +
      '. Every trade, fill, settlement and audit record stays in the ledger.') + '</p></details>';
}
function mgUnverifiedHTML(m) {
  const u = (m && m.unverified_positions) || [];
  if (!u.length) return '';
  return '<div class="why warn">' + esc(u.length + ' carried position' + (u.length === 1 ? '' : 's') + ' EPOCH_OPEN_MARK_UNVERIFIED: no defensible midnight mark, so ' + (u.length === 1 ? 'it is' : 'they are') + ' held outside the management figures until reconciled (current marked value ' + (U.usd(u.reduce((a, x) => a + (x.current_marked_value_usd || 0), 0), 0) || '—') + ').') + '</div>';
}
function capitalHTML(compact) {
  const m = mgmt();
  if (m) return mgCapitalHTML(m, compact);
  return '<div class="why warn">MANAGEMENT EPOCH NOT SERVED · ' + esc(mgmtWhy()) + '</div>' + ledgerCapitalHTML(compact);
}
function mgCapitalHTML(m, compact) {
  const p = HQ.equity().paper, cv = mgCurve(m);
  const kv = (k, v, s, cls) => '<div><div class="k">' + esc(k) + '</div><div class="v ' + (cls || '') + '">' + (v != null ? esc(v) : '<span class="dna">N/A</span>') + '</div>' + (s ? '<span class="s">' + esc(s) + '</span>' : '') + '</div>';
  return '<div class="lbl">Management equity <span class="book-tag paper">PAPER · SIMULATED</span></div>' + mgLabelHTML(m) +
    '<div class="big num">' + esc(U.usd(m.equity_usd, 0) || '—') + '</div>' +
    '<div class="chg"><span class="' + tone(m.total_pnl_usd) + '">' + esc(U.signedUsd(m.total_pnl_usd, 0) + ' ' + (pct(m.return_pct) || '')) + '</span><span class="dim">since management start</span>' +
    '<span class="' + (m.drawdown_usd > 0 ? 'neg' : '') + '">' + esc('DD ' + (U.usd(m.drawdown_usd, 0) || '—')) + '</span><span class="dim">from HWM</span></div>' +
    (cv.points.length > 1 ? sparkSVG(cv.points) : '<div class="why" style="margin-top:10px">' + esc(cv.why) + '</div>') +
    '<div class="kv">' +
      kv('Cash', U.usd(m.cash_usd, 0), 'reserved ' + (U.usd(m.reserved_usd, 0) || '—')) +
      kv('Marked value', U.usd(m.marked_open_position_value_usd, 0), ((m.exposure || {}).open_positions != null ? m.exposure.open_positions + ' open' : null)) +
      kv('Realized P&L', U.signedUsd(m.realized_pnl_usd, 0), 'since start', tone(m.realized_pnl_usd)) +
      kv('Unrealized', U.signedUsd(m.unrealized_pnl_usd, 0), 'since start', tone(m.unrealized_pnl_usd)) +
      (compact ? '' : kv('Opening', U.usd(m.opening_equity_usd, 0), (m.carried_positions || 0) + ' carried at midnight marks') + kv('Exposure', U.usd((m.exposure || {}).basis_usd, 0), 'management basis')) +
    '</div>' + (m.status !== 'OK' ? '<div class="why warn">management book ' + esc(m.status) + (m.why ? ': ' + esc(m.why) : '') + '</div>' : '') + mgUnverifiedHTML(m) +
    (compact ? '' : mgHistoryHTML(m, p)) +
    '<div class="why" style="margin-top:10px">' + esc((p && p.label) || '') + ' · as of ' + esc(U.hm(p && p.source_at)) + '</div>';
}
function ledgerCapitalHTML(compact) {
  const q = HQ.equity(), p = q.paper, why = equityWhy();
  if (!p) return '<div class="lbl">Paper equity <span class="book-tag paper">PAPER · SIMULATED</span></div>' + dna(why);
  const dc = p.day_change || {}, si = p.since_inception || {}, ex = p.exposure || {}, op = p.open_positions || {};
  const cv = HQ.curve();
  const unOk = !(fin(op.count) && op.count > 0 && !op.marked);
  const kv = (k, v, s, cls) => '<div><div class="k">' + esc(k) + '</div><div class="v ' + (cls || '') + '">' + (v != null ? esc(v) : '<span class="dna">N/A</span>') + '</div>' + (s ? '<span class="s">' + esc(s) + '</span>' : '') + '</div>';
  return '<div class="lbl">PRE-MANAGEMENT HISTORY · ledger equity <span class="book-tag paper">PAPER · SIMULATED</span></div>' +
    '<div class="big num">' + esc(U.usd(p.equity_usd, 0) || '—') + '</div>' +
    '<div class="chg"><span class="' + tone(dc.usd) + '">' + esc(dc.usd != null ? U.signedUsd(dc.usd, 0) + ' ' + (pct(dc.pct) || '') : 'day N/A') + '</span><span class="dim">today ET</span>' +
    '<span class="' + tone(si.usd) + '">' + esc(si.usd != null ? pct(si.pct) : '') + '</span><span class="dim">since start</span></div>' +
    (cv.points.length > 1 ? sparkSVG(cv.points) : '<div class="why" style="margin-top:10px">equity curve: ' + esc(cv.why || (cv.status === 'LOADING' ? 'reading equity/curve' : 'fewer than two recorded points')) + '</div>') +
    '<div class="kv">' +
      kv('Available', U.usd(p.available_usd, 0), fin(p.reserved_usd) ? 'reserved ' + U.usd(p.reserved_usd, 0) : null) +
      kv('Deployed', U.usd(ex.cost_basis_usd, 0), fin(op.count) ? op.count + ' open · cost basis' : null) +
      kv('Realized P&L', U.signedUsd(p.realized_pnl_usd, 0), 'ledger', tone(p.realized_pnl_usd)) +
      kv('Unrealized', unOk ? U.signedUsd(p.unrealized_pnl_usd, 0) : null, fin(op.count) ? (op.marked || 0) + ' of ' + op.count + ' marked' : null, tone(p.unrealized_pnl_usd)) +
      (compact ? '' : kv('Cash', U.usd(p.cash_usd, 0), 'fees paid ' + (U.usd(p.fees_paid_usd, 0) || '—')) + kv('Marks', fin(op.stale_marks) ? (op.count - op.stale_marks - (op.unmarked || 0)) + ' fresh' : null, fin(op.stale_marks) ? op.stale_marks + ' stale > ' + ((p.marks_as_of || {}).stale_mark_after_s || 300) + 's' : null, op.stale_marks ? 'warn' : '')) +
    '</div>' + (p.status !== 'OK' ? '<div class="why warn">paper book ' + esc(p.status) + (p.why ? ': ' + esc(p.why) : '') + '</div>' : '') +
    '<div class="why" style="margin-top:10px">' + esc(p.label || '') + ' · as of ' + esc(U.hm(p.source_at)) + '</div>';
}
function renderCapital() { setHTML('[data-render="capital"]', capitalHTML(PHONE)); }

/* ═══════════════════════════ ATTENTION ═══════════════════════════════ */
function attentionHTML(max) {
  const a = HQ.attention();
  const crit = a.items.filter((x) => x.sev === 'CRITICAL').length;
  const head = '<div class="lbl">Management attention <em' + (crit ? ' class="crit"' : '') + '>' + (a.read.length ? a.items.length + ' item' + (a.items.length === 1 ? '' : 's') + (crit ? ' · ' + crit + ' critical' : '') : 'reading') + '</em></div>';
  if (!a.items.length) return head + '<div class="att"><div class="att-item" data-sev="INFO"><i style="background:var(--good)"></i><div><b>' + (a.read.length ? 'No blocker in ' + a.read.length + ' of 4 reads' : 'UNKNOWN · no read yet') + '</b><p>' + (a.missing.length ? 'Not read: ' + esc(a.missing.join(', ')) : 'Release, equity, coverage and floor all read.') + '</p></div></div></div>';
  const order = a.items.slice().sort((x, y) => (y.sev === 'CRITICAL') - (x.sev === 'CRITICAL'));
  return head + '<div class="att">' + order.slice(0, max || 4).map((x) =>
    '<div class="att-item" data-sev="' + esc(x.sev) + '"><i></i><div><b>' + esc(x.title) + '</b><p>' + esc(x.detail || '') + (x.at ? ' · ' + esc(U.hm(x.at)) : '') + '</p></div></div>').join('') +
    (a.items.length > (max || 4) ? '<p class="why">' + (a.items.length - (max || 4)) + ' more on the risk wall and in REPORTS</p>' : '') + '</div>';
}
function renderAttention() { $$('[data-render="attention"]').forEach((el) => setHTML(el, attentionHTML(PHONE ? 5 : 3))); }

/* ═══════════════════════════ THE TEAM ═══════════════════════════════ */
function teamHTML() {
  const ds = HQ.desks();
  if (!HQ.reads.floor.data) return '<div class="lbl">Agent activity</div>' + dna(S.signedOut ? 'sign-in required' : HQ.reads.floor.why || 'reading the floor');
  const active = ds.filter((d) => d.active).length;
  return '<div class="lbl">Agent activity <em>' + active + '/' + ds.length + ' at work</em>' + (PHONE ? '' : '<button type="button" data-watch aria-pressed="' + (S.watch ? 'true' : 'false') + '" title="Follow the recorded agent events of the latest floor read"><i class="dot blue"></i>Watch BETTOR work</button>') + '</div><div class="team">' + ds.map((d) => {
    const img = portrait(d);
    return '<button class="mate' + (d.planned ? ' planned' : '') + '" data-desk="' + esc(d.slug) + '" type="button">' +
      (img ? '<img src="' + esc(img) + '" alt="" width="34" height="34" loading="lazy">' : '<span class="ph">' + esc(d.name.slice(0, 2).toUpperCase()) + '</span>') +
      '<span><b>' + esc(d.name) + '</b><small>' + esc(d.planned ? 'Head of Arbitrage · seat not served' : (d.detail || d.zone)) + '</small></span>' +
      '<span class="st" style="color:' + esc(d.color) + '">' + esc(d.planned ? 'NOT DEPLOYED' : d.label) + '</span></button>';
  }).join('') + '</div>';
}
function renderTeam() { $$('[data-render="team"]').forEach((el) => setHTML(el, teamHTML())); }

/* ═══════════════════════════ COVERAGE ═══════════════════════════════ */
function coverageHTML() {
  const cv = HQ.coverage();
  const head = '<div class="lbl">Market coverage <em>' + (cv.day ? esc(cv.day) : '') + '</em></div>';
  if (!cv.rows.length) return head + dna(cv.why || (cv.status === 'LOADING' ? 'reading coverage' : cv.status));
  const cnt = cv.counts, keys = Object.keys(cnt);
  return head + '<div class="cov">' + cv.rows.slice(0, PHONE ? 24 : 15).map((r) => '<span data-tone="' + stTone(r.status === 'HEALTHY' ? 'OK' : r.status) + '" title="' + esc((r.league_name || r.league) + ' · ' + U.words(r.status) + (r.reason ? ' · ' + r.reason : '')) + '">' + esc(r.league_name || r.league) + '</span>').join('') + '</div>' +
    '<div class="why" style="margin-top:8px">' + keys.map((k) => esc(U.words(k)) + ' ' + cnt[k]).join(' · ') + '</div>';
}
function renderCoverage() { $$('[data-render="coverage"]').forEach((el) => setHTML(el, coverageHTML())); }

/* ═══════════════════════════ FUNNEL ═════════════════════════════════ */
function funnelHTML() {
  const fu = HQ.funnel();
  const head = '<div class="lbl">Today · provider events → PAPER fills <em>' + (fu.day ? esc(fu.day) + ' ' + esc(fu.tz || '') + (fu.partial ? ' · PARTIAL' : '') : '') + '</em></div>';
  if (!fu.stages || !fu.stages.length) return head + dna(fu.why);
  const top = fu.stages[0].value || 0;
  return head + '<div class="funnel">' + fu.stages.map((s) => {
    const w = top > 0 && s.value != null ? Math.max(2, Math.min(100, s.value / top * 100)) : 0;
    return '<div class="fstep' + (s.largestLoss ? ' loss' : '') + '" title="' + esc(s.why || '') + '"><small>' + esc(s.h) + '</small><b class="num">' + (s.value != null ? esc(U.words(Math.round(s.value).toLocaleString('en-US'))) : '<span class="dna">N/A</span>') + '</b>' +
      (s.largestLoss ? '<em>largest drop −' + esc(Math.round(s.largestLoss).toLocaleString('en-US')) + '</em>' : '') + '<i><span style="width:' + w.toFixed(1) + '%"></span></i></div>';
  }).join('') + '</div>';
}
function renderFunnel() { $$('[data-render="funnel"]').forEach((el) => setHTML(el, funnelHTML())); }

/* ═══════════════════════════ EXECUTION / FILL TAPE ══════════════════ */
function tapeRows() {
  const rows = [], D = HQ.reads.derek.data, fl = D && D.fills;
  ((fl && fl.data) || []).forEach((f) => {
    const at = U.epoch(f.filled_at); if (at == null) return;
    const l = f.label || {};
    rows.push({at, kind: 'FILL', who: 'PAPER FILL', what: (l.event_title || short(f.us_market_slug)) + ' · ' + (f.direction || '') + ' ' + (l.participant || f.holding_side || '') + (fin(Number(f.qty)) ? ' · ' + Number(f.qty).toLocaleString('en-US') : '') + (fin(Number(f.price)) ? ' @ ' + Number(f.price).toFixed(3) : '')});
  });
  HQ.edges().forEach((e) => { if (e.at != null) rows.push({at: e.at, kind: 'EDGE', who: (HQ.BY_SLUG[e.from] || {}).name + ' → ' + (HQ.BY_SLUG[e.to] || {}).name, what: e.label + (e.count > 1 ? ' ×' + e.count : '')}); });
  HQ.decisions().forEach((d) => { if (d.at != null) rows.push({at: d.at, kind: 'DECISION', who: 'DEREK ' + (d.verdict || ''), what: short(d.market || d.id) + (d.refusal ? ' · ' + U.words(d.refusal) : '')}); });
  return rows.sort((a, b) => b.at - a.at);
}
function tapeHTML(n) {
  const rows = tapeRows();
  const head = '<div class="lbl">' + (PHONE ? 'Watch BETTOR work' : 'Execution &amp; activity tape') + ' <em>recorded · newest first</em></div>';
  if (!HQ.reads.floor.data && !HQ.reads.derek.data) return head + dna(S.signedOut ? 'sign-in required' : 'reading floor and paper/derek');
  if (!rows.length) return head + '<p class="why">No recorded fill, hand-off or decision in this read. Nothing is synthesized.</p>';
  return head + '<div class="tape">' + rows.slice(0, n || 4).map((r) => '<div class="tape-row"><time class="num">' + esc(U.hm(r.at)) + '</time><span><b>' + esc(r.who) + '</b> · ' + esc(r.what) + '</span></div>').join('') + '</div>';
}
function renderTape() { $$('[data-render="tape"]').forEach((el) => setHTML(el, tapeHTML(PHONE ? 8 : (el.dataset.n ? +el.dataset.n : 3)))); }

/* ═══════════════════════════ FLOOR: desk tags ═══════════════════════ */
function renderTags() {
  const host = $('#hq-tags'); if (!host) return;
  if (!host.children.length) {
    host.innerHTML = HQ.SEATS.map((s) => '<button class="tag' + (s.planned ? ' planned' : '') + '" type="button" data-desk="' + esc(s.slug) + '" style="--accent:' + esc(s.planned ? '#7d8a9c' : s.accent) + '" hidden><span class="box"><b></b><small></small><i></i></span></button>').join('');
  }
  HQ.SEATS.forEach((s) => {
    const el = host.querySelector('[data-desk="' + s.slug + '"]'), d = HQ.desk(s.slug);
    el.querySelector('b').textContent = d.name;
    const sm = el.querySelector('small'); sm.textContent = d.label; sm.style.color = d.planned ? '#aab4c2' : d.color;
    el.querySelector('i').textContent = d.planned ? 'Head of Arbitrage · no live activity' : (d.detail || d.zone);
  });
}
function placeTags() {
  const host = $('#hq-tags'); if (!host || !S.scene) return;
  const show = (S.view === 'floor' || S.view === 'command') && !S.desk && !S.watch;
  host.classList.toggle('compact-all', S.view === 'command');
  $$('#hq-tags .tag').forEach((t) => t.classList.toggle('compact', S.view === 'command'));
  HQ.SEATS.forEach((s) => {
    const el = host.querySelector('[data-desk="' + s.slug + '"]'); if (!el) return;
    const a = show ? S.scene.anchorOf(s.slug) : null;
    if (!a || a.behind || (S.view === 'command' && (a.x < 350 || a.x > innerWidth - 370 || a.y > innerHeight - 130 || a.y < (doc.body.classList.contains('has-critical') ? 240 : 170)))) { el.hidden = true; return; }
    el.hidden = false; el.style.left = a.x.toFixed(1) + 'px'; el.style.top = (a.y - 8).toFixed(1) + 'px';
  });
}
function floorCountsHTML() {
  const ds = HQ.desks(), f = HQ.reads.floor.data;
  if (!f) return '<div class="lbl">The floor</div>' + dna(HQ.reads.floor.why || 'reading the floor');
  const by = (pred) => ds.filter(pred).length, edges = HQ.edges();
  return '<div class="lbl">The floor <em>window ' + Math.round((f.window_s || 3600) / 60) + ' min · read ' + esc(U.hm(HQ.reads.floor.lastOk)) + '</em></div>' +
    '<div class="floor-counts">' +
    '<div><b style="color:var(--good)">' + by((d) => d.active) + '</b><small>At work</small></div>' +
    '<div><b style="color:var(--warn)">' + by((d) => d.tone === 'wait') + '</b><small>Waiting / blocked</small></div>' +
    '<div><b>' + edges.length + '</b><small>Recorded hand-offs</small></div>' +
    '<div><b style="color:#aab4c2">' + by((d) => d.planned) + '</b><small>Not deployed</small></div>' +
    '<div style="align-self:center"><button type="button" data-watch aria-pressed="' + (S.watch ? 'true' : 'false') + '"><i class="dot blue"></i>Watch BETTOR work</button></div></div>';
}
function legendHTML() {
  const STATES = [['WORKING', '#4fe0a8'], ['REVIEWING', '#6cc0ff'], ['CHALLENGING', '#ff7d8e'], ['WAITING / HANDOFF', '#eab768'], ['BLOCKED', '#ff9d6e'], ['NOT DEPLOYED', '#7d8a9c']];
  return '<div class="lbl">Desk light = recorded work state</div><div class="legend-row">' + STATES.map((s) => '<span><i class="dot" style="background:' + s[1] + ';box-shadow:0 0 8px ' + s[1] + '"></i>' + s[0] + '</span>').join('') +
    '<span><i class="dot blue"></i>light path = a recorded hand-off or challenge</span></div>';
}
function renderFloor() {
  setHTML('[data-render="floor-counts"]', floorCountsHTML());
  setHTML('[data-render="legend"]', legendHTML());
  renderTags();
  // the phone has no 3D room: the desks as a list
  setHTML('[data-render="desk-cards"]', HQ.desks().map((d) => {
    const img = portrait(d);
    return '<button class="mate' + (d.planned ? ' planned' : '') + '" data-desk="' + esc(d.slug) + '" type="button" style="grid-template-columns:40px minmax(0,1fr) auto;justify-items:stretch;min-width:0;width:100%">' +
      (img ? '<img src="' + esc(img) + '" alt="" width="40" height="40" loading="lazy">' : '<span class="ph">' + esc(d.name.slice(0, 2).toUpperCase()) + '</span>') +
      '<span><b>' + esc(d.name) + ' · ' + esc(d.zone) + '</b><small>' + esc(d.planned ? 'Head of Arbitrage · no live activity' : (d.detail || '')) + '</small></span><span class="st" style="color:' + esc(d.planned ? '#aab4c2' : d.color) + '">' + esc(d.planned ? 'NOT DEPLOYED' : d.label) + '</span></button>';
  }).join(''));
}

/* ═══════════════════════════ DESK: the work panel ═══════════════════ */
function deskHTML(slug) {
  const d = HQ.desk(slug), img = portrait(d);
  let h = '<button class="desk-x" type="button" data-close-desk aria-label="Close the desk">×</button>' +
    '<div class="desk-head">' + (img ? '<img src="' + esc(img) + '" alt="' + esc(d.name) + '" width="64" height="64">' : '<span class="ph"></span>') +
    '<div><h2>' + esc(d.name) + '</h2><p>' + esc(d.zone) + ' · ' + esc(d.role || '') + '</p></div></div>';
  if (d.planned) {
    return h + '<div class="desk-state"><span class="chip" style="color:#aab4c2">NOT DEPLOYED</span><span class="chip" style="color:var(--warn)">UNVERIFIED</span></div>' +
      '<div class="planned-note"><b>NOT DEPLOYED · UNVERIFIED</b><p class="mono" style="margin-top:8px">' + esc(d.detail) + '</p></div>' +
      '<div class="desk-sec"><h3>What this desk will do</h3><p>' + esc(d.title || 'Head of Arbitrage') + '. It appears here as a physical desk only; it carries no mission, output, metric or collaboration until the floor API serves an ADRIANA seat.</p></div>' +
      '<div class="desk-sec"><h3>Live activity</h3><p class="mono">None. No simulated activity is shown.</p></div>' +
      '<div class="desk-actions"><button class="btn ghost" type="button" data-close-desk>Back to the floor</button></div>';
  }
  const collab = HQ.collaborators(slug);
  const mons = d.monitor.slice(0, 4);
  h += '<div class="desk-state"><span class="chip" style="color:' + esc(d.color) + '">' + esc(d.label) + '</span>' +
    (d.shadow ? '<span class="chip flat" style="color:var(--brass)">SHADOW · NO ORDER AUTHORITY</span>' : '') +
    (d.readStale ? '<span class="chip flat" style="color:var(--warn)">STALE READ</span>' : '') + '</div>' +
    '<div class="desk-sec"><h3>Current mission</h3><p>' + esc(d.mission || DNA) + '</p></div>' +
    '<div class="desk-sec"><h3>Working on now</h3><p class="mono">' + esc(d.detail || DNA) + (d.since ? '<span class="why">since ' + esc(U.clock(d.since)) + ' · ' + esc(U.ago(d.since)) + '</span>' : '') + '</p></div>' +
    '<div class="desk-sec"><h3>Latest consequential output</h3>' + (d.last && d.last.summary ? '<p class="mono">' + esc(d.last.summary) + '<span class="why">' + esc(U.words(d.last.kind || '')) + (d.last.at ? ' · ' + esc(U.clock(d.last.at)) : '') + '</span></p>' : '<p class="mono">' + DNA + ' · no output in this read</p>') + '</div>' +
    '<div class="desk-sec"><h3>Collaborating with · last hour</h3>' + (collab.length ? '<div class="desk-collab">' + collab.slice(0, 5).map((c) => '<div><span><b>' + esc(c.name) + '</b> · ' + esc(Object.keys(c.kinds).slice(0, 2).join(', ')) + '</span><span>×' + c.count + ' · ' + esc(U.hm(c.at)) + '</span></div>').join('') + '</div>' : '<p class="mono">No recorded hand-off or challenge involving ' + esc(d.name) + ' in the window.</p>') + '</div>' +
    (slug === 'adriana' ? adrianaDeskHTML() : '') +
    '<div class="desk-sec"><h3>Management attention</h3><p class="mono" style="color:' + (d.attention ? 'var(--warn)' : 'var(--ink-2)') + '">' + esc(d.attention || 'None recorded for this desk.') + '</p></div>' +
    (mons.length ? '<div class="desk-sec"><h3>Instruments</h3><div class="desk-metrics">' + mons.map((m) => '<div><small>' + esc(m.label) + '</small><b class="num">' + (m.value == null ? '<span class="dna">N/A</span>' : esc(typeof m.value === 'number' ? m.value.toLocaleString('en-US') : m.value)) + '</b>' + (m.value == null && m.why ? '<span class="why">' + esc(m.why) + '</span>' : '') + '</div>').join('') + '</div></div>' : '') +
    '<div class="desk-sec"><h3>Authority</h3><p class="mono">' + esc(U.words(d.authority || DNA)) + '</p></div>' +
    '<div class="desk-actions">' + (d.href ? '<a class="btn" href="' + esc(d.href) + '">Open workspace</a>' : '') + '<button class="btn ghost" type="button" data-close-desk>Back to the floor</button></div>' +
    '<p class="why" style="margin-top:12px">GET /api/command/floor · ' + (d.heartbeatAt ? 'heartbeat ' + esc(U.ago(d.heartbeatAt)) : 'no heartbeat recorded') + ' · read ' + esc(U.hm(d.readAt)) + '</p>';
  return h;
}
/* ADRIANA'S DESK, once served: her census as recorded -- the newest proven
 * opportunities (SHADOW, GUARANTEED_AFTER_COSTS only) and her refusals with
 * their codes. Read from GET /api/command/floor/adriana; never inferred. */
function adrianaDeskHTML() {
  const w = HQ.adrianaWork();
  if (w.status !== 'OK') return '<div class="desk-sec"><h3>Arbitrage census</h3><p class="mono">' + esc(w.status === 'LOADING' ? 'Reading her census (floor/adriana)…' : DNA + ' · ' + (w.why || w.status)) + '</p></div>';
  const opp = w.opportunities.slice(0, 4), ref = w.refusals.slice(0, 5);
  return '<div class="desk-sec"><h3>Proven after costs · SHADOW <em class="sub">' + w.opportunities.length + ' in this read</em></h3>' +
    (opp.length ? '<div class="desk-list">' + opp.map((r) => '<div><b>' + esc(r.summary) + '</b><span>' + esc(U.clock(r.at)) + '</span></div>').join('') + '</div>' : '<p class="mono">No structure proven after every fee, slippage and cost in this read. None is invented.</p>') + '</div>' +
    '<div class="desk-sec"><h3>Refused · with the reason</h3>' +
    (ref.length ? '<div class="desk-list">' + ref.map((r) => '<div><b>' + esc(r.summary) + '</b><span>' + esc(U.clock(r.at)) + '</span></div>').join('') + '</div>' : '<p class="mono">No refusal recorded in this read.</p>') + '</div>';
}
function openDesk(slug, opts) {
  if (!HQ.BY_SLUG[slug]) return;
  // a desk is entered from the floor: the other views' panels step away first
  if (!PHONE && S.view !== 'floor' && S.view !== 'command') {
    S.view = 'floor'; doc.body.setAttribute('data-view', 'floor');
    $$('#hq-nav button, #hq-tabbar button').forEach((b) => { if (b.dataset.go === 'floor') b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
    render();
  }
  S.desk = slug;
  if (slug === 'adriana' && HQ.BY_SLUG.adriana && !HQ.BY_SLUG.adriana.planned) HQ.readDetail('adriana');
  doc.body.classList.add('desk-open');
  setHTML('#hq-desk .ins', deskHTML(slug));
  if (S.scene) { S.scene.focus(slug); insets(); }
  if (!(opts && opts.keepHash)) history.replaceState(null, '', '#desk=' + slug);
  placeTags();
}
function closeDesk() {
  if (!S.desk) return;
  S.desk = null;
  doc.body.classList.remove('desk-open');
  if (S.scene) { S.scene.view(S.view === 'command' || S.view === 'floor' ? S.view : 'floor'); insets(); }
  history.replaceState(null, '', '#' + S.view);
  placeTags();
}

/* ═══════════════════════════ MARKETS ════════════════════════════════ */
function oppsHTML(max) {
  const D = HQ.reads.derek.data, M = window.BTMobile && window.BTMobile.model;
  const o = M ? M.opps(D) : {rows: [], why: 'Mobile Command model not loaded'};
  const head = '<div class="lbl">Opportunities · PAPER decisions <em>newest ' + (o.rows.length || 0) + ' · paper/derek</em></div>';
  if (!D) return head + dna(S.signedOut ? 'sign-in required' : HQ.reads.derek.why || 'reading paper/derek');
  if (!o.rows.length) return head + '<p class="why">' + esc(o.status === 'EMPTY' ? 'No PAPER decision recorded' : (o.why || DNA)) + '</p>';
  return head + '<table class="tbl"><thead><tr><th>Event</th><th>Verdict</th><th class="r">Gross edge</th><th class="r">Net EV</th><th class="hide-phone">When</th></tr></thead><tbody>' + o.rows.slice(0, max || 24).map((r) =>
    '<tr class="' + (r.verdict === 'ENTER' ? 'enter' : '') + '"><td><b>' + esc(r.title || DNA) + '</b><span class="why">' + esc([r.competition, r.strategy ? r.strategy.replace(/_/g, ' ') : null].filter(Boolean).join(' · ')) + (r.refusal ? ' · ' + esc(U.words(r.refusal)) : '') + '</span></td>' +
    '<td><span class="chip flat" style="color:' + (r.verdict === 'ENTER' ? 'var(--good)' : 'var(--ink-3)') + '">' + esc(r.verdict || '—') + '</span></td>' +
    '<td class="r num">' + (r.gross != null ? esc((r.gross > 0 ? '+' : '') + r.gross.toFixed(2) + ' pp') : '<span class="dim">—</span>') + '</td>' +
    '<td class="r num ' + tone(r.net) + '">' + (r.net != null ? esc(U.signedUsd(r.net)) : '<span class="dim">—</span>') + '</td>' +
    '<td class="hide-phone num">' + esc(U.hm(r.at)) + '</td></tr>').join('') + '</tbody></table><p class="why">Gross edge and net EV as recorded on each decision; blank where the decision carries no economics. PAPER only.</p>';
}
/* THE OPPORTUNITY BOOK (desktop Markets): every recorded PAPER decision with
 * its contract spelled out -- market family, line, period, subject -- and the
 * three things that decide whether a price is real: probability freshness
 * (Pinnacle age against its limit), settlement terms as recorded, and the
 * executable depth on the book. Decisions on the SAME contract of the SAME
 * event are one row (×N), grouped under their event. Read from
 * paper/derek; a field the decision does not carry is shown as not recorded. */
function mktRows(D) {
  const raw = (D && D.opportunities && D.opportunities.data) || [];
  return raw.map((d) => {
    const lab = d.label || {}, e = d.economics || {}, pd = d.policy_decision || {}, acq = e.acquisition || {}, pin = d.pinnacle || {}, bk = d.book || {};
    const at = U.epoch(d.decided_at), bAt = U.epoch(bk.observed_at);
    const lv = Array.isArray(e.levels) ? e.levels : [];
    const qty = lv.reduce((a, l) => a + (fin(Number(l.qty)) ? Number(l.qty) : 0), 0);
    let gross = fin(e.best_level_edge_pp) ? e.best_level_edge_pp : fin(pd.gross_edge_pp) ? pd.gross_edge_pp : null;
    let net = fin(acq.expected_net_profit_usd) ? acq.expected_net_profit_usd : fin(pd.net_expected_profit_usd) ? pd.net_expected_profit_usd : null;
    const refs = [d.refusal].concat(Array.isArray(d.refusals) ? d.refusals : []).filter(Boolean).map(String);
    const st = pd.settlement || lab.settlement || d.settlement || null;
    const setRef = refs.find((r) => /SETTLE/.test(r));
    const settlement = st ? (typeof st === 'string' ? st : (st.compatibility || st.status || st.state || null)) : setRef || null;
    const pAge = fin(pin.age_s) ? pin.age_s : null, pLim = fin(pin.limit_s) ? pin.limit_s : null;
    return {at, event: lab.event_title || null, comp: lab.competition || lab.sport_family || null, fixture: d.fixture || lab.event_title || d.us_market_slug || 'UNKNOWN EVENT',
      family: lab.market_type ? U.words(lab.market_type) : family(d.us_market_slug), line: lab.line != null && lab.line !== '' ? String(lab.line) : null,
      period: lab.period ? U.words(lab.period) : null, subject: lab.participant || null, side: d.holding_side || d.intent || null, slug: d.us_market_slug || null,
      verdict: d.verdict || null, refusal: d.refusal || null, gross, net,
      pAge, pLim, pFresh: pAge != null && pLim != null ? pAge <= pLim : null, bookAge: at != null && bAt != null ? Math.max(0, at - bAt) : null,
      bestAsk: fin(Number(bk.best_ask)) ? Number(bk.best_ask) : null, depthQty: lv.length ? qty : null, depthLv: lv.length, propQty: fin(Number(d.proposed_qty)) ? Number(d.proposed_qty) : null,
      settlement, setBad: !!(setRef || (settlement && /INCOMPAT|UNKNOWN|CONFLICT|UNESTABLISHED/.test(settlement)))};
  });
}
function groupMkt(rows) {
  const ev = new Map();
  rows.forEach((r) => {
    const g = ev.get(r.fixture) || {fixture: r.fixture, event: r.event || r.fixture, comp: r.comp, at: r.at, contracts: new Map(), n: 0};
    const key = [r.slug || '', r.family, r.line || '', r.period || '', r.subject || '', r.side || ''].join('|');
    const c = g.contracts.get(key);
    if (!c) g.contracts.set(key, Object.assign({n: 1, firstAt: r.at, verdicts: {[r.verdict || '—']: 1}}, r));
    else { c.n++; c.verdicts[r.verdict || '—'] = (c.verdicts[r.verdict || '—'] || 0) + 1; if ((r.at || 0) > (c.at || 0)) Object.assign(c, r, {n: c.n, firstAt: c.firstAt, verdicts: c.verdicts}); else c.firstAt = Math.min(c.firstAt || r.at, r.at || c.firstAt); }
    g.n++; if ((r.at || 0) > (g.at || 0)) g.at = r.at;
    ev.set(r.fixture, g);
  });
  return Array.from(ev.values()).sort((a, b) => (b.at || 0) - (a.at || 0)).map((g) => Object.assign(g, {contracts: Array.from(g.contracts.values()).sort((a, b) => (b.at || 0) - (a.at || 0))}));
}
function mktHTML(max) {
  const D = HQ.reads.derek.data;
  const head = (n, k) => '<div class="lbl">Opportunity book · PAPER decisions <em>' + (n != null ? n + ' decisions · ' + k + ' contracts · paper/derek' : 'paper/derek') + '</em></div>';
  if (!D) return head() + dna(S.signedOut ? 'sign-in required' : HQ.reads.derek.why || 'reading paper/derek');
  const rows = mktRows(D);
  if (!rows.length) return head(0, 0) + '<p class="why">' + esc((D.opportunities && D.opportunities.why) || 'No PAPER decision recorded') + '</p>';
  const groups = groupMkt(rows), k = groups.reduce((a, g) => a + g.contracts.length, 0);
  let shown = 0;
  const body = groups.map((g) => {
    if (shown >= (max || 40)) return '';
    const cs = g.contracts.slice(0, Math.max(1, (max || 40) - shown)); shown += cs.length;
    return '<tbody class="evt"><tr class="evt-h"><th colspan="8"><b>' + esc(g.event) + '</b><span>' + esc([g.comp, g.contracts.length + ' contract' + (g.contracts.length > 1 ? 's' : ''), g.n + ' decision' + (g.n > 1 ? 's' : ''), 'latest ' + U.hm(g.at)].filter(Boolean).join(' · ')) + '</span></th></tr>' +
      cs.map((c) => '<tr class="' + (c.verdict === 'ENTER' ? 'enter' : '') + '">' +
        '<td><b>' + esc([c.family, c.line, c.period].filter(Boolean).join(' · ') || DNA) + '</b><span class="why">' + esc((c.subject || c.side || 'subject not recorded') + (c.side && c.subject ? ' · ' + c.side : '')) + '</span></td>' +
        '<td><span class="chip flat" style="color:' + (c.verdict === 'ENTER' ? 'var(--good)' : 'var(--ink-2)') + '">' + esc(c.verdict || '—') + '</span>' + (c.n > 1 ? '<span class="dup num">×' + c.n + '</span>' : '') + (c.refusal ? '<span class="why">' + esc(U.words(c.refusal)) + '</span>' : '') + '</td>' +
        '<td class="num" data-tone="' + (c.pFresh == null ? '' : c.pFresh ? 'good' : 'bad') + '">' + (c.pAge != null ? 'PIN ' + Math.round(c.pAge) + 's' + (c.pLim != null ? ' / ' + Math.round(c.pLim) + 's' : '') : '<span class="dim">PIN n/r</span>') + '<span class="why">' + (c.bookAge != null ? 'book ' + Math.round(c.bookAge) + 's old' : 'book time n/r') + '</span></td>' +
        '<td data-tone="' + (c.setBad ? 'bad' : '') + '">' + (c.settlement ? esc(U.words(c.settlement)) : '<span class="dim">not recorded</span>') + '</td>' +
        '<td class="r num">' + (c.depthQty != null ? esc(Math.round(c.depthQty).toLocaleString('en-US')) + '<span class="why">' + c.depthLv + ' level' + (c.depthLv > 1 ? 's' : '') + (c.bestAsk != null ? ' · ask ' + c.bestAsk.toFixed(3) : '') + '</span>' : '<span class="dim">no levels</span>') + '</td>' +
        '<td class="r num">' + (c.gross != null ? esc((c.gross > 0 ? '+' : '') + c.gross.toFixed(2) + 'pp') : '<span class="dim">—</span>') + '</td>' +
        '<td class="r num ' + tone(c.net) + '">' + (c.net != null ? esc(U.signedUsd(c.net)) : '<span class="dim">—</span>') + '</td>' +
        '<td class="num">' + esc(U.hm(c.at)) + (c.n > 1 ? '<span class="why">first ' + esc(U.hm(c.firstAt)) + '</span>' : '') + '</td></tr>').join('') + '</tbody>';
  }).join('');
  return head(rows.length, k) + '<table class="tbl mkt"><thead><tr><th>Contract · family · line · period</th><th>Verdict</th><th>Freshness</th><th>Settlement</th><th class="r">Exec. depth</th><th class="r">Gross</th><th class="r">Net EV</th><th>When</th></tr></thead>' + body + '</table>' +
    '<p class="why">Each row is one contract; ×N = N decisions on that same contract (duplicates of one underlying event are grouped, never double-counted). PIN = Pinnacle probability age against its freshness limit at decision time. Depth = contracts offered across the recorded ask levels. PAPER only.</p>';
}
function rankingHTML() {
  const rk = HQ.ranking();
  const head = '<div class="lbl">Allocator ranking <span class="book-tag shadow">SHADOW</span></div>';
  if (!HQ.reads.floor.data) return head + dna(HQ.reads.floor.why || 'reading the floor');
  if (!rk.length) return head + '<p class="why">No allocator run in this read.</p>';
  const maxEv = Math.max.apply(null, rk.map((x) => fin(x.net_ev_per_dollar) ? x.net_ev_per_dollar : 0).concat([1e-9]));
  return head + '<div class="bars">' + rk.slice(0, 6).map((x) => '<div class="bar brass" title="' + esc(x.market || x.id) + '"><span>#' + esc(x.rank) + ' ' + esc(short(x.market || x.id).slice(0, 12)) + '</span><i><span style="width:' + (fin(x.net_ev_per_dollar) && x.net_ev_per_dollar > 0 ? Math.min(100, x.net_ev_per_dollar / maxEv * 100) : 0).toFixed(1) + '%"></span></i><em>' + (fin(x.net_ev_per_dollar) ? 'EV/$ ' + x.net_ev_per_dollar.toFixed(2) : '—') + '</em></div>').join('') + '</div>' +
    '<p class="why">' + esc(U.words(rk[0].binding_constraint || '')) + ' · run ' + esc(U.hm(rk[0].at)) + ' · shadow allocation, no capital authority</p>';
}
function renderMarkets() {
  $$('[data-render="opps"]').forEach((el) => setHTML(el, PHONE || el.dataset.max ? oppsHTML(el.dataset.max ? +el.dataset.max : 24) : mktHTML(60)));
  setHTML('[data-render="ranking"]', rankingHTML());
}

/* ═══════════════════════════ CAPITAL / RISK ═════════════════════════ */
const SLEEVE_COLORS = {INVESTMENT: '#5fe1ff', TRAINING: '#5b93ff', BENCHMARK: '#e2c08a', UNCLASSIFIED: '#7d8a9c'};
function ringSVG(parts, center, sub) {
  const R = 60, r = 44, C = 75, total = parts.reduce((a, p) => a + p.v, 0);
  let a0 = -Math.PI / 2, out = '';
  if (total <= 0) out = '<circle cx="75" cy="75" r="52" fill="none" stroke="rgba(255,255,255,.08)" stroke-width="16"/>';
  else parts.forEach((p) => {
    if (p.v <= 0) return;
    const a1 = a0 + p.v / total * Math.PI * 2 - 0.02, large = a1 - a0 > Math.PI ? 1 : 0;
    const pt = (rad, ang) => (C + rad * Math.cos(ang)).toFixed(2) + ' ' + (C + rad * Math.sin(ang)).toFixed(2);
    out += '<path d="M' + pt(R, a0) + ' A' + R + ' ' + R + ' 0 ' + large + ' 1 ' + pt(R, a1) + ' L' + pt(r, a1) + ' A' + r + ' ' + r + ' 0 ' + large + ' 0 ' + pt(r, a0) + ' Z" fill="' + p.c + '"/>';
    a0 = a1 + 0.02;
  });
  return '<svg class="ring" viewBox="0 0 150 150" role="img" aria-label="' + esc(sub + ' ' + center) + '">' + out + '<text x="75" y="76" text-anchor="middle">' + esc(center) + '</text><text class="s" x="75" y="92" text-anchor="middle">' + esc(sub) + '</text></svg>';
}
function gaugeSVG(frac, center, sub, color) {
  const R = 38, C = 46, a0 = Math.PI * 0.75, a1 = a0 + Math.PI * 1.5 * Math.max(0, Math.min(1, frac || 0));
  const pt = (ang) => (C + R * Math.cos(ang)).toFixed(2) + ' ' + (C + R * Math.sin(ang)).toFixed(2);
  const arc = (x, y) => 'M' + pt(x) + ' A' + R + ' ' + R + ' 0 ' + (y - x > Math.PI ? 1 : 0) + ' 1 ' + pt(y);
  return '<svg class="gauge" viewBox="0 0 92 92" role="img" aria-label="' + esc(sub + ' ' + center) + '"><path d="' + arc(a0, a0 + Math.PI * 1.5) + '" fill="none" stroke="rgba(255,255,255,.08)" stroke-width="7" stroke-linecap="round"/>' +
    (frac > 0 ? '<path d="' + arc(a0, a1) + '" fill="none" stroke="' + color + '" stroke-width="7" stroke-linecap="round"/>' : '') +
    '<text x="46" y="50" text-anchor="middle">' + esc(center) + '</text><text class="s" x="46" y="64" text-anchor="middle">' + esc(sub) + '</text></svg>';
}
function curveSize() {
  const el = $('[data-render="cap-curve"]');
  const w = el && el.clientWidth ? el.clientWidth - 34 : 600, h = el && el.clientHeight ? el.clientHeight - 96 : 230;
  return {w: Math.max(240, Math.round(w)), h: Math.max(120, Math.round(h))};
}
function capCurveHTML() {
  const m = mgmt();
  if (m) {
    const cv = mgCurve(m);
    return '<div class="lbl">Management equity curve · PAPER <em>' + (cv.points.length > 1 ? cv.points.length + ' points since Oct 5, 2026 00:00 ET · step line' : '') + '</em></div>' + mgLabelHTML(m) +
      (cv.points.length > 1 ? sparkSVG(cv.points, Object.assign({cls: 'curve', grid: true}, curveSize())) : dna(cv.why)) +
      '<div class="chg"><span>' + esc(U.usd(m.equity_usd)) + '</span><span class="' + tone(m.total_pnl_usd) + '">' + esc(U.signedUsd(m.total_pnl_usd) + ' since management start') + '</span><span class="dim">' + esc('HWM ' + (U.usd(m.high_water_mark_usd, 0) || '—') + ' · ' + (m.high_water_mark_basis || '')) + '</span></div>';
  }
  const cv = HQ.curve(), p = HQ.equity().paper;
  return '<div class="lbl">PRE-MANAGEMENT HISTORY · ledger equity curve <em>' + (cv.points.length ? cv.points.length + ' recorded points · step line' : '') + '</em></div>' +
    (cv.points.length > 1 ? sparkSVG(cv.points, Object.assign({cls: 'curve', grid: true}, curveSize())) : dna(cv.why || (cv.status === 'LOADING' ? 'reading equity/curve' : 'fewer than two recorded points'))) +
    (p ? '<div class="chg"><span>' + esc(U.usd(p.equity_usd)) + '</span><span class="' + tone((p.day_change || {}).usd) + '">' + esc((p.day_change || {}).usd != null ? U.signedUsd(p.day_change.usd) + ' today' : '') + '</span><span class="dim">' + esc(p.no_new_mark ? 'NO NEW MARK · ' + (p.no_new_mark_rule || '') : 'last genuine mark ' + U.hm(p.last_genuine_mark_update_at)) + '</span></div>' : '');
}
function capBookHTML() {
  const p = HQ.equity().paper;
  if (!p) return '<div class="lbl">The book</div>' + dna(equityWhy());
  const m = mgmt();
  if (m) {
    const kv = (k, v, s, cls) => '<div><div class="k">' + esc(k) + '</div><div class="v ' + (cls || '') + '">' + (v != null ? esc(v) : '<span class="dna">N/A</span>') + '</div>' + (s ? '<span class="s">' + esc(s) + '</span>' : '') + '</div>';
    const op = m.opening || {};
    return '<div class="lbl">The book · management epoch <span class="book-tag paper">PAPER · SIMULATED</span></div>' + mgLabelHTML(m) +
      '<div class="kv" style="grid-template-columns:repeat(3,minmax(0,1fr))">' + mgKpis(m, kv) +
      kv('Carried at open', String(m.carried_positions != null ? m.carried_positions : '—'), 'midnight value ' + (U.usd(op.carried_position_mark_value_usd, 0) || '—')) + '</div>' +
      '<div class="why">' + esc('Opening: cash ' + (U.usd(op.available_cash_usd, 2) || '—') + ' + reserved ' + (U.usd(op.reserved_usd, 2) || '—') + ' + carried ' + (U.usd(op.carried_position_mark_value_usd, 2) || '—') + ' = ' + (U.usd(m.opening_equity_usd, 2) || '—') + (op.identity_holds ? ' ✓' : ' (does not hold)') + ' · opened today ' + (m.opened_after_epoch || 0) + ' · settled today ' + (m.settled_after_epoch || 0) + ' · fees today ' + (U.usd(m.fees_after_epoch_usd, 0) || '—')) + '</div>' +
      (m.status !== 'OK' ? '<div class="why warn">management book ' + esc(m.status) + (m.why ? ': ' + esc(m.why) : '') + '</div>' : '') + mgUnverifiedHTML(m) + mgHistoryHTML(m, p);
  }
  const ex = p.exposure || {}, op = p.open_positions || {};
  const kv = (k, v, s, cls) => '<div><div class="k">' + esc(k) + '</div><div class="v ' + (cls || '') + '">' + (v != null ? esc(v) : '<span class="dna">N/A</span>') + '</div>' + (s ? '<span class="s">' + esc(s) + '</span>' : '') + '</div>';
  const C = HQ.reads.capital, cd = C.data && (C.data.data || C.data);
  const turnover = cd && (cd.turnover || cd.capital_turnover), ch = cd && (cd.capital_hours || cd.capital_hours_usd);
  const tv = turnover && (fin(turnover) ? turnover : fin(turnover.value) ? turnover.value : fin(turnover.ratio) ? turnover.ratio : null);
  const chv = ch && (fin(ch) ? ch : fin(ch.value) ? ch.value : fin(ch.total) ? ch.total : null);
  const capWhy = C.status === 'LOADING' ? 'reading profitability/capital' : C.status !== 'OK' && C.status !== 'STALE' ? 'profitability/capital ' + C.status + (C.why ? ': ' + C.why : '') : 'not served by profitability/capital';
  return '<div class="why warn">MANAGEMENT EPOCH NOT SERVED · ' + esc(mgmtWhy()) + '</div><div class="lbl">PRE-MANAGEMENT HISTORY · the ledger <span class="book-tag paper">PAPER · SIMULATED</span></div><div class="kv" style="grid-template-columns:repeat(3,minmax(0,1fr))">' +
    kv('Equity', U.usd(p.equity_usd, 0), 'marked-only ' + (U.usd(p.equity_marked_only_usd, 0) || '—')) +
    kv('Cash', U.usd(p.cash_usd, 0), 'start ' + (U.usd(p.starting_cash_usd, 0) || '—')) +
    kv('Available', U.usd(p.available_usd, 0), 'free to deploy') +
    kv('Reserved', U.usd(p.reserved_usd, 0), 'resting orders') +
    kv('Deployed', U.usd(ex.cost_basis_usd, 0), (op.count || 0) + ' open · cost') +
    kv('Marked value', U.usd(ex.marked_value_usd, 0), 'unmarked at cost ' + (U.usd(ex.unmarked_cost_basis_usd, 0) || '—')) +
    kv('Realized', U.signedUsd(p.realized_pnl_usd, 0), 'settled + closed', tone(p.realized_pnl_usd)) +
    kv('Unrealized', U.signedUsd(p.unrealized_pnl_usd, 0), 'marked positions only', tone(p.unrealized_pnl_usd)) +
    kv('Fees paid', U.usd(p.fees_paid_usd, 0), 'simulated venue fees') +
    kv('Turnover', tv != null ? tv.toFixed(2) + '×' : null, tv != null ? 'profitability/capital' : capWhy) +
    kv('Capital-hours', chv != null ? Math.round(chv).toLocaleString('en-US') : null, chv != null ? 'profitability/capital' : capWhy) +
    kv('Session change', (p.session_change || {}).usd != null ? U.signedUsd(p.session_change.usd, 0) : null, pct((p.session_change || {}).pct) || '', tone((p.session_change || {}).usd)) +
    '</div>';
}
function sleevesHTML() {
  const p = HQ.equity().paper, sl = p && p.sleeves;
  const head = '<div class="lbl">Sleeve allocation <em>deployed cost</em></div>';
  if (!sl || !sl.sleeves) return head + dna(p ? 'equity/live carried no sleeves section' : equityWhy());
  const keys = Object.keys(sl.sleeves);
  const parts = keys.map((k) => ({k, v: fin((sl.sleeves[k].exposure || {}).cost_basis_usd) ? sl.sleeves[k].exposure.cost_basis_usd : 0, c: SLEEVE_COLORS[k] || '#7d8a9c'}));
  const total = parts.reduce((a, x) => a + x.v, 0);
  return head + '<div class="ring-wrap">' + ringSVG(parts, U.compactUsd(total) || '—', 'DEPLOYED') + '<div class="sleeve-rows">' + keys.map((k) => {
    const x = sl.sleeves[k], ex = x.exposure || {};
    return '<div><i style="background:' + (SLEEVE_COLORS[k] || '#7d8a9c') + '"></i><span><b>' + esc(k) + '</b><span class="why" style="margin:0">' + esc(String(x.positions_open != null ? x.positions_open : '—')) + ' open · P&amp;L <span class="' + tone(x.net_pnl_usd) + '">' + esc(U.signedUsd(x.net_pnl_usd, 0) || '—') + '</span></span></span><span class="num">' + esc(U.compactUsd(ex.cost_basis_usd) || '$0') + '</span></div>';
  }).join('') + '<span class="why">' + esc((sl.equity_method && sl.equity_method.rule) ? U.words(sl.equity_method.rule) : '') + ' · ' + esc(sl.classifier_version || '') + '</span></div></div>';
}
function concentrationHTML() {
  const p = HQ.equity().paper, op = p && p.open_positions, rows = HQ.tickers();
  const head = '<div class="lbl">Exposure concentration <em>' + (op ? rows.length + ' of ' + op.count + ' open positions served' : '') + '</em></div>';
  if (!p) return head + dna(equityWhy());
  if (!rows.length) return head + '<p class="why">No open position rows in equity/live.</p>';
  const group = (fn) => { const m = {}; rows.forEach((r) => { const k = fn(r); m[k] = (m[k] || 0) + (r.cost || 0); }); return Object.entries(m).sort((a, b) => b[1] - a[1]); };
  const tot = rows.reduce((a, r) => a + (r.cost || 0), 0) || 1;
  const bars = (list, cls, n) => '<div class="bars">' + list.slice(0, n).map(([k, v]) => '<div class="bar ' + cls + '"><span>' + esc(k) + '</span><i><span style="width:' + (v / list[0][1] * 100).toFixed(1) + '%"></span></i><em>' + esc(U.compactUsd(v)) + ' <span class="dim">' + (v / tot * 100).toFixed(0) + '%</span></em></div>').join('') + '</div>';
  return head + '<div class="subhead" style="margin-top:0">By league / competition</div>' + bars(group((r) => league(r.market)), '', PHONE ? 6 : 5) +
    '<div class="subhead">By market family</div>' + bars(group((r) => family(r.market)), 'fam', 4) +
    '<div class="subhead">By strategy</div>' + bars(group((r) => (r.strategy || 'UNKNOWN').replace(/_PAPER$|_POLICY_V\d+$/, '').replace(/_/g, ' ')), 'brass', 3) +
    '<p class="why">Venue: ' + esc(p.venue || 'Polymarket US (paper book of the fictional account)') + ' · cost basis of the served rows; family by slug prefix (moneyline / spreads / totals).</p>';
}
function exceptionsHTML() {
  const p = HQ.equity().paper, op = p && p.open_positions, ma = p && p.marks_as_of || {};
  const head = '<div class="lbl">Mark freshness &amp; exceptions</div>';
  if (!p) return head + dna(equityWhy());
  const lim = ma.stale_mark_after_s || 300, cnt = op && op.count || 0, stale = op && op.stale_marks || 0, unm = op && op.unmarked || 0;
  const fresh = cnt ? (cnt - stale - unm) / cnt : 0;
  const t = HQ.tickers(), ex = t.filter((r) => r.price == null || (U.epoch(r.at) != null && nowS() - U.epoch(r.at) > lim)).slice(0, PHONE ? 6 : 8);
  const mg = freshness().find((c) => c.k === 'Management');
  return head + (mg ? '<div class="mg-line" data-tone="' + mg.tone + '"><b>Management</b> ' + esc(mg.v != null ? mg.v : 'N/A') + ' <span>' + esc(mg.s || '') + '</span></div>' : '') + '<div class="fresh">' + gaugeSVG(fresh, cnt ? Math.round(fresh * 100) + '%' : 'N/A', 'FRESH MARKS', fresh > 0.8 ? '#3fe0a3' : fresh > 0.5 ? '#f2b45a' : '#ff6f7d') +
    '<div class="kv" style="margin-top:0;grid-template-columns:1fr 1fr"><div><div class="k">Stale marks</div><div class="v warn">' + stale + '</div><span class="s">older than ' + lim + 's</span></div><div><div class="k">Unmarked</div><div class="v ' + (unm ? 'warn' : '') + '">' + unm + '</div><span class="s">add nothing to P&amp;L</span></div>' +
    '<div><div class="k">Newest mark</div><div class="v">' + esc(U.hm(ma.newest_at)) + '</div><span class="s">' + esc(ma.newest_is ? 'book re-read' : '') + '</span></div><div><div class="k">Last price change</div><div class="v">' + esc(U.hm(p.last_genuine_mark_update_at)) + '</div><span class="s">' + esc(p.no_new_mark ? 'NO NEW MARK' : 'genuine change') + '</span></div></div></div>' +
    '<div class="subhead">Exception positions</div>' + (ex.length ? '<table class="tbl"><tbody>' + ex.map((r) => '<tr><td><b>' + esc(short(r.market)) + '</b> <span class="dim">' + esc(r.side) + '</span><span class="why">' + esc(r.price == null ? 'UNMARKED · ' + (r.why || 'no mark') : 'mark ' + Math.round(nowS() - U.epoch(r.at)) + 's old') + '</span></td><td class="r num">' + esc(U.usd(r.cost, 0) || '—') + '</td></tr>').join('') + '</tbody></table>' : '<p class="why">No stale or unmarked position among the served rows.</p>');
}
function renderCapitalView() {
  setHTML('[data-render="cap-curve"]', capCurveHTML());
  setHTML('[data-render="cap-book"]', capBookHTML());
  setHTML('[data-render="cap-sleeves"]', sleevesHTML());
  setHTML('[data-render="cap-conc"]', concentrationHTML());
  setHTML('[data-render="cap-exc"]', exceptionsHTML());
}

/* ═══════════════════════════ REPORTS ════════════════════════════════ */
/* Four executive reports, each compiled in the browser from the same reads
 * as the rest of the page (never a stored or fabricated figure), and one
 * branded print / PDF flow: the BETTOR letterhead, the report name, the
 * compile time and the read-only footer appear only on paper. */
const REPORTS = [
  {id: 'daily', name: 'Daily management briefing', note: 'The book, today\'s funnel, the team and what needs attention'},
  {id: 'capital', name: 'Capital & risk', note: 'Equity, sleeves, concentration, mark freshness and exceptions'},
  {id: 'markets', name: 'Markets & opportunities', note: 'The funnel, the opportunity book by event and the refusal mix'},
  {id: 'team', name: 'The team & arbitrage desk', note: 'Every desk\'s state, output and collaboration, Adriana\'s census'}
];
function dailyReport() {
  const q = HQ.equity(), p = q.paper, a = HQ.attention(), fu = HQ.funnel(), ds = HQ.desks(), rel = HQ.reads.release.data, cv = HQ.coverage();
  const kv = (k, v, s, cls) => '<div><div class="k">' + esc(k) + '</div><div class="v ' + (cls || '') + '">' + (v != null ? esc(v) : '<span class="dna">N/A</span>') + '</div>' + (s ? '<span class="s">' + esc(s) + '</span>' : '') + '</div>';
  const enter = fu.stages && fu.stages.find((s) => s.k === 'entered_events'), fills = fu.stages && fu.stages.find((s) => s.k === 'filled_events'), prov = fu.stages && fu.stages[0];
  const dc = p && p.day_change || {}, m = mgmt();
  if (m) {
    const hl = 'Management equity stands at ' + U.usd(m.equity_usd, 0) + ', ' + U.signedUsd(m.total_pnl_usd, 0) + ' (' + pct(m.return_pct) + ') since the management start on Oct 5, 2026 (opening equity $500,000), with ' + (U.usd(m.cash_usd, 0) || 'an unknown amount') + ' cash and ' + (U.usd(m.marked_open_position_value_usd, 0) || 'an unknown amount') + ' in marked open positions.';
    return '<p class="mg-start">' + esc(m.label || MG_LABEL) + '</p><h2>Headline</h2><p>' + esc(hl) + ' SMALL LIVE is ' + esc((q.small && q.small.status) || 'not readable') + '; nothing on this page can place, cancel or size an order.</p>' +
      '<h2>The book · management epoch</h2><div class="kv">' + mgKpis(m, kv) + '</div>' + mgUnverifiedHTML(m) +
      '<h2>Pre-management history</h2><p>' + esc('Ledger equity ' + (U.usd(p.equity_usd, 0) || '—') + ' since funding; ledger realized ' + (U.signedUsd(p.realized_pnl_usd, 0) || '—') + '. Kept for audit, not in the management figures.') + '</p>' +
      dailyRest(q, a, fu, ds, rel, cv, enter, fills, prov);
  }
  const headline = !p ? 'The paper book is not readable right now (' + (equityWhy() || 'no reason') + ').' :
    'PAPER equity stands at ' + U.usd(p.equity_usd, 0) + (dc.usd != null ? ', ' + (dc.usd >= 0 ? 'up ' : 'down ') + U.usd(Math.abs(dc.usd), 0) + ' (' + pct(dc.pct) + ') today' : '') + ', with ' + (U.usd(p.available_usd, 0) || 'an unknown amount') + ' available and ' + (U.usd((p.exposure || {}).cost_basis_usd, 0) || 'an unknown amount') + ' deployed across ' + ((p.open_positions || {}).count != null ? p.open_positions.count : 'an unknown number of') + ' open positions.';
  return '<h2>Headline</h2><p>' + esc(headline) + ' SMALL LIVE is ' + esc((q.small && q.small.status) || 'not readable') + '; nothing on this page can place, cancel or size an order.</p>' +
    '<h2>The book</h2><div class="kv">' + (p ? kv('Equity', U.usd(p.equity_usd, 0)) + kv('Today', dc.usd != null ? U.signedUsd(dc.usd, 0) : null, pct(dc.pct), tone(dc.usd)) + kv('Realized', U.signedUsd(p.realized_pnl_usd, 0), null, tone(p.realized_pnl_usd)) + kv('Unrealized', U.signedUsd(p.unrealized_pnl_usd, 0), 'marked only', tone(p.unrealized_pnl_usd)) +
      kv('Available', U.usd(p.available_usd, 0)) + kv('Deployed', U.usd((p.exposure || {}).cost_basis_usd, 0)) + kv('Open positions', String((p.open_positions || {}).count != null ? p.open_positions.count : '—')) + kv('Stale marks', String((p.open_positions || {}).stale_marks != null ? p.open_positions.stale_marks : '—'), null, (p.open_positions || {}).stale_marks ? 'warn' : '') : kv('Equity', null)) + '</div>' +
    dailyRest(q, a, fu, ds, rel, cv, enter, fills, prov);
}
function dailyRest(q, a, fu, ds, rel, cv, enter, fills, prov) {
  return '<h2>Opportunities today</h2><p>' + (prov ? esc((prov.value != null ? Math.round(prov.value).toLocaleString('en-US') : 'An unmeasured number of') + ' provider events today; ' + (enter && enter.value != null ? Math.round(enter.value).toLocaleString('en-US') : 'an unmeasured number') + ' reached ENTER and ' + (fills && fills.value != null ? Math.round(fills.value).toLocaleString('en-US') : 'an unmeasured number') + ' were filled on PAPER' + (fu.partial ? ' (partial: some leagues did not measure every stage)' : '') + '.') : esc('The funnel is not readable: ' + (fu.why || 'no reason'))) + '</p>' +
    '<h2>What the team did</h2><ul>' + ds.map((d) => '<li><b>' + esc(d.name) + '</b> — ' + esc(d.planned ? 'NOT DEPLOYED · UNVERIFIED (no activity)' : d.label + (d.last && d.last.summary ? ': ' + (d.last.summary.length > 110 ? d.last.summary.slice(0, 108) + '…' : d.last.summary) : '')) + '</li>').join('') + '</ul>' +
    '<h2>Needs management attention</h2>' + (a.items.length ? '<ul>' + a.items.slice(0, 8).map((x) => '<li><b>' + esc(x.sev) + '</b> · ' + esc(x.title) + ' — ' + esc(x.detail || '') + '</li>').join('') + '</ul>' : '<p>' + esc(a.read.length ? 'No blocker in ' + a.read.length + ' of 4 reads.' : 'Reads not complete.') + '</p>') +
    '<h2>Coverage &amp; systems</h2><p>' + esc(cv.rows.length ? cv.rows.length + ' competitions in today\'s league status: ' + Object.keys(cv.counts).map((k) => U.words(k) + ' ' + cv.counts[k]).join(', ') + '.' : 'League status not readable (' + (cv.why || cv.status) + ').') + ' ' +
      esc(rel ? 'API ' + ((rel.api || {}).short || '—') + ' and workers ' + ((rel.workers || {}).short || '—') + ': ' + ((rel.alignment || {}).verdict || 'UNKNOWN') + '.' : 'Release identity not readable.') + '</p>' +
    '';
}
const kvx = (k, v, sub, cls) => '<div><div class="k">' + esc(k) + '</div><div class="v ' + (cls || '') + '">' + (v != null ? esc(v) : '<span class="dna">N/A</span>') + '</div>' + (sub ? '<span class="s">' + esc(sub) + '</span>' : '') + '</div>';
function capitalReport() {
  const p = HQ.equity().paper;
  if (!p) return '<p>' + esc('The paper book is not readable right now (' + (equityWhy() || 'no reason') + ').') + '</p>';
  const ex = p.exposure || {}, op = p.open_positions || {}, sl = p.sleeves && p.sleeves.sleeves || {}, rows = HQ.tickers();
  const tot = rows.reduce((a, r) => a + (r.cost || 0), 0) || 0;
  const grp = (fn) => { const m = {}; rows.forEach((r) => { const k = fn(r); m[k] = (m[k] || 0) + (r.cost || 0); }); return Object.entries(m).sort((a, b) => b[1] - a[1]); };
  const tbl = (list) => '<table class="rtbl"><tbody>' + list.slice(0, 6).map(([k, v]) => '<tr><td>' + esc(k) + '</td><td class="r num">' + esc(U.compactUsd(v)) + '</td><td class="r num">' + (tot ? (v / tot * 100).toFixed(0) + '%' : '—') + '</td></tr>').join('') + '</tbody></table>';
  const fr = freshness(), m = mgmt();
  const book = m ? '<p class="mg-start">' + esc(m.label || MG_LABEL) + '</p><h2>The book · management epoch</h2><div class="kv">' + mgKpis(m, kvx) + '</div>' + mgUnverifiedHTML(m) +
      '<h2>Pre-management history</h2><p>' + esc('Ledger equity ' + (U.usd(p.equity_usd, 0) || '—') + ' · ledger cash ' + (U.usd(p.cash_usd, 0) || '—') + ' · ledger realized ' + (U.signedUsd(p.realized_pnl_usd, 0) || '—') + ' · ledger unrealized ' + (U.signedUsd(p.unrealized_pnl_usd, 0) || '—') + '. Kept for audit, not in the management figures.') + '</p>' : null;
  return (book || '<h2>The book (pre-management history: the management epoch is not served)</h2><div class="kv">' + kvx('Equity', U.usd(p.equity_usd, 0)) + kvx('Cash', U.usd(p.cash_usd, 0)) + kvx('Available', U.usd(p.available_usd, 0)) + kvx('Reserved', U.usd(p.reserved_usd, 0)) +
      kvx('Deployed', U.usd(ex.cost_basis_usd, 0), (op.count != null ? op.count : '—') + ' open') + kvx('Marked value', U.usd(ex.marked_value_usd, 0)) + kvx('Realized', U.signedUsd(p.realized_pnl_usd, 0), null, tone(p.realized_pnl_usd)) + kvx('Unrealized', U.signedUsd(p.unrealized_pnl_usd, 0), 'marked only', tone(p.unrealized_pnl_usd)) + '</div>') +
    '<h2>Sleeves</h2>' + (Object.keys(sl).length ? '<table class="rtbl"><thead><tr><th>Sleeve</th><th class="r">Open</th><th class="r">Deployed</th><th class="r">Net P&amp;L</th></tr></thead><tbody>' + Object.keys(sl).map((k) => { const x = sl[k]; return '<tr><td>' + esc(k) + '</td><td class="r num">' + esc(String(x.positions_open != null ? x.positions_open : '—')) + '</td><td class="r num">' + esc(U.compactUsd((x.exposure || {}).cost_basis_usd) || '—') + '</td><td class="r num ' + tone(x.net_pnl_usd) + '">' + esc(U.signedUsd(x.net_pnl_usd, 0) || '—') + '</td></tr>'; }).join('') + '</tbody></table>' : '<p>' + DNA + ' · equity/live carried no sleeves section.</p>') +
    '<h2>Concentration</h2><p class="small">Cost basis of the ' + rows.length + ' served of ' + (op.count != null ? op.count : '?') + ' open positions.</p><div class="rep-2">' + '<div><h3>By league</h3>' + tbl(grp((r) => league(r.market))) + '</div><div><h3>By market family</h3>' + tbl(grp((r) => family(r.market))) + '</div></div>' +
    '<h2>Freshness</h2><ul>' + fr.map((c) => '<li><b>' + esc(c.k) + '</b> — ' + esc(c.v != null ? c.v : 'N/A') + (c.s ? ' · ' + esc(c.s) : '') + '</li>').join('') + '</ul>' +
    '<p>SMALL LIVE is ' + esc((HQ.equity().small || {}).status || 'not readable') + ' and is never added to the PAPER figures above.</p>';
}
function marketsReport() {
  const fu = HQ.funnel(), D = HQ.reads.derek.data, rk = HQ.ranking();
  const rows = D ? mktRows(D) : [], groups = groupMkt(rows);
  const rs = D && D.refusal_summary_24h && D.refusal_summary_24h.data || [];
  return '<h2>Today\'s funnel</h2>' + (fu.stages && fu.stages.length ? '<table class="rtbl"><tbody>' + fu.stages.map((x) => '<tr><td>' + esc(x.h) + '</td><td class="r num">' + (x.value != null ? esc(Math.round(x.value).toLocaleString('en-US')) : 'N/A') + '</td><td>' + (x.largestLoss ? 'largest drop' : '') + '</td></tr>').join('') + '</tbody></table>' : '<p>' + esc('Not readable: ' + (fu.why || 'no reason')) + '</p>') +
    '<h2>Opportunity book</h2>' + (groups.length ? '<p class="small">' + rows.length + ' PAPER decisions on ' + groups.reduce((a, g) => a + g.contracts.length, 0) + ' contracts across ' + groups.length + ' events (duplicates on the same contract counted once).</p><table class="rtbl"><thead><tr><th>Event · contract</th><th>Verdict</th><th>Freshness</th><th>Settlement</th><th class="r">Depth</th><th class="r">Net EV</th></tr></thead><tbody>' +
      groups.slice(0, 12).map((g) => g.contracts.slice(0, 3).map((c, i) => '<tr><td>' + (i === 0 ? '<b>' + esc(g.event) + '</b><br>' : '') + esc([c.family, c.line, c.period, c.subject].filter(Boolean).join(' · ')) + '</td><td>' + esc(c.verdict || '—') + (c.n > 1 ? ' ×' + c.n : '') + (c.refusal ? '<br><small>' + esc(U.words(c.refusal)) + '</small>' : '') + '</td><td>' + (c.pAge != null ? 'PIN ' + Math.round(c.pAge) + 's' + (c.pLim != null ? '/' + Math.round(c.pLim) + 's' : '') : 'n/r') + '</td><td>' + esc(c.settlement ? U.words(c.settlement) : 'not recorded') + '</td><td class="r num">' + (c.depthQty != null ? esc(Math.round(c.depthQty).toLocaleString('en-US')) : '—') + '</td><td class="r num">' + (c.net != null ? esc(U.signedUsd(c.net)) : '—') + '</td></tr>').join('')).join('') + '</tbody></table>' : '<p>' + esc(D ? 'No PAPER decision recorded.' : 'paper/derek not readable.') + '</p>') +
    '<h2>Refusal mix · last 24 h</h2>' + (rs.length ? '<table class="rtbl"><tbody>' + rs.slice(0, 10).map((r) => '<tr><td>' + esc(U.words(r.reason)) + '</td><td>' + esc(U.words(r.strategy || '')) + '</td><td class="r num">' + esc(String(r.n)) + '</td></tr>').join('') + '</tbody></table>' : '<p>' + DNA + ' · no refusal summary in this read.</p>') +
    '<h2>Allocator ranking · SHADOW</h2>' + (rk.length ? '<ul>' + rk.slice(0, 6).map((x) => '<li>#' + esc(x.rank) + ' ' + esc(short(x.market || x.id)) + ' — EV/$ ' + (fin(x.net_ev_per_dollar) ? x.net_ev_per_dollar.toFixed(2) : '—') + ' · ' + esc(U.words(x.binding_constraint || '')) + '</li>').join('') + '</ul>' : '<p>No allocator run in this read.</p>');
}
function teamReport() {
  const ds = HQ.desks(), w = HQ.adrianaWork(), ad = HQ.desk('adriana');
  return '<h2>Desks</h2><table class="rtbl"><thead><tr><th>Desk</th><th>State</th><th>Latest consequential output</th></tr></thead><tbody>' + ds.map((d) => '<tr><td><b>' + esc(d.name) + '</b><br><small>' + esc(d.role || d.zone) + '</small></td><td>' + esc(d.label) + '</td><td>' + esc(d.planned ? 'No activity: the seat is not served.' : (d.last && d.last.summary ? d.last.summary : 'none in this read')) + '</td></tr>').join('') + '</tbody></table>' +
    '<h2>Adriana · arbitrage census</h2>' + (ad.planned ? '<p>' + esc(ad.detail) + '</p>' : w.status !== 'OK' ? '<p>' + esc('Census not read (' + (w.why || w.status) + ').') + '</p>' :
      '<p>' + w.opportunities.length + ' structures proven after every cost (SHADOW) and ' + w.refusals.length + ' refusals in this read. Nothing here is an order.</p>' + (w.opportunities.length ? '<ul>' + w.opportunities.slice(0, 6).map((r) => '<li>' + esc(r.summary) + ' · ' + esc(U.clock(r.at)) + '</li>').join('') + '</ul>' : '') +
      (w.refusals.length ? '<h3>Recent refusals</h3><ul>' + w.refusals.slice(0, 8).map((r) => '<li>' + esc(r.summary) + '</li>').join('') + '</ul>' : '')) +
    '<h2>Collaboration · last hour</h2>' + (HQ.edges().length ? '<ul>' + HQ.edges().sort((a, b) => (b.at || 0) - (a.at || 0)).slice(0, 10).map((e) => '<li>' + esc((HQ.BY_SLUG[e.from] || {}).name + ' → ' + (HQ.BY_SLUG[e.to] || {}).name + ' · ' + e.label + (e.count > 1 ? ' ×' + e.count : '')) + ' · ' + esc(U.hm(e.at)) + '</li>').join('') + '</ul>' : '<p>No recorded hand-off or challenge in the window.</p>');
}
function reportHTML() {
  const r = REPORTS.find((x) => x.id === S.report) || REPORTS[0];
  const at = new Date().toISOString();
  const body = r.id === 'capital' ? capitalReport() : r.id === 'markets' ? marketsReport() : r.id === 'team' ? teamReport() : dailyReport();
  return '<div class="print-brand"><img src="brand/bettortoken-logo.png" alt="BettorToken" width="170" height="30"><div><b>' + esc(r.name) + '</b><span>BETTOR Command · compiled ' + esc(at.slice(0, 10) + ' ' + at.slice(11, 19)) + 'Z · read-only</span></div></div>' +
    '<div class="rep-pick" role="tablist" aria-label="Report">' + REPORTS.map((x) => '<button type="button" role="tab" data-report="' + x.id + '" aria-selected="' + (x.id === r.id) + '"><b>' + esc(x.name) + '</b><small>' + esc(x.note) + '</small></button>').join('') + '</div>' +
    '<div class="lbl">' + esc(r.name) + ' <span class="book-tag paper">PAPER · SIMULATED</span></div>' +
    '<h1>BETTOR · ' + esc(at.slice(0, 10)) + '</h1><div class="meta">Compiled in the browser from the reads on this page · ' + esc(at.slice(11, 19)) + 'Z · read-only · not an audit</div>' +
    body +
    '<div class="sheet-actions"><button class="btn" type="button" data-print>Download PDF</button><span class="why">Opens the print dialog with the BETTOR letterhead; choose “Save as PDF”.</span></div>' +
    (r.id === 'daily' ? '<h2>Deeper reports</h2><div class="report-links">' +
      '<a href="ops.html"><b>Operations desk</b><small>Funnel, refusals, orders &amp; fills, incidents</small></a>' +
      '<a href="profitability.html"><b>Profitability</b><small>Sleeves, scorecards, evidence ladder</small></a>' +
      '<a href="position.html"><b>Position rooms</b><small>Every open PAPER position, marked</small></a>' +
      '<a href="improvements.html"><b>Improvements</b><small>Research and policy proposals in review</small></a>' +
      '<a href="mobile.html"><b>Mobile Command</b><small>The installed phone app</small></a>' +
      '<a href="classic.html"><b>Classic Command</b><small>The previous Command home, unchanged</small></a></div>' : '') +
    '<div class="print-foot">BETTOR · ' + esc(r.name) + ' · READ ONLY · PAPER is simulated execution · SMALL LIVE as served · compiled ' + esc(at.slice(0, 19)) + 'Z</div>';
}
function renderReports() { setHTML('[data-render="report"]', reportHTML()); }

/* ═══════════════════════════ WATCH BETTOR WORK ══════════════════════ */
/* Follows the recorded events of the latest floor read, oldest to newest:
 * each step moves the camera to the desk that recorded it and captions the
 * record (who, what, when, count). It stops at the newest record. */
function watchEvents() {
  const ev = [];
  HQ.edges().forEach((e) => { if (e.at != null) ev.push({at: e.at, desk: e.from, to: e.to, title: (HQ.BY_SLUG[e.from] || {}).name + ' → ' + (HQ.BY_SLUG[e.to] || {}).name + ' · ' + e.label + (e.count > 1 ? ' ×' + e.count : ''), detail: e.summary || ''}); });
  HQ.decisions().slice(0, 6).forEach((d) => { if (d.at != null) ev.push({at: d.at, desk: 'derek', title: 'Derek · ' + (d.verdict || '') + ' · ' + short(d.market || d.id), detail: d.refusal ? U.words(d.refusal) : (d.limit_price != null ? 'limit ' + d.limit_price : '')}); });
  return ev.sort((a, b) => b.at - a.at).slice(0, 12).reverse();
}
function watch(on) {
  const btns = $$('[data-watch]');
  if (!on) { if (S.watch) clearTimeout(S.watch.timer); S.watch = null; doc.body.classList.remove('watching'); btns.forEach((b) => b.setAttribute('aria-pressed', 'false')); closeDesk(); return; }
  const ev = watchEvents();
  if (!ev.length) { setHTML('#hq-caption .ins', '<div class="cap-top"><span>Watch BETTOR Work</span><span>no recorded event</span></div><b>No recorded event in the latest floor read.</b><p>Nothing is replayed or synthesized.</p>'); doc.body.classList.add('watching'); setTimeout(() => watch(false), 4000); return; }
  S.watch = {ev, i: 0, timer: null};
  btns.forEach((b) => b.setAttribute('aria-pressed', 'true'));
  doc.body.classList.add('watching');
  if (S.view !== 'floor' && S.view !== 'command') go('floor');
  step();
}
function step() {
  const w = S.watch; if (!w) return;
  const e = w.ev[w.i];
  setHTML('#hq-caption .ins', '<div class="cap-top"><span>Watch BETTOR Work · recorded event ' + (w.i + 1) + ' of ' + w.ev.length + '</span><span>' + esc(U.clock(e.at)) + '</span></div><b>' + esc(e.title) + '</b><p>' + esc(e.detail || '') + '</p>');
  if (S.scene) { S.scene.focus(e.desk); }
  w.i++;
  w.timer = setTimeout(() => { if (!S.watch) return; if (w.i >= w.ev.length) { watch(false); return; } step(); }, REDUCED ? 6000 : 5200);
}

/* ═══════════════════════════ NAVIGATION ═════════════════════════════ */
function insets() {
  if (!S.scene || PHONE) return;
  const w = innerWidth, top = 60;
  let v = {left: 0, right: 0, top: 0, bottom: 0};
  if (S.desk) v = {left: S.view === 'command' ? 330 : 0, right: 400, top: 0, bottom: 0};
  else if (S.view === 'command') v = {left: 330, right: 350, top: 0, bottom: 118};
  else if (S.view === 'floor') v = {left: 0, right: 0, top: 0, bottom: 90};
  else if (S.view === 'markets') v = {left: 0, right: Math.min(960, w * 0.64) + 30, top: 0, bottom: 140};
  else if (S.view === 'capital') v = {left: 0, right: 0, top: 0, bottom: 0};
  else if (S.view === 'reports') v = {left: 0, right: 0, top: 0, bottom: 0};
  v.top = Math.max(v.top, top * 0.5);
  S.scene.setInsets(v);
}
function go(view, opts) {
  if (VIEWS.indexOf(view) < 0) view = 'command';

  // The phone Command shell intentionally collapses the HQ stage into a
  // one-column executive HUD. FLOOR is different: it is an interactive room,
  // and the existing /floor page already has a phone-tuned WebGL renderer.
  // Never substitute the flat desk-card summary for the user's Floor action.
  if (PHONE && view === 'floor' && !(opts && opts.inlineMobileFloor)) {
    location.assign('/floor');
    return;
  }

  if (S.watch && !(opts && opts.fromWatch)) watch(false);
  S.view = view;
  doc.body.setAttribute('data-view', view);
  $$('#hq-nav button, #hq-tabbar button').forEach((b) => { if (b.dataset.go === view) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
  if (S.desk) { S.desk = null; doc.body.classList.remove('desk-open'); }
  if (S.scene) {
    if (view === 'markets') S.scene.focusWall('opps');
    else S.scene.view(view);
    insets();
  }
  if (view === 'capital') HQ.readCapital();
  history.replaceState(null, '', '#' + view);
  render();
  placeTags();
  if (PHONE) window.scrollTo(0, 0);
}

/* ═══════════════════════════ RENDER LOOP ════════════════════════════ */
function render() {
  guard('top', renderTop); guard('alert', renderAlert); guard('fresh', renderFresh);
  guard('capital', renderCapital); guard('attention', renderAttention); guard('team', renderTeam); guard('coverage', renderCoverage);
  guard('funnel', renderFunnel); guard('tape', renderTape);
  if (S.view === 'floor' || PHONE) guard('floor', renderFloor); else guard('floor-tags', renderTags);
  if (S.view === 'markets' || PHONE) guard('markets', renderMarkets);
  if (S.view === 'capital') guard('capital-view', renderCapitalView);
  if (S.view === 'reports') guard('report', renderReports);
  if (S.desk) setHTML('#hq-desk .ins', deskHTML(S.desk));
}
let pending = false;
function schedule() { if (pending) return; pending = true; requestAnimationFrame(() => { pending = false; render(); }); }

function wire() {
  doc.addEventListener('click', (e) => {
    const rp = e.target.closest('[data-report]'); if (rp) { S.report = rp.dataset.report; renderReports(); return; }
    const g = e.target.closest('[data-go]'); if (g) { if (g.dataset.reportPick) S.report = g.dataset.reportPick; go(g.dataset.go); return; }
    const d = e.target.closest('[data-desk]'); if (d) { if (PHONE) { openDesk(d.dataset.desk); } else { if (S.view !== 'floor' && S.view !== 'command') go('floor'); openDesk(d.dataset.desk); } return; }
    if (e.target.closest('[data-close-desk]')) { closeDesk(); return; }
    if (e.target.closest('[data-watch]')) { watch(!S.watch); return; }
    if (e.target.closest('[data-print]')) { const t = doc.title; doc.title = 'BETTOR ' + ((REPORTS.find((x) => x.id === S.report) || REPORTS[0]).name) + ' ' + new Date().toISOString().slice(0, 10); window.print(); doc.title = t; return; }
  });
  doc.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { if (S.watch) watch(false); else if (S.desk) closeDesk(); }
    if (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
    const n = {'1': 'command', '2': 'floor', '3': 'markets', '4': 'capital', '5': 'reports'}[e.key];
    if (n && !e.metaKey && !e.ctrlKey && !e.altKey) go(n);
  });
  window.addEventListener('resize', () => { insets(); placeTags(); });
  window.addEventListener('hashchange', fromHash);
  doc.addEventListener('error', (e) => { const t = e.target; if (t && t.tagName === 'IMG' && t.closest('.mate, .desk-head')) { const ph = doc.createElement('span'); ph.className = 'ph'; t.replaceWith(ph); } }, true);
  setInterval(() => { const c = $('#hq-clock'); if (c) c.textContent = new Date().toISOString().slice(11, 19) + 'Z'; }, 1000);
}
function fromHash() {
  const h = (location.hash || '').replace(/^#/, '');
  const m = h.match(/^desk=([a-z_]+)$/);
  if (m) { if (!PHONE && S.view !== 'floor') go('floor'); openDesk(m[1], {keepHash: true}); return; }
  if (VIEWS.indexOf(h) >= 0 && h !== S.view) go(h);
}

async function mountScene() {
  const stage = $('#hq-stage');
  if (PHONE || !stage) return;
  let mod;
  // the first floor read decides which planned seats are served (built as
  // desks with their people) -- bounded, so a slow API never holds the room
  await Promise.race([HQ.firstFloor(), new Promise((r) => setTimeout(r, 6000))]);
  try { mod = await import('./hq-scene.js'); } catch (e) { stage.innerHTML = '<div id="hq-nogl">The 3D headquarters could not load (' + esc(e && e.message || e) + '). Every figure is in the HUD.</div>'; return; }
  if (!mod.webglAvailable() || window.__hqForceNoWebGL) { stage.innerHTML = '<div id="hq-nogl">This browser has no WebGL: the 3D headquarters is off. Every figure is in the HUD.</div>'; return; }
  try {
    S.scene = await mod.createHQ(stage, {phone: false, reducedMotion: REDUCED, model: HQ, view: S.view === 'markets' ? 'command' : S.view,
      onPick: (slug) => { if (S.watch) watch(false); if (slug) { if (S.view !== 'floor' && S.view !== 'command') go('floor'); openDesk(slug); } else if (S.desk) closeDesk(); },
      onFrame: (f) => { if (f.cameraDirty) placeTags(); },
      onStatus: (kind, n) => { if (kind === 'characters-ready') doc.body.setAttribute('data-characters', String(n)); }});
    window.__hq = {scene: S.scene, model: HQ, go, openDesk, closeDesk, watch};
    if (S.view === 'markets') S.scene.focusWall('opps');
    if (S.desk) S.scene.focus(S.desk);
    insets(); placeTags();
  } catch (e) {
    stage.innerHTML = '<div id="hq-nogl">The 3D headquarters stopped: ' + esc(e && e.message || e) + '. Every figure is in the HUD.</div>';
    if (window.console) console.warn('BETTOR HQ scene', e);
  }
}

function boot() {
  const want = (location.hash || '').replace(/^#/, '');
  S.view = VIEWS.indexOf(want) >= 0 ? want : (doc.body.getAttribute('data-start') || 'command');

  // A bookmarked /#floor must have the same mobile semantics as tapping Floor:
  // use the dedicated mobile-capable 3D room rather than the list-only pocket
  // summary. replace() avoids leaving a dead summary view in browser history.
  if (PHONE && S.view === 'floor') {
    location.replace('/floor');
    return;
  }

  doc.body.setAttribute('data-view', S.view);
  $$('#hq-nav button, #hq-tabbar button').forEach((b) => { if (b.dataset.go === S.view) b.setAttribute('aria-current', 'page'); });
  wire();
  HQ.subscribe(schedule);
  HQ.onSignedOut(() => { if (S.signedOut) return; S.signedOut = true; if (window.BTUnlock && typeof window.BTUnlock.open === 'function') window.BTUnlock.open(); schedule(); });
  HQ.start();
  render();
  mountScene().then(() => { const m = want.match(/^desk=([a-z_]+)$/); if (m) openDesk(m[1], {keepHash: true}); });
  if (S.view === 'capital') HQ.readCapital();
  setTimeout(() => { const h = $('#hq-hint'); if (h) h.classList.add('gone'); }, 9000);
}
window.__hqModel = HQ;
if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', boot); else boot();
